# ==========================
# ELIAN — lightweight event bus (Phase 6)
# ==========================

from __future__ import annotations

import threading
from collections import deque
from typing import Callable, Deque, List, Optional

from core.clock import Clock, SystemClock
from core.events import Event


Subscriber = Callable[[Event], None]


class EventBus:
    """Simple publish/subscribe bus — not a heavy framework."""

    def __init__(self, *, clock: Optional[Clock] = None, history_size: int = 100):
        self.clock = clock or SystemClock()
        self._subs: List[Subscriber] = []
        self._lock = threading.RLock()
        self._history: Deque[Event] = deque(maxlen=max(10, int(history_size)))

    def subscribe(self, handler: Subscriber) -> None:
        with self._lock:
            if handler not in self._subs:
                self._subs.append(handler)

    def unsubscribe(self, handler: Subscriber) -> None:
        with self._lock:
            if handler in self._subs:
                self._subs.remove(handler)

    def publish(self, event: Event) -> Event:
        if not event.timestamp:
            event.timestamp = self.clock.time()
        print(
            f"[EVENT] type={event.type} priority={event.priority.value} "
            f"source={event.source}"
        )
        with self._lock:
            self._history.append(event)
            subscribers = list(self._subs)
        for handler in subscribers:
            try:
                handler(event)
            except Exception as exc:
                print(f"[EVENT] subscriber error: {exc}")
        return event

    def history(self) -> List[Event]:
        with self._lock:
            return list(self._history)

    def clear(self) -> None:
        with self._lock:
            self._history.clear()
            self._subs.clear()


_default_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    global _default_bus
    if _default_bus is None:
        _default_bus = EventBus()
    return _default_bus


def set_event_bus(bus: Optional[EventBus]) -> None:
    global _default_bus
    _default_bus = bus


def reset_event_bus_for_tests(bus: Optional[EventBus] = None) -> None:
    set_event_bus(bus)
