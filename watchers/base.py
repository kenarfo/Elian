# ==========================
# ELIAN — watcher base (Phase 6)
# ==========================

from __future__ import annotations

from typing import Optional

from core.clock import Clock, SystemClock
from core.event_bus import EventBus


class Watcher:
    name = "watcher"
    interval_seconds = 15.0

    def __init__(
        self,
        bus: EventBus,
        *,
        clock: Optional[Clock] = None,
    ):
        self.bus = bus
        self.clock = clock or SystemClock()
        self._last_poll = 0.0
        self._running = False
        self._last_error = ""

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def should_poll(self, now: Optional[float] = None) -> bool:
        now = self.clock.time() if now is None else now
        return (now - self._last_poll) >= float(self.interval_seconds)

    def poll_if_due(self) -> None:
        if not self._running:
            return
        now = self.clock.time()
        if not self.should_poll(now):
            return
        self._last_poll = now
        try:
            self.poll_once()
        except Exception as exc:
            self._last_error = str(exc)
            print(f"[EVENT] watcher {self.name} error: {exc}")
            try:
                from core.health import HealthStatus, get_health_registry

                get_health_registry().set(
                    f"watcher_{self.name}",
                    HealthStatus.ERROR,
                    error=str(exc),
                )
            except Exception:
                pass

    def poll_once(self) -> None:
        raise NotImplementedError
