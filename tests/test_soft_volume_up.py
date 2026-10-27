# ==========================
# ELIAN — soft volume up (symmetric with ciszej)
# ==========================
from __future__ import annotations

import unittest

from core.jarvis_behavioral.harness import jarvis_session
from core.soft_goals import detect_soft_goal


class SoftVolumeUpTests(unittest.TestCase):
    def test_phrases_detect_volume_up(self):
        for phrase in (
            "podgłośnij",
            "głośniej",
            "trochę głośniej",
            "zwiększ głośność",
            "podkręć decybele trochę",
        ):
            soft = detect_soft_goal(phrase)
            self.assertIsNotNone(soft, msg=phrase)
            self.assertEqual(soft.get("kind"), "volume_up", msg=phrase)

    def test_talk_locked_decybele_still_acts(self):
        from core.semantic_executive import decide_semantics

        env = decide_semantics("podkręć decybele trochę", allow_llm=False)
        soft = detect_soft_goal("podkręć decybele trochę", envelope=env)
        self.assertIsNotNone(soft)
        self.assertEqual(soft.get("kind"), "volume_up")

    def test_session_podglosnij_acts(self):
        with jarvis_session() as (cm, _backend, log):
            out = cm.handle("podgłośnij")
            tools = [r.get("tool") for r in log]
            text = (getattr(out, "text", None) or str(out)).casefold()
            self.assertIn("volume_up", tools)
            self.assertNotIn("jasne, słucham", text)
            log.clear()
            out2 = cm.handle("nie aż tak")
            tools2 = [r.get("tool") for r in log]
            self.assertIn("volume_down", tools2)

    def test_session_decybele_acts(self):
        with jarvis_session() as (cm, _backend, log):
            out = cm.handle("podkręć decybele trochę")
            tools = [r.get("tool") for r in log]
            text = (getattr(out, "text", None) or str(out)).casefold()
            self.assertIn("volume_up", tools)
            self.assertNotIn("jasne, słucham", text)


if __name__ == "__main__":
    unittest.main()
