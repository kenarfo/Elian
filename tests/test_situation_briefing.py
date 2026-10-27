# ==========================
# ELIAN — on-demand Jarvis situation briefing
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.action_firewall import allow_tool
from core.builtin_tools import build_default_registry
from core.conversation_manager import ConversationManager
from core.fast_path import FastPathStatus, evaluate_fast_path, run_fast_path
from core.intent_family import classify_intent_family
from core.model_client import ModelClient, reset_model_client
from core.semantic_executive import decide_semantics, fast_path_permitted
from core.situation_briefing import compose_situation_briefing, looks_situation_briefing
from core.tool_result import ToolResult
from core.tool_shortlist import shortlist_tools
from core.tools import reset_registry_for_tests
from memory.manager import MemoryManager


class BoomClient(ModelClient):
    def chat(self, messages, *, temperature=0.2, format=None, model=None) -> str:
        raise AssertionError("planner LLM must not run for situation briefing")


def _cm(registry=None):
    mem = MemoryManager(Path(tempfile.mkdtemp()) / "m.json")
    cm = ConversationManager(
        registry=registry or build_default_registry(),
        model=BoomClient(),
        memory_manager=mem,
    )
    cm.activate()
    return cm


class SituationBriefingSemanticTests(unittest.TestCase):
    def test_phrases_are_answer_situation_briefing(self):
        for utterance in (
            "Podsumuj sytuację.",
            "Co mnie czeka?",
            "Jaki mam dzień?",
            "Wstałem.",
            "Zaczynamy dzień.",
        ):
            with self.subTest(utterance=utterance):
                self.assertTrue(looks_situation_briefing(utterance), msg=utterance)
                env = decide_semantics(utterance, allow_llm=False)
                self.assertEqual(env.mode, "ANSWER", msg=utterance)
                self.assertEqual(env.intent, "situation_briefing", msg=utterance)
                self.assertTrue(env.needs_tools, msg=utterance)
                self.assertIn("read_only", env.safety_notes)
                self.assertTrue(fast_path_permitted(utterance, env), msg=utterance)
                self.assertEqual(
                    evaluate_fast_path(utterance, env).status,
                    FastPathStatus.HANDLED_COMPLETE,
                    msg=utterance,
                )

    def test_smalltalk_not_hijacked(self):
        for utterance in ("co słychać", "Co słychać?", "cześć", "jak tam", "dzień dobry"):
            with self.subTest(utterance=utterance):
                self.assertFalse(looks_situation_briefing(utterance), msg=utterance)
                env = decide_semantics(utterance, allow_llm=False)
                self.assertEqual(env.intent, "social_smalltalk", msg=utterance)
                self.assertFalse(env.needs_tools, msg=utterance)
                self.assertFalse(fast_path_permitted(utterance, env), msg=utterance)


class SituationBriefingComposerTests(unittest.TestCase):
    def test_composes_nonempty_slices_and_skips_empty(self):
        with patch(
            "core.situation_briefing._slice_calendar",
            return_value="Następne: trening o 17.",
        ), patch("core.situation_briefing._slice_reminders", return_value=None), patch(
            "core.situation_briefing._slice_home",
            return_value="W domu włączone: lampa.",
        ), patch(
            "core.situation_briefing._slice_weather",
            return_value="Pogoda w Łodzi: 18 stopni, zachmurzenie.",
        ), patch(
            "core.situation_briefing._slice_system",
            return_value="Na PC spokojnie.",
        ):
            result = compose_situation_briefing()
        self.assertTrue(result.success)
        self.assertEqual(result.tool_name, "get_situation_briefing")
        self.assertIn("trening", result.message)
        self.assertIn("lampa", result.message)
        self.assertIn("18 stopni", result.message)
        self.assertIn("Na PC spokojnie", result.message)
        self.assertLessEqual(len(result.message), 200)

    def test_all_sources_fail_is_honest_empty(self):
        with patch("core.situation_briefing._slice_calendar", return_value=None), patch(
            "core.situation_briefing._slice_reminders", return_value=None
        ), patch("core.situation_briefing._slice_home", return_value=None), patch(
            "core.situation_briefing._slice_weather", return_value=None
        ), patch(
            "core.situation_briefing._slice_system", return_value=None
        ):
            result = compose_situation_briefing()
        self.assertTrue(result.success)
        self.assertIn("nic konkretnego", result.message.lower())
        self.assertNotIn("wszystko OK", result.message)

    def test_failed_weather_omitted(self):
        with patch(
            "core.situation_briefing._slice_calendar",
            return_value="Następne: trening o 17.",
        ), patch("core.situation_briefing._slice_reminders", return_value=None), patch(
            "core.situation_briefing._slice_home", return_value=None
        ), patch(
            "Skills.Weather.get_weather",
            return_value="Nie udało mi się pobrać pogody.",
        ), patch(
            "core.situation_briefing._slice_system", return_value=None
        ):
            result = compose_situation_briefing()
        self.assertIn("trening", result.message)
        self.assertNotIn("Pogoda", result.message)
        self.assertNotIn("Nie udało", result.message)


class SituationBriefingRoutingTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()

    def test_firewall_allows_briefing_blocks_mutation(self):
        env = decide_semantics("Podsumuj sytuację.", allow_llm=False)
        allowed, reason = allow_tool(env, "get_situation_briefing", "Podsumuj sytuację.")
        self.assertTrue(allowed, msg=reason)
        blocked, _ = allow_tool(env, "turn_on_device", "Podsumuj sytuację.")
        self.assertFalse(blocked)

    def test_shortlist_contains_briefing_tool(self):
        catalog = [
            {"name": "get_situation_briefing", "available": True},
            {"name": "open_app", "available": True},
            {"name": "turn_on_device", "available": True},
        ]
        names = [
            item["name"]
            for item in shortlist_tools(
                catalog,
                goal="Podsumuj sytuację.",
                classification=classify_intent_family("Podsumuj sytuację."),
            )
        ]
        self.assertEqual(names, ["get_situation_briefing"])

    def test_fastpath_invokes_registered_tool(self):
        registry = build_default_registry()
        env = decide_semantics("Co mnie czeka?", allow_llm=False)
        spoken = (
            "Następne: trening o 17. W domu włączone: lampa. "
            "Pogoda w Łodzi: 18 stopni, zachmurzenie. Na PC spokojnie."
        )
        with patch(
            "core.situation_briefing.compose_situation_briefing",
            return_value=ToolResult.ok(spoken, tool_name="get_situation_briefing"),
        ):
            result = run_fast_path("Co mnie czeka?", registry, envelope=env)
        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        self.assertEqual(result.tool_name, "get_situation_briefing")
        self.assertIn("trening", result.message)

    def test_handle_speaks_briefing_not_unknown_tool(self):
        registry = build_default_registry()
        reset_registry_for_tests(registry)
        cm = _cm(registry)
        spoken = (
            "Następne: trening o 17. W domu włączone: lampa. "
            "Pogoda w Łodzi: 18 stopni, zachmurzenie. Na PC spokojnie."
        )
        with patch(
            "core.situation_briefing.compose_situation_briefing",
            return_value=ToolResult.ok(spoken, tool_name="get_situation_briefing"),
        ):
            result = cm.handle("Podsumuj sytuację.")
        self.assertTrue(result.should_speak)
        self.assertEqual(result.source, "fast_path")
        self.assertIn("trening", result.text)
        self.assertIn("lampa", result.text)
        self.assertNotIn("Chwila", result.text)
        self.assertNotIn("Nie znam narzędzia", result.text)

    def test_handle_wstalem_uses_briefing_not_procedure(self):
        registry = build_default_registry()
        reset_registry_for_tests(registry)
        cm = _cm(registry)
        with patch(
            "core.situation_briefing.compose_situation_briefing",
            return_value=ToolResult.ok(
                "Następne: trening o 17.",
                tool_name="get_situation_briefing",
            ),
        ):
            result = cm.handle("Wstałem.")
        self.assertEqual(result.source, "fast_path")
        self.assertIn("trening", result.text)
        self.assertNotIn("nie jest zaimplementowana", result.text.lower())

    def test_co_slychac_stays_smalltalk(self):
        registry = build_default_registry()
        reset_registry_for_tests(registry)
        invocations = []

        original = registry.invoke

        def wrapped(name, arguments=None, confirmed=False):
            invocations.append(name)
            return original(name, arguments, confirmed=confirmed)

        registry.invoke = wrapped
        cm = _cm(registry)
        result = cm.handle("co słychać")
        self.assertTrue(result.should_speak)
        self.assertNotEqual(result.source, "fast_path")
        self.assertNotIn("get_situation_briefing", invocations)
        self.assertNotIn("Nie znam narzędzia", result.text)
        self.assertNotIn("Chwila", result.text)
        self.assertIn("porządk", result.text.lower())


if __name__ == "__main__":
    unittest.main()
