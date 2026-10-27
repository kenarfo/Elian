# ==========================
# ELIAN — P3 live Jarvis RC regressions
# ==========================
"""Offline gates for sticky MJ topic, verified HA success, IntentFamily, clarify vs knowledge."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from core.agent_repair import build_deterministic_decision
from core.clarification_policy import clarification_limit_reached
from core.clarification_slots import build_pending_clarification
from core.conversation_focus import (
    clear_focus_topic,
    enrich_goal_with_focus,
    is_comprehension_repair,
    is_new_topic_knowledge,
    update_focus,
)
from core.conversation_manager import ConversationManager
from core.intent_family import IntentClassification, IntentFamily, classify_intent_family
from core.model_client import FakeModelClient
from core.planner import (
    Planner,
    PlannerResult,
    _goal_already_verified,
    _speech_for_verified_observations,
)
from core.semantic_executive import decide_semantics
from core.soft_goals import detect_soft_goal
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from memory.manager import MemoryManager


class StickyTopicP0Tests(unittest.TestCase):
    def test_wyjasnij_new_subject_not_comprehension_repair(self):
        text = "Wyjaśnij co to fotosynteza."
        self.assertTrue(is_new_topic_knowledge(text))
        self.assertFalse(is_comprehension_repair(text))

    def test_enrich_does_not_inject_mj_osoby(self):
        focus = update_focus(
            None,
            user_text="Michael Jackson wikipedia",
            assistant_text="Michael Jackson był piosenkarzem.",
        )
        self.assertIn("Michael", " ".join(focus.get("referenced_people") or []))
        enriched = enrich_goal_with_focus("Wyjaśnij co to fotosynteza.", focus)
        self.assertNotIn("Osoby:", enriched)
        self.assertNotIn("Michael Jackson", enriched)
        self.assertIn("fotosynteza", enriched.casefold())

    def test_clear_focus_drops_people_keeps_devices(self):
        focus = {
            "current_topic": "MJ",
            "referenced_people": ["Michael Jackson"],
            "referenced_apps": [],
            "referenced_devices": ["light.vizzini"],
            "referenced_web_entities": [],
            "last_question_summary": "MJ",
            "last_answer_summary": "bio",
            "last_web_consensus": "x",
            "last_web_query": "mj",
        }
        cleared = clear_focus_topic(focus)
        self.assertEqual(cleared.get("current_topic"), "")
        self.assertEqual(cleared.get("referenced_people"), [])
        self.assertEqual(cleared.get("last_answer_summary"), "")
        self.assertEqual(cleared.get("referenced_devices"), ["light.vizzini"])


class VerifiedSuccessP0Tests(unittest.TestCase):
    def test_goal_already_verified_turn_on(self):
        obs = [
            {
                "tool": "turn_on_device",
                "success": True,
                "message": "OK",
                "data": {"verification": "SUCCESS", "entity_id": "light.vizzini"},
            }
        ]
        info = IntentClassification(
            family=IntentFamily.SMART_HOME,
            is_complete=True,
            slots={},
            raw_text="włącz lampę",
        )
        self.assertTrue(_goal_already_verified(obs, info))
        spoken = _speech_for_verified_observations(obs, goal="włącz lampę")
        self.assertTrue(spoken)
        self.assertNotIn("Nie udało", spoken)

    def test_planner_stops_after_verified_no_empty_set_device(self):
        registry = ToolRegistry()
        calls = []

        def _on(args):
            calls.append(("turn_on_device", dict(args or {})))
            return ToolResult.ok(
                "Włączono.",
                tool_name="turn_on_device",
                data={
                    "verification": "SUCCESS",
                    "ha_outcome": "SUCCESS",
                    "ha_action": "turn_on",
                    "entity_id": "light.vizzini",
                    "friendly_name": "Vizzini",
                    "domain": "light",
                    "verified_success": True,
                },
            )

        def _set(args):
            calls.append(("set_device_value", dict(args or {})))
            return ToolResult.fail("brak args", tool_name="set_device_value")

        def _state(args):
            return ToolResult.ok(
                "on",
                tool_name="get_device_state",
                data={"state": "on", "entity_id": "light.vizzini"},
            )

        registry.register(
            Tool(
                name="turn_on_device",
                description="on",
                parameters={"type": "object", "properties": {}},
                handler=_on,
            )
        )
        registry.register(
            Tool(
                name="set_device_value",
                description="set",
                parameters={"type": "object", "properties": {}},
                handler=_set,
            )
        )
        registry.register(
            Tool(
                name="get_device_state",
                description="state",
                parameters={"type": "object", "properties": {}},
                handler=_state,
            )
        )

        class _Model:
            def __init__(self):
                self.n = 0

            def complete(self, *a, **k):
                self.n += 1
                if self.n == 1:
                    return (
                        '{"type":"tool_call","tool":"turn_on_device",'
                        '"arguments":{"entity_id":"light.vizzini","target":"light.vizzini"}}'
                    )
                return '{"type":"tool_call","tool":"set_device_value","arguments":{}}'

        info = IntentClassification(
            family=IntentFamily.SMART_HOME,
            is_complete=False,
            slots={},
            raw_text="włącz lampę",
        )
        model = FakeModelClient(
            [
                (
                    '{"type":"tool_call","tool":"turn_on_device",'
                    '"arguments":{"entity_id":"light.vizzini","target":"light.vizzini"}}'
                ),
                '{"type":"tool_call","tool":"set_device_value","arguments":{}}',
                '{"type":"tool_call","tool":"set_device_value","arguments":{}}',
            ]
        )
        planner = Planner(registry=registry, model=model, max_steps=4)
        result = planner.run(
            "włącz lampę",
            context={
                "raw_text": "włącz lampę",
                "intent_classification": info,
            },
        )
        self.assertIsInstance(result, PlannerResult)
        self.assertTrue(result.success)
        self.assertNotIn("Nie udało mi się przejść dalej", result.message or "")
        tools = [c[0] for c in calls]
        self.assertIn("turn_on_device", tools)
        self.assertNotIn("set_device_value", tools)


class IntentFamilyP0Tests(unittest.TestCase):
    def test_knowledge_deterministic_no_unbound(self):
        info = classify_intent_family("Wyjaśnij co to fotosynteza.")
        self.assertEqual(info.family, IntentFamily.KNOWLEDGE)
        det = build_deterministic_decision(info)
        self.assertIsNotNone(det)
        registry = ToolRegistry()
        planner = Planner(registry=registry, model=MagicMock(), max_steps=2)
        result = planner.run(
            "Wyjaśnij co to fotosynteza.",
            context={
                "raw_text": "Wyjaśnij co to fotosynteza.",
                "intent_classification": info,
            },
        )
        self.assertIsNotNone(result)
        self.assertTrue(
            result.needs_knowledge
            or result.source
            in ("deterministic", "knowledge", "local_knowledge", "talk", "social")
            or bool(result.message)
        )


class ClarifyVsKnowledgeP0Tests(unittest.TestCase):
    def test_pending_light_clarify_yields_to_information_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = ConversationManager(
                memory_manager=mem, registry=ToolRegistry(), model=MagicMock()
            )
            cm.activate()
            cm.pending_clarification = build_pending_clarification(
                goal="ciemno tu",
                question="Które światło mam rozjaśnić?",
                intent_family="SMART_HOME",
                action="brightness_up",
                question_type="light_target",
                missing_slots=["target"],
                known_slots={},
            )
            cm.clarification_rounds = max(2, 3)
            self.assertTrue(clarification_limit_reached(cm.clarification_rounds))

            result = cm.handle("Co to znaczy mieć arachnofobię?")
            self.assertNotEqual(result.source, "clarify_limit")
            self.assertNotIn("Spróbuj od nowa", result.text or "")


class PronounVolumeP1Tests(unittest.TestCase):
    def test_wylacz_ja_is_smart_home_not_app_close(self):
        env = decide_semantics("wyłącz ją", allow_llm=False)
        self.assertEqual(env.intent, "smart_home_control")
        self.assertIn("SMART_HOME", env.allowed_domains or [])
        self.assertNotEqual(env.intent, "close_referent")

    def test_tiszej_soft_volume(self):
        soft = detect_soft_goal("trochę tiszej")
        self.assertIsNotNone(soft)
        self.assertEqual(soft.get("kind"), "volume_down")


if __name__ == "__main__":
    unittest.main()
