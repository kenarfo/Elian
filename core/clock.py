# ==========================
# ELIAN — injectable clock (Phase 6)
# ==========================

from __future__ import annotations

import time
from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def time(self) -> float: ...
    def now_local(self) -> datetime: ...


class SystemClock:
    def time(self) -> float:
        return time.time()

    def now_local(self) -> datetime:
        return datetime.now()


class FakeClock:
    """Deterministic clock for tests — no sleep required."""

    def __init__(self, start: float = 1_000_000.0):
        self._t = float(start)
        self._dt = datetime(2026, 6, 15, 12, 0, 0)

    def time(self) -> float:
        return self._t

    def now_local(self) -> datetime:
        return self._dt

    def advance(self, seconds: float) -> None:
        self._t += float(seconds)
        from datetime import timedelta

        self._dt = self._dt + timedelta(seconds=float(seconds))

    def set_local(self, dt: datetime) -> None:
        self._dt = dt
