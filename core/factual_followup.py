# ==========================
# ELIAN — factual claim follow-up (Phase 11.4.2)
# ==========================
"""Resolve 'Czy to prawda?' to the previous factual assistant claim."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional


_VERIFY_FOLLOWUP = re.compile(
    r"^(?:"
    r"(?:no[,.\s]+|okej[,.\s]+|ok[,.\s]+)?"
    r"(?:"
    r"czy\s+to\s+prawda|"
    r"czy\s+to\s+si[eę]\s+zgadza|"
    r"czy\s+na\s+pewno|"
    r"na\s+pewno|"
    r"serio|"
    r"naprawd[eę]|"
    r"jeste[sś]\s+pewnie?n|"
    r"czy\s+jeste[sś]\s+pewnie?n|"
    r"to\s+prawda\??|"
    r"is\s+that\s+true|"
    r"are\s+you\s+sure"
    r")"
    r")[?.!…]*$",
    re.I,
)


@dataclass
class ClaimReferent:
    claim_text: str = ""
    topic: str = ""
    web_query: str = ""
    web_consensus: str = ""
    people: tuple = ()
    has_web_evidence: bool = False


def is_claim_verification_followup(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    return bool(_VERIFY_FOLLOWUP.match(raw))


def resolve_claim_referent(focus: Dict[str, Any] | None) -> Optional[ClaimReferent]:
    if not isinstance(focus, dict):
        return None
    last = str(focus.get("last_answer_summary") or "").strip()
    topic = str(focus.get("current_topic") or focus.get("last_question_summary") or "").strip()
    web_query = str(focus.get("last_web_query") or "").strip()
    consensus = str(focus.get("last_web_consensus") or "").strip()
    people = tuple(focus.get("referenced_people") or ())
    if not last and not topic:
        return None
    # Skip non-factual prior answers (ACK / meta).
    low = last.lower().rstrip(".!")
    if low in ("okej", "ok", "jasne", "super", "dobrze", "dobra"):
        return None
    return ClaimReferent(
        claim_text=last,
        topic=topic,
        web_query=web_query,
        web_consensus=consensus,
        people=people,
        has_web_evidence=bool(web_query or consensus or people),
    )


def verification_reply_from_referent(
    referent: ClaimReferent,
    *,
    lang: str = "pl",
) -> str:
    """Confirm previous factual claim without a new search when evidence was present."""
    claim = referent.claim_text or referent.topic
    claim = str(claim or "").strip()
    if not claim:
        if (lang or "pl").lower() == "en":
            return "I don't have a prior claim to verify."
        return "Nie mam wcześniejszego twierdzenia do sprawdzenia."
    # Avoid nesting "Tak" if claim already starts with it.
    body = claim
    if (lang or "pl").lower() == "en":
        if referent.has_web_evidence:
            return f"Yes — according to the sources I checked: {body}"
        return f"Yes — based on what I said: {body}"
    if referent.has_web_evidence:
        return f"Tak — według aktualnych źródeł: {body}"
    return f"Tak — chodziło o to: {body}"


def verification_search_query(referent: ClaimReferent) -> str:
    if referent.web_query:
        return referent.web_query
    topic = referent.topic or referent.claim_text
    return f"verify {topic}".strip()
