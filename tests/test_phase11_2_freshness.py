# ==========================
# ELIAN — Phase 11.2 freshness / claim / focus regressions
# ==========================
from __future__ import annotations

import unittest

from core.conversation_focus import update_focus
from core.freshness import (
    VERIFY_NOT_CHECKED,
    analyze_freshness,
    extract_person_candidates,
    is_meta_utterance,
)
from core.intent_family import IntentFamily, classify_intent_family
from core.model_client import FakeModelClient
from core.planner import Planner
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from core.web_grounding import clean_web_query, rank_hits


def _web_registry():
    registry = ToolRegistry(confirm_medium=False)
    calls = []

    def web_search(args):
        calls.append(dict(args or {}))
        hits = [
            {
                "title": "The White House",
                "url": "https://www.whitehouse.gov/",
                "snippet": "President Donald J. Trump",
                "source": "whitehouse.gov",
            },
            {
                "title": "Joe Biden - Wikipedia",
                "url": "https://en.wikipedia.org/wiki/Joe_Biden",
                "snippet": "Joe Biden was the 46th president.",
                "source": "en.wikipedia.org",
            },
        ]
        ranked = rank_hits(hits, query=str((args or {}).get("query") or ""))
        return ToolResult.ok(
            "ok",
            tool_name="web_search",
            data={
                "query": (args or {}).get("query"),
                "results": hits,
                "ranked_results": [h.to_dict() for h in ranked],
                "result_count": 2,
                "success": True,
            },
        )

    registry.register(
        Tool(
            name="web_search",
            description="web",
            handler=web_search,
            risk_level=RiskLevel.SAFE,
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
    )
    registry._calls = calls  # type: ignore[attr-defined]
    return registry


class FreshnessDetectionTests(unittest.TestCase):
    def test_president_without_teraz_requires_web(self):
        fresh = analyze_freshness("Kto jest prezydentem USA?")
        self.assertTrue(fresh.freshness_required)
        self.assertEqual(fresh.semantic_kind, "QUESTION")
        self.assertIn("president", fresh.search_query.lower())
        info = classify_intent_family("Kto jest prezydentem USA?")
        self.assertEqual(info.family, IntentFamily.WEB)

    def test_president_with_teraz_still_web(self):
        info = classify_intent_family("Kto jest teraz prezydentem USA?")
        self.assertEqual(info.family, IntentFamily.WEB)

    def test_opinion_not_fresh(self):
        fresh = analyze_freshness("Spotify jest fajne.")
        self.assertFalse(fresh.freshness_required)
        self.assertEqual(fresh.semantic_kind, "OPINION")

    def test_flat_earth_not_fresh_web(self):
        fresh = analyze_freshness("Ziemia jest płaska.")
        self.assertTrue(fresh.is_factual_claim)
        self.assertFalse(fresh.freshness_required)

    def test_planner_president_calls_web(self):
        registry = _web_registry()
        model = FakeModelClient(
            [
                {
                    "type": "final_answer",
                    "message": "Według źródeł prezydentem jest Donald Trump.",
                }
            ]
        )
        result = Planner(registry, model=model).run("Kto jest prezydentem USA?")
        self.assertTrue(registry._calls)  # type: ignore[attr-defined]
        self.assertTrue(result.fresh_web_observation_this_turn)
        self.assertIn("trump", (result.message or "").lower())

    def test_user_claim_verified_not_echoed(self):
        registry = _web_registry()
        model = FakeModelClient(
            [
                {
                    "type": "final_answer",
                    "message": "Joe Biden jest prezydentem USA.",
                }
            ]
        )
        result = Planner(registry, model=model).run("Joe Biden jest prezydentem USA.")
        self.assertTrue(registry._calls)  # type: ignore[attr-defined]
        q = str(registry._calls[0].get("query") or "").lower()  # type: ignore[attr-defined]
        self.assertIn("president", q)
        self.assertIn("trump", (result.message or "").lower())
        self.assertNotEqual(
            (result.message or "").strip().lower(),
            "joe biden jest prezydentem usa.",
        )
        ver = result.claim_verification or {}
        self.assertTrue(ver.get("web_checked"))
        self.assertNotEqual(ver.get("status"), VERIFY_NOT_CHECKED)


class FocusHygieneTests(unittest.TestCase):
    def test_teraz_wiem_keeps_black_hole_topic(self):
        focus = update_focus(
            None,
            user_text="Co to jest czarna dziura?",
            assistant_text="Czarna dziura to obszar o silnej grawitacji.",
        )
        self.assertIn("czarna", focus["current_topic"].lower())
        focus2 = update_focus(
            focus,
            user_text="Teraz wiem.",
            assistant_text="Dobrze.",
        )
        self.assertIn("czarna", focus2["current_topic"].lower())
        self.assertTrue(is_meta_utterance("Teraz wiem."))

    def test_nie_rozumiem_keeps_topic(self):
        focus = update_focus(
            None,
            user_text="Co to jest czarna dziura?",
            assistant_text="Region o silnej grawitacji.",
        )
        focus2 = update_focus(focus, user_text="Nie rozumiem.", assistant_text="…")
        self.assertIn("czarna", focus2["current_topic"].lower())

    def test_entity_extraction_ignores_polish_function_words(self):
        people = extract_person_candidates(
            "Zgadzam Nie Chcę Jest Czarne Spotify jest fajne."
        )
        lowered = {p.lower() for p in people}
        for bad in ("nie", "jest", "chcę", "chce", "zgadzam", "czarne"):
            self.assertNotIn(bad, lowered)
        # Multi-word names still work
        people2 = extract_person_candidates("Markus Persson stworzył Minecrafta.")
        self.assertTrue(any("Markus" in p for p in people2))


class QueryCleanupFreshnessTests(unittest.TestCase):
    def test_search_query_not_dump(self):
        fresh = analyze_freshness("Kto jest prezydentem USA?")
        self.assertNotIn("\n", fresh.search_query)
        self.assertLess(len(fresh.search_query.split()), 10)
        cleaned = clean_web_query("Ok, inaczej. Kto jest prezydentem USA?")
        self.assertIn("prezydent", cleaned.lower())


if __name__ == "__main__":
    unittest.main()
