# ==========================
# ELIAN — RC hear→respond→speak regression (post Phase 0–1 silence)
# ==========================
from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from core.conversation_manager import ConversationManager, HandleResult
from core.model_client import FakeModelClient, ModelClient, reset_model_client
from core.rc_trace import log_rc_trace
from core.rc_validation.collector import activate_collector, deactivate_collector
from core.rc_validation.status import RCStatusDisplay
from core.risk import RiskLevel
from core.semantic_executive import decide_semantics, fast_path_permitted
from core.semantic_talk import try_simple_math
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from voice.pipeline import VoicePipeline
from voice.settings import VoiceSettings
from voice.state import SpeechState


class BoomClient(ModelClient):
    """Any chat() means conversation fell through to the Ollama planner loop."""

    def chat(self, messages, *, temperature=0.2, format=None, model=None) -> str:
        raise AssertionError("planner LLM must not run for conversational RC turns")


def _registry(invocations):
    registry = ToolRegistry(confirm_medium=False)

    def open_app(args):
        invocations.append(("open_app", dict(args or {})))
        return ToolResult.ok(
            f"Uruchomiłem {args.get('name')}.",
            tool_name="open_app",
            data={"app": args.get("name")},
        )

    def type_text(args):
        invocations.append(("type_text", dict(args or {})))
        return ToolResult.ok("Wpisane.", tool_name="type_text", data=dict(args or {}))

    for name, handler, desc in (
        ("open_app", open_app, "Open app"),
        ("type_text", type_text, "Type text"),
    ):
        registry.register(
            Tool(
                name=name,
                description=desc,
                handler=handler,
                risk_level=RiskLevel.SAFE,
                parameters={"type": "object", "properties": {}},
            )
        )
    return registry


class RcResponseRegressionTests(unittest.TestCase):
    def setUp(self):
        self.invocations = []
        self.registry = _registry(self.invocations)
        reset_registry_for_tests(self.registry)
        self.model = BoomClient()
        self.manager = ConversationManager(
            registry=self.registry,
            memory=[],
            model=self.model,
        )
        self.manager.activate()

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()

    def _spoken(self, utterance: str) -> HandleResult:
        result = self.manager.handle(utterance)
        self.assertTrue(result.active)
        self.assertFalse(result.ended)
        self.assertTrue(result.should_speak, msg=f"silent source={result.source}")
        self.assertTrue(str(result.text or "").strip())
        self.assertNotEqual(result.text.strip().lower(), "nie wiem, co zrobić.")
        return result

    def test_greet_conversation_no_tool_tts_requested(self):
        env = decide_semantics("powiedz cześć", allow_llm=False)
        self.assertEqual(env.mode, "TALK")
        self.assertEqual(env.intent, "say_text")
        self.assertEqual(env.payload.lower(), "cześć")
        self.assertFalse(env.needs_tools)
        self.assertFalse(fast_path_permitted("powiedz cześć", env))
        result = self._spoken("Elian powiedz cześć")
        self.assertEqual(self.invocations, [])
        self.assertEqual(result.text.strip(), "Cześć.")
        self.assertTrue(result.should_speak)

    def test_knowledge_math_no_computer_no_ha(self):
        self.assertEqual(try_simple_math("ile to dwa plus dwa"), "Cztery.")

    def test_clock_reply_uses_system_time(self):
        from datetime import datetime

        from core.semantic_talk import try_clock_reply

        spoken = try_clock_reply("które jest godziną")
        now = datetime.now()
        self.assertTrue(spoken.startswith("Jest godzina "))
        self.assertIn(f"{now.hour}:{now.minute:02d}", spoken)
        self.assertNotEqual(spoken, "Teraz jest 14:30.")
        result = self._spoken("ile to dwa plus dwa")
        env = getattr(self.manager, "_decision_envelope", None)
        self.assertIsNotNone(env)
        self.assertFalse(env.needs_tools)
        self.assertEqual(self.invocations, [])
        lowered = result.text.lower()
        self.assertTrue("cztery" in lowered or "4" in lowered)

    def test_social_jak_tam_zero_tools(self):
        env = decide_semantics("jak tam", allow_llm=False)
        self.assertEqual(env.mode, "TALK")
        self.assertEqual(env.intent, "social_smalltalk")
        result = self._spoken("jak tam")
        self.assertEqual(self.invocations, [])
        self.assertTrue(result.should_speak)

    def test_action_open_notepad_mocked(self):
        result = self._spoken("otwórz notatnik")
        self.assertIn(result.source, ('fast_path', 'action_loop', 'agent'))
        self.assertEqual(self.invocations[0][0], "open_app")
        self.assertNotIn("computer", result.source.lower())

    def test_continuation_second_turn_still_speaks(self):
        first = self._spoken("otwórz notatnik")
        self.assertTrue(first.should_speak)
        second = self.manager.handle("i wpisz tam test")
        self.assertTrue(second.active)
        self.assertTrue(str(second.text or "").strip() or second.source in ("clarify", "agent", "fast_path"))
        self.assertNotEqual(getattr(second, "source", ""), "no_response")

    def test_correction_does_not_kill_pipeline(self):
        self._spoken("otwórz notatnik")
        result = self.manager.handle("nie jednak kalkulator")
        self.assertTrue(result.active)
        self.assertFalse(result.ended)

    def test_joke_after_action_zero_tools(self):
        self._spoken("otwórz notatnik")
        before = list(self.invocations)
        result = self._spoken("opowiedz mi żart")
        self.assertEqual(self.invocations, before)
        self.assertIn(result.source, ("agent", "talk", "social"))

    def test_negation_zero_action(self):
        before = list(self.invocations)
        result = self._spoken("nie otwieraj notatnika")
        self.assertEqual(self.invocations, before)
        self.assertIn(result.source, ("negation", "agent", "social"))

    def test_hypothetical_zero_action(self):
        before = list(self.invocations)
        result = self._spoken("co by było gdybym otworzył notatnik")
        self.assertEqual(self.invocations, before)
        env = getattr(self.manager, "_decision_envelope", None)
        self.assertFalse(bool(getattr(env, "needs_tools", True)))

    def test_quoted_command_zero_action(self):
        before = list(self.invocations)
        result = self._spoken('powiedziałem mu "otwórz notatnik"')
        self.assertEqual(self.invocations, before)
        self.assertTrue(result.should_speak)

    def test_empty_stt_does_not_crash(self):
        result = self.manager.handle("")
        self.assertIsNotNone(result)
        self.assertIn(result.source, ("idle_timeout", "empty", "empty_after_prep"))

    def test_five_simple_turns_stay_listening(self):
        phrases = (
            "powiedz cześć",
            "jak tam",
            "ile to dwa plus dwa",
            "co potrafisz",
            "opowiedz mi żart",
        )
        for phrase in phrases:
            result = self._spoken(phrase)
            self.assertTrue(result.active)
            self.assertFalse(result.ended)
            self.assertNotEqual(result.source, "no_response")
        self.assertEqual(self.invocations, [])


