# ==========================
# ELIAN — Phase 13 C.4 / C.5 / Jarvis tests
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.builtin_tools import build_default_registry
from core.computer_agent import (
    ComputerAgent,
    notepad_fixture_store,
    reset_computer_agent_for_tests,
    reset_computer_metrics,
)
from core.compound_plan import parse_compound_goal
from core.jarvis_response import (
    ResponseContext,
    dry_humor_allowed,
    execution_reply,
    polish_spoken,
    social_reply,
    strip_fake_feelings,
    strip_service_closers,
)
from core.model_client import FakeModelClient, reset_model_client
from core.perception.frame_provider import MemoryFrameProvider
from core.perception.metrics import reset_perception_metrics
from core.perception.screen_runtime import ScreenPerceptionRuntime, reset_screen_runtime_for_tests
from core.perception.visual_referent import VisualReferent
from core.perception.vision_router import LocalVisionProvider, VisionRouter
from core.planner import Planner
from core.tools import reset_registry_for_tests
from core.world_state import get_world_state, note_screen, reset_world_state
from memory.manager import MemoryManager
from vision.uia import FakeUIBackend
from WakeWord import extract_wake


HARDWARE_EQ = (
    "Hej Elian, otwórz notatnik i wpisz Elian działa jak agent i zaznasz cały tekst."
)


class Phase13C4InputTests(unittest.TestCase):
    def test_second_elian_survives_wake(self):
        wake = extract_wake(HARDWARE_EQ)
        self.assertTrue(wake.wake_detected)
        self.assertIn("Elian działa jak agent", wake.raw_command)
        self.assertNotEqual(wake.raw_command.lower().find("elian działa"), -1)
        # Second Elian is payload, not sliced away.
        self.assertTrue(wake.raw_command.startswith("otwórz"))

    def test_casing_survives_raw_pipeline(self):
        wake = extract_wake(HARDWARE_EQ)
        self.assertEqual(wake.raw_transcript, HARDWARE_EQ)
        self.assertIn("Elian działa jak agent", wake.raw_command)
        self.assertIn("elian działa jak agent", wake.normalized_command)

    def test_normalized_classification_works(self):
        wake = extract_wake(HARDWARE_EQ)
        plan = parse_compound_goal(wake.raw_command)
        self.assertTrue(plan.detected)
        self.assertEqual([s.family for s in plan.steps], ["APP", "COMPUTER", "COMPUTER"])

    def test_raw_type_payload_exact(self):
        wake = extract_wake(HARDWARE_EQ)
        plan = parse_compound_goal(wake.raw_command)
        type_step = plan.steps[1]
        self.assertEqual(type_step.semantic_action, "type_text")
        self.assertEqual(type_step.slots.get("text_payload"), "Elian działa jak agent")
        self.assertIn("Elian działa jak agent", type_step.raw_clause)

    def test_zaznasz_semantic_repair(self):
        from core.asr_repair import repair_action_verbs

        result = repair_action_verbs("zaznasz cały tekst")
        self.assertTrue(result.applied)
        self.assertEqual(result.canonical, "zaznacz cały tekst")
        self.assertEqual(result.heard, "zaznasz cały tekst")

    def test_unrelated_near_match_rejected(self):
        from core.asr_repair import repair_action_verbs

        result = repair_action_verbs("michał jackson na spotify")
        self.assertFalse(result.applied)
        self.assertEqual(result.canonical, "michał jackson na spotify")

    def test_destructive_typo_does_not_bypass_safety(self):
        from core.asr_repair import repair_action_verbs

        result = repair_action_verbs("usóń ten plik")
        self.assertTrue(result.needs_confirmation)
        self.assertFalse(result.applied)
        self.assertEqual(result.heard, "usóń ten plik")

    def test_compound_raw_spans_align(self):
        text = "Otwórz notatnik i wpisz Elian działa jak agent i zaznacz cały tekst."
        plan = parse_compound_goal(text)
        self.assertEqual(len(plan.steps), 3)
        type_step = plan.steps[1]
        self.assertEqual(text[type_step.raw_start:type_step.raw_end], type_step.raw_clause)
        self.assertEqual(type_step.raw_clause, "wpisz Elian działa jak agent")
        self.assertEqual(type_step.normalized_clause.lower().startswith("wpisz"), True)

    def test_hardware_equivalent_three_steps(self):
        wake = extract_wake(HARDWARE_EQ)
        plan = parse_compound_goal(wake.raw_command)
        self.assertEqual([s.family for s in plan.steps], ["APP", "COMPUTER", "COMPUTER"])
        self.assertEqual(plan.steps[0].semantic_action, "open_service")
        self.assertEqual(plan.steps[1].slots.get("text_payload"), "Elian działa jak agent")
        self.assertEqual(plan.steps[2].semantic_action, "select_all")


