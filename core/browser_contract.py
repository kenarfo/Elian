# ==========================
# ELIAN — BrowserAgent Stage D contract
# ==========================
"""Semantic browser tools. Honest UNAVAILABLE unless Playwright is installed and enabled."""

from __future__ import annotations

from typing import Any, Dict

from core.tool_result import ToolResult

STAGE = "D"
UNAVAILABLE_MSG = (
    "Nie mogę teraz sterować kartą przeglądarki."
)


def _browser_opt_in() -> bool:
    try:
        from core.browser_agent import browser_opt_in

        return bool(browser_opt_in())
    except Exception:
        return False


def browser_capability_unavailable(action: str, *, data: Dict[str, Any] | None = None) -> ToolResult:
    from core.browser_agent import get_browser_agent

    payload = dict(data or {})
    result = get_browser_agent().act(action, payload)
    merged = dict(payload)
    merged.update(result.data or {})
    if not result.success:
        merged.setdefault("capability", action)
        merged.setdefault("available", False)
        merged.setdefault("deferred_stage", STAGE)
        merged.setdefault("opt_in", _browser_opt_in())
    result.data = merged
    return result


def browser_search_service(arguments: Dict[str, Any] | None = None) -> ToolResult:
    args = dict(arguments or {})
    return browser_capability_unavailable(
        "browser_search_service",
        data={"query": args.get("query") or args.get("q") or "", "name": args.get("name") or ""},
    )


def browser_select_result(arguments: Dict[str, Any] | None = None) -> ToolResult:
    args = dict(arguments or {})
    return browser_capability_unavailable(
        "browser_select_result",
        data={"index": args.get("index"), "query": args.get("query") or ""},
    )


def browser_play_media(arguments: Dict[str, Any] | None = None) -> ToolResult:
    args = dict(arguments or {})
    return browser_capability_unavailable(
        "browser_play_media",
        data={"query": args.get("query") or "", "name": args.get("name") or ""},
    )


def close_browser_tab(arguments: Dict[str, Any] | None = None) -> ToolResult:
    args = dict(arguments or {})
    return browser_capability_unavailable(
        "close_browser_tab",
        data={
            "service": args.get("name") or args.get("service") or "",
            "name": args.get("name") or args.get("service") or "",
            "tab_id": args.get("tab_id") or "",
        },
    )
