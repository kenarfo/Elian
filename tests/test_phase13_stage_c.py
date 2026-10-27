"""Phase 13 Stage C — ComputerAgent Windows perception + action loop."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import HaActions
import HomeAssistant
from core.builtin_tools import build_default_registry
from core.computer_agent import (
    ComputerAgent,
    computer_quality_metrics,
    notepad_fixture_store,
    reset_computer_agent_for_tests,
    reset_computer_metrics,
)
from core.compound_plan import parse_compound_goal
from core.conversation_manager import ConversationManager
from core.intent_family import IntentFamily, classify_intent_family
from core.model_client import FakeModelClient, reset_model_client
from core.planner import Planner
from core.tool_result import ToolResult, ToolStatus
from core.tools import reset_registry_for_tests
from core.utterance import apply_mid_utterance_correction
from memory.manager import MemoryManager
from vision.uia import FakeUIBackend


SAMPLE_LIGHT = {
    "entity_id": "light.vizzini",
    "state": "on",
    "attributes": {"friendly_name": "Lampa", "supported_color_modes": ["brightness"]},
}


class StageCComputerTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        reset_computer_metrics()
        HomeAssistant.get_registry().load_states([SAMPLE_LIGHT])
        self.store = notepad_fixture_store()
        self.agent = ComputerAgent(self.store)
        self.agent._fake_ui = FakeUIBackend()
        reset_computer_agent_for_tests(self.agent)
        self.registry = build_default_registry(confirm_medium=False)

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()
        reset_computer_agent_for_tests(ComputerAgent(notepad_fixture_store()))

    def test_notepad_semantic_plan(self):
        plan = parse_compound_goal(
            "Otwórz Notatnik, wpisz Elian działa jak agent i zaznacz cały tekst."
        )
        self.assertTrue(plan.detected)
        self.assertEqual(
            [s.family for s in plan.steps],
            ["APP", "COMPUTER", "COMPUTER"],
        )
        self.assertEqual(plan.steps[1].semantic_action, "type_text")
        self.assertEqual(plan.steps[1].slots.get("text_payload"), "Elian działa jak agent")
        self.assertEqual(plan.steps[2].semantic_action, "select_all")

    def test_referent_continuation(self):
        self.agent.act("focus_window", {"name": "Notatnik"})
        typed = self.agent.act("type_text", {"text": "test", "app_name": "Notatnik"})
        self.assertTrue(typed.success)
        selected = self.agent.act("select_all", {"semantic_action": "select_all"})
        self.assertTrue(selected.success)
        undo = self.agent.act("undo", {"semantic_action": "undo"})
        self.assertTrue(undo.success)
        rec = self.store.record("win_notepad")
        self.assertEqual(self.store.get_active().app_name, "Notatnik")
        self.assertTrue(rec.selected or self.store.last_hotkey == "ctrl+z")

    def test_ambiguous_button_zero_click(self):
        rec = self.store.record("win_notepad")
        rec.elements = [
            {"element_id": "b1", "role": "button", "name": "Pobierz", "confidence": 0.95, "visible": True, "enabled": True},
            {"element_id": "b2", "role": "button", "name": "Pobierz", "confidence": 0.95, "visible": True, "enabled": True},
        ]
        self.agent.inspect()
        before = list(self.agent._fake_ui.clicks)
        result = self.agent.act("click_ui_element", {"query": "Pobierz"})
        self.assertFalse(result.success)
        self.assertTrue(result.data.get("needs_clarification"))
        self.assertEqual(self.agent._fake_ui.clicks, before)
        self.assertEqual(computer_quality_metrics()["ambiguous_click_execution"], 0)

    def test_low_confidence_lock(self):
        rec = self.store.record("win_notepad")
        rec.elements = [
            {"element_id": "b1", "role": "button", "name": "Dalej", "confidence": 0.72, "visible": True, "enabled": True},
        ]
        self.agent.inspect()
        result = self.agent.act("click_ui_element", {"query": "Dalej"})
        self.assertFalse(result.success)
        self.assertTrue(result.data.get("needs_clarification"))
        self.assertNotIn("b1", self.agent._fake_ui.clicks)

    def test_stale_snapshot_reobserve(self):
        self.agent.inspect()
        self.store.bump_window("win_notepad")
        before = list(self.agent._fake_ui.clicks)
        result = self.agent.act("click_ui_element", {"element_id": "gone:input_1"})
        self.assertFalse(result.success)
        self.assertFalse(result.data.get("clicked"))
        self.assertEqual(self.agent._fake_ui.clicks, before)
        self.assertEqual(computer_quality_metrics()["blind_coordinate_click"], 0)

    def test_unsaved_close_waiting_confirmation(self):
        rec = self.store.record("win_notepad")
        rec.unsaved = True
        result = self.agent.act("close_window", {"name": "Notatnik"})
        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolStatus.NEEDS_CONFIRMATION)
        self.assertTrue(rec.info.exists)
        self.assertEqual(self.store.dont_save_clicks, 0)

    def test_wrong_window_types_notepad_not_calculator(self):
        self.store.focus("win_calc")
        result = self.agent.act("type_text", {"text": "test", "app_name": "Notatnik"})
        self.assertTrue(result.success)
        np = self.store.record("win_notepad")
        calc = self.store.record("win_calc")
        self.assertIn("test", np.editor_text)
        self.assertEqual(calc.editor_text, "")
        self.assertEqual(computer_quality_metrics()["wrong_target"], 0)

    def test_computer_plus_ha_plan(self):
        plan = parse_compound_goal("Otwórz Notatnik, wpisz test i wyłącz lampę.")
        self.assertEqual([s.family for s in plan.steps], ["APP", "COMPUTER", "SMART_HOME"])
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            with patch.object(HaActions, "_call_service", return_value=True):
                result = Planner(self.registry, model=FakeModelClient([])).run(
                    "Otwórz Notatnik, wpisz test i wyłącz lampę."
                )
        self.assertEqual(result.source, "compound")
        families = [str(step.get("family") or "") for step in result.steps]
        self.assertIn("APP", families)
        self.assertIn("COMPUTER", families)
        self.assertIn("SMART_HOME", families)

    def test_computer_status_local_not_web(self):
        info = classify_intent_family("Jaka aplikacja jest teraz aktywna?")
        self.assertEqual(info.family, IntentFamily.COMPUTER)
        self.assertEqual((info.slots or {}).get("source_kind"), "LOCAL_COMPUTER")
        result = self.agent.act("get_active_window", {})
        self.assertTrue(result.success)
        self.assertIn("Notatnik", result.message)
        self.assertEqual(result.data.get("source_kind"), "LOCAL_COMPUTER")

    def test_no_fake_click_claim(self):
        rec = self.store.record("win_notepad")
        rec.elements = []
        self.agent.inspect()
        result = self.agent.act("click_ui_element", {"query": "BrakPrzycisku"})
        self.assertFalse(result.success)
        self.assertNotIn("Kliknąłem", result.message)
        self.assertNotIn("kliknąłem", result.message.lower())

    def test_chrome_running_focus_not_reopen(self):
        self.store.open_app("Chrome")
        opened = list(self.store.opened)
        result = self.agent.act("focus_or_open", {"name": "Chrome"})
        self.assertTrue(result.success)
        self.assertTrue(result.data.get("focused_existing"))
        self.assertEqual(self.store.opened, opened)

    def test_mid_utterance_click_correction(self):
        corrected, ok = apply_mid_utterance_correction("Kliknij Ustawienia nie Dźwięk")
        self.assertTrue(ok)
        self.assertIn("Dźwięk", corrected)
        self.assertNotIn("Ustawienia", corrected)

    def test_quality_metrics_zero(self):
        metrics = computer_quality_metrics()
        for key, value in metrics.items():
            self.assertEqual(int(value), 0, key)

    def test_conversation_type_after_open_uses_context(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mem = MemoryManager(Path(tmp.name) / "m.json")
        mgr = ConversationManager(
            registry=self.registry,
            memory_manager=mem,
            memory=[],
            model=FakeModelClient([]),
        )
        mgr.activate()
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            first = mgr.handle("Otwórz Notatnik.")
        self.assertTrue(first.text)
        second = mgr.handle("Wpisz test.")
        rec = self.store.record("win_notepad")
        self.assertIn("test", rec.editor_text)
        self.assertNotIn("Kliknąłem", second.text)


class StageCHardwareFix1Tests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        reset_computer_metrics()
        HomeAssistant.get_registry().load_states([SAMPLE_LIGHT])
        self.store = notepad_fixture_store()
        self.agent = ComputerAgent(self.store)
        self.agent._fake_ui = FakeUIBackend()
        reset_computer_agent_for_tests(self.agent)
        self.registry = build_default_registry(confirm_medium=False)

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()
        reset_computer_agent_for_tests(ComputerAgent(notepad_fixture_store()))

    def test_wake_extraction_preserves_second_elian(self):
        from WakeWord import extract_command_from_transcript, remove_wake_word
        from core.text_pipeline import prepare_user_text

        raw = "Hej Elian, otwórz Notatnik i wpisz Elian działa jak agent i zaznacz cały tekst."
        command = extract_command_from_transcript(raw)
        self.assertEqual(
            command,
            "otwórz Notatnik i wpisz Elian działa jak agent i zaznacz cały tekst",
        )
        self.assertEqual(remove_wake_word(raw), command)
        prepared = prepare_user_text(raw)
        self.assertIn("elian działa jak agent", prepared.semantic_text.lower())
        self.assertTrue(
            prepared.semantic_text.lower().startswith("otwórz")
            or prepared.semantic_text.lower().startswith("otworz")
        )
        self.assertNotEqual(
            prepared.semantic_text.lower(),
            "działa jak agent i zaznacz cały tekst",
        )

    def test_second_elian_in_notepad_type_payload_stays(self):
        from WakeWord import extract_command_from_transcript

        command = extract_command_from_transcript("Elian, wpisz w Notatniku tekst Elian działa.")
        self.assertEqual(command, "wpisz w Notatniku tekst Elian działa")
        self.assertIn("Elian działa", command)

    def test_select_all_focuses_notepad_not_cursor(self):
        from core.computer_agent import add_cursor_window

        add_cursor_window(self.store)
        self.store.focus("win_cursor")
        self.store.last_hotkey = ""
        keys_before = list(self.agent._fake_ui.keys)
        result = self.agent.act(
            "select_all",
            {"semantic_action": "select_all", "app_name": "Notatnik"},
        )
        self.assertTrue(result.success)
        self.assertEqual(self.store.last_hotkey_window, "win_notepad")
        self.assertNotIn("cursor", (self.store.last_hotkey_process or "").lower())
        self.assertEqual(self.store.get_active().window_id, "win_notepad")
        self.assertTrue(self.agent._fake_ui.keys[len(keys_before) :] or self.store.last_hotkey == "ctrl+a")

    def test_notepad_focus_fail_sends_zero_hotkey(self):
        from core.computer_agent import add_cursor_window

        add_cursor_window(self.store)
        self.store._windows["win_notepad"].info.exists = False
        self.store.focus("win_cursor")
        self.store.last_hotkey = ""
        keys_before = list(self.agent._fake_ui.keys)
        result = self.agent.act(
            "select_all",
            {"semantic_action": "select_all", "app_name": "Notatnik"},
        )
        self.assertFalse(result.success)
        self.assertEqual(self.store.last_hotkey, "")
        self.assertEqual(self.agent._fake_ui.keys, keys_before)
        self.assertNotIn("gotowe", result.message.lower())

    def test_same_plan_binds_canonical_window(self):
        self.agent.begin_plan("p1", goal="Otwórz Notatnik, wpisz Elian działa jak agent i zaznacz cały tekst.")
        focused = self.agent.act("focus_window", {"name": "Notatnik"})
        self.assertTrue(focused.success)
        typed = self.agent.act("type_text", {"text": "Elian działa jak agent"})
        self.assertTrue(typed.success)
        selected = self.agent.act("select_all", {"semantic_action": "select_all"})
        self.assertTrue(selected.success)
        self.assertEqual(self.agent.plan_working_window, "win_notepad")
        self.assertEqual(self.store.typed_into[-1], "win_notepad")
        self.assertEqual(self.store.last_hotkey_window, "win_notepad")

    def test_select_all_without_target_clarifies(self):
        self.agent.begin_plan("p2")
        self.agent.last_working_app = ""
        self.agent.last_working_window = ""
        self.store.last_hotkey = ""
        keys_before = list(self.agent._fake_ui.keys)
        result = self.agent.act("select_all", {"semantic_action": "select_all"})
        self.assertFalse(result.success)
        self.assertTrue(result.data.get("needs_clarification"))
        self.assertEqual(self.store.last_hotkey, "")
        self.assertEqual(self.agent._fake_ui.keys, keys_before)

    def test_wrong_window_cannot_claim_success(self):
        from core.action_claim import guard_action_claim
        from core.computer_agent import add_cursor_window

        add_cursor_window(self.store)
        self.store._windows["win_notepad"].info.exists = False
        self.store.focus("win_cursor")
        result = self.agent.act(
            "select_all",
            {"semantic_action": "select_all", "app_name": "Notatnik"},
        )
        self.assertFalse(result.success)
        guarded = guard_action_claim("Gotowe.", [result.to_observation()])
        self.assertTrue(guarded.blocked)
        self.assertNotIn("gotowe", result.message.lower())

    def test_injection_only_cannot_be_strong(self):
        rec = self.store.record("win_notepad")
        rec.supports_selection = False
        rec.selected = False
        result = self.agent.act(
            "select_all",
            {"semantic_action": "select_all", "app_name": "Notatnik"},
        )
        self.assertTrue(result.success)
        self.assertNotEqual(result.data.get("verification_level"), "STRONG")
        self.assertEqual(result.data.get("verification_level"), "BASIC")
        self.assertTrue(result.data.get("target_verified"))

    def test_explicit_cursor_select_all_allowed(self):
        from core.computer_agent import add_cursor_window
        from core.computer_semantics import parse_computer_goal

        add_cursor_window(self.store)
        goal = parse_computer_goal("W Cursorze zaznacz wszystko.")
        self.assertEqual(goal.app_name, "Cursor")
        self.store.focus("win_notepad")
        result = self.agent.act(
            "select_all",
            {
                "semantic_action": "select_all",
                "app_name": goal.app_name,
                "user_goal": "W Cursorze zaznacz wszystko.",
            },
        )
        self.assertTrue(result.success)
        self.assertEqual(self.store.last_hotkey_window, "win_cursor")
        self.assertEqual(self.store.get_active().window_id, "win_cursor")

    def test_truncated_fragment_does_not_select_active(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mem = MemoryManager(Path(tmp.name) / "m.json")
        mgr = ConversationManager(
            registry=self.registry,
            memory_manager=mem,
            memory=[],
            model=FakeModelClient([]),
        )
        mgr.activate()
        from core.computer_agent import add_cursor_window

        add_cursor_window(self.store)
        self.store.focus("win_cursor")
        self.store.last_hotkey = ""
        keys_before = list(self.agent._fake_ui.keys)
        result = mgr.handle("działa jak agent i zaznacz cały tekst")
        self.assertIn("początku", (result.text or "").lower())
        self.assertEqual(self.store.last_hotkey, "")
        self.assertEqual(self.agent._fake_ui.keys, keys_before)
        rec = self.store.record("win_cursor")
        self.assertFalse(rec.selected)

    def test_rc_inject_notepad_same_plan_as_stt_handle(self):
        from core.compound_plan import parse_compound_goal
        from core.rc_validation.inject import NOTEPAD_STAGE_C_PHRASE, dispatch_rc_inject

        phrase = NOTEPAD_STAGE_C_PHRASE
        expected = parse_compound_goal(phrase)
        self.assertEqual(
            [s.family for s in expected.steps],
            ["APP", "COMPUTER", "COMPUTER"],
        )
        self.assertEqual(
            [s.semantic_action for s in expected.steps],
            ["open_service", "type_text", "select_all"],
        )

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        def make_mgr(name: str):
            return ConversationManager(
                registry=self.registry,
                memory_manager=MemoryManager(Path(tmp.name) / f"{name}.json"),
                memory=[],
                model=FakeModelClient([]),
            )

        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            stt_mgr = make_mgr("stt")
            stt_mgr.activate()
            stt_result = stt_mgr.handle(phrase)

            inj_mgr = make_mgr("inj")
            inj_result = dispatch_rc_inject(inj_mgr, phrase)

        stt_tools = [str(item.get("tool") or "") for item in (stt_mgr.last_observations or [])]
        inj_tools = [str(item.get("tool") or "") for item in (inj_mgr.last_observations or [])]
        self.assertEqual(stt_tools, inj_tools)
        self.assertIn("open_app", stt_tools)
        self.assertTrue(
            any(name in ("type_text", "hotkey", "press_key") for name in stt_tools)
        )
        self.assertEqual(stt_result.source, inj_result.source)


class StageCHardwareFix2Tests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        reset_computer_metrics()
        HomeAssistant.get_registry().load_states([SAMPLE_LIGHT])
        self.store = notepad_fixture_store()
        self.agent = ComputerAgent(self.store)
        self.agent._fake_ui = FakeUIBackend()
        reset_computer_agent_for_tests(self.agent)
        self.registry = build_default_registry(confirm_medium=False)

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()
        reset_computer_agent_for_tests(ComputerAgent(notepad_fixture_store()))

    def test_type_text_no_redundant_full_scans(self):
        self.agent.inspect()
        self.agent.reset_scan_counts()
        result = self.agent.act("type_text", {"text": "Elian", "app_name": "Notatnik"})
        self.assertTrue(result.success)
        self.assertEqual(self.agent.full_scan_count, 0)

    def test_valid_snapshot_is_reused(self):
        self.agent.inspect()
        self.assertTrue(self.agent._snapshot_reusable("input_1"))
        self.agent.reset_scan_counts()
        result = self.agent.act("type_text", {"text": "reuse", "app_name": "Notatnik"})
        self.assertTrue(result.success)
        self.assertGreaterEqual(self.agent.snapshot_reuse_count, 1)
        self.assertEqual(self.agent.full_scan_count, 0)
        self.assertTrue(result.data.get("target_verified"))

    def test_hwnd_change_invalidates_snapshot(self):
        self.agent.inspect()
        self.assertTrue(self.agent._snapshot_reusable("input_1"))
        rec = self.store.record("win_notepad")
        rec.info.hwnd = 4242
        self.assertFalse(self.agent._snapshot_reusable("input_1"))

    def test_targeted_text_verify_skips_global_rescan(self):
        self.agent.inspect()
        self.agent.reset_scan_counts()
        result = self.agent.act(
            "type_text",
            {"text": "Elian działa", "app_name": "Notatnik", "element_id": "input_1"},
        )
        self.assertTrue(result.success)
        self.assertEqual(result.data.get("verification_level"), "STRONG")
        self.assertEqual(self.agent.full_scan_count, 0)

    def test_select_all_basic_without_selection_pattern(self):
        rec = self.store.record("win_notepad")
        rec.supports_selection = False
        rec.selected = False
        self.agent.inspect()
        self.agent.reset_scan_counts()
        result = self.agent.act(
            "select_all",
            {"semantic_action": "select_all", "app_name": "Notatnik"},
        )
        self.assertTrue(result.success)
        self.assertEqual(result.data.get("verification_level"), "BASIC")
        self.assertTrue(result.data.get("target_verified"))
        self.assertEqual(self.agent.full_scan_count, 0)

    def test_verified_success_not_overwritten_by_slow_diagnostics(self):
        import time

        from core.tool_result import ToolStatus

        self.agent.inspect()
        self.agent._after_verified_hook = lambda: time.sleep(0.25)
        tool = self.registry.get("type_text")
        self.assertIsNotNone(tool)
        tool.timeout = 0.05
        result = self.registry.invoke("type_text", {"text": "ok", "app_name": "Notatnik"})
        self.assertTrue(result.success)
        self.assertNotEqual(result.status, ToolStatus.TIMEOUT)
        self.assertEqual(computer_quality_metrics()["verified_action_timeout"], 0)

    def test_timeout_before_required_verification_is_genuine(self):
        import time

        from core.risk import RiskLevel
        from core.tool_result import ToolStatus
        from core.tools import Tool

        def slow(_arguments):
            time.sleep(0.2)
            return ToolResult.ok("late", tool_name="slow_unverified")

        self.registry.register(
            Tool(
                name="slow_unverified",
                description="slow",
                handler=slow,
                timeout=0.05,
                risk_level=RiskLevel.SAFE,
            )
        )
        result = self.registry.invoke("slow_unverified", {})
        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolStatus.TIMEOUT)

    def test_raw_type_payload_preserves_casing(self):
        from core.computer_semantics import overlay_raw_type_payload, parse_computer_goal

        raw = "Wpisz Elian działa jak agent."
        goal = parse_computer_goal(raw)
        self.assertIsNotNone(goal)
        self.assertEqual(goal.text, "Elian działa jak agent")
        lowered = classify_intent_family("wpisz elian działa jak agent.")
        over = overlay_raw_type_payload(lowered, raw)
        self.assertEqual(over.slots.get("text_payload"), "Elian działa jak agent")

    def test_intent_classification_uses_normalized_lowercase(self):
        info = classify_intent_family("wpisz elian działa jak agent.")
        self.assertEqual(info.family, IntentFamily.COMPUTER)
        self.assertEqual((info.slots or {}).get("semantic_action"), "type_text")
        self.assertEqual((info.slots or {}).get("text_payload"), "elian działa jak agent")

    def test_wrong_window_guards_remain(self):
        from core.computer_agent import add_cursor_window

        add_cursor_window(self.store)
        self.store.focus("win_cursor")
        result = self.agent.act(
            "select_all",
            {"semantic_action": "select_all", "app_name": "Notatnik"},
        )
        self.assertTrue(result.success)
        self.assertEqual(self.store.last_hotkey_window, "win_notepad")
        self.assertNotIn("cursor", (self.store.last_hotkey_process or "").lower())
        self.assertEqual(computer_quality_metrics()["wrong_window_keyboard_action"], 0)


class StageCHardwareFix3Tests(unittest.TestCase):
    def setUp(self):
        from core.computer_ui_worker import reset_computer_ui_worker_for_tests

        reset_registry_for_tests()
        reset_model_client()
        reset_computer_metrics()
        reset_computer_ui_worker_for_tests()
        HomeAssistant.get_registry().load_states([SAMPLE_LIGHT])
        self.store = notepad_fixture_store()
        self.agent = ComputerAgent(self.store)
        self.agent._fake_ui = FakeUIBackend()
        reset_computer_agent_for_tests(self.agent)
        self.registry = build_default_registry(confirm_medium=False)

    def tearDown(self):
        from core.computer_ui_worker import wait_computer_idle

        wait_computer_idle(timeout=2.0)
        reset_registry_for_tests(None)
        reset_model_client()
        reset_computer_agent_for_tests(ComputerAgent(notepad_fixture_store()))

    def _focus_notepad(self):
        self.store.focus("win_notepad")
        self.agent.inspect()
        rec = self.store.record("win_notepad")
        rec.editor_text = ""
        return rec

    def test_timeout_before_action_has_no_side_effect(self):
        import time

        rec = self._focus_notepad()
        self.agent._block_before_action = lambda: time.sleep(0.2)
        tool = self.registry.get("type_text")
        tool.timeout = 0.05
        result = self.registry.invoke("type_text", {"text": "ABC", "app_name": "Notatnik"})
        self.assertFalse(result.success)
        self.assertNotIn("ABC", rec.editor_text)
        self.assertIn(result.status, (ToolStatus.TIMEOUT, ToolStatus.FAILURE))
        self.assertFalse(self.agent.is_busy())
        self.assertEqual(computer_quality_metrics()["post_timeout_side_effect_race"], 0)

    def test_deadline_after_action_observes_without_retry(self):
        import time

        rec = self._focus_notepad()
        self.agent._block_after_action = lambda: time.sleep(0.2)
        tool = self.registry.get("type_text")
        tool.timeout = 0.05
        typed_before = list(self.store.typed_into)
        result = self.registry.invoke("type_text", {"text": "ABC", "app_name": "Notatnik"})
        self.assertIn("ABC", rec.editor_text)
        self.assertTrue(result.success or (result.data or {}).get("side_effect_may_have_occurred"))
        self.assertNotEqual(result.status, ToolStatus.TIMEOUT)
        self.assertEqual(self.store.typed_into.count("win_notepad"), typed_before.count("win_notepad") + 1)

    def test_action_plus_observation_is_success(self):
        rec = self._focus_notepad()
        result = self.registry.invoke("type_text", {"text": "ABC", "app_name": "Notatnik"})
        self.assertTrue(result.success)
        self.assertEqual(result.status, ToolStatus.SUCCESS)
        self.assertIn("ABC", rec.editor_text)
        self.assertIn("Gotowe", result.message)

    def test_unknown_side_effect_is_partial_and_idle(self):
        rec = self._focus_notepad()

        def after():
            rec.editor_text = ""
            self.agent.signal_deadline()

        self.agent._block_after_action = after
        result = self.agent.act("type_text", {"text": "XYZ", "app_name": "Notatnik"})
        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolStatus.PARTIAL)
        self.assertTrue((result.data or {}).get("side_effect_may_have_occurred"))
        self.assertEqual(result.error, "unknown_side_effect")
        self.assertNotIn("Gotowe", result.message)
        self.assertTrue(self.agent.wait_until_idle(timeout=1.0))
        self.assertFalse(self.agent.is_busy())

    def test_dependent_step_waits_while_worker_busy(self):
        import threading
        import time

        from core.computer_ui_worker import submit_computer_job, wait_computer_idle

        hold = threading.Event()

        def hang():
            hold.wait(2.0)
            return True

        future = submit_computer_job(hang)
        busy = False
        for _ in range(80):
            if self.agent.is_busy():
                busy = True
                break
            time.sleep(0.01)
        self.assertTrue(busy)
        self.assertFalse(self.agent.wait_until_idle(timeout=0.05))
        hold.set()
        self.assertTrue(wait_computer_idle(timeout=2.0))
        future.result(timeout=1.0)
        self.assertFalse(self.agent.is_busy())

    def test_retry_after_uncertain_does_not_duplicate(self):
        rec = self._focus_notepad()
        rec.editor_text = "ABC"
        self.agent.inspect()
        resolved = self.agent.resolve_uncertain_type({"text": "ABC", "app_name": "Notatnik"})
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.success)
        self.agent.lifecycle = "UNKNOWN_SIDE_EFFECT"
        typed_before = list(self.store.typed_into)
        again = self.registry.invoke("type_text", {"text": "ABC", "app_name": "Notatnik"})
        self.assertTrue(again.success)
        self.assertEqual(rec.editor_text, "ABC")
        self.assertEqual(self.store.typed_into, typed_before)
        self.assertEqual(computer_quality_metrics()["duplicate_retry_after_uncertain_side_effect"], 0)

    def test_select_all_reuses_resolved_editor(self):
        self._focus_notepad()
        typed = self.agent.act("type_text", {"text": "cache", "app_name": "Notatnik"})
        self.assertTrue(typed.success)
        handle = self.agent.editor_handle
        self.assertIsNotNone(handle)
        self.agent.reset_scan_counts()
        selected = self.agent.act("select_all", {"semantic_action": "select_all", "app_name": "Notatnik"})
        self.assertTrue(selected.success)
        self.assertEqual(self.agent.editor_handle.element_id, handle.element_id)
        self.assertEqual(self.agent.editor_handle.hwnd, handle.hwnd)

    def test_valid_cached_element_zero_descendants_search(self):
        self._focus_notepad()
        self.agent.act("type_text", {"text": "cache", "app_name": "Notatnik"})
        self.agent.reset_scan_counts()
        selected = self.agent.act("select_all", {"semantic_action": "select_all", "app_name": "Notatnik"})
        self.assertTrue(selected.success)
        self.assertEqual(self.agent.descendants_search_count, 0)

    def test_stale_handle_invalidates_and_reresolves(self):
        rec = self._focus_notepad()
        self.agent.act("type_text", {"text": "stale", "app_name": "Notatnik"})
        self.assertIsNotNone(self.agent.editor_handle)
        rec.elements = []
        self.agent.reset_scan_counts()
        self.assertFalse(self.agent.editor_handle_valid())
        rec.elements = [
            {
                "element_id": "input_1",
                "role": "document",
                "name": "Text Editor",
                "enabled": True,
                "visible": True,
                "focused": True,
                "confidence": 0.97,
            }
        ]
        self.agent.invalidate_editor_handle()
        self.agent.last_observation = None
        self.agent.ui_context.snapshot_id = ""
        selected = self.agent.act("select_all", {"semantic_action": "select_all", "app_name": "Notatnik"})
        self.assertTrue(selected.success)
        self.assertGreaterEqual(self.agent.descendants_search_count, 1)

    def test_hwnd_change_invalidates_editor_cache(self):
        rec = self._focus_notepad()
        self.agent.act("type_text", {"text": "hwnd", "app_name": "Notatnik"})
        self.assertTrue(self.agent.editor_handle_valid())
        rec.info.hwnd = 4242
        self.assertFalse(self.agent.editor_handle_valid())

    def test_live_equivalent_raw_type_payload(self):
        from core.text_pipeline import prepare_user_text

        raw = "Hej Elian, otwórz notatnik i wpisz Elian działa jak agent i zaznacz cały tekst."
        prepared = prepare_user_text(raw)
        plan = parse_compound_goal(prepared.raw_text or raw)
        self.assertTrue(plan.detected)
        typed = next(s for s in plan.steps if s.semantic_action == "type_text")
        self.assertEqual(typed.slots.get("text_payload"), "Elian działa jak agent")
        self.assertEqual(typed.normalized_clause.lower(), typed.normalized_clause)
        self.assertIn("elian", typed.normalized_clause)
        self.assertIn("Elian", typed.raw_clause)
        from core.compound_execute import resolve_step_tool

        _tool, args, _reason = resolve_step_tool(typed)
        self.assertEqual(args.get("text"), "Elian działa jak agent")

    def test_mixed_case_payload_preserved(self):
        from core.computer_semantics import extract_type_payload, parse_computer_goal

        raw = "Wpisz Ala Ma Kota 123."
        goal = parse_computer_goal(raw)
        self.assertEqual(goal.text, "Ala Ma Kota 123")
        self.assertEqual(extract_type_payload(raw), "Ala Ma Kota 123")

    def test_code_payload_not_lowercased(self):
        from core.computer_semantics import parse_computer_goal

        raw = "Wpisz print('HelloWorld')"
        goal = parse_computer_goal(raw)
        self.assertEqual(goal.text, "print('HelloWorld')")

    def test_hardware_like_cached_perf_fixture(self):
        self._focus_notepad()
        first = self.agent.act("type_text", {"text": "perf", "app_name": "Notatnik"})
        self.assertTrue(first.success)
        first_query = int(self.agent.descendants_search_count)
        self.agent.reset_scan_counts()
        second = self.agent.act("select_all", {"semantic_action": "select_all", "app_name": "Notatnik"})
        self.assertTrue(second.success)
        self.assertEqual(self.agent.descendants_search_count, 0)
        self.assertGreaterEqual(first_query, 0)
        metrics = computer_quality_metrics()
        for key in (
            "fake_success",
            "wrong_target",
            "wrong_window_keyboard_action",
            "duplicate_side_effect",
            "verified_action_timeout",
            "post_timeout_side_effect_race",
            "duplicate_retry_after_uncertain_side_effect",
        ):
            self.assertEqual(metrics.get(key, 0), 0)


if __name__ == "__main__":
    unittest.main()
