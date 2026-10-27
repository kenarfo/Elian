# ==========================
# ELIAN — clarification / follow-up policy (Phase 10 RC)
# ==========================
"""Deterministic rules for pending clarification and bad Agent clarifications."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from core.behavior import max_clarification_rounds


OPEN_APP_CLARIFICATION = re.compile(
    r"czy\s+(?:chcesz|mam|mog[eę])\s+(?:otworzy[cć]|uruchomi[cć]|odpali[cć])\s+(?:aplikacj[eę]|program)?",
    re.I,
)
YOUTUBE_CLARIFICATION = re.compile(r"\byoutube\b", re.I)
TOOL_DUMP = re.compile(
    r"\b(?:tool_call|parameters|capabilities|ToolRegistry|open_app|volume_up|"
    r"describe_screen|create_reminder)\b",
    re.I,
)
INTERNAL_LIST = re.compile(
    r"\b(?:available tools|registered tools|function calling|json schema|"
    r"do you want to open|would you like to|which application)\b",
    re.I,
)
_ENGLISH_CLARIFY = re.compile(
    r"\b(?:do you want|would you like|which (?:app|application|tool)|"
    r"please (?:specify|confirm)|open an? (?:app|application))\b",
    re.I,
)

CANCEL_PHRASES = frozenset({
    "nieważne",
    "niewazne",
    "anuluj",
    "anuluj to",
    "jednak nie",
    "zostaw",
    "pomiń",
    "pomin",
    "never mind",
})

STOP_PHRASES = frozenset({
    "stop",
    "stop!",
    "cisza",
    "wystarczy",
    "stój",
    "stoj",
})

WAIT_PHRASES = frozenset({
    "czekaj",
    "moment",
    "chwila",
    "daj chwilę",
    "daj chwile",
    "zaraz",
})

NO_PHRASES = frozenset({"nie", "niee", "nope"})

NEW_INTENT_PATTERNS = (
    (re.compile(r"\b(?:co\s+mam\s+na\s+ekranie|ekran|notatnik|kliknij|vision|ui)\b", re.I), "vision"),
    (re.compile(r"\b(?:przypomnij|przypomnienie|za\s+(?:dwie|2|trzy|3)\s+minut)\b", re.I), "reminder"),
    (re.compile(r"\b(?:kalendarz|wydarzenie|spotkanie|schedule|calendar)\b", re.I), "calendar"),
    (re.compile(
        r"\b(?:puść|pusc|puścisz|puscisz|odtwórz|odtworz|play|pause|posłuchać|posluchac|"
        r"nast[eę]pn|poprzedni|next|previous)\b",
        re.I,
    ), "media"),
    (re.compile(
        r"\b(?:włącz|wlacz|wyłącz|wylacz|światło|swiatlo|lamp|klimatyzac|temperatur|"
        r"ciemn|jaśniej|jasniej|nic\s+nie\s+widz)\b",
        re.I,
    ), "ha"),
    (re.compile(r"\b(?:co\s+to\s+jest|czym\s+jest|wyjaśnij|wyjasnij|co\s+by\s+si[eę]\s+stało|gdybym)\b", re.I), "knowledge"),
    (re.compile(r"\b(?:głośniej|ciszej|zwiększ|zmniejsz\s+głośno)\b", re.I), "volume"),
    (re.compile(r"\b(?:otwórz|otworz|uruchom|odpal\w*)\s+\w+", re.I), "open_app"),
    (re.compile(r"\b(?:wyszukaj|poszukaj|znajdź|znajdz|google)\b", re.I), "search"),
    (re.compile(r"\b(?:nie\s*,\s*nie|nie\s+o\s+to|to\s+nie\s+to)\b", re.I), "correction"),
)

ACTION_VERBS = re.compile(
    r"\b(?:otwórz|otworz|włącz|wlacz|wyłącz|wylacz|przypomnij|utwórz|utworz|"
    r"puść|pusc|zamknij|ustaw|kliknij|wyszukaj|poszukaj|co\s+mam\s+na\s+ekranie)\b",
    re.I,
)

CLARIFICATION_TTL_SECONDS = 120


class FollowUpKind(str, Enum):
    CLARIFICATION_ANSWER = "clarification_answer"
    NEW_INTENT = "new_intent"
    CANCEL = "cancel"
    CORRECTION = "correction"
    STOP = "stop"
    WAIT = "wait"
    NO = "no"


@dataclass
class FollowUpClassification:
    kind: FollowUpKind
    intent_category: str = ""


def detect_intent_category(text: str) -> str:
    lowered = str(text or "").lower().strip()
    if not lowered:
        return "general"
    # Prefer binding intent_family over polite-phrase heuristics.
    try:
        from core.intent_family import IntentFamily, classify_intent_family

        classification = classify_intent_family(text)
        family = classification.family
        if family == IntentFamily.APP:
            return "open_app"
        if family == IntentFamily.MEDIA:
            return "media"
        if family == IntentFamily.SMART_HOME:
            return "ha"
        if family == IntentFamily.REMINDER:
            return "reminder"
        if family == IntentFamily.CALENDAR:
            return "calendar"
        if family == IntentFamily.VISION:
            return "vision"
        if family == IntentFamily.WEB:
            return "search"
        if family == IntentFamily.CORRECTION:
            return "correction"
        if family == IntentFamily.KNOWLEDGE:
            return "knowledge"
        if family == IntentFamily.SYSTEM and classification.slots.get("action") in (
            "volume_up",
            "volume_down",
        ):
            return "volume"
    except Exception:
        pass
    for pattern, category in NEW_INTENT_PATTERNS:
        if pattern.search(lowered):
            return category
    if re.search(r"\b(?:czy\s+m[oó]g|możesz\s+mi|dasz\s+rad[eę])\b", lowered):
        # Polite opener alone is not knowledge when an action verb/app is present.
        if re.search(
            r"\b(?:odpal\w*|otwórz|otworz|uruchom|włącz|wlacz|puść|pusc|puścisz)\b",
            lowered,
        ):
            return "open_app"
        return "knowledge"
    return "general"


def _goal_mentions_open_app(goal: str) -> bool:
    lowered = str(goal or "").lower()
    if re.search(
        r"\b(?:otwórz|otworz|uruchom|odpal\w*|włącz|wlacz)\s+"
        r"(?:aplikac|program|spotify|discord|chrome|youtube|\w+)",
        lowered,
    ):
        return True
    try:
        from core.intent_family import IntentFamily, classify_intent_family

        classification = classify_intent_family(goal)
        return classification.family == IntentFamily.APP and bool(
            (classification.slots or {}).get("app_name")
        )
    except Exception:
        return False


def is_inappropriate_open_app_clarification(goal: str, question: str) -> bool:
    """True when reply asks to open an app but the user did not ask to open one."""
    if not OPEN_APP_CLARIFICATION.search(question or ""):
        return False
    if _goal_mentions_open_app(goal):
        return False
    # Any non-open goal: open-app prompt is wrong (talk, vision, HA, knowledge, …).
    return True


def strip_open_app_upsell(text: str, *, goal: str = "") -> str:
    """Remove 'Czy chcesz otworzyć aplikację?' upsell from final answers."""
    raw = str(text or "").strip()
    if not raw or not is_inappropriate_open_app_clarification(goal, raw):
        return raw
    # Drop the open-app sentence(s); keep the rest if it still makes sense.
    parts = re.split(r"(?<=[.!?])\s+", raw)
    kept = [p for p in parts if p and not OPEN_APP_CLARIFICATION.search(p)]
    cleaned = " ".join(kept).strip()
    if cleaned and len(cleaned) >= 3:
        return cleaned
    return ""


def looks_like_tool_dump(text: str) -> bool:
    raw = str(text or "")
    if len(raw) > 180:
        return True
    if TOOL_DUMP.search(raw) or INTERNAL_LIST.search(raw):
        return True
    if raw.count(",") >= 4 and raw.count("tool") >= 2:
        return True
    return False


def sanitize_clarification_question(question: str, *, goal: str = "") -> str:
    text = str(question or "").strip()
    if not text:
        return "Możesz sprecyzować?"

    if _ENGLISH_CLARIFY.search(text) or looks_like_tool_dump(text):
        category = detect_intent_category(goal or text)
        defaults = {
            "reminder": "O czym i na kiedy mam przypomnieć?",
            "calendar": "Jaki tytuł i termin wydarzenia?",
            "vision": "Który element na ekranie?",
            "ha": "Które urządzenie masz na myśli?",
            "knowledge": "Chcesz krótkie wyjaśnienie?",
            "open_app": "Którą aplikację mam otworzyć?",
        }
        return defaults.get(category, "Możesz sprecyzować?")

    if is_inappropriate_open_app_clarification(goal, text):
        category = detect_intent_category(goal)
        if category == "reminder":
            return "O czym i na kiedy mam przypomnieć?"
        if category == "vision":
            return "Co dokładnie mam zrobić na ekranie?"
        if category == "knowledge":
            return "Chcesz krótkie wyjaśnienie?"
        return "Możesz sprecyzować?"

    # Polish-only short voice form — take first sentence.
    for sep in (". ", "! ", "? "):
        if sep in text:
            head = text.split(sep, 1)[0].strip()
            if head:
                text = head + sep.strip()
            break
    if len(text) > 120:
        text = text[:117].rstrip() + "…"
    return text


def classify_followup(
    text: str,
    pending: Optional[Dict[str, Any]],
    *,
    last_assistant: str = "",
) -> FollowUpClassification:
    normalized = str(text or "").lower().strip()
    if not normalized:
        return FollowUpClassification(FollowUpKind.CLARIFICATION_ANSWER)

    if normalized in CANCEL_PHRASES:
        return FollowUpClassification(FollowUpKind.CANCEL)
    if normalized in STOP_PHRASES:
        return FollowUpClassification(FollowUpKind.STOP)
    if normalized in WAIT_PHRASES:
        return FollowUpClassification(FollowUpKind.WAIT)
    if normalized in NO_PHRASES and pending:
        return FollowUpClassification(FollowUpKind.NO)

    new_category = detect_intent_category(normalized)
    if pending:
        pending_goal = str(pending.get("goal") or "").lower()
        pending_category = detect_intent_category(pending_goal)

        # Short media/vision/volume commands during unrelated clarification → new intent.
        if new_category in ("vision", "media", "volume", "reminder", "calendar", "search"):
            if pending_category not in (new_category, "open_app", "general"):
                return FollowUpClassification(FollowUpKind.NEW_INTENT, new_category)

        if ACTION_VERBS.search(normalized) and new_category != pending_category:
            if new_category != "general" and pending_category != new_category:
                return FollowUpClassification(FollowUpKind.NEW_INTENT, new_category)

        if len(normalized.split()) >= 4 and new_category == "knowledge" and pending_category != "knowledge":
            return FollowUpClassification(FollowUpKind.NEW_INTENT, new_category)

        if re.search(r"\b(?:nie\s+o\s+to|popraw|zamiast|nie\s*,\s*nie)\b", normalized):
            return FollowUpClassification(FollowUpKind.CORRECTION, new_category)

        if new_category == "correction":
            return FollowUpClassification(FollowUpKind.CORRECTION, new_category)

    return FollowUpClassification(FollowUpKind.CLARIFICATION_ANSWER, new_category)


def clarification_expired(pending: Optional[Dict[str, Any]]) -> bool:
    if not pending:
        return False
    started = float(pending.get("started_at") or 0)
    if started <= 0:
        return False
    return (time.monotonic() - started) >= CLARIFICATION_TTL_SECONDS


def clarification_limit_reached(rounds: int) -> bool:
    return int(rounds or 0) >= max_clarification_rounds()
