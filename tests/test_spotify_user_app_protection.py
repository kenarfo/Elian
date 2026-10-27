"""Regression: automated suite must not close pre-existing / playing Spotify."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.app_lifecycle_guard import (
    disable_protect_user_apps,
    enable_protect_user_apps,
    mark_media_playing,
    note_test_owned_launch,
    should_block_close,
    snapshot_status,
)
from core.builtin_tools import build_default_registry
from core.tools import reset_registry_for_tests


class SpotifyUserAppProtectionTests(unittest.TestCase):
    def setUp(self):
        enable_protect_user_apps(snapshot=False)
        # Simulate user Spotify already running before suite.
        with patch(
            "core.app_lifecycle_guard._pids_for_app",
            return_value={4242},
        ):
            from core import app_lifecycle_guard as guard

            guard._PREEXISTING["spotify"] = {4242}
            guard._TEST_OWNED.clear()
            guard._MEDIA_PLAYING_APPS.clear()

    def tearDown(self):
        from core import app_lifecycle_guard as guard

        guard._PREEXISTING.clear()
        guard._TEST_OWNED.clear()
        guard._MEDIA_PLAYING_APPS.clear()
        enable_protect_user_apps(snapshot=False)
        reset_registry_for_tests(None)

    def test_block_close_preexisting_spotify(self):
        with patch("core.app_lifecycle_guard._pids_for_app", return_value={4242}):
            block, reason = should_block_close("Spotify", explicit_user_request=False)
        self.assertTrue(block)
        self.assertIn("USER_OWNED", reason)

    def test_playing_blocks_even_harder(self):
        mark_media_playing("spotify", True)
        with patch("core.app_lifecycle_guard._pids_for_app", return_value={4242}):
            block, reason = should_block_close("Spotify", explicit_user_request=False)
        self.assertTrue(block)
        self.assertEqual(reason, "USER_OWNED_PLAYING")

    def test_user_voice_still_allowed(self):
        block, reason = should_block_close("Spotify", explicit_user_request=True)
        self.assertFalse(block)
        self.assertEqual(reason, "")

    def test_tool_automated_cleanup_skips_user_spotify(self):
        registry = build_default_registry(confirm_medium=False)
        with patch("ComputerControl.close_application") as close:
            result = registry.invoke(
                "close_app",
                {"name": "Spotify", "automated": True, "media_playing": True},
                confirmed=True,
            )
        self.assertFalse(result.success)
        self.assertTrue((result.data or {}).get("protected_user_app"))
        close.assert_not_called()

    def test_tool_user_request_still_closes(self):
        registry = build_default_registry(confirm_medium=False)
        with patch(
            "ComputerControl.close_application",
            return_value="Zamykam aplikację Spotify.",
        ) as close:
            result = registry.invoke(
                "close_app",
                {"name": "Spotify"},
                confirmed=True,
            )
        self.assertTrue(result.success)
        close.assert_called_once()
        # Default allow_user_owned remains True for voice.
        self.assertTrue(close.call_args.kwargs.get("allow_user_owned", True))

    def test_test_owned_pid_may_close(self):
        note_test_owned_launch("spotify", {9999})
        with patch("core.app_lifecycle_guard._pids_for_app", return_value={9999}):
            block, reason = should_block_close("Spotify", explicit_user_request=False)
        # Only test-owned live PID — not pre-existing 4242.
        self.assertFalse(block)

    def test_suite_snapshot_leaves_preexisting(self):
        status = snapshot_status("spotify")
        self.assertEqual(status["preexisting_pids"], [4242])


if __name__ == "__main__":
    unittest.main()
