# ==========================
# ELIAN — language smoke (Phase 11.3)
# ==========================
"""Offline language / meta ACK smoke — no mic."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List

from core.conversation_manager import ConversationManager
from core.freshness import analyze_freshness, is_meta_utterance, split_meta_compound
from core.model_client import FakeModelClient
from core.response_language import (
    detect_utterance_language,
    looks_like_english,
    parse_language_request,
)
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from core.web_grounding import rank_hits
from memory.manager import MemoryManager


def _registry() -> ToolRegistry:
    registry = ToolRegistry(confirm_medium=False)
    invoked: List[Dict[str, Any]] = []

    def web_search(args):
        query = str((args or {}).get("query") or "")
        invoked.append({"tool": "web_search", "query": query})
        low = query.lower()
        if "uk" in low or "prime" in low or "premier" in low or "britain" in low:
            hits = [
                {
                    "title": "UK Government",
                    "url": "https://www.gov.uk/",
                    "snippet": "Prime Minister Keir Starmer",
                    "source": "gov.uk",
                }
            ]
        else:
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


def _row(**kwargs) -> Dict[str, Any]:
    return kwargs


def _topic(mem: MemoryManager) -> str:
    focus = getattr(mem.short, "focus", None) or {}
    if not isinstance(focus, dict):
        return ""
    return str(focus.get("current_topic") or "")


def run_language_smoke() -> int:
    print("=== Elian language-smoke (Phase 11.3) ===")
    rows: List[Dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")
        registry = _registry()
        model = FakeModelClient(
            [
                {
                    "type": "final_answer",
                    "message": (
                        "The current President of the United States is Donald Trump."
                    ),
                },
                {
                    "type": "final_answer",
                    "message": (
                        "The current President of the United States is Donald Trump."
                    ),
                },
                {
                    "type": "final_answer",
                    "message": "The current UK prime minister is Keir Starmer.",
                },
            ]
        )
        cm = ConversationManager(registry=registry, model=model, memory_manager=mem)
        cm.activate()

        # 1) Polish president question → WEB → Polish answer
        registry._invoked.clear()  # type: ignore[attr-defined]
        topic_before = _topic(mem)
        r1 = cm.handle("Kto jest prezydentem USA?")
        topic_after = _topic(mem)
        web1 = len(getattr(registry, "_invoked", []))
        detected1 = detect_utterance_language("Kto jest prezydentem USA?")
        ok1 = (
            detected1 == "pl"
            and cm.session_language == "pl"
            and web1 >= 1
            and not looks_like_english(r1.text or "")
            and "trump" in (r1.text or "").lower()
        )
        rows.append(
            _row(
                input="Kto jest prezydentem USA?",
                detected_language=detected1,
                session_language=cm.session_language,
                route="WEB" if web1 else r1.source,
                web_called=web1,
                topic_before=topic_before,
                topic_after=topic_after,
                previous_answer_replayed=False,
                response=r1.text,
                pass_=ok1,
            )
        )

        # 2) Explicit Polish request
        registry._invoked.clear()  # type: ignore[attr-defined]
        r2 = cm.handle("Ale mów po polsku.")
        ok2 = (
            parse_language_request("Ale mów po polsku.") == "pl"
            and cm.session_language == "pl"
            and len(getattr(registry, "_invoked", [])) == 0
            and "polsku" in (r2.text or "").lower()
        )
        rows.append(
            _row(
                input="Ale mów po polsku.",
                detected_language="pl",
                session_language=cm.session_language,
                route="session_language",
                web_called=0,
                topic_before=topic_after,
                topic_after=_topic(mem),
                previous_answer_replayed=False,
                response=r2.text,
                pass_=ok2,
            )
        )

        # 3) Meta ACK — no tools, no previous-answer replay
        registry._invoked.clear()  # type: ignore[attr-defined]
        prev = cm.last_assistant or (r1.text or "")
        topic_b3 = _topic(mem)
        r3 = cm.handle("No, okej, teraz wiem.")
        topic_a3 = _topic(mem)
        replayed = bool(prev) and prev[:40].lower() in (r3.text or "").lower()
        ok3 = (
            is_meta_utterance("No, okej, teraz wiem.")
            and r3.source == "ack"
            and len(getattr(registry, "_invoked", [])) == 0
            and not replayed
            and "trump" not in (r3.text or "").lower()
            and topic_b3 == topic_a3
        )
        rows.append(
            _row(
                input="No, okej, teraz wiem.",
                detected_language="pl",
                session_language=cm.session_language,
                route="ACK/META",
                web_called=0,
                topic_before=topic_b3,
                topic_after=topic_a3,
                previous_answer_replayed=replayed,
                response=r3.text,
                pass_=ok3,
            )
        )

        # 4) Compound meta + UK PM → WEB Polish
        registry._invoked.clear()  # type: ignore[attr-defined]
        kind, rest = split_meta_compound(
            "Teraz wiem, ale kto jest premierem Wielkiej Brytanii?"
        )
        fresh = analyze_freshness(
            "Teraz wiem, ale kto jest premierem Wielkiej Brytanii?"
        )
        r4 = cm.handle("Teraz wiem, ale kto jest premierem Wielkiej Brytanii?")
        web4 = len(getattr(registry, "_invoked", []))
        ok4 = (
            kind == "compound"
            and "premier" in rest.lower()
            and fresh.freshness_required
            and web4 >= 1
            and not looks_like_english(r4.text or "")
        )
        rows.append(
            _row(
                input="Teraz wiem, ale kto jest premierem Wielkiej Brytanii?",
                detected_language="pl",
                session_language=cm.session_language,
                route="WEB",
                web_called=web4,
                topic_before=topic_a3,
                topic_after=_topic(mem),
                previous_answer_replayed=False,
                response=r4.text,
                pass_=ok4,
            )
        )

    failed = 0
    for row in rows:
        status = "PASS" if row.get("pass_") else "FAIL"
        if not row.get("pass_"):
            failed += 1
        print(
            f"[{status}] input={row['input']!r}\n"
            f"  detected_language={row['detected_language']} "
            f"session_language={row['session_language']} "
            f"route={row['route']} web_called={row['web_called']}\n"
            f"  topic_before={row['topic_before']!r} "
            f"topic_after={row['topic_after']!r}\n"
            f"  previous_answer_replayed={row['previous_answer_replayed']}\n"
            f"  response={row['response']!r}"
        )

    print(f"\n=== language-smoke: {len(rows) - failed}/{len(rows)} PASS ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_language_smoke())
