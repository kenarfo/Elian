# ==========================
# ELIAN — desktop HUD tests (Phase 13 / HUD track)
# Headless: no display required. PySide6 optional.
# ==========================

from __future__ import annotations

import ast
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.hud_events import (
    HudDisplayState,
    HudEvent,
    HudEventQueue,
    HudEventType,
    HudSnapshot,
    apply_event,
    dispatch_to_view,
    emit_error,
    emit_goal_started,
    emit_integration,
    emit_memory_candidate,
    emit_memory_saved,
    emit_plan_updated,
    emit_response,
    emit_situation,
    emit_state_changed,
    emit_tool_finished,
    emit_tool_started,
    emit_transcript,
    emit_world_state_updated,
    get_hud_queue,
    map_speech_state,
    reset_hud_events_for_tests,
    status_label_pl,
)
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from ui.hud_host import HudHost, hud_enabled_from_cli_and_config, try_start_hud
from voice.state import SpeechState


_BANNED_HUD_IMPORTS = (
    "ConversationManager",
    "core.conversation_manager",
    "core.planner",
    "core.plan_cycle",
    "core.semantic_executive",
    "ToolRegistry",
    "core.tools",
)


class HudEventBindingTests(unittest.TestCase):
    def setUp(self):
        reset_hud_events_for_tests()

    def tearDown(self):
        reset_hud_events_for_tests()

    def test_subscribe_receives_emits(self):
        seen = []
        get_hud_queue().subscribe(lambda ev: seen.append(ev.type))
        emit_state_changed("PROCESSING")
        emit_transcript("hej")
        emit_response("Słucham.")
        self.assertEqual(
            seen,
            [
                HudEventType.STATE_CHANGED,
                HudEventType.TRANSCRIPT_RECEIVED,
                HudEventType.RESPONSE_CREATED,
            ],
        )

    def test_queue_never_blocks_when_full(self):
        q = get_hud_queue()
        started = time.monotonic()
        for i in range(200):
            emit_transcript(f"x{i}")
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.5)
        drained = q.drain()
        self.assertLessEqual(len(drained), 64)
        self.assertGreater(q.dropped, 0)

    def test_tool_registry_emits_started_and_finished(self):
        registry = ToolRegistry(confirm_medium=False)
        reset_registry_for_tests(registry)
        registry.register(
            Tool(
                name="echo",
                description="echo",
                handler=lambda args: ToolResult.ok("ok", tool_name="echo"),
                risk_level=RiskLevel.SAFE,
            )
        )
        reset_hud_events_for_tests()
        registry.invoke("echo", {})
        types = [e.type for e in get_hud_queue().drain()]
        self.assertEqual(
            types,
            [HudEventType.TOOL_STARTED, HudEventType.TOOL_FINISHED],
        )
        reset_registry_for_tests(None)

    def test_unsubscribe_stops_delivery(self):
        seen = []

        def handler(ev):
            seen.append(ev.type)

        q = get_hud_queue()
        q.subscribe(handler)
        emit_transcript("a")
        q.unsubscribe(handler)
        emit_transcript("b")
        self.assertEqual(seen, [HudEventType.TRANSCRIPT_RECEIVED])


