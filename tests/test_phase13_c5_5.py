# ==========================
# ELIAN — Phase 13 C.5.5 AI-first FastPath + verified SMART_HOME
# ==========================
from __future__ import annotations

import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import HaActions
import HomeAssistant
from core.action_firewall import allow_tool
from core.builtin_tools import build_default_registry
from core.conversation_manager import ConversationManager
from core.device_identity import (
    dedup_device_name,
    has_duplicate_device_token,
    spoken_name_for,
    strip_entity_ids,
)
from core.fast_path import FastPathStatus, evaluate_fast_path, run_fast_path
from core.ha_speech import ha_user_message, speak_ha_result
from core.ha_verify import UNKNOWN, clear_unverified, execute_verified
from core.latency import LatencyTracker, sanity_reason
from core.model_client import FakeModelClient, reset_model_client
from core.semantic_eval import all_eval_cases, run_behavioral_eval
from core.semantic_executive import (
    decide_semantics,
    fast_path_permitted,
    looks_discussion_not_action,
    looks_hypothetical,
    looks_quoted_command,
)
from core.tool_result import ToolResult, ToolStatus
from core.tools import reset_registry_for_tests
from memory.manager import MemoryManager


SAMPLE_OFF = {
    "entity_id": "light.vizzini",
    "state": "off",
    "attributes": {
        "friendly_name": "Inspire Vizzini D56 Vizzini",
        "supported_color_modes": ["rgb", "color_temp", "brightness"],
        "brightness": 0,
    },
}
SAMPLE_ON = {
    "entity_id": "light.vizzini",
    "state": "on",
    "attributes": {
        "friendly_name": "Inspire Vizzini D56 Vizzini",
        "supported_color_modes": ["rgb", "color_temp", "brightness"],
        "brightness": 128,
    },
}


def _load(state):
    HomeAssistant.get_registry().load_states([state])


class Phase13C55FastPathTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        clear_unverified()

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()
        clear_unverified()

    def test_wlacz_swiatlo_act_smart_home_fastpath(self):
        env = decide_semantics("włącz światło", allow_llm=False)
        self.assertEqual(env.mode, "ACT")
        self.assertEqual(env.intent, "smart_home_control")
        self.assertIn("SMART_HOME", env.allowed_domains)
        self.assertTrue(env.needs_tools)
        self.assertTrue(fast_path_permitted("włącz światło", env))
        self.assertEqual(
            evaluate_fast_path("włącz światło", env).status,
            FastPathStatus.HANDLED_COMPLETE,
        )

    def test_fastpath_never_before_semantic(self):
        text = "włącz światło"
        env = decide_semantics(text, allow_llm=False)
        self.assertEqual(env.mode, "ACT")
        self.assertTrue(fast_path_permitted(text, env))

    def test_talk_cannot_reach_ha_fastpath(self):
        env = decide_semantics("opowiedz żart o włączaniu światła", allow_llm=False)
        self.assertEqual(env.mode, "TALK")
        self.assertFalse(fast_path_permitted("opowiedz żart o włączaniu światła", env))
        self.assertEqual(
            evaluate_fast_path("opowiedz żart o włączaniu światła", env).status,
            FastPathStatus.NOT_HANDLED,
        )
        allowed, _ = allow_tool(env, "turn_on_device", "opowiedz żart o włączaniu światła")
        self.assertFalse(allowed)

    def test_negation_blocks_turn_on(self):
        env = decide_semantics("Nie włączaj światła.", allow_llm=False)
        self.assertFalse(env.needs_tools)
        self.assertTrue(looks_discussion_not_action("Nie włączaj światła."))
        allowed, _ = allow_tool(env, "turn_on_device", "Nie włączaj światła.")
        self.assertFalse(allowed)

    def test_hypothetical_blocks_turn_on(self):
        text = "Co się stanie, jeśli włączysz światło?"
        self.assertTrue(looks_hypothetical(text))
        env = decide_semantics(text, allow_llm=False)
        self.assertFalse(env.needs_tools)
        self.assertEqual(evaluate_fast_path(text, env).status, FastPathStatus.NOT_HANDLED)

    def test_quoted_command_blocks_turn_on(self):
        text = "Powiedziałem mu: włącz światło."
        self.assertTrue(looks_quoted_command(text))
        env = decide_semantics(text, allow_llm=False)
        self.assertFalse(env.needs_tools)
        self.assertEqual(evaluate_fast_path(text, env).status, FastPathStatus.NOT_HANDLED)

    def test_how_to_question_no_mutation(self):
        env = decide_semantics("Jak włączyć światło?", allow_llm=False)
        self.assertIn(env.mode, ("ANSWER", "TALK"))
        self.assertFalse(env.needs_tools)
        self.assertEqual(
            evaluate_fast_path("Jak włączyć światło?", env).status,
            FastPathStatus.NOT_HANDLED,
        )

    def test_fastpath_uses_jarvis_not_raw_tool_message(self):
        spoken = speak_ha_result(
            ToolResult.ok(
                "Włączam Inspire Vizzini D56 Vizzini.",
                data={
                    "verification": "SUCCESS",
                    "ha_outcome": "SUCCESS",
                    "ha_action": "device_on",
                    "spoken_name": "światło",
                    "entity_id": "light.vizzini",
                },
                tool_name="turn_on_device",
            ),
            utterance="włącz światło",
        )
        self.assertIn("włączone", spoken.lower())
        self.assertNotIn("włączam", spoken.lower())
        self.assertNotIn("inspire", spoken.lower())


