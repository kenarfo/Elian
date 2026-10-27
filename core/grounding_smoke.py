# ==========================
# ELIAN — grounding smoke (Phase 11.1)
# ==========================
"""Offline + optional live web grounding smoke — no mic."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

from core.conversation_manager import ConversationManager
from core.intent_family import classify_intent_family
from core.model_client import FakeModelClient
from core.planner import Planner
from core.risk import RiskLevel
from core.text_pipeline import prepare_user_text
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from core.web_grounding import (
    authoritative_consensus,
    clean_web_query,
    evaluate_grounding,
    rank_hits,
    repair_current_fact_answer,
)
from memory.manager import MemoryManager


def _fixture_president_hits():
    return [
        {
            "title": "Joe Biden - Wikipedia",
            "url": "https://en.wikipedia.org/wiki/Joe_Biden",
            "snippet": "Joe Biden was the 46th president.",
            "source": "en.wikipedia.org",
        },
        {
            "title": "The White House",
            "url": "https://www.whitehouse.gov/",
            "snippet": "President Donald J. Trump",
            "source": "whitehouse.gov",
        },
        {
            "title": "Trump news",
            "url": "https://www.reuters.com/world/us/trump-president",
            "snippet": "Donald Trump is the current president of the United States.",
            "source": "reuters.com",
        },
    ]


def _dry_registry() -> ToolRegistry:
    registry = ToolRegistry(confirm_medium=False)
    invoked: List[Dict[str, Any]] = []

    def web_search(args):
        query = clean_web_query(str((args or {}).get("query") or ""))
        invoked.append({"tool": "web_search", "arguments": {"query": query}})
        ranked = rank_hits(_fixture_president_hits(), query=query)
        return ToolResult.ok(
            "Znalazłem wyniki.",
            tool_name="web_search",
            data={
                "query": query,
                "results": _fixture_president_hits(),
                "ranked_results": [item.to_dict() for item in ranked],
                "result_count": 3,
                "success": True,
                "provider": "fixture",
            },
        )

    def open_app(args):
        invoked.append({"tool": "open_app", "arguments": dict(args or {})})
        return ToolResult.ok("ok", tool_name="open_app")

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
            handler=open_app,
            risk_level=RiskLevel.SAFE,
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        )
    )
    registry._dry_invoked = invoked  # type: ignore[attr-defined]
    return registry


def _row(**kwargs) -> Dict[str, Any]:
    return kwargs


def run_grounding_smoke() -> int:
    print("=== Elian grounding-smoke (Phase 11.1) ===")
    rows: List[Dict[str, Any]] = []
    registry = _dry_registry()

    # 1) Knowledge black hole (deterministic needs_knowledge path; mocked ask_ai)
    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")
        with patch(
            "core.conversation_manager.ask_ai",
            return_value=(
                "Czarna dziura to obszar o tak silnej grawitacji, "
                "że nawet światło nie ucieka poza horyzont zdarzeń."
            ),
        ):
            cm = ConversationManager(
                registry=registry,
                model=FakeModelClient([]),
                memory_manager=mem,
            )
            cm.activate()
            r1 = cm.handle("Co to jest czarna dziura?")
            prepared1 = prepare_user_text("Co to jest czarna dziura?")
            ok1 = (
                "grawit" in (r1.text or "").lower()
                and ("światł" in (r1.text or "").lower() or "swiatl" in (r1.text or "").lower())
                and "wybuch" not in (r1.text or "").lower()
            )
            rows.append(
                _row(
                    name="black_hole",
                    raw_text="Co to jest czarna dziura?",
                    semantic_text=prepared1.semantic_text,
                    topic=mem.short.focus.get("current_topic"),
                    route=r1.source,
                    web_called=0,
                    fresh_web_observation=False,
                    grounding_status="NONE",
                    sources_used=[],
                    response=r1.text,
                    pass_=ok1,
                )
            )

            # 2) Nie rozumiem → re-explain same topic
            r2 = cm.handle("Nie rozumiem.")
            prepared2 = prepare_user_text("Nie rozumiem.")
            ok2 = (
                "nie" in prepared2.semantic_text.lower()
                and r2.source != "ack"
                and bool(r2.text)
                and "czarna" in str(mem.short.focus.get("current_topic") or "").lower()
            )
            rows.append(
                _row(
                    name="nie_rozumiem",
                    raw_text="Nie rozumiem.",
                    semantic_text=prepared2.semantic_text,
                    topic=mem.short.focus.get("current_topic"),
                    route=r2.source,
                    web_called=0,
                    fresh_web_observation=False,
                    grounding_status="NONE",
                    sources_used=[],
                    response=r2.text,
                    pass_=ok2,
                )
            )

    # 3) President web + synthesis repair
    registry._dry_invoked.clear()  # type: ignore[attr-defined]
    model = FakeModelClient(
        [
            {
                "type": "final_answer",
                "message": "Według kilku aktualnych źródeł Joe Biden jest obecnie prezydentem USA.",
            }
        ]
    )
    result = Planner(registry, model=model).run(
        "Kto jest teraz prezydentem USA?",
        context={"raw_text": "Kto jest teraz prezydentem USA?"},
    )
    web_calls = sum(
        1 for item in getattr(registry, "_dry_invoked", []) if item.get("tool") == "web_search"
    )
    ok3 = (
        web_calls >= 1
        and "trump" in (result.message or "").lower()
        and result.fresh_web_observation_this_turn
        and result.grounding_status in ("SUPPORTED", "PARTIAL")
    )
    ranked = rank_hits(_fixture_president_hits(), query="Kto jest teraz prezydentem USA?")
    rows.append(
        _row(
            name="president_web",
            raw_text="Kto jest teraz prezydentem USA?",
            semantic_text="Kto jest teraz prezydentem USA?",
            topic="",
            route="agent",
            web_called=web_calls,
            fresh_web_observation=result.fresh_web_observation_this_turn,
            grounding_status=result.grounding_status,
            sources_used=[h.source for h in ranked[:3]],
            response=result.message,
            pass_=ok3,
        )
    )

    # 4) Next turn: factual claim is verified with web (not inherited grounding alone)
    registry._dry_invoked.clear()  # type: ignore[attr-defined]
    model2 = FakeModelClient(
        [{"type": "final_answer", "message": "Tak, Joe Biden jest prezydentem."}]
    )
    result2 = Planner(registry, model=model2).run(
        "Joe Biden jest prezydentem USA.",
        context={
            "raw_text": "Joe Biden jest prezydentem USA.",
            "observations": result.observations,
            "conversation_focus": {
                "last_web_consensus": authoritative_consensus(ranked) or "",
            },
        },
    )
    web2 = sum(
        1 for item in getattr(registry, "_dry_invoked", []) if item.get("tool") == "web_search"
    )
    ok4 = (
        web2 >= 1
        and "trump" in (result2.message or "").lower()
        and (result2.claim_verification or {}).get("status") != "NOT_CHECKED"
    )
    rows.append(
        _row(
            name="user_contradiction",
            raw_text="Joe Biden jest prezydentem USA.",
            semantic_text="Joe Biden jest prezydentem USA.",
            topic="",
            route="agent",
            web_called=web2,
            fresh_web_observation=result2.fresh_web_observation_this_turn,
            grounding_status=result2.grounding_status,
            sources_used=[],
            response=result2.message,
            pass_=ok4,
        )
    )

    # 5) Negation preserved opinion
    phrase = "Spotify nie jest nudne, Spotify jest fajne."
    prepared5 = prepare_user_text(phrase)
    info = classify_intent_family(prepared5.semantic_text)
    registry._dry_invoked.clear()  # type: ignore[attr-defined]
    model3 = FakeModelClient(
        [{"type": "final_answer", "message": "Też lubię Spotify."}]
    )
    result5 = Planner(registry, model=model3).run(
        prepared5.semantic_text,
        context={"raw_text": prepared5.semantic_text, "intent_classification": info},
    )
    ok5 = (
        "nie" in prepared5.semantic_text.lower()
        and len(getattr(registry, "_dry_invoked", [])) == 0
        and not result5.steps
    )
    rows.append(
        _row(
            name="spotify_negation",
            raw_text=phrase,
            semantic_text=prepared5.semantic_text,
            topic="",
            route="talk",
            web_called=0,
            fresh_web_observation=False,
            grounding_status="NONE",
            sources_used=[],
            response=result5.message,
            pass_=ok5,
        )
    )

    # Optional live ranking check (does not hardcode surname beyond evidence).
    try:
        from core.web_search_provider import provider_available, search_structured

        if provider_available():
            live = search_structured("current president of the United States", max_results=5)
            live_ranked = rank_hits(live.results, query="kto jest teraz prezydentem USA")
            consensus = authoritative_consensus(live_ranked)
            live_answer = repair_current_fact_answer(
                "Joe Biden jest obecnie prezydentem USA.",
                live_ranked,
                query="Kto jest teraz prezydentem USA?",
            )
            live_eval = evaluate_grounding(
                live_answer,
                live_ranked,
                fresh_web=True,
                query="Kto jest teraz prezydentem USA?",
            )
            ok_live = bool(live.success and live_ranked and live_eval["grounding_status"] != "NONE")
            print(
                f"[LIVE] consensus={consensus} status={live_eval['grounding_status']} "
                f"answer={live_answer[:80]!r}"
            )
        else:
            ok_live = True
            print("[LIVE] skipped — provider unavailable")
    except Exception as error:
        ok_live = False
        print(f"[LIVE] error={error}")

    passed = 0
    for row in rows:
        mark = "PASS" if row["pass_"] else "FAIL"
        if row["pass_"]:
            passed += 1
        print(
            f"[{mark}] {row['name']}: raw={row['raw_text']!r} "
            f"semantic={row['semantic_text']!r} topic={row.get('topic')!r} "
            f"route={row['route']} web={row['web_called']} "
            f"fresh={row['fresh_web_observation']} status={row['grounding_status']} "
            f"sources={row['sources_used']} resp={(row['response'] or '')[:70]!r}"
        )

    ok = passed == len(rows) and ok_live
    print(f"Result: {passed}/{len(rows)} + live={'PASS' if ok_live else 'FAIL'}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run_grounding_smoke())
