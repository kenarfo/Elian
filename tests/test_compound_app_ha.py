# ==========================
# ELIAN — compound APP close + HA light off
# ==========================
from __future__ import annotations

import unittest

from core.compound_plan import parse_compound_goal
from core.intent_family import classify_intent_family
from core.text_inject import _make_manager, run_one_turn


class CompoundAppHaTests(unittest.TestCase):
    def test_compound_chrome_and_light(self):
        plan = parse_compound_goal("zamknij chrome i zgaś lampę")
        self.assertTrue(plan.detected)
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.steps[0].family, "APP")
        self.assertEqual(plan.steps[1].family, "SMART_HOME")
        info = classify_intent_family("zamknij chrome i zgaś lampę")
        self.assertTrue(info.slots.get("compound"))
        self.assertFalse(info.slots.get("needs_target_disambiguation"))

    def test_inject_compound_executes_both(self):
        mgr = _make_manager(live_tools=False)
        try:
            row = run_one_turn(mgr, "zamknij chrome i zgaś lampę", debug=False)
            tools = row.get("TOOL") or []
            names = [str(t.get("tool") or "") for t in tools if isinstance(t, dict)]
            text = str(row.get("FINAL_RESPONSE") or row.get("text") or "").lower()
            self.assertIn("close_app", names)
            self.assertIn("turn_off_device", names)
            self.assertNotIn("masz na myśli", text)
            self.assertNotIn("czy urządzenie", text)
        finally:
            try:
                mgr.deactivate(reason="test_end")
            except Exception:
                pass

    def test_odetnij_zasilanie_soft(self):
        mgr = _make_manager(live_tools=False)
        try:
            run_one_turn(mgr, "włącz lampę", debug=False)
            row = run_one_turn(mgr, "odetnij zasilanie lampy", debug=False)
            text = str(row.get("FINAL_RESPONSE") or row.get("text") or "").lower()
            self.assertEqual(str(row.get("soft_kind") or ""), "light_off")
            self.assertNotIn("nic nie robię", text)
        finally:
            try:
                mgr.deactivate(reason="test_end")
            except Exception:
                pass

    def test_sciemnij_totalnie_is_off(self):
        from core.soft_goals import detect_soft_goal

        soft = detect_soft_goal("ściemnij totalnie")
        self.assertEqual(soft and soft.get("kind"), "light_off")
        mgr = _make_manager(live_tools=False)
        try:
            run_one_turn(mgr, "włącz lampę", debug=False)
            row = run_one_turn(mgr, "ściemnij totalnie", debug=False)
            self.assertEqual(str(row.get("soft_kind") or ""), "light_off")
        finally:
            try:
                mgr.deactivate(reason="test_end")
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
