# ==========================
# ELIAN — close_app confirmation allowlist (Phase 13 Stage A)
# ==========================
"""Spotify/Chrome close without extra confirm. Unsaved-document apps stay MEDIUM."""

from __future__ import annotations

import re
_SKIP = {
    "spotify",
    "chrome",
    "google chrome",
    "youtube music",
    "youtube",
}


def close_skips_confirmation(name: str) -> bool:
    raw = str(name or "").strip().lower()
    if not raw:
        return False
    raw = re.sub(r"\s+", " ", raw)
    if raw in _SKIP:
        return True
    return any(raw == item or raw.endswith(f" {item}") for item in _SKIP)


def close_invoke_confirmed(name: str, *, confirmed: bool = False) -> bool:
    return bool(confirmed) or close_skips_confirmation(name)
