# ==========================
# ELIAN — Future Intent Engine
# ==========================
"""Conditional future intents — AT_TIME / BEFORE_EVENT / IF_NOT_COMPLETED / …

No fake sensors: missing location/presence → UNAVAILABLE.
Cancel rules persist across restart („już nie musisz”).
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.persistence import safe_read_json

_LOCK = threading.RLock()
_ENGINE: Optional["FutureIntentEngine"] = None
_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "Data" / "future_intents.json"

_CANCEL_RE = re.compile(
    r"(?:"
    r"ju[zż]\s+nie\s+musisz|"
    r"nie\s+musisz\s+(?:ju[zż]\s+)?(?:tego\s+)?(?:robi[cć]|przypomina[cć])|"
    r"anuluj\s+(?:to\s+)?(?:przypomnienie|zamiar|intencj\w*)|"
    r"odwo[lł]aj\s+(?:to\s+)?(?:przypomnienie|zamiar)"
    r")",
    re.I,
)


class IntentCondition(str, Enum):
    AT_TIME = "AT_TIME"
    BEFORE_EVENT = "BEFORE_EVENT"
    AFTER_EVENT = "AFTER_EVENT"
    ON_STATE_CHANGE = "ON_STATE_CHANGE"
    ON_ARRIVAL = "ON_ARRIVAL"
    ON_DEPARTURE = "ON_DEPARTURE"
    WHEN_IDLE = "WHEN_IDLE"
    DEADLINE_APPROACHING = "DEADLINE_APPROACHING"
    IF_NOT_COMPLETED = "IF_NOT_COMPLETED"


class IntentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    TRIGGERED = "TRIGGERED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    UNAVAILABLE = "UNAVAILABLE"
    EXPIRED = "EXPIRED"


@dataclass
class FutureIntent:
    id: str = field(default_factory=lambda: f"fi_{uuid.uuid4().hex[:10]}")
    condition: str = IntentCondition.AT_TIME.value
    description: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    # Timing
    at_time: Optional[float] = None  # epoch
    before_event_id: str = ""
    before_minutes: float = 15.0
    after_event_id: str = ""
    state_key: str = ""
    state_equals: Any = None
    deadline: Optional[float] = None
    completed_key: str = ""
    # Lifecycle
    status: str = IntentStatus.ACTIVE.value
    created_at: float = 0.0
    updated_at: float = 0.0
    expires_at: Optional[float] = None
    unavailable_reason: str = ""
    message: str = ""
    suggested_action: str = ""
    require_confirmation: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "FutureIntent":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        data = {k: v for k, v in dict(raw or {}).items() if k in known}
        return cls(**data)


def looks_like_cancel_future_intent(text: str) -> bool:
    return bool(_CANCEL_RE.search(str(text or "").strip()))


def get_future_intent_engine(
    *, path: Optional[Path] = None
) -> "FutureIntentEngine":
    global _ENGINE
    with _LOCK:
        if _ENGINE is None:
            _ENGINE = FutureIntentEngine(path=path or _DEFAULT_PATH)
        return _ENGINE


def reset_future_intent_engine_for_tests(
    *, path: Optional[Path] = None
) -> "FutureIntentEngine":
    global _ENGINE
    with _LOCK:
        _ENGINE = FutureIntentEngine(path=path or _DEFAULT_PATH)
        _ENGINE.reset(clear_file=True)
        return _ENGINE


class FutureIntentEngine:
    """Store / evaluate / cancel conditional intents."""

    def __init__(self, *, path: Optional[Path] = None, clock: Optional[Callable[[], float]] = None):
        self.path = Path(path or _DEFAULT_PATH)
        self._clock = clock or time.time
        self._intents: Dict[str, FutureIntent] = {}
        self._load()

    def reset(self, *, clear_file: bool = False) -> None:
        self._intents.clear()
        if clear_file:
            try:
                if self.path.exists():
                    self.path.unlink()
            except OSError:
                pass

    def set_clock(self, clock_fn: Callable[[], float]) -> None:
        self._clock = clock_fn

    def list_active(self) -> List[FutureIntent]:
        return [
            i
            for i in self._intents.values()
            if i.status == IntentStatus.ACTIVE.value
        ]

    def add(self, intent: FutureIntent) -> FutureIntent:
        now = float(self._clock())
        if not intent.created_at:
            intent.created_at = now
        intent.updated_at = now
        if intent.status != IntentStatus.CANCELLED.value:
            intent.status = IntentStatus.ACTIVE.value
        self._intents[intent.id] = intent
        self._save()
        return intent

    def cancel(
        self,
        intent_id: str = "",
        *,
        description_substr: str = "",
        all_active: bool = False,
    ) -> List[str]:
        """Cancel matching intents; persists across restart."""
        cancelled: List[str] = []
        now = float(self._clock())
        needle = str(description_substr or "").strip().lower()
        for intent in list(self._intents.values()):
            if intent.status != IntentStatus.ACTIVE.value:
                continue
            match = False
            if all_active:
                match = True
            elif intent_id and intent.id == intent_id:
                match = True
            elif needle and needle in str(intent.description or "").lower():
                match = True
            elif not intent_id and not needle and not all_active:
                # bare cancel → most recent active
                continue
            if match:
                intent.status = IntentStatus.CANCELLED.value
                intent.updated_at = now
                cancelled.append(intent.id)
        if not cancelled and not intent_id and not needle:
            # Cancel most recently updated active
            active = sorted(
                self.list_active(),
                key=lambda i: float(i.updated_at or i.created_at or 0.0),
                reverse=True,
            )
            if active:
                active[0].status = IntentStatus.CANCELLED.value
                active[0].updated_at = now
                cancelled.append(active[0].id)
        if cancelled:
            self._save()
        return cancelled

    def mark_completed(self, completed_key: str) -> None:
        key = str(completed_key or "").strip()
        if not key:
            return
        now = float(self._clock())
        changed = False
        for intent in self._intents.values():
            if intent.status != IntentStatus.ACTIVE.value:
                continue
            if intent.condition == IntentCondition.IF_NOT_COMPLETED.value:
                if str(intent.completed_key or "") == key:
                    intent.status = IntentStatus.COMPLETED.value
                    intent.updated_at = now
                    changed = True
        if changed:
            self._save()

    def evaluate(
        self,
        *,
        world: Optional[Dict[str, Any]] = None,
        events: Optional[List[Dict[str, Any]]] = None,
        idle_seconds: float = 0.0,
    ) -> List[FutureIntent]:
        """Return newly triggered intents (status flipped to TRIGGERED)."""
        world = world or {}
        events = list(events or [])
        now = float(self._clock())
        triggered: List[FutureIntent] = []
        for intent in list(self._intents.values()):
            if intent.status != IntentStatus.ACTIVE.value:
                continue
            if intent.expires_at is not None and now >= float(intent.expires_at):
                intent.status = IntentStatus.EXPIRED.value
                intent.updated_at = now
                continue
            result = self._check(intent, now=now, world=world, events=events, idle_seconds=idle_seconds)
            if result == IntentStatus.UNAVAILABLE.value:
                intent.status = IntentStatus.UNAVAILABLE.value
                intent.updated_at = now
                continue
            if result == IntentStatus.TRIGGERED.value:
                intent.status = IntentStatus.TRIGGERED.value
                intent.updated_at = now
                triggered.append(intent)
        self._save()
        return triggered

    def _check(
        self,
        intent: FutureIntent,
        *,
        now: float,
        world: Dict[str, Any],
        events: List[Dict[str, Any]],
        idle_seconds: float,
    ) -> str:
        cond = str(intent.condition or "")
        if cond == IntentCondition.AT_TIME.value:
            if intent.at_time is None:
                return IntentStatus.ACTIVE.value
            if now >= float(intent.at_time):
                return IntentStatus.TRIGGERED.value
            return IntentStatus.ACTIVE.value

        if cond == IntentCondition.BEFORE_EVENT.value:
            ev = self._find_event(intent.before_event_id, world, events)
            if ev is None:
                # Missing calendar/event sensor → UNAVAILABLE only if explicitly required
                if intent.payload.get("require_event"):
                    intent.unavailable_reason = "event_unavailable"
                    return IntentStatus.UNAVAILABLE.value
                return IntentStatus.ACTIVE.value
            start = ev.get("start_ts")
            if start is None and ev.get("minutes") is not None:
                start = now + float(ev["minutes"]) * 60.0
            if start is None:
                intent.unavailable_reason = "event_time_unknown"
                return IntentStatus.UNAVAILABLE.value
            lead = float(intent.before_minutes or 15.0) * 60.0
            if float(start) - now <= lead and float(start) >= now:
                return IntentStatus.TRIGGERED.value
            return IntentStatus.ACTIVE.value

        if cond == IntentCondition.AFTER_EVENT.value:
            for row in events:
                if str(row.get("id") or row.get("type") or "") == intent.after_event_id:
                    return IntentStatus.TRIGGERED.value
                if str(row.get("type") or "") == intent.after_event_id:
                    return IntentStatus.TRIGGERED.value
            return IntentStatus.ACTIVE.value

        if cond == IntentCondition.ON_STATE_CHANGE.value:
            val = _dig(world, intent.state_key)
            if val is None and intent.payload.get("required"):
                intent.unavailable_reason = f"state_unavailable:{intent.state_key}"
                return IntentStatus.UNAVAILABLE.value
            if intent.state_equals is not None and val == intent.state_equals:
                return IntentStatus.TRIGGERED.value
            return IntentStatus.ACTIVE.value

        if cond == IntentCondition.ON_ARRIVAL.value:
            presence = _presence(world)
            if presence == "UNAVAILABLE":
                intent.unavailable_reason = "presence_unavailable"
                return IntentStatus.UNAVAILABLE.value
            if presence in ("arriving", "home", "arrived"):
                return IntentStatus.TRIGGERED.value
            return IntentStatus.ACTIVE.value

        if cond == IntentCondition.ON_DEPARTURE.value:
            presence = _presence(world)
            if presence == "UNAVAILABLE":
                intent.unavailable_reason = "presence_unavailable"
                return IntentStatus.UNAVAILABLE.value
            if presence in ("leaving", "away", "departed"):
                return IntentStatus.TRIGGERED.value
            return IntentStatus.ACTIVE.value

        if cond == IntentCondition.WHEN_IDLE.value:
            need = float(intent.payload.get("idle_seconds") or 300)
            if idle_seconds >= need:
                return IntentStatus.TRIGGERED.value
            return IntentStatus.ACTIVE.value

        if cond == IntentCondition.DEADLINE_APPROACHING.value:
            if intent.deadline is None:
                return IntentStatus.ACTIVE.value
            lead = float(intent.payload.get("lead_seconds") or 3600)
            if 0 <= float(intent.deadline) - now <= lead:
                return IntentStatus.TRIGGERED.value
            return IntentStatus.ACTIVE.value

        if cond == IntentCondition.IF_NOT_COMPLETED.value:
            if intent.deadline is not None and now >= float(intent.deadline):
                # Still active means not completed
                return IntentStatus.TRIGGERED.value
            # Also trigger when lead window hits
            if intent.deadline is not None:
                lead = float(intent.payload.get("lead_seconds") or 1800)
                if 0 <= float(intent.deadline) - now <= lead:
                    return IntentStatus.TRIGGERED.value
            return IntentStatus.ACTIVE.value

        return IntentStatus.ACTIVE.value

    def _find_event(
        self,
        event_id: str,
        world: Dict[str, Any],
        events: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        needle = str(event_id or "").strip().lower()
        for row in events:
            if not isinstance(row, dict):
                continue
            if needle and needle in (
                str(row.get("id") or "").lower(),
                str(row.get("title") or "").lower(),
            ):
                return row
            if not needle:
                return row
        cal = world.get("calendar") or {}
        if isinstance(cal, dict):
            title = str(cal.get("next") or cal.get("title") or "").strip()
            if title and (not needle or needle in title.lower()):
                return {
                    "title": title,
                    "minutes": cal.get("minutes"),
                    "start_ts": cal.get("start_ts"),
                }
            if cal.get("status") == "UNAVAILABLE" or world.get("_calendar_status") == "UNAVAILABLE":
                return None
        return None

    def _load(self) -> None:
        data, _corrupt = safe_read_json(self.path, default={"intents": []})
        rows = []
        if isinstance(data, dict):
            rows = list(data.get("intents") or [])
        elif isinstance(data, list):
            rows = data
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                intent = FutureIntent.from_dict(row)
                self._intents[intent.id] = intent
            except Exception:
                continue

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "intents": [i.to_dict() for i in self._intents.values()],
                "updated_at": float(self._clock()),
            }
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass


def _dig(world: Dict[str, Any], key: str) -> Any:
    if not key:
        return None
    cur: Any = world
    for part in str(key).split("."):
        if not isinstance(cur, dict):
            return None
        if part not in cur:
            return None
        cur = cur[part]
    return cur


def _presence(world: Dict[str, Any]) -> str:
    conv = world.get("conversation") or {}
    session = world.get("session") or {}
    for blob in (conv, session, world):
        if not isinstance(blob, dict):
            continue
        if blob.get("presence_unavailable") or blob.get("presence_status") == "UNAVAILABLE":
            return "UNAVAILABLE"
        raw = str(blob.get("presence") or "").strip().lower()
        if raw:
            return raw
        if blob.get("leaving"):
            return "leaving"
        if blob.get("home_arrival") or blob.get("arriving"):
            return "arriving"
    return ""


def spoken_for_intent(intent: FutureIntent) -> str:
    if intent.message:
        return str(intent.message).strip()
    desc = str(intent.description or "").strip()
    if desc:
        return desc if desc.endswith((".", "?", "!")) else desc + "."
    return "Przypominam o zaplanowanej rzeczy."