class HudStateTransitionTests(unittest.TestCase):
    def test_speech_mapping_all_core_states(self):
        self.assertEqual(map_speech_state(SpeechState.IDLE), HudDisplayState.IDLE)
        self.assertEqual(
            map_speech_state(SpeechState.WAKE_LISTENING), HudDisplayState.AWARE
        )
        self.assertEqual(
            map_speech_state(SpeechState.ACTIVE_LISTENING),
            HudDisplayState.LISTENING,
        )
        self.assertEqual(
            map_speech_state(SpeechState.PROCESSING), HudDisplayState.THINKING
        )
        self.assertEqual(map_speech_state(SpeechState.ACTING), HudDisplayState.ACTING)
        self.assertEqual(
            map_speech_state(SpeechState.SPEAKING), HudDisplayState.SPEAKING
        )
        self.assertEqual(
            map_speech_state(SpeechState.INTERRUPTED),
            HudDisplayState.INTERRUPTED,
        )
        self.assertEqual(map_speech_state("AWARE"), HudDisplayState.AWARE)
        self.assertEqual(map_speech_state("THINKING"), HudDisplayState.THINKING)

    def test_apply_event_full_turn_sequence(self):
        snap = HudSnapshot()
        snap = apply_event(
            snap,
            HudEvent(HudEventType.STATE_CHANGED, {"speech": "WAKE_LISTENING"}),
        )
        self.assertEqual(snap.state, HudDisplayState.AWARE)
        snap = apply_event(
            snap,
            HudEvent(HudEventType.STATE_CHANGED, {"speech": "ACTIVE_LISTENING"}),
        )
        self.assertEqual(snap.state, HudDisplayState.LISTENING)
        self.assertTrue(snap.mic_open)
        snap = apply_event(
            snap,
            HudEvent(HudEventType.TRANSCRIPT_RECEIVED, {"text": "odpal spotify"}),
        )
        self.assertIn("spotify", snap.transcript)
        snap = apply_event(
            snap,
            HudEvent(HudEventType.STATE_CHANGED, {"speech": "PROCESSING"}),
        )
        self.assertEqual(snap.state, HudDisplayState.THINKING)
        self.assertFalse(snap.mic_open)
        snap = apply_event(
            snap, HudEvent(HudEventType.TOOL_STARTED, {"name": "open_app"})
        )
        snap = apply_event(
            snap, HudEvent(HudEventType.STATE_CHANGED, {"speech": "ACTING"})
        )
        self.assertEqual(snap.state, HudDisplayState.ACTING)
        self.assertEqual(snap.tool, "open_app")
        snap = apply_event(
            snap,
            HudEvent(
                HudEventType.TOOL_FINISHED, {"name": "open_app", "success": True}
            ),
        )
        self.assertEqual(snap.tool, "")
        snap = apply_event(
            snap,
            HudEvent(HudEventType.RESPONSE_CREATED, {"text": "Uruchomiłem Spotify."}),
        )
        snap = apply_event(
            snap, HudEvent(HudEventType.STATE_CHANGED, {"speech": "SPEAKING"})
        )
        self.assertEqual(snap.state, HudDisplayState.SPEAKING)
        self.assertEqual(snap.reply, "Uruchomiłem Spotify.")
        snap = apply_event(
            snap, HudEvent(HudEventType.STATE_CHANGED, {"speech": "INTERRUPTED"})
        )
        self.assertEqual(snap.state, HudDisplayState.INTERRUPTED)
        snap = apply_event(
            snap, HudEvent(HudEventType.STATE_CHANGED, {"speech": "IDLE"})
        )
        self.assertEqual(snap.state, HudDisplayState.IDLE)

    def test_muted_error_degraded_offline_overlays(self):
        snap = apply_event(
            HudSnapshot(),
            HudEvent(HudEventType.STATE_CHANGED, {"speech": "IDLE", "muted": True}),
        )
        self.assertEqual(snap.state, HudDisplayState.MUTED)

        snap = apply_event(
            HudSnapshot(),
            HudEvent(HudEventType.ERROR_OCCURRED, {"message": "boom"}),
        )
        self.assertEqual(snap.state, HudDisplayState.ERROR)

        snap = apply_event(
            HudSnapshot(),
            HudEvent(
                HudEventType.INTEGRATION_STATUS_CHANGED,
                {"name": "ollama", "status": "degraded"},
            ),
        )
        self.assertEqual(snap.state, HudDisplayState.DEGRADED)

        snap = apply_event(
            HudSnapshot(),
            HudEvent(
                HudEventType.INTEGRATION_STATUS_CHANGED,
                {"name": "ha", "status": "offline"},
            ),
        )
        self.assertEqual(snap.state, HudDisplayState.OFFLINE)

    def test_rapid_transitions_remain_consistent(self):
        snap = HudSnapshot()
        sequence = [
            "ACTIVE_LISTENING",
            "PROCESSING",
            "ACTING",
            "SPEAKING",
            "INTERRUPTED",
            "IDLE",
            "WAKE_LISTENING",
            "PROCESSING",
            "IDLE",
        ]
        for name in sequence:
            snap = apply_event(
                snap, HudEvent(HudEventType.STATE_CHANGED, {"speech": name})
            )
        self.assertEqual(snap.state, HudDisplayState.IDLE)
        self.assertEqual(snap.speech_name, "IDLE")

    def test_integration_status_fields(self):
        snap = HudSnapshot()
        snap = apply_event(
            snap,
            HudEvent(
                HudEventType.INTEGRATION_STATUS_CHANGED,
                {"name": "ha", "status": "ready"},
            ),
        )
        snap = apply_event(
            snap,
            HudEvent(
                HudEventType.INTEGRATION_STATUS_CHANGED,
                {"name": "ollama", "status": "ready"},
            ),
        )
        snap = apply_event(
            snap,
            HudEvent(
                HudEventType.INTEGRATION_STATUS_CHANGED,
                {"name": "voice", "status": "ready"},
            ),
        )
        snap = apply_event(
            snap,
            HudEvent(
                HudEventType.INTEGRATION_STATUS_CHANGED,
                {"name": "whisper", "status": "online"},
            ),
        )
        snap = apply_event(
            snap,
            HudEvent(
                HudEventType.INTEGRATION_STATUS_CHANGED,
                {"name": "piper", "status": "online"},
            ),
        )
        snap = apply_event(
            snap,
            HudEvent(
                HudEventType.INTEGRATION_STATUS_CHANGED,
                {"name": "browser", "status": "disabled"},
            ),
        )
        snap = apply_event(
            snap,
            HudEvent(
                HudEventType.INTEGRATION_STATUS_CHANGED,
                {"name": "phone", "status": "offline"},
            ),
        )
        self.assertEqual(snap.ha, "online")
        self.assertEqual(snap.ollama, "online")
        self.assertEqual(snap.voice, "online")
        self.assertEqual(snap.whisper, "online")
        self.assertEqual(snap.piper, "online")
        self.assertEqual(snap.browser, "disabled")
        self.assertEqual(snap.phone, "offline")
        self.assertFalse(snap.offline)  # ha/voice online; phone offline alone OK
        self.assertFalse(snap.degraded)


