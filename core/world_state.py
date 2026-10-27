# ==========================
# ELIAN — WorldState (canonical live snapshot)
# ==========================
"""Session-live world snapshot. Not long-term memory.

Never dump the whole store into the planner prompt — Salience/ContextSelector
picks fragments; Planner gets compact_for_llm() only.
"""

from __future__ import annotations

import copy
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core.active_service import ActiveServiceContext

CURRENT = "CURRENT"
KNOWN = "KNOWN"
STALE = "STALE"
UNKNOWN = "UNKNOWN"
UNAVAILABLE = "UNAVAILABLE"

_STATUS_FROM_FRESHNESS = {
    CURRENT: KNOWN,
    KNOWN: KNOWN,
    STALE: STALE,
    UNKNOWN: UNKNOWN,
    UNAVAILABLE: UNAVAILABLE,
}


def _is_live_status(status: str | None) -> bool:
    """KNOWN/CURRENT only — STALE must not drive pronoun/device resolve."""
    return str(status or "") in (KNOWN, CURRENT)


class Freshness(str, Enum):
    CURRENT = CURRENT
    STALE = STALE
    UNKNOWN = UNKNOWN
    UNAVAILABLE = UNAVAILABLE


SOURCE_OF_TRUTH = (
    "LOCAL_RUNTIME",
    "LOCAL_COMPUTER_UIA",
    "CURRENT_SCREEN_PIXELS",
    "LOCAL_VISION",
    "LOCAL_HOME_ASSISTANT",
    "LOCAL_FILE",
    "BROWSER_DOM",
    "WEB",
    "MEMORY",
    "MODEL_KNOWLEDGE",
)

_PIXEL_KEYS = ("pixels", "image", "frame", "png", "jpeg", "bitmap", "video")
_SECRET_KEYS = ("password", "passwd", "token", "secret", "api_key", "cookie")

_TTL = {
    "screen": 2.0,
    "computer": 2.0,
    "smart_home": 15.0,
    "services": 30.0,
    "conversation": 300.0,
    "active_plan": 600.0,
    "media": 15.0,
    "network": 30.0,
    "session": 600.0,
    "active_referent": 300.0,
    "mentioned": 180.0,
    "volume": 30.0,
    "active_app": 15.0,
    "pc": 30.0,
    "calendar": 120.0,
    "reminders": 120.0,
    "weather": 600.0,
    "integrations": 60.0,
    "stt": 8.0,
    "tts": 8.0,
    "last_actions": 600.0,
    "procedure": 600.0,
    "browser": 5.0,
}

_STAGE_B_KEYS = (
    "services",
    "conversation",
    "active_plan",
    "smart_home",
    "computer",
    "screen",
)

_EXTRA_KEYS = (
    "media",
    "network",
    "session",
    "active_referent",
    "mentioned",
    "volume",
    "active_app",
    "pc",
    "calendar",
    "reminders",
    "weather",
    "integrations",
    "stt",
    "tts",
    "last_actions",
    "procedure",
    "browser",
)

_COMPACT_ORDER = (
    "session",
    "active_referent",
    "mentioned",
    "smart_home",
    "media",
    "volume",
    "active_app",
    "computer",
    "pc",
    "calendar",
    "reminders",
    "weather",
    "integrations",
    "stt",
    "tts",
    "procedure",
    "last_actions",
    "active_plan",
)

_HA_TOOLS = frozenset(
    {
        "turn_on_device",
        "turn_off_device",
        "set_device_value",
        "adjust_brightness",
        "activate_scene",
        "get_device_state",
        "list_devices",
    }
)
_MEDIA_TOOLS = frozenset(
    {
        "play_media",
        "pause_media",
        "resume_media",
        "next_track",
        "previous_track",
        "get_now_playing",
    }
)
_VOLUME_TOOLS = frozenset({"volume_up", "volume_down"})
_APP_TOOLS = frozenset(
    {"open_app", "close_app", "focus_window", "focus_or_open", "get_active_window"}
)
_CALENDAR_TOOLS = frozenset(
    {
        "get_calendar_events",
        "get_today_schedule",
        "get_next_event",
        "create_calendar_event",
        "update_calendar_event",
        "delete_calendar_event",
    }
)
_REMINDER_TOOLS = frozenset(
    {"create_reminder", "list_reminders", "cancel_reminder", "update_reminder"}
)

_PRONOUN_RE = re.compile(
    r"\b(?:ją|ja|nią|nia|go|niego|je|to|ten|tę|te|tą|ta|tamto|tamtą|tamten|tamtę)\b",
    re.I,
)
_VOLUME_RE = re.compile(
    r"\b(?:ciszej|g[lł]o[sś]niej|g[lł]o[sś]no[sś][cć]|wycisz|daj\s+ciszej)\b",
    re.I,
)
_LIGHT_ACT_RE = re.compile(
    r"\b(?:zga[sś]|zapal|przyciemn|rozja[sś]n|w[lł][aą]cz|wy[lł][aą]cz)\b",
    re.I,
)
_CLOSE_RE = re.compile(r"\b(?:zamknij|wy[lł][aą]cz)\b", re.I)
_MEDIA_RE = re.compile(r"\b(?:muzyk|utwór|utwor|spotify|odtwarz)\b", re.I)

_MENTION_MAX = 8
_ACTION_MAX = 8
_JOURNAL_MAX = 48
_COMPACT_MAX_CHARS = 720

_LOCK = threading.RLock()
_CLOCK = time.time

_STATE: Dict[str, Any] = {}
_OBSERVATIONS: Dict[str, "Observation"] = {}
_ENTITY_META: Dict[str, Tuple[str, float]] = {}
_JOURNAL: deque = deque(maxlen=_JOURNAL_MAX)


