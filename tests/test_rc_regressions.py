"""Phase 10 RC regression tests — reproduce real hardware campaign bugs."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from CommandParser import parse_commands
from ConversationContext import clear as clear_ha_context, update as update_ha_context
from WakeWord import remove_wake_word
from core.clarification_policy import (
    FollowUpKind,
    classify_followup,
    detect_intent_category,
    is_inappropriate_open_app_clarification,
    sanitize_clarification_question,
)
from core.conversation_manager import ConversationManager
from core.fast_path import FastPathStatus, evaluate_fast_path, run_fast_path
from core.model_client import FakeModelClient, reset_model_client
from core.planner import Planner
from core.text_pipeline import ensure_semantic_text, prepare_user_text
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from core.risk import RiskLevel
from core.tool_result import ToolResult
from memory.manager import MemoryManager
from voice.pipeline import VoicePipeline
from voice.settings import VoiceSettings


def _registry():
    registry = ToolRegistry(confirm_medium=False)

    def open_app(args):
        return ToolResult.ok(
            f"Uruchomiłem {args.get('name')}.",
            tool_name="open_app",
            data={"app": args.get("name")},
        )

    def volume_up(_args):
        return ToolResult.ok("Głośniej.", tool_name="volume_up")

    def create_reminder(args):
        return ToolResult.ok(
            "Przypomnienie ustawione.",
            tool_name="create_reminder",
            data=args,
        )

    def describe_screen(_args):
        return ToolResult.ok("Na ekranie widać…", tool_name="describe_screen")

    def web_search(args):
        return ToolResult.ok(
            f"Szukam: {args.get('query')}",
            tool_name="web_search",
        )

    for name, handler, desc in (
        ("open_app", open_app, "Open app"),
        ("volume_up", volume_up, "Volume up"),
        ("create_reminder", create_reminder, "Reminder"),
        ("describe_screen", describe_screen, "Vision"),
        ("web_search", web_search, "Web search"),
    ):
        registry.register(Tool(
            name=name,
            description=desc,
            handler=handler,
            risk_level=RiskLevel.SAFE,
            parameters={"type": "object", "properties": {}},
        ))
    return registry


class ClarificationPolicyTests(unittest.TestCase):

    def test_knowledge_question_not_open_app_clarification(self):
        bad = 'Czy chcesz otworzyć aplikację "YouTube"?'
        goal = "Co by się stało, gdybym wyłączył komputer?"
        self.assertTrue(is_inappropriate_open_app_clarification(goal, bad))

    def test_casual_talk_not_open_app_clarification(self):
        from core.clarification_policy import strip_open_app_upsell

        bad = "Nie mam co powiedzieć. Czy chcesz otworzyć aplikację?"
        goal = "nie mam"
        self.assertTrue(is_inappropriate_open_app_clarification(goal, bad))
        cleaned = strip_open_app_upsell(bad, goal=goal)
        self.assertNotIn("otworzyć aplikację", cleaned.lower())
        self.assertTrue(cleaned)

    def test_open_app_goal_allows_open_app_clarification(self):
        q = "Czy chcesz otworzyć aplikację?"
        self.assertFalse(is_inappropriate_open_app_clarification("otwórz discord", q))

    def test_reminder_intent_category(self):
        self.assertEqual(
            detect_intent_category("Przypomnij mi za dwie minuty o treningu"),
            "reminder",
        )

    def test_vision_intent_category(self):
        self.assertEqual(detect_intent_category("Co mam na ekranie?"), "vision")

    def test_sanitize_tool_dump_clarification(self):
        dump = (
            "Available tools: open_app, volume_up, create_reminder with parameters "
            "tool_call schema capabilities ToolRegistry"
        )
        q = sanitize_clarification_question(dump, goal="Przypomnij mi coś")
        self.assertNotIn("ToolRegistry", q)
        self.assertLess(len(q), 120)

    def test_new_intent_during_pending_clarification(self):
        pending = {"goal": "Wyjaśnij czym jest sztuczna inteligencja"}
        kind = classify_followup("Co mam na ekranie?", pending)
        self.assertEqual(kind.kind, FollowUpKind.NEW_INTENT)

    def test_cancel_clears_via_niewazne(self):
        pending = {"goal": "Otwórz coś"}
        kind = classify_followup("nieważne", pending)
        self.assertEqual(kind.kind, FollowUpKind.CANCEL)


class PlannerRegressionTests(unittest.TestCase):

    def setUp(self):
        self.registry = _registry()
        reset_registry_for_tests(self.registry)

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()

    def test_knowledge_bad_clarification_repaired_to_final(self):
        model = FakeModelClient([
            {
                "type": "clarification",
                "question": 'Czy chcesz otworzyć aplikację "YouTube"?',
            },
        ])
        planner = Planner(self.registry, model=model)
        result = planner.run(
            "Co by się stało, gdybym wyłączył komputer?",
            context={"intent_hint": "explain"},
        )
        self.assertFalse(result.needs_clarification)
        self.assertNotIn("aplikac", result.message.lower())

    def test_reminder_tool_not_open_app_clarification(self):
        model = FakeModelClient([
            {
                "type": "tool_call",
                "tool": "create_reminder",
                "arguments": {"text": "sprawdź Eliana", "when": "za 2 minuty"},
            },
            {"type": "final_answer", "message": "Ustawione."},
        ])
        planner = Planner(self.registry, model=model)
        result = planner.run("Przypomnij mi za dwie minuty, żebym sprawdził Eliana.")
        self.assertFalse(result.needs_clarification)
        self.assertTrue(result.steps)
        self.assertEqual(result.steps[0]["tool"], "create_reminder")


class FastPathRegressionTests(unittest.TestCase):

    def setUp(self):
        self.registry = _registry()
        reset_registry_for_tests(self.registry)
        self.invocations = []

        original = self.registry.invoke

        def tracking(name, args, confirmed=False):
            self.invocations.append((name, args))
            return original(name, args, confirmed=confirmed)

        self.registry.invoke = tracking  # type: ignore[method-assign]

    def tearDown(self):
        reset_registry_for_tests(None)

    def test_multi_intent_partial_does_not_execute(self):
        text = "Otwórz Discord i poszukaj pogodę w internecie."
        evaluation = evaluate_fast_path(text)
        self.assertIn(
            evaluation.status,
            (FastPathStatus.PARTIAL_MATCH, FastPathStatus.NOT_HANDLED),
        )
        outcome = run_fast_path(text, self.registry)
        self.assertIsNone(outcome)
        self.assertEqual(self.invocations, [])

    def test_multi_intent_open_and_volume_goes_to_agent_not_partial_only_volume(self):
        evaluation = evaluate_fast_path("Otwórz Spotify i zwiększ głośność.")
        # Cross-domain APP+SYSTEM belongs to Agent/Planner (planner_steps >= 2).
        self.assertIn(
            evaluation.status,
            (FastPathStatus.NOT_HANDLED, FastPathStatus.PARTIAL_MATCH),
        )
        self.assertIsNone(
            run_fast_path("Otwórz Spotify i zwiększ głośność.", self.registry)
        )
        self.assertEqual(self.invocations, [])


class ConversationManagerRegressionTests(unittest.TestCase):

    def setUp(self):
        self.registry = _registry()
        reset_registry_for_tests(self.registry)
        self.model = FakeModelClient([])
        self.manager = ConversationManager(
            registry=self.registry,
            memory=[],
            model=self.model,
        )
        self.manager.activate()

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()

    def test_pending_clarification_new_intent_not_merged(self):
        self.model.push(
            {"type": "clarification", "question": "Którą aplikację mam otworzyć?"},
        )
        first = self.manager.handle("odpal grę")
        self.assertEqual(first.source, "clarify")
        self.assertIsNotNone(self.manager.pending_clarification)

        self.model.push(
            {"type": "tool_call", "tool": "describe_screen", "arguments": {}},
            {"type": "final_answer", "message": "Widzę Notatnik."},
        )
        second = self.manager.handle("Co mam na ekranie?")
        self.assertEqual(second.source, "agent")
        merged_goal = self.model.calls[-1]["messages"][-1]["content"]
        self.assertNotIn("Użytkownik uzupełnił", merged_goal)

    def test_stop_clears_pending_clarification(self):
        self.model.push(
            {"type": "clarification", "question": "Którą aplikację?"},
        )
        self.manager.handle("Otwórz coś")
        self.assertIsNotNone(self.manager.pending_clarification)
        self.manager.handle("stop")
        self.assertIsNone(self.manager.pending_clarification)

    def test_niewazne_clears_pending(self):
        self.model.push(
            {"type": "clarification", "question": "Którą aplikację?"},
        )
        self.manager.handle("Otwórz coś")
        result = self.manager.handle("nieważne")
        self.assertEqual(result.source, "cancel")
        self.assertIsNone(self.manager.pending_clarification)


class ContextSafetyTests(unittest.TestCase):

    def setUp(self):
        clear_ha_context()
        update_ha_context(
            last_device="light.vizzini",
            last_device_name="Inspire Vizzini D56",
            last_domain="light",
            last_light="light.vizzini",
            last_light_name="Inspire Vizzini D56",
        )

    def tearDown(self):
        clear_ha_context()

    def test_light_followup_brightness_not_climate(self):
        commands = parse_commands("ustaw na 50")
        self.assertTrue(commands)
        cmd = commands[0]
        self.assertEqual(cmd.domain, "light")
        self.assertEqual(cmd.action, "light_brightness")
        self.assertEqual(cmd.value, 50)


class WakeStripTests(unittest.TestCase):

    def test_polish_wake_prefix(self):
        result = remove_wake_word("Hej, Elian. Puść muzykę.")
        self.assertIn("puść muzykę", result.lower())
        self.assertNotIn("elian", result.lower())

    def test_english_wake_comma(self):
        self.assertEqual(
            remove_wake_word("Hey Elian, play"),
            "play",
        )

    def test_ok_elian_volume(self):
        self.assertEqual(
            remove_wake_word("OK Elian zwiększ głośność"),
            "zwiększ głośność",
        )


class TextPipelineTests(unittest.TestCase):

    def test_calendar_preprocessing_preserves_verb(self):
        raw = "utwórz wydarzenie z tytułem jedzenie"
        broken = "z tytułem jedzenie"
        fixed = ensure_semantic_text(raw, broken)
        self.assertIn("utwórz", fixed.lower())
        self.assertIn("jedzenie", fixed.lower())

    def test_prepare_user_text_keeps_semantic(self):
        prepared = prepare_user_text("Przypomnij mi jutro o spotkaniu")
        self.assertIn("przypomnij", prepared.semantic_text.lower())


class BargeInStressTests(unittest.TestCase):

    def test_repeated_barge_stop_does_not_close_pipeline(self):
        pipeline = VoicePipeline(
            settings=VoiceSettings(
                tts_enabled=False,
                barge_in_enabled=True,
                sample_rate=16000,
                block_size=512,
            ),
            speak_fn=lambda text: time.sleep(0.01),
            stop_speak_fn=lambda: None,
        )
        pipeline.start()
        for _ in range(50):
            pipeline.speak("test", allow_barge=False)
            pipeline.stop_speaking()
            pipeline.clear_cancellation()
        self.assertFalse(pipeline._closed)
        pipeline.close()


class AgentClarificationPolishTests(unittest.TestCase):

    def test_clarification_stays_short_polish(self):
        from core.planner import _shorten_voice

        long_q = (
            "I can help with open_app, volume_up, describe_screen. "
            "Which application do you want?"
        )
        sanitized = sanitize_clarification_question(long_q, goal="Przypomnij mi")
        short = _shorten_voice(sanitized)
        self.assertLess(len(short), 120)
        self.assertNotIn("open_app", short)


if __name__ == "__main__":
    unittest.main()