class HudCrashIsolationTests(unittest.TestCase):
    def setUp(self):
        reset_hud_events_for_tests()

    def tearDown(self):
        reset_hud_events_for_tests()

    def test_subscriber_exception_does_not_break_emit(self):
        def boom(_event):
            raise RuntimeError("gui boom")

        q = get_hud_queue()
        q.subscribe(boom)
        emit_transcript("hej")
        emit_response("cześć")
        self.assertEqual(q.subscriber_errors, 2)
        types = [e.type for e in q.drain()]
        self.assertEqual(
            types,
            [HudEventType.TRANSCRIPT_RECEIVED, HudEventType.RESPONSE_CREATED],
        )

    def test_dispatch_to_view_swallows_paint_errors(self):
        class BoomView:
            def apply(self, event):
                raise RuntimeError("paint fail")

        ok = dispatch_to_view(
            BoomView(),
            HudEvent(HudEventType.STATE_CHANGED, {"speech": "IDLE"}),
        )
        self.assertFalse(ok)
        emit_state_changed("SPEAKING")
        self.assertTrue(get_hud_queue().drain())

    def test_host_thread_crash_does_not_kill_caller(self):
        def boom():
            raise RuntimeError("qt exploded")

        host = try_start_hud(enabled=True, run_fn=boom)
        self.assertIsNotNone(host)
        deadline = time.monotonic() + 2.0
        while host.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(host.crashed)
        self.assertFalse(host.is_alive())
        # Runtime observation bus still works after HUD death.
        before = get_hud_queue().dropped
        emit_transcript("voice still works")
        emit_state_changed("IDLE")
        drained = get_hud_queue().drain()
        types = {e.type for e in drained}
        self.assertIn(HudEventType.TRANSCRIPT_RECEIVED, types)
        self.assertIn(HudEventType.STATE_CHANGED, types)
        self.assertEqual(get_hud_queue().dropped, before)

    def test_disabled_hud_does_not_start_thread(self):
        host = try_start_hud(enabled=False)
        self.assertIsNone(host)

    def test_render_exception_increments_counter_not_raise(self):
        host = HudHost(run_fn=lambda: None)
        host._window = MagicMock()
        host._window.render.side_effect = RuntimeError("paint")
        emit_state_changed("IDLE")
        host._drain_into_window()
        self.assertEqual(host.render_errors, 1)

    def test_multiple_start_while_alive_is_noop(self):
        gate = threading.Event()

        def hold():
            gate.wait(timeout=2.0)

        host = HudHost(run_fn=hold)
        host.start()
        first = host._thread
        host.start()
        self.assertIs(host._thread, first)
        gate.set()
        host.stop()
        self.assertFalse(host.is_alive())

    def test_hud_stop_does_not_stop_runtime(self):
        runtime = MagicMock()
        pipeline = MagicMock()
        pipeline.state = None
        host = HudHost(pipeline=pipeline, runtime=runtime, run_fn=lambda: None)
        host.start()
        deadline = time.monotonic() + 2.0
        while host.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        host.stop()
        runtime.stop.assert_not_called()
        pipeline.close.assert_not_called()

    def test_tray_close_signal_only_stops_hud(self):
        """Tray 'Zamknij HUD' sets stop event; runtime mock stays untouched."""
        runtime = MagicMock()
        host = HudHost(runtime=runtime, run_fn=lambda: time.sleep(0.3))
        host.start()
        host._stop.set()
        host.stop()
        runtime.stop.assert_not_called()
        self.assertFalse(host.is_alive())