class RcLifecycleTests(unittest.TestCase):
    def setUp(self):
        import core.rc_validation.runner as rc_runner

        self.rc_runner = rc_runner
        self.invocations = []
        self.registry = _registry(self.invocations)
        reset_registry_for_tests(self.registry)
        self.manager = ConversationManager(
            registry=self.registry,
            memory=[],
            model=BoomClient(),
        )
        self.manager.activate()
        self.spoken = []
        self.pipeline = VoicePipeline(
            settings=VoiceSettings(tts_enabled=True, barge_in_enabled=False),
            wake_listener=None,
            listen_fn=lambda **kw: "",
            speak_fn=lambda text, **k: self.spoken.append(text),
            stop_speak_fn=lambda: None,
            is_speaking_fn=lambda: False,
            remove_wake_fn=lambda s: s,
            is_wake_fn=lambda s: False,
        )
        self.status = RCStatusDisplay(enabled=False)
        activate_collector(store_transcripts=False)
        rc_runner._RC_MANAGER = self.manager
        rc_runner._RC_PIPELINE = self.pipeline
        rc_runner._RC_STATUS = self.status

    def tearDown(self):
        import core.rc_validation.runner as rc_runner

        rc_runner._RC_MANAGER = None
        rc_runner._RC_PIPELINE = None
        rc_runner._RC_STATUS = None
        deactivate_collector()
        reset_registry_for_tests(None)
        reset_model_client()

    def test_listening_processing_speaking_listening(self):
        states = []
        self.pipeline.state.add_listener(lambda old, new: states.append(new))
        self.pipeline.state.set(SpeechState.ACTIVE_LISTENING)
        self.rc_runner._execute_canonical_turn("powiedz cześć")
        self.assertIn(SpeechState.PROCESSING, states)
        self.assertIn(SpeechState.SPEAKING, states)
        self.assertEqual(self.pipeline.state.state, SpeechState.ACTIVE_LISTENING)
        self.assertTrue(self.spoken)
        self.assertNotEqual(self.pipeline.state.state, SpeechState.SPEAKING)

    def test_tts_failure_visible_and_not_stuck_speaking(self):
        def boom(_text, **_k):
            raise RuntimeError("controlled TTS failure")

        self.pipeline._speak = boom
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            self.pipeline.state.set(SpeechState.ACTIVE_LISTENING)
            self.rc_runner._execute_canonical_turn("powiedz cześć")
        self.assertNotEqual(self.pipeline.state.state, SpeechState.SPEAKING)
        self.assertTrue(self.pipeline.state.is_active_session())
        self.assertIn("TTS error", buf.getvalue())

    def test_rc_trace_omits_transcript_when_privacy_on(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            log_rc_trace(heard="yes", stt_text_present="yes", route="agent")
            self.rc_runner._execute_canonical_turn("powiedz cześć")
        dumped = buf.getvalue()
        self.assertIn("[RC_TRACE]", dumped)
        self.assertNotIn("tajne", dumped)


class FastPathSafetyRegressionTests(unittest.TestCase):
    def test_talk_does_not_bypass_semantic_or_policy(self):
        env = decide_semantics("powiedz cześć", allow_llm=False)
        self.assertFalse(fast_path_permitted("powiedz cześć", env))
        env_j = decide_semantics("opowiedz mi żart", allow_llm=False)
        self.assertFalse(fast_path_permitted("opowiedz mi żart", env_j))
        env_q = decide_semantics('powiedziałem mu "otwórz notatnik"', allow_llm=False)
        self.assertFalse(fast_path_permitted('powiedziałem mu "otwórz notatnik"', env_q))
        env_h = decide_semantics("co by było gdybym otworzył notatnik", allow_llm=False)
        self.assertFalse(fast_path_permitted("co by było gdybym otworzył notatnik", env_h))
