# ==========================
# ELIAN — APP ``nie`` exclusion regression
# ==========================
from __future__ import annotations

import unittest

from core.intent_family import _find_app_name, classify_intent_family


class AppNieExclusionTests(unittest.TestCase):
    def test_find_app_prefers_target_before_nie(self):
        self.assertEqual(
            _find_app_name("Otwórz Spotify nie YouTube Music"),
            "Spotify",
        )

    def test_classify_app_slot_is_spotify(self):
        info = classify_intent_family("Otwórz Spotify nie YouTube Music")
        self.assertEqual(info.family.value, "APP")
        self.assertEqual(info.slots.get("app_name"), "Spotify")

    def test_single_app_still_works(self):
        self.assertEqual(_find_app_name("odpal spotify"), "Spotify")
        self.assertEqual(_find_app_name("otwórz youtube music"), "YouTube Music")


if __name__ == "__main__":
    unittest.main()
