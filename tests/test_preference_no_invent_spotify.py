# ==========================
# ELIAN — preference must not invent Spotify
# ==========================
from __future__ import annotations

import unittest

from core.intent_family import IntentFamily, classify_intent_family
from core.jarvis_behavioral.harness import jarvis_session


class PreferenceNoInventSpotifyTests(unittest.TestCase):
    def test_theme_pref_not_music_family(self):
        info = classify_intent_family("zapamiętaj że wolę ciemny motyw")
        self.assertNotEqual(info.family, IntentFamily.PREFERENCE)

    def test_spotify_pref_still_works(self):
        info = classify_intent_family("zapamiętaj że wolę Spotify")
        self.assertEqual(info.family, IntentFamily.PREFERENCE)
        self.assertEqual(info.slots.get("preference_value"), "Spotify")

    def test_theme_pref_does_not_set_spotify(self):
        with jarvis_session() as (cm, _backend, log):
            out = cm.handle("zapamiętaj że wolę ciemny motyw")
            text = getattr(out, "text", None) or str(out)
            for row in log:
                if row.get("tool") == "set_preference":
                    args = row.get("arguments") or {}
                    self.assertNotEqual(
                        str(args.get("value") or "").casefold(),
                        "spotify",
                        msg=args,
                    )
                    self.assertNotEqual(args.get("key"), "music.default_app")
            self.assertNotIn("spotify", text.casefold())


if __name__ == "__main__":
    unittest.main()
