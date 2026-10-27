# ==========================
# ELIAN — Phase 13 C.5.4 Part X proactive collaboration
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.collaboration import (
    ActiveTaskContext,
    CollaborationStore,
    ContextSalience,
    OptionContext,
    collaboration_overrides_ack,
    looks_internal_tts,
    should_initiate,
)
from core.collaboration_eval import (
    multi_turn_scenarios,
    proactive_case_specs,
    run_multi_turn_eval,
    run_proactive_eval,
    sample_conversations,
)
from core.clock import FakeClock
from core.conversation_manager import ConversationManager
from core.events import ACTIVE_WINDOW_CHANGED, Event, Priority
from core.followup_policy import has_generic_closer
from core.metrics import get_metrics, reset_metrics
from core.model_client import FakeModelClient, reset_model_client
from core.proactive import ProactiveManager
from core.semantic_eval import all_eval_cases, run_behavioral_eval
from core.semantic_executive import DecisionMode, decide_semantics, fast_path_permitted
from core.tools import reset_registry_for_tests
from memory.manager import MemoryManager


class Phase13C54PartXTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        reset_metrics()

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()

    def _mgr(self) -> ConversationManager:
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        mgr = ConversationManager(
            memory_manager=MemoryManager(Path(tmp.name)),
            memory=[],
            model=FakeModelClient([]),
        )
        mgr.activate()
        return mgr

    def test_existing_eval_not_reduced(self):
        cases = all_eval_cases()
        self.assertGreaterEqual(len(cases), 650)
        det = [c for c in cases if c.category == "deterministic"]
        poison = [c for c in cases if c.category == "context-poison"]
        semantic = [c for c in cases if c.category == "semantic-nl"]
        self.assertGreaterEqual(len(det), 250)
        self.assertGreaterEqual(len(poison), 250)
        self.assertGreaterEqual(len(semantic), 150)

    def test_proactive_eval_minimum_50(self):
        specs = proactive_case_specs()
        self.assertGreaterEqual(len(specs), 50)
        summary = run_proactive_eval()
        self.assertGreaterEqual(summary["total"], 50)
        self.assertEqual(summary["failed"], 0, summary["failures"][:12])
        for key, value in summary["metrics"].items():
            self.assertEqual(value, 0, key)

    def test_multi_turn_minimum_30(self):
        scenarios = multi_turn_scenarios()
        self.assertGreaterEqual(len(scenarios), 30)
        for sc in scenarios:
            self.assertGreaterEqual(len(sc.turns), 3, sc.name)
            self.assertLessEqual(len(sc.turns), 8, sc.name)
        summary = run_multi_turn_eval()
        self.assertEqual(summary["failed"], 0, summary["failures"][:12])
        self.assertGreaterEqual(summary["multi_turn_total_turns"], 90)

    def test_behavioral_eval_reports_split_counts(self):
        summary = run_behavioral_eval()
        self.assertGreaterEqual(summary["single_turn_behavioral_cases"], 250)
        self.assertGreaterEqual(summary["context_poison_cases"], 250)
        self.assertGreaterEqual(summary["semantic_natural_language_cases"], 150)
        self.assertGreaterEqual(summary["proactive_cases"], 50)
        self.assertGreaterEqual(summary["multi_turn_scenarios"], 30)
        self.assertGreaterEqual(summary["multi_turn_total_turns"], 90)
        self.assertEqual(summary["failed"], 0, summary["failures"][:8])
        self.assertEqual(summary.get("proactive_passed"), summary["proactive_cases"])
        self.assertEqual(summary.get("multi_turn_passed"), summary["multi_turn_scenarios"])

    def test_window_change_silent(self):
        spoken = []
        clock = FakeClock()
        pm = ProactiveManager(clock=clock, speak_fn=spoken.append, busy_check=lambda: False)
        decision = pm.handle_event(
            Event(
                type=ACTIVE_WINDOW_CHANGED,
                source="vision",
                data={"title": "Chrome", "hwnd": 4},
                timestamp=clock.time(),
            )
        )
        self.assertEqual(decision.action, "silent")
        self.assertEqual(spoken, [])

    def test_notify_after_requested_completion(self):
        spoken = []
        clock = FakeClock()
        pm = ProactiveManager(clock=clock, speak_fn=spoken.append, busy_check=lambda: False)
        decision = pm.handle_event(
            Event(
                type="task_completed",
                source="task",
                priority=Priority.NORMAL,
                timestamp=clock.time(),
                data={
                    "reason": "task_completed_requested",
                    "notify_requested": True,
                    "user_value": True,
                    "has_background_work": True,
                    "message": "Skończyłem. Problem jest w konfiguracji portu, nie w samym urządzeniu.",
                },
            )
        )
        self.assertEqual(decision.action, "notify")
        self.assertTrue(spoken)
        self.assertIn("konfiguracji", spoken[0].lower())
        self.assertNotIn("PROCEDURE_COMPLETE", spoken[0])

    def test_joke_during_task_not_notepad(self):
        store = CollaborationStore()
        store.seed_task(goal="Notatnik", status="running", current_step="wpisywanie")
        env = decide_semantics(
            "Opowiedz żart.",
            conversation=store.semantic_conversation(),
            allow_llm=False,
        )
        self.assertEqual(env.mode, "TALK")
        self.assertEqual(env.intent, "creative_humor")
        self.assertFalse(env.needs_tools)
        self.assertFalse(fast_path_permitted("Opowiedz żart.", env))

    def test_short_followup_picks_first(self):
        store = CollaborationStore()
        store.seed_task(goal="czujniki")
        store.seed_decision(
            options=[],
        )
        from core.collaboration import DecisionOption

        store.seed_decision(
            options=[
                DecisionOption(id="a", label="filtr programowy"),
                DecisionOption(id="b", label="okablowanie"),
            ],
            recommended_option="a",
            recommended_why="łatwo cofnąć",
        )
        self.assertTrue(collaboration_overrides_ack(store, "dobra"))
        env = decide_semantics("pierwsza", conversation=store.semantic_conversation(), allow_llm=False)
        reply = store.try_handle("pierwsza", env)
        self.assertIsNotNone(reply)
        self.assertEqual(reply.execute_option_id, "a")

    def test_cm_dobra_resolves_pending_not_silent_ack(self):
        from core.collaboration import DecisionOption

        mgr = self._mgr()
        mgr.collab.seed_task(goal="czujniki", status="blocked")
        mgr.collab.seed_decision(
            options=[
                DecisionOption(id="a", label="filtr programowy"),
                DecisionOption(id="b", label="okablowanie"),
            ],
            recommended_option="a",
            recommended_why="łatwo cofnąć",
        )
        result = mgr.handle("dobra")
        self.assertTrue(result.should_speak)
        self.assertIn("filtr", (result.text or "").lower())
        self.assertFalse(has_generic_closer(result.text or ""))

    def test_cm_open_notepad_no_permission_question(self):
        mgr = self._mgr()
        result = mgr.handle("Otwórz Notatnik.")
        text = (result.text or "").lower()
        self.assertNotIn("czy mogę", text)
        self.assertNotIn("czy na pewno", text)
        self.assertFalse(has_generic_closer(result.text or ""))

    def test_schemas_exist(self):
        task = ActiveTaskContext(task_id="t1", goal="x", status="running")
        dec = OptionContext(decision_id="d1", question="co?")
        sal = ContextSalience(conversation_topic="joke", active_task="x")
        self.assertEqual(task.task_id, "t1")
        self.assertEqual(dec.decision_id, "d1")
        self.assertEqual(sal.conversation_topic, "joke")
        self.assertIn("PROACTIVE_DIALOGUE", DecisionMode.__members__)

    def test_should_not_initiate_without_value(self):
        ok, why = should_initiate(
            event_type=ACTIVE_WINDOW_CHANGED, user_value=False
        )
        self.assertFalse(ok)
        self.assertEqual(why, "silent_low_value")

    def test_internal_enum_blocked(self):
        self.assertTrue(looks_internal_tts("PROCEDURE_COMPLETE"))
        self.assertTrue(looks_internal_tts("plan_id=123 step_id=4 status=RUNNING"))
        self.assertFalse(looks_internal_tts("Skończyłem sprawdzać ten problem."))

    def test_sample_conversations_available(self):
        samples = sample_conversations()
        self.assertGreaterEqual(len(samples), 5)
        for conv in samples:
            self.assertGreaterEqual(len(conv), 3)

    def test_metrics_snapshot_has_new_rates(self):
        snap = get_metrics().snapshot()
        self.assertEqual(snap["irrelevant_proactive_speech_rate"], 0)
        self.assertEqual(snap["internal_event_proactive_rate"], 0)
        self.assertEqual(snap["wrong_pending_decision_resolution_rate"], 0)
        self.assertEqual(snap["task_context_hijack_rate"], 0)
        self.assertEqual(snap["conversation_context_hijack_rate"], 0)
        self.assertEqual(snap["generic_callcenter_closer_rate"], 0)
        self.assertIn("useful_followup_offer_rate", snap)


if __name__ == "__main__":
    unittest.main()
