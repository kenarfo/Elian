# ==========================
# ELIAN — "powiedz X" is SAY_TEXT, not a greeting
# ==========================
from __future__ import annotations

import unittest

from core.action_firewall import MUTATION_TOOLS, allow_tool
from core.conversation_manager import ConversationManager
from core.model_client import ModelClient, reset_model_client
from core.risk import RiskLevel
from core.semantic_executive import decide_semantics, extract_say_payload, fast_path_permitted
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests


class BoomClient(ModelClient):
    def chat(self, messages, *, temperature=0.2, format=None, model=None) -> str:
        raise AssertionError("Ollama planner loop must not run for say-text")


def _registry(invocations):
    registry = ToolRegistry(confirm_medium=False)

    def open_app(args):
        invocations.append(("open_app", dict(args or {})))
        return ToolResult.ok("Uruchomiłem notatnik.", tool_name="open_app")

    def type_text(args):
        invocations.append(("type_text", dict(args or {})))
        return ToolResult.ok("Wpisane.", tool_name="type_text")

    for name, handler in (("open_app", open_app), ("type_text", type_text)):
        registry.register(
            Tool(
                name=name,
                description=name,
                handler=handler,
                risk_level=RiskLevel.SAFE,
                parameters={"type": "object", "properties": {}},
            )
        )
    return registry


class SayTextSemanticTests(unittest.TestCase):
    def setUp(self):
        self.invocations = []
        self.registry = _registry(self.invocations)
        reset_registry_for_tests(self.registry)
        self.manager = ConversationManager(
            registry=self.registry,
            memory=[],
            model=BoomClient(),
        )
        self.manager.activate()

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()

    def _say(self, utterance: str, expected: str) -> None:
        env = decide_semantics(utterance, allow_llm=False)
        self.assertEqual(env.intent, "say_text", msg=utterance)
        self.assertNotEqual(env.intent, "social_smalltalk")
        self.assertFalse(env.needs_tools)
        self.assertTrue(env.payload)
        self.assertFalse(fast_path_permitted(utterance, env))
        for tool in MUTATION_TOOLS:
            allowed, _ = allow_tool(env, tool, utterance)
            self.assertFalse(allowed, msg=tool)
        result = self.manager.handle(utterance)
        self.assertTrue(result.should_speak)
        self.assertEqual(result.text.strip(), expected, msg=utterance)
        self.assertEqual(self.invocations, [])

    def test_powiedz_czesc(self):
        self._say("powiedz cześć", "Cześć.")

    def test_powiedz_hej(self):
        self._say("powiedz hej", "Hej.")

    def test_powiedz_dzien_dobry(self):
        self._say("powiedz dzień dobry", "Dzień dobry.")

    def test_powiedz_kot(self):
        self._say("powiedz kot", "Kot.")

    def test_powiedz_ala_ma_kota(self):
        self._say("powiedz Ala ma kota", "Ala ma kota.")

    def test_mozesz_powiedziec_czesc(self):
        self._say("możesz powiedzieć cześć", "Cześć.")

    def test_powiesz_czesc(self):
        self._say("powiesz cześć", "Cześć.")

    def test_powiedz_prosze_czesc(self):
        self._say("powiedz proszę cześć", "Cześć.")

    def test_powiedz_tylko_czesc(self):
        self._say("powiedz tylko cześć", "Cześć.")

    def test_powiedz_teraz_dzien_dobry(self):
        self._say("powiedz teraz dzień dobry", "Dzień dobry.")

    def test_powiedz_mi_czesc(self):
        self._say("powiedz mi cześć", "Cześć.")

    def test_chce_zebys_powiedzial_czesc(self):
        self._say("chcę żebyś powiedział cześć", "Cześć.")

    def test_genuine_czesc_is_smalltalk(self):
        env = decide_semantics("cześć", allow_llm=False)
        self.assertEqual(env.intent, "social_smalltalk")
        self.assertFalse(extract_say_payload("cześć"))
        result = self.manager.handle("cześć")
        self.assertTrue(result.should_speak)
        self.assertEqual(result.text.strip(), "Cześć.")
        self.assertEqual(self.invocations, [])

    def test_genuine_hej_is_smalltalk(self):
        env = decide_semantics("hej", allow_llm=False)
        self.assertEqual(env.intent, "social_smalltalk")
        result = self.manager.handle("hej")
        self.assertEqual(result.text.strip(), "Hej.")

    def test_genuine_jak_tam(self):
        env = decide_semantics("jak tam", allow_llm=False)
        self.assertEqual(env.intent, "social_smalltalk")
        result = self.manager.handle("jak tam")
        self.assertNotEqual(result.text.strip(), "Cześć.")
        self.assertTrue(result.should_speak)

    def test_genuine_co_slychac(self):
        env = decide_semantics("co słychać", allow_llm=False)
        self.assertEqual(env.intent, "social_smalltalk")
        result = self.manager.handle("co słychać")
        self.assertTrue(result.should_speak)
        self.assertNotEqual(result.text.strip(), "Cześć.")

    def test_negation_nie_mow_czesc(self):
        env = decide_semantics("nie mów cześć", allow_llm=False)
        self.assertNotEqual(env.intent, "say_text")
        self.assertFalse(extract_say_payload("nie mów cześć"))
        result = self.manager.handle("nie mów cześć")
        self.assertNotEqual(result.text.strip(), "Cześć.")
        self.assertEqual(self.invocations, [])

    def test_negation_nie_powiedz_czesc(self):
        env = decide_semantics("nie powiedz cześć", allow_llm=False)
        self.assertNotEqual(env.intent, "say_text")
        result = self.manager.handle("nie powiedz cześć")
        self.assertNotEqual(result.text.strip(), "Cześć.")
        self.assertEqual(self.invocations, [])

    def test_hypothetical_zero_say(self):
        env = decide_semantics("co by było gdybym powiedział cześć", allow_llm=False)
        self.assertNotEqual(env.intent, "say_text")
        self.assertFalse(env.needs_tools)
        result = self.manager.handle("co by było gdybym powiedział cześć")
        self.assertNotEqual(result.text.strip(), "Cześć.")
        self.assertEqual(self.invocations, [])

    def test_quoted_zero_execution(self):
        env = decide_semantics('powiedziałem mu "powiedz cześć"', allow_llm=False)
        self.assertNotEqual(env.intent, "say_text")
        result = self.manager.handle('powiedziałem mu "powiedz cześć"')
        self.assertNotEqual(result.text.strip(), "Cześć.")
        self.assertEqual(self.invocations, [])

    def test_how_to_say_is_knowledge_not_say_text(self):
        env = decide_semantics("jak powiedzieć cześć po angielsku", allow_llm=False)
        self.assertNotEqual(env.intent, "say_text")
        self.assertIn(env.mode, ("ANSWER", "TALK"))
        self.assertFalse(extract_say_payload("jak powiedzieć cześć po angielsku"))

    def test_after_notepad_does_not_reuse_target(self):
        first = self.manager.handle("otwórz notatnik")
        self.assertTrue(first.should_speak)
        self.assertEqual(self.invocations[0][0], "open_app")
        before = list(self.invocations)
        env = decide_semantics("powiedz cześć", allow_llm=False)
        self.assertEqual(env.intent, "say_text")
        result = self.manager.handle("powiedz cześć")
        self.assertEqual(result.text.strip(), "Cześć.")
        self.assertEqual(self.invocations, before)
