# ==========================
# ELIAN — web-state smoke (Phase 11.4.1)
# ==========================
"""META→WEB stale ACK + raw provider error + pending clear — no mic."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List

from core.conversation_manager import ConversationManager
from core.model_client import FakeModelClient
from core.response_finalize import map_provider_error
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from core.web_grounding import rank_hits
from memory.manager import MemoryManager


def _registry(*, fail: bool = False) -> ToolRegistry:
    registry = ToolRegistry(confirm_medium=False)
    invoked: List[Dict[str, Any]] = []

    def web_search(args):
        query = str((args or {}).get("query") or "")
        invoked.append({"tool": "web_search", "query": query})
        if fail:
            return ToolResult.fail(
                "No results found.",
                tool_name="web_search",
                error="No results found.",
                data={"query": query, "provider_error": "No results found."},
                retryable=True,
            )
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


def run_web_state_smoke() -> int:
    print("=== Elian web-state-smoke (Phase 11.4.1) ===")
    rows = []
    failed = 0

    # A) META Okej. → next WEB question must not reuse ACK
    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")
        registry = _registry(fail=False)
        cm = ConversationManager(
            registry=registry,
            model=FakeModelClient(
                [{"type": "final_answer", "message": "Okej."}]
            ),
            memory_manager=mem,
        )
        cm.activate()
        cm.session_language = "pl"
        prev = cm.handle("Okej, już teraz wiem.")
        pending_before = bool(cm.pending_clarification)
        r = cm.handle("Jeszcze raz, kto jest premierem Wielkiej Brytanii?")
        web_ok = len(getattr(registry, "_invoked", [])) >= 1
        origin = (r.data or {}).get("response_origin") or cm._turn_response_origin
        ok = (
            prev.source == "ack"
            and web_ok
            and (r.text or "").strip().lower().rstrip(".") != "okej"
            and origin in ("WEB_SYNTHESIS", "WEB_REPAIR", "WEB_EVIDENCE_FALLBACK")
            and "starmer" in (r.text or "").lower()
        )
        rows.append(
            {
                "case": "A_meta_then_web",
                "previous_response": prev.text,
                "pending_before": pending_before,
                "pending_after": bool(cm.pending_clarification),
                "web_success": web_ok,
                "fresh_web": bool((r.data or {}).get("fresh_web")),
                "response_origin": origin,
                "response_language": cm.session_language,
                "final": r.text,
                "pass_": ok,
            }
        )

    # B) mock provider no-results
    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")
        registry = _registry(fail=True)
        cm = ConversationManager(
            registry=registry,
            model=FakeModelClient(
                [{"type": "final_answer", "message": "No results found."}]
            ),
            memory_manager=mem,
        )
        cm.activate()
        cm.session_language = "pl"
        r = cm.handle("Kto jest premierem Wielkiej Brytanii?")
        mapped = map_provider_error("No results found.", lang="pl")
        ok = (
            "no results found" not in (r.text or "").lower()
            and mapped is not None
            and cm.session_language == "pl"
        )
        rows.append(
            {
                "case": "B_no_results",
                "previous_response": "",
                "pending_before": False,
                "pending_after": bool(cm.pending_clarification),
                "web_success": False,
                "fresh_web": False,
                "response_origin": (r.data or {}).get("response_origin")
                or cm._turn_response_origin
                or "FALLBACK",
                "response_language": cm.session_language,
                "final": r.text,
                "pass_": ok,
            }
        )

    # C) pending clarification → new complete WEB
    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")
        registry = _registry(fail=False)
        cm = ConversationManager(
            registry=registry,
            model=FakeModelClient(
                [
                    {
                        "type": "final_answer",
                        "message": "Obecnym premierem Wielkiej Brytanii jest Keir Starmer.",
                    }
                ]
            ),
            memory_manager=mem,
        )
        cm.activate()
        cm.session_language = "pl"
        cm.pending_clarification = {
            "question": "Okej.",
            "last_answer": "Okej.",
            "original_goal": "niejasne",
            "intent_family": "UNKNOWN",
        }
        pending_before = True
        r = cm.handle("Kto jest premierem Wielkiej Brytanii?")
        ok = (
            cm.pending_clarification is None
            and (r.text or "").strip() != "Okej."
            and "starmer" in (r.text or "").lower()
        )
        rows.append(
            {
                "case": "C_pending_then_web",
                "previous_response": "Okej.",
                "pending_before": pending_before,
                "pending_after": bool(cm.pending_clarification),
                "web_success": len(getattr(registry, "_invoked", [])) >= 1,
                "fresh_web": bool((r.data or {}).get("fresh_web")),
                "response_origin": (r.data or {}).get("response_origin")
                or cm._turn_response_origin,
                "response_language": cm.session_language,
                "final": r.text,
                "pass_": ok,
            }
        )

    for row in rows:
        status = "PASS" if row["pass_"] else "FAIL"
        if not row["pass_"]:
            failed += 1
        print(
            f"[{status}] case={row['case']}\n"
            f"  previous_response={row['previous_response']!r}\n"
            f"  pending_before={row['pending_before']} pending_after={row['pending_after']}\n"
            f"  web_success={row['web_success']} fresh_web={row['fresh_web']}\n"
            f"  response_origin={row['response_origin']} "
            f"response_language={row['response_language']}\n"
            f"  final={row['final']!r}"
        )

    print(f"\n=== web-state-smoke: {len(rows) - failed}/{len(rows)} PASS ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_web_state_smoke())
