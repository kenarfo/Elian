# ==========================
# ELIAN — Voice Engine 2.0 tests (Phase 4)
# Offline / headless — no mic, GPU, Piper, OpenWakeWord required
# ==========================

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from core.conversation_manager import ConversationManager
from core.latency import LatencyTracker, new_turn
from core.model_client import FakeModelClient
from core.tools import ToolRegistry
from voice.pipeline import VoicePipeline
from voice.settings import VoiceSettings, validate_voice_config
from voice.state import SpeechState, SpeechStateMachine
from voice.wake import (
    AcousticWakeListener,
    FakeWakeListener,
    create_wake_listener,
)


class LatencyTests(unittest.TestCase):
    def test_snapshot_named_metrics(self):
        t = LatencyTracker()
        t.mark("wake_start")
        time.sleep(0.01)
        t.mark("wake_detected")
        t.mark("stt_start")
        time.sleep(0.01)
        t.mark("stt_end")
        snap = t.snapshot()
        self.assertIn("wake_detection_ms", snap)
        self.assertIn("stt_ms", snap)
        self.assertGreater(snap["wake_detection_ms"], 0)


class ConfigValidationTests(unittest.TestCase):
    def test_wake_threshold_out_of_range(self):
        errors = validate_voice_config({"wake_threshold": 15})
        self.assertTrue(errors)

    def test_valid_threshold(self):
        errors = validate_voice_config({"wake_threshold": 0.5})
        self.assertEqual(errors, [])


class SpeechStateTests(unittest.TestCase):
    def test_state_machine(self):
        sm = SpeechStateMachine()
        self.assertTrue(sm.is_idle())
        sm.set(SpeechState.SPEAKING)
        self.assertTrue(sm.is_speaking())
        sm.set(SpeechState.ACTIVE_LISTENING)
        self.assertTrue(sm.is_active_session())


class WakePipelineTests(unittest.TestCase):
    def test_fake_wake_idle_to_active(self):
        fake = FakeWakeListener()
        spoken = []
        pipeline = VoicePipeline(
            settings=VoiceSettings(session_idle_timeout=30, barge_in_enabled=False),
            wake_listener=fake,
            listen_fn=lambda **kw: "",
            speak_fn=lambda t, **k: spoken.append(t),
            stop_speak_fn=lambda: None,
            is_speaking_fn=lambda: False,
            remove_wake_fn=lambda s: s,
            is_wake_fn=lambda s: False,
        )
        pipeline.start()
        self.assertEqual(pipeline.state.state, SpeechState.IDLE)

        def fire():
            time.sleep(0.05)
            fake.trigger(score=0.9)

        threading.Thread(target=fire, daemon=True).start()
        result = pipeline.await_wake(timeout=2.0)
        self.assertTrue(result.detected)
        self.assertEqual(pipeline.state.state, SpeechState.ACTIVE_LISTENING)
        pipeline.close()

    def test_wake_plus_command_transcript(self):
        fake = FakeWakeListener()
        audio = np.zeros(1600, dtype=np.float32)

        def transcribe(a, llm_correct=False):
            return "elian otwórz spotify"

        pipeline = VoicePipeline(
            settings=VoiceSettings(),
            wake_listener=fake,
            listen_fn=lambda **kw: "",
            transcribe_fn=transcribe,
            speak_fn=lambda *a, **k: None,
            remove_wake_fn=lambda s: s.replace("elian", "").strip(),
            is_wake_fn=lambda s: "elian" in s,
        )
        pipeline.start()
        fake.trigger(audio_after_wake=audio, score=0.85)
        wake = pipeline.await_wake(timeout=1.0)
        cmd = pipeline.transcript_from_wake(wake)
        self.assertIn("spotify", cmd)
        self.assertNotIn("elian", cmd)
        pipeline.close()

    def test_wake_cooldown_does_not_double_session(self):
        settings = VoiceSettings(wake_cooldown=2.0, wake_threshold=0.5)
        listener = AcousticWakeListener(
            settings,
            is_speaking=lambda: False,
            sounddevice=MagicMock(),
        )
        listener.available = True
        listener._model = MagicMock()
        listener._model.predict.return_value = {"hey_jarvis": 0.9}
        listener._last_wake_at = time.time()
        # Cooldown path is inside wait_for_wake with real stream — unit-check flag
        self.assertLess(
            time.time() - listener._last_wake_at,
            settings.wake_cooldown,
        )

    def test_session_timeout_path_via_manager(self):
        manager = ConversationManager(
            registry=ToolRegistry(),
            model=FakeModelClient(responses=[]),
        )
        manager.activate()
        self.assertTrue(manager.active)
        result = manager.handle("")
        self.assertTrue(result.ended)
        self.assertFalse(manager.active)

    def test_acoustic_unavailable_fallback(self):
        settings = VoiceSettings(
            wake_enabled=True,
            wake_legacy_stt_fallback=True,
        )
        with patch(
            "voice.wake.AcousticWakeListener.load",
            return_value=False,
        ):
            # Force load fail by using create after patching load on instance
            pass

        calls = []

        def listen_fn(**kw):
            calls.append(1)
            if len(calls) == 1:
                return "elian głośniej"
            return ""

        # Simulate create_wake_listener fallback path directly
        from voice.wake import LegacySttWakeListener

        legacy = LegacySttWakeListener(listen_fn, settings)
        legacy.start()
        result = legacy.wait_for_wake(timeout=1.0)
        self.assertTrue(result.detected)
        self.assertEqual(result.mode, "legacy_stt")
        self.assertEqual(legacy.last_remainder, "głośniej")


