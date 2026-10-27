# ==========================
# ELIAN — honest latency telemetry (Faza 2.2)
# ==========================
from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from core.latency import LatencyTracker, new_turn, record_current_ms, set_current_tracker


class HonestLatencyTelemetryTests(unittest.TestCase):
    def test_fish_synthesis_not_reported_as_zero_when_recorded(self) -> None:
        t = LatencyTracker()
        t.mark("turn_start")
        t.mark("tts_gen_start")
        time.sleep(0.01)
        t.record_ms("gpu_lease_wait_ms", 120.0)
        t.record_ms("fish_worker_startup_ms", 9000.0)
        t.record_ms("fish_synthesis_ms", 3500.0)
        t.mark("tts_gen_end")
        t.mark("tts_playback_start")
        t.mark("tts_first_audio")
        snap = t.snapshot()
        self.assertGreaterEqual(snap.get("fish_synthesis_ms", 0), 3500.0)
        self.assertGreaterEqual(snap.get("gpu_lease_wait_ms", 0), 120.0)
        self.assertGreaterEqual(snap.get("fish_worker_startup_ms", 0), 9000.0)
        # tts_generate must reflect Fish wall, not a zero-width wrapper.
        self.assertGreaterEqual(snap.get("tts_generation_ms", 0), 3500.0)

    def test_zero_width_wrapper_replaced_by_fish_parts(self) -> None:
        t = LatencyTracker()
        t.mark("turn_start")
        # Simulate old speak_from_wav bug: gen_start/end identical.
        now = time.perf_counter()
        t.marks["tts_gen_start"] = now
        t.marks["tts_gen_end"] = now
        t.record_ms("fish_synthesis_ms", 4123.0)
        t.mark("tts_first_audio")
        snap = t.snapshot()
        self.assertGreaterEqual(snap["tts_generation_ms"], 4123.0)
        self.assertNotEqual(snap["tts_generation_ms"], 0.0)

    def test_pipeline_marks_present_in_snapshot(self) -> None:
        t = new_turn()
        t.mark("capture_start")
        t.mark("capture_end")
        t.mark("stt_start")
        t.mark("stt_end")
        t.mark("understanding_start")
        t.mark("understanding_end")
        t.mark("cognitive_start")
        t.mark("cognitive_end")
        t.mark("decision_start")
        t.mark("decision_end")
        t.mark("planner_start")
        t.mark("planner_end")
        t.mark("tool_start")
        t.mark("tool_end")
        t.mark("llm_start")
        t.mark("llm_end")
        t.record_ms("llm_ms", 250.0)
        t.mark("tts_queue_start")
        t.mark("tts_gen_start")
        t.record_ms("fish_synthesis_ms", 800.0)
        t.mark("tts_gen_end")
        t.mark("tts_playback_start")
        t.mark("tts_first_audio")
        t.mark("tts_playback_end")
        snap = t.snapshot()
        for key in (
            "speech_capture_ms",
            "stt_ms",
            "understanding_ms",
            "cognitive_ms",
            "decision_ms",
            "planner_ms",
            "tool_ms",
            "llm_ms",
            "fish_synthesis_ms",
            "tts_generation_ms",
            "total_response_ms",
        ):
            self.assertIn(key, snap, msg=f"missing {key}")
        self.assertGreaterEqual(snap["llm_ms"], 250.0)
        set_current_tracker(None)

    def test_record_current_ms_helper(self) -> None:
        t = new_turn()
        record_current_ms("gpu_lease_wait_ms", 55.0)
        snap = t.snapshot()
        self.assertGreaterEqual(snap.get("gpu_lease_wait_ms", 0), 55.0)
        set_current_tracker(None)

    def test_fish_speak_marks_generation_around_synth(self) -> None:
        from voice.tts_engine import FishAudioTTS

        t = new_turn()
        engine = FishAudioTTS()

        def fake_synth(text, path):
            time.sleep(0.02)
            # Pretend worker recorded synthesis.
            record_current_ms("fish_synthesis_ms", 1234.0)
            return True, 1234.0

        with patch.object(engine, "synthesize_to_file", side_effect=fake_synth), patch(
            "Voice.speak_from_wav", return_value=True
        ):
            ok = engine.speak("Hello latency test.")
        self.assertTrue(ok)
        snap = t.snapshot()
        self.assertIn("tts_gen_start", t.marks)
        self.assertIn("tts_gen_end", t.marks)
        self.assertGreaterEqual(snap.get("fish_synthesis_ms", 0), 1234.0)
        self.assertGreater(snap.get("tts_generation_ms", 0), 5.0)
        set_current_tracker(None)


if __name__ == "__main__":
    unittest.main()
