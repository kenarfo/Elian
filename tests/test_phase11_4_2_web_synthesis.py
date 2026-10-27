# ==========================
# ELIAN — Phase 11.4.2 web synthesis + claim follow-up
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.conversation_manager import ConversationManager
from core.factual_followup import (
    is_claim_verification_followup,
    resolve_claim_referent,
    verification_reply_from_referent,
)
from core.model_client import FakeModelClient
from core.planner import Planner
from core.response_finalize import is_forbidden_web_final
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from core.web_grounding import rank_hits
from core.web_synthesis import (
    detect_question_type,
    extract_office_holder_name,
    is_generic_failure,
    is_raw_snippet_dump,
    is_useful_web_answer,
    repair_web_answer,
    synthesize_from_evidence,
)
from memory.manager import MemoryManager


def _canada_hits():
    return [
        {
            "title": "Mark Carney - Wikipedia",
            "url": "https://en.wikipedia.org/wiki/Mark_Carney",
            "snippet": "Mark Carney is a Canadian economist.",
            "source": "en.wikipedia.org",
        },
        {
            "title": "Mark Carney - Prime Minister of Canada",
            "url": "https://www.pm.gc.ca/en",
            "snippet": "The Prime Minister of Canada is Mark Carney.",
            "source": "pm.gc.ca",
        },
        {
            "title": "Prime Minister of Canada",
            "url": "https://en.wikipedia.org/wiki/Prime_Minister_of_Canada",
            "snippet": (
                "The prime minister of Canada is the head of government of Canada."
            ),
            "source": "en.wikipedia.org",
        },
    ]


