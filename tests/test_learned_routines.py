# ==========================
# ELIAN — learned routines / teach phrase
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.builtin_tools import build_default_registry
from core.conversation_manager import ConversationManager
from core.fast_path import FastPathStatus, evaluate_fast_path, run_fast_path
from core.learned_routines import (
    lookup_learned_routine,
    looks_teach_phrase,
    looks_unclassified_request,
    note_learned_routine,
    parse_teach_phrase,
    reset_routines_for_tests,
)
from core.model_client import ModelClient, reset_model_client
from core.semantic_executive import decide_semantics, fast_path_permitted
from core.tool_result import ToolResult
from core.tools import reset_registry_for_tests
from memory.manager import MemoryManager
from memory.vocabulary import apply_aliases, remember_phrase


class BoomClient(ModelClient):
    def chat(self, messages, *, temperature=0.2, format=None, model=None) -> str:
        raise AssertionError("planner LLM must not run for learned-routine replay")


def _cm(registry=None):
    mem = MemoryManager(Path(tempfile.mkdtemp()) / "m.json")
    cm = ConversationManager(
        registry=registry or build_default_registry(),
        model=BoomClient(),
        memory_manager=mem,
    )
    cm.activate()
    return cm


class LearnedRoutineTests(unittest.TestCase):
    def setUp(self):
        reset_routines_for_tests([])
        reset_registry_for_tests()
        reset_model_client()

    def tearDown(self):
        reset_routines_for_tests([])
        reset_registry_for_tests(None)
        reset_model_client()

    def test_unclassified_request_goes_to_agent_not_smalltalk(self):
        self.assertTrue(looks_unclassified_request("zrób mi to samo co rano"))
        env = decide_semantics("zrób mi to samo co rano", allow_llm=False)
        self.assertEqual(env.intent, "unclassified_request")
        self.assertTrue(env.needs_tools)
        self.assertFalse(fast_path_permitted("zrób mi to samo co rano", env))
        small = decide_semantics("cześć", allow_llm=False)
        self.assertEqual(small.intent, "social_smalltalk")
        chat = decide_semantics("Fajny masz głos.", allow_llm=False)
        self.assertEqual(chat.intent, "general_conversation")

    def test_teach_phrase_and_alias(self):
        self.assertTrue(looks_teach_phrase("Jak powiem szot to otwórz Spotify"))
        src, dest = parse_teach_phrase("Jak powiem szot to otwórz Spotify")
        self.assertEqual(src, "szot")
        self.assertIn("otwórz", dest.lower())
        self.assertIn("spotify", dest.lower())
        vocab_path = Path(tempfile.mkdtemp()) / "vocab.json"
        self.assertTrue(remember_phrase("szot", "otwórz Spotify", path=vocab_path))
        expanded = apply_aliases("szot", vocab={"phrases": [{"from": "szot", "to": "otwórz Spotify"}]})
        self.assertIn("Spotify", expanded)

    def test_learned_replay_without_llm(self):
        note_learned_routine(
            "odpal mi szota",
            "open_app",
            {"name": "Spotify"},
            intent="open_app",
        )
        env = decide_semantics("Odpal mi szota.", allow_llm=False)
        self.assertEqual(env.intent, "learned_routine")
        self.assertTrue(fast_path_permitted("Odpal mi szota.", env))
        self.assertEqual(
            evaluate_fast_path("Odpal mi szota.", env).status,
            FastPathStatus.HANDLED_COMPLETE,
        )
        registry = build_default_registry()
        with patch.object(
            registry,
            "invoke",
            return_value=ToolResult.ok("Uruchomiłem Spotify.", tool_name="open_app"),
        ) as invoke:
            result = run_fast_path("Odpal mi szota.", registry, envelope=env)
        self.assertEqual(invoke.call_args[0][0], "open_app")
        self.assertEqual(invoke.call_args[0][1], {"name": "Spotify"})
        self.assertIn("Spotify", result.message)

    def test_handle_teach_then_replay(self):
        note_learned_routine("otwórz spotify", "open_app", {"name": "Spotify"})
        registry = build_default_registry()
        reset_registry_for_tests(registry)
        cm = _cm(registry)
        with patch("memory.vocabulary.remember_phrase", return_value=True):
            taught = cm.handle("Jak powiem szot to otwórz Spotify")
        self.assertEqual(taught.source, "teach")
        self.assertIn("szot", taught.text.lower())
        self.assertIsNotNone(lookup_learned_routine("szot"))
        with patch.object(
            registry,
            "invoke",
            return_value=ToolResult.ok("Uruchomiłem Spotify.", tool_name="open_app"),
        ):
            replay = cm.handle("szot")
        self.assertEqual(replay.source, "fast_path")
        self.assertNotIn("Chwila", replay.text)
        self.assertNotIn("Nie znam narzędzia", replay.text)

    def test_czesc_unchanged(self):
        registry = build_default_registry()
        reset_registry_for_tests(registry)
        cm = _cm(registry)
        result = cm.handle("cześć")
        self.assertNotEqual(result.source, "fast_path")
        self.assertNotIn("Chwila", result.text)


if __name__ == "__main__":
    unittest.main()
