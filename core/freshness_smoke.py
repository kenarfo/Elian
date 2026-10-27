# ==========================
# ELIAN — freshness smoke (Phase 11.2)
# ==========================
"""Offline freshness / claim / focus smoke — no mic."""

from __future__ import annotations

from typing import Any, Dict, List

from core.conversation_focus import update_focus
from core.freshness import analyze_freshness, is_meta_utterance
from core.model_client import FakeModelClient
from core.planner import Planner
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from core.web_grounding import rank_hits


def _registry() -> ToolRegistry:
    registry = ToolRegistry(confirm_medium=False)
    invoked: List[Dict[str, Any]] = []

    def web_search(args):
        query = str((args or {}).get("query") or "")
        invoked.append({"tool": "web_search", "query": query})
        hits = [
            {
                "title": "The White House",
                "url": "https://www.whitehouse.gov/",
                "snippet": "President Donald J. Trump",
                "source": "whitehouse.gov",
            },
            {
                "title": "Joe Biden Wikipedia",
                "url": "https://en.wikipedia.org/wiki/Joe_Biden",
                "snippet": "Joe Biden was the 46th president.",
                "source": "en.wikipedia.org",
            },
        ]
        ranked = rank_hits(hits, query=query)
        return ToolResult.ok(
            "Znalazłem wyniki.",
            tool_name="web_search",
            data={
                "query": query,
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
    registry.register(
        Tool(
            name="open_app",
            description="app",
            handler=lambda a: ToolResult.ok("ok", tool_name="open_app"),
            risk_level=RiskLevel.SAFE,
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        )
    )
    registry._invoked = invoked  # type: ignore[attr-defined]
    return registry


def run_freshness_smoke() -> int:
    print("=== Elian freshness-smoke (Phase 11.2) ===")
    rows = []
    registry = _registry()

    # 1) President without "teraz"
    registry._invoked.clear()  # type: ignore[attr-defined]
    fresh1 = analyze_freshness("Kto jest prezydentem USA?")
    result1 = Planner(
        registry,
        model=FakeModelClient(
            [
                {
                    "type": "final_answer",
                    "message": "Według źródeł prezydentem USA jest Donald Trump.",
                }
            ]
        ),
    ).run("Kto jest prezydentem USA?")
    web1 = len(getattr(registry, "_invoked", []))
    ok1 = (
        fresh1.freshness_required
        and fresh1.semantic_kind == "QUESTION"
        and web1 >= 1
        and "trump" in (result1.message or "").lower()
    )
    rows.append(
        {
            "input": "Kto jest prezydentem USA?",
            "semantic_kind": fresh1.semantic_kind,
            "freshness_required": fresh1.freshness_required,
            "web_called": web1,
            "verification_status": (result1.claim_verification or {}).get("status"),
            "topic_before": "",
            "topic_after": "",
            "entities": [],
            "response": result1.message,
            "pass": ok1,
        }
    )

    # 2) User factual claim
    registry._invoked.clear()  # type: ignore[attr-defined]
    fresh2 = analyze_freshness("Joe Biden jest prezydentem USA.")
    result2 = Planner(
        registry,
        model=FakeModelClient(
            [
                {
                    "type": "final_answer",
                    "message": "Joe Biden jest prezydentem USA.",
                }
            ]
        ),
    ).run("Joe Biden jest prezydentem USA.")
    web2 = len(getattr(registry, "_invoked", []))
    ver2 = (result2.claim_verification or {}).get("status")
    ok2 = (
        fresh2.freshness_required
        and fresh2.semantic_kind == "CLAIM"
        and web2 >= 1
        and ver2 not in (None, "NOT_CHECKED")
        and "trump" in (result2.message or "").lower()
    )
    rows.append(
        {
            "input": "Joe Biden jest prezydentem USA.",
            "semantic_kind": fresh2.semantic_kind,
            "freshness_required": fresh2.freshness_required,
            "web_called": web2,
            "verification_status": ver2,
            "topic_before": "",
            "topic_after": "",
            "entities": [],
            "response": result2.message,
            "pass": ok2,
        }
    )

    # 3) Opinion
    registry._invoked.clear()  # type: ignore[attr-defined]
    fresh3 = analyze_freshness("Spotify jest fajne.")
    result3 = Planner(
        registry,
        model=FakeModelClient(
            [{"type": "final_answer", "message": "Też lubię Spotify."}]
        ),
    ).run("Spotify jest fajne.")
    web3 = len(getattr(registry, "_invoked", []))
    ok3 = (
        fresh3.semantic_kind == "OPINION"
        and not fresh3.freshness_required
        and web3 == 0
        and not result3.steps
    )
    rows.append(
        {
            "input": "Spotify jest fajne.",
            "semantic_kind": fresh3.semantic_kind,
            "freshness_required": fresh3.freshness_required,
            "web_called": web3,
            "verification_status": "NOT_CHECKED",
            "topic_before": "",
            "topic_after": "",
            "entities": [],
            "response": result3.message,
            "pass": ok3,
        }
    )

    # 4) topic + Teraz wiem
    focus = update_focus(
        None,
        user_text="Co to jest czarna dziura?",
        assistant_text="Czarna dziura to obszar o silnej grawitacji.",
    )
    before4 = focus.get("current_topic")
    focus4 = update_focus(focus, user_text="Teraz wiem.", assistant_text="Dobrze.")
    after4 = focus4.get("current_topic")
    people4 = focus4.get("referenced_people") or []
    ok4 = (
        is_meta_utterance("Teraz wiem.")
        and "czarna" in str(before4).lower()
        and "czarna" in str(after4).lower()
        and not any(p.lower() in ("nie", "jest", "chcę", "zgadzam") for p in people4)
    )
    rows.append(
        {
            "input": "Teraz wiem.",
            "semantic_kind": "META",
            "freshness_required": False,
            "web_called": 0,
            "verification_status": "NOT_CHECKED",
            "topic_before": before4,
            "topic_after": after4,
            "entities": people4,
            "response": "Dobrze.",
            "pass": ok4,
        }
    )

    # 5) topic + Nie rozumiem
    focus5b = update_focus(
        focus4,
        user_text="Nie rozumiem.",
        assistant_text="Czarna dziura to miejsce, z którego światło nie ucieka.",
    )
    ok5 = "czarna" in str(focus5b.get("current_topic") or "").lower()
    rows.append(
        {
            "input": "Nie rozumiem.",
            "semantic_kind": "REPAIR",
            "freshness_required": False,
            "web_called": 0,
            "verification_status": "NOT_CHECKED",
            "topic_before": after4,
            "topic_after": focus5b.get("current_topic"),
            "entities": focus5b.get("referenced_people") or [],
            "response": focus5b.get("last_answer_summary"),
            "pass": ok5,
        }
    )

    passed = 0
    for row in rows:
        mark = "PASS" if row["pass"] else "FAIL"
        if row["pass"]:
            passed += 1
        print(
            f"[{mark}] input={row['input']!r} kind={row['semantic_kind']} "
            f"fresh={row['freshness_required']} web={row['web_called']} "
            f"verify={row['verification_status']} "
            f"topic={row['topic_before']!r}->{row['topic_after']!r} "
            f"entities={row['entities']} resp={(row['response'] or '')[:70]!r}"
        )

    ok = passed == len(rows)
    print(f"Result: {passed}/{len(rows)}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run_freshness_smoke())
