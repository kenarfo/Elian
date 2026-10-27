# ==========================
# ELIAN — web evidence synthesis (Phase 11.4.2)
# ==========================
"""Deterministic WHO/YES_NO answers from ranked evidence — never raw snippets."""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence, Tuple


Q_WHO = "WHO"
Q_WHAT = "WHAT"
Q_WHEN = "WHEN"
Q_WHERE = "WHERE"
Q_HOW_MUCH = "HOW_MUCH"
Q_YES_NO = "YES_NO"
Q_OTHER = "OTHER"

ORIGIN_WEB_SYNTHESIS = "WEB_SYNTHESIS"
ORIGIN_WEB_REPAIR = "WEB_REPAIR"
ORIGIN_WEB_EVIDENCE_FALLBACK = "WEB_EVIDENCE_FALLBACK"

_GENERIC_FAILURE = re.compile(
    r"(?:"
    r"nie\s+udało\s+mi\s+si[eę]\s+teraz\s+dobrze\s+odpowiedzie|"
    r"nie\s+udało\s+mi\s+si[eę]\s+sformułować|"
    r"spróbuj\s+zapytać\s+jeszcze\s+raz|"
    r"mam\s+wyniki,?\s+ale\s+nie\s+udało|"
    r"couldn'?t\s+(?:find|answer)|try\s+asking\s+again"
    r")",
    re.I,
)

_RAW_SNIPPET = re.compile(
    r"(?:"
    r"^według\s+\S+:\s*.{20,}|"
    r"^according\s+to\s+\S+:\s*.{20,}|"
    r"the\s+prime\s+minister\s+of\s+\w+\s+is\s+the\s+head\s+of|"
    r"is\s+the\s+head\s+of\s+government"
    r")",
    re.I,
)

_PERSON = re.compile(
    r"\b([A-ZÁÉÍÓÚÄÖÜ][a-zàáâãäåæçèéêëìíîïñòóôõöùúûüýÿ]+"
    r"(?:\s+[A-ZÁÉÍÓÚÄÖÜ][a-zàáâãäåæçèéêëìíîïñòóôõöùúûüýÿ]+)+)\b"
)

_GENERIC_TITLE = re.compile(
    r"(?:"
    r"^wynik\s+o\s*:|"
    r"^(?:pierwszy|drugi|trzeci)\s+wynik\b|"
    r"^result\s+(?:about|#?\d+|o:)\b"
    r")",
    re.I,
)

_THIN_TITLE = re.compile(
    r"(?:"
    r"—\s*(?:przegląd|dokumentacja|overview|wynik|result)\s*$|"
    r"\b(?:przegląd|dokumentacja)\s*$"
    r")",
    re.I,
)


def _hit_title(hit: Any) -> str:
    if isinstance(hit, dict):
        return str(hit.get("title") or "").strip()
    return str(getattr(hit, "title", "") or "").strip()


def _hit_snippet(hit: Any) -> str:
    if isinstance(hit, dict):
        return str(hit.get("snippet") or "").strip()
    return str(getattr(hit, "snippet", "") or "").strip()


def _first_sentence(text: str, *, limit: int = 140) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return ""
    parts = re.split(r"(?<=[.!?…])\s+", cleaned)
    sent = (parts[0] if parts else cleaned).strip()
    if len(sent) > limit:
        sent = sent[: limit - 1].rstrip(" ,;") + "…"
    return sent


def _title_is_weak(title: str) -> bool:
    t = str(title or "").strip()
    if not t:
        return True
    if _GENERIC_TITLE.search(t):
        return True
    if _THIN_TITLE.search(t):
        return True
    if len(t) < 12:
        return True
    return False


def evidence_briefs(ranked: Sequence[Any], *, max_items: int = 2) -> list[str]:
    """Prefer informative titles; else first sentence of snippet (not a raw dump)."""
    briefs: list[str] = []
    for hit in list(ranked or [])[: max(1, int(max_items))]:
        title = _hit_title(hit)
        snippet = _hit_snippet(hit)
        sent = _first_sentence(snippet) if snippet else ""
        if sent and _title_is_weak(title):
            briefs.append(sent)
        elif title and not _GENERIC_TITLE.search(title):
            briefs.append(title)
        elif sent:
            briefs.append(sent)
        elif title:
            briefs.append(title)
    return briefs

