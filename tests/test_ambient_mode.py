# ==========================
# ELIAN — Ambient Mode + barge-in (offline)
# ==========================

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from core.conversation_manager import ConversationManager
from core.metrics import get_metrics, reset_metrics
from core.model_client import FakeModelClient, reset_model_client
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from core.utterance import extract_correction_target, is_bare_rejection
from voice.pipeline import VoicePipeline, looks_like_self_echo
from voice.settings import VoiceSettings
from voice.state import SpeechState, SpeechStateMachine
from voice.wake import FakeWakeListener


def _dry_registry(log=None):
    log = log if log is not None else []
    registry = ToolRegistry(confirm_medium=False)

    def volume_up(_args):
        log.append("volume_up")
        return ToolResult.ok("Głośniej.", tool_name="volume_up")

    def volume_down(_args):
        log.append("volume_down")
        return ToolResult.ok("Ciszej.", tool_name="volume_down")

    def turn_on_device(args):
        log.append(("turn_on_device", dict(args or {})))
        return ToolResult.ok("Włączam lampę.", tool_name="turn_on_device")

    for name, handler in (
        ("volume_up", volume_up),
        ("volume_down", volume_down),
        ("turn_on_device", turn_on_device),
    ):
        registry.register(
            Tool(
                name=name,
                description=name,
                handler=handler,
                risk_level=RiskLevel.SAFE,
                parameters={"type": "object", "properties": {}},
            )
        )
    return registry, log


def _pipeline(speak_fn=None, listen_fn=None, **kwargs):
    spoken = []

    def _speak(text, **_k):
        spoken.append(text)
        if speak_fn:
            speak_fn(text)

    pipeline = VoicePipeline(
        settings=VoiceSettings(
            tts_enabled=True,
            barge_in_enabled=False,
            echo_guard_ms=800,
            session_idle_timeout=30,
        ),
        wake_listener=FakeWakeListener(),
        listen_fn=listen_fn or (lambda **kw: ""),
        speak_fn=_speak,
        stop_speak_fn=lambda: None,
        is_speaking_fn=lambda: False,
        remove_wake_fn=lambda s: s,
        is_wake_fn=lambda s: False,
        **kwargs,
    )
    pipeline.start()
    return pipeline, spoken


class StateMachineAmbientTests(unittest.TestCase):
    def test_required_states_exist(self):
        names = {item.name for item in SpeechState}
        for required in (
            "IDLE",
            "WAKE_LISTENING",
            "ACTIVE_LISTENING",
            "PROCESSING",
            "ACTING",
            "SPEAKING",
            "INTERRUPTED",
            "PAUSED",
        ):
            self.assertIn(required, names)

    def test_transition_telemetry(self):
        reset_metrics()
        sm = SpeechStateMachine(SpeechState.IDLE)
        sm.transition(SpeechState.WAKE_LISTENING, reason="await_wake")
        sm.transition(SpeechState.ACTIVE_LISTENING, reason="wake_detected")
        sm.transition(SpeechState.PROCESSING, reason="handle")
        self.assertEqual(
            [item.new.name for item in sm.history],
            ["WAKE_LISTENING", "ACTIVE_LISTENING", "PROCESSING"],
        )
        self.assertGreaterEqual(get_metrics().speech_transitions, 3)
        self.assertTrue(sm.history[-1].legal)

    def test_new_states_are_active_session(self):
        sm = SpeechStateMachine()
        for state in (
            SpeechState.ACTING,
            SpeechState.INTERRUPTED,
            SpeechState.PAUSED,
        ):
            sm.set(state)
            self.assertTrue(sm.is_active_session(), state)


class EchoProtectionTests(unittest.TestCase):
    def test_looks_like_self_echo(self):
        self.assertTrue(looks_like_self_echo("Słucham.", "Słucham."))
        self.assertTrue(looks_like_self_echo("dobrze czekam", "Dobrze, czekam."))
        self.assertFalse(looks_like_self_echo("włącz lampę", "Dobrze, czekam."))

    def test_listen_active_drops_own_tts(self):
        pipeline, _ = _pipeline(listen_fn=lambda **kw: "Słucham.")
        pipeline._last_tts_text = "Słucham."
        pipeline._arm_echo_guard()
        heard = pipeline.listen_active()
        self.assertEqual(heard, "")
        self.assertTrue(pipeline.last_listen_echo)
        pipeline.close()

    def test_wake_ignored_while_speaking_or_echo_guard(self):
        pipeline, _ = _pipeline()
        pipeline.state.set(SpeechState.SPEAKING)
        self.assertTrue(pipeline.should_ignore_wake())
        pipeline.state.set(SpeechState.ACTIVE_LISTENING)
        pipeline._arm_echo_guard()
        self.assertTrue(pipeline.should_ignore_wake())
        pipeline.close()


