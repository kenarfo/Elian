# ==========================
# ELIAN — Fish S2 backend unit tests (no GPU required)
# ==========================
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice.fish_s2_backend import (
    files_ready,
    health_snapshot,
    load_fish_s2_config,
    reset_fish_health_for_tests,
    synthesize_s2_wav,
)
from voice.tts_engine import FishAudioTTS, RoutedTTS, reset_tts_engine_for_tests


class FishS2ConfigTests(unittest.TestCase):
    def test_quant_maps_model_file(self) -> None:
        cfg = load_fish_s2_config()
        self.assertTrue(str(cfg.quant))
        self.assertTrue(str(cfg.model_file).endswith(".gguf"))
        self.assertEqual(cfg.gpu_policy, "exclusive")

    def test_local_jarvis_voice_profile_config(self) -> None:
        cfg = load_fish_s2_config()
        self.assertEqual(str(cfg.quant).lower(), "q4_k_m")
        self.assertEqual(cfg.voice_profile, "elian_jarvis")
        self.assertIn("ElianResearch", cfg.voice_dir.replace("/", "\\"))
        self.assertTrue(
            (Path(cfg.voice_dir) / "elian_jarvis.s2voice").is_file(),
            msg=f"missing {cfg.voice_dir}\\elian_jarvis.s2voice",
        )
        # Cloud reference IDs must never be the selection mechanism.
        self.assertNotEqual(cfg.voice_profile, "7c1a7dc37829497593ab4db29eed387c")


class FishS2ForcedUnavailableTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tts_engine_for_tests()
        reset_fish_health_for_tests()
        os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "1"

    def tearDown(self) -> None:
        os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "1"
        reset_tts_engine_for_tests()
        reset_fish_health_for_tests()

    def test_files_ready_forced_off(self) -> None:
        ok, detail = files_ready()
        self.assertFalse(ok)
        self.assertEqual(detail, "forced_unavailable")

    def test_routed_falls_back_to_piper(self) -> None:
        routed = RoutedTTS(prefer_fish=True)
        st = routed.health_check()
        self.assertEqual(st.extras.get("fish_readiness"), "UNAVAILABLE")
        self.assertIn(st.extras.get("active"), ("english_piper", "fallback_piper"))

    def test_fish_speak_fails_cleanly(self) -> None:
        fish = FishAudioTTS()
        self.assertFalse(fish.speak("hello"))


class FishS2SynthMockTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_fish_health_for_tests()
        os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "0"
        # One-shot path under test (legacy spawn-per-request).
        os.environ["ELIAN_FISH_PERSISTENT"] = "0"

    def tearDown(self) -> None:
        os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "1"
        os.environ.pop("ELIAN_FISH_PERSISTENT", None)
        reset_fish_health_for_tests()

    def test_synthesize_uses_exclusive_path(self) -> None:
        cfg = load_fish_s2_config()
        with patch("voice.fish_s2_backend.files_ready", return_value=(True, "files_ok")):
            with patch("voice.fish_s2_backend._acquire_exclusive", return_value={"policy": "exclusive"}):
                with patch("voice.fish_s2_backend._release_exclusive") as rel:
                    with patch("subprocess.Popen") as popen:
                        class _P:
                            returncode = 0

                            def communicate(self, timeout=None):
                                return ("ok", "")

                        popen.return_value = _P()
                        fd, path = tempfile.mkstemp(suffix=".wav")
                        os.close(fd)
                        try:
                            # Create minimal wav-sized file after "synth"
                            Path(path).write_bytes(b"RIFF" + b"\x00" * 2000)

                            def _fake_popen(*a, **k):
                                Path(path).write_bytes(b"RIFF" + b"\x00" * 2000)
                                return _P()

                            popen.side_effect = _fake_popen
                            ok, latency, detail = synthesize_s2_wav(
                                "hi", path, cfg=cfg, acquire_gpu=True
                            )
                            self.assertTrue(ok)
                            self.assertIn("s2_ok", detail)
                            self.assertGreaterEqual(latency, 0.0)
                            rel.assert_called()
                        finally:
                            try:
                                os.unlink(path)
                            except OSError:
                                pass

    def test_s2_cmd_uses_local_voice_profile_not_cloud_id(self) -> None:
        cfg = load_fish_s2_config()
        cloud_id = "7c1a7dc37829497593ab4db29eed387c"
        captured: list = []

        class _P:
            returncode = 0

            def communicate(self, timeout=None):
                return ("ok", "")

        def _fake_popen(cmd, *a, **k):
            captured.append(list(cmd))
            Path(cmd[cmd.index("--output") + 1]).write_bytes(b"RIFF" + b"\x00" * 2000)
            return _P()

        with patch("voice.fish_s2_backend.files_ready", return_value=(True, "files_ok")):
            with patch(
                "voice.fish_s2_backend._acquire_exclusive",
                return_value={"policy": "exclusive"},
            ):
                with patch("voice.fish_s2_backend._release_exclusive"):
                    with patch("subprocess.Popen", side_effect=_fake_popen):
                        fd, path = tempfile.mkstemp(suffix=".wav")
                        os.close(fd)
                        try:
                            ok, _lat, detail = synthesize_s2_wav(
                                "jarvis voice check",
                                path,
                                cfg=cfg,
                                acquire_gpu=True,
                            )
                            self.assertTrue(ok)
                            self.assertIn("s2_ok", detail)
                            self.assertEqual(len(captured), 1)
                            cmd = captured[0]
                            self.assertIn("--voice", cmd)
                            self.assertEqual(cmd[cmd.index("--voice") + 1], "elian_jarvis")
                            self.assertIn("--voice-dir", cmd)
                            self.assertEqual(
                                cmd[cmd.index("--voice-dir") + 1],
                                cfg.voice_dir,
                            )
                            joined = " ".join(cmd)
                            self.assertNotIn(cloud_id, joined)
                            self.assertNotIn("fish_voice_id", joined)
                        finally:
                            try:
                                os.unlink(path)
                            except OSError:
                                pass

    def test_health_ready_only_after_cuda_verified(self) -> None:
        os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "0"
        reset_fish_health_for_tests()
        with patch("voice.fish_s2_backend.files_ready", return_value=(True, "files_ok")):
            snap = health_snapshot()
            self.assertFalse(snap["ready"])
            self.assertEqual(snap["detail"], "files_ok_cuda_unverified")


if __name__ == "__main__":
    unittest.main()
