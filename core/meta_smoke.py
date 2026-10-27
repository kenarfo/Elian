# ==========================
# ELIAN — meta smoke (Phase 11.4)
# ==========================
"""Offline semantic META / compound / false-premise smoke — no mic."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

from core.conversation_manager import ConversationManager
from core.freshness import analyze_freshness
from core.meta_ack import analyze_meta
from core.model_client import FakeModelClient
from core.response_language import detect_utterance_language, looks_like_english
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from core.web_grounding import rank_hits
from memory.manager import MemoryManager


CASES = (
    "Okej, to okej, to teraz wiem.",
    "Okej, już teraz wiem.",
    "Nie, nadal nie rozumiem.",
    "Okej, wiem, wiem. Ale kto jest prezydentem Wielkiej Brytanii?",
    "Teraz wiem, ale kto jest premierem Wielkiej Brytanii?",
)


def _registry() -> ToolRegistry:
    registry = ToolRegistry(confirm_medium=False)
    invoked: List[Dict[str, Any]] = []

    def web_search(args):
        query = str((args or {}).get("query") or "")
        invoked.append({"tool": "web_search", "query": query})
        hits = [
            {
                "title": "UK Government",
                "url": "https://www.gov.uk/",
                "snippet": "Prime Minister Keir Starmer",
                "source": "gov.uk",
            }
        ]
        ranked = rank_hits(hits, query=query)
        return ToolResult.ok(
            "Znalazłem 5 wyników.",
            tool_name="web_search",
            data={
                "query": query,
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
    registry._invoked = invoked  # type: ignore[attr-defined]
    return registry


def run_meta_smoke() -> int:
    print("=== Elian meta-smoke (Phase 11.4) ===")
    rows: List[Dict[str, Any]] = []
    failed = 0

    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")
        mem.short.update_focus(
            user_text="Co to jest czarna dziura?",
            assistant_text="Czarna dziura to obszar o silnej grawitacji.",
        )
        registry = _registry()
        model = FakeModelClient(
            [
                {
                    "type": "final_answer",
                    "message": (
                        "The current president of the United States is Donald Trump."
                    ),
                },
                {
                    "type": "clarification",
                    "question": (
                        "The current president of the United States is Donald Trump."
                    ),
                },
                {
                    "type": "final_answer",
                    "message": "Obecnym premierem Wielkiej Brytanii jest Keir Starmer.",
                },
                {
                    "type": "tool_call",
                    "tool": "web_search",
                    "arguments": {"query": "current UK prime minister"},
                },
                {
                    "type": "final_answer",
                    "message": "Obecnym premierem Wielkiej Brytanii jest Keir Starmer.",
                },
            ]
        )
        cm = ConversationManager(registry=registry, model=model, memory_manager=mem)
        cm.activate()
        cm.session_language = "pl"
        cm.last_assistant = (
            "The current president of the United States is Donald Trump."
        )

        for raw in CASES:
            registry._invoked.clear()  # type: ignore[attr-defined]
            meta = analyze_meta(raw)
            fresh = analyze_freshness(raw)
            before_calls = 0

            if meta.is_repair:
                with patch(
                    "core.conversation_manager.ask_ai",
                    return_value=(
                        "Czarna dziura to obszar, z którego nawet światło nie ucieka."
                    ),
                ):
                    result = cm.handle(raw)
            else:
                result = cm.handle(raw)

            web_calls = len(getattr(registry, "_invoked", []))
            queries = [c.get("query", "") for c in getattr(registry, "_invoked", [])]
            repeat_search = len(queries) >= 2 and len(set(queries)) == 1
            noisy = any(
                "wiem" in str(q).lower() or "okej" in str(q).lower() for q in queries
            )
            resp = result.text or ""
            lang = cm.session_language
            replayed = "trump" in resp.lower() and meta.is_meta_ack

            if meta.is_meta_ack:
                ok = (
                    result.source == "ack"
                    and web_calls == 0
                    and not replayed
                    and not looks_like_english(resp)
                )
                route = "ACK/META"
            elif meta.is_repair:
                ok = (
                    result.source != "ack"
                    and "czarna" in resp.lower()
                    and web_calls == 0
                )
                route = "REPAIR"
            elif meta.is_compound and "prezydentem wielkiej" in raw.lower():
                ok = (
                    web_calls == 0
                    and "nie ma prezydenta" in resp.lower()
                    and not noisy
                )
                route = "FALSE_PREMISE"
            elif meta.is_compound and "premierem" in raw.lower():
                ok = (
                    web_calls >= 1
                    and not noisy
                    and not looks_like_english(resp)
                    and not repeat_search
                    and "starmer" in resp.lower()
                )
                route = "WEB"
            else:
                ok = False
                route = result.source

            row = {
                "raw": raw,
                "semantic_kind": fresh.semantic_kind,
                "meta_detected": meta.is_meta_ack or meta.is_compound,
                "negated": meta.negated,
                "compound": meta.is_compound,
                "meta_prefix": meta.meta_prefix,
                "remainder": meta.remainder,
                "route": route,
                "search_query": queries[0] if queries else fresh.search_query,
                "web_calls": web_calls,
                "repeat_search": repeat_search,
                "response_language": lang,
                "response": resp,
                "pass_": ok and detect_utterance_language(raw) in ("pl", "en"),
            }
            rows.append(row)
            status = "PASS" if row["pass_"] else "FAIL"
            if not row["pass_"]:
                failed += 1
            print(
                f"[{status}] raw={row['raw']!r}\n"
                f"  semantic_kind={row['semantic_kind']} "
                f"meta_detected={row['meta_detected']} negated={row['negated']} "
                f"compound={row['compound']}\n"
                f"  meta_prefix={row['meta_prefix']!r} remainder={row['remainder']!r}\n"
                f"  route={row['route']} search_query={row['search_query']!r} "
                f"web_calls={row['web_calls']} repeat_search={row['repeat_search']}\n"
                f"  response_language={row['response_language']} "
                f"response={row['response']!r}"
            )

    print(f"\n=== meta-smoke: {len(rows) - failed}/{len(rows)} PASS ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_meta_smoke())
