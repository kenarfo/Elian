# ==========================
# ELIAN — dry COMPUTER tools + summarize after open
# ==========================
from __future__ import annotations

import unittest

from core.text_inject import _make_manager, run_one_turn


class DryComputerAndSummarizeTests(unittest.TestCase):
    def test_type_text_after_notepad(self):
        mgr = _make_manager(live_tools=False)
        try:
            run_one_turn(mgr, "otwórz notatnik", debug=False)
            row = run_one_turn(mgr, "wpisz cześć", debug=False)
            tools = [str(t.get("tool") or "") for t in (row.get("TOOL") or []) if isinstance(t, dict)]
            text = str(row.get("FINAL_RESPONSE") or row.get("text") or "").lower()
            self.assertIn("type_text", tools)
            self.assertNotIn("nie znam narzędzia", text)
            self.assertNotIn("nie udało mi się wpisać", text)
        finally:
            try:
                mgr.deactivate(reason="test_end")
            except Exception:
                pass

    def test_select_all_uses_hotkey(self):
        mgr = _make_manager(live_tools=False)
        try:
            run_one_turn(mgr, "otwórz notatnik", debug=False)
            row = run_one_turn(mgr, "zaznacz wszystko", debug=False)
            tools = [str(t.get("tool") or "") for t in (row.get("TOOL") or []) if isinstance(t, dict)]
            text = str(row.get("FINAL_RESPONSE") or row.get("text") or "").lower()
            self.assertTrue("hotkey" in tools or "select_all" in tools)
            self.assertNotIn("nie znam narzędzia", text)
        finally:
            try:
                mgr.deactivate(reason="test_end")
            except Exception:
                pass

    def test_summarize_after_open_not_otwieram(self):
        mgr = _make_manager(live_tools=False)
        try:
            run_one_turn(mgr, "poszukaj piper tts", debug=False)
            run_one_turn(mgr, "otwórz pierwszy", debug=False)
            row = run_one_turn(mgr, "streść to", debug=False)
            text = str(row.get("FINAL_RESPONSE") or row.get("text") or "").lower()
            self.assertEqual(str(row.get("soft_kind") or ""), "summarize_web")
            self.assertNotEqual(text.strip().rstrip("."), "otwieram")
            self.assertTrue(
                "skrócie" in text or "piper" in text or "lokalny" in text,
                msg=text[:200],
            )
        finally:
            try:
                mgr.deactivate(reason="test_end")
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
