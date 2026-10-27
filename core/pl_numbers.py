# ==========================
# ELIAN — Polish number words (Phase 12.1)
# ==========================
"""Parse Polish number words and digits into integers (0–199)."""

from __future__ import annotations

import re
from typing import Optional


_ONES = {
    "zero": 0,
    "jeden": 1,
    "jedna": 1,
    "jedno": 1,
    "dwa": 2,
    "dwie": 2,
    "trzy": 3,
    "cztery": 4,
    "pięć": 5,
    "piec": 5,
    "sześć": 6,
    "szesc": 6,
    "siedem": 7,
    "osiem": 8,
    "dziewięć": 9,
    "dziewiec": 9,
}

_TEENS = {
    "dziesięć": 10,
    "dziesiec": 10,
    "jedenaście": 11,
    "jedenascie": 11,
    "dwanaście": 12,
    "dwanascie": 12,
    "trzynaście": 13,
    "trzynascie": 13,
    "czternaście": 14,
    "czternascie": 14,
    "piętnaście": 15,
    "pietnascie": 15,
    "szesnaście": 16,
    "szesnascie": 16,
    "siedemnaście": 17,
    "siedemnascie": 17,
    "osiemnaście": 18,
    "osiemnascie": 18,
    "dziewiętnaście": 19,
    "dziewietnascie": 19,
}

_TENS = {
    "dwadzieścia": 20,
    "dwadziescia": 20,
    "trzydzieści": 30,
    "trzydziesci": 30,
    "czterdzieści": 40,
    "czterdziesci": 40,
    "pięćdziesiąt": 50,
    "piecdziesiat": 50,
    "sześćdziesiąt": 60,
    "szescdziesiat": 60,
    "siedemdziesiąt": 70,
    "siedemdziesiat": 70,
    "osiemdziesiąt": 80,
    "osiemdziesiat": 80,
    "dziewięćdziesiąt": 90,
    "dziewiecdziesiat": 90,
}

_HUNDRED = {
    "sto": 100,
}


def _norm_token(token: str) -> str:
    t = str(token or "").lower().strip(" .,!?;:…")
    # Strip combining marks lightly for ascii fallbacks already listed.
    return t


def parse_pl_number_words(text: str) -> Optional[int]:
    """
    Parse a Polish number expression into an int.

    Handles: digits, ones, teens, tens (+ optional ones), sto (+ remainder).
    Returns None when no number can be read.
    """
    raw = str(text or "").lower().strip()
    if not raw:
        return None

    # Prefer explicit digits first.
    digit = re.search(r"\b(\d{1,3})\b", raw)
    if digit:
        try:
            value = int(digit.group(1))
        except (TypeError, ValueError):
            value = None
        if value is not None and 0 <= value <= 199:
            return value

    tokens = [_norm_token(t) for t in re.split(r"[\s\-]+", raw) if _norm_token(t)]
    # Drop common non-number glue words that often wrap values.
    skip = {
        "na", "do", "o", "procent", "procenta", "procentów", "procentow",
        "stopni", "stopnie", "stopnia", "teraz", "jeszcze", "daj", "ustaw",
        "zrób", "zrob", "ją", "ja", "go", "je", "to", "proszę", "prosze",
    }
    tokens = [t for t in tokens if t not in skip]
    if not tokens:
        return None

    total = 0
    i = 0
    matched = False
    while i < len(tokens):
        tok = tokens[i]
        if tok in _HUNDRED:
            total += 100
            matched = True
            i += 1
            continue
        if tok in _TEENS:
            total += _TEENS[tok]
            matched = True
            i += 1
            continue
        if tok in _TENS:
            total += _TENS[tok]
            matched = True
            i += 1
            if i < len(tokens) and tokens[i] in _ONES:
                total += _ONES[tokens[i]]
                i += 1
            continue
        if tok in _ONES:
            total += _ONES[tok]
            matched = True
            i += 1
            continue
        # Unknown token — stop if we already matched; else fail.
        if matched:
            break
        return None

    if not matched:
        return None
    if total < 0 or total > 199:
        return None
    return total


def extract_number(text: str) -> Optional[int]:
    """Public helper: digit or Polish number word anywhere in the utterance."""
    raw = str(text or "").strip()
    if not raw:
        return None
    # Whole-ish utterance parse (value-only follow-ups).
    value = parse_pl_number_words(raw)
    if value is not None:
        return value
    # Fallback: scan for a tens/ones phrase in a longer sentence.
    for pattern in (
        r"\b((?:sto\s+)?(?:(?:dwadzieścia|trzydzieści|czterdzieści|pięćdziesiąt|"
        r"sześćdziesiąt|siedemdziesiąt|osiemdziesiąt|dziewięćdziesiąt|"
        r"dwadziescia|trzydziesci|czterdziesci|piecdziesiat|szescdziesiat|"
        r"siedemdziesiat|osiemdziesiat|dziewiecdziesiat)\s+)?"
        r"(?:jeden|jedna|jedno|dwa|dwie|trzy|cztery|pięć|piec|sześć|szesc|"
        r"siedem|osiem|dziewięć|dziewiec|dziesięć|dziesiec|jedenaście|"
        r"jedenascie|dwanaście|dwanascie|trzynaście|trzynascie|czternaście|"
        r"czternascie|piętnaście|pietnascie|szesnaście|szesnascie|siedemnaście|"
        r"siedemnascie|osiemnaście|osiemnascie|dziewiętnaście|dziewietnascie|"
        r"dwadzieścia|trzydzieści|czterdzieści|pięćdziesiąt|sześćdziesiąt|"
        r"siedemdziesiąt|osiemdziesiąt|dziewięćdziesiąt|sto)?)\b",
    ):
        match = re.search(pattern, raw, re.I)
        if match and match.group(1).strip():
            parsed = parse_pl_number_words(match.group(1))
            if parsed is not None:
                return parsed
    return None
