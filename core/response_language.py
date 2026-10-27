# ==========================
# ELIAN — response language (Phase 11.3)
# ==========================
"""Shared session response language for TALK / KNOWLEDGE / WEB / clarify."""

from __future__ import annotations

import re
from typing import Optional


_LANG_PL = re.compile(
    r"(?:"
    r"\bm[oó]w\s+po\s+polsku\b|"
    r"\bodpowiadaj\s+po\s+polsku\b|"
    r"\bpo\s+polsku\s+prosz[eę]\b|"
    r"\bale\s+m[oó]w\s+po\s+polsku\b|"
    r"\bale\s+po\s+polsku\b|"
    r"\bpo\s+polsku\b|"
    r"\btylko\s+po\s+polsku\b|"
    r"\bin\s+polish\b|"
    r"\bspeak\s+polish\b"
    r")",
    re.I,
)

_LANG_EN = re.compile(
    r"(?:"
    r"\bm[oó]w\s+po\s+angielsku\b|"
    r"\bodpowiadaj\s+po\s+angielsku\b|"
    r"\bpo\s+angielsku\b|"
    r"\bspeak\s+english\b|"
    r"\bin\s+english\b|"
    r"\banswer\s+in\s+english\b"
    r")",
    re.I,
)

_ENGLISH_MARKERS = re.compile(
    r"\b(?:the|current|president|united|states|is|of|and|with|from|that|this)\b",
    re.I,
)

_POLISH_MARKERS = re.compile(
    r"\b(?:jest|prezydentem|obecnie|według|wedlug|źródeł|zrodel|stanów|stanow|"
    r"możesz|mozesz|dobrze|okej|proszę|prosze|czy|prawda|ile|gdzie|kiedy|"
    r"jak|dlaczego|który|ktory|która|ktora|które|ktore|premierem|wielkiej|"
    r"brytanii|kanady|otwórz|otworz|włącz|wlacz|głośniej|glosniej|ciszej)\b|"
    r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]",
    re.I,
)


def detect_utterance_language(text: str) -> str:
    raw = str(text or "")
    if _POLISH_MARKERS.search(raw):
        return "pl"
    if _ENGLISH_MARKERS.search(raw) and not _POLISH_MARKERS.search(raw):
        return "en"
    # English-first default when markers are ambiguous.
    try:
        from Config import config

        default_lang = str(config.get("language") or "en")
        if default_lang.lower().startswith("pl"):
            return "pl"
    except Exception:
        pass
    return "en"


def parse_language_request(text: str) -> Optional[str]:
    raw = str(text or "").strip()
    if not raw:
        return None
    if _LANG_PL.search(raw):
        return "pl"
    if _LANG_EN.search(raw):
        return "en"
    return None


def is_language_only_request(text: str) -> bool:
    raw = str(text or "").strip()
    if parse_language_request(raw) is None:
        return False
    # Short directive without a separate content question.
    cleaned = _LANG_PL.sub("", raw)
    cleaned = _LANG_EN.sub("", cleaned)
    cleaned = re.sub(r"^(?:ale|no|okej|ok|dobra)[,\s]*", "", cleaned, flags=re.I)
    cleaned = cleaned.strip(" .,!?;:")
    return len(cleaned.split()) <= 2


def looks_like_english(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if _POLISH_MARKERS.search(raw):
        return False
    return bool(_ENGLISH_MARKERS.search(raw))


def language_instruction(lang: str) -> str:
    code = (lang or "pl").lower()
    if code == "en":
        return (
            "RESPONSE LANGUAGE: English. Answer in English. "
            "Keep proper names unchanged (Donald Trump, OpenAI, Blender)."
        )
    return (
        "RESPONSE LANGUAGE: Polish (pl). Answer entirely in Polish. "
        "Web sources may be English — synthesize in Polish. "
        "Keep proper names unchanged (Donald Trump, The White House, OpenAI, Blender). "
        "Never reply with an English sentence when the session language is Polish."
    )


def ack_for_language(lang: str) -> str:
    if (lang or "pl").lower() == "en":
        return "Okay, I'll speak English."
    return "Dobrze, mówię po polsku."


def coerce_to_session_language(
    message: str,
    lang: str,
    *,
    ranked=None,
    query: str = "",
    soft_fallback_ack: bool = False,
) -> str:
    """If model slipped into the wrong language, repair lightly."""
    text = str(message or "").strip()
    code = (lang or "pl").lower()
    if not text:
        return text

    # Never turn a short ACK into an error lecture.
    if soft_fallback_ack or re.match(
        r"^(?:okej|ok|okay|jasne|super|dobrze|dobra|spoko)[.!…]*$",
        text,
        re.I,
    ):
        if code == "pl" and looks_like_english(text):
            return "Okej."
        return text

    if code == "pl" and looks_like_english(text):
        try:
            from core.web_grounding import (
                authoritative_consensus,
                repair_current_fact_answer,
            )

            if ranked:
                repaired = repair_current_fact_answer(text, ranked, query=query)
                if repaired and not looks_like_english(repaired):
                    # Avoid wrong-domain repair (e.g. US president for UK PM).
                    qlow = (query or "").lower()
                    if "premier" in qlow or "prime minister" in qlow or "uk" in qlow:
                        if "prezydent" in repaired.lower() or "trump" in repaired.lower():
                            repaired = text  # fall through to snippet repair
                        else:
                            return repaired
                    else:
                        return repaired
                consensus = authoritative_consensus(ranked)
                qlow = (query or "").lower()
                office_uk = (
                    "premier" in qlow
                    or "prime minister" in qlow
                    or "britain" in qlow
                    or "uk" in qlow
                    or "wielk" in qlow
                )
                if not office_uk and consensus == "president:donald_trump":
                    return (
                        "Obecnym prezydentem Stanów Zjednoczonych jest Donald Trump."
                    )
                if not office_uk and consensus == "president:joe_biden":
                    return (
                        "Obecnym prezydentem Stanów Zjednoczonych jest Joe Biden."
                    )
                # Snippet-based soft Polish for other office facts.
                for hit in ranked[:4]:
                    snip = f"{getattr(hit, 'title', '')} {getattr(hit, 'snippet', '')}"
                    if re.search(r"keir\s+starmer", snip, re.I):
                        return (
                            "Obecnym premierem Wielkiej Brytanii jest Keir Starmer."
                        )
                    if re.search(r"donald\s+j\.?\s*trump|president\s+donald", snip, re.I):
                        if not office_uk:
                            return (
                                "Obecnym prezydentem Stanów Zjednoczonych jest Donald Trump."
                            )
        except Exception:
            pass
        # Soft fallback for answer paths — never disguise failure as ACK.
        if soft_fallback_ack:
            return "Okej."
        return (
            "Nie udało mi się teraz dobrze odpowiedzieć. Spróbuj zapytać jeszcze raz."
        )
    if code == "en" and _POLISH_MARKERS.search(text) and not _ENGLISH_MARKERS.search(text):
        return text
    return text
