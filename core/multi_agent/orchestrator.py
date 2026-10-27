# ==========================
# ELIAN — Multi-agent orchestrator (authoritative runtime)
# ==========================
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .base import BaseAgent
from .factory import AgentFactory
from .memory_scopes import ScopedMemoryStore
from .registry import AgentRegistry
from .resource_manager import ResourceManager
from .self_model import SelfModel
from .task_graph import TaskGraph
from .types import (
    PERM_REQUEST_AGENTS,
    AgentPriority,
    AgentResult,
    AgentStatus,
    HealthState,
    SpawnRequest,
    SystemMode,
    VoiceUIState,
    new_task_id,
)


class ElianOrchestrator:
    """
    Central multi-agent runtime.

    Agents request spawns; the orchestrator validates policy/resources and creates workers.
    User-facing personality remains one Elian (ConversationBridgeAgent / ConversationManager).
    """

    def __init__(
        self,
        *,
        max_agents_total: int = 24,
        max_concurrent_agents: int = 8,
        max_children_per_agent: int = 6,
        max_spawn_depth: int = 3,
        max_agents_per_task: int = 6,
        agent_task_timeout_s: float = 120.0,
        background_resource_budget: float = 0.35,
        conversation_manager: Any = None,
        tool_registry: Any = None,
        event_bus: Any = None,
        language: str = "en-US",
        mode: str = SystemMode.NORMAL.value,
        state_path: Optional[Path] = None,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        self.state_path = Path(
            state_path or (root / "Data" / "multi_agent" / "orchestrator_state.json")
        )
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        self.max_agents_total = int(max_agents_total)
        self.max_concurrent_agents = int(max_concurrent_agents)
        self.max_children_per_agent = int(max_children_per_agent)
        self.max_spawn_depth = int(max_spawn_depth)
        self.max_agents_per_task = int(max_agents_per_task)
        self.agent_task_timeout_s = float(agent_task_timeout_s)

        self.registry = AgentRegistry()
        warn_ratio = 0.75
        crit_ratio = 0.90
        try:
            from Config import config as _cfg

            ma = _cfg.get("multi_agent") if isinstance(_cfg.get("multi_agent"), dict) else {}
            warn_ratio = float(ma.get("vram_warn_ratio") or warn_ratio)
            crit_ratio = float(ma.get("vram_critical_ratio") or crit_ratio)
        except Exception:
            pass
        self.resources = ResourceManager(
            background_budget=background_resource_budget,
            vram_warn_ratio=warn_ratio,
            vram_critical_ratio=crit_ratio,
        )
        self.self_model = SelfModel()
        self.memory_scopes = ScopedMemoryStore()
        self.factory = AgentFactory(on_event=self._on_agent_event)
        self.conversation_manager = conversation_manager
        self.tool_registry = tool_registry
        self.event_bus = event_bus

        self._agents: Dict[str, BaseAgent] = {}
        self._lock = threading.RLock()
        self._queue: List[SpawnRequest] = []
        self._spawn_history: List[dict] = []
        self._performance: Dict[str, dict] = {}
        self._stop = threading.Event()
        self._bg_thread: Optional[threading.Thread] = None
        self._executor = ThreadPoolExecutor(max_workers=max(4, max_concurrent_agents))
        self._started = False
        self._language = language
        self._mode = mode
        self._gui_callbacks: List[Callable[[str, dict], None]] = []

        self.self_model.update(
            language=language,
            mode=mode,
            identity="Elian",
            voice_engine="",
        )

    # ---------- lifecycle ----------

    def start(
        self,
        *,
        start_self_development: bool = False,
        self_dev_max_cycles: int = 0,
    ) -> None:
        if self._started:
            return
        self._bootstrap_core_agents(include_self_development=bool(start_self_development))
        try:
            self.resources.apply_hardware_limits()
        except Exception:
            pass
        self._sync_self_model()
        self._started = True
        self._emit("system.health", {"health": self.self_model.snapshot().get("health")})
        print("[ORCHESTRATOR] multi-agent runtime started")

        if start_self_development:
            try:
                self.start_background_self_development(max_cycles=self_dev_max_cycles)
                print("[ORCHESTRATOR] Self-Development Agent started in background")
            except Exception as exc:
                print(f"[ORCHESTRATOR] Self-Development start failed: {exc}")
                self.self_model.set_development(
                    active=False, phase="FAILED", candidate="", cycle=0
                )
                self.self_model.note_failure(f"self_dev_start:{exc}")
                self.self_model.update(health="DEGRADED")
                self._emit("self_dev.failed", {"error": str(exc)[:200]})

    def stop(self) -> None:
        self._stop.set()
        try:
            from core.self_improve.self_development.state import (
                DevelopmentStateStore,
                default_state_path,
            )

            DevelopmentStateStore(default_state_path()).request_stop()
        except Exception:
            pass
        if self._bg_thread and self._bg_thread.is_alive():
            self._bg_thread.join(timeout=5.0)
        # If a cycle was cut mid-mutation, recover (rollback unfinished work).
        try:
            from core.self_improve.self_development.engine import SelfDevelopmentEngine

            eng = SelfDevelopmentEngine(max_cycles=0, skip_agent_smoke=True)
            st = eng.state.load()
            if st.get("incomplete_cycle") or st.get("running"):
                eng._recover_interrupted(st)
                eng.state.update(
                    running=False,
                    mode="stopped",
                    stop_requested=False,
                    current_phase="STOPPED",
                )
        except Exception as exc:
            print(f"[ORCHESTRATOR] self-dev recovery soft-fail: {exc}")
        with self._lock:
            ids = list(self._agents.keys())
        for agent_id in ids:
            try:
                self.stop_agent(agent_id)
            except Exception:
                pass
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._executor.shutdown(wait=False)
        self._persist_state()
        self._started = False
        print("[ORCHESTRATOR] stopped")

    def _bootstrap_core_agents(self, *, include_self_development: bool = False) -> None:
        cores = [
            ("conversation", "elian_main", True, AgentPriority.CRITICAL.value),
            ("memory", "memory_primary", True, AgentPriority.HIGH.value),
        ]
        if include_self_development:
            cores.append(
                (
                    "self_development",
                    "self_developer",
                    True,
                    AgentPriority.BACKGROUND.value,
                )
            )
        for agent_type, role, persistent, priority in cores:
            existing = None
            for agent in self.registry.list_by_type(agent_type):
                if agent.role == role and agent.status not in (
                    AgentStatus.STOPPED.value,
                    AgentStatus.FAILED.value,
                ):
                    existing = agent
                    break
            if existing:
                continue
            req = SpawnRequest(
                agent_type=agent_type,
                role=role,
                task="",
                parent_agent_id="",
                priority=priority,
                justification="core persistent agent",
            )
            self._create_agent(req, persistent=persistent, parent_depth=-1)

    # ---------- spawn policy ----------

    def spawn_agent(self, req: SpawnRequest) -> AgentResult:
        """Validate and create (or reuse) an agent. Does not always run a task."""
        decision = self._validate_spawn(req)
        if decision.get("action") == "reuse":
            agent_id = decision["agent_id"]
            agent = self._agents.get(agent_id)
            if agent:
                return AgentResult(
                    task_id=new_task_id("reuse"),
                    status="ok",
                    summary=f"Reusing idle agent {agent_id}",
                    artifacts={"agent_id": agent_id, "reused": True},
                    source_agent=req.parent_agent_id,
                    target_agent=agent_id,
                )
        if decision.get("action") == "queue":
            with self._lock:
                self._queue.append(req)
            return AgentResult(
                task_id=new_task_id("queued"),
                status="queued",
                summary=str(decision.get("reason") or "queued"),
                artifacts={"queued": True},
                source_agent=req.parent_agent_id,
            )
        if decision.get("action") == "reject":
            return AgentResult(
                task_id=new_task_id("reject"),
                status="failed",
                summary=str(decision.get("reason") or "spawn rejected"),
                errors=[str(decision.get("reason") or "rejected")],
                source_agent=req.parent_agent_id,
            )

        agent = self._create_agent(req, persistent=False)
        return AgentResult(
            task_id=new_task_id("spawn"),
            status="ok",
            summary=f"Created {agent.identity.agent_type} {agent.identity.agent_id}",
            artifacts={
                "agent_id": agent.identity.agent_id,
                "justification": req.justification,
            },
            source_agent=req.parent_agent_id,
            target_agent=agent.identity.agent_id,
        )

    def spawn_and_run(self, req: SpawnRequest) -> AgentResult:
        """Spawn (or reuse) then execute task; cleanup temporary workers."""
        parent = self._agents.get(req.parent_agent_id)
        if parent and PERM_REQUEST_AGENTS not in (parent.identity.permissions or []):
            # Core/self-dev always allowed when parent missing perms list empty? enforce.
            if parent.identity.agent_type not in ("self_development", "conversation"):
                return AgentResult(
                    task_id=new_task_id("perm"),
                    status="failed",
                    summary="Parent lacks request_agents permission",
                    errors=["permission_denied"],
                    source_agent=req.parent_agent_id,
                )

        # Prefer reuse of idle specialist with same role.
        idle = self.registry.find_idle(req.agent_type, role=req.role)
        agent: Optional[BaseAgent] = None
        created = False
        if idle and idle.agent_id in self._agents:
            agent = self._agents[idle.agent_id]
        else:
            spawn_res = self.spawn_agent(req)
            if spawn_res.status == "queued":
                return spawn_res
            if spawn_res.status != "ok":
                return spawn_res
            agent_id = str((spawn_res.artifacts or {}).get("agent_id") or "")
            agent = self._agents.get(agent_id)
            created = True
            if spawn_res.artifacts.get("reused"):
                created = False

        if agent is None:
            return AgentResult(
                task_id=new_task_id("missing"),
                status="failed",
                summary="Spawn produced no agent",
                errors=["no_agent"],
                source_agent=req.parent_agent_id,
            )

        # Resource gate — interactive wins.
        kind = "gpu" if req.priority != AgentPriority.LOW.value else "cpu"
        got = self.resources.acquire(
            agent.identity.agent_id,
            kind=kind,
            priority=req.priority,
            timeout_s=2.0 if req.priority == AgentPriority.CRITICAL.value else 0.5,
        )
        if not got and req.priority in (
            AgentPriority.BACKGROUND.value,
            AgentPriority.LOW.value,
        ):
            return AgentResult(
                task_id=new_task_id("wait"),
                status="queued",
                summary="Waiting for resources (background deferred)",
                artifacts={"deferred": True},
                source_agent=req.parent_agent_id,
                target_agent=agent.identity.agent_id,
            )

        scoped = dict(req.context or {})
        # Never pass entire parent brain — only explicit scoped keys.
        t0 = time.time()
        try:
            result = agent.handle_task(req.task, context=scoped)
        finally:
            self.resources.release_all(agent.identity.agent_id)
        duration = time.time() - t0
        self._record_performance(agent.identity.agent_type, result.status == "ok", duration)

        # Cleanup temporary completed workers.
        if created and not agent.identity.persistent:
            if agent.identity.status in (
                AgentStatus.COMPLETED.value,
                AgentStatus.FAILED.value,
                AgentStatus.IDLE.value,
            ):
                self.stop_agent(agent.identity.agent_id, reason="temp_cleanup")

        self._sync_self_model()
        self._drain_queue()
        return result

    def _validate_spawn(self, req: SpawnRequest) -> dict:
        # Reuse idle specialist first.
        idle = self.registry.find_idle(req.agent_type, role=req.role)
        if idle is not None:
            return {"action": "reuse", "agent_id": idle.agent_id}

        parent = self.registry.get(req.parent_agent_id) if req.parent_agent_id else None
        depth = (parent.spawn_depth + 1) if parent else 0
        if depth > self.max_spawn_depth:
            return {"action": "reject", "reason": "max_spawn_depth"}

        if parent and len(self.registry.children_of(parent.agent_id)) >= self.max_children_per_agent:
            return {"action": "queue", "reason": "max_children_per_agent"}

        if self.registry.count() >= self.max_agents_total:
            return {"action": "queue", "reason": "max_agents_total"}

        if self.registry.active_count() >= self.max_concurrent_agents:
            if req.priority not in (AgentPriority.CRITICAL.value, AgentPriority.HIGH.value):
                return {"action": "queue", "reason": "max_concurrent_agents"}

        if not str(req.justification or "").strip() and req.agent_type not in (
            "conversation",
            "memory",
        ):
            # Soft require justification for dynamic specialists.
            if not req.task:
                return {"action": "reject", "reason": "missing_justification"}

        return {"action": "create"}

    def _create_agent(
        self,
        req: SpawnRequest,
        *,
        persistent: bool = False,
        parent_depth: Optional[int] = None,
    ) -> BaseAgent:
        parent = self.registry.get(req.parent_agent_id) if req.parent_agent_id else None
        depth = parent_depth if parent_depth is not None else (parent.spawn_depth if parent else 0)
        model = req.model or self._model_for(req.agent_type, req.priority)
        agent = self.factory.spawn(
            req,
            orchestrator=self,
            parent_depth=depth,
            persistent=persistent,
            model=model,
            scoped_context=dict(req.context or {}),
        )
        with self._lock:
            self._agents[agent.identity.agent_id] = agent
        self.registry.register(agent.identity)
        agent.identity.status = AgentStatus.IDLE.value
        self._spawn_history.append(
            {
                "ts": time.time(),
                "agent_id": agent.identity.agent_id,
                "type": agent.identity.agent_type,
                "role": agent.identity.role,
                "parent": req.parent_agent_id,
                "justification": req.justification,
            }
        )
        self._spawn_history = self._spawn_history[-100:]
        self._sync_self_model()
        self._persist_state()
        return agent

    def stop_agent(self, agent_id: str, *, reason: str = "") -> bool:
        with self._lock:
            agent = self._agents.pop(agent_id, None)
        if agent is None:
            self.registry.remove(agent_id)
            return False
        try:
            agent.stop()
        except Exception:
            pass
        self.resources.release_all(agent_id)
        self.registry.remove(agent_id)
        self._emit(
            "agent.stopped",
            {"agent_id": agent_id, "reason": reason},
        )
        self._sync_self_model()
        return True

    def restart_agent(self, agent_id: str) -> Optional[str]:
        with self._lock:
            agent = self._agents.get(agent_id)
        if agent is None:
            return None
        ident = agent.identity
        req = SpawnRequest(
            agent_type=ident.agent_type,
            role=ident.role,
            task=ident.task,
            parent_agent_id=ident.parent_agent_id,
            priority=ident.priority,
            justification="restart",
            capabilities=list(ident.capabilities),
            model=ident.model,
        )
        persistent = ident.persistent
        self.stop_agent(agent_id, reason="restart")
        new_agent = self._create_agent(req, persistent=persistent)
        return new_agent.identity.agent_id

    # ---------- parallel task graph ----------

    def run_task_graph(
        self,
        tasks: List[dict],
        *,
        parent_agent_id: str = "",
        priority: str = AgentPriority.NORMAL.value,
    ) -> List[AgentResult]:
        """
        tasks: [{name, agent_type, role, task, depends_on?, context?}, ...]
        Independent ready tasks run concurrently.
        """
        graph = TaskGraph()
        meta: Dict[str, dict] = {}
        for row in tasks:
            node = graph.add(
                str(row.get("name") or row.get("task") or "task"),
                agent_type=str(row.get("agent_type") or "specialist"),
                depends_on=list(row.get("depends_on") or []),
                priority=str(row.get("priority") or priority),
                task_id=str(row.get("task_id") or ""),
            )
            meta[node.task_id] = row

        results: List[AgentResult] = []
        safety = 0
        while not graph.all_terminal() and safety < 50:
            safety += 1
            ready = graph.ready_tasks()
            if not ready:
                break
            futures = {}
            for node in ready:
                graph.mark_running(node.task_id)
                row = meta[node.task_id]
                req = SpawnRequest(
                    agent_type=str(row.get("agent_type") or "specialist"),
                    role=str(row.get("role") or row.get("name") or "worker"),
                    task=str(row.get("task") or row.get("name") or ""),
                    parent_agent_id=parent_agent_id,
                    priority=str(row.get("priority") or priority),
                    justification=str(row.get("justification") or f"graph:{node.name}"),
                    context=dict(row.get("context") or {}),
                )
                futures[self._executor.submit(self.spawn_and_run, req)] = node.task_id

            for fut in as_completed(futures):
                tid = futures[fut]
                try:
                    res = fut.result()
                except Exception as exc:
                    res = AgentResult(
                        task_id=tid,
                        status="failed",
                        summary="parallel task failed",
                        errors=[str(exc)],
                    )
                graph.mark_done(tid, res)
                results.append(res)

        self._sync_self_model()
        return results

    # ---------- user / self-dev ----------

    def handle_user_text(self, text: str) -> AgentResult:
        """Highest-priority interactive path through Conversation agent."""
        self.resources.note_interactive(True)
        self.self_model.update(
            current_focus=str(text or "")[:120],
            voice_state=VoiceUIState.THINKING.value,
        )
        try:
            conv = self._first_of_type("conversation")
            if conv is None:
                req = SpawnRequest(
                    agent_type="conversation",
                    role="elian_main",
                    task=text,
                    parent_agent_id="",
                    priority=AgentPriority.CRITICAL.value,
                    justification="interactive user",
                )
                conv = self._create_agent(req, persistent=True, parent_depth=-1)
            ctx = {"conversation_manager": self.conversation_manager}
            # Optional parallel specialists for multi-part compare-style requests.
            if self._looks_parallelizable(text):
                self._run_research_parallel(text, parent_id=conv.identity.agent_id)
            result = conv.handle_task(text, context=ctx)
            self.self_model.update(
                current_focus="",
                voice_state=VoiceUIState.IDLE.value,
            )
            return result
        finally:
            self.resources.note_interactive(False)
            self._sync_self_model()

    def _looks_parallelizable(self, text: str) -> bool:
        t = (text or "").lower()
        return any(
            k in t
            for k in (
                "compare",
                "cheapest",
                "in stock",
                "research",
                "three products",
                "parallel",
            )
        )

    def _run_research_parallel(self, text: str, *, parent_id: str) -> List[AgentResult]:
        tasks = [
            {
                "name": "research",
                "agent_type": "research",
                "role": "web_research",
                "task": f"Research options for: {text[:160]}",
                "justification": "parallel research for user compare request",
                "context": {"payload": {"query": text[:200]}},
            },
            {
                "name": "availability",
                "agent_type": "specialist",
                "role": "availability",
                "task": f"Check availability for: {text[:160]}",
                "justification": "parallel availability check",
                "context": {"payload": {"query": text[:200]}},
            },
            {
                "name": "automation",
                "agent_type": "specialist",
                "role": "automation_check",
                "task": f"Check automation feasibility for: {text[:160]}",
                "justification": "parallel automation capability check",
                "context": {"payload": {"query": text[:200]}},
            },
        ]
        return self.run_task_graph(
            tasks,
            parent_agent_id=parent_id,
            priority=AgentPriority.HIGH.value,
        )

    def start_background_self_development(self, *, max_cycles: int = 0) -> None:
        if self._bg_thread and self._bg_thread.is_alive():
            return

        # Ensure persistent Self-Development agent exists before the loop.
        if self._first_of_type("self_development") is None:
            self._create_agent(
                SpawnRequest(
                    agent_type="self_development",
                    role="self_developer",
                    task="",
                    parent_agent_id="",
                    priority=AgentPriority.BACKGROUND.value,
                    justification="core persistent agent",
                ),
                persistent=True,
                parent_depth=-1,
            )

        def _run():
            cycles_done = 0
            while not self._stop.is_set():
                if self.resources.interactive_active():
                    time.sleep(0.5)
                    continue
                if self.resources.pressure() in ("WARNING", "CRITICAL"):
                    time.sleep(1.0)
                    continue
                req = SpawnRequest(
                    agent_type="self_development",
                    role="self_developer",
                    task="continuous_self_development",
                    parent_agent_id="",
                    priority=AgentPriority.BACKGROUND.value,
                    justification="background continuous improvement",
                    context={
                        "max_cycles": 1,
                        "skip_agent_smoke": True,
                        "use_analyst": True,
                    },
                )
                existing = self._first_of_type("self_development")
                if existing is None:
                    agent = self._create_agent(req, persistent=True, parent_depth=-1)
                else:
                    agent = existing
                got = self.resources.acquire(
                    agent.identity.agent_id,
                    kind="cpu",
                    priority=AgentPriority.BACKGROUND.value,
                    timeout_s=0.2,
                )
                if not got:
                    time.sleep(1.0)
                    continue
                try:
                    self.self_model.set_development(
                        active=True,
                        phase="OBSERVE",
                        candidate="",
                    )
                    self._emit(
                        "self_dev.phase_changed",
                        {"phase": "OBSERVE", "agent_id": agent.identity.agent_id},
                    )
                    agent.handle_task(
                        "continuous_self_development",
                        context=dict(req.context),
                    )
                    cycles_done += 1
                except Exception as exc:
                    self.self_model.set_development(active=False, phase="FAILED")
                    self.self_model.note_failure(f"self_dev_cycle:{exc}")
                    self._emit("self_dev.failed", {"error": str(exc)[:200]})
                finally:
                    self.resources.release_all(agent.identity.agent_id)
                    self._sync_self_model()
                if self._stop.wait(timeout=1.0):
                    break
                if max_cycles and max_cycles > 0 and cycles_done >= max_cycles:
                    break

        self._bg_thread = threading.Thread(
            target=_run, name="elian-self-dev-agent", daemon=True
        )
        self._bg_thread.start()
        self.self_model.set_development(active=True, phase="STARTING")
        self._emit("self_dev.cycle_started", {"background": True})
        self._sync_self_model()

    def pause_background(self) -> None:
        self.resources.note_interactive(True)

    def resume_background(self) -> None:
        self.resources.note_interactive(False)

    # ---------- introspection ----------

    def describe_self(self) -> str:
        self._sync_self_model()
        return self.self_model.describe()

    def snapshot(self) -> dict:
        self._sync_self_model()
        return {
            "self_model": self.self_model.snapshot(),
            "agents": self.registry.snapshot(),
            "resources": self.resources.snapshot(),
            "memory": self.memory_scopes.snapshot(),
            "queue_len": len(self._queue),
            "spawn_history": list(self._spawn_history)[-20:],
            "performance": dict(self._performance),
            "mode": self._mode,
            "language": self._language,
        }

    def set_voice_state(self, state: str) -> None:
        self.self_model.set_voice_state(state)
        self._emit("voice.state", {"state": state})

    def set_mode(self, mode: str) -> None:
        self._mode = str(mode)
        self.self_model.update(mode=self._mode)

    def register_gui_callback(self, cb: Callable[[str, dict], None]) -> None:
        self._gui_callbacks.append(cb)

    def emit(self, event_type: str, data: Optional[dict] = None) -> None:
        self._emit(event_type, dict(data or {}))

    # ---------- recovery ----------

    def recover(self) -> dict:
        """Restart recovery — do not revive stale temporary workers."""
        report = {
            "cleaned_temp": 0,
            "kept_persistent": 0,
            "self_model": True,
        }
        # Load prior state for audit only.
        prior = self._load_state()
        for row in prior.get("agents") or []:
            if row.get("persistent"):
                report["kept_persistent"] += 1
            else:
                report["cleaned_temp"] += 1
        # Fresh core agents only.
        self._bootstrap_core_agents()
        self._sync_self_model()
        # Self-dev resume is safe only when engine state says so — never blind resume of mutations.
        try:
            from core.self_improve.self_development.state import (
                DevelopmentStateStore,
                default_state_path,
            )

            st = DevelopmentStateStore(default_state_path()).status_summary()
            if st.get("running") or st.get("incomplete_cycle"):
                self.self_model.set_development(
                    active=False,
                    cycle=int(st.get("current_cycle") or 0),
                    phase="RECOVERED",
                    candidate=str(st.get("current_candidate") or ""),
                    last_improvement=str(st.get("last_successful_improvement") or ""),
                    last_rollback=str(st.get("last_failed_improvement") or ""),
                )
                self.self_model.add_limitation(
                    "Interrupted self-development was not blindly resumed after restart"
                )
        except Exception:
            pass
        return report

    # ---------- internals ----------

    def _first_of_type(self, agent_type: str) -> Optional[BaseAgent]:
        for agent in self._agents.values():
            if agent.identity.agent_type == agent_type and agent.identity.status not in (
                AgentStatus.STOPPED.value,
                AgentStatus.FAILED.value,
            ):
                return agent
        return None

    def _model_for(self, agent_type: str, priority: str) -> str:
        try:
            from Config import get_config_value

            conv = str(get_config_value("ai", "ollama_model", default="llama3.1:8b") or "")
            strong = str(
                get_config_value("multi_agent", "coding_model", default=conv) or conv
            )
            fast = str(
                get_config_value("multi_agent", "fast_model", default=conv) or conv
            )
        except Exception:
            conv, strong, fast = "llama3.1:8b", "llama3.1:8b", "llama3.1:8b"
        if agent_type == "self_development":
            return strong
        if agent_type in ("specialist", "tool") or priority == AgentPriority.LOW.value:
            return fast
        return conv

    def _drain_queue(self) -> None:
        with self._lock:
            if not self._queue:
                return
            pending = list(self._queue)
            self._queue.clear()
        for req in pending:
            try:
                self.spawn_and_run(req)
            except Exception:
                pass

    def _record_performance(self, agent_type: str, ok: bool, duration: float) -> None:
        row = self._performance.setdefault(
            agent_type,
            {"success": 0, "fail": 0, "total_duration": 0.0, "count": 0},
        )
        row["count"] += 1
        row["total_duration"] += float(duration)
        if ok:
            row["success"] += 1
        else:
            row["fail"] += 1

    def _sync_self_model(self) -> None:
        agents = self.registry.snapshot()
        metrics = self.resources.refresh_metrics()
        health = HealthState.HEALTHY.value
        if any(a.get("status") == AgentStatus.FAILED.value for a in agents):
            health = HealthState.DEGRADED.value
        try:
            tools = []
            disabled = []
            if self.tool_registry is not None and hasattr(self.tool_registry, "list_tools"):
                for t in self.tool_registry.list_tools() or []:
                    name = getattr(t, "name", None) or str(t)
                    tools.append(str(name))
            caps = sorted(
                {
                    *(tools or []),
                    *[a.get("agent_type") for a in agents],
                    "self_model",
                    "multi_agent",
                }
            )
        except Exception:
            tools, disabled, caps = [], [], ["multi_agent", "self_model"]

        hw = {}
        tts_info = {}
        try:
            hw = self.resources.hardware.snapshot()
        except Exception:
            hw = {}
        try:
            from voice.tts_engine import get_tts_engine

            st = get_tts_engine().health_check()
            tts_info = {
                "active": st.extras.get("active") or st.engine,
                "fish": st.extras.get("fish_readiness") or "UNAVAILABLE",
                "fallback": st.extras.get("fallback_readiness") or "UNAVAILABLE",
                "detail": st.detail,
                "last_latency_ms": st.last_latency_ms,
            }
            if not self.self_model.snapshot().get("voice_engine"):
                self.self_model.update(voice_engine=tts_info["active"])
        except Exception:
            tts_info = {"active": "unknown", "fish": "UNAVAILABLE", "fallback": "UNKNOWN"}

        prev_rt = dict(self.self_model.snapshot().get("runtime_state") or {})
        prev_rt.update(
            {
                "queue_len": len(self._queue),
                "started": self._started,
                "mode": self._mode,
                "hardware": hw,
                "tts": tts_info,
                "stt": prev_rt.get("stt")
                or {"engine": "whisper", "language": "en"},
                "resource_pressure": self.resources.pressure(),
                "bg_thread_alive": bool(self._bg_thread and self._bg_thread.is_alive()),
            }
        )
        self.self_model.update(
            active_agents=agents,
            resource_state=metrics,
            health=health,
            installed_tools=tools,
            disabled_tools=disabled,
            available_capabilities=caps,
            memory_state=self.memory_scopes.snapshot(),
            runtime_state=prev_rt,
            language=self._language,
        )

    def _on_agent_event(self, event_type: str, data: dict) -> None:
        self._emit(event_type, data)
        if event_type == "agent.failed":
            self.self_model.note_failure(str((data or {}).get("error") or "agent.failed"))
        if event_type.startswith("self_dev"):
            pass
        self._sync_self_model()

    def _emit(self, event_type: str, data: dict) -> None:
        payload = dict(data or {})
        payload.setdefault("timestamp", time.time())
        # Existing EventBus bridge
        if self.event_bus is not None:
            try:
                from core.events import Event, Priority

                self.event_bus.publish(
                    Event(
                        type=event_type,
                        data=payload,
                        source="orchestrator",
                        priority=Priority.NORMAL,
                    )
                )
            except Exception:
                pass
        for cb in list(self._gui_callbacks):
            try:
                cb(event_type, payload)
            except Exception:
                pass

    def _persist_state(self) -> None:
        payload = {
            "agents": [
                a.to_dict()
                for a in self.registry.list_all()
                if a.persistent
            ],
            "spawn_history": self._spawn_history[-50:],
            "performance": self._performance,
            "updated_at": time.time(),
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(
            prefix=".orch_", suffix=".tmp", dir=str(self.state_path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.state_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}


_ORCH: Optional[ElianOrchestrator] = None


def get_orchestrator() -> Optional[ElianOrchestrator]:
    return _ORCH


def set_orchestrator(orch: Optional[ElianOrchestrator]) -> None:
    global _ORCH
    _ORCH = orch
