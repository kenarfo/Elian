# ==========================
# ELIAN — speech state machine (Phase 4 / Ambient)
# ==========================

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
import threading
import time
from typing import Callable, List, Optional


class SpeechState(Enum):
    IDLE = auto()
    WAKE_LISTENING = auto()
    ACTIVE_LISTENING = auto()
    PROCESSING = auto()
    ACTING = auto()
    SPEAKING = auto()
    INTERRUPTED = auto()
    PAUSED = auto()
    WAITING = auto()  # RC ambient-session display (not "czekaj")


# Canonical ambient / barge-in graph. `set()` still allows any jump (RC/tests);
# `transition()` is the voice-loop path and records illegal hops in telemetry.
ALLOWED_TRANSITIONS: dict[SpeechState, frozenset[SpeechState]] = {
    SpeechState.IDLE: frozenset(
        {
            SpeechState.IDLE,
            SpeechState.WAKE_LISTENING,
            SpeechState.ACTIVE_LISTENING,
            SpeechState.SPEAKING,
        }
    ),
    SpeechState.WAKE_LISTENING: frozenset(
        {
            SpeechState.IDLE,
            SpeechState.WAKE_LISTENING,
            SpeechState.ACTIVE_LISTENING,
        }
    ),
    SpeechState.ACTIVE_LISTENING: frozenset(
        {
            SpeechState.PROCESSING,
            SpeechState.SPEAKING,
            SpeechState.PAUSED,
            SpeechState.IDLE,
            SpeechState.WAKE_LISTENING,
            SpeechState.INTERRUPTED,
            SpeechState.WAITING,
        }
    ),
    SpeechState.PROCESSING: frozenset(
        {
            SpeechState.ACTING,
            SpeechState.SPEAKING,
            SpeechState.ACTIVE_LISTENING,
            SpeechState.PAUSED,
            SpeechState.IDLE,
            SpeechState.INTERRUPTED,
            SpeechState.WAITING,
        }
    ),
    SpeechState.ACTING: frozenset(
        {
            SpeechState.SPEAKING,
            SpeechState.PROCESSING,
            SpeechState.ACTIVE_LISTENING,
            SpeechState.INTERRUPTED,
            SpeechState.IDLE,
        }
    ),
    SpeechState.SPEAKING: frozenset(
        {
            SpeechState.ACTIVE_LISTENING,
            SpeechState.INTERRUPTED,
            SpeechState.PAUSED,
            SpeechState.IDLE,
            SpeechState.PROCESSING,
            SpeechState.WAITING,
        }
    ),
    SpeechState.INTERRUPTED: frozenset(
        {
            SpeechState.ACTIVE_LISTENING,
            SpeechState.PROCESSING,
            SpeechState.IDLE,
        }
    ),
    SpeechState.PAUSED: frozenset(
        {
            SpeechState.ACTIVE_LISTENING,
            SpeechState.PROCESSING,
            SpeechState.IDLE,
            SpeechState.WAITING,
            SpeechState.SPEAKING,
        }
    ),
    SpeechState.WAITING: frozenset(
        {
            SpeechState.ACTIVE_LISTENING,
            SpeechState.PROCESSING,
            SpeechState.IDLE,
            SpeechState.PAUSED,
            SpeechState.SPEAKING,
            SpeechState.WAKE_LISTENING,
        }
    ),
}

_SESSION_STATES = frozenset(
    {
        SpeechState.ACTIVE_LISTENING,
        SpeechState.PROCESSING,
        SpeechState.ACTING,
        SpeechState.SPEAKING,
        SpeechState.INTERRUPTED,
        SpeechState.PAUSED,
        SpeechState.WAITING,
    }
)


@dataclass(frozen=True)
class StateTransition:
    old: SpeechState
    new: SpeechState
    reason: str
    ts: float
    legal: bool


class SpeechStateMachine:
    """Single source of truth for voice pipeline phase."""

    def __init__(self, initial: SpeechState = SpeechState.IDLE):
        self._lock = threading.RLock()
        self._state = initial
        self._listeners: List[Callable] = []
        self.history: List[StateTransition] = []
        self.illegal_count = 0

    def add_listener(self, callback) -> None:
        """Optional observer: callback(old_state, new_state). No audio side effects."""
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback) -> None:
        with self._lock:
            try:
                self._listeners.remove(callback)
            except ValueError:
                pass

    @property
    def state(self) -> SpeechState:
        with self._lock:
            return self._state

    def set(self, new_state: SpeechState, *, reason: str = "") -> SpeechState:
        return self._apply(new_state, reason=reason, enforce=False)

    def transition(self, new_state: SpeechState, *, reason: str = "") -> SpeechState:
        """Canonical voice-loop hop with telemetry (still applies the state)."""
        return self._apply(new_state, reason=reason, enforce=True)

    def _apply(
        self,
        new_state: SpeechState,
        *,
        reason: str,
        enforce: bool,
    ) -> SpeechState:
        with self._lock:
            old = self._state
            legal = new_state is old or new_state in ALLOWED_TRANSITIONS.get(
                old, frozenset()
            )
            self._state = new_state
            listeners = list(self._listeners)
        if old is not new_state:
            rec = StateTransition(
                old=old,
                new=new_state,
                reason=str(reason or ""),
                ts=time.monotonic(),
                legal=legal,
            )
            with self._lock:
                self.history.append(rec)
                if len(self.history) > 64:
                    self.history = self.history[-64:]
                if not legal:
                    self.illegal_count += 1
            self._record_telemetry(rec, enforce=enforce)
            for callback in listeners:
                try:
                    callback(old, new_state)
                except Exception:
                    pass
        return old

    def _record_telemetry(self, rec: StateTransition, *, enforce: bool) -> None:
        flag = "" if rec.legal else " ILLEGAL"
        why = f" ({rec.reason})" if rec.reason else ""
        try:
            print(f"[STATE] {rec.old.name} -> {rec.new.name}{why}{flag}")
        except Exception:
            pass
        try:
            from core.metrics import get_metrics

            metrics = get_metrics()
            metrics.speech_transitions = int(
                getattr(metrics, "speech_transitions", 0) or 0
            ) + 1
            if rec.new is SpeechState.INTERRUPTED:
                metrics.barge_in_count = int(
                    getattr(metrics, "barge_in_count", 0) or 0
                ) + 1
            if not rec.legal:
                metrics.illegal_state_hops = int(
                    getattr(metrics, "illegal_state_hops", 0) or 0
                ) + 1
        except Exception:
            pass
        try:
            from Diagnostics import log_turn

            log_turn(
                "speech_state",
                event="transition",
                old=rec.old.name,
                new=rec.new.name,
                reason=rec.reason,
                legal=rec.legal,
            )
        except Exception:
            pass

    def names(self) -> List[str]:
        with self._lock:
            return [item.new.name for item in self.history]

    def is_speaking(self) -> bool:
        return self.state is SpeechState.SPEAKING

    def is_idle(self) -> bool:
        return self.state is SpeechState.IDLE

    def is_processing(self) -> bool:
        return self.state is SpeechState.PROCESSING

    def is_paused(self) -> bool:
        return self.state in (SpeechState.PAUSED, SpeechState.WAITING)

    def is_active_session(self) -> bool:
        return self.state in _SESSION_STATES
