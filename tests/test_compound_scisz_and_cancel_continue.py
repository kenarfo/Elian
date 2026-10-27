# ==========================
# ELIAN — compound light+volume + cancel then kontynuuj
# ==========================
from __future__ import annotations

import unittest

from core.intent_family import _is_compound_intent
from core.jarvis_behavioral.harness import jarvis_session
from core.soft_goals import detect_soft_goal
from core.utterance_class import _EXPECTANT_CONTINUE


class CompoundAndCancelContinueTests(unittest.TestCase):
    def test_scisz_is_compound_second_action(self):
        self.assertTrue(_is_compound_intent("zgaś lampę i ścisz"))
        self.assertTrue(_is_compound_intent("zgaś lampę i ciszej"))
        self.assertTrue(_is_compound_intent("wyłącz światło i przycisz"))
        soft = detect_soft_goal("zgaś lampę i ścisz")
        self.assertIsNone(soft)
        self.assertIsNone(detect_soft_goal("wyłącz światło i przycisz"))

    def test_kontynuuj_is_expectant(self):
        self.assertTrue(_EXPECTANT_CONTINUE.match("kontynuuj"))

    def test_compound_runs_both_tools(self):
        with jarvis_session() as (cm, _backend, log):
            out = cm.handle("zgaś lampę i ścisz")
            tools = [r.get("tool") for r in log]
            self.assertIn("turn_off_device", tools)
            self.assertTrue(
                any(t in tools for t in ("volume_down", "set_volume")),
                msg=f"tools={tools} text={getattr(out, 'text', out)}",
            )

    def test_compound_wylacz_przycisz_runs_both(self):
        with jarvis_session() as (cm, _backend, log):
            out = cm.handle("wyłącz światło i przycisz")
            tools = [r.get("tool") for r in log]
            self.assertIn("turn_off_device", tools)
            self.assertTrue(
                any(t in tools for t in ("volume_down", "set_volume")),
                msg=f"tools={tools} text={getattr(out, 'text', out)}",
            )

    def test_cancel_then_kontynuuj_asks(self):
        with jarvis_session() as (cm, _backend, log):
            cm.handle("otwórz Chrome")
            cm.handle("anuluj")
            log.clear()
            out = cm.handle("kontynuuj")
            text = (getattr(out, "text", None) or str(out)).casefold()
            tools = [r.get("tool") for r in log]
            self.assertFalse(tools, msg=tools)
            self.assertNotIn("jasne, słucham", text)
            self.assertIn("zrobić", text)


if __name__ == "__main__":
    unittest.main()
