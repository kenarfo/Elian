# ==========================
# ELIAN — speech gate energy fallback (close-talk)
# ==========================
from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np


class SpeechGateEnergyTests(unittest.TestCase):
    def test_energy_fallback_accepts_close_talk_peak(self):
        import Voice as V

        prev = V.ENERGY_THRESHOLD
        V.ENERGY_THRESHOLD = 0.008
        try:
            audio = np.zeros((512, 1), dtype=np.float32)
            with patch.object(V, "detect_speech", return_value=False):
                # Was rejected by max(0.018, thr*3)=0.024; must pass now.
                self.assertTrue(V._speech_gate(audio, 0.020))
                self.assertFalse(V._speech_gate(audio, 0.010))
                self.assertFalse(V._speech_gate(audio, 0.007))
        finally:
            V.ENERGY_THRESHOLD = prev


if __name__ == "__main__":
    unittest.main()