class Phase13C55HaVerifyTests(unittest.TestCase):
    def setUp(self):
        clear_unverified()
        _load(SAMPLE_OFF)

    def tearDown(self):
        clear_unverified()

    def test_success_before_after(self):
        entity = HomeAssistant.get_registry().get("light.vizzini")
        with patch.object(HaActions, "_call_service", return_value=True):
            result = execute_verified("device_on", entity, utterance="włącz światło")
        self.assertEqual(result.outcome, "SUCCESS")
        self.assertEqual(result.before, "off")
        self.assertEqual(result.after, "on")
        self.assertTrue(result.mutated)

    def test_accepted_but_after_off_is_not_success(self):
        entity = HomeAssistant.get_registry().get("light.vizzini")

        with patch.object(HaActions, "_call_service", return_value=True):
            result = execute_verified(
                "device_on",
                entity,
                utterance="włącz światło",
                read_state=lambda eid: entity,
                timeout_s=0.2,
            )
        self.assertEqual(result.outcome, UNKNOWN)
        self.assertNotEqual(result.verification, "SUCCESS")

    def test_unavailable_no_success(self):
        HomeAssistant.get_registry().load_states(
            [
                {
                    "entity_id": "light.vizzini",
                    "state": "unavailable",
                    "attributes": {"friendly_name": "Lampa"},
                }
            ]
        )
        entity = HomeAssistant.get_registry().get("light.vizzini")
        with patch.object(HaActions, "_call_service", return_value=True):
            result = execute_verified("device_on", entity, utterance="włącz światło")
        self.assertEqual(result.outcome, "UNAVAILABLE")

    def test_already_on_zero_service(self):
        _load(SAMPLE_ON)
        entity = HomeAssistant.get_registry().get("light.vizzini")
        calls = []

        def _svc(*args, **kwargs):
            calls.append(args)
            return True

        with patch.object(HaActions, "_call_service", side_effect=_svc):
            result = execute_verified("device_on", entity, utterance="włącz światło")
        self.assertEqual(result.outcome, "ALREADY_SATISFIED")
        self.assertEqual(calls, [])
        self.assertIn("już", result.message.lower())

    def test_already_off_zero_service(self):
        entity = HomeAssistant.get_registry().get("light.vizzini")
        calls = []
        with patch.object(HaActions, "_call_service", side_effect=lambda *a, **k: calls.append(a) or True):
            result = execute_verified("device_off", entity, utterance="wyłącz światło")
        self.assertEqual(result.outcome, "ALREADY_SATISFIED")
        self.assertEqual(calls, [])

    def test_turn_off_verified(self):
        _load(SAMPLE_ON)
        entity = HomeAssistant.get_registry().get("light.vizzini")
        with patch.object(HaActions, "_call_service", return_value=True):
            result = execute_verified("device_off", entity, utterance="wyłącz światło")
        self.assertEqual(result.outcome, "SUCCESS")
        self.assertEqual(result.after, "off")

    def test_brightness_verified(self):
        _load(SAMPLE_ON)
        entity = HomeAssistant.get_registry().get("light.vizzini")
        with patch.object(HaActions, "_call_service", return_value=True):
            result = execute_verified(
                "light_brightness", entity, value=80, utterance="ustaw światło na 80 procent"
            )
        self.assertEqual(result.outcome, "SUCCESS")
        self.assertTrue(result.mutated)

    def test_timeout_bounded(self):
        entity = HomeAssistant.get_registry().get("light.vizzini")
        t0 = time.perf_counter()
        with patch.object(HaActions, "_call_service", return_value=True):
            execute_verified(
                "device_on",
                entity,
                utterance="włącz światło",
                read_state=lambda eid: entity,
                timeout_s=0.2,
            )
        self.assertLess(time.perf_counter() - t0, 1.5)

    def test_unknown_does_not_blind_retry(self):
        entity = HomeAssistant.get_registry().get("light.vizzini")
        with patch.object(HaActions, "_call_service", return_value=True):
            first = execute_verified(
                "device_on",
                entity,
                utterance="włącz światło",
                read_state=lambda eid: entity,
                timeout_s=0.2,
            )
        self.assertEqual(first.outcome, UNKNOWN)
        calls = []
        with patch.object(HaActions, "_call_service", side_effect=lambda *a, **k: calls.append(a) or True):
            second = execute_verified(
                "device_on",
                entity,
                utterance="włącz światło",
                read_state=lambda eid: entity,
                timeout_s=0.2,
            )
        self.assertEqual(calls, [])
        self.assertEqual(second.service_status, "skipped_unverified_retry")

    def test_service_failed_is_failed_not_success(self):
        _load(SAMPLE_OFF)
        entity = HomeAssistant.get_registry().get("light.vizzini")
        with patch.object(HaActions, "_call_service", return_value=False):
            result = execute_verified("device_on", entity, utterance="włącz światło")
        self.assertEqual(result.outcome, "FAILED")
        self.assertFalse(result.mutated)
        self.assertNotIn("włączone", result.message.lower())


