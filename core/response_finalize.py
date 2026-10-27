# ==========================
# ELIAN — final response boundary (Phase 11.4 / 11.4.1)
# ==========================
"""Single user-facing exit: language coerce + ACK-safe fallbacks."""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence, Tuple


ORIGIN_META_ACK = "META_ACK"
ORIGIN_MODEL = "MODEL"
ORIGIN_KNOWLEDGE = "KNOWLEDGE"
ORIGIN_WEB_SYNTHESIS = "WEB_SYNTHESIS"
ORIGIN_WEB_REPAIR = "WEB_REPAIR"
ORIGIN_WEB_EVIDENCE_FALLBACK = "WEB_EVIDENCE_FALLBACK"
ORIGIN_TOOL_RESULT = "TOOL_RESULT"
ORIGIN_FALLBACK = "FALLBACK"
ORIGIN_GENERIC_FAILURE = "GENERIC_FAILURE"

_SHORT_ACK = re.compile(
    r"^(?:okej|ok|okay|jasne|super|dobrze|dobra|spoko|mhm|aha)[.!…]*$",
    re.I,
)

_TOOL_STATUS = re.compile(
    r"znalaz[łl]em\s+\d+\s+wynik|"
    r"found\s+\d+\s+result",
    re.I,
)

_PROVIDER_ERRORS = (
    (
        re.compile(r"no\s+results\s+found", re.I),
        {
            "pl": "Nie udało mi się teraz znaleźć wyników. Mogę spróbować ponownie.",
            "en": "I couldn't find results right now. I can try again.",
        },
    ),
    (
        re.compile(r"timed?\s*out|timeout|connection\s+(?:error|refused)", re.I),
        {
            "pl": "Wyszukiwanie chwilowo nie odpowiada. Spróbujmy za moment.",
            "en": "Search is temporarily unavailable. Let's try again in a moment.",
        },
    ),
    (
        re.compile(r"rate\s*limit|too\s+many\s+requests", re.I),
        {
            "pl": "Za dużo zapytań do wyszukiwarki. Spróbuj za chwilę.",
            "en": "Search rate limit hit. Please try again shortly.",
        },
    ),
)


def is_ack_like(message: str) -> bool:
    return bool(_SHORT_ACK.match(str(message or "").strip()))


def looks_like_tool_status(message: str) -> bool:
    return bool(_TOOL_STATUS.search(str(message or "").strip()))


def map_provider_error(message: str, *, lang: str = "pl") -> Optional[str]:
    """Translate raw provider/tool errors for TTS. None if not a known raw error."""
    text = str(message or "").strip()
    if not text:
        return None
    code = (lang or "pl").lower()
    for pattern, variants in _PROVIDER_ERRORS:
        if pattern.search(text):
            return variants.get(code) or variants["pl"]
    # Generic English-only failure lines (short, no Polish letters).
    if re.fullmatch(r"[A-Za-z0-9 .,'\"!?-]{3,80}", text) and not re.search(
        r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]", text
    ):
        lowered = text.lower()
        if any(
            token in lowered
            for token in ("error", "fail", "unable", "cannot", "not found", "no result")
        ):
            if code == "en":
                return "Something went wrong with search. I can try again."
            return (
                "Nie udało mi się teraz znaleźć wyników. Mogę spróbować ponownie."
            )
    return None


def is_forbidden_web_final(message: str) -> bool:
    """ACK / tool-status / generic failure / raw snippet must not be web finals."""
    text = str(message or "").strip()
    if not text:
        return True
    if is_ack_like(text):
        return True
    if looks_like_tool_status(text):
        return True
    try:
        from core.web_synthesis import is_generic_failure, is_raw_snippet_dump

        if is_generic_failure(text) or is_raw_snippet_dump(text):
            return True
    except Exception:
        pass
    if map_provider_error(text) is not None and looks_like_tool_status(text):
        return True
    return False


