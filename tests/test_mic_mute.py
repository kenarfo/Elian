# ==========================
# ELIAN — capture mute helpers
# ==========================
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class MicMuteTests(unittest.TestCase):
    def test_ensure_unmutes_when_muted(self):
        from voice import mic_mute

        with patch.object(mic_mute, "get_capture_mute_state") as get_state:
            with patch.object(mic_mute, "set_capture_mute") as set_mute:
                get_state.side_effect = [
                    (True, "muted=1 level=0.3"),
                    (False, "muted=0 level=0.3"),
                ]
                set_mute.return_value = (True, "set_mute=0")
                out = mic_mute.ensure_capture_unmuted(auto_unmute=True)
        self.assertTrue(out["unmuted"])
        set_mute.assert_called_once_with(False)

    def test_ensure_warn_only_when_auto_disabled(self):
        from voice import mic_mute

        with patch.object(
            mic_mute, "get_capture_mute_state", return_value=(True, "muted=1")
        ):
            with patch.object(mic_mute, "set_capture_mute") as set_mute:
                out = mic_mute.ensure_capture_unmuted(auto_unmute=False)
        self.assertFalse(out["unmuted"])
        self.assertEqual(out.get("action"), "warn_only")
        set_mute.assert_not_called()

    def test_ensure_noop_when_already_open(self):
        from voice import mic_mute

        with patch.object(
            mic_mute, "get_capture_mute_state", return_value=(False, "muted=0")
        ):
            with patch.object(mic_mute, "set_capture_mute") as set_mute:
                out = mic_mute.ensure_capture_unmuted(auto_unmute=True)
        self.assertFalse(out["unmuted"])
        set_mute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
