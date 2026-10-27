# ==========================
# ELIAN — Memory 2.0 types
# ==========================
"""Typed long-term knowledge. Not WorldState (live snapshot) and not ProcedureEngine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class MemoryType(str, Enum):
    FACT = "FACT"
    PREFERENCE = "PREFERENCE"
    RELATION = "RELATION"
    ROUTINE = "ROUTINE"
    TEMPORARY = "TEMPORARY"
    SESSION = "SESSION"
    VOCABULARY = "VOCABULARY"


class WriteStatus(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    MERGED = "merged"
    REJECTED_WEAK = "rejected_weak"
    REJECTED_SECRET = "rejected_secret"
    CONFLICT_ASK = "conflict_ask"


# Explicit user/agent prefs outrank a single weak observation.
CONFIDENCE_EXPLICIT = 1.0
CONFIDENCE_AGENT = 0.95
CONFIDENCE_OBSERVATION = 0.4
CONFIDENCE_WEAK = 0.3
STRONG_CONFIDENCE = 0.8
WEAK_MARGIN = 0.15

DEFAULT_IMPORTANCE = {
    MemoryType.PREFERENCE: 0.85,
    MemoryType.RELATION: 0.7,
    MemoryType.VOCABULARY: 0.75,
    MemoryType.ROUTINE: 0.75,
    MemoryType.FACT: 0.55,
    MemoryType.TEMPORARY: 0.4,
    MemoryType.SESSION: 0.25,
}

PERSISTENT_TYPES = frozenset(
    {
        MemoryType.FACT,
        MemoryType.PREFERENCE,
        MemoryType.RELATION,
        MemoryType.ROUTINE,
        MemoryType.TEMPORARY,
        MemoryType.VOCABULARY,
    }
)


def normalize_type(value) -> MemoryType:
    if isinstance(value, MemoryType):
        return value
    raw = str(getattr(value, "value", value) or MemoryType.FACT.value).strip().upper()
    if raw.startswith("MEMORYTYPE."):
        raw = raw.split(".", 1)[-1]
    try:
        return MemoryType(raw)
    except ValueError:
        return MemoryType.FACT


def default_importance(kind: MemoryType) -> float:
    return float(DEFAULT_IMPORTANCE.get(kind, 0.5))


def clamp01(value, default=0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(0.0, min(1.0, number))


@dataclass
class MemoryEntry:
    id: str
    type: str
    key: str
    value: str
    source: str
    confidence: float
    created_at: str
    updated_at: str
    last_used_at: str
    expires_at: Optional[str] = None
    importance: float = 0.5
    provenance: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    tags: list[str] = field(default_factory=list)
    explicit: bool = True
    confirmations: int = 1
    status: str = "active"
    persistent: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data.get("expires_at"):
            data["expires_at"] = None
        return data


@dataclass
class WriteResult:
    status: WriteStatus
    entry: Optional[dict[str, Any]] = None
    existing: Optional[dict[str, Any]] = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (
            WriteStatus.CREATED,
            WriteStatus.UPDATED,
            WriteStatus.MERGED,
        )
