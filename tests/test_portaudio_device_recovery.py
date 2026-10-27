# ==========================
# ELIAN — PortAudio device recovery (-9996 / -9985)
# ==========================
from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

from voice.audio_gate import reset_audio_gate_for_tests
from voice.settings import VoiceSettings


class _FakePortAudioError(Exception):
    pass


class PortAudioDeviceRecoveryTests(unittest.TestCase):
    def setUp(self):
        reset_audio_gate_for_tests()
        import Voice as voice_mod

        self.voice = voice_mod
        self._prev_input = voice_mod._INPUT_DEVICE
        self._prev_rate = voice_mod._CAPTURE_RATE
        self._prev_settings = voice_mod._voice_settings

    def tearDown(self):
        self.voice._INPUT_DEVICE = self._prev_input
        self.voice._CAPTURE_RATE = self._prev_rate
        self.voice._voice_settings = self._prev_settings

    def test_is_portaudio_device_error_codes(self):
        self.assertTrue(
            self.voice._is_portaudio_device_error(
                _FakePortAudioError(
                    "Error opening InputStream: Invalid device [PaErrorCode -9996]"
                )
            )
        )
        self.assertTrue(
            self.voice._is_portaudio_device_error(
                _FakePortAudioError(
                    "Error opening InputStream: Device unavailable [PaErrorCode -9985]"
                )
            )
        )
        self.assertFalse(
            self.voice._is_portaudio_device_error(RuntimeError("unrelated"))
        )

    def test_record_reresolves_stale_index_instead_of_default_storm(self):
        """-9996 on stale index → re-resolve by name → open succeeds; no crash."""
        self.voice._INPUT_DEVICE = 7
        self.voice._CAPTURE_RATE = 16000
        self.voice._voice_settings = VoiceSettings(
            input_device=7,
            input_device_name="Plantronics Blackwire",
            sample_rate=16000,
            block_size=512,
        )

        opens: list = []

        class _Stream:
            def __init__(self, device):
                self.device = device

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_input_stream(**kwargs):
            device = kwargs.get("device")
            opens.append(device)
            if device == 7:
                raise _FakePortAudioError(
                    "Error opening InputStream: Invalid device [PaErrorCode -9996]"
                )
            if device == 11:
                return _Stream(device)
            raise _FakePortAudioError(
                "Error opening InputStream: Device unavailable [PaErrorCode -9985]"
            )

        with patch("builtins.print"):
            with patch.object(self.voice, "get_vad_model", return_value=MagicMock()):
                with patch.object(
                    self.voice.sd, "InputStream", side_effect=fake_input_stream
                ):
                    with patch.object(
                        self.voice,
                        "reresolve_input_device",
                        side_effect=lambda exclude=None: self._fake_reresolve(
                            exclude
                        ),
                    ):
                        # Empty queue → wait_timeout returns None without speech
                        audio = self.voice.record_until_silence(wait_timeout=0.05)

        self.assertIsNone(audio)
        self.assertEqual(opens[0], 7)
        self.assertIn(11, opens)
        # Must not open blind default (device=None) before named recovery.
        self.assertNotEqual(opens[1], None)
        self.assertEqual(self.voice._INPUT_DEVICE, 11)

    def _fake_reresolve(self, exclude=None):
        skipped = {int(x) for x in (exclude or set())}
        self.assertIn(7, skipped)
        self.voice._INPUT_DEVICE = 11
        self.voice._CAPTURE_RATE = 16000
        vs = self.voice._voice_settings
        self.voice._voice_settings = replace(vs, input_device=11)
        return 11

    def test_record_returns_none_when_all_opens_fail(self):
        """Final PortAudio failure must not raise (process stay alive)."""
        self.voice._INPUT_DEVICE = 3
        self.voice._CAPTURE_RATE = 16000
        self.voice._voice_settings = VoiceSettings(
            input_device=3,
            input_device_name="Plantronics Blackwire",
            sample_rate=16000,
            block_size=512,
        )

        def always_fail(**kwargs):
            raise _FakePortAudioError(
                "Error opening InputStream: Device unavailable [PaErrorCode -9985]"
            )

        with patch("builtins.print"):
            with patch.object(self.voice, "get_vad_model", return_value=MagicMock()):
                with patch.object(
                    self.voice.sd, "InputStream", side_effect=always_fail
                ):
                    with patch.object(
                        self.voice, "reresolve_input_device", return_value=None
                    ):
                        audio = self.voice.record_until_silence(wait_timeout=0.05)

        self.assertIsNone(audio)

    def test_resolve_barge_skips_wdm_ks_for_blocking(self):
        """Barge blocking path must not pick WDM-KS (-9999 Blocking API)."""
        devices = {
            47: {"name": "Plantronics Blackwire Mic", "hostapi": 0, "default_samplerate": 16000},
            10: {"name": "Plantronics Blackwire Mic", "hostapi": 1, "default_samplerate": 16000},
        }
        hostapis = {
            0: {"name": "Windows WDM-KS"},
            1: {"name": "Windows DirectSound"},
        }

        def query_devices(idx=None):
            if idx is None:
                return list(devices.values())
            return devices[int(idx)]

        def query_hostapis(idx=None):
            if idx is None:
                return list(hostapis.values())
            return hostapis[int(idx)]

        opened = []

        class _OkStream:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def input_stream(**kwargs):
            opened.append(kwargs.get("device"))
            # Simulate WDM-KS rejecting blocking open.
            if kwargs.get("device") == 47:
                raise _FakePortAudioError(
                    "Unanticipated host error [PaErrorCode -9999]: "
                    "'Blocking API not supported yet'"
                )
            return _OkStream()

        with patch.object(self.voice, "_list_named_devices") as list_named:
            list_named.return_value = [
                (47, devices[47], "Windows WDM-KS"),
                (10, devices[10], "Windows DirectSound"),
            ]
            with patch.object(self.voice.sd, "query_devices", side_effect=query_devices):
                with patch.object(
                    self.voice.sd, "query_hostapis", side_effect=query_hostapis
                ):
                    with patch.object(
                        self.voice.sd, "InputStream", side_effect=input_stream
                    ):
                        idx = self.voice.resolve_barge_input_device(
                            preferred_index=47,
                            name="Plantronics Blackwire",
                        )
        self.assertEqual(idx, 10)
        self.assertIn(10, opened)


if __name__ == "__main__":
    unittest.main()
