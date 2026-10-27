# ==========================
# ELIAN — contextual follow-up offers (C.5.4)
# ==========================
"""Offer a next step only when it is useful. Never a call-center closer."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from core.semantic_executive import DecisionEnvelope


GENERIC_CLOSERS = (
    "czy mogę pomóc w czymś jeszcze",
    "czy mogę ci jeszcze pomóc",
    "w czym mogę pomóc",
    "jak mogę pomóc",
    "co dalej",
    "czy mogę jeszcze w czymś pomóc",
)

_GENERIC_RE = re.compile(
    r"(?:czy\s+mog[eę]\s+(?:ci\s+)?(?:jeszcze\s+)?pom[oó]c(?:\s+w\s+czym[sś]\s+jeszcze)?"
    r"|w\s+czym\s+mog[eę]\s+pom[oó]c"
    r"|jak\s+mog[eę]\s+pom[oó]c"
    r"|co\s+dalej)[?.!]?\s*$",
    re.I,
)

_TRIVIAL_INTENTS = {
    "system_volume",
    "social_smalltalk",
    "creative_humor",
    "wait",
    "cancel",
    "task_status",
    "defer_proactive",
    "no_task_status",
    "decision_followup",
    "say_text",
}


def has_generic_closer(text: str) -> bool:
    raw = str(text or "").strip().lower()
    if not raw:
        return False
    if _GENERIC_RE.search(raw):
        return True
    return any(raw.endswith(item) or raw.endswith(item + "?") for item in GENERIC_CLOSERS)


def strip_generic_closers(text: str) -> str:
    out = _GENERIC_RE.sub("", str(text or "")).strip()
    lowered = out.lower()
    for phrase in GENERIC_CLOSERS:
        if lowered.endswith(phrase) or lowered.endswith(phrase + "?"):
            out = out[: -len(phrase)].rstrip(" ?!.")
            lowered = out.lower()
    return out.strip()


def should_offer_followup(
    envelope: Optional[DecisionEnvelope] = None,
    *,
    success: bool = True,
    interrupted: bool = False,
    goal: str = "",
) -> bool:
    if not success or interrupted:
        return False
    env = envelope
    intent = str(getattr(env, "intent", "") or "")
    mode = str(getattr(env, "mode", "") or "")
    if intent in _TRIVIAL_INTENTS or mode in ("WAIT", "TALK"):
        return False
    text = str(goal or getattr(env, "user_goal", "") or "").lower()
    if "głośniej" in text or "ciszej" in text or "głośność" in text:
        return False
    if "żart" in text or "suchar" in text:
        return False
    # Useful next step: open a document / prepare a list.
    if re.search(r"\b(?:dokument|plan|list[eęa]|notatnik)\b", text) and re.search(
        r"\b(?:otw[oó]rz|przygotuj)\b", text
    ):
        if "list" in text:
            return True
        if "plan" in text or "dokument" in text:
            return True
    return False


def followup_offer(envelope: Optional[DecisionEnvelope] = None, *, goal: str = "") -> str:
    text = str(goal or getattr(envelope, "user_goal", "") or "").lower()
    if "list" in text:
        return "Chcesz, żebym od razu zaczął listę?"
    if "dokument" in text or "plan" in text:
        return "Chcesz, żebym go podsumował?"
    return ""


def apply_followup_policy(
    spoken: str,
    envelope: Optional[DecisionEnvelope] = None,
    *,
    success: bool = True,
    interrupted: bool = False,
    goal: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> tuple[str, bool]:
    del extra
    cleaned = strip_generic_closers(spoken)
    offered = False
    if should_offer_followup(envelope, success=success, interrupted=interrupted, goal=goal):
        offer = followup_offer(envelope, goal=goal)
        if offer and offer.lower() not in cleaned.lower():
            cleaned = f"{cleaned.rstrip('. ')}. {offer}".strip()
            offered = True
    return cleaned, offered
