"""Phase 10.3 — natural language safety + implicit intent regressions."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.conversation_manager import ConversationManager
from core.intent_family import classify_intent_family
from core.model_client import FakeModelClient, reset_model_client
from core.planner import Planner
from core.risk import RiskLevel
from core.text_pipeline import prepare_user_text
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from core.utterance import apply_mid_utterance_correction, is_rejection_correction
from memory.manager import MemoryManager


def _registry(log):
    registry = ToolRegistry(confirm_medium=False)

    def open_app(args):
        log.append({"tool": "open_app", "arguments": dict(args)})
        return ToolResult.ok(f"Uruchomiłem {args.get('name')}.", tool_name="open_app")

    def play_media(args):
        log.append({"tool": "play_media", "arguments": dict(args)})
        return ToolResult.ok(f"Puścimy {args.get('query')}.", tool_name="play_media", data=args)

    def set_preference(args):
        log.append({"tool": "set_preference", "arguments": dict(args)})
        return ToolResult.ok("Zapisano preferencję.", tool_name="set_preference", data=args)

    def set_device_value(args):
        log.append({"tool": "set_device_value", "arguments": dict(args)})
        return ToolResult.ok("OK.", tool_name="set_device_value")

    for name, handler, props, required in (
        ("open_app", open_app, {"name": {"type": "string"}}, ["name"]),
        ("play_media", play_media, {"query": {"type": "string"}, "app": {"type": "string"}}, ["query"]),
        ("set_preference", set_preference, {"key": {"type": "string"}, "value": {"type": "string"}}, ["key", "value"]),
        ("set_device_value", set_device_value, {
            "action": {"type": "string"},
            "target": {"type": "string"},
            "domain": {"type": "string"},
            "value": {"type": "integer"},
            "entity_id": {"type": "string"},
        }, []),
    ):
        registry.register(Tool(
            name=name,
            description=name,
            handler=handler,
            risk_level=RiskLevel.SAFE,
            parameters={"type": "object", "properties": props, "required": required},
        ))
    return registry


class NegationPreservationTests(unittest.TestCase):

    def test_nie_nie_o_to_preserves_negation(self):
        phrase = "Nie, nie o to mi chodziło."
        self.assertTrue(is_rejection_correction(phrase))
        corrected, changed = apply_mid_utterance_correction(phrase)
        self.assertFalse(changed)
        self.assertIn("nie", corrected.lower())
        prepared = prepare_user_text(phrase)
        self.assertIn("nie", prepared.semantic_text.lower())
        self.assertNotEqual(prepared.semantic_text.lower().strip(), "o to mi chodziło.")

    def test_mid_utterance_spotify_youtube_still_works(self):
        # Exclusion ``nie`` keeps both apps; explicit ``znaczy`` still retargets.
        text, ok = apply_mid_utterance_correction("Otwórz Spotify nie YouTube Music")
        self.assertFalse(ok)
        self.assertIn("Spotify", text)
        self.assertIn("nie", text.lower())
        text2, ok2 = apply_mid_utterance_correction(
            "Otwórz Spotify znaczy YouTube Music"
        )
        self.assertTrue(ok2)
        self.assertIn("YouTube", text2)


class IntentNaturalLanguageTests(unittest.TestCase):

    def test_ciemno_smart_home(self):
        info = classify_intent_family("Trochę tu ciemno.")
        self.assertEqual(info.family.value, "SMART_HOME")
        self.assertTrue(info.slots.get("implicit_light"))

    def test_correction_family(self):
        info = classify_intent_family("Nie, nie o to mi chodziło.")
        self.assertEqual(info.family.value, "CORRECTION")
        self.assertTrue(info.is_complete)

    def test_natural_media_variants(self):
        for phrase in (
            "A puścisz Michael'a Jacksona?",
            "Możesz puścić Michaela Jacksona?",
            "Puściłbyś coś Michaela Jacksona?",
            "Chcę posłuchać Michaela Jacksona.",
            "Może coś Michaela Jacksona?",
        ):
            info = classify_intent_family(phrase)
            self.assertEqual(info.family.value, "MEDIA", phrase)
            self.assertIn("michael", info.slots.get("media_query", "").lower(), phrase)

    def test_explicit_preference_allowed_classify(self):
        info = classify_intent_family("Zapamiętaj, że wolę Spotify do muzyki.")
        self.assertEqual(info.family.value, "PREFERENCE")

    def test_opinion_not_preference(self):
        info = classify_intent_family("Spotify jest fajne.")
        self.assertNotEqual(info.family.value, "PREFERENCE")


class HardwareRegressionIntegrationTests(unittest.TestCase):

    def setUp(self):
        self.log = []
        self.registry = _registry(self.log)
        reset_registry_for_tests(self.registry)
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = MemoryManager(path=Path(self.tmp.name) / "memory.json")
        self.model = FakeModelClient([
            {"type": "tool_call", "tool": "set_preference", "arguments": {"key": "music.default_app", "value": "Spotify"}},
            {"type": "clarification", "question": "Jaką aplikację chcesz otworzyć?"},
            {"type": "tool_call", "tool": "open_app", "arguments": {}},
        ])
        self.manager = ConversationManager(
            registry=self.registry,
            memory=[],
            model=self.model,
            memory_manager=self.mem,
        )
        self.manager.activate()

    def tearDown(self):
        self.tmp.cleanup()
        reset_registry_for_tests(None)
        reset_model_client()

    def test_ciemno_zero_open_app(self):
        with patch("core.conversation_manager.run_fast_path", return_value=None), patch(
            "core.conversation_manager.run_skills", return_value=None
        ), patch("core.conversation_manager.run_command", return_value=None), patch(
            "core.conversation_manager.run_intent", return_value=None
        ):
            result = self.manager.handle("Trochę tu ciemno.")
        self.assertEqual(
            sum(1 for item in self.log if item["tool"] == "open_app"),
            0,
        )
        self.assertNotIn("aplikację", (result.text or "").lower())
        self.assertTrue(
            result.source in ("clarify", "agent", "deterministic", "action_loop")
            or "światło" in (result.text or "").lower()
            or "swiatlo" in (result.text or "").lower()
            or "rozjaśn" in (result.text or "").lower()
            or "rozjasn" in (result.text or "").lower()
        )

    def test_correction_zero_tools(self):
        result = self.manager.handle("Nie, nie o to mi chodziło.")
        self.assertEqual(result.source, "correction")
        self.assertEqual(self.log, [])
        self.assertIn("nie", prepare_user_text("Nie, nie o to mi chodziło.").semantic_text.lower())

    def test_pending_plus_correction_rejects(self):
        self.manager.pending_clarification = {
            "goal": "puść muzykę",
            "question": "Którą aplikację?",
            "started_at": 0,
        }
        result = self.manager.handle("Nie, nie o to mi chodziło.")
        self.assertIsNone(self.manager.pending_clarification)
        self.assertEqual(
            sum(1 for item in self.log if item["tool"] == "set_preference"),
            0,
        )
        self.assertEqual(result.source, "correction")

    def test_a_puscisz_media_no_preference(self):
        self.model = FakeModelClient([
            {"type": "tool_call", "tool": "set_preference", "arguments": {"key": "music.default_app", "value": "Spotify"}},
            {"type": "tool_call", "tool": "set_preference", "arguments": {"key": "music.default_app", "value": "Spotify"}},
        ])
        self.manager.model = self.model
        with patch("core.conversation_manager.run_fast_path", return_value=None), patch(
            "core.conversation_manager.run_skills", return_value=None
        ), patch("core.conversation_manager.run_command", return_value=None), patch(
            "core.conversation_manager.run_intent", return_value=None
        ):
            result = self.manager.handle("A puścisz Michael'a Jacksona?")
        self.assertEqual(
            sum(1 for item in self.log if item["tool"] == "set_preference"),
            0,
        )
        plays = [item for item in self.log if item["tool"] == "play_media"]
        self.assertEqual(len(plays), 1)
        self.assertIn("michael", str(plays[0]["arguments"]).lower())
        self.assertNotEqual(result.source, "clarify")

    def test_memory_pref_play_no_set_preference(self):
        self.mem.set_preference("music.default_app", "Spotify", explicit=True, source="test")
        self.model = FakeModelClient([
            {"type": "tool_call", "tool": "set_preference", "arguments": {"key": "music.default_app", "value": "Spotify"}},
        ])
        self.manager.model = self.model
        with patch("core.conversation_manager.run_fast_path", return_value=None), patch(
            "core.conversation_manager.run_skills", return_value=None
        ), patch("core.conversation_manager.run_command", return_value=None), patch(
            "core.conversation_manager.run_intent", return_value=None
        ):
            self.manager.handle("Puść Michaela Jacksona.")
        self.assertEqual(
            sum(1 for item in self.log if item["tool"] == "set_preference"),
            0,
        )
        plays = [item for item in self.log if item["tool"] == "play_media"]
        self.assertEqual(len(plays), 1)
        self.assertEqual(plays[0]["arguments"].get("app"), "Spotify")

    def test_unknown_with_pref_no_mutation(self):
        self.mem.set_preference("music.default_app", "Spotify", explicit=True, source="test")
        self.model = FakeModelClient([
            {"type": "tool_call", "tool": "set_preference", "arguments": {"key": "music.default_app", "value": "Spotify"}},
            {"type": "tool_call", "tool": "set_preference", "arguments": {"key": "music.default_app", "value": "Spotify"}},
        ])
        planner = Planner(self.registry, model=self.model)
        result = planner.run(
            "asdf qwer",
            context={
                "preferences": {"music.default_app": "Spotify"},
                "intent_classification": classify_intent_family("asdf qwer"),
            },
        )
        self.assertEqual(
            sum(1 for item in self.log if item["tool"] == "set_preference"),
            0,
        )
        self.assertFalse(result.needs_clarification and "aplikac" in (result.message or "").lower())

    def test_explicit_preference_allowed(self):
        with patch("core.conversation_manager.run_fast_path", return_value=None), patch(
            "core.conversation_manager.run_skills", return_value=None
        ), patch("core.conversation_manager.run_command", return_value=None), patch(
            "core.conversation_manager.run_intent", return_value=None
        ):
            self.manager.handle("Zapamiętaj, że wolę Spotify do muzyki.")
        prefs = [item for item in self.log if item["tool"] == "set_preference"]
        saved = self.mem.get_preference("music.default_app")
        self.assertTrue(len(prefs) == 1 or saved == "Spotify")
        if prefs:
            self.assertEqual(prefs[0]["arguments"].get("value"), "Spotify")

    def test_spotify_fajne_zero_mutation(self):
        self.model = FakeModelClient([
            {"type": "tool_call", "tool": "set_preference", "arguments": {"key": "music.default_app", "value": "Spotify"}},
        ])
        self.manager.model = self.model
        with patch("core.conversation_manager.run_fast_path", return_value=None), patch(
            "core.conversation_manager.run_skills", return_value=None
        ), patch("core.conversation_manager.run_command", return_value=None), patch(
            "core.conversation_manager.run_intent", return_value=None
        ), patch("core.conversation_manager.ask_ai", return_value="Tak, Spotify jest popularne."):
            self.manager.handle("Spotify jest fajne.")
        self.assertEqual(
            sum(1 for item in self.log if item["tool"] == "set_preference"),
            0,
        )

    def test_repeat_identical_success_blocked(self):
        model = FakeModelClient([
            {"type": "tool_call", "tool": "set_preference", "arguments": {"key": "music.default_app", "value": "Spotify"}},
            {"type": "tool_call", "tool": "set_preference", "arguments": {"key": "music.default_app", "value": "Spotify"}},
            {"type": "tool_call", "tool": "set_preference", "arguments": {"key": "music.default_app", "value": "Spotify"}},
        ])
        classification = classify_intent_family("Zapamiętaj, że wolę Spotify do muzyki.")
        result = Planner(self.registry, model=model).run(
            "Zapamiętaj, że wolę Spotify do muzyki.",
            context={"intent_classification": classification},
        )
        self.assertEqual(
            sum(1 for item in self.log if item["tool"] == "set_preference"),
            1,
        )
        self.assertIn(result.source, ("deterministic", "repeat_blocked", "planner"))


if __name__ == "__main__":
    unittest.main()