_ROLE_HINTS = (
    "prime minister",
    "premier",
    "president",
    "chancellor",
    "prezydent",
    "kanclerz",
)

_SKIP_NAMES = frozenset(
    {
        "prime minister",
        "the white house",
        "united states",
        "united kingdom",
        "new zealand",
        "wikipedia",
        "government of",
        "house of commons",
    }
)


def detect_question_type(text: str) -> str:
    raw = str(text or "").strip().lower()
    if not raw:
        return Q_OTHER
    if re.search(
        r"^(?:czy\s+to\s+prawda|na\s+pewno|serio|jeste[sś]\s+pewnie|"
        r"is\s+that\s+true|are\s+you\s+sure)",
        raw,
    ):
        return Q_YES_NO
    if raw.startswith("czy ") or raw.startswith("is ") or raw.startswith("are "):
        return Q_YES_NO
    if re.search(r"\bkto\b|\bwho\b", raw):
        return Q_WHO
    if re.search(r"\bile\b|\bhow\s+much\b|\bcena\b|\bkosztuje\b", raw):
        return Q_HOW_MUCH
    if re.search(r"\bkiedy\b|\bwhen\b", raw):
        return Q_WHEN
    if re.search(r"\bgdzie\b|\bwhere\b", raw):
        return Q_WHERE
    if re.search(r"\bco\b|\bwhat\b", raw):
        return Q_WHAT
    return Q_OTHER


def is_generic_failure(message: str) -> bool:
    return bool(_GENERIC_FAILURE.search(str(message or "")))


def is_raw_snippet_dump(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    if _RAW_SNIPPET.search(text):
        return True
    # Long English dump without a clear Polish WHO answer.
    from core.response_language import looks_like_english

    if looks_like_english(text) and len(text) > 80:
        return True
    return False


def is_useful_web_answer(message: str, *, question_type: str, query: str = "") -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    if is_generic_failure(text):
        return False
    if is_raw_snippet_dump(text):
        return False
    from core.response_finalize import is_ack_like, looks_like_tool_status

    if is_ack_like(text) or looks_like_tool_status(text):
        return False
    if question_type == Q_WHO:
        # Need a person-like token or explicit "jest X".
        if _PERSON.search(text):
            return True
        # Polish short form with known-looking capitalised name mid-sentence.
        if re.search(r"\bjest\s+[A-ZÁÉÍÓÚÄÖÜ][\w\-]+", text):
            return True
        # Definition-only answers fail WHO.
        if re.search(r"jest\s+(?:szefem|głow[aą]|head\s+of)", text, re.I):
            if not _PERSON.search(text):
                return False
        return False
    if question_type == Q_YES_NO:
        return bool(re.search(r"^\s*(tak|nie|yes|no)\b", text, re.I))
    return len(text) >= 8


def _clean_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(name or "")).strip(" .,;:-–|")
    # Drop trailing role fragments.
    cleaned = re.sub(
        r"\s+(?:prime\s+minister|premier|president|of\s+canada|of\s+the).*$",
        "",
        cleaned,
        flags=re.I,
    ).strip()
    return cleaned


def _is_plausible_person(name: str) -> bool:
    cleaned = _clean_name(name)
    if not cleaned or " " not in cleaned:
        return False
    low = cleaned.lower()
    if any(skip in low for skip in _SKIP_NAMES):
        return False
    if len(cleaned.split()) > 5:
        return False
    return bool(_PERSON.fullmatch(cleaned) or _PERSON.search(cleaned))


