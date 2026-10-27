"""Phase 10.1 — hard gate agent intent contract tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.conversation_manager import ConversationManager
from core.intent_family import classify_intent_family, family_label, is_complete_intent
from core.model_client import FakeModelClient, reset_model_client
from core.text_pipeline import prepare_user_text
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from core.risk import RiskLevel
from core.tool_result import ToolResult
from integrations.datetime_parse import ensure_aware, parse_when


def _registry():
    registry = ToolRegistry(confirm_medium=False)

    def open_app(args):
        return ToolResult.ok(
            f"Uruchomiłem {args.get('name')}.",
            tool_name="open_app",
            data={"app": args.get("name")},
        )

    def play_media(args):
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


class IntentFamilyTests(unittest.TestCase):

    def test_spotify_app_family(self):
        info = classify_intent_family("możesz odpalić mi spotify")
        self.assertIn(info.family.value, ("APP", "MEDIA"))
        self.assertEqual(info.slots.get("app_name"), "Spotify")
        self.assertTrue(info.is_complete)

    def test_media_family(self):
        info = classify_intent_family("puść coś michaela jacksona")
        self.assertEqual(info.family.value, "MEDIA")
        self.assertTrue(info.is_complete)
        self.assertIn("michael", info.slots.get("media_query", "").lower())

    def test_knowledge_family(self):
        info = classify_intent_family("co oznacza ai?")
        self.assertEqual(info.family.value, "KNOWLEDGE")
        self.assertTrue(info.is_complete)

    def test_vision_family(self):
        self.assertEqual(family_label("co mam na ekranie"), "VISION")

    def test_reminder_family(self):
        info = classify_intent_family("przypomnij mi za dwie minuty o treningu")
        self.assertEqual(info.family.value, "REMINDER")

    def test_pingnij_reminder_family_extracts_when(self):
        info = classify_intent_family("pingnij mnie za kwadrans o herbacie")
        self.assertEqual(info.family.value, "REMINDER")
        self.assertTrue(info.is_complete)
        self.assertEqual(info.slots.get("when"), "za kwadrans")
        self.assertEqual(info.slots.get("reminder_text"), "herbacie")

    def test_parse_when_quarter_hour(self):
        from datetime import datetime, timezone

        base = ensure_aware(datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc))
        parsed = parse_when("za kwadrans", base=base)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed, base.replace(minute=15))


class AgentSmokeIntegrationTests(unittest.TestCase):
    """CM → Agent → Planner with simulated json_retry + bad clarification."""

    def setUp(self):
        self.registry = _registry()
        reset_registry_for_tests(self.registry)
        self.model = FakeModelClient([
            "broken json {{{",
            {"type": "clarification", "question": "Czy chcesz otworzyć aplikację?"},
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

    def test_spotify_no_generic_clarification(self):
        result = self.manager.handle("możesz odpalić mi spotify")
        self.assertNotEqual(result.source, "clarify")
        self.assertIn("Spotify", result.text)
        self.assertTrue(
            any(
                step.get("tool") == "open_app"
                for step in self.manager.last_observations
            )
            or "Uruchomiłem" in result.text
        )
        self.assertNotIn("Czy chcesz otworzyć aplikację", result.text)

    def test_michael_jackson_media_not_open_app(self):
        result = self.manager.handle("puść coś michaela jacksona")
        self.assertNotEqual(result.source, "clarify")
        self.assertIn("Michael", result.text)
        self.assertNotIn("otworzyć aplikację", result.text.lower())

    @patch("core.conversation_manager.ask_ai", return_value="AI to sztuczna inteligencja.")
    def test_knowledge_no_tools_no_clarification(self, mock_ai):
        result = self.manager.handle("co oznacza ai?")
        self.assertNotEqual(result.source, "clarify")
        self.assertIn("sztuczna inteligencja", result.text.lower())
        mock_ai.assert_called_once()
        self.assertFalse(self.manager.last_observations)

    @patch("core.conversation_manager.ask_ai", return_value="AI to sztuczna inteligencja.")
    def test_pending_cleared_on_new_knowledge_intent(self, mock_ai):
        self.model.push(
            {"type": "clarification", "question": "Czy chcesz otworzyć aplikację?"},
        )
        first = self.manager.handle("możesz odpalić mi spotify")
        self.assertIn(first.source, ("agent", "fast_path", "deterministic"))
        self.assertNotEqual(first.source, "clarify")
        # Force pending for test of second utterance
        self.manager.pending_clarification = {
            "goal": "możesz odpalić mi spotify",
            "question": "Czy chcesz otworzyć aplikację?",
            "started_at": 0,
        }
        second = self.manager.handle("co oznacza ai?")
        self.assertIsNone(self.manager.pending_clarification)
        self.assertNotIn("Użytkownik uzupełnił", str(self.model.calls))
        self.assertIn("sztuczna inteligencja", second.text.lower())


class WakeActiveSessionTests(unittest.TestCase):

    def test_elian_prefix_stripped_in_active_session(self):
        prepared = prepare_user_text("Elian, co oznacza AI?")
        self.assertNotIn("elian", prepared.semantic_text.lower())
        self.assertIn("oznacza", prepared.semantic_text.lower())


if __name__ == "__main__":
    unittest.main()