def _now() -> float:
    return float(_CLOCK())


def set_world_clock(fn=None) -> None:
    """Tests inject a clock. Production leaves the default."""
    global _CLOCK
    _CLOCK = fn or time.time


def _empty_state() -> Dict[str, Any]:
    state: Dict[str, Any] = {key: {} for key in _STAGE_B_KEYS}
    for key in _EXTRA_KEYS:
        if key in ("mentioned", "last_actions"):
            state[key] = []
        else:
            state[key] = {}
    return state


def _init_state() -> None:
    global _STATE
    _STATE = _empty_state()


_init_state()


@dataclass
class Observation:
    slot: str
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = "LOCAL_RUNTIME"
    timestamp: float = 0.0
    confidence: float = 0.0
    ttl_s: float = 30.0
    available: bool = True

    def freshness(self, *, now: float | None = None) -> str:
        if not self.available:
            return UNAVAILABLE
        if not self.payload:
            return UNKNOWN
        age = float((now if now is not None else _now()) - float(self.timestamp or 0.0))
        if age > float(self.ttl_s or 0.0):
            return STALE
        return CURRENT

    def status(self, *, now: float | None = None) -> str:
        return _STATUS_FROM_FRESHNESS.get(self.freshness(now=now), UNKNOWN)

    def to_dict(self, *, now: float | None = None) -> Dict[str, Any]:
        fresh = self.freshness(now=now)
        return {
            "slot": self.slot,
            "source": self.source,
            "timestamp": self.timestamp,
            "confidence": round(float(self.confidence), 3),
            "freshness": fresh,
            "status": _STATUS_FROM_FRESHNESS.get(fresh, UNKNOWN),
            "ttl_s": self.ttl_s,
        }


@dataclass(frozen=True)
class Fact:
    slot: str
    value: Any
    timestamp: float
    ttl_s: float
    source: str
    status: str
    freshness: str
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot": self.slot,
            "value": copy.deepcopy(self.value),
            "timestamp": self.timestamp,
            "ttl_s": self.ttl_s,
            "source": self.source,
            "status": self.status,
            "freshness": self.freshness,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class WorldSnapshot:
    taken_at: float
    facts: Mapping[str, Fact]

    def fact(self, slot: str) -> Optional[Fact]:
        return self.facts.get(str(slot or ""))

    def compact_for_llm(self) -> str:
        return _compact_snapshot(self)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "taken_at": self.taken_at,
            "facts": {key: item.to_dict() for key, item in self.facts.items()},
        }


@dataclass(frozen=True)
class ReferentResolution:
    kind: str
    entity_id: str = ""
    name: str = ""
    domain: str = ""
    confidence: float = 0.0
    source: str = ""
    ambiguous: bool = False
    status: str = UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "entity_id": self.entity_id,
            "name": self.name,
            "domain": self.domain,
            "confidence": self.confidence,
            "source": self.source,
            "ambiguous": self.ambiguous,
            "status": self.status,
        }