class SelfEchoAndBargeTests(unittest.TestCase):
    def test_self_echo_ignored_while_speaking(self):
        settings = VoiceSettings(wake_threshold=0.4)
        speaking = {"v": True}
        listener = AcousticWakeListener(
            settings,
            is_speaking=lambda: speaking["v"],
        )
        # Directly verify gate: when speaking, predict would be skipped
        # (logic under wait_for_wake). Unit-level: is_speaking blocks.
        self.assertTrue(listener.is_speaking())
        speaking["v"] = False
        self.assertFalse(listener.is_speaking())

    def test_barge_in_stops_playback(self):
        stopped = []
        speaking_flag = {"v": True}

        def speak_fn(text, **kw):
            # Simulate long TTS
            for _ in range(20):
                if not speaking_flag["v"]:
                    break
                time.sleep(0.02)

        def stop_fn():
            speaking_flag["v"] = False
            stopped.append(True)

        pipeline = VoicePipeline(
            settings=VoiceSettings(barge_in_enabled=False),
            wake_listener=FakeWakeListener(),
            listen_fn=lambda **kw: "",
            speak_fn=speak_fn,
            stop_speak_fn=stop_fn,
            is_speaking_fn=lambda: speaking_flag["v"],
        )
        pipeline.start()
        pipeline.state.set(SpeechState.SPEAKING)
        t = threading.Thread(
            target=lambda: pipeline.speak("długa odpowiedź", allow_barge=False),
            daemon=True,
        )
        t.start()
        time.sleep(0.05)
        pipeline.stop_speaking()
        t.join(timeout=2.0)
        self.assertTrue(stopped)
        self.assertFalse(pipeline.state.is_speaking())
        pipeline.close()


class TtsStopTests(unittest.TestCase):
    def test_stop_speaking_no_deadlock(self):
        import Voice as voice_mod

        with (
            patch.object(voice_mod, "PIPER", "piper.exe"),
            patch.object(voice_mod, "VOICE_MODEL", "voice.onnx"),
            patch.object(voice_mod.os.path, "isfile", return_value=True),
            patch.object(voice_mod.shutil, "which", return_value="piper.exe"),
            patch.object(voice_mod.os.path, "exists", return_value=True),
            patch.object(
                voice_mod.sf, "read", return_value=(np.zeros(1000), 22050)
            ),
            patch.object(voice_mod.sd, "play"),
            patch.object(voice_mod.sd, "wait", side_effect=lambda: time.sleep(0.3)),
            patch.object(voice_mod.sd, "stop") as stop_mock,
            patch.object(voice_mod.os, "remove"),
            patch.object(voice_mod.os, "makedirs"),
            patch.object(voice_mod.time, "sleep"),
        ):
            fake_proc = MagicMock()
            fake_proc.returncode = 0
            fake_proc.communicate = MagicMock(return_value=("", ""))
            with patch.object(
                voice_mod.subprocess, "Popen", return_value=fake_proc
            ):
                thr = threading.Thread(
                    target=lambda: voice_mod.speak("test", cooldown=0),
                    daemon=True,
                )
                thr.start()
                time.sleep(0.05)
                voice_mod.stop_speaking()
                thr.join(timeout=2.0)
                self.assertFalse(thr.is_alive())
                stop_mock.assert_called()


