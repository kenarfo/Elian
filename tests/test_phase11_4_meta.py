# ==========================
# ELIAN — Phase 11.4 meta / compound / false-premise / web synth
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.conversation_manager import ConversationManager
from core.freshness import analyze_freshness
from core.meta_ack import analyze_meta, is_pure_meta_ack, split_meta_compound
from core.model_client import FakeModelClient
from core.planner import Planner
from core.response_finalize import finalize_response, looks_like_tool_status
from core.response_language import looks_like_english
from core.role_semantics import analyze_role_request, false_premise_reply
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from core.web_grounding import clean_web_query, rank_hits
from memory.manager import MemoryManager


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
            }
        ]
        ranked = rank_hits(hits, query=str((args or {}).get("query") or ""))
        return ToolResult.ok(
            "Znalazłem 5 wyników.",
            tool_name="web_search",
            data={
                "query": (args or {}).get("query"),
                "results": hits,
                "ranked_results": [h.to_dict() for h in ranked],
                "result_count": 5,
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


class SemanticMetaTests(unittest.TestCase):
    def test_okej_to_okej_to_teraz_wiem(self):
        a = analyze_meta("Okej, to okej, to teraz wiem.")
        self.assertTrue(a.is_meta_ack)
        self.assertTrue(a.suppress_previous_answer_replay)

    def test_okej_juz_teraz_wiem(self):
        self.assertTrue(is_pure_meta_ack("Okej, już teraz wiem."))
        self.assertEqual(analyze_meta("Okej, już teraz wiem.").kind, "META_ACK")

    def test_nadal_nie_rozumiem_not_ack(self):
        a = analyze_meta("Nie, nadal nie rozumiem.")
        self.assertTrue(a.is_repair)
        self.assertFalse(a.is_meta_ack)

    def test_compound_wiem_wiem_uk_president(self):
        kind, rest = split_meta_compound(
            "Okej, wiem, wiem. Ale kto jest prezydentem Wielkiej Brytanii?"
        )
        self.assertEqual(kind, "compound")
        self.assertIn("prezydentem", rest.lower())
        self.assertNotIn("wiem", rest.lower())
        fresh = analyze_freshness(
            "Okej, wiem, wiem. Ale kto jest prezydentem Wielkiej Brytanii?"
        )
        self.assertFalse(fresh.is_meta)
        self.assertNotIn("wiem", (fresh.search_query or "").lower())

    def test_compound_premier(self):
        kind, rest = split_meta_compound(
            "Teraz wiem, ale kto jest premierem Wielkiej Brytanii?"
        )
        self.assertEqual(kind, "compound")
        fresh = analyze_freshness(
            "Teraz wiem, ale kto jest premierem Wielkiej Brytanii?"
        )
        self.assertTrue(fresh.freshness_required)
        self.assertIn("prime minister", fresh.search_query.lower())
        role = analyze_role_request(rest)
        self.assertEqual(role.requested_role, "prime_minister")

    def test_compound_usa_after_rozumiem(self):
        kind, rest = split_meta_compound(
            "Już rozumiem. A kto jest prezydentem USA?"
        )
        self.assertEqual(kind, "compound")
        self.assertIn("usa", rest.lower())


class QueryCleanupTests(unittest.TestCase):
    def test_no_wiem_in_clean_query(self):
        cleaned = clean_web_query(
            "current wiem, wiem. ale kto jest prezydentem wielkiej brytanii"
        )
        self.assertNotIn("wiem", cleaned.lower())
        self.assertNotIn("okej", cleaned.lower())


class FalsePremiseTests(unittest.TestCase):
    def test_uk_president(self):
        role = analyze_role_request("Kto jest prezydentem Wielkiej Brytanii?")
        self.assertFalse(role.role_exists)
        self.assertEqual(role.requested_role, "president")
        self.assertIn("head_of_state", role.closest_roles)
        reply = false_premise_reply(role, lang="pl")
        self.assertIn("nie ma prezydenta", reply.lower())
        self.assertNotIn("karol", reply.lower())  # no silent monarch-as-president

    def test_planner_false_premise_no_web(self):
        registry = _web_registry()
        result = Planner(
            registry,
            model=FakeModelClient(
                [
                    {
                        "type": "final_answer",
                        "message": "Prezydentem Wielkiej Brytanii jest król Karol III.",
                    }
                ]
            ),
        ).run(
            "Kto jest prezydentem Wielkiej Brytanii?",
            context={"response_language": "pl", "session_language": "pl"},
        )
        self.assertEqual(result.source, "false_premise")
        self.assertEqual(registry._calls, [])
        self.assertIn("nie ma prezydenta", (result.message or "").lower())


class WebSynthNoRepeatTests(unittest.TestCase):
    def test_identical_second_search_synthesizes(self):
        registry = _web_registry()
        # First call succeeds via freshness force; model then asks identical search / clarify.
        result = Planner(
            registry,
            model=FakeModelClient(
                [
                    {
                        "type": "clarification",
                        "question": "The current president of the United States is Donald Trump.",
                    }
                ]
            ),
        ).run(
            "Kto jest prezydentem USA?",
            context={"response_language": "pl", "session_language": "pl"},
        )
        self.assertEqual(len(registry._calls), 1)
        self.assertFalse(result.needs_clarification)
        msg = (result.message or "").lower()
        self.assertNotIn("znalazłem 5", msg)
        self.assertNotIn("the current president", msg)
        self.assertIn("trump", msg)

    def test_tool_status_not_final(self):
        self.assertTrue(looks_like_tool_status("Znalazłem 5 wyników."))
        out, origin = finalize_response(
            "Znalazłem 5 wyników.",
            lang="pl",
            kind="tool_fallback",
        )
        self.assertNotIn("5 wyników", out.lower())

    def test_finalize_all_paths_polish(self):
        spoken, origin = finalize_response(
            "The current president of the United States is Donald Trump.",
            lang="pl",
            kind="ack",
        )
        self.assertEqual(spoken, "Okej.")
        self.assertEqual(origin, "META_ACK")


class CmMetaHardwareTests(unittest.TestCase):
    def test_cm_meta_no_planner_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = ConversationManager(
                registry=ToolRegistry(confirm_medium=False),
                model=FakeModelClient(
                    [
                        {
                            "type": "final_answer",
                            "message": (
                                "The current president of the United States "
                                "is Donald Trump."
                            ),
                        }
                    ]
                ),
                memory_manager=mem,
            )
            cm.activate()
            cm.last_assistant = (
                "The current president of the United States is Donald Trump."
            )
            for phrase in (
                "Okej, to okej, to teraz wiem.",
                "Okej, już teraz wiem.",
            ):
                result = cm.handle(phrase)
                self.assertEqual(result.source, "ack", phrase)
                self.assertNotIn("trump", (result.text or "").lower())
                self.assertNotIn("president", (result.text or "").lower())
                self.assertFalse(looks_like_english(result.text or ""))

    def test_cm_repair_not_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            mem.short.update_focus(
                user_text="Co to jest czarna dziura?",
                assistant_text="Obszar o silnej grawitacji.",
            )
            with patch(
                "core.conversation_manager.ask_ai",
                return_value="Czarna dziura to obszar, z którego światło nie ucieka.",
            ):
                cm = ConversationManager(
                    registry=ToolRegistry(confirm_medium=False),
                    model=FakeModelClient([]),
                    memory_manager=mem,
                )
                cm.activate()
                result = cm.handle("Nie, nadal nie rozumiem.")
            self.assertNotEqual(result.source, "ack")
            self.assertIn("czarna", (result.text or "").lower())

    def test_compound_uk_president_false_premise(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            registry = _web_registry()
            cm = ConversationManager(
                registry=registry,
                model=FakeModelClient(
                    [
                        {
                            "type": "final_answer",
                            "message": "Prezydentem jest Karol III.",
                        }
                    ]
                ),
                memory_manager=mem,
            )
            cm.activate()
            result = cm.handle(
                "Okej, wiem, wiem. Ale kto jest prezydentem Wielkiej Brytanii?"
            )
            self.assertEqual(registry._calls, [])
            self.assertIn("nie ma prezydenta", (result.text or "").lower())


if __name__ == "__main__":
    unittest.main()
