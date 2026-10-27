# ==========================
# ELIAN — preference ack must not use HA device speech
# ==========================
from __future__ import annotations

import unittest

from core.ha_speech import speak_ha_result
from core.jarvis_behavioral.harness import jarvis_session
from core.tool_result import ToolResult


class PreferenceAckSpeechTests(unittest.TestCase):
    def test_speak_ha_ignores_set_preference(self):
        result = ToolResult.ok(
            "ok",
            tool_name="set_preference",
            data={"key": "music.default_app", "value": "Spotify"},
        )
        self.assertEqual(speak_ha_result(result), "")

    def test_remember_spotify_not_device_on(self):
        with jarvis_session() as (cm, _backend, log):
            out = cm.handle("zapamiętaj że wolę Spotify")
            text = getattr(out, "text", None) or str(out)
            self.assertTrue(any(r.get("tool") == "set_preference" for r in log))
            lowered = text.casefold()
            self.assertNotIn("urządzenie", lowered)
            self.assertNotIn("włączone", lowered)
            self.assertTrue(
                "zapamięt" in lowered or "spotify" in lowered or "wolisz" in lowered,
                msg=text,
            )


if __name__ == "__main__":
    unittest.main()
