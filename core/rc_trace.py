# ==========================
# ELIAN — RC utterance trace (privacy-safe)
# ==========================
"""Structured [RC_TRACE] lines for hardware validation. No transcripts/secrets."""

from __future__ import annotations

from typing import Any


def rc_trace_enabled() -> bool:
    try:
        from core.rc_validation.collector import get_rc_collector

        col = get_rc_collector()
        return bool(col is not None and getattr(col, "active", False))
    except Exception:
        return False


def log_rc_trace(**fields: Any) -> None:
    """Print compact RC diagnostics. Callers must never pass raw transcripts."""
    if not rc_trace_enabled():
        return
    parts = []
    for key, value in fields.items():
        if value is None:
            continue
        raw = str(value).replace("\n", " ").strip()
        if len(raw) > 80:
            raw = raw[:77] + "..."
        parts.append(f"{key}={raw}")
    if parts:
        print("[RC_TRACE] " + " ".join(parts))
