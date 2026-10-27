# ==========================
# ELIAN — Jarvis home arrival („jestem w domu”)
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
from core.read_intents import looks_home_arrival, looks_home_overview
from core.semantic_executive import decide_semantics, fast_path_permitted
from core.session_digest import note_digest_line, peek_digest_lines, reset_digest_for_tests
from core.situation_briefing import compose_situation_briefing
from core.tool_result import ToolResult
from core.tool_shortlist import shortlist_tools
from core.tools import reset_registry_for_tests
from memory.manager import MemoryManager


class BoomClient(ModelClient):
    def chat(self, messages, *, temperature=0.2, format=None, model=None) -> str:
        raise AssertionError("planner LLM must not run for home arrival")


def _cm(registry=None):
    mem = MemoryManager(Path(tempfile.mkdtemp()) / "m.json")
    cm = ConversationManager(
        registry=registry or build_default_registry(),
        model=BoomClient(),
        memory_manager=mem,
    )
    cm.activate()
    return cm


class HomeArrivalSemanticTests(unittest.TestCase):
    def test_phrases_are_answer_home_arrival(self):
        for utterance in (
            "Już jestem w domu.",
            "Jestem w domu.",
            "Właśnie wróciłem.",
            "Wróciłem do domu.",
            "I'm home.",
            "I'm back.",
        ):
            with self.subTest(utterance=utterance):
                self.assertTrue(looks_home_arrival(utterance), msg=utterance)
                env = decide_semantics(utterance, allow_llm=False)
                self.assertEqual(env.mode, "ANSWER", msg=utterance)
                self.assertEqual(env.intent, "home_arrival", msg=utterance)
                self.assertTrue(env.needs_tools, msg=utterance)
                self.assertIn("read_only", env.safety_notes)
                self.assertTrue(fast_path_permitted(utterance, env), msg=utterance)
                self.assertEqual(
                    evaluate_fast_path(utterance, env).status,
                    FastPathStatus.HANDLED_COMPLETE,
                    msg=utterance,
                )

    def test_home_overview_and_smalltalk_not_stolen(self):
        self.assertFalse(looks_home_arrival("Co w domu?"))
        self.assertTrue(looks_home_overview("Co w domu?"))
        home = decide_semantics("Co w domu?", allow_llm=False)
        self.assertEqual(home.intent, "home_overview")
        for utterance in ("cześć", "dzień dobry"):
            with self.subTest(utterance=utterance):
                self.assertFalse(looks_home_arrival(utterance), msg=utterance)
                env = decide_semantics(utterance, allow_llm=False)
                self.assertEqual(env.intent, "social_smalltalk", msg=utterance)
                self.assertFalse(env.needs_tools, msg=utterance)
                self.assertFalse(fast_path_permitted(utterance, env), msg=utterance)


class HomeArrivalComposerTests(unittest.TestCase):
    def setUp(self):
        reset_digest_for_tests()

    def tearDown(self):
        reset_digest_for_tests()

    def test_arrival_prefix_briefing_digest_news(self):
        reset_digest_for_tests()
        note_digest_line("przypomnienie leki")
        with patch(
            "core.situation_briefing._slice_calendar",
            return_value="Następne: trening o 17.",
        ), patch("core.situation_briefing._slice_reminders", return_value=None), patch(
            "core.situation_briefing._slice_home", return_value=None
        ), patch(
            "core.situation_briefing._slice_now_playing", return_value=None
        ), patch(
            "core.situation_briefing._slice_weather", return_value=None
        ), patch(
            "core.situation_briefing._slice_system", return_value=None
        ), patch(
            "core.situation_briefing._slice_news",
            return_value="Wiadomość: Sejm przyjął ustawę.",
        ):
            result = compose_situation_briefing({"mode": "arrival"})
        self.assertTrue(result.success)
        self.assertTrue(result.message.startswith("Witaj w domu."))
        self.assertIn("trening", result.message)
        self.assertIn("Ominęło Cię", result.message)
        self.assertIn("leki", result.message)
        self.assertIn("Wiadomość", result.message)
        self.assertNotIn("Nic mi nie umknęło", result.message)
        self.assertNotIn("wszystko OK", result.message)
        self.assertLessEqual(len(result.message), 250)
        self.assertEqual(peek_digest_lines(), [])

    def test_empty_sources_are_just_welcome(self):
        reset_digest_for_tests()
        with patch("core.situation_briefing._slice_calendar", return_value=None), patch(
            "core.situation_briefing._slice_reminders", return_value=None
        ), patch("core.situation_briefing._slice_home", return_value=None), patch(
            "core.situation_briefing._slice_now_playing", return_value=None
        ), patch(
            "core.situation_briefing._slice_weather", return_value=None
        ), patch(
            "core.situation_briefing._slice_system", return_value=None
        ), patch(
            "core.situation_briefing._slice_news", return_value=None
        ):
            result = compose_situation_briefing({"mode": "arrival"})
        self.assertEqual(result.message, "Witaj w domu.")
        self.assertNotIn("Nic mi nie umknęło", result.message)
        self.assertNotIn("nic konkretnego", result.message.lower())


class HomeArrivalRoutingTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()

    def test_firewall_and_shortlist(self):
        env = decide_semantics("Już jestem w domu.", allow_llm=False)
        allowed, reason = allow_tool(env, "get_situation_briefing", "Już jestem w domu.")
        self.assertTrue(allowed, msg=reason)
        blocked, _ = allow_tool(env, "turn_on_device", "Już jestem w domu.")
        self.assertFalse(blocked)
        names = [
            item["name"]
            for item in shortlist_tools(
                [
                    {"name": "get_situation_briefing", "available": True},
                    {"name": "get_home_summary", "available": True},
                    {"name": "open_app", "available": True},
                ],
                goal="Już jestem w domu.",
                classification=classify_intent_family("Już jestem w domu."),
            )
        ]
        self.assertEqual(names, ["get_situation_briefing"])

    def test_fastpath_passes_arrival_mode(self):
        registry = build_default_registry()
        env = decide_semantics("Jestem w domu.", allow_llm=False)
        seen = []

        original = registry.invoke

        def wrapped(name, arguments=None, confirmed=False):
            seen.append((name, dict(arguments or {})))
            return original(name, arguments, confirmed=confirmed)

        registry.invoke = wrapped
        spoken = "Witaj w domu. Następne: trening o 17."
        with patch(
            "core.situation_briefing.compose_situation_briefing",
            return_value=ToolResult.ok(spoken, tool_name="get_situation_briefing"),
        ):
            result = run_fast_path("Jestem w domu.", registry, envelope=env)
        self.assertIsNotNone(result)
        self.assertEqual(seen[0][0], "get_situation_briefing")
        self.assertEqual(seen[0][1], {"mode": "arrival"})
        self.assertIn("Witaj w domu", result.message)

    def test_handle_arrival_not_unknown_tool(self):
        registry = build_default_registry()
        reset_registry_for_tests(registry)
        cm = _cm(registry)
        spoken = (
            "Witaj w domu. Następne: trening o 17. "
            "Ominęło Cię: przypomnienie leki. Wiadomość: Sejm przyjął ustawę."
        )
        with patch(
            "core.situation_briefing.compose_situation_briefing",
            return_value=ToolResult.ok(spoken, tool_name="get_situation_briefing"),
        ):
            result = cm.handle("Już jestem w domu.")
        self.assertTrue(result.should_speak)
        self.assertEqual(result.source, "fast_path")
        self.assertIn("Witaj w domu", result.text)
        self.assertIn("trening", result.text)
        self.assertIn("leki", result.text)
        self.assertNotIn("Chwila", result.text)
        self.assertNotIn("Nie znam narzędzia", result.text)

    def test_co_w_domu_still_home_overview(self):
        registry = build_default_registry()
        reset_registry_for_tests(registry)
        cm = _cm(registry)
        env = decide_semantics("Co w domu?", allow_llm=False)
        self.assertEqual(env.intent, "home_overview")
        with patch.object(
            registry,
            "invoke",
            return_value=ToolResult.ok(
                "W domu włączone: lampa.",
                tool_name="get_home_summary",
                data={"on": ["lampa"]},
            ),
        ) as invoke:
            result = cm.handle("Co w domu?")
        self.assertEqual(invoke.call_args[0][0], "get_home_summary")
        self.assertEqual(result.source, "fast_path")
        self.assertNotIn("Witaj w domu", result.text)
        self.assertNotIn("Chwila", result.text)

    def test_czesc_stays_smalltalk(self):
        registry = build_default_registry()
        reset_registry_for_tests(registry)
        invocations = []
        original = registry.invoke

        def wrapped(name, arguments=None, confirmed=False):
            invocations.append(name)
            return original(name, arguments, confirmed=confirmed)

        registry.invoke = wrapped
        cm = _cm(registry)
        result = cm.handle("cześć")
        self.assertTrue(result.should_speak)
        self.assertNotEqual(result.source, "fast_path")
        self.assertNotIn("get_situation_briefing", invocations)
        self.assertNotIn("Nie znam narzędzia", result.text)
        self.assertNotIn("Chwila", result.text)


if __name__ == "__main__":
    unittest.main()
