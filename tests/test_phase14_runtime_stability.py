# ==========================
# ELIAN — Phase 14 barge / runtime stress (offline)
# ==========================
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from voice.audio_gate import get_audio_gate, reset_audio_gate_for_tests
from voice.pipeline import VoicePipeline
from voice.settings import VoiceSettings
from voice.state import SpeechState


class FakePortAudio:
    """Track concurrent InputStream / play / stop without native audio."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.open_inputs = 0
        self.max_inputs = 0
        self.play_calls = 0
        self.stop_calls = 0
        self.stop_from_threads: list[str] = []
        self.play_active = False
        self.aborted = False

    class _Stream:
        def __init__(self, owner: "FakePortAudio", **kwargs) -> None:
            self._owner = owner
            self._callback = kwargs.get("callback")
            self._blocksize = int(kwargs.get("blocksize") or 512)
            self._pump_stop = threading.Event()
            self._pump_thread: threading.Thread | None = None

        def __enter__(self):
            with self._owner.lock:
                self._owner.open_inputs += 1
                self._owner.max_inputs = max(
                    self._owner.max_inputs, self._owner.open_inputs
                )
                if self._owner.open_inputs > 1:
                    self._owner.aborted = True
            if self._callback is not None:

                def _pump():
                    import numpy as np

                    while not self._pump_stop.wait(0.005):
                        try:
                            frames = np.zeros((self._blocksize, 1), dtype="float32")
                            self._callback(frames, self._blocksize, None, None)
                        except Exception:
                            break

                self._pump_thread = threading.Thread(
                    target=_pump, name="fake-pa-cb", daemon=True
                )
                self._pump_thread.start()
            return self

        def __exit__(self, *args):
            self._pump_stop.set()
            if self._pump_thread is not None:
                self._pump_thread.join(timeout=1.0)
            with self._owner.lock:
                self._owner.open_inputs = max(0, self._owner.open_inputs - 1)

        def read(self, frames):
            time.sleep(0.005)
            # Quiet by default — tests inject barge via pipeline.stop_speaking
            import numpy as np

            return (np.zeros((frames, 1), dtype="float32"), True)

    def InputStream(self, **kwargs):
        return FakePortAudio._Stream(self, **kwargs)

    def play(self, *args, **kwargs):
        with self.lock:
            self.play_calls += 1
            self.play_active = True

    def wait(self):
        # Block until stop or short timeout
        for _ in range(40):
            if not self.play_active:
                return
            time.sleep(0.01)
        self.play_active = False

    def stop(self):
        with self.lock:
            self.stop_calls += 1
            self.stop_from_threads.append(threading.current_thread().name)
            self.play_active = False


class Phase14BargeStressTests(unittest.TestCase):
    def setUp(self):
        reset_audio_gate_for_tests()
        self.fake_sd = FakePortAudio()

    def _pipeline(self, *, barge: bool = True) -> VoicePipeline:
        spoken = []

        def speak_fn(text):
            spoken.append(text)
            # Simulate Voice.speak interruptible wait using fake PortAudio.
            import Voice as voice_mod

            voice_mod._stop_playback.clear()
            gate = get_audio_gate()
            gate.begin_tts()
            try:
                self.fake_sd.play(None, 16000)
                done = threading.Event()

                def waiter():
                    try:
                        self.fake_sd.wait()
                    finally:
                        done.set()

                t = threading.Thread(target=waiter, name="elian-tts-wait", daemon=True)
                t.start()
                while not done.wait(timeout=0.02):
                    if voice_mod._stop_playback.is_set():
                        with gate.with_portaudio("sd_stop_owner"):
                            self.fake_sd.stop()
                        break
                t.join(timeout=1.0)
            finally:
                gate.end_tts()

        def stop_fn(*args, **kwargs):
            import Voice as voice_mod

            force = True
            if kwargs:
                force = bool(kwargs.get("force_portaudio_stop", True))
            voice_mod.request_playback_interrupt()
            if force:
                with get_audio_gate().with_portaudio("sd_stop"):
                    self.fake_sd.stop()

        pipeline = VoicePipeline(
            settings=VoiceSettings(
                tts_enabled=True,
                barge_in_enabled=barge,
                sample_rate=16000,
                block_size=512,
                barge_in_grace_ms=0,
                barge_in_energy=99.0,  # energy barge disabled; use stop_speaking
            ),
            speak_fn=speak_fn,
            stop_speak_fn=stop_fn,
            is_speaking_fn=lambda: get_audio_gate().snapshot().active_tts_playback > 0,
        )
        pipeline._spoken = spoken
        return pipeline

    def test_50_barge_cycles_no_abort_no_duplicate_streams(self):
        pipeline = self._pipeline(barge=True)
        pipeline.start()
        with patch.dict("sys.modules", {"sounddevice": self.fake_sd}):
            # Patch InputStream path used by barge monitor
            with patch("voice.pipeline.np") as np_mod:
                import numpy as real_np

                np_mod.sqrt = real_np.sqrt
                np_mod.mean = real_np.mean
                np_mod.square = real_np.square
                for i in range(50):
                    thr = threading.Thread(
                        target=lambda: pipeline.speak(f"odpowiedź {i}", allow_barge=True),
                        daemon=True,
                    )
                    thr.start()
                    time.sleep(0.03)
                    pipeline.stop_speaking()
                    thr.join(timeout=2.0)
                    self.assertNotEqual(
                        pipeline.state.state,
                        SpeechState.SPEAKING,
                        msg=f"cycle {i} stuck SPEAKING",
                    )
                    pipeline.clear_cancellation()
        self.assertFalse(self.fake_sd.aborted)
        self.assertLessEqual(self.fake_sd.max_inputs, 1)
        self.assertFalse(pipeline._closed)
        gate = get_audio_gate()
        self.assertLessEqual(gate.snapshot().active_input_streams, 1)
        self.assertLessEqual(gate.snapshot().active_tts_playback, 1)
        # No stop from barge thread while stream held — owner or main stop ok.
        for name in self.fake_sd.stop_from_threads:
            self.assertNotEqual(name, "elian-barge")
        pipeline.close()

    def test_cancel_during_tts_startup(self):
        pipeline = self._pipeline(barge=False)
        pipeline.start()
        started = threading.Event()

        def slow_speak(text):
            started.set()
            time.sleep(0.05)
            import Voice as voice_mod

            voice_mod.request_playback_interrupt()

        pipeline._speak = slow_speak
        pipeline.speak("start", allow_barge=False)
        pipeline.stop_speaking()
        self.assertNotEqual(pipeline.state.state, SpeechState.SPEAKING)
        pipeline.close()

    def test_rapid_speaking_interrupted_listening(self):
        pipeline = self._pipeline(barge=False)
        pipeline.start()
        for _ in range(20):
            pipeline.state.transition(SpeechState.SPEAKING, reason="t")
            pipeline.stop_speaking()
            self.assertEqual(pipeline.state.state, SpeechState.INTERRUPTED)
            pipeline.resume_listening(reason="after")
            self.assertEqual(pipeline.state.state, SpeechState.ACTIVE_LISTENING)
        pipeline.close()

    def test_multiple_cancel_requests_safe(self):
        pipeline = self._pipeline(barge=False)
        pipeline.start()
        pipeline.state.transition(SpeechState.SPEAKING, reason="t")
        for _ in range(10):
            pipeline.stop_speaking()
            pipeline.request_cancel()
        self.assertTrue(pipeline.cancellation_requested)
        self.assertNotEqual(pipeline.state.state, SpeechState.SPEAKING)
        pipeline.close()

    def test_audio_gate_serializes_second_opener(self):
        """Second InputStream waits until the first slot closes (anti -9985)."""
        gate = reset_audio_gate_for_tests()
        order: list[str] = []

        def holder():
            with gate.input_stream_slot():
                order.append("a_open")
                time.sleep(0.12)
                order.append("a_close")

        def waiter():
            time.sleep(0.02)
            with gate.input_stream_slot():
                order.append("b_open")

        t1 = threading.Thread(target=holder, name="holder")
        t2 = threading.Thread(target=waiter, name="waiter")
        t1.start()
        t2.start()
        t1.join(timeout=3.0)
        t2.join(timeout=3.0)
        self.assertEqual(order, ["a_open", "a_close", "b_open"])
        self.assertEqual(gate.snapshot().active_input_streams, 0)

    def test_audio_gate_duplicate_input_recorded_after_timeout(self):
        gate = reset_audio_gate_for_tests()
        with gate.input_stream_slot():
            with gate.input_stream_slot():
                pass
        self.assertIn("duplicate_input_stream", gate.violations)

    def test_whisper_exception_does_not_raise(self):
        import Voice as voice_mod

        with patch("builtins.print"):
            with patch.object(
                voice_mod, "get_whisper_model", side_effect=RuntimeError("cuda boom")
            ):
                out = voice_mod.transcribe_audio(real_audio())
        self.assertEqual(out, "")

    def test_piper_failure_classified(self):
        import Voice as voice_mod

        with patch("builtins.print"):
            with patch.object(voice_mod.subprocess, "run", side_effect=OSError("piper gone")):
                with patch.object(voice_mod.os.path, "isfile", return_value=True):
                    with patch.object(voice_mod.shutil, "which", return_value="piper.exe"):
                        with patch.object(voice_mod, "PIPER", "piper.exe"):
                            with patch.object(voice_mod, "VOICE_MODEL", "model.onnx"):
                                ok = voice_mod._generate_piper_wav("czesc", "out.wav")
        self.assertFalse(ok)

    def test_cancellation_does_not_call_sys_exit(self):
        pipeline = self._pipeline(barge=False)
        pipeline.start()
        with patch("sys.exit") as mock_exit:
            pipeline.state.transition(SpeechState.SPEAKING, reason="t")
            pipeline.stop_speaking()
            mock_exit.assert_not_called()
        pipeline.close()

    def test_runtime_snapshot_fields(self):
        from core.crash_log import build_runtime_snapshot, update_runtime_context

        update_runtime_context(
            session_id="sess_test",
            active_tool="open_app",
            last_semantic={"intent": "open_app"},
            last_planner_step={"tool": "open_app"},
        )
        snap = build_runtime_snapshot(reason="barge_test", barge_in=True, gpu_op="whisper")
        for key in (
            "session_id",
            "timestamp",
            "thread",
            "voice_state",
            "active_tool",
            "tts_active",
            "stt_active",
            "barge_in",
            "gpu_op",
            "last_semantic_decision",
            "last_planner_step",
            "last_exception",
            "exit_reason",
        ):
            self.assertIn(key, snap)
        self.assertEqual(snap["session_id"], "sess_test")
        self.assertTrue(snap["barge_in"])

    def test_classify_and_recover_marks_degraded(self):
        from core.crash_log import classify_and_recover, update_runtime_context

        update_runtime_context(degraded_subsystems=[])
        classify_and_recover(
            RuntimeError("ollama down"),
            subsystem="ollama",
            classification="INTEGRATION_UNAVAILABLE",
        )
        from core import crash_log as cl

        self.assertTrue(
            any("ollama" in x for x in cl._RUNTIME_CTX.get("degraded_subsystems") or [])
        )

    def test_clear_degraded_subsystem_after_success(self):
        from core import crash_log as cl
        from core.crash_log import (
            classify_and_recover,
            clear_degraded_subsystem,
            update_runtime_context,
        )

        update_runtime_context(degraded_subsystems=[])
        classify_and_recover(
            FileNotFoundError(2, "missing"),
            subsystem="tts",
            classification="TTS_PROCESS_FAILURE",
        )
        self.assertTrue(
            any("tts:" in x for x in cl._RUNTIME_CTX.get("degraded_subsystems") or [])
        )
        clear_degraded_subsystem("tts")
        self.assertFalse(
            any("tts:" in x for x in cl._RUNTIME_CTX.get("degraded_subsystems") or [])
        )

    def test_barge_snapshot_disk_persist_rate_limited(self):
        """stop_speaking floods must not write a JSON file per barge call."""
        import tempfile
        from pathlib import Path
        from unittest import mock

        from core import crash_log as cl

        cl.reset_snapshot_rate_limits_for_tests()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.object(cl, "_diagnostics_dir", return_value=root):
                with mock.patch.object(cl, "_barge_snapshot_min_interval_s", return_value=30.0):
                    for _ in range(25):
                        cl.write_runtime_snapshot(
                            reason="barge_or_stop",
                            barge_in=True,
                            persist=True,
                        )
                        cl.write_runtime_snapshot(
                            reason="before_barge_signal",
                            barge_in=True,
                            persist=True,
                        )
            barge_files = list(root.glob("runtime_snapshot_*barge*"))
            self.assertEqual(
                len(barge_files),
                1,
                f"expected 1 rate-limited barge file, got {len(barge_files)}",
            )
            self.assertTrue((root / "runtime_heartbeat.json").is_file())
            # Crash-class reasons stay unthrottled; recover_* is rate-limited.
            cl.reset_snapshot_rate_limits_for_tests()
            with mock.patch.object(cl, "_diagnostics_dir", return_value=root):
                with mock.patch.object(cl, "_barge_snapshot_min_interval_s", return_value=30.0):
                    for _ in range(10):
                        cl.write_runtime_snapshot(
                            reason="recover_TTS_PROCESS_FAILURE",
                            persist=True,
                        )
                    cl.write_runtime_snapshot(reason="crash_test", persist=True)
                    cl.write_runtime_snapshot(reason="crash_test", persist=True)
            recover_files = list(root.glob("runtime_snapshot_*recover_*"))
            self.assertEqual(len(recover_files), 1)
            crash_files = list(root.glob("runtime_snapshot_*crash*"))
            self.assertGreaterEqual(len(crash_files), 1)


def real_audio():
    import numpy as np

    return np.zeros(1600, dtype="float32")


class Phase14NaturalSpeechContractTests(unittest.TestCase):
    def test_mozysz_mi_odpalic_spotify_not_knowledge(self):
        from core.clarification_policy import detect_intent_category
        from core.intent_family import IntentFamily, classify_intent_family

        text = "możesz mi odpalić Spotify"
        self.assertEqual(classify_intent_family(text).family, IntentFamily.APP)
        self.assertEqual(detect_intent_category(text), "open_app")

    def test_sanitize_does_not_rewrite_app_to_knowledge(self):
        from core.clarification_policy import sanitize_clarification_question

        q = sanitize_clarification_question(
            "Do you want to open an application?",
            goal="możesz mi odpalić Spotify",
        )
        self.assertNotIn("wyjaśnienie", q.lower())
        self.assertIn("aplikac", q.lower())

    def test_fastpath_natural_app_media(self):
        from core.fast_path import FastPathStatus, evaluate_fast_path

        app = evaluate_fast_path("możesz mi odpalić Spotify")
        self.assertEqual(app.status, FastPathStatus.HANDLED_COMPLETE)
        media = evaluate_fast_path("puść coś Michaela Jacksona")
        self.assertEqual(media.status, FastPathStatus.HANDLED_COMPLETE)


if __name__ == "__main__":
    unittest.main()
