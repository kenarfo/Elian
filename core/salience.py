# ==========================
# ELIAN — SalienceEngine (Phase 1)
# ==========================
"""Adapter over ContextSelector. Never dump WorldState. Measure context pollution."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.context_selection import ContextSelection, select_context

_METRICS = {
    "salience_calls": 0,
    "irrelevant_context_injections": 0,
}


def salience_metrics() -> Dict[str, float]:
    calls = max(1, int(_METRICS.get("salience_calls") or 0))
    leaks = int(_METRICS.get("irrelevant_context_injections") or 0)
    return {
        "salience_calls": int(_METRICS.get("salience_calls") or 0),
        "irrelevant_context_injections": leaks,
        "irrelevant_context_injection_rate": round(leaks / calls, 3) if _METRICS["salience_calls"] else 0.0,
    }


def reset_salience_metrics() -> None:
    _METRICS["salience_calls"] = 0
    _METRICS["irrelevant_context_injections"] = 0


def _talk_like(envelope: Any) -> bool:
    mode = str(getattr(envelope, "mode", "") or "")
    if mode in ("TALK", "ANSWER", "WAIT", "SIMULATE"):
        return not bool(getattr(envelope, "continuation", False))
    return False


def _poison_keys(selection: ContextSelection) -> List[str]:
    dirty = []
    fragments = dict(selection.fragments or {})
    for key in ("active_service", "smart_home", "computer", "screen", "active_plan", "visual_referent"):
        if fragments.get(key):
            dirty.append(key)
    if selection.include_service or selection.include_device or selection.include_computer:
        dirty.append("include_flags")
    return dirty


def select_salient(
    text: str,
    *,
    world: Dict[str, Any] | None = None,
    close_candidates: List[Dict[str, Any]] | None = None,
    envelope: Any = None,
) -> ContextSelection:
    """Canonical salience entry. Delegates to existing select_context."""
    selection = select_context(
        text,
        world=world,
        close_candidates=close_candidates,
        envelope=envelope,
    )
    _METRICS["salience_calls"] = int(_METRICS.get("salience_calls") or 0) + 1
    if _talk_like(envelope) and _poison_keys(selection):
        _METRICS["irrelevant_context_injections"] = int(
            _METRICS.get("irrelevant_context_injections") or 0
        ) + 1
        try:
            from core.metrics import get_metrics

            get_metrics().irrelevant_context_injection_count += 1
        except Exception:
            pass
    return selection