class Phase13C3RegressionTests(unittest.TestCase):
    def setUp(self):
        reset_computer_metrics()
        self.store = notepad_fixture_store()
        self.agent = ComputerAgent(self.store)
        self.agent._fake_ui = FakeUIBackend()
        reset_computer_agent_for_tests(self.agent)

    def test_select_all_still_focuses_notepad(self):
        from core.computer_agent import add_cursor_window

        add_cursor_window(self.store)
        self.store.focus("win_cursor")
        result = self.agent.act("select_all", {"semantic_action": "select_all", "app_name": "Notatnik"})
        self.assertTrue(result.success)
        self.assertEqual(self.store.get_active().window_id, "win_notepad")


class Phase13C5PerceptionTests(unittest.TestCase):
    def setUp(self):
        reset_perception_metrics()
        reset_world_state()
        self.provider = MemoryFrameProvider()
        self.vision = VisionRouter(
            provider=LocalVisionProvider(
                mock={"description": "ok", "candidates": [{"label": "x", "role": "button", "bbox": [1, 1, 2, 2], "confidence": 0.94}]}
            )
        )
        self.runtime = ScreenPerceptionRuntime(provider=self.provider, vision=self.vision)
        reset_screen_runtime_for_tests(self.runtime)

    def tearDown(self):
        self.runtime.stop()

    def test_start_stop_cleanly(self):
        self.runtime.start()
        self.assertTrue(self.runtime.health()["started"])
        self.runtime.stop()
        self.assertEqual(self.runtime.get_state().perception_health, "stopped")

    def test_latest_frame_updates(self):
        self.runtime.start()
        frame = self.provider.push(b"a")
        self.runtime.ingest_frame(frame, hwnd=1, title="A")
        self.assertEqual(self.runtime.get_latest_frame().frame_id, frame.frame_id)

    def test_no_disk_frame_storage_default(self):
        self.assertFalse(self.runtime.store_frames)
        self.assertEqual(self.runtime.disk_writes, 0)

    def test_unchanged_screen_does_not_call_vision(self):
        self.runtime.start()
        self.runtime.ingest_frame(self.provider.push(b"same"), hwnd=1)
        before = self.vision.provider.calls
        self.runtime.ingest_frame(self.provider.push(b"same"), hwnd=1)
        self.assertEqual(self.vision.provider.calls, before)

    def test_major_change_updates_state(self):
        self.runtime.start()
        self.runtime.ingest_frame(self.provider.push(b"one"), hwnd=1, title="A")
        change = self.runtime.ingest_frame(self.provider.push(b"two"), hwnd=1, title="A")
        self.assertTrue(change.major)

    def test_active_window_change_updates_state(self):
        self.runtime.start()
        self.runtime.ingest_frame(self.provider.push(b"a"), hwnd=1, title="Notatnik")
        self.runtime.ingest_frame(self.provider.push(b"b"), hwnd=2, title="Kalkulator")
        self.assertEqual(self.runtime.get_state().active_window.get("hwnd"), 2)

    def test_sensitive_field_not_persisted(self):
        self.runtime.fuse_ui([{"role": "password", "is_password": True, "value": "hunter2", "name": "Hasło"}])
        public = self.runtime.get_state().to_public_dict()
        note_screen(public)
        blob = str(get_world_state())
        self.assertNotIn("hunter2", blob)
        self.assertNotIn("pixels", get_world_state().get("screen") or {})

    def test_no_raw_frames_in_worldstate(self):
        self.runtime.start()
        self.runtime.ingest_frame(self.provider.push(b"secret-pixels"), hwnd=1, title="A")
        screen = get_world_state().get("screen") or {}
        self.assertNotIn("pixels", screen)
        self.assertNotIn("secret-pixels", str(screen))

    def test_store_frames_false_enforced(self):
        self.runtime.store_frames = True
        self.runtime.ingest_frame(self.provider.push(b"x"), hwnd=1)
        self.assertFalse(self.runtime.store_frames)

    def test_ollama_unavailable_does_not_break_computer(self):
        store = notepad_fixture_store()
        agent = ComputerAgent(store)
        agent._fake_ui = FakeUIBackend()
        reset_computer_agent_for_tests(agent)
        result = agent.act("select_all", {"semantic_action": "select_all", "app_name": "Notatnik"})
        self.assertTrue(result.success)

    def test_local_vision_parses_structured(self):
        payload = self.vision.provider.localize("where")
        self.assertEqual(payload["candidates"][0]["bbox"], [1, 1, 2, 2])

    def test_stale_visual_frame_blocks_click(self):
        store = notepad_fixture_store()
        agent = ComputerAgent(store)
        agent._fake_ui = FakeUIBackend()
        reset_computer_agent_for_tests(agent)
        agent.inspect()
        rec = store.record("win_notepad")
        rec.elements = [
            {
                "element_id": "ghost",
                "role": "button",
                "name": "Ghost",
                "enabled": True,
                "visible": True,
                "confidence": 0.95,
                "source": ["VISION_LOCAL"],
                "frame_id": 999,
                "hwnd": 1,
            }
        ]
        agent.inspect()
        clicks = list(agent._fake_ui.clicks)
        result = agent.act("click_ui_element", {"element_id": "ghost"})
        self.assertFalse(result.success)
        self.assertEqual(agent._fake_ui.clicks, clicks)

    def test_low_confidence_blocks_click(self):
        store = notepad_fixture_store()
        agent = ComputerAgent(store)
        agent._fake_ui = FakeUIBackend()
        reset_computer_agent_for_tests(agent)
        rec = store.record("win_notepad")
        rec.elements = [
            {
                "element_id": "low",
                "role": "button",
                "name": "Low",
                "enabled": True,
                "visible": True,
                "confidence": 0.4,
                "source": ["VISION_LOCAL"],
            }
        ]
        agent.inspect()
        result = agent.act("click_ui_element", {"element_id": "low"})
        self.assertFalse(result.success)

    def test_ambiguous_candidates_clarify(self):
        result = self.runtime.resolve_visual_referent(
            "kliknij to",
            candidates=[
                VisualReferent(label="a", confidence=0.83),
                VisualReferent(label="b", confidence=0.82),
            ],
        )
        self.assertTrue(result.get("clarify"))
        self.assertFalse(result.get("ok"))

    def test_cached_referent_resolves_to(self):
        first = VisualReferent(label="Zapisz", confidence=0.95, bbox=[0, 0, 10, 10])
        self.runtime.referents.remember(first)
        result = self.runtime.resolve_visual_referent("to")
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["referent"].label, "Zapisz")

    def test_cursor_context_resolves_nearby(self):
        near = VisualReferent(label="Tu", confidence=0.92, bbox=[10, 10, 30, 30])
        far = VisualReferent(label="Tam", confidence=0.92, bbox=[200, 200, 220, 220])
        result = self.runtime.resolve_visual_referent("to tutaj", cursor_position=(15, 15), candidates=[far, near])
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["referent"].label, "Tu")

    def test_keyboard_no_vision(self):
        store = notepad_fixture_store()
        agent = ComputerAgent(store)
        agent._fake_ui = FakeUIBackend()
        reset_computer_agent_for_tests(agent)
        agent.act("type_text", {"text": "Elian działa jak agent", "app_name": "Notatnik"})
        agent.act("select_all", {"semantic_action": "select_all", "app_name": "Notatnik"})
        self.assertEqual(agent.vision_requests, 0)

    def test_valid_uia_button_avoids_vision(self):
        store = notepad_fixture_store()
        agent = ComputerAgent(store)
        agent._fake_ui = FakeUIBackend()
        reset_computer_agent_for_tests(agent)
        rec = store.record("win_notepad")
        rec.elements = [
            {
                "element_id": "save",
                "role": "button",
                "name": "Zapisz",
                "enabled": True,
                "visible": True,
                "confidence": 0.97,
                "source": ["LOCAL_COMPUTER_UIA"],
            }
        ]
        agent.inspect()
        before = agent.vision_requests
        agent.act("click_ui_element", {"query": "Zapisz"})
        self.assertEqual(agent.vision_requests, before)

    def test_vision_failure_safe_fallback(self):
        self.vision.provider.available = False
        self.vision.provider.mock = None
        payload = self.vision.maybe_request("test", "x", force=True) or {}
        self.assertEqual(payload.get("candidates"), [])

    def test_no_direct_vision_side_effect(self):
        payload = self.vision.maybe_request("test", "x", force=True)
        self.assertNotIn("click", payload or {})
        self.assertTrue(self.vision.provider.calls >= 1)

    def test_capture_stop_releases(self):
        self.runtime.start()
        self.provider.push(b"keep")
        self.runtime.stop()
        self.assertIsNone(self.provider.latest_frame())


