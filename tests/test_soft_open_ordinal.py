# ELIAN — soft open ordinal web result (P3 live regression)
from __future__ import annotations

import unittest
from types import SimpleNamespace

from core.soft_goals import detect_soft_goal


class SoftOpenOrdinalTests(unittest.TestCase):
    def _ctx(self):
        return SimpleNamespace(
            last_web_results=[
                {"title": "One", "url": "https://example.com/1"},
                {"title": "Two", "url": "https://example.com/2"},
                {"title": "Three", "url": "https://example.com/3"},
            ]
        )

    def test_a_drugi_opens_second(self):
        payload = detect_soft_goal("a drugi?", goal_context=self._ctx())
        self.assertIsNotNone(payload)
        self.assertEqual(payload.get("kind"), "open_result")
        self.assertEqual(payload.get("slots", {}).get("index"), 1)
        self.assertEqual(payload.get("target"), "https://example.com/2")

    def test_a_drugi_under_talk_lock(self):
        from types import SimpleNamespace

        env = SimpleNamespace(
            mode="TALK",
            needs_tools=False,
            intent="general_conversation",
            utterance_class="REFERENCE",
        )
        payload = detect_soft_goal(
            "a drugi?", goal_context=self._ctx(), envelope=env
        )
        self.assertEqual(payload.get("kind"), "open_result")
        self.assertEqual(payload.get("slots", {}).get("index"), 1)

    def test_otworz_drugi_wynik(self):
        payload = detect_soft_goal("otwórz drugi wynik", goal_context=self._ctx())
        self.assertEqual(payload.get("kind"), "open_result")
        self.assertEqual(payload.get("slots", {}).get("index"), 1)

    def test_first_still_works(self):
        payload = detect_soft_goal("otwórz pierwszy wynik", goal_context=self._ctx())
        self.assertEqual(payload.get("kind"), "open_result")
        self.assertEqual(payload.get("slots", {}).get("index"), 0)


if __name__ == "__main__":
    unittest.main()