def _scrub(payload: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = dict(payload)
    for key in _PIXEL_KEYS:
        cleaned.pop(key, None)
    for key in list(cleaned.keys()):
        low = str(key).lower()
        if any(part in low for part in _SECRET_KEYS):
            cleaned.pop(key, None)
    if isinstance(cleaned.get("elements"), list):
        cleaned.pop("elements", None)
    return cleaned


def _source_rank(source: str) -> int:
    name = str(source or "")
    try:
        return SOURCE_OF_TRUTH.index(name)
    except ValueError:
        return len(SOURCE_OF_TRUTH)


def _accept_write(
    slot: str,
    *,
    source: str,
    timestamp: float,
    available: bool,
) -> bool:
    prev = _OBSERVATIONS.get(slot)
    if prev is None:
        return True
    if not available:
        return True
    if not prev.available:
        return True
    incoming = _source_rank(source)
    held = _source_rank(prev.source)
    if incoming < held:
        return True
    if incoming > held and prev.payload and prev.freshness() in (CURRENT, STALE):
        return False
    return float(timestamp) >= float(prev.timestamp or 0.0)


def _journal(slot: str, *, source: str, status: str) -> None:
    _JOURNAL.append(
        {
            "ts": _now(),
            "slot": slot,
            "source": source,
            "status": status,
        }
    )


def _observe(
    slot: str,
    payload: Dict[str, Any],
    *,
    source: str = "LOCAL_RUNTIME",
    confidence: float = 0.9,
    ttl_s: float | None = None,
    available: bool = True,
    force: bool = False,
) -> Observation:
    ts = _now()
    if not force and not _accept_write(slot, source=source, timestamp=ts, available=available):
        existing = _OBSERVATIONS.get(slot)
        if existing is not None:
            return existing
    body: Any = payload
    if isinstance(payload, dict):
        body = dict(payload)
    elif isinstance(payload, list):
        body = list(payload)
    obs = Observation(
        slot=slot,
        payload=body if isinstance(body, dict) else {"items": body},
        source=source,
        timestamp=ts,
        confidence=float(confidence),
        ttl_s=float(ttl_s if ttl_s is not None else _TTL.get(slot, 30.0)),
        available=bool(available),
    )
    _OBSERVATIONS[slot] = obs
    _journal(slot, source=source, status=obs.status())
    return obs


def _commit(
    slot: str,
    value: Any,
    *,
    source: str = "LOCAL_RUNTIME",
    confidence: float = 0.9,
    ttl_s: float | None = None,
    available: bool = True,
    force: bool = False,
) -> Optional[Observation]:
    ts = _now()
    if not force and not _accept_write(slot, source=source, timestamp=ts, available=available):
        return None
    _STATE[slot] = value
    payload = value if isinstance(value, dict) else {"items": value}
    return _observe(
        slot,
        payload if isinstance(payload, dict) else {"items": payload},
        source=source,
        confidence=confidence,
        ttl_s=ttl_s,
        available=available,
        force=True,
    )


def reset_world_state() -> None:
    with _LOCK:
        _init_state()
        _OBSERVATIONS.clear()
        _ENTITY_META.clear()
        _JOURNAL.clear()
        set_world_clock(None)


def get_world_state() -> Dict[str, Any]:
    """Public Stage B fragments only — freshness lives in get_observation()."""
    with _LOCK:
        return {key: copy.deepcopy(_STATE.get(key) or {}) for key in _STAGE_B_KEYS}


def get_observation(slot: str) -> Optional[Observation]:
    with _LOCK:
        return _OBSERVATIONS.get(str(slot or ""))


def observation_freshness(slot: str, *, now: float | None = None) -> str:
    obs = get_observation(slot)
    if obs is None:
        return UNKNOWN
    return obs.freshness(now=now)


def observation_status(slot: str, *, now: float | None = None) -> str:
    obs = get_observation(slot)
    if obs is None:
        return UNKNOWN
    return obs.status(now=now)


def journal_entries() -> List[Dict[str, Any]]:
    with _LOCK:
        return [dict(item) for item in _JOURNAL]


def mark_unavailable(slot: str, *, source: str = "LOCAL_RUNTIME") -> None:
    with _LOCK:
        prev = _OBSERVATIONS.get(slot)
        payload = dict(prev.payload) if prev is not None else {}
        _observe(
            slot,
            payload,
            source=source,
            available=False,
            ttl_s=_TTL.get(slot, 30.0),
            force=True,
        )


def take_snapshot(*, now: float | None = None) -> WorldSnapshot:
    """Immutable copy for Planner / semantics. Does not call integrations."""
    stamp = float(now if now is not None else _now())
    facts: Dict[str, Fact] = {}
    with _LOCK:
        slots = set(_OBSERVATIONS.keys()) | set(_STATE.keys())
        for slot in slots:
            obs = _OBSERVATIONS.get(slot)
            raw = _STATE.get(slot)
            if obs is None and raw in ({}, [], None):
                continue
            if obs is None:
                value = copy.deepcopy(raw)
                facts[slot] = Fact(
                    slot=slot,
                    value=value,
                    timestamp=0.0,
                    ttl_s=float(_TTL.get(slot, 30.0)),
                    source="LOCAL_RUNTIME",
                    status=UNKNOWN,
                    freshness=UNKNOWN,
                )
                continue
            payload = obs.payload
            if slot in _STATE:
                stored = _STATE.get(slot)
                if stored not in ({}, [], None):
                    payload = stored
            facts[slot] = Fact(
                slot=slot,
                value=copy.deepcopy(payload),
                timestamp=obs.timestamp,
                ttl_s=obs.ttl_s,
                source=obs.source,
                status=obs.status(now=stamp),
                freshness=obs.freshness(now=stamp),
                confidence=obs.confidence,
            )
    return WorldSnapshot(taken_at=stamp, facts=MappingProxyType(facts))


def compact_world_for_llm(*, now: float | None = None) -> str:
    return take_snapshot(now=now).compact_for_llm()


def compact_planner_view(*, now: float | None = None) -> Dict[str, Any]:
    """Small flat dict for Planner / plan_cycle. Empty dict → context fallback."""
    snap = take_snapshot(now=now)
    view: Dict[str, Any] = {}
    statuses: Dict[str, str] = {}

    services = snap.fact("services")
    if services is not None and services.status not in (UNKNOWN, UNAVAILABLE):
        payload = services.value if isinstance(services.value, dict) else {}
        if payload:
            view["services"] = copy.deepcopy(payload)
            statuses["services"] = services.status

    ha = snap.fact("smart_home")
    if ha is not None and ha.status not in (UNKNOWN,):
        payload = ha.value if isinstance(ha.value, dict) else {}
        trimmed = {}
        for key, item in list(payload.items())[:6]:
            if isinstance(item, dict):
                trimmed[str(key)] = {
                    "entity_id": item.get("entity_id") or key,
                    "state": item.get("state"),
                    "domain": item.get("domain") or "",
                }
        if trimmed:
            view["smart_home"] = trimmed
            statuses["smart_home"] = ha.status

    for slot, key in (
        ("media", "media"),
        ("computer", "computer"),
        ("active_app", "computer"),
        ("active_referent", "active_referent"),
        ("volume", "volume"),
        ("session", "session"),
    ):
        fact = snap.fact(slot)
        if fact is None or fact.status == UNKNOWN:
            continue
        payload = copy.deepcopy(fact.value) if isinstance(fact.value, dict) else fact.value
        if not payload:
            continue
        if slot == "active_app" and "computer" not in view:
            view["computer"] = payload if isinstance(payload, dict) else {"active_app": payload}
        elif slot != "active_app":
            view[key] = payload
        statuses[slot] = fact.status

    if not view:
        return {}
    view["brief"] = snap.compact_for_llm()
    view["source"] = "world_state"
    view["taken_at"] = snap.taken_at
    if statuses:
        view["statuses"] = statuses
    return view


planner_snapshot = compact_planner_view
to_planner_view = compact_planner_view


def _fmt_value(slot: str, value: Any) -> str:
    if value in (None, {}, [], ""):
        return ""
    if slot == "smart_home" and isinstance(value, dict):
        parts = []
        items = list(value.items())
        preferred = [item for item in items if item[0] == "light.vizzini"]
        rest = [item for item in items if item[0] != "light.vizzini"]
        for entity_id, info in (preferred + rest)[:4]:
            if str(entity_id).startswith("_"):
                continue
            if isinstance(info, dict):
                state = info.get("state")
                label = entity_id
                if state:
                    parts.append(f"{label}={state}")
                else:
                    parts.append(str(label))
            else:
                parts.append(str(entity_id))
        return ", ".join(parts)
    if slot in ("media", "volume", "weather", "active_app", "pc", "session") and isinstance(
        value, dict
    ):
        bits = []
        for key in (
            "kind",
            "title",
            "query",
            "playing",
            "direction",
            "city",
            "summary",
            "name",
            "process",
            "active",
            "language",
            "online",
            "cpu_percent",
            "battery_percent",
        ):
            if value.get(key) not in (None, "", []):
                bits.append(f"{key}={value.get(key)}")
        return " ".join(bits[:5])
    if slot == "active_referent" and isinstance(value, dict):
        kind = value.get("kind") or ""
        name = value.get("name") or value.get("entity_id") or ""
        return f"{kind} {name}".strip()
    if slot == "mentioned" and isinstance(value, list):
        names = [
            str(item.get("name") or item.get("entity_id") or "")
            for item in value[-4:]
            if isinstance(item, dict)
        ]
        return ", ".join(part for part in names if part)
    if slot == "last_actions" and isinstance(value, list):
        parts = []
        for item in value[-3:]:
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool") or "")
            ok = "ok" if item.get("success") else "fail"
            parts.append(f"{tool}:{ok}")
        return " ".join(parts)
    if slot == "integrations" and isinstance(value, dict):
        return " ".join(f"{key}={val}" for key, val in list(value.items())[:6] if val)
    if slot in ("calendar", "reminders") and isinstance(value, dict):
        summary = value.get("summary") or value.get("next") or value.get("title")
        count = value.get("count")
        if summary:
            return str(summary)[:80]
        if count is not None:
            return f"count={count}"
    if slot == "procedure" and isinstance(value, dict):
        return str(value.get("id") or value.get("status") or "")
    if slot == "stt" or slot == "tts":
        if isinstance(value, dict):
            return str(value.get("status") or value.get("detail") or "known")
        return str(value)
    if isinstance(value, dict):
        return ",".join(str(k) for k in list(value.keys())[:4])
    return str(value)[:80]


def _compact_snapshot(snap: WorldSnapshot) -> str:
    lines: List[str] = []
    for slot in _COMPACT_ORDER:
        fact = snap.facts.get(slot)
        if fact is None:
            continue
        if fact.status == UNKNOWN:
            continue
        if fact.status == UNAVAILABLE:
            lines.append(f"{slot}: UNAVAILABLE")
            continue
        summary = _fmt_value(slot, fact.value)
        if not summary:
            continue
        tag = " STALE" if fact.status == STALE else ""
        lines.append(f"{slot}{tag}: {summary}"[:96])
        if len(lines) >= 16:
            break
    text = "\n".join(lines)
    if len(text) > _COMPACT_MAX_CHARS:
        text = text[:_COMPACT_MAX_CHARS].rstrip() + "…"
    return text


def _set_state(slot: str, value: Any) -> None:
    _STATE[slot] = value


def _remember_object(
    *,
    kind: str,
    name: str = "",
    entity_id: str = "",
    domain: str = "",
) -> None:
    label = str(name or entity_id or "").strip()
    if not label:
        return
    with _LOCK:
        rows = list(_STATE.get("mentioned") or [])
        item = {
            "kind": kind,
            "name": label,
            "entity_id": str(entity_id or ""),
            "domain": str(domain or ""),
            "ts": _now(),
        }
        rows = [
            row
            for row in rows
            if not (
                isinstance(row, dict)
                and str(row.get("entity_id") or row.get("name") or "").lower() == label.lower()
            )
        ]
        rows.append(item)
        _commit("mentioned", rows[-_MENTION_MAX:], source="LOCAL_RUNTIME", force=True)


def _set_referent(
    *,
    kind: str,
    name: str = "",
    entity_id: str = "",
    domain: str = "",
    source: str = "LOCAL_RUNTIME",
) -> None:
    payload = {
        "kind": kind,
        "name": str(name or entity_id or ""),
        "entity_id": str(entity_id or ""),
        "domain": str(domain or ""),
    }
    if not payload["name"] and not payload["entity_id"]:
        return
    _commit(
        "active_referent",
        payload,
        source=source,
        ttl_s=_TTL["active_referent"],
        force=True,
    )
    _remember_object(kind=kind, name=payload["name"], entity_id=entity_id, domain=domain)


def _append_action(tool: str, *, success: bool, target: str = "", status: str = "") -> None:
    rows = list(_STATE.get("last_actions") or [])
    rows.append(
        {
            "tool": str(tool or ""),
            "success": bool(success),
            "target": str(target or ""),
            "status": str(status or ""),
            "ts": _now(),
        }
    )
    _commit("last_actions", rows[-_ACTION_MAX:], source="LOCAL_RUNTIME", force=True)


def note_last_action_id(action_id: str) -> None:
    """Stamp ActionJournal id onto the newest last_actions row. Additive."""
    aid = str(action_id or "").strip()
    if not aid:
        return
    with _LOCK:
        rows = list(_STATE.get("last_actions") or [])
        if not rows or not isinstance(rows[-1], dict):
            return
        last = dict(rows[-1])
        last["action_id"] = aid
        rows[-1] = last
        _commit("last_actions", rows[-_ACTION_MAX:], source="LOCAL_RUNTIME", force=True)


def note_service(service: ActiveServiceContext) -> None:
    name = str(service.service_name or "").strip()
    if not name:
        return
    with _LOCK:
        services = dict(_STATE.get("services") or {})
        payload = service.to_dict()
        services[name.lower()] = payload
        _commit("services", services, source="LOCAL_RUNTIME", force=True)
        conversation = dict(_STATE.get("conversation") or {})
        conversation["active_service"] = payload
        _commit(
            "conversation",
            conversation,
            source="LOCAL_RUNTIME",
            ttl_s=_TTL["conversation"],
            force=True,
        )
        _set_referent(kind="app", name=name, source="LOCAL_RUNTIME")
        app = {"name": name, "kind": str(getattr(service, "resolved_kind", "") or "APP")}
        _STATE["active_app"] = app
        _observe("active_app", app, source="LOCAL_RUNTIME")


def note_active_plan(plan: Dict[str, Any] | None) -> None:
    with _LOCK:
        payload = dict(plan or {})
        _commit(
            "active_plan",
            payload,
            source="LOCAL_RUNTIME",
            ttl_s=_TTL["active_plan"],
            force=True,
        )


def note_smart_home(device: Dict[str, Any] | None, *, source: str = "LOCAL_HOME_ASSISTANT") -> None:
    if not device:
        return
    with _LOCK:
        _merge_smart_home(dict(device), source=source)


def _merge_smart_home(device: Dict[str, Any], *, source: str) -> None:
    entity_id = str(device.get("entity_id") or device.get("target") or "").strip()
    if not entity_id:
        return
    ts = _now()
    prev_src, prev_ts = _ENTITY_META.get(entity_id, ("", 0.0))
    if prev_src:
        incoming = _source_rank(source)
        held = _source_rank(prev_src)
        if incoming > held:
            return
        if incoming == held and ts < prev_ts:
            return
    refs = dict(_STATE.get("smart_home") or {})
    previous = dict(refs.get(entity_id) or {})
    merged = dict(previous)
    for key, value in device.items():
        if value is None or str(key).startswith("_"):
            continue
        merged[key] = value
    merged["entity_id"] = entity_id
    refs[entity_id] = merged
    _ENTITY_META[entity_id] = (source, ts)
    _commit("smart_home", refs, source=source, force=True)
    domain = str(merged.get("domain") or (entity_id.split(".", 1)[0] if "." in entity_id else ""))
    name = str(merged.get("name") or merged.get("friendly_name") or entity_id)
    _set_referent(
        kind="device",
        name=name,
        entity_id=entity_id,
        domain=domain,
        source=source,
    )


def note_conversation(key: str, value: Any) -> None:
    with _LOCK:
        conversation = dict(_STATE.get("conversation") or {})
        conversation[str(key)] = value
        _commit(
            "conversation",
            conversation,
            source="LOCAL_RUNTIME",
            ttl_s=_TTL["conversation"],
            force=True,
        )


def note_session(payload: Dict[str, Any] | None) -> None:
    if not payload:
        return
    with _LOCK:
        session = dict(_STATE.get("session") or {})
        session.update(dict(payload))
        _commit("session", session, source="LOCAL_RUNTIME", ttl_s=_TTL["session"], force=True)
        conversation = dict(_STATE.get("conversation") or {})
        conversation["session"] = dict(session)
        _commit(
            "conversation",
            conversation,
            source="LOCAL_RUNTIME",
            ttl_s=_TTL["conversation"],
            force=True,
        )


def note_computer(payload: Dict[str, Any] | None) -> None:
    if not payload:
        return
    with _LOCK:
        cleaned = _scrub(dict(payload))
        _commit("computer", cleaned, source="LOCAL_COMPUTER_UIA", force=True)
        window = cleaned.get("active_window") or cleaned.get("title") or cleaned.get("process")
        if window:
            app = {
                "name": str(
                    cleaned.get("process")
                    or (window.get("process") if isinstance(window, dict) else window)
                    or ""
                ),
                "title": str(
                    cleaned.get("title")
                    or (window.get("title") if isinstance(window, dict) else "")
                    or ""
                ),
            }
            _commit("active_app", app, source="LOCAL_COMPUTER_UIA", force=True)


def note_screen(payload: Dict[str, Any] | None) -> None:
    """Minimal world_state.screen. Never store raw pixels or password values."""
    if not payload:
        return
    with _LOCK:
        cleaned = _scrub(dict(payload))
        _commit("screen", cleaned, source="LOCAL_RUNTIME", force=True)


def note_media(payload: Dict[str, Any] | None, *, source: str = "LOCAL_RUNTIME") -> None:
    if not payload:
        return
    with _LOCK:
        cleaned = _scrub(dict(payload))
        if _commit("media", cleaned, source=source, ttl_s=_TTL["media"]) is None:
            return
        title = str(cleaned.get("title") or cleaned.get("query") or "")
        if title:
            _set_referent(kind="media", name=title, source=source)


def note_network(*, online: bool, extra: Dict[str, Any] | None = None) -> None:
    with _LOCK:
        payload = {"online": bool(online), **dict(extra or {})}
        _commit("network", payload, source="LOCAL_RUNTIME", ttl_s=_TTL["network"], force=True)


def note_volume(payload: Dict[str, Any] | None, *, source: str = "LOCAL_RUNTIME") -> None:
    if not payload:
        return
    with _LOCK:
        cleaned = dict(payload)
        _commit("volume", cleaned, source=source, ttl_s=_TTL["volume"], force=True)
        _set_referent(kind="volume", name="volume", source=source)


def note_weather(payload: Dict[str, Any] | None) -> None:
    if not payload:
        return
    with _LOCK:
        cleaned = dict(payload)
        _commit("weather", cleaned, source="WEB", ttl_s=_TTL["weather"], force=True)


def note_calendar(payload: Dict[str, Any] | None) -> None:
    if not payload:
        return
    with _LOCK:
        cleaned = dict(payload)
        _commit("calendar", cleaned, source="LOCAL_FILE", ttl_s=_TTL["calendar"], force=True)


def note_reminders(payload: Dict[str, Any] | None) -> None:
    if not payload:
        return
    with _LOCK:
        cleaned = dict(payload)
        _commit("reminders", cleaned, source="LOCAL_FILE", ttl_s=_TTL["reminders"], force=True)


def note_pc(payload: Dict[str, Any] | None) -> None:
    if not payload:
        return
    with _LOCK:
        cleaned = dict(payload)
        pc = dict(_STATE.get("pc") or {})
        pc.update(cleaned)
        _commit("pc", pc, source="LOCAL_RUNTIME", ttl_s=_TTL["pc"], force=True)


def note_procedure(payload: Dict[str, Any] | None) -> None:
    if not payload:
        return
    with _LOCK:
        cleaned = dict(payload)
        _commit("procedure", cleaned, source="LOCAL_RUNTIME", ttl_s=_TTL["procedure"], force=True)


def note_browser(payload: Dict[str, Any] | None) -> None:
    """url/title/tab only. No DOM dump, pixels, or secrets. Stage B public keys stay unchanged."""
    if not payload:
        return
    with _LOCK:
        cleaned = _scrub(dict(payload))
        _commit("browser", cleaned, source="BROWSER_DOM", ttl_s=_TTL["browser"], force=True)
        status = str(cleaned.get("status") or "KNOWN")
        rows = dict(_STATE.get("integrations") or {})
        rows["browser"] = status
        _commit("integrations", rows, source="BROWSER_DOM", force=True)


def note_integration(name: str, status: str, *, source: str = "LOCAL_RUNTIME") -> None:
    label = str(name or "").strip()
    if not label:
        return
    with _LOCK:
        rows = dict(_STATE.get("integrations") or {})
        rows[label] = str(status or UNKNOWN)
        _commit("integrations", rows, source=source, force=True)
        if label in ("home_assistant", "ha") and str(status) == UNAVAILABLE:
            mark_unavailable("smart_home", source=source)


def note_voice_status(*, stt: Dict[str, Any] | None = None, tts: Dict[str, Any] | None = None) -> None:
    """Read-only voice flags supplied by runtime/health. Does not start STT/TTS."""
    with _LOCK:
        if stt is not None:
            payload = dict(stt)
            available = str(payload.get("status") or "").upper() not in (
                UNAVAILABLE,
                "ERROR",
                "NOT CONFIGURED",
                "NOT_CONFIGURED",
            )
            _commit(
                "stt",
                payload,
                source="LOCAL_RUNTIME",
                available=available,
                ttl_s=_TTL["stt"],
                force=True,
            )
        if tts is not None:
            payload = dict(tts)
            available = str(payload.get("status") or "").upper() not in (
                UNAVAILABLE,
                "ERROR",
                "NOT CONFIGURED",
                "NOT_CONFIGURED",
            )
            _commit(
                "tts",
                payload,
                source="LOCAL_RUNTIME",
                available=available,
                ttl_s=_TTL["tts"],
                force=True,
            )


def refresh_voice_status_readonly() -> None:
    """Best-effort in-memory health read. Never blocks on Whisper/TTS."""
    try:
        from core.health import get_health_registry

        reg = get_health_registry()
        for name in ("stt", "tts"):
            item = reg.get(name) if hasattr(reg, "get") else None
            if item is None:
                continue
            raw = str(getattr(getattr(item, "status", None), "value", getattr(item, "status", "")) or "")
            detail = str(getattr(item, "detail", "") or "")
            note_voice_status(**{name: {"status": raw or UNKNOWN, "detail": detail}})
    except Exception:
        pass


def get_service(name: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return dict((_STATE.get("services") or {}).get(str(name or "").strip().lower()) or {}) or None


def active_device_from_world(*, now: float | None = None):
    """ActiveDevice from live WorldState, or None. Does not invent HA entities.

    STALE / UNKNOWN / UNAVAILABLE referents must not drive device resolve.
    """
    snap = take_snapshot(now=now)
    entity_id = ""
    domain = ""
    name = ""
    fact = snap.fact("active_referent")
    if fact is not None and _is_live_status(fact.status):
        value = fact.value if isinstance(fact.value, dict) else {}
        kind = str(value.get("kind") or "")
        entity_id = str(value.get("entity_id") or "")
        if kind in ("device", "") or entity_id.startswith(("light.", "switch.", "climate.")):
            domain = str(value.get("domain") or "")
            name = str(value.get("name") or "")
        else:
            entity_id = ""
    if not entity_id:
        ha = snap.fact("smart_home")
        if ha is None or not _is_live_status(ha.status):
            return None
        value = ha.value if isinstance(ha.value, dict) else {}
        payload = value.get("light.vizzini") if isinstance(value.get("light.vizzini"), dict) else None
        if not isinstance(payload, dict):
            for item in value.values():
                if isinstance(item, dict) and item.get("entity_id"):
                    payload = item
                    break
        if not isinstance(payload, dict):
            return None
        entity_id = str(payload.get("entity_id") or "")
        domain = str(payload.get("domain") or "")
        name = str(payload.get("name") or entity_id)
    if not entity_id:
        return None
    if not domain and "." in entity_id:
        domain = entity_id.split(".", 1)[0]
    if not domain:
        return None
    try:
        from core.device_context import ActiveDevice

        return ActiveDevice(entity_id=entity_id, domain=domain, name=name or entity_id)
    except Exception:
        return None


def resolve_pronoun_referent(text: str, *, now: float | None = None) -> Optional[ReferentResolution]:
    """Resolve ją/to/tamto and implicit volume against live WorldState.

    TTL-expired (STALE) slots must not drive pronoun/device resolve. Compact
    prompts may still show STALE tags elsewhere; resolvers require KNOWN.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    snap = take_snapshot(now=now)
    pronoun = bool(_PRONOUN_RE.search(raw))
    volume = bool(_VOLUME_RE.search(raw))
    if volume:
        vol = snap.fact("volume")
        media = snap.fact("media")
        source = "LOCAL_RUNTIME"
        confidence = 0.7
        # Volume speech-act resolves as kind=volume. Live slots raise confidence;
        # STALE volume/media must not be treated as a live driving context.
        if vol is not None and _is_live_status(vol.status):
            source = vol.source
            confidence = 0.86
        elif media is not None and _is_live_status(media.status):
            source = media.source
            confidence = 0.8
        elif (vol is not None and vol.status == STALE) or (
            media is not None and media.status == STALE
        ):
            # Explicit volume command still maps to volume, but without stale payload.
            confidence = 0.55
        return ReferentResolution(
            kind="volume",
            name="volume",
            confidence=confidence,
            source=source or "LOCAL_RUNTIME",
            status=KNOWN,
        )
    referent = snap.fact("active_referent")
    ha = snap.fact("smart_home")
    app = snap.fact("active_app")

    def _device_from_ha() -> Optional[ReferentResolution]:
        if ha is None or not _is_live_status(ha.status):
            return None
        value = ha.value if isinstance(ha.value, dict) else {}
        payload = value.get("light.vizzini") if isinstance(value.get("light.vizzini"), dict) else None
        if payload is None:
            for item in value.values():
                if isinstance(item, dict) and str(item.get("entity_id") or "").startswith("light."):
                    payload = item
                    break
        if not isinstance(payload, dict):
            return None
        entity_id = str(payload.get("entity_id") or "light.vizzini")
        domain = str(payload.get("domain") or "light")
        return ReferentResolution(
            kind="device",
            entity_id=entity_id,
            name=str(payload.get("name") or entity_id),
            domain=domain,
            confidence=0.9,
            source=ha.source,
            status=KNOWN,
        )

    if pronoun or _LIGHT_ACT_RE.search(raw):
        if referent is not None and _is_live_status(referent.status):
            value = referent.value if isinstance(referent.value, dict) else {}
            kind = str(value.get("kind") or "")
            if kind == "device" or str(value.get("entity_id") or "").startswith(("light.", "switch.")):
                return ReferentResolution(
                    kind="device",
                    entity_id=str(value.get("entity_id") or ""),
                    name=str(value.get("name") or ""),
                    domain=str(value.get("domain") or ""),
                    confidence=0.92,
                    source=referent.source,
                    status=KNOWN,
                )
            if kind == "app" and _CLOSE_RE.search(raw):
                return ReferentResolution(
                    kind="app",
                    name=str(value.get("name") or ""),
                    confidence=0.88,
                    source=referent.source,
                    status=KNOWN,
                )
            if kind == "media" and not _LIGHT_ACT_RE.search(raw) and (
                pronoun or _MEDIA_RE.search(raw)
            ):
                return ReferentResolution(
                    kind="media",
                    name=str(value.get("name") or ""),
                    confidence=0.84,
                    source=referent.source,
                    status=KNOWN,
                )
        device = _device_from_ha()
        if device is not None:
            return device
        # Mentioned-object fallback is inherently soft/stale — must not drive.
        if app is not None and _is_live_status(app.status) and _CLOSE_RE.search(raw):
            value = app.value if isinstance(app.value, dict) else {}
            return ReferentResolution(
                kind="app",
                name=str(value.get("name") or ""),
                confidence=0.8,
                source=app.source,
                status=KNOWN,
            )
    return None


def apply_tool_result(tool_name: str, arguments: Dict[str, Any] | None = None, result: Any = None) -> None:
    """Update live WorldState after ToolRegistry execution. Never calls integrations."""
    arguments = dict(arguments or {})
    observation: Dict[str, Any]
    if result is None:
        observation = {}
    elif hasattr(result, "to_observation"):
        try:
            observation = dict(result.to_observation() or {})
        except Exception:
            observation = {}
    elif isinstance(result, dict):
        observation = dict(result)
    else:
        observation = {}
    data = dict(observation.get("data") or {})
    success = bool(observation.get("success", getattr(result, "success", False)))
    status = str(observation.get("status") or getattr(getattr(result, "status", None), "value", "") or "")
    error = str(observation.get("error") or data.get("error_code") or "")
    tool = str(tool_name or observation.get("tool") or "")
    target = str(
        data.get("entity_id")
        or arguments.get("entity_id")
        or data.get("query")
        or arguments.get("query")
        or arguments.get("name")
        or arguments.get("target")
        or ""
    )
    with _LOCK:
        _append_action(tool, success=success, target=target, status=status)

    unavailable = status in ("unavailable", UNAVAILABLE.lower()) or error in (
        "UNAVAILABLE",
        "NOT_CONFIGURED",
        "DISABLED",
        "timeout",
    )
    if unavailable:
        if tool in _HA_TOOLS:
            note_integration("home_assistant", UNAVAILABLE, source="LOCAL_HOME_ASSISTANT")
        elif tool in _MEDIA_TOOLS:
            note_integration("media", UNAVAILABLE, source="LOCAL_RUNTIME")
            mark_unavailable("media", source="LOCAL_RUNTIME")
        elif tool in _CALENDAR_TOOLS:
            note_integration("calendar", UNAVAILABLE, source="LOCAL_FILE")
            mark_unavailable("calendar", source="LOCAL_FILE")
        elif tool in _REMINDER_TOOLS:
            note_integration("reminders", UNAVAILABLE, source="LOCAL_FILE")
            mark_unavailable("reminders", source="LOCAL_FILE")
        return

    if not success:
        return

    if tool in _HA_TOOLS:
        entity = {
            "entity_id": data.get("entity_id") or arguments.get("entity_id") or "",
            "name": data.get("name") or data.get("friendly_name") or "",
            "domain": data.get("domain") or "",
        }
        if data.get("state") is not None:
            entity["state"] = data.get("state")
        if tool == "list_devices" and isinstance(data.get("devices"), list):
            for item in data.get("devices") or []:
                if isinstance(item, dict):
                    note_smart_home(item, source="LOCAL_HOME_ASSISTANT")
            note_integration("home_assistant", KNOWN, source="LOCAL_HOME_ASSISTANT")
            return
        if entity.get("entity_id"):
            note_smart_home(entity, source="LOCAL_HOME_ASSISTANT")
            note_integration("home_assistant", KNOWN, source="LOCAL_HOME_ASSISTANT")
        return

    if tool in _MEDIA_TOOLS:
        payload = {
            "kind": "now_playing" if tool == "get_now_playing" else "requested",
            "query": data.get("query") or arguments.get("query") or arguments.get("title") or "",
            "title": data.get("title") or data.get("query") or arguments.get("query") or "",
            "artist": data.get("artist") or "",
            "app": data.get("app") or arguments.get("app") or "",
        }
        if tool == "pause_media":
            payload["playing"] = False
        elif tool in ("play_media", "resume_media", "get_now_playing"):
            payload["playing"] = True
        note_media(payload, source="LOCAL_RUNTIME")
        note_integration("media", KNOWN, source="LOCAL_RUNTIME")
        return

    if tool in _VOLUME_TOOLS:
        direction = data.get("direction") or ("increase" if tool == "volume_up" else "decrease")
        note_volume({"direction": direction, "tool": tool}, source="LOCAL_RUNTIME")
        return

    if tool in _APP_TOOLS:
        name = str(
            data.get("app")
            or data.get("name")
            or arguments.get("name")
            or arguments.get("app")
            or ""
        )
        title = str(data.get("title") or data.get("active_window") or "")
        if name or title:
            note_computer({"process": name, "title": title, "active_window": title or name})
        return

    if tool == "get_weather":
        note_weather(
            {
                "city": data.get("city") or arguments.get("city") or "",
                "summary": str(observation.get("message") or "")[:160],
            }
        )
        return

    if tool in _CALENDAR_TOOLS:
        note_calendar(
            {
                "summary": str(observation.get("message") or "")[:160],
                "next": data.get("next") or data.get("title") or "",
                "count": data.get("count"),
            }
        )
        note_integration("calendar", KNOWN, source="LOCAL_FILE")
        return

    if tool in _REMINDER_TOOLS:
        note_reminders(
            {
                "summary": str(observation.get("message") or "")[:160],
                "count": data.get("count"),
            }
        )
        note_integration("reminders", KNOWN, source="LOCAL_FILE")
        return

    if tool == "get_system_status":
        note_pc(dict(data) if data else {"summary": str(observation.get("message") or "")[:160]})
        return

    if tool in ("get_situation_briefing", "procedure_run"):
        proc_id = str(data.get("procedure_id") or arguments.get("procedure_id") or "")
        if proc_id:
            note_procedure({"id": proc_id, "status": "ran"})


def ingest_event(event: Any) -> None:
    """EventBus subscriber. Never blocks on HA/media I/O."""
    try:
        etype = str(getattr(event, "type", "") or "")
        data = dict(getattr(event, "data", None) or {})
        source = str(getattr(event, "source", "") or "LOCAL_RUNTIME")
        if etype in ("ha_unavailable", "HA_UNAVAILABLE"):
            note_integration("home_assistant", UNAVAILABLE, source="LOCAL_HOME_ASSISTANT")
            mark_unavailable("smart_home", source="LOCAL_HOME_ASSISTANT")
            return
        if etype in ("ha_restored", "HA_RESTORED"):
            note_integration("home_assistant", KNOWN, source="LOCAL_HOME_ASSISTANT")
            return
        if etype in ("network_lost", "NETWORK_LOST"):
            note_network(online=False)
            return
        if etype in ("network_restored", "NETWORK_RESTORED"):
            note_network(online=True)
            return
        if etype in ("calendar_upcoming", "CALENDAR_UPCOMING"):
            note_calendar({"next": data.get("title") or data.get("summary") or "", "summary": str(data)[:120]})
            return
        if etype in ("reminder_due", "REMINDER_DUE"):
            note_reminders({"next": data.get("title") or data.get("text") or "", "summary": str(data)[:120]})
            return
        if etype in ("ACTIVE_WINDOW_CHANGED", "active_window_changed"):
            note_computer(
                {
                    "title": data.get("title") or "",
                    "process": data.get("process") or data.get("app") or "",
                    "hwnd": data.get("hwnd"),
                    "active_window": data.get("title") or data.get("process") or "",
                }
            )
            return
        if etype in ("VISUAL_REFERENT_UPDATED", "visual_referent_updated"):
            label = str(data.get("label") or data.get("name") or "")
            if label:
                _remember_object(kind="visual", name=label)
            return
        if etype in ("battery_low", "battery_critical", "battery_charging", "BATTERY_LOW", "BATTERY_CRITICAL", "BATTERY_CHARGING"):
            note_pc(
                {
                    "battery_percent": data.get("percent") or data.get("battery_percent"),
                    "battery_event": etype,
                }
            )
            return
        if etype in ("cpu_high_sustained", "CPU_HIGH_SUSTAINED"):
            note_pc({"cpu_percent": data.get("percent") or data.get("cpu_percent")})
            return
        if source:
            return
    except Exception:
        return
