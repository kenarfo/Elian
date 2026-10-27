# ==========================
# ELIAN — Phase 11.1 grounding / negation regressions
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.conversation_focus import (
    enrich_goal_with_focus,
    is_comprehension_repair,
    update_focus,
)
from core.conversation_manager import ConversationManager
from core.intent_family import classify_intent_family
from core.agent_safety import is_acknowledgement as safety_is_ack
from core.model_client import FakeModelClient
from core.planner import Planner
from core.risk import RiskLevel
from core.text_pipeline import prepare_user_text
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from core.tool_shortlist import looks_like_pure_talk, shortlist_tools
from core.utterance import apply_mid_utterance_correction, negation_was_lost
from core.web_grounding import (
    authoritative_consensus,
    clean_web_query,
    evaluate_grounding,
    rank_hits,
    repair_current_fact_answer,
)
from memory.manager import MemoryManager


class NegationAndAckTests(unittest.TestCase):
    def test_negation_preserved_in_pipeline(self):
        for phrase in (
            "Spotify nie jest nudne.",
            "Spotify nie jest nudne, Spotify jest fajne.",
            "Nie rozumiem.",
            "Nie rozumiem, co to jest.",
            "Ale dokładnie, wytłumacz mi, bo nie rozumiem co to jest.",
        ):
            prepared = prepare_user_text(phrase)
            self.assertIn("nie", prepared.semantic_text.lower(), phrase)
            fixed, changed = apply_mid_utterance_correction(phrase)
            self.assertFalse(negation_was_lost(phrase, fixed), phrase)
            if "otwórz" not in phrase.lower():
                self.assertFalse(changed, phrase)

    def test_mid_correction_still_retargets_apps(self):
        # Bare ``nie`` = exclusion; explicit retarget markers still swap.
        text, ok = apply_mid_utterance_correction("Otwórz Spotify nie YouTube Music")
        self.assertFalse(ok)
        self.assertIn("Spotify", text)
        text2, ok2 = apply_mid_utterance_correction(
            "Otwórz Spotify znaczy YouTube Music"
        )
        self.assertTrue(ok2)
        self.assertIn("YouTube", text2)

    def test_nie_rozumiem_not_ack(self):
        info = classify_intent_family("Nie rozumiem.")
        self.assertFalse(safety_is_ack(info))
        self.assertTrue(info.slots.get("comprehension_repair") or is_comprehension_repair("Nie rozumiem."))
        self.assertTrue(is_comprehension_repair("Nie rozumiem."))
        self.assertTrue(safety_is_ack(classify_intent_family("rozumiem.")))

    def test_negated_open_zero_tools_via_cm(self):
        log = []
        registry = ToolRegistry(confirm_medium=False)

        def open_app(args):
            log.append("open_app")
            return ToolResult.ok("ok", tool_name="open_app")

        registry.register(
            Tool(
                name="open_app",
                description="open",
                handler=open_app,
                risk_level=RiskLevel.SAFE,
                parameters={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = ConversationManager(
                registry=registry,
                model=FakeModelClient([{"type": "final_answer", "message": "x"}]),
                memory_manager=mem,
            )
            cm.activate()
            result = cm.handle("Nie chcę otwierać Spotify.")
        self.assertEqual(log, [])
        self.assertIn(result.source, ("negation", "agent", "chat"))

    def test_talk_shortlist_empty(self):
        catalog = [
            {"name": n, "available": True}
            for n in (
                "web_search",
                "open_app",
                "play_media",
                "set_device_value",
                "create_reminder",
            )
        ]
        for phrase in (
            "Fajny masz głos.",
            "Spotify jest fajne.",
            "Spotify nie jest nudne, Spotify jest fajne.",
        ):
            info = classify_intent_family(phrase)
            short = shortlist_tools(
                catalog,
                goal=phrase,
                classification=info,
                talk_only=looks_like_pure_talk(phrase, info),
            )
            self.assertEqual(short, [], phrase)


class KnowledgeFollowupTests(unittest.TestCase):
    def test_nie_rozumiem_keeps_black_hole_topic(self):
        focus = update_focus(
            None,
            user_text="Co to jest czarna dziura?",
            assistant_text="Czarna dziura to obszar o bardzo silnej grawitacji.",
        )
        self.assertIn("czarna", focus["current_topic"].lower())
        enriched = enrich_goal_with_focus("Nie rozumiem.", focus)
        self.assertIn("czarna", enriched.lower())
        self.assertIn("nie zrozumiał", enriched.lower())

    def test_cm_reexplain_not_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            mem.short.update_focus(
                user_text="Co to jest czarna dziura?",
                assistant_text="Region o silnej grawitacji.",
            )
            with patch(
                "core.conversation_manager.ask_ai",
                return_value="Czarna dziura to obszar, z którego nawet światło nie ucieka.",
            ) as mocked:
                cm = ConversationManager(
                    registry=ToolRegistry(confirm_medium=False),
                    model=FakeModelClient([]),
                    memory_manager=mem,
                )
                cm.activate()
                result = cm.handle("Nie rozumiem.")
        self.assertNotEqual(result.source, "ack")
        self.assertTrue(mocked.called)
        self.assertIn("światło", (result.text or "").lower() + "swiatlo")


class WebGroundingTests(unittest.TestCase):
    def _mixed_president_results(self):
        return [
            {
                "title": "Joe Biden - Wikipedia",
                "url": "https://en.wikipedia.org/wiki/Joe_Biden",
                "snippet": "Joe Biden was the 46th president of the United States.",
                "source": "en.wikipedia.org",
            },
            {
                "title": "The White House",
                "url": "https://www.whitehouse.gov/",
                "snippet": "President Donald J. Trump",
                "source": "whitehouse.gov",
            },
            {
                "title": "Donald Trump inaugurated",
                "url": "https://www.reuters.com/world/us/trump",
                "snippet": "Donald Trump is the current US president.",
                "source": "reuters.com",
            },
        ]

    def test_ranking_prefers_authoritative_current(self):
        ranked = rank_hits(
            self._mixed_president_results(),
            query="Kto jest teraz prezydentem USA?",
        )
        self.assertGreaterEqual(ranked[0].score, ranked[-1].score)
        consensus = authoritative_consensus(ranked)
        self.assertEqual(consensus, "president:donald_trump")

    def test_repair_rejects_biden_when_consensus_trump(self):
        ranked = rank_hits(
            self._mixed_president_results(),
            query="Kto jest teraz prezydentem USA?",
        )
        bad = "Według kilku aktualnych źródeł Joe Biden jest obecnie prezydentem USA."
        fixed = repair_current_fact_answer(
            bad, ranked, query="Kto jest teraz prezydentem USA?"
        )
        self.assertIn("trump", fixed.lower())
        self.assertNotIn("biden jest obecnie", fixed.lower())

    def test_grounding_supported_vs_conflict(self):
        ranked = rank_hits(
            self._mixed_president_results(),
            query="Kto jest teraz prezydentem USA?",
        )
        good = evaluate_grounding(
            "Według aktualnych źródeł prezydentem USA jest Donald Trump.",
            ranked,
            fresh_web=True,
            query="Kto jest teraz prezydentem USA?",
        )
        self.assertTrue(good["grounded_in_web"])
        self.assertEqual(good["grounding_status"], "SUPPORTED")
        bad = evaluate_grounding(
            "Joe Biden jest obecnie prezydentem USA.",
            ranked,
            fresh_web=True,
            query="Kto jest teraz prezydentem USA?",
        )
        self.assertFalse(bad["grounded_in_web"])
        self.assertEqual(bad["grounding_status"], "CONFLICT")

    def test_no_fresh_web_means_not_grounded(self):
        ranked = rank_hits(self._mixed_president_results(), query="prezydent")
        out = evaluate_grounding(
            "Donald Trump.",
            ranked,
            fresh_web=False,
            query="Joe Biden jest prezydentem USA.",
        )
        self.assertFalse(out["grounded_in_web"])
        self.assertFalse(out["fresh_web_observation_this_turn"])

    def test_query_cleanup(self):
        cleaned = clean_web_query("Ok, inaczej. Kto jest teraz prezydentem USA?")
        self.assertEqual(cleaned.lower(), "kto jest teraz prezydentem usa")

    def test_planner_does_not_inherit_web_grounding(self):
        registry = ToolRegistry(confirm_medium=False)
        model = FakeModelClient(
            [{"type": "final_answer", "message": "Dzięki, miło słyszeć."}]
        )
        prior = [
            {
                "success": True,
                "tool": "web_search",
                "data": {
                    "query": "prezydent",
                    "results": self._mixed_president_results(),
                    "result_count": 3,
                },
            }
        ]
        result = Planner(registry, model=model).run(
            "Fajny masz głos.",
            context={
                "raw_text": "Fajny masz głos.",
                "observations": prior,
                "conversation_focus": {
                    "last_web_consensus": "president:donald_trump",
                },
            },
        )
        self.assertFalse(result.fresh_web_observation_this_turn)
        self.assertFalse(result.grounded_in_web)
        self.assertEqual(result.grounding_status, "NONE")


class KnowledgeFactualContractTests(unittest.TestCase):
    def test_knowledge_prompt_mentions_event_horizon_rules(self):
        from prompts import knowledge_system_prompt

        prompt = knowledge_system_prompt().lower()
        self.assertIn("gravity", prompt)
        self.assertIn("event horizon", prompt)


if __name__ == "__main__":
    unittest.main()
