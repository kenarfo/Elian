# ==========================
# ELIAN — dismiss filler + actionable remainder (Phase 12.1)
# ==========================
"""Split 'Nieważne. Włącz lampę.' into dismiss prefix + real command."""

from __future__ import annotations

import re
from typing import Optional, Tuple


_ACTION = re.compile(
    r"\b(?:"
    r"w[lł][aą]cz|wy[lł][aą]cz|ustaw|zmie[nń]|otw[oó]rz|uruchom|odpal|"
    r"pu[sś][cć]|zagraj|odtw[oó]rz|przypomnij|szukaj|wyszukaj|"
    r"zwi[eę]ksz|zmniejsz|rozja[sś]ni"
    r")\b",
    re.I,
)

# One dismiss clause ending at punctuation (does not swallow the next command).
_DISMISS_CLAUSE = re.compile(
    r"(?:"
    r"(?:ju[zż]\s+)?"
    r"(?:"
    r"ustawi[łl]a?[eę]?[sś]|ustawi[łl]e[sś]|ustawi[łl]am|"
    r"ok(?:ej)?|dobra|dobrze|spoko|jasne|"
    r"niewa[zż]ne|niewazne|"
    r"zostaw(?:\s+to)?|"
    r"to\s+niewa[zż]ne|"
    r"ju[zż]\s+ok(?:ej)?"
    r")"
    r"(?:[^.!?,;]{0,60})?"
    r"(?:[.!?…,;]+\s*|\s+)"
    r")",
    re.I,
)

_PURE_DISMISS = re.compile(
    r"^(?:"
    r"niewa[zż]ne|niewazne|koniec|anuluj|zostaw(?:\s+to)?|"
    r"dobra|dobrze|ok(?:ej)?|ju[zż]\s+ok(?:ej)?|"
    r"ju[zż]\s+ustawi[łl]a?[eę]?[sś].*"
    r")[.!…]*$",
    re.I,
)

_LEADING_ALE = re.compile(r"^(?:ale|tylko|a)\s+", re.I)


def split_dismiss_and_action(text: str) -> Tuple[str, str]:
    """
    Returns (dismiss_prefix, actionable_remainder).

    If the whole utterance is dismiss-only, remainder is empty.
    If no dismiss prefix, remainder equals the original text.
    """
    raw = str(text or "").strip()
    if not raw:
        return "", ""

    if _PURE_DISMISS.match(raw) and not _ACTION.search(raw):
        return raw, ""

    pos = 0
    matched_any = False
    while True:
        m = _DISMISS_CLAUSE.match(raw, pos)
        if not m:
            break
        matched_any = True
        pos = m.end()

    if not matched_any:
        return "", raw

    prefix = raw[:pos].strip()
    rest = raw[pos:].strip(" .,!?;:…")
    rest = _LEADING_ALE.sub("", rest).strip(" .,!?;:…")

    if rest and _ACTION.search(rest):
        return prefix, rest
    if not rest:
        return prefix, ""
    # Remainder without action — keep original intact.
    if _ACTION.search(raw):
        return "", raw
    return prefix, ""


def actionable_remainder(text: str) -> Optional[str]:
    """Return command remainder when dismiss/filler precedes a real request."""
    prefix, rest = split_dismiss_and_action(text)
    if rest and prefix:
        return rest
    return None
