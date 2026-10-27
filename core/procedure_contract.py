# ==========================
# ELIAN — ProcedureEngine contract (Phase 13 Stage B)
# ==========================
"""Named procedures exist as identity only. Stage E executes them."""

from __future__ import annotations

import re
from typing import Dict

PROCEDURE_IDS = (
    "sleep",
    "morning",
    "gaming",
    "study",
    "movie",
    "stream",
    "leave_home",
    "home",
)

_MENTION_NOT_TRIGGER = re.compile(
    r"\b(?:co\s+oznacza|czym\s+jest|co\s+to\s+jest|wyja[sś]nij)\b",
    re.I,
)
_SLEEP = re.compile(
    r"\b(?:id[eę]\s+spa[cć]|dobranoc|pora\s+spa[cć]|po[lł]o[zż][eę]|ko[nń]cz[eę]\s+na\s+dzi[sś])\b",
    re.I,
)
_MORNING = re.compile(
    r"^(?:dzie[nń]\s+dobry|wsta[lł]em|zaczynamy\s+dzie[nń])[.!?…]*$",
    re.I,
)
_GAMING = re.compile(
    r"(?:"
    r"\bogarnij\s+komputer\s+do\s+grania\b|"
    r"\bprzygotuj\s+(?:pc|komputer)\s+do\s+grania\b|"
    r"\btryb\s+grania\b|"
    r"\buruchom\s+procedur[eę]?\s+gaming\b"
    r")",
    re.I,
)


def looks_procedure_mention_not_trigger(text: str) -> bool:
    return bool(_MENTION_NOT_TRIGGER.search(str(text or "")))


def classify_procedure(text: str) -> str:
    raw = str(text or "").strip()
    if not raw or looks_procedure_mention_not_trigger(raw):
        return ""
    if _SLEEP.search(raw):
        return "sleep"
    if _MORNING.match(raw):
        return "morning"
    if _GAMING.search(raw):
        return "gaming"
    return ""


def procedure_unavailable_message(procedure_id: str) -> str:
    name = str(procedure_id or "tej procedury")
    return f"Nie mam procedury {name}."