class Phase13C55SpeechTests(unittest.TestCase):
    def test_dedup_vizzini(self):
        self.assertEqual(
            dedup_device_name("Inspire Vizzini D56 Vizzini"),
            "Inspire Vizzini D56",
        )
        self.assertFalse(
            has_duplicate_device_token(dedup_device_name("Inspire Vizzini D56 Vizzini"))
        )

    def test_entity_id_stripped(self):
        self.assertNotIn("light.vizzini", strip_entity_ids("Stan light.vizzini jest on"))

    def test_spoken_name_semantic(self):
        self.assertEqual(
            spoken_name_for(
                entity_id="light.vizzini",
                friendly_name="Inspire Vizzini D56 Vizzini",
                domain="light",
                utterance="włącz światło",
            ),
            "światło",
        )

    def test_verified_on_natural(self):
        msg = ha_user_message(
            outcome="SUCCESS",
            action="device_on",
            data={"spoken_name": "światło"},
        )
        self.assertEqual(msg, "Światło włączone.")

    def test_already_on_copy(self):
        msg = ha_user_message(outcome="ALREADY_SATISFIED", action="device_on")
        self.assertEqual(msg, "Już jest włączone.")

    def test_unverified_honest(self):
        msg = ha_user_message(
            outcome="UNKNOWN",
            action="device_on",
            data={"spoken_name": "światło"},
        )
        self.assertIn("nie mogę potwierdzić", msg.lower())
        self.assertNotIn("gotowe", msg.lower())

    def test_no_callcenter_closer(self):
        spoken = speak_ha_result(
            ToolResult.ok(
                "Światło włączone. Czy mogę pomóc w czymś jeszcze?",
                data={
                    "verification": "SUCCESS",
                    "ha_action": "device_on",
                    "spoken_name": "światło",
                },
                tool_name="turn_on_device",
            ),
            utterance="włącz światło",
        )
        self.assertNotIn("czymś jeszcze", spoken.lower())


class Phase13C55LatencyTests(unittest.TestCase):
    def test_idle_span_marked_invalid(self):
        tracker = LatencyTracker()
        tracker.mark("wake_start")
        tracker.marks["wake_detected"] = tracker.marks["wake_start"] + 7000.0
        snap = tracker.snapshot()
        self.assertIn("wake_detection_ms", snap)
        self.assertIn("wake_detection_ms", tracker.invalid)
        self.assertEqual(sanity_reason("wake_detection_ms", 6_967_813), "idle_or_stale_timer")

    def test_recorded_wake_preferred(self):
        tracker = LatencyTracker()
        tracker.mark("wake_start")
        tracker.marks["wake_detected"] = tracker.marks["wake_start"] + 7000.0
        tracker.record_ms("wake_detection_ms", 42.0)
        snap = tracker.snapshot()
        self.assertEqual(snap["wake_detection_ms"], 42.0)
        self.assertNotIn("wake_detection_ms", tracker.invalid)


