# ==========================
# ELIAN — utterance text pipeline (Phase 10 RC)
# ==========================
"""Preserve raw user text; avoid destructive intent stripping."""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.utterance import analyze_utterance

try:
    from WakeWord import is_wake_word, remove_wake_word
except ImportError:
    is_wake_word = lambda _t: False  # noqa: E731
    remove_wake_word = lambda t: t  # noqa: E731


ACTION_VERB_PREFIX = re.compile(
    r"^(?:"
    r"utwórz|utworz|stwórz|stworz|dodaj|przypomnij|zaplanuj|"
    r"otwórz|otworz|odw[oó]rz|włącz|wlacz|wyłącz|wylacz|zamknij|"
    r"puść|pusc|odtwórz|odtworz|wyszukaj|poszukaj|"
    r"wpisz|dopisz|zaznacz|cofnij|skopiuj|wklej|wytnij|"
    r"zminimalizuj|zmaksymalizuj|przewi[nń]|"
    r"co\s+mam\s+na\s+ekranie|kliknij|ustaw|zwiększ|zmniejsz"
    r")\b",
    re.I,
)
COMPUTER_ACTION_LATER = re.compile(
    r"\b(?:zaznacz|wpisz|dopisz|cofnij|wklej|wytnij|skopiuj)\b",
    re.I,
)
FRAGMENT_START = re.compile(
    r"^(?:działa|dziala|jak|że|ze|oraz|tekst|agent)\b",
    re.I,
)
# Natural continuations — not truncated STT leftovers.
DISCOURSE_THEN_ACTION = re.compile(
    r"^(?:"
    r"no\s+i|"
    r"i\s+teraz|"
    r"a\s+teraz|"
    r"to\s+teraz|"
    r"dobra(?:\s+i)?|"
    r"okej(?:\s+i)?|"
    r"no\s+dobra|"
    r"i"
    r")[,.]?\s+"
    r"(?:wpisz|dopisz|zaznacz|cofnij|wklej|wytnij|skopiuj|"
    r"otw[oó]rz|zamknij|w[lł][aą]cz|wy[lł][aą]cz|zapal|zga[sś]|"
    r"pu[sś][cć]|kliknij|ustaw|zwi[eę]ksz|zmniejsz|"
    r"co\s+(?:teraz\s+)?widz|jakie\s+okno)"
    r"",
    re.I,
)
BARE_TRUNCATED = re.compile(
    r"^(?:"
    r"i\s+wtedy|"
    r"no\s+i|"
    r"tego|"
    r"tamto|"
    r"ca[lł]y|"
    r"zr[oó]b"
    r")[\s.…]*$",
    re.I,
)


@dataclass
class PreparedText:
    raw_text: str
    normalized_text: str
    semantic_text: str


def _leading_action_phrase(text: str) -> str:
    match = ACTION_VERB_PREFIX.match(str(text or "").strip())
    if not match:
        return ""
    return match.group(0)


def ensure_semantic_text(raw_text: str, normalized_text: str) -> str:
    """
    Restore a leading action verb if preprocessing dropped it.
    Example bug: 'utwórz wydarzenie…' → 'z tytułem jedzenie'.
    Phase 10.3: never drop sentence-changing negation ("nie").
    """
    raw = str(raw_text or "").strip()
    normalized = str(normalized_text or "").strip()
    if not normalized:
        return raw
    if not raw:
        return normalized

    # Negation preservation — semantic corruption guard.
    raw_has_nie = bool(re.search(r"\bnie\b", raw, re.I))
    norm_has_nie = bool(re.search(r"\bnie\b", normalized, re.I))
    if raw_has_nie and not norm_has_nie:
        return raw

    raw_action = _leading_action_phrase(raw)
    norm_action = _leading_action_phrase(normalized)
    if raw_action and not norm_action:
        # Reattach lost verb chunk from raw when normalized looks like a fragment.
        if normalized.lower() in raw.lower():
            idx = raw.lower().find(normalized.lower())
            if idx > 0:
                prefix = raw[:idx].strip(" ,.;")
                if prefix:
                    return f"{prefix} {normalized}".strip()
        return f"{raw_action} {normalized}".strip()

    return normalized


def looks_truncated_command(text: str) -> bool:
    """True when STT/wake strip left a mid-sentence fragment plus a later action."""
    raw = str(text or "").strip()
    if not raw:
        return False
    if ACTION_VERB_PREFIX.match(raw):
        return False
    if DISCOURSE_THEN_ACTION.match(raw):
        return False
    if BARE_TRUNCATED.match(raw):
        return True
    if COMPUTER_ACTION_LATER.search(raw) and FRAGMENT_START.match(raw):
        return True
    return False


def recover_full_command(raw_transcript: str, extracted: str) -> str:
    """If raw STT is longer, re-strip only the leading wake prefix."""
    raw = str(raw_transcript or "").strip()
    got = str(extracted or "").strip()
    if not raw:
        return got
    recovered = strip_session_wake(raw)
    if looks_truncated_command(recovered):
        return got
    if len(recovered) > len(got) + 8 and got.lower() in recovered.lower():
        return recovered
    if recovered and not looks_truncated_command(recovered):
        if looks_truncated_command(got):
            return recovered
    return got


def strip_session_wake(text: str) -> str:
    """Remove wake prefix in active session; preserve in-sentence names like Eliana."""
    raw = str(text or "").strip()
    if not raw:
        return raw
    lowered = raw.lower()
    if is_wake_word(lowered):
        stripped = remove_wake_word(raw)
        return stripped if stripped else raw
    return raw


def prepare_user_text(raw_text: str) -> PreparedText:
    source = str(raw_text or "").strip()
    try:
        from WakeWord import extract_wake

        wake = extract_wake(source)
        raw = wake.raw_command or strip_session_wake(source)
        normalized_command = wake.normalized_command or raw.lower()
        print(
            f"[TEXT PIPELINE] raw_command={wake.raw_command!r} "
            f"normalized_command={normalized_command!r}"
        )
    except Exception:
        raw = strip_session_wake(source)
        normalized_command = raw.lower()
    analysis = analyze_utterance(raw)
    if analysis.filler_only:
        return PreparedText(raw_text=raw, normalized_text="", semantic_text="")

    normalized = analysis.normalized if analysis.normalized else raw
    semantic = ensure_semantic_text(raw, normalized)
    return PreparedText(
        raw_text=raw,
        normalized_text=normalized,
        semantic_text=semantic,
    )
