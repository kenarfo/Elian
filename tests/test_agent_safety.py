"""Phase 10.2 — acknowledgement / UNKNOWN safety guards."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent_safety import (
    is_acknowledgement,
    side_effect_blocked_for_intent,
    validate_side_effect_arguments,
)
from core.conversation_manager import ConversationManager
from core.intent_family import classify_intent_family
from core.model_client import FakeModelClient, reset_model_client
from core.planner import Planner
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests


def _registry(log):
    registry = ToolRegistry(confirm_medium=False)

    def open_app(args):
        log.append({"tool": "open_app", "arguments": dict(args)})
        return ToolResult.ok(
            f"Uruchomiłem {args.get('name')}.",
            tool_name="open_app",
            data={"app": args.get("name")},
        )

    def play_media(args):
        log.append({"tool": "play_media", "arguments": dict(args)})
        return ToolResult.ok(
            f"Puścimy {args.get('query')}.",
            tool_name="play_media",
            data=args,
        )

    registry.register(Tool(
        name="open_app",
        description="Open app",
        handler=open_app,
        risk_level=RiskLevel.SAFE,
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    ))
    registry.register(Tool(
        name="play_media",
        description="Play media",
        handler=play_media,
        risk_level=RiskLevel.SAFE,
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    ))
    return registry


class AcknowledgementClassificationTests(unittest.TestCase):

    def test_ok_is_acknowledgement(self):
        info = classify_intent_family("Ok.")
        self.assertTrue(is_acknowledgement(info))
        self.assertEqual(info.family.value, "CONVERSATION_CONTROL")

    def test_dzieki_spoko(self):
        for text in ("Dzięki.", "Spoko.", "Jasne", "Super!"):
            info = classify_intent_family(text)
            self.assertTrue(is_acknowledgement(info), text)

    def test_ok_open_discord_is_app(self):
        info = classify_intent_family("Ok, otwórz Discord.")
        self.assertEqual(info.family.value, "APP")
        self.assertEqual(info.slots.get("app_name"), "Discord")
        self.assertTrue(info.is_complete)

    def test_dobra_play_michael(self):
        info = classify_intent_family("Dobra, puść Michaela Jacksona.")
        self.assertEqual(info.family.value, "MEDIA")
        self.assertIn("michael", info.slots.get("media_query", "").lower())


class AcknowledgementNoToolTests(unittest.TestCase):

    def setUp(self):
        self.log = []
        self.registry = _registry(self.log)
        reset_registry_for_tests(self.registry)
        self.model = FakeModelClient([
            {"type": "tool_call", "tool": "open_app", "arguments": {}},
            {"type": "tool_call", "tool": "play_media", "arguments": {}},
            {"type": "clarification", "question": "Jaką aplikację chcesz otworzyć?"},
        ])
        self.manager = ConversationManager(
            registry=self.registry,
            memory=[],
            model=self.model,
        )
        self.manager.activate()

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()

    def test_ok_zero_tools(self):
        result = self.manager.handle("Ok.")
        self.assertEqual(result.source, "ack")
        self.assertEqual(self.log, [])
        self.assertEqual(len(self.model.calls), 0)

    def test_dzieki_zero_tools(self):
        self.manager.handle("Dzięki.")
        self.assertEqual(self.log, [])

    def test_spoko_zero_tools(self):
        self.manager.handle("Spoko.")
        self.assertEqual(self.log, [])


class UnknownSafetyTests(unittest.TestCase):

    def setUp(self):
        self.log = []
        self.registry = _registry(self.log)
        reset_registry_for_tests(self.registry)

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()

    def test_nie_wiem_conversation_not_forced_tools(self):
        model = FakeModelClient([
            {
                "type": "final_answer",
                "message": "Możemy pogadać albo coś odpalić — co wolisz?",
            },
        ])
        result = Planner(self.registry, model=model).run("Nie wiem.")
        self.assertEqual(self.log, [])
        self.assertFalse(result.needs_clarification)
        self.assertNotIn("nie zrozumiałem", (result.message or "").lower())

    def test_unknown_garbage_empty_open_app_blocked(self):
        model = FakeModelClient([
            {"type": "tool_call", "tool": "open_app", "arguments": {}},
            {"type": "tool_call", "tool": "play_media", "arguments": {}},
            {"type": "final_answer", "message": "Powiedz konkretniej, co mam zrobić."},
        ])
        result = Planner(self.registry, model=model).run("asdf qwer zxcv")
        self.assertEqual(self.log, [])
        self.assertFalse(
            any(step.get("tool") in ("open_app", "play_media") for step in result.steps)
        )

    def test_empty_open_app_not_executed(self):
        model = FakeModelClient([
            {"type": "tool_call", "tool": "open_app", "arguments": {}},
            {"type": "tool_call", "tool": "play_media", "arguments": {"query": "x"}},
        ])
        # Force APP incomplete without known target so LLM path runs
        from core.intent_family import IntentClassification, IntentFamily

        classification = IntentClassification(
            family=IntentFamily.APP,
            is_complete=False,
            slots={},
            raw_text="otwórz coś",
        )
        result = Planner(self.registry, model=model).run(
            "otwórz coś",
            context={"intent_classification": classification},
        )
        self.assertEqual(self.log, [])
        self.assertEqual(result.source, "validation_failure")
        self.assertEqual(result.steps[0]["observation"]["error"], "missing_argument")
        self.assertEqual(len(result.steps), 1)

    def test_ok_open_discord_still_works(self):
        model = FakeModelClient([])
        result = Planner(self.registry, model=model).run("Ok, otwórz Discord.")
        self.assertEqual(len(self.log), 1)
        self.assertEqual(self.log[0]["tool"], "open_app")
        self.assertEqual(self.log[0]["arguments"].get("name"), "Discord")
        self.assertFalse(result.needs_clarification)

    def test_dobra_play_media_still_works(self):
        model = FakeModelClient([])
        result = Planner(self.registry, model=model).run("Dobra, puść Michaela Jacksona.")
        self.assertEqual(len(self.log), 1)
        self.assertEqual(self.log[0]["tool"], "play_media")
        self.assertIn("michael", str(self.log[0]["arguments"]).lower())

    def test_side_effect_blocked_helper(self):
        info = classify_intent_family("Nie wiem.")
        self.assertTrue(side_effect_blocked_for_intent(info, "open_app", {}))
        self.assertTrue(
            side_effect_blocked_for_intent(
                info,
                "play_media",
                {"query": "Michael Jackson"},
            )
        )
        self.assertIsNotNone(validate_side_effect_arguments("open_app", {}))
        self.assertIsNotNone(validate_side_effect_arguments("play_media", {}))
        self.assertIsNone(
            validate_side_effect_arguments("play_media", {"query": "Michael Jackson"})
        )


if __name__ == "__main__":
    unittest.main()
