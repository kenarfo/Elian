# ==========================
# ELIAN — home overview + prepare-tomorrow briefing routing
# ==========================
from __future__ import annotations

import unittest

from core.jarvis_behavioral.harness import jarvis_session
from core.read_intents import looks_home_overview
from core.situation_briefing import looks_situation_briefing


class HomeBriefingRoutingTests(unittest.TestCase):
    def test_detectors(self):
        self.assertTrue(looks_home_overview("co słychać w domu"))
        self.assertTrue(looks_home_overview("jak tam w domu"))
        self.assertTrue(looks_situation_briefing("przygotuj mnie na jutro"))
        self.assertTrue(looks_situation_briefing("ogarnij mi jutro"))
        self.assertTrue(looks_situation_briefing("jaka jest sytuacja"))

    def test_home_status_uses_summary(self):
        with jarvis_session() as (cm, _backend, log):
            out = cm.handle("co słychać w domu")
            text = getattr(out, "text", None) or str(out)
            tools = [r.get("tool") for r in log]
            self.assertIn("get_home_summary", tools)
            self.assertNotIn("jasne, słucham", text.casefold())
            self.assertTrue("lampa" in text.casefold() or "włącz" in text.casefold())

    def test_prepare_tomorrow_briefing(self):
        with jarvis_session() as (cm, _backend, log):
            out = cm.handle("przygotuj mnie na jutro")
            text = getattr(out, "text", None) or str(out)
            tools = [r.get("tool") for r in log]
            self.assertIn("get_situation_briefing", tools)
            self.assertNotIn("jasne, słucham", text.casefold())
            self.assertTrue(len(text.strip()) > 12)


if __name__ == "__main__":
    unittest.main()
