# ==========================
# ELIAN — Situation / Attention vocabulary (Phase proactive-reasoning)
# ==========================
"""Shared types for SituationEngine + AttentionManager + Future Intent.

SituationEngine correlates facts; AttentionManager decides timing;
ProactivePolicy remains the final speak/suppress vocabulary.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ----- policy vocabulary (extends ProactivePolicy) -----

IGNORE = "IGNORE"
STORE = "STORE"
DEFER = "DEFER"
DIGEST = "DIGEST"
NOTIFY = "NOTIFY"
SUGGEST = "SUGGEST"
ASK = "ASK"

# ----- attention decisions -----


class AttentionDecision(str, Enum):
    INTERRUPT_NOW = "INTERRUPT_NOW"
    SPEAK_AFTER_TURN = "SPEAK_AFTER_TURN"
    DEFER = "DEFER"
    DIGEST = "DIGEST"
    SILENT = "SILENT"


# ----- situation kinds -----


class SituationType(str, Enum):
    DEVICE_MAY_NOT_LAST_FOR_UPCOMING_EVENT = "DEVICE_MAY_NOT_LAST_FOR_UPCOMING_EVENT"
    PHONE_CHARGE_BEFORE_DEPARTURE = "PHONE_CHARGE_BEFORE_DEPARTURE"
    BATTERY_LOW = "BATTERY_LOW"
    CALENDAR_SOON = "CALENDAR_SOON"
    EVENING_TOMORROW_EVENT = "EVENING_TOMORROW_EVENT"
    REMINDER_WHILE_BUSY = "REMINDER_WHILE_BUSY"
    LIGHTS_ON_AFTER_LEAVE = "LIGHTS_ON_AFTER_LEAVE"
    HA_DOWN_MID_PROCEDURE = "HA_DOWN_MID_PROCEDURE"
    DOWNLOAD_COMPLETE = "DOWNLOAD_COMPLETE"
    DISK_LOW = "DISK_LOW"
    CALENDAR_CONFLICT = "CALENDAR_CONFLICT"
    DEADLINE_APPROACHING = "DEADLINE_APPROACHING"
    INTERESTING_HIGH_VALUE = "INTERESTING_HIGH_VALUE"
    SUSTAINED_SYSTEM_PRESSURE = "SUSTAINED_SYSTEM_PRESSURE"
    DIGEST_BATCH = "DIGEST_BATCH"
    GENERIC = "GENERIC"


@dataclass
class Observation:
    """One factual signal feeding a situation (never spoken alone)."""

    kind: str
    value: Any = None
    source: str = ""
    timestamp: float = 0.0
    entity: str = ""
    status: str = "KNOWN"  # KNOWN | STALE | UNAVAILABLE | UNKNOWN
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Situation:
    """Correlated multi-signal situation — one user-facing story, not N alerts."""

    id: str = field(default_factory=lambda: f"sit_{uuid.uuid4().hex[:10]}")
    type: str = SituationType.GENERIC.value
    observations: List[Observation] = field(default_factory=list)
    entities: Dict[str, Any] = field(default_factory=dict)
    first_seen: float = 0.0
    last_updated: float = 0.0
    importance: float = 0.5
    confidence: float = 0.5
    urgency: float = 0.0
    relevance: float = 0.5
    expiry: Optional[float] = None
    related_goal: str = ""
    related_event: str = ""
    suggested_action: str = ""
    already_presented: bool = False
    message: str = ""
    offer_kind: str = ""
    offer_extra: Dict[str, Any] = field(default_factory=dict)
    policy_hint: str = NOTIFY
    deferrable: bool = True
    critical: bool = False
    mute_key: str = ""
    resolved: bool = False

    @property
    def observation_kinds(self) -> set[str]:
        return {str(o.kind) for o in self.observations}

    def is_expired(self, now: float) -> bool:
        if self.resolved:
            return True
        if self.expiry is None:
            return False
        return float(now) >= float(self.expiry)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "observations": [
                {
                    "kind": o.kind,
                    "value": o.value,
                    "source": o.source,
                    "timestamp": o.timestamp,
                    "entity": o.entity,
                    "status": o.status,
                }
                for o in self.observations
            ],
            "entities": dict(self.entities),
            "first_seen": self.first_seen,
            "last_updated": self.last_updated,
            "importance": self.importance,
            "confidence": self.confidence,
            "urgency": self.urgency,
            "relevance": self.relevance,
            "expiry": self.expiry,
            "related_goal": self.related_goal,
            "related_event": self.related_event,
            "suggested_action": self.suggested_action,
            "already_presented": self.already_presented,
            "message": self.message,
            "policy_hint": self.policy_hint,
            "deferrable": self.deferrable,
            "critical": self.critical,
            "resolved": self.resolved,
        }


@dataclass
class SituationConsiderResult:
    """Outcome of SituationEngine.consider for one inbound event."""

    route: str  # situation | absorb | legacy | silent
    situation: Optional[Situation] = None
    reason: str = ""


@dataclass
class AttentionContext:
    """Runtime attention snapshot — no fake sensors."""

    now: float = 0.0
    user_speaking: bool = False
    tts_active: bool = False
    conversation_active: bool = False
    important_tts: bool = False
    quiet_hours: bool = False
    last_user_activity: float = 0.0
    active_app: str = ""
    fullscreen: bool = False
    gaming: bool = False
    muted_types: Dict[str, float] = field(default_factory=dict)
    suppress_non_urgent: bool = False
    rate_limited: bool = False
    proactive_enabled: bool = True