class HudLifecycleTests(unittest.TestCase):
    def setUp(self):
        reset_hud_events_for_tests()
        os_environ = patch.dict("os.environ", {"QT_QPA_PLATFORM": "offscreen"})
        self._env = os_environ
        self._env.start()

    def tearDown(self):
        self._env.stop()
        reset_hud_events_for_tests()

    def test_repeated_show_hide_offscreen(self):
        try:
            from PySide6.QtWidgets import QApplication
            from ui.hud_window import HudWindow
        except Exception:
            self.skipTest("PySide6 unavailable")

        app = QApplication.instance() or QApplication(["elian-hud-test"])
        window = HudWindow(always_on_top=False, compact=False)
        for _ in range(5):
            window.show()
            self.assertTrue(window.is_visible())
            window.hide()
            self.assertFalse(window.is_visible())
        snap = HudSnapshot(state=HudDisplayState.LISTENING, speech_name="ACTIVE_LISTENING")
        window.render(snap)
        window.show()
        window.hide()
        _ = app  # keep ref

    def test_close_handler_hides_not_quit(self):
        try:
            from PySide6.QtWidgets import QApplication
            from ui.hud_window import HudWindow
        except Exception:
            self.skipTest("PySide6 unavailable")

        app = QApplication.instance() or QApplication(["elian-hud-test-close"])
        window = HudWindow(always_on_top=False, compact=True)
        hidden = []
        window.set_close_handler(lambda: hidden.append(True) or window.hide())
        window.show()
        window.widget.close()
        self.assertTrue(hidden)
        self.assertFalse(window.is_visible())
        _ = app


class HudArchitectureGuardTests(unittest.TestCase):
    def setUp(self):
        reset_hud_events_for_tests()

    def tearDown(self):
        reset_hud_events_for_tests()

    def test_core_sources_do_not_import_pyside(self):
        core_dir = PROJECT_ROOT / "core"
        banned = []
        for path in core_dir.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name.startswith("PySide") or name.startswith("ui.hud"):
                        banned.append(f"{path.name}:{name}")
        self.assertEqual(banned, [])

    def test_hud_modules_do_not_own_runtime_or_tools(self):
        ui_dir = PROJECT_ROOT / "ui"
        banned = []
        for path in ui_dir.glob("hud_*.py"):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module] + [a.name for a in node.names]
                for name in names:
                    for banned_name in _BANNED_HUD_IMPORTS:
                        if name == banned_name or name.endswith("." + banned_name):
                            banned.append(f"{path.name}:{name}")
            # Soft string scan for direct invoke patterns beyond imports
            if "ToolRegistry(" in source or ".invoke(" in source:
                # allow comments mentioning them
                for i, line in enumerate(source.splitlines(), 1):
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                        continue
                    if "ToolRegistry(" in stripped or (
                        ".invoke(" in stripped and "subscribe" not in stripped
                    ):
                        banned.append(f"{path.name}:{i}:{stripped[:60]}")
        self.assertEqual(banned, [])

    def test_no_circular_import_hud_events_host(self):
        """hud_events stays Qt-free; host imports events one-way only."""
        events_src = (PROJECT_ROOT / "core" / "hud_events.py").read_text(encoding="utf-8")
        host_src = (PROJECT_ROOT / "ui" / "hud_host.py").read_text(encoding="utf-8")
        for line in events_src.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ui", "from ui", "import PySide", "from PySide")):
                self.fail(f"hud_events imports GUI: {stripped}")
        self.assertIn("from core.hud_events import", host_src)
        self.assertNotIn("from ui.hud_host", events_src)
        self.assertNotIn("import ui.hud_host", events_src)

    def test_hud_default_off(self):
        with patch("ui.hud_host.get_config_value", return_value=False), patch.object(
            sys, "argv", ["main.py"]
        ):
            self.assertFalse(hud_enabled_from_cli_and_config())

    def test_hud_does_not_execute_tools_directly(self):
        """HUD observes tool events; it must not call ToolRegistry.invoke."""
        registry = ToolRegistry(confirm_medium=False)
        reset_registry_for_tests(registry)
        called = []

        def handler(args):
            called.append(args)
            return ToolResult.ok("ok", tool_name="echo")

        registry.register(
            Tool(
                name="echo",
                description="echo",
                handler=handler,
                risk_level=RiskLevel.SAFE,
            )
        )
        reset_hud_events_for_tests()
        # Simulate HUD observing tool events without invoking
        emit_tool_started("echo")
        emit_tool_finished("echo", success=True)
        self.assertEqual(called, [])
        types = [e.type for e in get_hud_queue().drain()]
        self.assertEqual(
            types,
            [HudEventType.TOOL_STARTED, HudEventType.TOOL_FINISHED],
        )
        reset_registry_for_tests(None)