class BargeInAndCancellationTests(unittest.TestCase):
    def test_tts_interrupt_goes_interrupted_then_listening(self):
        speaking = {"v": True}
        stopped = []

        def speak_fn(text):
            for _ in range(40):
                if not speaking["v"]:
                    break
                time.sleep(0.02)

        def stop_fn():
            speaking["v"] = False
            stopped.append(True)

        pipeline, _ = _pipeline()
        pipeline._speak = speak_fn
        pipeline._stop_speak = stop_fn
        pipeline.settings = VoiceSettings(tts_enabled=True, barge_in_enabled=False)
        thread = threading.Thread(
            target=lambda: pipeline.speak("długa odpowiedź", allow_barge=False),
            daemon=True,
        )
        thread.start()
        time.sleep(0.05)
        pipeline.stop_speaking()
        thread.join(timeout=2.0)
        self.assertTrue(stopped)
        self.assertIn(SpeechState.INTERRUPTED, [item.new for item in pipeline.state.history])
        self.assertEqual(pipeline.state.state, SpeechState.ACTIVE_LISTENING)
        self.assertTrue(pipeline.was_interrupted)
        self.assertFalse(pipeline._closed)
        pipeline.close()


class ConversationAmbientTests(unittest.TestCase):
    def setUp(self):
        reset_metrics()
        self.log = []
        self.registry, self.log = _dry_registry(self.log)
        reset_registry_for_tests(self.registry)
        self.model = FakeModelClient(responses=[])
        self.manager = ConversationManager(
            registry=self.registry,
            memory=[],
            model=self.model,
        )
        self.manager.activate()
        self.pipeline, self.spoken = _pipeline()

    def tearDown(self):
        self.pipeline.close()
        reset_registry_for_tests(None)
        reset_model_client()

    def test_multi_turn_without_repeating_wake(self):
        first = self.manager.handle("głośniej")
        self.assertTrue(self.manager.active)
        self.assertFalse(first.ended)
        second = self.manager.handle("ciszej")
        self.assertTrue(self.manager.active)
        self.assertFalse(second.ended)
        wake_again = self.manager.handle("hej elian")
        self.assertTrue(self.manager.active)
        self.assertEqual(wake_again.source, "wake_ack")
        self.assertTrue(wake_again.should_speak)
        ack = (wake_again.text or "").lower()
        self.assertTrue("słucham" in ack or "listening" in ack)

    def test_stop_keeps_session_and_returns_to_listening(self):
        result = self.pipeline.execute_turn(self.manager, "stop")
        self.assertEqual(result.source, "stop")
        self.assertTrue(self.manager.active)
        self.assertFalse(result.ended)
        self.assertEqual(self.pipeline.state.state, SpeechState.ACTIVE_LISTENING)
        self.assertFalse(self.pipeline.state.is_processing())

    def test_czekaj_pauses_then_returns_to_active_listening(self):
        result = self.pipeline.execute_turn(self.manager, "czekaj")
        self.assertEqual(result.source, "wait")
        self.assertTrue(result.paused)
        self.assertTrue(self.manager.active)
        names = self.pipeline.state.names()
        self.assertIn("PAUSED", names)
        self.assertEqual(self.pipeline.state.state, SpeechState.ACTIVE_LISTENING)
        self.assertFalse(self.pipeline.state.is_processing())

    def test_nie_is_correction_without_tools(self):
        result = self.manager.handle("nie")
        self.assertEqual(result.source, "correction")
        self.assertTrue(self.manager.active)
        self.assertEqual(self.log, [])
        self.assertIn("powiedz", (result.text or "").lower())

    def test_nie_o_to_chodzilo_keeps_context(self):
        self.manager.handle("głośniej")
        result = self.manager.handle("nie o to chodziło")
        self.assertEqual(result.source, "correction")
        self.assertTrue(self.manager.active)
        self.assertGreaterEqual(len(self.manager.recent_user), 1)

    def test_mid_reply_correction_retargets_lamp(self):
        self.manager.pending_clarification = {
            "goal": "włącz światło",
            "question": "Którą lampę?",
            "started_at": 0,
        }
        with patch(
            "core.conversation_manager.run_fast_path",
            return_value=None,
        ), patch(
            "core.conversation_manager.run_agent",
            return_value=None,
        ):
            result = self.manager.handle("chodziło mi o lampę")
        self.assertTrue(self.manager.active)
        self.assertNotEqual(result.source, "correction")
        self.assertNotIn("powiedz co miałeś", (result.text or "").lower())
        self.assertIsNone(self.manager.pending_clarification)

    def test_no_processing_deadlock(self):
        boom = MagicMock()
        boom.handle.side_effect = RuntimeError("controlled handle failure")
        boom.cancel_requested = False
        with self.assertRaises(RuntimeError):
            self.pipeline.execute_turn(boom, "głośniej")
        self.assertNotEqual(self.pipeline.state.state, SpeechState.PROCESSING)
        self.assertEqual(self.pipeline.state.state, SpeechState.ACTIVE_LISTENING)
        self.assertGreaterEqual(get_metrics().processing_recovered, 1)

    def test_execute_turn_never_stuck_processing_on_success(self):
        result = self.pipeline.execute_turn(self.manager, "głośniej")
        self.assertTrue(result.active)
        self.assertNotEqual(self.pipeline.state.state, SpeechState.PROCESSING)
        self.assertEqual(self.pipeline.state.state, SpeechState.ACTIVE_LISTENING)


