# ==========================
# ELIAN — runtime lifecycle (Phase 6)
# ==========================

from __future__ import annotations

import threading
import time
from typing import Any, Callable, List, Optional

from core.clock import Clock, SystemClock
from core.event_bus import EventBus, get_event_bus, set_event_bus
from core.proactive import ProactiveManager
from core.system_state import SystemStateStore, get_system_state_store
from watchers.base import Watcher
from watchers.home_assistant import HomeAssistantWatcher
from watchers.system import SystemWatcher


def _fish_healthy(st: Any) -> bool:
    try:
        if (st.extras or {}).get("fish_readiness") == "READY":
            return True
        fish = (st.extras or {}).get("fish") or {}
        if fish.get("extras", {}).get("readiness") == "READY":
            return True
        return bool(fish.get("healthy"))
    except Exception:
        return False


class ElianRuntime:
    """Owns EventBus, ProactiveManager, watchers, and background poll loop."""

    def __init__(
        self,
        *,
        bus: Optional[EventBus] = None,
        proactive: Optional[ProactiveManager] = None,
        watchers: Optional[List[Watcher]] = None,
        clock: Optional[Clock] = None,
        state_store: Optional[SystemStateStore] = None,
        speak_fn: Optional[Callable[[str], None]] = None,
        busy_check: Optional[Callable[[], bool]] = None,
        preference_get: Optional[Callable] = None,
        poll_sleep: float = 1.0,
        start_background: bool = True,
        conversation_manager: Optional[Any] = None,
    ):
        self.clock = clock or SystemClock()
        self.bus = bus or EventBus(clock=self.clock)
        set_event_bus(self.bus)
        self.state_store = state_store or get_system_state_store()
        self.proactive = proactive or ProactiveManager(
            clock=self.clock,
            speak_fn=speak_fn,
            busy_check=busy_check,
            preference_get=preference_get,
        )
        self.watchers = list(watchers) if watchers is not None else []
        if watchers is None:
            from watchers.os_notifications import OsNotificationWatcher
            from watchers.reminders import CalendarWatcher, ReminderWatcher
            from watchers.twilio_sms import TwilioSmsWatcher

            self.watchers = [
                SystemWatcher(
                    self.bus, clock=self.clock, state_store=self.state_store
                ),
                HomeAssistantWatcher(
                    self.bus, clock=self.clock, state_store=self.state_store
                ),
                ReminderWatcher(self.bus, clock=self.clock),
                CalendarWatcher(self.bus, clock=self.clock),
                OsNotificationWatcher(self.bus, clock=self.clock),
                TwilioSmsWatcher(self.bus, clock=self.clock),
            ]
        self.poll_sleep = float(poll_sleep)
        self.start_background = start_background
        self.conversation_manager = conversation_manager
        # Prefer ConversationManager speech path for proactive deliveries.
        try:
            self.proactive.conversation_manager = conversation_manager
        except Exception:
            pass
        self._companion = None
        self._orchestrator = None
        self._control_center = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        try:
            self.proactive.conversation_manager = self.conversation_manager
        except Exception:
            pass
        self.bus.subscribe(self.proactive.handle_event)
        try:
            from core.world_state import ingest_event

            self.bus.subscribe(ingest_event)
        except Exception:
            pass
        for watcher in self.watchers:
            watcher.start()
        self._stop.clear()
        if self.start_background:
            self._thread = threading.Thread(
                target=self._loop,
                name="elian-runtime",
                daemon=True,
            )
            self._thread.start()
        try:
            from core.companion.server import maybe_start_companion

            self._companion = maybe_start_companion(
                conversation_manager=self.conversation_manager,
                bus=self.bus,
                clock=self.clock,
            )
        except Exception as exc:
            print(f"[COMPANION] start skipped: {exc}")
            self._companion = None

        # Multi-agent orchestrator + Control Center (isolated failures).
        try:
            self._start_orchestrator()
        except Exception as exc:
            print(f"[ORCHESTRATOR] start skipped: {exc}")
            self._orchestrator = None

        self._started = True
        print("[RUNTIME] EventBus + Proactive + watchers started")

    def _start_orchestrator(self) -> None:
        from Config import config, get_config_value
        from core.multi_agent import ElianOrchestrator, set_orchestrator
        from voice.tts_engine import get_tts_engine

        ma = config.get("multi_agent") if isinstance(config.get("multi_agent"), dict) else {}
        lang = str(config.get("language") or "en-US")
        tool_registry = None
        try:
            if self.conversation_manager is not None:
                tool_registry = getattr(self.conversation_manager, "registry", None)
        except Exception:
            tool_registry = None

        orch = ElianOrchestrator(
            max_agents_total=int(ma.get("max_agents_total") or 24),
            max_concurrent_agents=int(ma.get("max_concurrent_agents") or 8),
            max_children_per_agent=int(ma.get("max_children_per_agent") or 6),
            max_spawn_depth=int(ma.get("max_spawn_depth") or 3),
            max_agents_per_task=int(ma.get("max_agents_per_task") or 6),
            agent_task_timeout_s=float(ma.get("agent_task_timeout_s") or 120.0),
            background_resource_budget=float(ma.get("background_resource_budget") or 0.35),
            conversation_manager=self.conversation_manager,
            tool_registry=tool_registry,
            event_bus=self.bus,
            language=lang,
            mode=str(ma.get("mode") or "NORMAL"),
        )
        # Apply measured hardware limits (never invent 32 GB VRAM).
        try:
            limits = orch.resources.apply_hardware_limits()
            # Config may further cap concurrency for ~12 GB GPUs.
            if ma.get("max_concurrent_agents") is not None:
                orch.max_concurrent_agents = int(ma.get("max_concurrent_agents") or 3)
                orch.resources.max_concurrent_normal = min(
                    orch.resources.max_concurrent_normal,
                    orch.max_concurrent_agents,
                )
            print(
                f"[ORCHESTRATOR] hardware limits gpu_slots={limits.get('max_gpu_slots')} "
                f"concurrent={orch.resources.max_concurrent_normal} "
                f"bg_workers={limits.get('max_background_workers')}"
            )
        except Exception as exc:
            print(f"[ORCHESTRATOR] hardware probe soft-fail: {exc}")

        orch.recover()
        tts = get_tts_engine()
        st = tts.health_check()
        fish_ready = _fish_healthy(st)
        fb_ready = str((st.extras.get("fallback") or {}).get("extras", {}).get("readiness") or "")
        if not fb_ready:
            fb_ready = "READY" if (st.extras.get("fallback") or {}).get("healthy") else "UNAVAILABLE"
        orch.self_model.update(
            voice_engine=st.extras.get("active") or st.engine,
            runtime_state={
                **(orch.self_model.snapshot().get("runtime_state") or {}),
                "tts": {
                    "active": st.extras.get("active"),
                    "fish": "READY" if fish_ready else "UNAVAILABLE",
                    "fallback": fb_ready,
                    "detail": st.detail,
                },
                "stt": {
                    "engine": "whisper",
                    "language": str(
                        (config.get("whisper") or {}).get("language")
                        if isinstance(config.get("whisper"), dict)
                        else "en"
                    ),
                },
                "hardware": orch.resources.hardware.snapshot(),
            },
        )
        if not fish_ready:
            orch.self_model.add_limitation(
                "Fish Audio S2 Pro UNAVAILABLE — English Piper fallback active"
            )

        # Product mic/runtime NEVER auto-starts self-improve.
        # Self-development is an engineering mode only (CLI / explicit supervised API).
        # Both multi_agent.background_self_development AND self_improve.autonomous.enabled
        # must be true to even consider background start — and the product path still
        # refuses it. Kill-switches remain for any future opt-in wiring.
        import os
        import sys

        start_sd = False
        disable_env = str(
            os.environ.get("ELIAN_DISABLE_BACKGROUND_SELF_DEV") or ""
        ).lower() in ("1", "true", "yes")
        argv_l = " ".join(sys.argv).lower()
        disable_runner = ("unittest" in argv_l or "pytest" in argv_l) and not str(
            os.environ.get("ELIAN_ALLOW_SELF_DEV_IN_TESTS") or ""
        ).lower() in ("1", "true", "yes")
        auto_on = False
        try:
            auto = get_config_value("self_improve", "autonomous", default={}) or {}
            auto_on = isinstance(auto, dict) and auto.get("enabled") is True
        except Exception:
            auto_on = False
        bg_flag = bool(ma.get("background_self_development")) if isinstance(ma, dict) else False
        # Hard rule: ElianRuntime must not launch background self-dev on the mic path.
        # Even if both flags are true, product runtime stays False (use CLI/supervised).
        if auto_on and bg_flag and not disable_env and not disable_runner:
            print(
                "[SELF-DEV] config requested background self-dev; "
                "refused by product runtime (use CLI/supervised)"
            )
        start_sd = False

        try:
            orch.start(start_self_development=start_sd, self_dev_max_cycles=0)
        except Exception as exc:
            print(f"[SELF-DEV] background start failed (conversation continues): {exc}")
            orch.self_model.set_development(
                active=False, phase="FAILED", candidate="", cycle=0
            )
            orch.self_model.note_failure(f"self_development_start_failed:{exc}")
            orch.self_model.update(health="DEGRADED")
            if not orch._started:
                orch.start(start_self_development=False)
        set_orchestrator(orch)
        self._orchestrator = orch
        try:
            from core.control_center import maybe_start_control_center

            self._control_center = maybe_start_control_center(orchestrator=orch)
        except Exception as exc:
            print(f"[CONTROL] start skipped: {exc}")
            self._control_center = None

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        for watcher in self.watchers:
            try:
                watcher.stop()
            except Exception:
                pass
        try:
            self.bus.unsubscribe(self.proactive.handle_event)
        except Exception:
            pass
        try:
            from core.world_state import ingest_event

            self.bus.unsubscribe(ingest_event)
        except Exception:
            pass
        if self._control_center is not None:
            try:
                self._control_center.stop()
            except Exception:
                pass
            self._control_center = None
        if self._orchestrator is not None:
            try:
                from core.multi_agent import set_orchestrator

                self._orchestrator.stop()
                set_orchestrator(None)
            except Exception:
                pass
            self._orchestrator = None
        if self._companion is not None:
            try:
                self._companion.stop()
            except Exception:
                pass
            self._companion = None
        self._thread = None
        self._started = False
        print("[RUNTIME] EventBus shutdown reason=runtime.stop")
        print("[RUNTIME] stopped")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                print(f"[RUNTIME] tick error: {exc}")
            self._stop.wait(timeout=self.poll_sleep)

    def tick(self) -> None:
        for watcher in self.watchers:
            watcher.poll_if_due()
        try:
            self.proactive.tick_future_intents()
        except Exception:
            pass
        self.proactive.try_deliver()

    def threads_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())
