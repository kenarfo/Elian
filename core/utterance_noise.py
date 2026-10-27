# ==========================
# ELIAN — noise-like UNKNOWN utterances (Phase 13 Stage A)
# ==========================
"""Do not inherit stale Spotify/web focus for garbage STT."""

from __future__ import annotations

import re

_WORD = re.compile(r"[a-ząćęłńóśźż]+", re.I)
_ANCHOR = re.compile(
    r"\b(?:"
    r"otw[oó]rz|zamknij|wyłącz|wylacz|włącz|wlacz|pu[sś][cć]|g[lł]o[sś]n|"
    r"ciszej|lamp|świat|swiat|klima|spotify|chrome|discord|"
    r"co|jak|gdzie|kiedy|czy|dlaczego|czemu|godzin|przypomn|"
    r"ok|okej|dobra|dzięki|dzieki|hej|proszę|prosze|"
    r"tak|nie|nieważne|niewazne|anuluj|elian|eliam|eljan"
    r")\b",
    re.I,
)
_REPEATED_DO = re.compile(r"(?:\bdo\b[\s,\-!]*){3,}", re.I)
_SYLLABLE_LOOP = re.compile(
    r"^(?:[a-z]{1,5}[\s\-–—]+){3,}[a-z]{0,5}[.!]*$",
    re.I,
)
# YouTube / TV / browser bleed into the mic — never chat about these.
_AMBIENT_BLEED = re.compile(
    r"(?:"
    r"dzi[eę]k[ui]\w*\s+za\s+ogl[aą]danie|"
    r"thanks\s+for\s+watching|"
    r"please\s+subscribe|"
    r"like\s+and\s+subscribe|"
    r"subskrybuj|"
    r"zostaw\s+[łl]apk|"
    r"koniec\s+odcinka|"
    r"do\s+zobaczenia\s+w\s+nast[eę]pn"
    r")",
    re.I,
)


def looks_ambient_bleed(text: str) -> bool:
    """True for YouTube/outro phrases that must not become a conversation turn."""
    return bool(_AMBIENT_BLEED.search(str(text or "")))


def looks_noise_like(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if looks_ambient_bleed(raw):
        return True
    core = raw.split("[kontekst:", 1)[0].split("\n(", 1)[0].strip()
    if _ANCHOR.search(core):
        return False
    words = _WORD.findall(core.lower())
    if _REPEATED_DO.search(core):
        return True
    if len(words) >= 3 and len(set(words)) == 1:
        return True
    if re.search(r"[-–—]", core) and len(core) <= 48 and _SYLLABLE_LOOP.match(core):
        return True
    # Single long token with no Polish action anchor (e.g. Whisper "Wunschlampe").
    # Keep this strict: avoid killing short wake/command fragments.
    if len(words) == 1 and len(words[0]) >= 10 and not re.search(r"[ąćęłńóśźż]", words[0], re.I):
        return True
    return False


def looks_idle_action_command(text: str) -> bool:
    """Clear action while idle (legacy STT) — treat as wake+command without 'hej elian'."""
    raw = str(text or "").strip()
    if not raw or looks_ambient_bleed(raw) or looks_noise_like(raw):
        return False
    try:
        from core.semantic_talk import try_clock_reply

        if try_clock_reply(raw):
            return True
    except Exception:
        pass
    if re.search(
        r"\b(?:g[lł]o[sś]niej|ciszej|zrób\s+(?:g[lł]o[sś]niej|ciszej))\b",
        raw,
        re.I,
    ):
        return True
    try:
        from core.intent_family import IntentFamily, classify_intent_family

        info = classify_intent_family(raw)
        if info.family in (
            IntentFamily.APP,
            IntentFamily.MEDIA,
            IntentFamily.SMART_HOME,
            IntentFamily.SYSTEM,
            IntentFamily.REMINDER,
            IntentFamily.CALENDAR,
        ) and bool(info.is_complete):
            return True
    except Exception:
        pass
    # Explicit short Polish actions even if slots incomplete.
    if re.match(
        r"^\s*(?:otw[oó]rz|zamknij|w[lł][aą]cz|wy[lł][aą]cz|pu[sś][cć]|przypomnij)\b",
        raw,
        re.I,
    ):
        return True
    return False