def _canada_registry(broken_model_msg: str = ""):
    registry = ToolRegistry(confirm_medium=False)
    calls = []

    def web_search(args):
        calls.append(dict(args or {}))
        hits = _canada_hits()
        ranked = rank_hits(hits, query=str((args or {}).get("query") or ""))
        return ToolResult.ok(
            "Znalazłem 5 wyników.",
            tool_name="web_search",
            data={
                "query": (args or {}).get("query"),
                "results": hits,
                "ranked_results": [h.to_dict() for h in ranked],
                "result_count": len(hits),
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


class ClaimFollowupTests(unittest.TestCase):
    def test_detect_czy_to_prawda(self):
        self.assertTrue(is_claim_verification_followup("Czy to prawda?"))
        self.assertTrue(is_claim_verification_followup("Na pewno?"))
        self.assertTrue(is_claim_verification_followup("Serio?"))
        self.assertFalse(is_claim_verification_followup("Kto jest premierem?"))

    def test_resolve_and_reply(self):
        focus = {
            "current_topic": "premier UK",
            "last_answer_summary": (
                "Obecnym premierem Wielkiej Brytanii jest Keir Starmer."
            ),
            "last_web_query": "current UK prime minister",
            "last_web_consensus": "",
            "referenced_people": ["Keir Starmer"],
        }
        ref = resolve_claim_referent(focus)
        self.assertIsNotNone(ref)
        self.assertTrue(ref.has_web_evidence)
        reply = verification_reply_from_referent(ref, lang="pl")
        self.assertTrue(reply.lower().startswith("tak"))
        self.assertIn("starmer", reply.lower())

    def test_cm_czy_to_prawda(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            mem.short.update_focus(
                user_text="Kto jest premierem Wielkiej Brytanii?",
                assistant_text=(
                    "Obecnym premierem Wielkiej Brytanii jest Keir Starmer."
                ),
                web_query="current Prime Minister of the United Kingdom",
                web_consensus="",
            )
            cm = ConversationManager(
                registry=ToolRegistry(confirm_medium=False),
                model=FakeModelClient([]),
                memory_manager=mem,
            )
            cm.activate()
            cm.session_language = "pl"
            result = cm.handle("Czy to prawda?")
            self.assertNotIn("nie udało", (result.text or "").lower())
            self.assertIn("starmer", (result.text or "").lower())
            self.assertTrue((result.text or "").lower().startswith("tak"))


class WebSynthesisTests(unittest.TestCase):
    def test_canada_who_from_evidence(self):
        ranked = rank_hits(_canada_hits(), query="current Prime Minister of Canada")
        name = extract_office_holder_name(
            ranked, query="Kto jest premierem Kanady?"
        )
        self.assertEqual(name, "Mark Carney")
        ans, origin = synthesize_from_evidence(
            "Kto jest premierem Kanady?", ranked, lang="pl"
        )
        self.assertIn("carney", ans.lower())
        self.assertIn("kanady", ans.lower())
        self.assertTrue(is_useful_web_answer(ans, question_type="WHO"))
        self.assertFalse(is_raw_snippet_dump(ans))
        self.assertIn(origin, ("WEB_EVIDENCE_FALLBACK", "WEB_REPAIR", "WEB_SYNTHESIS"))

    def test_definition_snippet_not_useful(self):
        bad = (
            "Według en.wikipedia.org: The prime minister of Canada is the "
            "head of government of Canada."
        )
        self.assertTrue(is_raw_snippet_dump(bad))
        self.assertFalse(is_useful_web_answer(bad, question_type="WHO"))
        self.assertTrue(is_forbidden_web_final(bad))

    def test_generic_failure_forbidden(self):
        bad = (
            "Nie udało mi się teraz dobrze odpowiedzieć. "
            "Spróbuj zapytać jeszcze raz."
        )
        self.assertTrue(is_generic_failure(bad))
        self.assertTrue(is_forbidden_web_final(bad))

    def test_repair_replaces_generic_on_supported(self):
        ranked = rank_hits(_canada_hits(), query="current Prime Minister of Canada")
        fixed, origin = repair_web_answer(
            "Nie udało mi się teraz dobrze odpowiedzieć. Spróbuj zapytać jeszcze raz.",
            "Kto jest premierem Kanady?",
            ranked,
            lang="pl",
            grounding_status="SUPPORTED",
        )
        self.assertIn("carney", fixed.lower())
        self.assertNotIn("nie udało", fixed.lower())
        self.assertIn(origin, ("WEB_REPAIR", "WEB_EVIDENCE_FALLBACK"))

    def test_planner_supported_not_generic(self):
        registry = _canada_registry()
        result = Planner(
            registry,
            model=FakeModelClient(
                [
                    {
                        "type": "final_answer",
                        "message": (
                            "Według en.wikipedia.org: The prime minister of Canada "
                            "is the head of government of Canada."
                        ),
                    }
                ]
            ),
        ).run(
            "Kto jest premierem Kanady?",
            context={"response_language": "pl", "session_language": "pl"},
        )
        self.assertGreaterEqual(len(registry._calls), 1)
        msg = (result.message or "").lower()
        self.assertNotIn("nie udało", msg)
        self.assertNotIn("head of government", msg)
        self.assertIn("carney", msg)
        self.assertIn(
            result.response_origin,
            ("WEB_SYNTHESIS", "WEB_REPAIR", "WEB_EVIDENCE_FALLBACK"),
        )

    def test_planner_broken_llm_uses_evidence(self):
        registry = _canada_registry()
        result = Planner(
            registry,
            model=FakeModelClient(
                [
                    {
                        "type": "final_answer",
                        "message": (
                            "Nie udało mi się teraz dobrze odpowiedzieć. "
                            "Spróbuj zapytać jeszcze raz."
                        ),
                    }
                ]
            ),
        ).run(
            "Kto jest premierem Kanady?",
            context={"response_language": "pl", "session_language": "pl"},
        )
        self.assertIn("carney", (result.message or "").lower())
        self.assertNotIn("nie udało", (result.message or "").lower())

    def test_question_type_who(self):
        self.assertEqual(detect_question_type("Kto jest premierem Kanady?"), "WHO")
        self.assertEqual(detect_question_type("Czy to prawda?"), "YES_NO")

    def test_generic_titles_prefer_snippet_briefs(self):
        from core.web_synthesis import evidence_briefs

        hits = [
            {
                "title": "Wynik o: piper tts",
                "url": "https://example.com/a",
                "snippet": (
                    "Piper TTS to lokalny silnik syntezy mowy oparty o modele ONNX."
                ),
            },
            {
                "title": "Drugi wynik",
                "url": "https://example.com/b",
                "snippet": "Dokumentacja Piper opisuje szybkie wnioskowanie CPU.",
            },
        ]
        briefs = evidence_briefs(hits, max_items=2)
        self.assertEqual(len(briefs), 2)
        self.assertIn("Piper TTS", briefs[0])
        self.assertNotIn("Wynik o:", briefs[0])
        self.assertNotEqual(briefs[1].lower(), "drugi wynik")
        self.assertIn("Dokumentacja Piper", briefs[1])

        ranked = rank_hits(hits, query="czym jest piper tts")
        ans, _origin = synthesize_from_evidence(
            "czym jest piper tts", ranked, lang="pl"
        )
        low = (ans or "").lower()
        self.assertTrue(ans)
        self.assertNotIn("wynik o:", low)
        self.assertNotIn("drugi wynik", low)
        self.assertIn("piper", low)
        self.assertIn("skrócie", low)

if __name__ == "__main__":
    unittest.main()
