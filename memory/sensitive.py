"""Sensitive data guards for memory writes."""

from __future__ import annotations

import re

from memory.store import clean_text


def _fold(text):
    return str(text or "").translate(str.maketrans({
        "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
        "ó": "o", "ś": "s", "ź": "z", "ż": "z",
        "Ą": "a", "Ć": "c", "Ę": "e", "Ł": "l", "Ń": "n",
        "Ó": "o", "Ś": "s", "Ź": "z", "Ż": "z",
    }))


_SENSITIVE_PATTERNS = (
    re.compile(r"haslo"),
    re.compile(r"password"),
    re.compile(r"\btoken\b"),
    re.compile(r"api[_\s-]?key"),
    re.compile(r"\bsecret\b"),
    re.compile(r"\b2fa\b"),
    re.compile(r"\bkart[ay]\b"),
    re.compile(r"\bcvv\b"),
    re.compile(r"\bpin\b"),
    re.compile(r"bearer\s+\S+"),
)


def looks_sensitive(text):
    cleaned = clean_text(text, 2000)
    if not cleaned:
        return False
    folded = _fold(cleaned).lower()
    return any(pattern.search(folded) for pattern in _SENSITIVE_PATTERNS)


def refuse_sensitive_message():
    return "Nie zapamiętam haseł, tokenów ani innych tajnych danych."