class LiveUsabilityNoiseTests(unittest.TestCase):
    def test_youtube_bleed_is_ambient(self):
        from core.utterance_noise import looks_ambient_bleed, looks_idle_action_command

        self.assertTrue(looks_ambient_bleed("Dzięki za oglądanie!"))
        self.assertFalse(looks_idle_action_command("Dzięki za oglądanie!"))

    def test_wunschlampe_is_noise_not_idle_action(self):
        from core.utterance_noise import looks_idle_action_command, looks_noise_like

        self.assertTrue(looks_noise_like("Wunschlampe"))
        self.assertFalse(looks_idle_action_command("Wunschlampe"))

    def test_clear_commands_are_idle_actions(self):
        from core.utterance_noise import looks_idle_action_command

        self.assertTrue(looks_idle_action_command("otwórz notatnik"))
        self.assertTrue(looks_idle_action_command("zrób ciszej"))
        self.assertTrue(looks_idle_action_command("wyłącz lampę"))
        self.assertTrue(looks_idle_action_command("która jest godzina"))

    def test_legacy_wake_accepts_idle_action(self):
        from voice.settings import VoiceSettings
        from voice.wake import LegacySttWakeListener

        queue = ["Wunschlampe", "otwórz notatnik"]

        def listen_fn(**_kwargs):
            return queue.pop(0) if queue else ""

        wake = LegacySttWakeListener(listen_fn, VoiceSettings())
        result = wake.wait_for_wake(timeout=1.0)
        self.assertTrue(result.detected)
        self.assertEqual(wake.last_remainder, "otwórz notatnik")
        self.assertEqual(result.model_name, "legacy_stt_idle_action")


class CorrectionRemainderTests(unittest.TestCase):
    def test_extractors(self):
        self.assertTrue(is_bare_rejection("nie"))
        self.assertTrue(is_bare_rejection("nie o to chodziło"))
        self.assertEqual(extract_correction_target("nie o to chodziło"), "")
        self.assertEqual(
            extract_correction_target("chodziło mi o lampę").lower(),
            "lampę",
        )


class PlannerCancelTests(unittest.TestCase):
    def test_cancel_before_tool_invoke(self):
        from core.planner import Planner

        log = []
        registry, log = _dry_registry(log)
        planner = Planner(registry, model=FakeModelClient(responses=[]), max_steps=2)
        cancelled = {"v": True}
        result = planner.run(
            "głośniej",
            cancel_check=lambda: cancelled["v"],
        )
        self.assertEqual(result.source, "cancelled")
        self.assertFalse(result.success)
        self.assertEqual(log, [])


if __name__ == "__main__":
    unittest.main()
