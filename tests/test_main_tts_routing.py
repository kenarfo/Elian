# ==========================
# ELIAN — main / pipeline RoutedTTS hot-path (Fish → Piper)
# ==========================
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from voice.audio_gate import reset_audio_gate_for_tests
from voice.pipeline import VoicePipeline
from voice.settings import VoiceSettings
from voice.tts_engine import RoutedTTS, get_tts_engine, reset_tts_engine_for_tests


class MainTtsRoutingTests(unittest.TestCase):
    def setUp(self):
        reset_audio_gate_for_tests()
        reset_tts_engine_for_tests()
        os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "1"

    def tearDown(self):
        os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "1"
        reset_tts_engine_for_tests()

    def test_get_tts_engine_respects_prefer_fish_config(self):
        with patch("Config.get_config_value") as cfg:
            cfg.side_effect = lambda section, key, default=None, **kw: {
                ("voice", "prefer_fish_tts"): True,
                ("voice", "fish_voice_id"): "",
            }.get((section, key), default)
            reset_tts_engine_for_tests()
            engine = get_tts_engine()
        self.assertIsInstance(engine, RoutedTTS)
        self.assertTrue(engine.prefer_fish)

    def test_pipeline_binds_routed_tts_when_speak_not_injected(self):
        """listen_fn alone must not force Piper-only Voice.speak (Jarvis path)."""
        pipeline = VoicePipeline(
            settings=VoiceSettings(tts_enabled=True, wake_enabled=False),
            listen_fn=lambda **k: "",
            speak_fn=None,
            stop_speak_fn=None,
            wake_listener=MagicMock(
                mode="fake",
                start=lambda: None,
                stop=lambda: None,
                wait_for_wake=lambda **k: MagicMock(detected=False),
            ),
        )
        with patch("voice.wake.create_wake_listener") as _:
            pipeline._wake = MagicMock(
                mode="fake",
                start=lambda: None,
                stop=lambda: None,
            )
            pipeline.start()
        engine = get_tts_engine()
        self.assertIs(pipeline._speak.__self__, engine)
        self.assertIs(pipeline._stop_speak.__self__, engine)
        self.assertEqual(pipeline._speak.__func__, RoutedTTS.speak)
        pipeline.close()

    def test_routed_stop_forwards_barge_force_flag(self):
        routed = RoutedTTS(prefer_fish=False)
        with patch("Voice.stop_speaking") as stop:
            routed.stop(force_portaudio_stop=False)
        stop.assert_called()
        # At least one call carries barge-safe flag (fish and/or fallback).
        kwargs_list = [c.kwargs for c in stop.call_args_list]
        self.assertTrue(
            any(kw.get("force_portaudio_stop") is False for kw in kwargs_list),
            msg=f"expected force_portaudio_stop=False, got {kwargs_list}",
        )


if __name__ == "__main__":
    unittest.main()