class HudCliFlagTests(unittest.TestCase):
    def test_hud_flag_enables(self):
        with patch.object(sys, "argv", ["main.py", "--hud"]):
            self.assertTrue(hud_enabled_from_cli_and_config())

    def test_no_hud_flag_disables(self):
        with patch.object(sys, "argv", ["main.py", "--hud", "--no-hud"]):
            self.assertFalse(hud_enabled_from_cli_and_config())


class HudOptionalSmokeTests(unittest.TestCase):
    def test_hud_smoke_never_raises(self):
        from ui.hud_smoke import run_hud_smoke

        code = run_hud_smoke()
        self.assertEqual(code, 0)


class HudGuiSurfacesTests(unittest.TestCase):
    def setUp(self):
        reset_hud_events_for_tests()

    def tearDown(self):
        reset_hud_events_for_tests()

    def test_goal_and_plan_progress(self):
        emit_goal_started(
            "Kalendarz",
            steps=[
                {"id": "1", "label": "Połącz", "done": False},
                {"id": "2", "label": "Pobierz", "done": False},
                {"id": "3", "label": "Podsumuj", "done": False},
            ],
            thought="Planuję 3 kroki",
        )
        snap = HudSnapshot()
        for ev in get_hud_queue().drain():
            snap = apply_event(snap, ev)
        self.assertEqual(snap.goal_title, "Kalendarz")
        self.assertEqual(len(snap.plan_steps), 3)
        self.assertIn("3", snap.thought)
        emit_plan_updated(
            steps=[
                {"id": "1", "label": "Połącz", "done": True},
                {"id": "2", "label": "Pobierz", "done": True},
                {"id": "3", "label": "Podsumuj", "done": False},
            ]
        )
        for ev in get_hud_queue().drain():
            snap = apply_event(snap, ev)
        self.assertEqual(sum(1 for s in snap.plan_steps if s.done), 2)

    def test_proactive_situation_card(self):
        emit_situation("Spotkanie", body="za 15 min", situation_id="s1")
        snap = HudSnapshot()
        for ev in get_hud_queue().drain():
            snap = apply_event(snap, ev)
        self.assertEqual(len(snap.situations), 1)
        self.assertEqual(snap.situations[0].id, "s1")
        self.assertTrue(snap.notifications)
        snap = apply_event(
            snap,
            HudEvent(HudEventType.PLAN_UPDATED, {"dismiss_situation_id": "s1"}),
        )
        self.assertEqual(snap.situations, ())

    def test_memory_candidate_and_saved(self):
        emit_memory_candidate("lubi jazz")
        snap = HudSnapshot()
        for ev in get_hud_queue().drain():
            snap = apply_event(snap, ev)
        self.assertEqual(snap.memory_kind, "candidate")
        emit_memory_saved("lubi jazz", kind="persistent")
        for ev in get_hud_queue().drain():
            snap = apply_event(snap, ev)
        self.assertEqual(snap.memory_kind, "persistent")
        self.assertIn("jazz", snap.memory_hint)

    def test_world_state_throttled(self):
        snap = HudSnapshot()
        t0 = 1000.0
        snap = apply_event(
            snap,
            HudEvent(
                HudEventType.WORLD_STATE_UPDATED,
                {"world": {"DOM": "a", "PC": "x"}, "force": False},
            ),
            world_throttle_s=0.5,
            now=t0,
        )
        self.assertEqual(snap.world.get("DOM"), "a")
        # Within throttle — ignored
        snap = apply_event(
            snap,
            HudEvent(
                HudEventType.WORLD_STATE_UPDATED,
                {"world": {"DOM": "b"}, "force": False},
            ),
            world_throttle_s=0.5,
            now=t0 + 0.1,
        )
        self.assertEqual(snap.world.get("DOM"), "a")
        # After throttle
        snap = apply_event(
            snap,
            HudEvent(
                HudEventType.WORLD_STATE_UPDATED,
                {"world": {"DOM": "c"}, "force": False},
            ),
            world_throttle_s=0.5,
            now=t0 + 0.6,
        )
        self.assertEqual(snap.world.get("DOM"), "c")
        # Force bypasses throttle
        snap = apply_event(
            snap,
            HudEvent(
                HudEventType.WORLD_STATE_UPDATED,
                {"world": {"DOM": "d"}, "force": True},
            ),
            world_throttle_s=0.5,
            now=t0 + 0.65,
        )
        self.assertEqual(snap.world.get("DOM"), "d")

    def test_status_labels_polish(self):
        self.assertEqual(status_label_pl(HudDisplayState.LISTENING), "Słucham")
        self.assertEqual(status_label_pl(HudDisplayState.THINKING), "Myślę")
        snap = HudSnapshot(
            state=HudDisplayState.ACTING,
            tool="calendar",
            status_text="Sprawdzam kalendarz",
        )
        self.assertEqual(status_label_pl(snap.state, snap), "Sprawdzam kalendarz")

    def test_all_display_states_projectable(self):
        for state in HudDisplayState:
            self.assertIsInstance(state.value, str)
            self.assertTrue(status_label_pl(state))

    def test_event_bridge_types_complete(self):
        required = {
            "state_changed",
            "transcript_received",
            "response_created",
            "goal_started",
            "plan_updated",
            "tool_started",
            "tool_finished",
            "world_state_updated",
            "situation_created",
            "proactive_message",
            "memory_candidate_created",
            "memory_saved",
            "integration_status_changed",
            "error_occurred",
        }
        present = {e.value for e in HudEventType}
        self.assertTrue(required.issubset(present))

    def test_mini_full_transition_offscreen(self):
        try:
            from PySide6.QtWidgets import QApplication
            from ui.hud_window import HudWindow
        except Exception:
            self.skipTest("PySide6 unavailable")

        with patch.dict("os.environ", {"QT_QPA_PLATFORM": "offscreen"}):
            app = QApplication.instance() or QApplication(["elian-hud-mini"])
            window = HudWindow(always_on_top=False, compact=False, mini=False)
            window.show()
            snap = HudSnapshot(state=HudDisplayState.LISTENING, transcript="hej")
            window.render(snap)
            window.set_mini(True)
            self.assertTrue(window.is_mini)
            window.set_mini(False)
            self.assertFalse(window.is_mini)
            window.set_compact(True)
            window.set_compact(False)
            window.hide()
            _ = app

    def test_widgets_do_not_import_backend(self):
        ui_dir = PROJECT_ROOT / "ui"
        for path in ui_dir.glob("hud_*.py"):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module in (
                        "core.conversation_manager",
                        "core.planner",
                        "core.tools",
                        "core.semantic_executive",
                    ):
                        self.fail(f"{path.name} imports {node.module}")


class HudQueueConcurrencyTests(unittest.TestCase):
    def setUp(self):
        reset_hud_events_for_tests(HudEventQueue(maxlen=32))

    def tearDown(self):
        reset_hud_events_for_tests()

    def test_concurrent_publish_drain_bounded(self):
        errors = []

        def publisher():
            try:
                for i in range(100):
                    emit_transcript(f"t{i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=publisher) for _ in range(4)]
        for t in threads:
            t.start()
        drained = 0
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            drained += len(get_hud_queue().drain())
            if all(not t.is_alive() for t in threads) and not get_hud_queue().drain():
                break
            time.sleep(0.005)
        for t in threads:
            t.join(timeout=1.0)
        self.assertEqual(errors, [])
        # Queue never grows unbounded; drops are expected under load.
        self.assertLessEqual(len(get_hud_queue().drain()), 32)


if __name__ == "__main__":
    unittest.main()
