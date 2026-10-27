# ==========================
# ELIAN — expected value of interruption
# ==========================
"""Gate noisy telemetry so Elian does not narrate every blip.

Safety alarms bypass this; routine window/CPU/song/HA refresh stay quiet.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.events import Event, Priority

# Events that almost never justify speaking on their own.
_NOISE_TYPES = frozenset(
    {
        "heartbeat",
        "world_state_refresh",
        "WORLD_STATE_REFRESH",
        "cpu_sample",
        "cpu_update",
        "cpu_percent",
        "cpu_tick",
        "ACTIVE_WINDOW_CHANGED",
        "active_window_changed",
        "SCREEN_FRAME_UPDATED",
        "SCREEN_MAJOR_CHANGE",
        "VISUAL_REFERENT_UPDATED",
        "media_track_changed",
        "song_changed",
        "ha_state_changed",
        "INTERNAL_EVENT",
        "PROCEDURE_COMPLETE",
    }
)

_HIGH_VALUE_HINTS = frozenset(
    {
        "download_complete",
        "task_completed",
        "critical_task_failure",
        "procedure_partial_fail",
        "battery_critical",
        "ha_unavailable",
        "network_lost",
        "calendar_conflict",
        "deadline_approaching",
    }
)


def expected_value_of_interruption(
    event: Event | None,
    *,
    world: Optional[Dict[str, Any]] = None,
    sustained_seconds: float = 0.0,
    user_relevance: bool = False,
    goal_blocked: bool = False,
    safety: bool = False,
) -> float:
    """Return 0..1 expected value of interrupting the user.

    Low values → SILENT / ignore. Does not invent sensors.
    """
    if event is None:
        return 0.0
    if safety or event.priority is Priority.CRITICAL:
        return 1.0
    etype = str(event.type or "")
    data = dict(event.data or {})
    if etype in _NOISE_TYPES and not (
        data.get("user_relevance") or data.get("goal_blocked") or user_relevance
    ):
        return 0.0
    if etype == "cpu_high_sustained" or etype.startswith("cpu"):
        # Blip: worthless. Sustained pressure: modest value.
        if sustained_seconds < 60:
            return 0.05
        if sustained_seconds < 600:
            return 0.35
        return 0.55
    score = 0.25
    if event.priority is Priority.HIGH:
        score = 0.65
    if event.priority is Priority.LOW:
        score = 0.15
    if etype in _HIGH_VALUE_HINTS or data.get("high_value"):
        score = max(score, 0.7)
    if user_relevance or data.get("user_relevance") or data.get("user_value"):
        score = max(score, 0.6)
    if goal_blocked or data.get("goal_blocked"):
        score = max(score, 0.75)
    if data.get("notify_requested") or data.get("user_asked_notify"):
        score = max(score, 0.8)
    world = world or {}
    # Routine HA entity churn without conversation topic → near zero.
    if etype in ("ha_state_changed", "smart_home_update") and not (
        (world.get("conversation") or {}).get("ha_topic")
    ):
        return min(score, 0.1)
    return max(0.0, min(1.0, float(score)))


def is_worth_speaking(
    event: Event | None,
    *,
    threshold: float = 0.4,
    **kwargs: Any,
) -> bool:
    return expected_value_of_interruption(event, **kwargs) >= float(threshold)
