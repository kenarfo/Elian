# ==========================
# ELIAN — freshness + claim detection (Phase 11.2)
# ==========================
"""Semantic freshness / factual-claim prepass — not a closed command router."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


VERIFY_SUPPORTED = "SUPPORTED"
VERIFY_CONTRADICTED = "CONTRADICTED"
VERIFY_UNCERTAIN = "UNCERTAIN"
VERIFY_NOT_CHECKED = "NOT_CHECKED"

SEMANTIC_QUESTION = "QUESTION"
SEMANTIC_CLAIM = "CLAIM"
SEMANTIC_OPINION = "OPINION"
SEMANTIC_META = "META"
SEMANTIC_OTHER = "OTHER"


_OPINION = re.compile(
    r"(?:"
    r"\b(?:fajn\w*|super|nudn\w*|ok|dobre|dobry|dobra|kocham|lubi[eę]|nienawidz|"
    r"denerwuje|świetn\w*|swietn\w*|beznadziej\w*)\b|"
    r"\bmasz\s+(?:fajny|ładny|ladny)\s+głos\b"
    r")",
    re.I,
)

# Meta detection lives in core.meta_ack (Phase 11.4) — re-exported below.

# Roles / offices that change over time (no "teraz" required).
_OFFICE_ROLE = re.compile(
    r"\b(?:"
    r"prezydent\w*|premier\w*|kanclerz\w*|kr[oó]l\w*|papie[zż]\w*|"
    r"prezes\w*|ceo|cto|dyrektor\w*\s+generaln|"
    r"minister\w*|gubernator\w*|burmistrz\w*|marsza[lł]ek\w*"
    r")\b",
    re.I,
)

_PRICE = re.compile(
    r"\b(?:"
    r"ile\s+kosztuje|kosztuje|cena|cen[eę]|price|"
    r"kurs\s+(?:dolara|euro|funta|btc|bitcoina)|"
    r"ile\s+jest\s+wart"
    r")\b",
    re.I,
)

_SPORT = re.compile(
    r"\b(?:"
    r"mecz|wynik|tabel[ai]|liga|premier\s+league|ekstraklasa|"
    r"prowadzi\s+w\s+tabel|"
    r"kto\s+wygra[lł]|kto\s+prowadzi"
    r")\b",
    re.I,
)

_WEATHER = re.compile(
    r"\b(?:pogod[ay]|temperatura\s+na\s+zewn|b[eę]dzie\s+padac|"
    r"prognoz[ay]\s+pogody)\b",
    re.I,
)

_NEWS = re.compile(
    r"\b(?:"
    r"wiadomo[sś]ci|newsy?|co\s+wydarzy|"
    r"najnowsz\w+|aktualn\w+\s+wersj|"
    r"ju[zż]\s+wysz[eę][lł]a?|released|availability|dost[eę]pno[sś][cć]"
    r")\b",
    re.I,
)

_EXPLICIT_TIME = re.compile(
    r"\b(?:teraz|obecnie|aktualnie|dzisiaj|dzi[sś]|wczoraj|current|now)\b",
    re.I,
)

_WHO_IS = re.compile(
    r"\bkto\s+(?:jest|zosta[lł]|pe[lł]ni)\b",
    re.I,
)

_CLAIM_IS = re.compile(
    r"^(?P<sub>.+?)\s+jest\s+(?P<pred>.+?)[.!?]*$",
    re.I,
)

_FLAT_EARTH_STYLE = re.compile(
    r"\b(?:ziemia\s+jest\s+p[lł]aska|ksi[eę][zż]yc\s+jest\s+z\s+sera)\b",
    re.I,
)

_STOP_PERSON = frozenset(
    {
        "nie", "jest", "chcę", "chce", "zgadzam", "zgadzam się", "zgadzam sie",
        "rozumiem", "okej", "ok", "dobra", "dobrze", "spoko", "teraz", "wiem",
        "aha", "hmm", "elian", "spotify", "youtube", "discord", "co", "to",
        "jestem", "jesteś", "jestes", "było", "bylo", "będzie", "bedzie",
        "według", "wedlug", "źródeł", "zrodel", "aktualnych", "wiarygodnych",
        "prezydentem", "premierem", "czarna", "dziura", "czarne",
    }
)


@dataclass
class FreshnessAnalysis:
    semantic_kind: str = SEMANTIC_OTHER
    freshness_required: bool = False
    is_question: bool = False
    is_factual_claim: bool = False
    is_opinion: bool = False
    is_meta: bool = False
    search_query: str = ""
    claim: str = ""
    categories: List[str] = field(default_factory=list)
    verification_status: str = VERIFY_NOT_CHECKED

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def split_meta_compound(text: str) -> tuple[str, str]:
    from core.meta_ack import split_meta_compound as _split

    return _split(text)


def is_meta_utterance(text: str) -> bool:
    from core.meta_ack import is_meta_utterance as _meta

    return _meta(text)


def meta_ack_phrase() -> str:
    from core.meta_ack import meta_ack_phrase as _phrase

    return _phrase()


def requires_fresh_information(text: str) -> bool:
    return analyze_freshness(text).freshness_required


def _categories_for(text: str) -> List[str]:
    cats: List[str] = []
    if _OFFICE_ROLE.search(text):
        cats.append("office_role")
    if _PRICE.search(text):
        cats.append("price")
    if _SPORT.search(text):
        cats.append("sport")
    if _WEATHER.search(text):
        cats.append("weather")
    if _NEWS.search(text):
        cats.append("news")
    if _EXPLICIT_TIME.search(text):
        cats.append("explicit_time")
    return cats


_QUESTION_SUBJECT = re.compile(
    r"^(?:jaki|jaka|jakie|jaką|jacy|czy|kto|ile|co)\b",
    re.I,
)


def _is_freshness_sensitive(text: str, cats: List[str]) -> bool:
    if not cats:
        return False
    # Standing office / role questions or claims are always freshness-sensitive.
    if "office_role" in cats:
        return True
    if "price" in cats or "sport" in cats or "weather" in cats or "news" in cats:
        return True
    if "explicit_time" in cats:
        if _WHO_IS.search(text) or _PRICE.search(text):
            return True
        claim = _CLAIM_IS.match(text.strip())
        if claim and not _QUESTION_SUBJECT.search(text.strip()):
            return True
    return False


def _build_search_query(text: str, *, kind: str, cats: List[str]) -> str:
    from core.meta_ack import strip_meta_noise_from_query
    from core.role_semantics import analyze_role_request
    from core.web_grounding import clean_web_query

    lowered = strip_meta_noise_from_query(str(text or "").strip()) or str(text or "").strip()
    lowered = clean_web_query(lowered)
    low = lowered.lower()

    role = analyze_role_request(lowered)
    if not role.role_exists and role.requested_role == "president":
        # Do not silently remap president → head of state for search.
        return ""
    if role.search_query:
        return role.search_query

    if "office_role" in cats and ("prezydent" in low or "president" in low) and (
        "usa" in low or "stanów" in low or "stanow" in low or "ameryk" in low
        or "united states" in low
    ):
        return "current president of the United States"
    if "office_role" in cats and "premier" in low and (
        "bryta" in low or "uk" in low or "england" in low
    ):
        return "current Prime Minister of the United Kingdom"
    if "office_role" in cats and ("prezes" in low or "ceo" in low) and "openai" in low:
        return "current OpenAI CEO"
    if "price" in cats and "rtx" in low:
        m = re.search(r"rtx\s*\d+", low, re.I)
        chip = m.group(0) if m else "GPU"
        return f"current price {chip}"
    if "price" in cats and "dolar" in low:
        return "current USD PLN exchange rate"
    if kind == SEMANTIC_CLAIM and "office_role" in cats:
        return "current president of the United States" if "prezydent" in low else lowered
    if kind == SEMANTIC_QUESTION:
        if "office_role" in cats:
            # Prefer cleaned English-ish role query without conversational noise.
            return f"current {lowered}" if lowered else ""
        return lowered
    return lowered


def analyze_freshness(text: str, *, skip_local_guard: bool = False) -> FreshnessAnalysis:
    raw = str(text or "").strip()
    if not raw:
        return FreshnessAnalysis()

    # Strip planner focus dump if present.
    goal = raw.split("\n(", 1)[0].strip()

    # Phase 12.3.2 — local device / runtime state is live, but not internet-fresh.
    if not skip_local_guard:
        try:
            from core.local_device_status import classify_local_request

            local = classify_local_request(goal)
            if local.kind in ("LOCAL_HA", "LOCAL_RUNTIME") or (
                local.kind == "KNOWLEDGE" and not local.web_allowed and local.intent
            ):
                return FreshnessAnalysis(
                    semantic_kind=SEMANTIC_QUESTION if goal.endswith("?") else SEMANTIC_OTHER,
                    freshness_required=False,
                    is_question=bool(goal.endswith("?") or local.intent),
                    categories=["local_source", local.kind.lower()],
                    claim=goal,
                )
        except Exception:
            pass

    meta_kind, meta_rest = split_meta_compound(goal)
    if meta_kind == "meta":
        return FreshnessAnalysis(
            semantic_kind=SEMANTIC_META,
            is_meta=True,
            freshness_required=False,
        )
    if meta_kind == "compound" and meta_rest:
        goal = meta_rest

    low = goal.lower()
    cats = _categories_for(goal)
    is_q = goal.endswith("?") or bool(_WHO_IS.search(goal)) or low.startswith(
        ("kto ", "ile ", "jaki ", "jaka ", "jakie ", "co wydarzy")
    )

    # False-premise office roles (e.g. UK president) — no silent remap.
    try:
        from core.role_semantics import analyze_role_request

        role = analyze_role_request(goal)
        if not role.role_exists and is_q:
            return FreshnessAnalysis(
                semantic_kind=SEMANTIC_QUESTION,
                freshness_required=False,
                is_question=True,
                search_query="",
                categories=cats,
                claim=goal,
            )
        if role.search_query and is_q and "office_role" in cats:
            return FreshnessAnalysis(
                semantic_kind=SEMANTIC_QUESTION,
                freshness_required=True,
                is_question=True,
                search_query=role.search_query,
                categories=cats,
                claim=goal,
            )
    except Exception:
        pass

    # Opinion about apps / taste — not a verifyable current office fact.
    if _OPINION.search(goal) and "office_role" not in cats and "price" not in cats:
        # "Spotify jest fajne" / compliments
        if not is_q:
            return FreshnessAnalysis(
                semantic_kind=SEMANTIC_OPINION,
                is_opinion=True,
                freshness_required=False,
            )

    claim_match = _CLAIM_IS.match(goal)
    is_claim = bool(claim_match) and not is_q
    # "Ziemia jest płaska" — factual-ish but not freshness-sensitive.
    if is_claim and _FLAT_EARTH_STYLE.search(goal):
        return FreshnessAnalysis(
            semantic_kind=SEMANTIC_CLAIM,
            is_factual_claim=True,
            freshness_required=False,
            claim=goal,
        )

    fresh = _is_freshness_sensitive(goal, cats)

    if is_q and fresh:
        return FreshnessAnalysis(
            semantic_kind=SEMANTIC_QUESTION,
            freshness_required=True,
            is_question=True,
            search_query=_build_search_query(goal, kind=SEMANTIC_QUESTION, cats=cats),
            categories=cats,
            claim=goal,
        )

    if is_claim and fresh:
        claim_text = goal
        return FreshnessAnalysis(
            semantic_kind=SEMANTIC_CLAIM,
            freshness_required=True,
            is_factual_claim=True,
            search_query=_build_search_query(goal, kind=SEMANTIC_CLAIM, cats=cats),
            categories=cats,
            claim=claim_text,
        )

    if is_q:
        return FreshnessAnalysis(
            semantic_kind=SEMANTIC_QUESTION,
            freshness_required=fresh,
            is_question=True,
            search_query=_build_search_query(goal, kind=SEMANTIC_QUESTION, cats=cats)
            if fresh
            else "",
            categories=cats,
        )

    if is_claim:
        return FreshnessAnalysis(
            semantic_kind=SEMANTIC_CLAIM,
            freshness_required=False,
            is_factual_claim=True,
            claim=goal,
            categories=cats,
        )

    return FreshnessAnalysis(
        semantic_kind=SEMANTIC_OTHER,
        freshness_required=False,
        categories=cats,
    )


def build_claim_verification(
    *,
    claim: str,
    freshness_required: bool,
    web_checked: bool,
    status: str = VERIFY_NOT_CHECKED,
    evidence_sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "claim": claim,
        "freshness_required": bool(freshness_required),
        "web_checked": bool(web_checked),
        "status": status if web_checked else VERIFY_NOT_CHECKED,
        "evidence_sources": list(evidence_sources or []),
    }


def extract_person_candidates(text: str) -> List[str]:
    """Conservative named-person extraction — no random capitalized Polish words."""
    raw = str(text or "")
    # Multi-word Proper Names only (e.g. Markus Persson, Joe Biden, Donald Trump).
    pattern = re.compile(
        r"\b([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)+)\b"
    )
    out: List[str] = []
    for match in pattern.findall(raw):
        parts = match.split()
        if any(p.lower() in _STOP_PERSON for p in parts):
            continue
        if match.lower() in _STOP_PERSON:
            continue
        out.append(match)
    return out
