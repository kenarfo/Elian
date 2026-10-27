# ==========================
# ELIAN — definitional "czym jest X" → soft web (no Ollama)
# ==========================
from __future__ import annotations

import unittest

from core.semantic_executive import DecisionEnvelope, decide_semantics
from core.soft_goals import detect_soft_goal
from core.text_inject import _make_manager, run_one_turn


class DefineFactWebSoftTests(unittest.TestCase):
    def test_semantic_define_stays_answer_chat(self):
        """Behavioral corpus expects ANSWER; soft layer does the web work."""
        env = decide_semantics("czym jest piper tts", allow_llm=False)
        self.assertEqual(env.intent, "knowledge_explanation")
        self.assertEqual(env.mode, "ANSWER")
        self.assertFalse(env.needs_tools)

    def test_semantic_opinion_stays_chat(self):
        env = decide_semantics("co sądzisz o tym", allow_llm=False)
        self.assertEqual(env.intent, "knowledge_explanation")
        self.assertFalse(env.needs_tools)

    def test_soft_define_under_talk_lock(self):
        env = DecisionEnvelope(
            mode="ANSWER",
            intent="knowledge_explanation",
            needs_tools=False,
        )
        soft = detect_soft_goal("czym jest piper tts", envelope=env)
        self.assertIsNotNone(soft)
        self.assertEqual(soft.get("kind"), "web_search")
        self.assertIn("piper", str(soft.get("slots", {}).get("query") or "").lower())

    def test_soft_define_goal(self):
        soft = detect_soft_goal("czym jest piper tts")
        self.assertIsNotNone(soft)
        self.assertEqual(soft.get("kind"), "web_search")

    def test_inject_define_without_ollama(self):
        mgr = _make_manager(live_tools=False)
        try:
            row = run_one_turn(mgr, "czym jest piper tts", debug=False)
            text = str(row.get("FINAL_RESPONSE") or row.get("text") or "").lower()
            self.assertNotIn("chwilowo nie odpowiada", text)
            self.assertTrue(
                "skrócie" in text or "piper" in text or "lokalny" in text,
                msg=text[:240],
            )
            self.assertEqual(str(row.get("soft_kind") or ""), "web_search")
        finally:
            try:
                mgr.deactivate(reason="test_end")
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
