# ==========================
# ELIAN — L2 simulated voice glue (dry + optional live)
# ==========================
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import numpy as np


class SimulatedVoiceDryTests(unittest.TestCase):
    def setUp(self):
        os.environ["ELIAN_SIM_VOICE_DRY"] = "1"
        os.environ["ELIAN_PROTECT_USER_APPS"] = "1"

    def tearDown(self):
        os.environ.pop("ELIAN_SIM_VOICE_DRY", None)

    def test_dry_simulate_echoes_text(self):
        from core.simulated_voice import simulate_utterance

        row = simulate_utterance("ciemno tu")
        self.assertEqual(row["status"], "PASS")
        self.assertEqual(row["label"], "SIMULATED_VOICE_PASS")
        self.assertEqual(row["stt_text"], "ciemno tu")
        self.assertTrue(row["dry"])

    def test_dry_fixtures_degrade_stt_text(self):
        from core.simulated_voice import apply_stt_fixture, simulate_utterance

        self.assertEqual(apply_stt_fixture("włącz lampę", "no_diacritics"), "wlacz lampe")
        self.assertEqual(apply_stt_fixture("ciemno tu", "fast"), "ciemnotu")
        row = simulate_utterance("włącz lampę", fixture="no_diacritics")
        self.assertEqual(row["stt_text"], "wlacz lampe")
        self.assertEqual(row["fixture"], "no_diacritics")
        self.assertEqual(row["label"], "SIMULATED_VOICE_PASS")

    def test_dry_turn_hits_cm(self):
        from core.simulated_voice import run_simulated_voice_turn

        payload = run_simulated_voice_turn("ciemno tu", live_tools=False, debug=False)
        self.assertEqual(payload["sim"]["status"], "PASS")
        cm = payload.get("cm") or {}
        self.assertNotIn("niedozwolone argumenty", (cm.get("text") or "").lower())

    def test_dry_fast_fixture_brightness_via_correction(self):
        """fast→ciemnotu must still reach soft brightness after known STT fix."""
        from core.simulated_voice import run_simulated_voice_turn

        payload = run_simulated_voice_turn(
            "ciemno tu", live_tools=False, debug=False, fixture="fast"
        )
        self.assertEqual(payload["sim"]["stt_text"], "ciemnotu")
        cm = payload.get("cm") or {}
        soft = str(cm.get("soft_kind") or "").lower()
        text = str(cm.get("FINAL_RESPONSE") or cm.get("text") or "").lower()
        self.assertTrue(
            soft in ("brightness_up", "light_on")
            or "rozjaś" in text
            or "jasn" in text
            or "lamp" in text,
            msg=f"soft={soft!r} text={text[:160]!r}",
        )


class SimulatedVoiceLiveOptionalTests(unittest.TestCase):
    def test_piper_roundtrip_when_available(self):
        os.environ.pop("ELIAN_SIM_VOICE_DRY", None)
        from core.simulated_voice import piper_available, simulate_utterance

        if not piper_available():
            self.skipTest("Piper/model unavailable")
        # Mock Whisper to avoid GPU load in CI — still exercise Piper→wav→float32.
        fake_audio = np.zeros(16000, dtype=np.float32)
        with patch("core.simulated_voice.stt_from_audio", return_value="ciemno tu"):
            with patch("core.simulated_voice.wav_to_float32", return_value=fake_audio):
                row = simulate_utterance("ciemno tu")
        self.assertEqual(row["status"], "PASS")
        self.assertEqual(row["label"], "SIMULATED_VOICE_PASS")
        self.assertEqual(row["stt_text"], "ciemno tu")


if __name__ == "__main__":
    unittest.main()