class Phase13JarvisTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        reset_world_state()
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = MemoryManager(Path(self.tmp.name) / "m.json")
        self.registry = build_default_registry(confirm_medium=False)

    def tearDown(self):
        self.tmp.cleanup()
        reset_registry_for_tests(None)

    def test_social_co_tam_no_tools_no_lamp(self):
        planner = Planner(self.registry, model=FakeModelClient([]))
        result = planner.run(
            "co tam?",
            context={"active_device": {"entity_id": "light.vizzini", "name": "lampa"}},
        )
        self.assertEqual(result.source, "social")
        self.assertFalse(result.steps)
        lowered = (result.message or "").lower()
        self.assertNotIn("lampa", lowered)
        self.assertNotIn("spotify", lowered)

    def test_success_concise(self):
        text = execution_reply(
            ResponseContext(
                successful_steps=[{"tool": "open_app", "success": True, "message": "Notatnik jest otwarty."}]
            )
        )
        self.assertTrue(len(text) < 80)
        self.assertNotIn("Czy mogę pomóc", text)

    def test_partial_describes_success_only(self):
        text = execution_reply(
            ResponseContext(
                successful_steps=[{"tool": "open_app", "success": True, "message": "Notatnik otwarty"}],
                failed_steps=[{"tool": "select_all", "success": False, "message": "Nie udało mi się zaznaczyć tekstu."}],
            )
        )
        self.assertIn("Notatnik", text)
        self.assertIn("zaznaczyć", text.lower())

    def test_failed_no_fake_success(self):
        text = execution_reply(
            ResponseContext(
                failed_steps=[{"success": False, "message": "Nie mogę teraz przejąć tego okna. Cursor nadal ma fokus."}]
            )
        )
        lowered = text.lower()
        self.assertNotIn("gotowe", lowered)
        self.assertIn("cursor", lowered)

    def test_ambiguous_visual_clarify(self):
        from core.jarvis_response import clarify_reply

        text = clarify_reply("", candidates=[{}, {}])
        self.assertIn("dwa", text.lower())

    def test_screen_question_uses_state(self):
        runtime = ScreenPerceptionRuntime(provider=MemoryFrameProvider())
        from core.perception.foreground import FakeForegroundProvider

        runtime.foreground = FakeForegroundProvider(
            hwnd=1, title="Notatnik", process="notepad.exe"
        )
        reset_screen_runtime_for_tests(runtime)
        runtime.observe_window_meta(hwnd=1, title="Notatnik", process="notepad")
        planner = Planner(self.registry, model=FakeModelClient([]))
        result = planner.run("co mam na ekranie?")
        self.assertEqual(result.source, "perception")
        self.assertIn("Notatnik", result.message)

    def test_no_repeated_help_closer(self):
        text = strip_service_closers("Gotowe. Czy mogę pomóc w czymś jeszcze?")
        self.assertNotIn("pomóc", text.lower())

    def test_no_fake_feelings(self):
        self.assertNotIn("czuję", strip_fake_feelings("Czuję, że coś jest nie tak.").lower())

    def test_humor_disabled_for_safety(self):
        self.assertFalse(dry_humor_allowed(safety=True))

    def test_responses_remain_polish(self):
        self.assertTrue(any(ch in social_reply("co tam?") for ch in "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ") or "słucham" in social_reply("co tam?").lower() or "spokojnie" in social_reply("co tam?").lower())
        spoken = polish_spoken("Gotowe. Czy mogę pomóc w czymś jeszcze?")
        self.assertNotIn("help", spoken.lower())


if __name__ == "__main__":
    unittest.main()
