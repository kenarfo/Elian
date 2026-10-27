# ==========================
# ELIAN — desktop HUD observation events (Phase 13 / GUI 2.0)
# ==========================
"""Qt-free HUD event interface.

Runtime and voice threads publish here. The GUI only observes.
This module must stay toolkit-free (no GUI imports).

Wiring hooks (Cognitive Core / SituationEngine / Future Intent — later):
  emit_goal_started / emit_plan_updated  ← Planner / Agent
  emit_world_state_updated               ← SituationEngine / WorldState
  emit_situation / emit_proactive        ← AttentionManager / proactive
  emit_memory_candidate / emit_memory_saved ← memory pipeline
Until those modules emit, smoke/tests may simulate the same event types.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple, Union
import threading
import time


class HudEventType(str, Enum):
    STATE_CHANGED = "state_changed"
    TRANSCRIPT_RECEIVED = "transcript_received"
    RESPONSE_CREATED = "response_created"
    GOAL_STARTED = "goal_started"
    PLAN_UPDATED = "plan_updated"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    WORLD_STATE_UPDATED = "world_state_updated"
    SITUATION_CREATED = "situation_created"
    PROACTIVE_MESSAGE = "proactive_message"
    MEMORY_CANDIDATE_CREATED = "memory_candidate_created"
    MEMORY_SAVED = "memory_saved"
    INTEGRATION_STATUS_CHANGED = "integration_status_changed"
    CONFIDENCE_UPDATED = "confidence_updated"
    ERROR_OCCURRED = "error_occurred"
    # Optional DEV VIEW fields travel inside state_changed / tool / world payloads.


class HudDisplayState(str, Enum):
    IDLE = "IDLE"
    AWARE = "AWARE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    ACTING = "ACTING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    MUTED = "MUTED"
    ERROR = "ERROR"
    OFFLINE = "OFFLINE"
    DEGRADED = "DEGRADED"

    # Backward-compatible alias used by older emitters / tests.
    @classmethod
    def _missing_(cls, value):  # type: ignore[override]
        if isinstance(value, str) and value.upper() == "PROCESSING":
            return cls.THINKING
        return None


# SpeechState names → HUD display. Overlay states (MUTED/ERROR/OFFLINE/DEGRADED)
# are projected in apply_event from flags, not decided here.
_SPEECH_TO_DISPLAY: Dict[str, HudDisplayState] = {
    "IDLE": HudDisplayState.IDLE,
    "WAKE_LISTENING": HudDisplayState.AWARE,
    "ACTIVE_LISTENING": HudDisplayState.LISTENING,
    "PROCESSING": HudDisplayState.THINKING,
    "THINKING": HudDisplayState.THINKING,
    "AWARE": HudDisplayState.AWARE,
    "ACTING": HudDisplayState.ACTING,
    "SPEAKING": HudDisplayState.SPEAKING,
    "INTERRUPTED": HudDisplayState.INTERRUPTED,
    "PAUSED": HudDisplayState.IDLE,
    "WAITING": HudDisplayState.LISTENING,
}

_LISTENING_SPEECH = frozenset(
    {"WAKE_LISTENING", "ACTIVE_LISTENING", "WAITING", "AWARE"}
)

_STATE_STATUS_PL: Dict[HudDisplayState, str] = {
    HudDisplayState.IDLE: "Czuwa",
    HudDisplayState.AWARE: "Czuję obecność",
    HudDisplayState.LISTENING: "Słucham",
    HudDisplayState.THINKING: "Myślę",
    HudDisplayState.ACTING: "Działam",
    HudDisplayState.SPEAKING: "Mówię",
    HudDisplayState.INTERRUPTED: "Przerwano",
    HudDisplayState.MUTED: "Wyciszony",
    HudDisplayState.ERROR: "Błąd",
    HudDisplayState.OFFLINE: "Offline",
    HudDisplayState.DEGRADED: "Osłabiony",
}

_TEXT_LIMIT = 140
_THOUGHT_LIMIT = 120
_MAX_PLAN_STEPS = 8
_MAX_SITUATIONS = 4
_MAX_NOTIFICATIONS = 12
_WORLD_KEYS = ("DOM", "PC", "MEDIA", "NEXT")

# Throttle for world_state_updated (seconds). Host may also throttle paint.
_DEFAULT_WORLD_THROTTLE_S = 0.5

HudSubscriber = Callable[["HudEvent"], None]


@dataclass(frozen=True)
class HudEvent:
    type: HudEventType
    data: Dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0
    source: str = "runtime"

    def __post_init__(self):
        if not self.ts:
            object.__setattr__(self, "ts", time.monotonic())
        if isinstance(self.type, str):
            object.__setattr__(self, "type", HudEventType(self.type))


@dataclass(frozen=True)
class HudPlanStep:
    id: str
    label: str
    done: bool = False


@dataclass(frozen=True)
class HudSituation:
    id: str
    title: str
    body: str = ""
    action_label: str = "Wykonaj"
    dismissible: bool = True


@dataclass(frozen=True)
class HudNotification:
    id: str
    title: str
    body: str = ""
    kind: str = "proactive"  # proactive | memory | error | info
    ts: float = 0.0


@dataclass
class HudSnapshot:
    state: HudDisplayState = HudDisplayState.IDLE
    speech_name: str = "IDLE"
    status_text: str = ""
    transcript: str = ""
    reply: str = ""
    thought: str = ""
    tool: str = ""
    confidence: Optional[float] = None
    mic_open: bool = False
    # Goal / plan
    goal_title: str = ""
    plan_steps: Tuple[HudPlanStep, ...] = ()
    # World essentials only
    world: Dict[str, str] = field(default_factory=dict)
    world_ts: float = 0.0
    # Proactive
    situations: Tuple[HudSituation, ...] = ()
    notifications: Tuple[HudNotification, ...] = ()
    proactive_enabled: bool = True
    # Memory toast
    memory_kind: str = ""  # session | candidate | persistent | ""
    memory_hint: str = ""
    memory_toast_ts: float = 0.0
    # Integrations
    ha: str = "unknown"
    ollama: str = "unknown"
    voice: str = "unknown"
    whisper: str = "unknown"
    piper: str = "unknown"
    browser: str = "unknown"
    phone: str = "unknown"
    muted: bool = False
    error: bool = False
    offline: bool = False
    degraded: bool = False
    error_text: str = ""
    # DEV VIEW (hidden unless hud.dev_view)
    intent_family: str = ""
    selected_tool: str = ""
    latency_ms: Optional[float] = None
    world_freshness_s: Optional[float] = None
    session_id: str = ""
    planner_phase: str = ""
    last_error_dev: str = ""


def map_speech_state(speech: Union[str, Any]) -> HudDisplayState:
    """Pure mapping from ambient SpeechState (or its name) to HUD display."""
    name = getattr(speech, "name", None) or str(speech or "IDLE")
    name = str(name).upper()
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    if name == "PROCESSING":
        return HudDisplayState.THINKING
    return _SPEECH_TO_DISPLAY.get(name, HudDisplayState.IDLE)


def status_label_pl(state: HudDisplayState, snap: Optional[HudSnapshot] = None) -> str:
    """Short Polish status for the CURRENT STATE surface."""
    if snap is not None and snap.status_text:
        return snap.status_text
    if snap is not None and snap.state is HudDisplayState.ACTING and snap.tool:
        return f"Sprawdzam: {snap.tool}"
    return _STATE_STATUS_PL.get(state, state.value)


def _speech_name(speech: Union[str, Any]) -> str:
    name = getattr(speech, "name", None) or str(speech or "IDLE")
    name = str(name).upper()
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    return name


def _clip(text: str, limit: int = _TEXT_LIMIT) -> str:
    raw = " ".join(str(text or "").split())
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)].rstrip() + "…"


def _project_state(snap: HudSnapshot) -> HudDisplayState:
    """Display overlay only — no routing / decision logic."""
    mapped = map_speech_state(snap.speech_name)
    if snap.error and mapped in (
        HudDisplayState.IDLE,
        HudDisplayState.LISTENING,
        HudDisplayState.AWARE,
    ):
        return HudDisplayState.ERROR
    if snap.muted and mapped is HudDisplayState.IDLE:
        return HudDisplayState.MUTED
    if mapped is HudDisplayState.IDLE:
        if snap.offline:
            return HudDisplayState.OFFLINE
        if snap.degraded:
            return HudDisplayState.DEGRADED
    return mapped


def _parse_plan_steps(raw: Any) -> Tuple[HudPlanStep, ...]:
    steps: List[HudPlanStep] = []
    if not isinstance(raw, (list, tuple)):
        return ()
    for i, item in enumerate(raw[:_MAX_PLAN_STEPS]):
        if isinstance(item, HudPlanStep):
            steps.append(item)
            continue
        if isinstance(item, dict):
            sid = str(item.get("id") or f"s{i}")
            label = _clip(str(item.get("label") or item.get("text") or sid), 80)
            done = bool(item.get("done") or item.get("completed"))
            steps.append(HudPlanStep(id=sid, label=label, done=done))
        else:
            steps.append(HudPlanStep(id=f"s{i}", label=_clip(str(item), 80), done=False))
    return tuple(steps)


def _parse_situation(data: Dict[str, Any]) -> Optional[HudSituation]:
    sid = str(data.get("id") or data.get("situation_id") or "").strip()
    title = _clip(str(data.get("title") or data.get("text") or ""), 80)
    if not sid:
        sid = f"sit-{int(time.monotonic() * 1000) % 1_000_000}"
    if not title:
        return None
    return HudSituation(
        id=sid,
        title=title,
        body=_clip(str(data.get("body") or data.get("detail") or ""), 120),
        action_label=_clip(str(data.get("action_label") or "Wykonaj"), 24) or "Wykonaj",
        dismissible=bool(data.get("dismissible", True)),
    )


def _push_notification(
    snap: HudSnapshot,
    *,
    title: str,
    body: str = "",
    kind: str = "proactive",
) -> HudSnapshot:
    nid = f"n-{int(time.monotonic() * 1000) % 1_000_000}"
    note = HudNotification(
        id=nid,
        title=_clip(title, 80),
        body=_clip(body, 120),
        kind=kind,
        ts=time.monotonic(),
    )
    notes = (note,) + tuple(snap.notifications)
    return replace(snap, notifications=notes[:_MAX_NOTIFICATIONS])


def _integration_offline(snap: HudSnapshot) -> bool:
    return any(
        getattr(snap, key) == "offline"
        for key in ("ha", "voice", "whisper", "piper")
    )


def _integration_degraded(snap: HudSnapshot) -> bool:
    return any(
        getattr(snap, key) == "degraded"
        for key in ("ha", "ollama", "voice", "whisper", "piper", "browser", "phone")
    )


def apply_event(
    snap: HudSnapshot,
    event: HudEvent,
    *,
    world_throttle_s: float = _DEFAULT_WORLD_THROTTLE_S,
    now: Optional[float] = None,
) -> HudSnapshot:
    """Reduce one observation into a HUD snapshot. Pure; no I/O."""
    data = dict(event.data or {})
    et = event.type
    clock = float(now if now is not None else time.monotonic())

    if et is HudEventType.STATE_CHANGED:
        speech = _speech_name(data.get("speech") or data.get("state") or "IDLE")
        status = _clip(str(data.get("status_text") or data.get("status") or ""), 80)
        snap = replace(
            snap,
            speech_name=speech,
            mic_open=speech in _LISTENING_SPEECH or speech == "ACTIVE_LISTENING",
            status_text=status or snap.status_text,
        )
        if speech not in ("IDLE", "PAUSED"):
            snap = replace(snap, error=False)
        # Optional DEV fields
        if "intent_family" in data:
            snap = replace(snap, intent_family=str(data.get("intent_family") or ""))
        if "session_id" in data:
            snap = replace(snap, session_id=str(data.get("session_id") or ""))
        if "planner_phase" in data:
            snap = replace(snap, planner_phase=str(data.get("planner_phase") or ""))
        if "latency_ms" in data:
            try:
                snap = replace(snap, latency_ms=float(data["latency_ms"]))
            except (TypeError, ValueError):
                pass

    elif et is HudEventType.TRANSCRIPT_RECEIVED:
        snap = replace(snap, transcript=_clip(data.get("text") or ""))

    elif et is HudEventType.RESPONSE_CREATED:
        snap = replace(snap, reply=_clip(data.get("text") or ""))

    elif et is HudEventType.GOAL_STARTED:
        title = _clip(str(data.get("title") or data.get("goal") or ""), 80)
        steps = _parse_plan_steps(data.get("steps") or data.get("plan") or [])
        thought = _clip(str(data.get("thought") or ""), _THOUGHT_LIMIT)
        snap = replace(
            snap,
            goal_title=title,
            plan_steps=steps,
            thought=thought or (f"Planuję {len(steps)} kroków" if steps else snap.thought),
        )

    elif et is HudEventType.PLAN_UPDATED:
        title = data.get("title") or data.get("goal")
        steps = data.get("steps") or data.get("plan")
        thought = data.get("thought")
        updates: Dict[str, Any] = {}
        if title is not None:
            updates["goal_title"] = _clip(str(title), 80)
        if steps is not None:
            updates["plan_steps"] = _parse_plan_steps(steps)
        if thought is not None:
            updates["thought"] = _clip(str(thought), _THOUGHT_LIMIT)
        if updates:
            snap = replace(snap, **updates)

    elif et is HudEventType.TOOL_STARTED:
        name = str(data.get("name") or data.get("tool") or "")
        thought = _clip(str(data.get("thought") or ""), _THOUGHT_LIMIT)
        snap = replace(
            snap,
            tool=name,
            selected_tool=name,
            thought=thought or (f"Używam: {name}" if name else snap.thought),
            status_text=_clip(str(data.get("status_text") or ""), 80) or snap.status_text,
        )

    elif et is HudEventType.TOOL_FINISHED:
        name = str(data.get("name") or data.get("tool") or "")
        current = snap.tool
        snap = replace(snap, tool="" if (not name or name == current) else current)
        # Mark matching plan step done when id/name provided
        step_id = str(data.get("step_id") or "")
        if step_id and snap.plan_steps:
            new_steps = tuple(
                replace(s, done=True) if s.id == step_id or s.label == name else s
                for s in snap.plan_steps
            )
            snap = replace(snap, plan_steps=new_steps)

    elif et is HudEventType.WORLD_STATE_UPDATED:
        # Throttle rapid WorldState floods — keep last essentials only.
        if snap.world_ts and (clock - snap.world_ts) < max(0.0, float(world_throttle_s)):
            if not data.get("force"):
                # Still refresh freshness marker for DEV view, skip content replace.
                snap = replace(snap, world_freshness_s=0.0)
                muted = bool(data.get("muted")) if "muted" in data else snap.muted
                snap = replace(snap, muted=muted)
                return replace(snap, state=_project_state(snap))
        world = dict(snap.world)
        incoming = data.get("world") or data.get("snapshot") or data
        if isinstance(incoming, dict):
            for key in _WORLD_KEYS:
                if key in incoming:
                    world[key] = _clip(str(incoming[key]), 48)
                low = key.lower()
                if low in incoming:
                    world[key] = _clip(str(incoming[low]), 48)
            # Also accept nested essentials
            for key in _WORLD_KEYS:
                if key not in world and key.lower() in {k.lower() for k in incoming}:
                    pass
        snap = replace(
            snap,
            world={k: world[k] for k in _WORLD_KEYS if k in world},
            world_ts=clock,
            world_freshness_s=0.0,
        )

    elif et is HudEventType.SITUATION_CREATED:
        sit = _parse_situation(data)
        if sit is not None:
            existing = [s for s in snap.situations if s.id != sit.id]
            situations = (sit,) + tuple(existing)
            snap = replace(snap, situations=situations[:_MAX_SITUATIONS])
            snap = _push_notification(
                snap, title=sit.title, body=sit.body, kind="proactive"
            )

    elif et is HudEventType.PROACTIVE_MESSAGE:
        title = _clip(str(data.get("title") or data.get("text") or ""), 80)
        body = _clip(str(data.get("body") or ""), 120)
        if title:
            snap = _push_notification(snap, title=title, body=body, kind="proactive")
            # Optionally surface as situation card
            if data.get("as_card", True):
                sit = _parse_situation(
                    {
                        "id": data.get("id") or f"pro-{int(clock * 1000) % 1_000_000}",
                        "title": title,
                        "body": body,
                        "action_label": data.get("action_label") or "OK",
                    }
                )
                if sit is not None:
                    existing = [s for s in snap.situations if s.id != sit.id]
                    snap = replace(
                        snap, situations=((sit,) + tuple(existing))[:_MAX_SITUATIONS]
                    )

    elif et is HudEventType.MEMORY_CANDIDATE_CREATED:
        hint = _clip(str(data.get("hint") or data.get("text") or "kandydat pamięci"), 80)
        snap = replace(
            snap,
            memory_kind="candidate",
            memory_hint=hint,
            memory_toast_ts=clock,
        )
        snap = _push_notification(snap, title="Kandydat pamięci", body=hint, kind="memory")

    elif et is HudEventType.MEMORY_SAVED:
        kind = str(data.get("kind") or "persistent").lower()
        if kind not in ("session", "candidate", "persistent"):
            kind = "persistent"
        hint = _clip(str(data.get("hint") or data.get("text") or "zapisano"), 80)
        snap = replace(
            snap,
            memory_kind=kind,
            memory_hint=hint,
            memory_toast_ts=clock,
        )
        snap = _push_notification(
            snap,
            title="Pamięć zapisana" if kind == "persistent" else "Pamięć sesji",
            body=hint,
            kind="memory",
        )

    elif et is HudEventType.INTEGRATION_STATUS_CHANGED:
        name = str(data.get("name") or "").lower()
        status = str(data.get("status") or "unknown").lower()
        # Normalize common aliases → ONLINE/DEGRADED/OFFLINE/DISABLED vocabulary
        if status in ("ready", "ok", "online"):
            status = "online"
        elif status in ("unavailable", "error"):
            status = "offline"
        elif status in ("not_configured", "disabled", "off"):
            status = "disabled"
        mapping = {
            "ha": "ha",
            "home_assistant": "ha",
            "ollama": "ollama",
            "ai": "ollama",
            "voice": "voice",
            "runtime": "voice",
            "whisper": "whisper",
            "stt": "whisper",
            "piper": "piper",
            "tts": "piper",
            "browser": "browser",
            "phone": "phone",
            "companion": "phone",
        }
        field_name = mapping.get(name)
        if field_name:
            snap = replace(snap, **{field_name: status})
        offline = _integration_offline(snap)
        degraded = _integration_degraded(snap) and not offline
        snap = replace(snap, offline=offline, degraded=degraded)

    elif et is HudEventType.CONFIDENCE_UPDATED:
        try:
            value = float(data.get("value"))
        except (TypeError, ValueError):
            value = None
        snap = replace(snap, confidence=value)

    elif et is HudEventType.ERROR_OCCURRED:
        msg = _clip(data.get("message") or data.get("text") or "", 80)
        snap = replace(
            snap,
            error=True,
            error_text=msg,
            last_error_dev=msg,
        )
        snap = _push_notification(snap, title="Błąd", body=msg, kind="error")

    # Explicit dismiss / action acknowledgements from GUI (observer-side only)
    dismiss_id = data.get("dismiss_situation_id")
    if dismiss_id:
        snap = replace(
            snap,
            situations=tuple(s for s in snap.situations if s.id != str(dismiss_id)),
        )

    if "thought" in data and et not in (
        HudEventType.GOAL_STARTED,
        HudEventType.PLAN_UPDATED,
        HudEventType.TOOL_STARTED,
    ):
        thought = _clip(str(data.get("thought") or ""), _THOUGHT_LIMIT)
        if thought:
            snap = replace(snap, thought=thought)

    if "proactive_enabled" in data:
        snap = replace(snap, proactive_enabled=bool(data.get("proactive_enabled")))

    muted = bool(data.get("muted")) if "muted" in data else snap.muted
    snap = replace(snap, muted=muted)
    return replace(snap, state=_project_state(snap))


class HudEventQueue:
    """Bounded, non-blocking queue. Publishers must never wait on the GUI."""

    def __init__(self, maxlen: int = 64):
        self._lock = threading.Lock()
        self._queue: Deque[HudEvent] = deque()
        self._maxlen = max(8, int(maxlen))
        self._subs: List[HudSubscriber] = []
        self.dropped = 0
        self.subscriber_errors = 0

    def subscribe(self, handler: HudSubscriber) -> None:
        with self._lock:
            if handler not in self._subs:
                self._subs.append(handler)

    def unsubscribe(self, handler: HudSubscriber) -> None:
        with self._lock:
            if handler in self._subs:
                self._subs.remove(handler)

    def publish(self, event: HudEvent) -> None:
        with self._lock:
            subs = list(self._subs)
            if len(self._queue) >= self._maxlen:
                self._queue.popleft()
                self.dropped += 1
            self._queue.append(event)
        for handler in subs:
            try:
                handler(event)
            except Exception:
                self.subscriber_errors += 1
                # Never re-raise — GUI/subscriber faults must not kill publishers.

    def drain(self) -> List[HudEvent]:
        with self._lock:
            items = list(self._queue)
            self._queue.clear()
            return items

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()
            self._subs.clear()
            self.dropped = 0
            self.subscriber_errors = 0


_queue = HudEventQueue()
_queue_lock = threading.Lock()


def get_hud_queue() -> HudEventQueue:
    return _queue


def reset_hud_events_for_tests(queue: Optional[HudEventQueue] = None) -> HudEventQueue:
    global _queue
    with _queue_lock:
        _queue = queue or HudEventQueue()
        return _queue


def emit(event: HudEvent) -> None:
    try:
        get_hud_queue().publish(event)
    except Exception:
        pass


def emit_state_changed(speech: Union[str, Any], *, reason: str = "", **extra: Any) -> None:
    payload: Dict[str, Any] = {
        "speech": _speech_name(speech),
        "reason": str(reason or ""),
    }
    payload.update({k: v for k, v in extra.items() if v is not None})
    emit(HudEvent(HudEventType.STATE_CHANGED, payload, source="speech"))


def emit_transcript(text: str) -> None:
    clipped = _clip(text)
    if not clipped:
        return
    emit(
        HudEvent(
            HudEventType.TRANSCRIPT_RECEIVED,
            {"text": clipped},
            source="conversation",
        )
    )


def emit_response(text: str) -> None:
    clipped = _clip(text)
    if not clipped:
        return
    emit(
        HudEvent(
            HudEventType.RESPONSE_CREATED,
            {"text": clipped},
            source="conversation",
        )
    )


def emit_goal_started(
    title: str,
    steps: Optional[List[Any]] = None,
    *,
    thought: str = "",
) -> None:
    emit(
        HudEvent(
            HudEventType.GOAL_STARTED,
            {
                "title": _clip(title, 80),
                "steps": steps or [],
                "thought": _clip(thought, _THOUGHT_LIMIT),
            },
            source="planner",
        )
    )


def emit_plan_updated(
    *,
    title: Optional[str] = None,
    steps: Optional[List[Any]] = None,
    thought: Optional[str] = None,
) -> None:
    data: Dict[str, Any] = {}
    if title is not None:
        data["title"] = _clip(title, 80)
    if steps is not None:
        data["steps"] = steps
    if thought is not None:
        data["thought"] = _clip(thought, _THOUGHT_LIMIT)
    if not data:
        return
    emit(HudEvent(HudEventType.PLAN_UPDATED, data, source="planner"))


def emit_tool_started(name: str, *, thought: str = "", status_text: str = "") -> None:
    tool = str(name or "").strip()
    if not tool:
        return
    emit(
        HudEvent(
            HudEventType.TOOL_STARTED,
            {
                "name": tool,
                "thought": _clip(thought, _THOUGHT_LIMIT),
                "status_text": _clip(status_text, 80),
            },
            source="tools",
        )
    )


def emit_tool_finished(
    name: str,
    *,
    success: bool = True,
    step_id: str = "",
) -> None:
    emit(
        HudEvent(
            HudEventType.TOOL_FINISHED,
            {
                "name": str(name or ""),
                "success": bool(success),
                "step_id": str(step_id or ""),
            },
            source="tools",
        )
    )


def emit_world_state_updated(
    essentials: Optional[Dict[str, str]] = None,
    *,
    force: bool = False,
    **kwargs: str,
) -> None:
    world = dict(essentials or {})
    world.update({k.upper(): v for k, v in kwargs.items()})
    emit(
        HudEvent(
            HudEventType.WORLD_STATE_UPDATED,
            {"world": world, "force": bool(force)},
            source="situation",
        )
    )


def emit_situation(
    title: str,
    *,
    body: str = "",
    situation_id: str = "",
    action_label: str = "Wykonaj",
) -> None:
    emit(
        HudEvent(
            HudEventType.SITUATION_CREATED,
            {
                "id": situation_id,
                "title": title,
                "body": body,
                "action_label": action_label,
            },
            source="proactive",
        )
    )


def emit_proactive(title: str, *, body: str = "", as_card: bool = True) -> None:
    emit(
        HudEvent(
            HudEventType.PROACTIVE_MESSAGE,
            {"title": title, "body": body, "as_card": as_card},
            source="proactive",
        )
    )


def emit_memory_candidate(hint: str = "") -> None:
    emit(
        HudEvent(
            HudEventType.MEMORY_CANDIDATE_CREATED,
            {"hint": hint or "nowy kandydat"},
            source="memory",
        )
    )


def emit_memory_saved(hint: str = "", *, kind: str = "persistent") -> None:
    emit(
        HudEvent(
            HudEventType.MEMORY_SAVED,
            {"hint": hint or "zapisano", "kind": kind},
            source="memory",
        )
    )


def emit_integration(name: str, status: str) -> None:
    emit(
        HudEvent(
            HudEventType.INTEGRATION_STATUS_CHANGED,
            {"name": str(name or ""), "status": str(status or "unknown")},
            source="runtime",
        )
    )


def emit_confidence(value: float) -> None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return
    emit(
        HudEvent(
            HudEventType.CONFIDENCE_UPDATED,
            {"value": number},
            source="voice",
        )
    )


def emit_error(message: str) -> None:
    emit(
        HudEvent(
            HudEventType.ERROR_OCCURRED,
            {"message": _clip(message, 80)},
            source="runtime",
        )
    )


def emit_dismiss_situation(situation_id: str) -> None:
    """GUI → snapshot only (does not call backend tools)."""
    emit(
        HudEvent(
            HudEventType.PLAN_UPDATED,
            {"dismiss_situation_id": str(situation_id)},
            source="hud",
        )
    )


def dispatch_to_view(view: Any, event: HudEvent) -> bool:
    """Apply one event to a view. Exceptions never propagate (GUI isolation)."""
    try:
        apply = getattr(view, "apply", None)
        if callable(apply):
            apply(event)
            return True
        render = getattr(view, "render", None)
        if callable(render):
            snap = getattr(view, "snapshot", HudSnapshot())
            snap = apply_event(snap, event)
            view.snapshot = snap
            render(snap)
            return True
        return False
    except Exception:
        return False
