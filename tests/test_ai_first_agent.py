"""Phase 10.5 — AI-first tool decision (paraphrases, not regex lists)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent_safety import (
    side_effect_blocked_for_intent,
    unknown_allows_tool,
    validate_side_effect_arguments,
)
from core.conversation_manager import ConversationManager
from core.intent_family import IntentClassification, IntentFamily, classify_intent_family
from core.model_client import FakeModelClient, reset_model_client
from core.planner import Planner
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from memory.manager import MemoryManager


def _registry(log):
    registry = ToolRegistry(confirm_medium=False)

    def open_app(args):
        log.append({"tool": "open_app", "arguments": dict(args)})
        return ToolResult.ok(f"Uruchomiłem {args.get('name')}.", tool_name="open_app")

    def play_media(args):
        log.append({"tool": "play_media", "arguments": dict(args)})
        return ToolResult.ok("Odtwarzam.", tool_name="play_media", data=args)

    def set_device_value(args):
        log.append({"tool": "set_device_value", "arguments": dict(args)})
        return ToolResult.ok("OK.", tool_name="set_device_value", data=args)

    def web_search(args):
        log.append({"tool": "web_search", "arguments": dict(args)})
        return ToolResult.ok("Szukam.", tool_name="web_search", data=args)

    for name, handler, props, required in (
        ("open_app", open_app, {"name": {"type": "string"}}, ["name"]),
        ("play_media", play_media, {"query": {"type": "string"}}, ["query"]),
        (
            "set_device_value",
            set_device_value,
            {
                "action": {"type": "string"},
                "target": {"type": "string"},
                "domain": {"type": "string"},
                "value": {"type": "integer"},
            },
            [],
        ),
        ("web_search", web_search, {"query": {"type": "string"}}, ["query"]),
    ):
        registry.register(Tool(
            name=name,
            description=name,
            handler=handler,
            risk_level=RiskLevel.SAFE,
            parameters={"type": "object", "properties": props, "required": required},
        ))
    return registry


class UnknownPolicyTests(unittest.TestCase):

    def test_open_app_empty_blocked_under_unknown(self):
        info = classify_intent_family("asdf qwer")
        self.assertEqual(info.family, IntentFamily.UNKNOWN)
        self.assertTrue(
            side_effect_blocked_for_intent(info, "open_app", {})
        )
        self.assertFalse(unknown_allows_tool("open_app", {}, info))
        self.assertIsNotNone(validate_side_effect_arguments("open_app", {}))

    def test_play_media_blocked_under_unknown(self):
        info = IntentClassification(
            family=IntentFamily.UNKNOWN,
            is_complete=False,
            raw_text="asdf qwer zxcv",
        )
        self.assertTrue(
            side_effect_blocked_for_intent(
                info,
                "play_media",
                {"query": "lofi"},
            )
        )
        self.assertFalse(unknown_allows_tool("play_media", {"query": "lofi"}, info))

    def test_web_search_allowed_under_unknown(self):
        info = IntentClassification(
            family=IntentFamily.UNKNOWN,
            is_complete=False,
            raw_text="Kto wygrał mecz?",
        )
        self.assertFalse(
            side_effect_blocked_for_intent(
                info,
                "web_search",
                {"query": "wynik meczu wczoraj"},
            )
        )

    def test_shutdown_hard_blocked(self):
        info = IntentClassification(
            family=IntentFamily.UNKNOWN,
            is_complete=False,
            raw_text="x",
        )
        self.assertTrue(
            side_effect_blocked_for_intent(info, "shutdown_pc", {})
        )


class AiFirstConversationTests(unittest.TestCase):

    def setUp(self):
        reset_model_client()
        self.tmp = tempfile.TemporaryDirectory()
        self.log = []
        self.registry = _registry(self.log)
        reset_registry_for_tests(self.registry)
        self.mem = MemoryManager(Path(self.tmp.name) / "mem.json")

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()
        self.tmp.cleanup()

    def test_fajny_glos_zero_tools(self):
        model = FakeModelClient([
            {
                "type": "final_answer",
                "message": "Dzięki! Trochę nad nim pracowaliśmy.",
            },
        ])
        manager = ConversationManager(
            registry=self.registry,
            model=model,
            memory_manager=self.mem,
        )
        manager.activate()
        result = manager.handle("Fajny masz głos.")
        self.assertEqual(self.log, [])
        self.assertNotIn("nie zrozumiałem", (result.text or "").lower())
        self.assertTrue(result.should_speak or result.source in ("agent", "chat", "ack"))

    def test_nie_wiem_co_robic_not_nie_zrozumialem(self):
        model = FakeModelClient([
            {
                "type": "final_answer",
                "message": "Możemy puścić muzykę, pogadać albo coś ogarnąć w domu — co wolisz?",
            },
        ])
        manager = ConversationManager(
            registry=self.registry,
            model=model,
            memory_manager=self.mem,
        )
        manager.activate()
        result = manager.handle("Nie wiem co robić.")
        self.assertEqual(self.log, [])
        self.assertNotIn("nie zrozumiałem", (result.text or "").lower())
        self.assertTrue(result.text)

    def test_czarna_dziura_knowledge_zero_side_effects(self):
        # Phrase is UNKNOWN to classifier — AI-first final_answer (no new regex).
        model = FakeModelClient([
            {
                "type": "final_answer",
                "message": (
                    "Czarna dziura to obszar o tak silnej grawitacji, "
                    "że nic — nawet światło — nie ucieka."
                ),
            },
        ])
        manager = ConversationManager(
            registry=self.registry,
            model=model,
            memory_manager=self.mem,
        )
        manager.activate()
        result = manager.handle("Jak działa czarna dziura?")
        self.assertEqual(self.log, [])
        self.assertNotIn("nie zrozumiałem", (result.text or "").lower())
        self.assertTrue(result.text)

    def test_otworz_discord_still_works(self):
        model = FakeModelClient([])
        manager = ConversationManager(
            registry=self.registry,
            model=model,
            memory_manager=self.mem,
        )
        manager.activate()
        result = manager.handle("Otwórz Discord.")
        self.assertTrue(any(item.get("tool") == "open_app" for item in self.log))
        self.assertEqual(
            str(self.log[-1]["arguments"].get("name") or "").lower(),
            "discord",
        )

    def test_media_paraphrase_play_allowed(self):
        """Paraphrase not required to match MEDIA regex — FakeModel play_media OK."""
        model = FakeModelClient([
            {
                "type": "tool_call",
                "tool": "play_media",
                "arguments": {"query": "muzyka"},
            },
        ])
        unknown = IntentClassification(
            family=IntentFamily.UNKNOWN,
            is_complete=False,
            raw_text="Może coś puścimy?",
        )
        result = Planner(self.registry, model=model).run(
            "Może coś puścimy?",
            context={"intent_classification": unknown, "raw_text": "Może coś puścimy?"},
        )
        self.assertTrue(any(item.get("tool") == "play_media" for item in self.log))
        self.assertFalse(result.source == "unknown_guard" and not self.log)

    def test_light_paraphrase_clarify_or_device(self):
        model = FakeModelClient([
            {
                "type": "clarification",
                "question": "Które światło mam rozjaśnić?",
            },
        ])
        unknown = IntentClassification(
            family=IntentFamily.UNKNOWN,
            is_complete=False,
            raw_text="Przydałoby się więcej światła.",
        )
        result = Planner(self.registry, model=model).run(
            "Przydałoby się więcej światła.",
            context={
                "intent_classification": unknown,
                "raw_text": "Przydałoby się więcej światła.",
            },
        )
        self.assertFalse(any(item.get("tool") == "open_app" for item in self.log))
        self.assertTrue(
            result.needs_clarification
            or any(item.get("tool") == "set_device_value" for item in self.log)
            or "świat" in (result.message or "").lower()
        )

    def test_ack_ok_and_dziekuje(self):
        model = FakeModelClient([
            {"type": "tool_call", "tool": "open_app", "arguments": {"name": "X"}},
        ])
        manager = ConversationManager(
            registry=self.registry,
            model=model,
            memory_manager=self.mem,
        )
        manager.activate()
        self.assertEqual(manager.handle("Ok.").source, "ack")
        self.assertEqual(manager.handle("Dziękuję.").source, "ack")
        self.assertEqual(self.log, [])

    def test_unknown_empty_open_app_not_executed(self):
        model = FakeModelClient([
            {"type": "tool_call", "tool": "open_app", "arguments": {}},
            {
                "type": "final_answer",
                "message": "Powiedz, co mam otworzyć.",
            },
        ])
        unknown = IntentClassification(
            family=IntentFamily.UNKNOWN,
            is_complete=False,
            raw_text="asdf qwer zxcv",
        )
        result = Planner(self.registry, model=model).run(
            "asdf qwer zxcv",
            context={"intent_classification": unknown},
        )
        self.assertFalse(any(item.get("tool") == "open_app" for item in self.log))
        self.assertNotIn("nie zrozumiałem", (result.message or "").lower())


if __name__ == "__main__":
    unittest.main()
