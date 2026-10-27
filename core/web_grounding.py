# ==========================
# ELIAN — web grounding (Phase 11.1)
# ==========================
"""Source ranking + synthesis grounding contract (per interaction)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse


GROUNDING_SUPPORTED = "SUPPORTED"
GROUNDING_PARTIAL = "PARTIAL"
GROUNDING_CONFLICT = "CONFLICT"
GROUNDING_NONE = "NONE"
GROUNDING_INSUFFICIENT = "INSUFFICIENT"

_CONVERSATIONAL_PREFIX = re.compile(
    r"^(?:"
    r"ok|okej|okay|dobra|dobrze|spoko|jasne|"
    r"inaczej|no\s+dobra|no\s+to|to\s+sprawd[zź]|sprawd[zź]|"
    r"w\s+takim\s+razie|a\s+wi[eę]c"
    r")[,!.…]+\s*",
    re.I,
)

# Imperative search wrappers — keep the topic, drop the verb shell.
_SEARCH_WRAPPER = re.compile(
    r"^(?:"
    r"(?:wyszukaj|poszukaj|szukaj|znajd[zź]|wygoogluj)"
    r"(?:\s+(?:mi|prosz[eę]|w\s+internecie))*\s+"
    r"(?:informacje\s+(?:o\s+)?|co[sś]\s+o\s+|o\s+)?"
    r")",
    re.I,
)

_CURRENT_FACT = re.compile(
    r"\b(?:teraz|obecnie|aktualnie|dzisiaj|dzi[sś]|current|now)\b|"
    r"\bkto\s+jest\b",
    re.I,
)

_DOMAIN_RANK: List[tuple[str, int]] = [
    ("whitehouse.gov", 100),
    ("usa.gov", 95),
    ("reuters.com", 70),
    ("apnews.com", 70),
    ("bbc.com", 65),
    ("bbc.co.uk", 65),
    ("nytimes.com", 60),
    ("washingtonpost.com", 60),
    ("cnn.com", 55),
    ("britannica.com", 45),
    ("wikipedia.org", 40),
]


@dataclass
class RankedHit:
    title: str = ""
    url: str = ""
    snippet: str = ""
    source: str = ""
    published_at: str = ""
    score: int = 0
    claims: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "published_at": self.published_at,
            "score": self.score,
        }


def clean_web_query(query: str) -> str:
    text = str(query or "").strip()
    if not text:
        return ""
    try:
        from core.meta_ack import split_meta_compound, strip_meta_noise_from_query

        kind, rest = split_meta_compound(text)
        if kind == "compound" and rest:
            text = rest
        elif kind == "meta":
            return ""
        text = strip_meta_noise_from_query(text) or text
    except Exception:
        pass
    for _ in range(4):
        next_text = _CONVERSATIONAL_PREFIX.sub("", text).strip(" .,!?")
        if next_text == text:
            break
        text = next_text
    wrapped = _SEARCH_WRAPPER.sub("", text).strip(" .,!?")
    if wrapped:
        text = wrapped
    # Never ship "current wiem…" style noise.
    low = text.lower()
    if re.search(r"\b(?:wiem|rozumiem|kumam|okej|ok)\b", low) and re.search(
        r"\b(?:kto|prezydent|premier|ile|cena)\b", low
    ):
        text = re.sub(
            r"\b(?:okej|ok|okay|no|dobra|wiem|rozumiem|kumam|ale|to)\b[,.]*\s*",
            "",
            text,
            flags=re.I,
        ).strip(" .,!?")
    return text.strip(" .,!?") or str(query or "").strip()


def looks_like_current_fact_query(query: str) -> bool:
    try:
        from core.freshness import analyze_freshness

        return bool(analyze_freshness(query).freshness_required)
    except Exception:
        return bool(_CURRENT_FACT.search(str(query or "")))


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def score_source(url: str = "", source: str = "", *, current_fact: bool = False) -> int:
    domain = (source or _domain(url) or "").lower()
    score = 10
    if domain.endswith(".gov") or ".gov." in domain:
        score = max(score, 80)
    for needle, points in _DOMAIN_RANK:
        if domain == needle or domain.endswith("." + needle):
            score = max(score, points)
    if current_fact and ("whitehouse.gov" in domain or domain.endswith(".gov")):
        score += 15
    if current_fact and "wikipedia.org" in domain:
        score -= 5
    return score


def _extract_person_claims(text: str) -> List[str]:
    lowered = str(text or "").lower()
    claims: List[str] = []
    if "donald trump" in lowered or re.search(r"\btrump\b", lowered):
        claims.append("president:donald_trump")
    if "joe biden" in lowered or re.search(r"\bbiden\b", lowered):
        claims.append("president:joe_biden")
    return claims


def rank_hits(
    results: Sequence[Dict[str, Any]] | Sequence[Any],
    *,
    query: str = "",
) -> List[RankedHit]:
    current = looks_like_current_fact_query(query)
    ranked: List[RankedHit] = []
    for item in results or []:
        if hasattr(item, "to_dict"):
            data = item.to_dict()
        elif isinstance(item, dict):
            data = item
        else:
            continue
        title = str(data.get("title") or "")
        url = str(data.get("url") or "")
        snippet = str(data.get("snippet") or "")
        source = str(data.get("source") or _domain(url))
        blob = f"{title} {snippet}"
        ranked.append(
            RankedHit(
                title=title,
                url=url,
                snippet=snippet,
                source=source,
                published_at=str(data.get("published_at") or ""),
                score=score_source(url, source, current_fact=current),
                claims=_extract_person_claims(blob),
            )
        )
    ranked.sort(key=lambda h: h.score, reverse=True)
    return ranked


def detect_claim_conflict(ranked: Sequence[RankedHit]) -> bool:
    bags = {
        claim
        for hit in ranked[:6]
        for claim in hit.claims
        if claim.startswith("president:")
    }
    return len(bags) >= 2


def authoritative_consensus(ranked: Sequence[RankedHit]) -> Optional[str]:
    if not ranked:
        return None
    top = list(ranked[:4])
    preferred = [h for h in top if h.score >= 70] or top[:2]
    counts: Dict[str, int] = {}
    for hit in preferred:
        for claim in hit.claims:
            if claim.startswith("president:"):
                counts[claim] = counts.get(claim, 0) + hit.score
    if not counts:
        return None
    winner = max(counts.items(), key=lambda kv: kv[1])[0]
    scores = sorted(counts.values(), reverse=True)
    if len(scores) >= 2 and scores[1] >= scores[0] * 0.85:
        return None
    return winner


def answer_supports_claim(answer: str, claim: str) -> bool:
    lowered = str(answer or "").lower()
    if claim == "president:donald_trump":
        return bool(re.search(r"\btrump\b", lowered))
    if claim == "president:joe_biden":
        return bool(re.search(r"\bbiden\b", lowered)) and not re.search(
            r"\btrump\b", lowered
        )
    token = claim.split(":", 1)[-1].replace("_", " ")
    return token in lowered


def evaluate_grounding(
    answer: str,
    ranked: Sequence[RankedHit],
    *,
    fresh_web: bool,
    query: str = "",
) -> Dict[str, Any]:
    if not fresh_web or not ranked:
        return {
            "grounded_in_web": False,
            "grounding_status": GROUNDING_NONE,
            "fresh_web_observation_this_turn": bool(fresh_web),
            "sources_used": [],
            "consensus_claim": None,
        }

    conflict = detect_claim_conflict(ranked)
    consensus = authoritative_consensus(ranked)
    sources_used = [h.to_dict() for h in ranked[:5]]

    if conflict and not consensus:
        return {
            "grounded_in_web": False,
            "grounding_status": GROUNDING_CONFLICT,
            "fresh_web_observation_this_turn": True,
            "sources_used": sources_used,
            "consensus_claim": None,
        }

    if consensus and looks_like_current_fact_query(query):
        if answer_supports_claim(answer, consensus):
            return {
                "grounded_in_web": True,
                "grounding_status": GROUNDING_SUPPORTED,
                "fresh_web_observation_this_turn": True,
                "sources_used": sources_used,
                "consensus_claim": consensus,
            }
        return {
            "grounded_in_web": False,
            "grounding_status": GROUNDING_CONFLICT,
            "fresh_web_observation_this_turn": True,
            "sources_used": sources_used,
            "consensus_claim": consensus,
        }

    top = ranked[0]
    tokens = [
        t
        for t in re.findall(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{4,}", top.title)
        if t.lower() not in {"http", "https", "www", "the", "and"}
    ]
    hits = sum(1 for t in tokens[:6] if t.lower() in str(answer or "").lower())
    claim_hit = any(
        c.split(":")[-1].replace("_", " ").split()[0] in str(answer or "").lower()
        for c in top.claims
    )
    if hits >= 1 or claim_hit:
        return {
            "grounded_in_web": True,
            "grounding_status": GROUNDING_SUPPORTED if hits >= 2 else GROUNDING_PARTIAL,
            "fresh_web_observation_this_turn": True,
            "sources_used": sources_used,
            "consensus_claim": consensus,
        }
    return {
        "grounded_in_web": False,
        "grounding_status": GROUNDING_INSUFFICIENT,
        "fresh_web_observation_this_turn": True,
        "sources_used": sources_used,
        "consensus_claim": consensus,
    }


def synthesis_hint(ranked: Sequence[RankedHit], *, query: str = "") -> str:
    lines = []
    for index, hit in enumerate(ranked[:5], start=1):
        lines.append(
            f"{index}. score={hit.score} domain={hit.source} "
            f"title={hit.title!r} snippet={hit.snippet[:180]!r}"
        )
    consensus = authoritative_consensus(ranked)
    conflict = detect_claim_conflict(ranked)
    parts = [
        "RANKED WEB EVIDENCE (prefer higher score; for current facts prefer .gov / whitehouse):",
        *lines,
    ]
    if consensus:
        parts.append(f"AUTHORITATIVE CONSENSUS CLAIM: {consensus}")
    if conflict and not consensus:
        parts.append(
            "CONFLICTING CLAIMS across sources — do NOT guess; say wyniki są niespójne "
            "or fetch a higher-authority page."
        )
    elif consensus and looks_like_current_fact_query(query):
        parts.append(
            "For this current-fact question, your final_answer MUST agree with the "
            "authoritative consensus. Do not prefer older historical snippets."
        )
    return "\n".join(parts)


def repair_current_fact_answer(
    answer: str,
    ranked: Sequence[RankedHit],
    *,
    query: str = "",
) -> str:
    if not looks_like_current_fact_query(query):
        return answer
    consensus = authoritative_consensus(ranked)
    if not consensus:
        if detect_claim_conflict(ranked):
            return (
                "Źródła podają sprzeczne informacje — nie chcę zgadywać. "
                "Mogę sprawdzić oficjalną stronę whitehouse.gov."
            )
        return answer
    if answer_supports_claim(answer, consensus):
        return answer
    if consensus == "president:donald_trump":
        return (
            "Według aktualnych, wiarygodnych źródeł prezydentem USA jest Donald Trump."
        )
    if consensus == "president:joe_biden":
        return (
            "Według aktualnych, wiarygodnych źródeł prezydentem USA jest Joe Biden."
        )
    return answer