def finalize_response(
    message: str,
    *,
    lang: str = "pl",
    ranked: Optional[Sequence[Any]] = None,
    query: str = "",
    kind: str = "answer",
    origin: str = ORIGIN_MODEL,
) -> Tuple[str, str]:
    """
    Central boundary for every user-facing string.

    kind: answer | ack | clarify | tool_fallback | tool_failure
    Returns (spoken_text, response_origin).
    """
    from core.response_language import coerce_to_session_language, looks_like_english

    text = str(message or "").strip()
    code = (lang or "pl").lower()

    if kind == "ack":
        if not text or looks_like_english(text):
            spoken = "Okej." if code == "pl" else "Okay."
        elif is_ack_like(text):
            spoken = text if text.endswith((".", "!", "?")) else text + "."
            if not spoken[0].isupper():
                spoken = spoken[0].upper() + spoken[1:]
        else:
            spoken = text
        return spoken, ORIGIN_META_ACK

    if kind == "tool_failure":
        mapped = map_provider_error(text, lang=code)
        if mapped:
            return mapped, ORIGIN_FALLBACK
        coerced = coerce_to_session_language(
            text or (
                "Nie udało mi się teraz znaleźć wyników. Mogę spróbować ponownie."
                if code == "pl"
                else "I couldn't find results right now."
            ),
            code,
            soft_fallback_ack=False,
        )
        # Never leave raw English provider text.
        if looks_like_english(coerced) and code == "pl":
            return (
                "Nie udało mi się teraz znaleźć wyników. Mogę spróbować ponownie.",
                ORIGIN_FALLBACK,
            )
        return coerced, ORIGIN_FALLBACK

    if kind == "tool_fallback" or looks_like_tool_status(text):
        if code == "en":
            return (
                "I found some sources.",
                ORIGIN_FALLBACK,
            )
        # Honest: do not promise a summary we will not speak.
        ranked_titles = []
        try:
            for row in ranked or []:
                title = str((row or {}).get("title") or "").strip()
                if title:
                    ranked_titles.append(title)
                    break
        except Exception:
            ranked_titles = []
        if ranked_titles:
            return (
                f"Znalazłem: {ranked_titles[0]}.",
                ORIGIN_FALLBACK,
            )
        return (
            "Mam wyniki z sieci.",
            ORIGIN_FALLBACK,
        )

    # kind == answer | clarify — NEVER treat ACK-like as success for answers.
    if kind == "answer" and is_ack_like(text):
        # Caller must replace; keep a neutral non-ACK fallback only if forced.
        return (
            (
                "Chwila — sprawdzam to dokładniej."
                if code == "pl"
                else "One moment — let me check that properly."
            ),
            ORIGIN_FALLBACK,
        )

    mapped = map_provider_error(text, lang=code)
    if mapped:
        return mapped, ORIGIN_FALLBACK

    coerced = coerce_to_session_language(
        text,
        code,
        ranked=ranked,
        query=query,
        soft_fallback_ack=False,
    )
    out_origin = origin or ORIGIN_MODEL
    try:
        from core.web_synthesis import is_generic_failure, is_raw_snippet_dump

        if is_generic_failure(coerced) or is_raw_snippet_dump(coerced):
            # Do not speak generic failure / snippet when evidence exists —
            # caller must run evidence repair. Return empty to force repair.
            if ranked:
                return "", ORIGIN_GENERIC_FAILURE
            return coerced, ORIGIN_GENERIC_FAILURE
    except Exception:
        pass
    if looks_like_english(coerced) and code == "pl":
        if ranked:
            # Signal repair — never dump English or generic failure as TTS.
            return "", ORIGIN_GENERIC_FAILURE
        return (
            "Nie udało mi się teraz dobrze odpowiedzieć. Spróbuj zapytać jeszcze raz.",
            ORIGIN_GENERIC_FAILURE,
        )
    try:
        from core.jarvis_response import polish_spoken

        coerced = polish_spoken(coerced)
    except Exception:
        pass
    return coerced, out_origin
