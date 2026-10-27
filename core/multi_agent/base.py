# ==========================
# ELIAN — Base agent worker
# ==========================
from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Callable, Dict, Optional

from .types import (
    AgentIdentity,
    AgentPriority,
    AgentResult,
    AgentStatus,
    SpawnRequest,
    new_task_id,
)


class BaseAgent:
    """Isolated worker. Failures stay local; orchestrator supervises."""

    agent_type = "worker"
    default_permissions: list = []
    default_capabilities: list = []

    def __init__(
        self,
        identity: AgentIdentity,
        *,
        orchestrator: Any = None,
        on_event: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self.identity = identity
        self.orchestrator = orchestrator
        self.on_event = on_event
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._last_result: Optional[AgentResult] = None

    def start(self) -> None:
        self.identity.status = AgentStatus.IDLE.value
        self._emit("agent.started", {"agent_id": self.identity.agent_id})

    def stop(self) -> None:
        self._stop.set()
        self.identity.status = AgentStatus.STOPPING.value
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.identity.status = AgentStatus.STOPPED.value
        self._emit("agent.stopped", {"agent_id": self.identity.agent_id})

    def handle_task(self, task: str, *, context: Optional[Dict[str, Any]] = None) -> AgentResult:
        """Synchronous task entry — subclasses override `execute`."""
        task_id = new_task_id(self.agent_type)
        self.identity.status = AgentStatus.WORKING.value
        self.identity.task = str(task or "")
        self.identity.last_active = time.time()
        self._emit(
            "task.started",
            {
                "task_id": task_id,
                "agent_id": self.identity.agent_id,
                "task": self.identity.task,
            },
        )
        try:
            result = self.execute(str(task or ""), context=dict(context or {}), task_id=task_id)
            if not isinstance(result, AgentResult):
                result = AgentResult(
                    task_id=task_id,
                    status="ok",
                    summary=str(result),
                    source_agent=self.identity.agent_id,
                )
            result.task_id = result.task_id or task_id
            result.source_agent = result.source_agent or self.identity.agent_id
            self._last_result = result
            self.identity.status = (
                AgentStatus.IDLE.value
                if self.identity.persistent
                else AgentStatus.COMPLETED.value
            )
            self._emit(
                "task.completed",
                {
                    "task_id": task_id,
                    "agent_id": self.identity.agent_id,
                    "status": result.status,
                    "summary": result.summary[:200],
                },
            )
            return result
        except Exception as exc:
            self.identity.status = AgentStatus.FAILED.value
            self.identity.health = "DEGRADED"
            err = f"{type(exc).__name__}: {exc}"
            self._emit(
                "agent.failed",
                {"agent_id": self.identity.agent_id, "error": err[:200]},
            )
            return AgentResult(
                task_id=task_id,
                status="failed",
                summary="Agent task failed",
                errors=[err],
                source_agent=self.identity.agent_id,
            )

    def execute(
        self,
        task: str,
        *,
        context: Dict[str, Any],
        task_id: str,
    ) -> AgentResult:
        raise NotImplementedError

    def request_spawn(
        self,
        agent_type: str,
        role: str,
        task: str,
        *,
        justification: str = "",
        context: Optional[Dict[str, Any]] = None,
        priority: str = AgentPriority.NORMAL.value,
    ) -> Optional[AgentResult]:
        """Ask orchestrator to spawn a specialist — never forks processes directly."""
        if self.orchestrator is None:
            return AgentResult(
                task_id=new_task_id("spawn"),
                status="failed",
                summary="No orchestrator available",
                errors=["no_orchestrator"],
                source_agent=self.identity.agent_id,
            )
        req = SpawnRequest(
            agent_type=agent_type,
            role=role,
            task=task,
            parent_agent_id=self.identity.agent_id,
            priority=priority,
            justification=justification or f"{self.identity.agent_type} needs {role}",
            context=dict(context or {}),
        )
        return self.orchestrator.spawn_and_run(req)

    def _emit(self, event_type: str, data: dict) -> None:
        if callable(self.on_event):
            try:
                self.on_event(event_type, dict(data or {}))
            except Exception:
                pass


class ConversationBridgeAgent(BaseAgent):
    """Wraps existing ConversationManager — one Elian personality surface."""

    agent_type = "conversation"

    def execute(self, task: str, *, context: Dict[str, Any], task_id: str) -> AgentResult:
        cm = context.get("conversation_manager") or getattr(
            self.orchestrator, "conversation_manager", None
        )
        if cm is None:
            return AgentResult(
                task_id=task_id,
                status="failed",
                summary="ConversationManager unavailable",
                errors=["no_cm"],
                source_agent=self.identity.agent_id,
            )
        try:
            if not getattr(cm, "active", False) and hasattr(cm, "activate"):
                cm.activate()
            result = cm.handle(task)
            text = str(getattr(result, "text", "") or "")
            source = str(getattr(result, "source", "") or "")
            return AgentResult(
                task_id=task_id,
                status="ok",
                summary=text[:300] or "Handled.",
                evidence=[source],
                confidence=0.9,
                artifacts={"source": source},
                source_agent=self.identity.agent_id,
            )
        except Exception as exc:
            return AgentResult(
                task_id=task_id,
                status="failed",
                summary="Conversation handling failed",
                errors=[str(exc)],
                source_agent=self.identity.agent_id,
            )


class MemoryAgent(BaseAgent):
    agent_type = "memory"

    def execute(self, task: str, *, context: Dict[str, Any], task_id: str) -> AgentResult:
        scope = str(context.get("scope") or "SESSION")
        action = str(context.get("action") or "recall")
        key = str(context.get("key") or "")
        value = context.get("value")
        store = context.get("memory_store")
        if store is None and self.orchestrator is not None:
            store = getattr(self.orchestrator, "memory_scopes", None)
        if store is None:
            return AgentResult(
                task_id=task_id,
                status="ok",
                summary=f"Memory agent idle (no store); task={task[:80]}",
                artifacts={"scope": scope, "action": action},
                source_agent=self.identity.agent_id,
            )
        try:
            if action == "store" and key:
                store.put(scope, key, value)
                return AgentResult(
                    task_id=task_id,
                    status="ok",
                    summary=f"Stored {key} in {scope}",
                    source_agent=self.identity.agent_id,
                )
            if action == "recall" and key:
                data = store.get(scope, key)
                return AgentResult(
                    task_id=task_id,
                    status="ok",
                    summary=f"Recalled {key}" if data is not None else f"Missing {key}",
                    artifacts={"value": data},
                    source_agent=self.identity.agent_id,
                )
            listing = store.list_keys(scope)
            return AgentResult(
                task_id=task_id,
                status="ok",
                summary=f"{scope} has {len(listing)} keys",
                artifacts={"keys": listing[:50]},
                source_agent=self.identity.agent_id,
            )
        except Exception as exc:
            return AgentResult(
                task_id=task_id,
                status="failed",
                summary="Memory operation failed",
                errors=[str(exc)],
                source_agent=self.identity.agent_id,
            )


class ToolWorkerAgent(BaseAgent):
    agent_type = "tool"

    def execute(self, task: str, *, context: Dict[str, Any], task_id: str) -> AgentResult:
        tool_name = str(context.get("tool") or self.identity.role or "")
        args = dict(context.get("arguments") or {})
        registry = context.get("registry")
        if registry is None and self.orchestrator is not None:
            registry = getattr(self.orchestrator, "tool_registry", None)
        if not tool_name or registry is None:
            return AgentResult(
                task_id=task_id,
                status="failed",
                summary="Tool worker missing tool/registry",
                errors=["missing_tool"],
                source_agent=self.identity.agent_id,
            )
        try:
            result = registry.invoke(tool_name, args, confirmed=True)
            ok = bool(getattr(result, "success", False))
            msg = str(getattr(result, "message", "") or "")
            return AgentResult(
                task_id=task_id,
                status="ok" if ok else "failed",
                summary=msg[:300] or ("ok" if ok else "tool failed"),
                evidence=[tool_name],
                confidence=0.85 if ok else 0.2,
                errors=[] if ok else [str(getattr(result, "error", "") or "tool_error")],
                artifacts={"tool": tool_name},
                source_agent=self.identity.agent_id,
            )
        except Exception as exc:
            return AgentResult(
                task_id=task_id,
                status="failed",
                summary="Tool invoke failed",
                errors=[str(exc)],
                source_agent=self.identity.agent_id,
            )


class SpecialistWorkerAgent(BaseAgent):
    """Generic temporary specialist used for dynamic spawn demos/tests."""

    agent_type = "specialist"

    def execute(self, task: str, *, context: Dict[str, Any], task_id: str) -> AgentResult:
        # Deterministic specialist work — no LLM required for core tests.
        role = self.identity.role or "specialist"
        payload = dict(context.get("payload") or {})
        summary = f"{role} completed: {task[:120]}"
        return AgentResult(
            task_id=task_id,
            status="ok",
            summary=summary,
            evidence=[role],
            confidence=0.8,
            artifacts={"role": role, "payload": payload},
            source_agent=self.identity.agent_id,
            next_action="return_to_parent",
        )


class SelfDevelopmentAgent(BaseAgent):
    """Background autonomous developer — wraps existing SelfDevelopmentEngine."""

    agent_type = "self_development"

    def execute(self, task: str, *, context: Dict[str, Any], task_id: str) -> AgentResult:
        # Optional specialist workers for analysis (never write unless orchestrator grants).
        if context.get("use_analyst"):
            child = self.request_spawn(
                "specialist",
                "codebase_analyst",
                "Analyze repository health for next improvement",
                justification="Self-development needs architecture analysis",
                priority=AgentPriority.BACKGROUND.value,
                context={"payload": {"focus": "repository"}},
            )
            analyst_summary = child.summary if child else "analyst unavailable"
        else:
            analyst_summary = ""

        max_cycles = int(context.get("max_cycles") or 1)
        skip_smoke = bool(context.get("skip_agent_smoke", True))
        try:
            from core.self_improve.self_development import SelfDevelopmentEngine

            engine = SelfDevelopmentEngine(
                max_cycles=max_cycles,
                skip_agent_smoke=skip_smoke,
                include_runtime=True,
                include_repo=True,
                sleep_between_cycles_s=0.0,
            )
            if self.orchestrator is not None:
                sm = getattr(self.orchestrator, "self_model", None)
                if sm is not None:
                    sm.set_development(
                        active=True,
                        cycle=int(engine.state.load().get("current_cycle") or 0),
                        phase="OBSERVE",
                        candidate="",
                    )
                    self.orchestrator.emit(
                        "self_dev.cycle_started",
                        {"cycle": int(engine.state.load().get("current_cycle") or 0)},
                    )
            code = engine.run()
            st = engine.state.status_summary()
            if self.orchestrator is not None:
                sm = getattr(self.orchestrator, "self_model", None)
                if sm is not None:
                    sm.set_development(
                        active=False,
                        cycle=int(st.get("current_cycle") or 0),
                        phase=str(st.get("current_phase") or "STOPPED"),
                        candidate=str(st.get("current_candidate") or ""),
                        last_improvement=str(st.get("last_successful_improvement") or ""),
                        last_rollback=str(st.get("last_failed_improvement") or ""),
                    )
                    if st.get("last_successful_improvement"):
                        sm.note_success(str(st.get("last_successful_improvement")))
            return AgentResult(
                task_id=task_id,
                status="ok" if code == 0 else "failed",
                summary=(
                    f"Self-development finished code={code} "
                    f"phase={st.get('current_phase')} "
                    f"last={st.get('last_result')} "
                    f"{analyst_summary}"
                ).strip(),
                confidence=0.7,
                artifacts={"status": st, "analyst": analyst_summary},
                source_agent=self.identity.agent_id,
            )
        except Exception as exc:
            traceback.print_exc()
            return AgentResult(
                task_id=task_id,
                status="failed",
                summary="Self-development agent failed",
                errors=[str(exc)],
                source_agent=self.identity.agent_id,
            )