def extract_office_holder_name(ranked: Sequence[Any], *, query: str = "") -> str:
    """Extract current office-holder name from titles/snippets — not hardcoded."""
    qlow = str(query or "").lower()
    role_needles = list(_ROLE_HINTS)
    if "premier" in qlow or "prime minister" in qlow:
        role_needles = ["prime minister", "premier"] + role_needles
    if "prezydent" in qlow or "president" in qlow:
        role_needles = ["president", "prezydent"] + role_needles

    candidates: list[tuple[int, str]] = []
    for hit in ranked or []:
        title = str(getattr(hit, "title", "") or "")
        snippet = str(getattr(hit, "snippet", "") or "")
        source = str(getattr(hit, "source", "") or "")
        score = int(getattr(hit, "score", 0) or 0)
        blob = f"{title} {snippet}"
        blob_low = blob.lower()
        role_hit = any(n in blob_low for n in role_needles)
        # "Mark Carney - Prime Minister of Canada"
        m = re.match(r"^([^|\-–—]+?)\s*[-–—|]\s*(.+)$", title.strip())
        if m:
            left, right = m.group(1).strip(), m.group(2).strip()
            if _is_plausible_person(left) and (
                role_hit or any(n in right.lower() for n in role_needles)
            ):
                candidates.append((score + 40, _clean_name(left)))
            if _is_plausible_person(right) and any(n in left.lower() for n in role_needles):
                candidates.append((score + 15, _clean_name(right)))
        # "Prime Minister Mark Carney" / "premier Mark Carney" (no IGNORECASE on name class)
        for needle in role_needles:
            m2 = re.search(
                rf"(?i){re.escape(needle)}\s+"
                rf"([A-ZÁÉÍÓÚÄÖÜ][a-zàáâãäåæçèéêëìíîïñòóôõöùúûüýÿ]+"
                rf"(?:\s+[A-ZÁÉÍÓÚÄÖÜ][a-zàáâãäåæçèéêëìíîïñòóôõöùúûüýÿ]+)+)",
                blob,
            )
            if m2:
                name = _clean_name(m2.group(1))
                if _is_plausible_person(name):
                    bonus = 30 if (".gc.ca" in source or source.endswith(".gov")) else 12
                    candidates.append((score + bonus, name))
        # "is Mark Carney" / "jest Mark Carney"
        m3 = re.search(
            r"(?i)(?:\bis\b|\bjest\b)\s+"
            r"([A-ZÁÉÍÓÚÄÖÜ][a-zàáâãäåæçèéêëìíîïñòóôõöùúûüýÿ]+"
            r"(?:\s+[A-ZÁÉÍÓÚÄÖÜ][a-zàáâãäåæçèéêëìíîïñòóôõöùúûüýÿ]+)+)",
            blob,
        )
        if m3 and role_hit:
            name = _clean_name(m3.group(1))
            if _is_plausible_person(name):
                candidates.append((score + 18, name))

    if not candidates:
        return ""
    best: dict[str, int] = {}
    for score, name in candidates:
        # Reject role-fragment leftovers.
        low = name.lower()
        if any(tok in low for tok in ("of ", "the ", "minister", "premier", "president")):
            continue
        best[name] = max(best.get(name, 0), score) + 3
    if not best:
        return ""
    return max(best.items(), key=lambda kv: kv[1])[0]


def _office_label(query: str, *, lang: str) -> str:
    low = str(query or "").lower()
    if lang == "en":
        if "canada" in low or "kanad" in low:
            return "Prime Minister of Canada"
        if "uk" in low or "britain" in low or "bryta" in low:
            return "Prime Minister of the United Kingdom"
        if "usa" in low or "united states" in low or "stan" in low:
            return "President of the United States"
        if "premier" in low or "prime minister" in low:
            return "prime minister"
        if "president" in low or "prezydent" in low:
            return "president"
        return "officeholder"
    if "kanad" in low:
        return "premierem Kanady"
    if "bryta" in low or "uk" in low:
        return "premierem Wielkiej Brytanii"
    if "usa" in low or "stan" in low or "ameryk" in low:
        return "prezydentem Stanów Zjednoczonych"
    if "premier" in low:
        return "premierem"
    if "prezydent" in low:
        return "prezydentem"
    return "osobą na tym stanowisku"


def build_who_answer(name: str, query: str, *, lang: str = "pl") -> str:
    name = _clean_name(name)
    if not name:
        return ""
    label = _office_label(query, lang=lang)
    if lang == "en":
        return f"The current {label} is {name}."
    return f"Obecnym {label} jest {name}."


