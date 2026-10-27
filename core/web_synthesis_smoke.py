# ==========================
# ELIAN — web-synthesis smoke (Phase 11.4.2)
# ==========================
"""Claim follow-up + evidence synthesis robustness — no mic."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List

from core.conversation_manager import ConversationManager
from core.factual_followup import resolve_claim_referent
from core.model_client import FakeModelClient
from core.planner import Planner
from core.response_language import detect_utterance_language, looks_like_english
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from core.web_grounding import rank_hits
from core.web_synthesis import (
    detect_question_type,
    is_useful_web_answer,
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


def _registry() -> ToolRegistry:
    registry = ToolRegistry(confirm_medium=False)
    invoked: List[Dict[str, Any]] = []

    def web_search(args):
        query = str((args or {}).get("query") or "")
        invoked.append({"tool": "web_search", "query": query})
        hits = _canada_hits()
        ranked = rank_hits(hits, query=query)
        return ToolResult.ok(
            "Znalazłem 5 wyników.",
            tool_name="web_search",
            data={
                "query": query,
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
    registry._invoked = invoked  # type: ignore[attr-defined]
    return registry


def run_web_synthesis_smoke() -> int:
    print("=== Elian web-synthesis-smoke (Phase 11.4.2) ===")
    rows = []
    failed = 0

    # 1) Canada PM WHO
    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")
        registry = _registry()
        cm = ConversationManager(
            registry=registry,
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
            memory_manager=mem,
        )
        cm.activate()
        cm.session_language = "pl"
        r1 = cm.handle("Kto jest premierem Kanady?")
        qtype = detect_question_type("Kto jest premierem Kanady?")
        ok1 = (
            qtype == "WHO"
            and "carney" in (r1.text or "").lower()
            and "nie udało" not in (r1.text or "").lower()
            and not looks_like_english(r1.text or "")
            and is_useful_web_answer(r1.text or "", question_type="WHO")
        )
        rows.append(
            {
                "case": "1_canada_who",
                "question_type": qtype,
                "fresh_web": bool((r1.data or {}).get("fresh_web")),
                "grounding_status": "SUPPORTED",
                "previous_claim": "",
                "followup_referent": "",
                "response_origin": (r1.data or {}).get("response_origin")
                or cm._turn_response_origin,
                "answer_relevant": is_useful_web_answer(
                    r1.text or "", question_type="WHO"
                ),
                "response_language": cm.session_language,
                "response": r1.text,
                "pass_": ok1,
            }
        )

        # 2) Czy to prawda?
        r2 = cm.handle("Czy to prawda?")
        focus = dict(getattr(mem.short, "focus", {}) or {})
        ref = resolve_claim_referent(focus)
        ok2 = (
            detect_question_type("Czy to prawda?") == "YES_NO"
            and ref is not None
            and (r2.text or "").lower().startswith("tak")
            and "carney" in (r2.text or "").lower()
            and "nie udało" not in (r2.text or "").lower()
        )
        rows.append(
            {
                "case": "2_czy_to_prawda",
                "question_type": "YES_NO",
                "fresh_web": False,
                "grounding_status": "PRIOR",
                "previous_claim": (ref.claim_text if ref else "")[:120],
                "followup_referent": (ref.claim_text if ref else "")[:120],
                "response_origin": (r2.data or {}).get("response_origin")
                or "WEB_SYNTHESIS",
                "answer_relevant": True,
                "response_language": cm.session_language,
                "response": r2.text,
                "pass_": ok2,
            }
        )

    # 3) broken LLM + supported evidence
    registry = _registry()
    result3 = Planner(
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
    ok3 = (
        "carney" in (result3.message or "").lower()
        and "nie udało" not in (result3.message or "").lower()
        and result3.response_origin
        in ("WEB_SYNTHESIS", "WEB_REPAIR", "WEB_EVIDENCE_FALLBACK")
    )
    rows.append(
        {
            "case": "3_broken_llm",
            "question_type": "WHO",
            "fresh_web": True,
            "grounding_status": "SUPPORTED",
            "previous_claim": "",
            "followup_referent": "",
            "response_origin": result3.response_origin,
            "answer_relevant": is_useful_web_answer(
                result3.message or "", question_type="WHO"
            ),
            "response_language": "pl",
            "response": result3.message,
            "pass_": ok3,
        }
    )

    # 4) English snippets → Polish answer
    ranked = rank_hits(_canada_hits(), query="current Prime Minister of Canada")
    ans4, origin4 = synthesize_from_evidence(
        "Kto jest premierem Kanady?", ranked, lang="pl"
    )
    ok4 = (
        detect_utterance_language(ans4) == "pl"
        and "carney" in ans4.lower()
        and not looks_like_english(ans4)
        and origin4 in ("WEB_EVIDENCE_FALLBACK", "WEB_REPAIR", "WEB_SYNTHESIS")
    )
    rows.append(
        {
            "case": "4_en_snippets_pl",
            "question_type": "WHO",
            "fresh_web": True,
            "grounding_status": "SUPPORTED",
            "previous_claim": "",
            "followup_referent": "",
            "response_origin": origin4,
            "answer_relevant": is_useful_web_answer(ans4, question_type="WHO"),
            "response_language": "pl",
            "response": ans4,
            "pass_": ok4,
        }
    )

    for row in rows:
        status = "PASS" if row["pass_"] else "FAIL"
        if not row["pass_"]:
            failed += 1
        print(
            f"[{status}] case={row['case']}\n"
            f"  question_type={row['question_type']} fresh_web={row['fresh_web']} "
            f"grounding_status={row['grounding_status']}\n"
            f"  previous_claim={row['previous_claim']!r}\n"
            f"  followup_referent={row['followup_referent']!r}\n"
            f"  response_origin={row['response_origin']} "
            f"answer_relevant={row['answer_relevant']} "
            f"response_language={row['response_language']}\n"
            f"  response={row['response']!r}"
        )

    print(f"\n=== web-synthesis-smoke: {len(rows) - failed}/{len(rows)} PASS ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_web_synthesis_smoke())
