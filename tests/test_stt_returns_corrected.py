# ==========================
# ELIAN — STT returns corrected command text (not discarded raw)
# ==========================
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from TextCorrector import apply_known_corrections
from core.transcript import envelope_from_whisper


class SttCorrectionReturnTests(unittest.TestCase):
    def test_known_correction_czemnotu(self):
        self.assertEqual(apply_known_corrections("czemnotu."), "ciemno tu.")
        self.assertEqual(apply_known_corrections("Czemnotu.").lower(), "ciemno tu.")

    def test_envelope_keeps_raw_uses_corrected_normalized(self):
        env = envelope_from_whisper("Czemnotu.", corrected="ciemno tu.")
        self.assertEqual(env.raw_transcript, "Czemnotu.")
        self.assertEqual(env.normalized_transcript, "ciemno tu.")

    def test_transcribe_audio_returns_corrected(self):
        import Voice as voice_mod

        audio = np.zeros(16000, dtype=np.float32)
        audio[100:500] = 0.2
        segments = [MagicMock(text="Czemnotu.")]
        model = MagicMock()
        model.transcribe.return_value = (segments, MagicMock())
        with patch.object(voice_mod, "get_whisper_model", return_value=model):
            with patch.object(voice_mod, "_clip_too_quiet", return_value=False):
                with patch.object(voice_mod, "_normalize_stt_audio", side_effect=lambda a: a):
                    with patch.object(voice_mod, "_should_llm_correct", return_value=False):
                        with patch("builtins.print"):
                            out = voice_mod.transcribe_audio(audio, llm_correct=False)
        self.assertEqual(out.lower().rstrip("."), "ciemno tu")


if __name__ == "__main__":
    unittest.main()
