# ==========================
# ELIAN — dry computer + read-tool speech (no Chwila / unknown hotkey)
# ==========================
from __future__ import annotations

import unittest

from core.jarvis_behavioral.harness import jarvis_session


class DryComputerReadSpeechTests(unittest.TestCase):
    def test_type_and_select_not_chwila(self):
        with jarvis_session() as (cm, _backend, log):
            cm.handle("otwórz notatnik")
            log.clear()
            typed = cm.handle("wpisz hello")
            text = getattr(typed, "text", None) or str(typed)
            self.assertTrue(any(r.get("tool") == "type_text" for r in log))
            self.assertNotIn("chwila", text.casefold())
            self.assertNotIn("sprawdzam to dokładniej", text.casefold())
            log.clear()
            sel = cm.handle("zaznacz wszystko")
            sel_text = getattr(sel, "text", None) or str(sel)
            tools = [r.get("tool") for r in log]
            self.assertTrue("hotkey" in tools or "select_all" in tools)
            self.assertNotIn("nie znam narzędzia", sel_text.casefold())

    def test_weather_schedule_reminders_spoken(self):
        with jarvis_session() as (cm, _backend, log):
            for utter, tool in (
                ("jaka pogoda", "get_weather"),
                ("co mam dziś", "get_today_schedule"),
                ("jakie mam przypomnienia", "list_reminders"),
            ):
                log.clear()
                out = cm.handle(utter)
                text = getattr(out, "text", None) or str(out)
                self.assertIn(tool, [r.get("tool") for r in log], msg=utter)
                self.assertNotIn("nie mogę potwierdzić", text.casefold(), msg=text)
                self.assertNotIn("chwila", text.casefold(), msg=text)
                self.assertGreater(len(text.strip()), 8, msg=text)


if __name__ == "__main__":
    unittest.main()