def synthesize_from_evidence(
    goal: str,
    ranked: Sequence[Any],
    *,
    lang: str = "pl",
    prefer_origin: str = ORIGIN_WEB_EVIDENCE_FALLBACK,
) -> Tuple[str, str]:
    """Build a short spoken answer from ranked hits. Never returns raw snippets."""
    goal_core = str(goal or "").split("\n(", 1)[0].strip()
    qtype = detect_question_type(goal_core)
    code = (lang or "pl").lower()

    try:
        from core.role_semantics import analyze_role_request, false_premise_reply

        role = analyze_role_request(goal_core)
        if not role.role_exists:
            return false_premise_reply(role, lang=code), prefer_origin
    except Exception:
        pass

    from core.web_grounding import authoritative_consensus

    consensus = authoritative_consensus(ranked) if ranked else None
    if consensus == "president:donald_trump":
        msg = (
            "Obecnym prezydentem Stanów Zjednoczonych jest Donald Trump."
            if code != "en"
            else "The current President of the United States is Donald Trump."
        )
        return msg, prefer_origin
    if consensus == "president:joe_biden":
        msg = (
            "Obecnym prezydentem Stanów Zjednoczonych jest Joe Biden."
            if code != "en"
            else "The current President of the United States is Joe Biden."
        )
        return msg, prefer_origin

    name = extract_office_holder_name(ranked, query=goal_core)
    if name and qtype in (Q_WHO, Q_OTHER, Q_WHAT):
        answer = build_who_answer(name, goal_core, lang=code)
        if answer:
            return answer, prefer_origin

    # Soft non-snippet fallback — still better than generic failure when we have hits.
    if ranked and code != "en":
        top = ranked[0]
        title = str(getattr(top, "title", "") or "").strip()
        # Prefer short title if it looks like a person page.
        if _is_plausible_person(title.split("-")[0].strip()):
            person = _clean_name(title.split("-")[0])
            answer = build_who_answer(person, goal_core, lang=code)
            if answer:
                return answer, prefer_origin
        # Informational / WHAT queries: prefer useful title, else snippet sentence.
        if qtype not in (Q_WHO,):
            briefs = evidence_briefs(ranked, max_items=2)
            if briefs:
                if len(briefs) == 1:
                    return (f"W skrócie: {briefs[0]}", prefer_origin)
                return (
                    f"W skrócie: {briefs[0]} Dodatkowo: {briefs[1]}",
                    prefer_origin,
                )
        return (
            "Według aktualnych źródeł znalazłem potwierdzenie, "
            "ale nie wyodrębniłem jednoznacznie nazwiska. Mogę sprawdzić jeszcze raz.",
            prefer_origin,
        )
    if ranked:
        return (
            "Current sources confirm an answer, but I could not extract a clear name.",
            prefer_origin,
        )
    return ("", prefer_origin)


def repair_web_answer(
    message: str,
    goal: str,
    ranked: Sequence[Any],
    *,
    lang: str = "pl",
    grounding_status: str = "",
) -> Tuple[str, str]:
    """
    If model/coerce produced a useless final despite SUPPORTED evidence,
    replace with evidence-based synthesis.
    """
    goal_core = str(goal or "").split("\n(", 1)[0].strip()
    qtype = detect_question_type(goal_core)
    text = str(message or "").strip()
    supported = str(grounding_status or "").upper() in ("SUPPORTED", "PARTIAL")

    if ranked and (supported or ranked):
        if not is_useful_web_answer(text, question_type=qtype, query=goal_core):
            synth, origin = synthesize_from_evidence(
                goal_core,
                ranked,
                lang=lang,
                prefer_origin=ORIGIN_WEB_REPAIR
                if text
                else ORIGIN_WEB_EVIDENCE_FALLBACK,
            )
            if synth and is_useful_web_answer(
                synth, question_type=qtype, query=goal_core
            ):
                return synth, origin
            if synth:
                return synth, ORIGIN_WEB_EVIDENCE_FALLBACK
    return text, ORIGIN_WEB_SYNTHESIS if text else ORIGIN_WEB_EVIDENCE_FALLBACK
