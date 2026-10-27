# ==========================
# ELIAN — event model (Phase 6)
# ==========================

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {
            Priority.LOW: 0,
            Priority.NORMAL: 1,
            Priority.HIGH: 2,
            Priority.CRITICAL: 3,
        }[self]


@dataclass
class Event:
    type: str
    source: str
    data: Dict[str, Any] = field(default_factory=dict)
    priority: Priority = Priority.NORMAL
    timestamp: float = 0.0
    dedup_key: str = ""
    ttl_seconds: Optional[float] = 300.0
    id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:10]}")
    invalidates: tuple = ()  # event types / dedup prefixes this cancels

    def __post_init__(self):
        if not self.dedup_key:
            self.dedup_key = f"{self.source}:{self.type}"
        if isinstance(self.priority, str):
            self.priority = Priority(self.priority)

    def is_expired(self, now: float) -> bool:
        if self.ttl_seconds is None:
            return False
        return (now - self.timestamp) > float(self.ttl_seconds)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "data": dict(self.data),
            "priority": self.priority.value,
            "timestamp": self.timestamp,
            "dedup_key": self.dedup_key,
            "ttl_seconds": self.ttl_seconds,
            "invalidates": list(self.invalidates),
        }


@dataclass
class Notification:
    message: str
    priority: Priority = Priority.NORMAL
    source: str = ""
    event_id: str = ""
    event_type: str = ""
    topic: str = ""  # short-lived context topic


# Common event type constants (future-ready)
BATTERY_LOW = "battery_low"
BATTERY_CRITICAL = "battery_critical"
BATTERY_CHARGING = "battery_charging"
CPU_HIGH_SUSTAINED = "cpu_high_sustained"
RAM_HIGH_SUSTAINED = "ram_high_sustained"
DISK_LOW = "disk_low"
NETWORK_LOST = "network_lost"
NETWORK_RESTORED = "network_restored"
HA_UNAVAILABLE = "ha_unavailable"
HA_RESTORED = "ha_restored"
CALENDAR_UPCOMING = "calendar_upcoming"
REMINDER_DUE = "reminder_due"
INCOMING_MESSAGE = "incoming_message"
OS_NOTIFICATION = "os_notification"
SCREEN_FRAME_UPDATED = "SCREEN_FRAME_UPDATED"
SCREEN_MAJOR_CHANGE = "SCREEN_MAJOR_CHANGE"
ACTIVE_WINDOW_CHANGED = "ACTIVE_WINDOW_CHANGED"
VISUAL_REFERENT_UPDATED = "VISUAL_REFERENT_UPDATED"
VISION_STARTED = "VISION_STARTED"
VISION_COMPLETED = "VISION_COMPLETED"
VISION_FAILED = "VISION_FAILED"
TASK_COMPLETED = "task_completed"
TASK_BLOCKED = "task_blocked"
ANALYSIS_COMPLETE = "analysis_complete"
PROCEDURE_PARTIAL_FAIL = "procedure_partial_fail"
CRITICAL_TASK_FAILURE = "critical_task_failure"
HOME_ARRIVAL = "home_arrival"
LEAVING_HOME = "leaving_home"
WINDOWS_UPDATE = "windows_update_starting"
HEARTBEAT = "heartbeat"
WORLD_STATE_REFRESH = "world_state_refresh"
CPU_SAMPLE = "cpu_sample"