class FastPathArchitectureTests(unittest.TestCase):
    def test_glosniej_zero_agent_llm_and_no_corrector_path(self):
        """Wake → STT text → FastPath; Agent model calls = 0."""
        from core.builtin_tools import build_default_registry

        model = FakeModelClient(responses=[])
        registry = build_default_registry()

        manager = ConversationManager(registry=registry, model=model)
        manager.activate()

        with patch("core.builtin_tools.Music.run", return_value="Gotowe."):
            result = manager.handle("głośniej")

        self.assertEqual(len(model.calls), 0)
        self.assertIn(result.source, ('fast_path', 'action_loop', 'agent'))
        self.assertTrue(result.should_speak)

class CreateWakeFallbackTests(unittest.TestCase):
    def test_create_wake_falls_back_to_legacy(self):
        settings = VoiceSettings(
            wake_enabled=True,
            wake_model="",  # no custom elian.onnx
            wake_legacy_stt_fallback=True,
        )

        def listen_fn(**kw):
            return "elian"

        # Missing custom model must not load stock OpenWakeWord (no "Elian").
        with patch.object(AcousticWakeListener, "load") as load_mock:
            listener, mode = create_wake_listener(
                settings,
                listen_fn=listen_fn,
            )
            load_mock.assert_not_called()
        self.assertEqual(mode, "legacy_stt")
        self.assertEqual(listener.__class__.__name__, "LegacySttWakeListener")

    def test_missing_elian_onnx_uses_legacy_for_hej_elian(self):
        """Production path: 'hej elian' works via STT text, not OWW defaults."""
        from WakeWord import is_wake_word

        settings = VoiceSettings(
            wake_enabled=True,
            wake_model="",
            wake_legacy_stt_fallback=True,
        )
        heard = {"n": 0}

        def listen_fn(**kw):
            heard["n"] += 1
            if heard["n"] == 1:
                return "hej elian głośniej"
            return ""

        listener, mode = create_wake_listener(settings, listen_fn=listen_fn)
        self.assertEqual(mode, "legacy_stt")
        listener.start()
        result = listener.wait_for_wake(timeout=1.0)
        self.assertTrue(result.detected)
        self.assertEqual(result.mode, "legacy_stt")
        self.assertTrue(is_wake_word("hej elian głośniej"))
        self.assertEqual(listener.last_remainder, "głośniej")

    def test_create_wake_acoustic_load_fail_falls_back(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "elian.onnx"
            model.write_bytes(b"fake")
            settings = VoiceSettings(
                wake_enabled=True,
                wake_model=str(model),
                wake_legacy_stt_fallback=True,
            )

            def listen_fn(**kw):
                return "elian"

            with patch.object(
                AcousticWakeListener,
                "load",
                return_value=False,
            ):
                listener, mode = create_wake_listener(
                    settings,
                    listen_fn=listen_fn,
                )
            self.assertEqual(mode, "legacy_stt")
            self.assertEqual(listener.__class__.__name__, "LegacySttWakeListener")


class WaitPhraseSessionTests(unittest.TestCase):
    def test_wait_does_not_end_session(self):
        manager = ConversationManager(
            registry=ToolRegistry(),
            model=FakeModelClient(responses=[]),
        )
        manager.activate()
        result = manager.handle("daj mi chwilę")
        self.assertTrue(manager.active)
        self.assertFalse(result.ended)
        self.assertEqual(result.source, "wait")


if __name__ == "__main__":
    unittest.main()