class Phase13C55EvalTests(unittest.TestCase):
    def test_behavioral_eval_minimum_750(self):
        cases = all_eval_cases()
        self.assertGreaterEqual(len(cases), 1200)
        det = [c for c in cases if c.category == "deterministic"]
        poison = [c for c in cases if c.category == "context-poison"]
        semantic = [c for c in cases if c.category == "semantic-nl"]
        smart = [c for c in cases if c.category == "smart-home"]
        hardening = [c for c in cases if c.category == "hardening"]
        random_poison = [c for c in cases if c.category == "random-poison"]
        self.assertGreaterEqual(len(det), 250)
        self.assertGreaterEqual(len(poison), 250)
        self.assertGreaterEqual(len(semantic), 150)
        self.assertGreaterEqual(len(smart), 100)
        self.assertGreaterEqual(len(hardening), 150)
        self.assertGreaterEqual(len(random_poison), 300)
        core_n = len(det) + len(poison) + len(semantic) + len(smart) + len(hardening)
        self.assertGreaterEqual(core_n, 900)
        summary = run_behavioral_eval()
        self.assertGreaterEqual(summary["total"], 1200)
        self.assertEqual(summary["failed"], 0, summary["failures"][:12])
        self.assertEqual(summary.get("fastpath_semantic_bypass_rate"), 0)
        self.assertEqual(summary.get("fastpath_policy_bypass_rate"), 0)
        self.assertEqual(summary.get("ha_false_success_rate"), 0)
        self.assertEqual(summary.get("ha_unverified_success_claim_rate"), 0)
        self.assertEqual(summary.get("dictation_target_leak_rate"), 0)

    def test_cm_fastpath_natural_and_firewall(self):
        clear_unverified()
        _load(SAMPLE_OFF)
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            mgr = ConversationManager(
                registry=build_default_registry(confirm_medium=False),
                model=FakeModelClient([]),
                memory_manager=mem,
            )
            mgr.activate()
            buf = io.StringIO()
            with patch.object(HaActions, "_call_service", return_value=True):
                with patch("sys.stdout", buf):
                    result = mgr.handle("włącz światło")
            log = buf.getvalue()
            self.assertIn("[AI SEMANTIC]", log)
            self.assertIn("selected_path=FASTPATH", log)
            self.assertIn("[INTENT FIREWALL]", log)
            self.assertIn("włączone", (result.text or "").lower())
            self.assertNotIn("czy mogę pomóc", (result.text or "").lower())
            self.assertLess(
                log.find("[AI SEMANTIC]"),
                log.find("selected_path=FASTPATH"),
            )


class Phase13C55HardeningTests(unittest.TestCase):
    def test_no_i_wpisz_is_not_truncated(self):
        from core.text_pipeline import looks_truncated_command

        self.assertFalse(
            looks_truncated_command("No i wpisz tam, że Elian jest GOAT-em.")
        )
        self.assertFalse(looks_truncated_command("I teraz zaznacz cały tekst."))
        self.assertTrue(looks_truncated_command("działa jak agent i zaznacz cały tekst"))
        self.assertTrue(looks_truncated_command("no i..."))

    def test_dictation_payload_strips_target_and_ze(self):
        from core.computer_semantics import extract_type_payload, parse_dictation

        self.assertEqual(
            extract_type_payload("Wpisz w Notatniku Elian jest GOAT-em."),
            "Elian jest GOAT-em",
        )
        self.assertEqual(extract_type_payload("Wpisz, że jest fajnie."), "jest fajnie")
        self.assertEqual(
            parse_dictation("Do Notatnika wpisz Hello World.")["payload"],
            "Hello World",
        )
        self.assertEqual(
            extract_type_payload("Wpisz print('HelloWorld')"),
            "print('HelloWorld')",
        )
        self.assertEqual(
            extract_type_payload("Wpisz https://example.com/TestA"),
            "https://example.com/TestA",
        )

    def test_garbled_is_ask_no_tools(self):
        env = decide_semantics("To zróbce.", allow_llm=False)
        self.assertEqual(env.mode, "ASK")
        self.assertFalse(env.needs_tools)

    def test_screen_variants(self):
        from core.perception.screen_truth import looks_screen_observation_query

        for phrase in (
            "co widzisz?",
            "okej, co teraz widzisz?",
            "jakie okno jest aktywne?",
            "na co teraz patrzę?",
        ):
            self.assertTrue(looks_screen_observation_query(phrase), phrase)

    def test_unknown_shortlist_has_no_mutations(self):
        from core.intent_family import IntentClassification, IntentFamily
        from core.tool_shortlist import shortlist_tools

        catalog = [
            {"name": "open_app", "available": True},
            {"name": "play_media", "available": True},
            {"name": "turn_on_device", "available": True},
            {"name": "web_search", "available": True},
        ]
        info = IntentClassification(family=IntentFamily.UNKNOWN, is_complete=False)
        names = [item["name"] for item in shortlist_tools(catalog, goal="asdf", classification=info)]
        self.assertNotIn("open_app", names)
        self.assertNotIn("play_media", names)
        self.assertNotIn("turn_on_device", names)


if __name__ == "__main__":
    unittest.main()
