# ==========================
# ELIAN — user-facing speech guard (C.5.2)
# ==========================
"""Block internal event/debug identifiers from TTS. Fallback safety net only."""

from __future__ import annotations

import re
from typing import Iterable, Set

from core.events import (
    ACTIVE_WINDOW_CHANGED,
    BATTERY_CHARGING,
    BATTERY_CRITICAL,
    BATTERY_LOW,
    CALENDAR_UPCOMING,
    CPU_HIGH_SUSTAINED,
    CPU_SAMPLE,
    DISK_LOW,
    HA_RESTORED,
    HA_UNAVAILABLE,
    HEARTBEAT,
    NETWORK_LOST,
    NETWORK_RESTORED,
    RAM_HIGH_SUSTAINED,
    REMINDER_DUE,
    SCREEN_FRAME_UPDATED,
    SCREEN_MAJOR_CHANGE,
    VISION_COMPLETED,
    VISION_FAILED,
    VISION_STARTED,
    VISUAL_REFERENT_UPDATED,
    WORLD_STATE_REFRESH,
)

SILENT_STATE_EVENTS = frozenset(
    {
        ACTIVE_WINDOW_CHANGED,
        SCREEN_FRAME_UPDATED,
        SCREEN_MAJOR_CHANGE,
        VISUAL_REFERENT_UPDATED,
        VISION_STARTED,
        VISION_COMPLETED,
        VISION_FAILED,
        HEARTBEAT,
        WORLD_STATE_REFRESH,
        CPU_SAMPLE,
    }
)

_EXTRA_DEBUG_TOKENS = frozenset(
    {
        "WAITING_CONFIRMATION",
        "UNKNOWN_SIDE_EFFECT",
        "LOCAL_COMPUTER",
        "LOCAL_HA",
        "STEP_SUCCEEDED",
        "STEP_FAILED",
        "STEP_SKIPPED",
        "PLAN_RUNNING",
        "PLAN_COMPLETED",
        "PLAN_PARTIAL",
        "PLAN_FAILED",
        "SIDE_EFFECT",
        "TOOLSTATUS",
        "TOOL_STATUS",
        "TOOLRESULT",
        "UNKNOWN_SIDE_EFFECT",
        "ACTIVE_WINDOW_CHANGED",
    }
)

_SCREAMING_SNAKE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
_SNAKE_EVENT = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")


def known_event_type_names() -> Set[str]:
    names = set(SILENT_STATE_EVENTS)
    names.update(
        {
            BATTERY_LOW,
            BATTERY_CRITICAL,
            BATTERY_CHARGING,
            CPU_HIGH_SUSTAINED,
            RAM_HIGH_SUSTAINED,
            DISK_LOW,
            NETWORK_LOST,
            NETWORK_RESTORED,
            HA_UNAVAILABLE,
            HA_RESTORED,
            CALENDAR_UPCOMING,
            REMINDER_DUE,
        }
    )
    names.update(_EXTRA_DEBUG_TOKENS)
    return names


def looks_like_internal_identifier(text: str) -> bool:
    """True when the whole utterance is an internal enum/event type, not speech."""
    raw = str(text or "").strip().strip("\"'`").rstrip(".!?,;:")
    if not raw:
        return False
    tokens = known_event_type_names()
    if raw in tokens or raw.upper() in {item.upper() for item in tokens}:
        return True
    if _SCREAMING_SNAKE.match(raw):
        return True
    if raw in SILENT_STATE_EVENTS:
        return True
    # snake_case event type leaked as the entire utterance
    if _SNAKE_EVENT.match(raw) and raw in tokens:
        return True
    return False


def guard_user_facing_speech(text: str) -> str:
    """Return speakable text, or empty if the payload is an internal identifier."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if looks_like_internal_identifier(raw):
        return ""
    return raw


def any_internal_identifier(parts: Iterable[str]) -> bool:
    return any(looks_like_internal_identifier(item) for item in parts)
