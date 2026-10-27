# ==========================
# ELIAN — in-session missed-events digest
# ==========================
"""Persist queued proactive lines for this process only. Not a post-exit timeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from core.jarvis_response import polish_spoken

_PATH = Path(__file__).resolve().parents[1] / "Data" / "session_digest.json"
_MAX = 12


def _load() -> List[str]:
    try:
        raw = json.loads(_PATH.read_text(encoding="utf-8"))
        rows = raw.get("lines") if isinstance(raw, dict) else raw
        return [str(item).strip() for item in (rows or []) if str(item).strip()]
    except Exception:
        return []


def _save(lines: List[str]) -> None:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(
            json.dumps({"lines": lines[-_MAX:]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def note_digest_line(message: str) -> None:
    text = polish_spoken(str(message or "").strip())
    if not text:
        return
    lines = _load()
    if lines and lines[-1] == text:
        return
    lines.append(text)
    _save(lines)


def peek_digest_lines() -> List[str]:
    return list(_load())


def consume_digest_lines() -> List[str]:
    lines = _load()
    _save([])
    return lines


def consume_digest_spoken() -> str:
    lines = consume_digest_lines()
    if not lines:
        return "Nic mi nie umknęło w tej sesji."
    spoken = " ".join(lines[:4])
    return polish_spoken(spoken) or "Nic mi nie umknęło w tej sesji."


def reset_digest_for_tests() -> None:
    _save([])
