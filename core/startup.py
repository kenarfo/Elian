# ==========================
# ELIAN — startup timing (Phase 9)
# ==========================

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class StartupMetrics:
    marks: Dict[str, float] = field(default_factory=dict)
    durations_ms: Dict[str, float] = field(default_factory=dict)
    _t0: float = field(default_factory=time.perf_counter)

    def mark(self, name: str) -> None:
        self.marks[name] = time.perf_counter()

    def finish_phase(self, name: str, *, since: Optional[str] = None) -> None:
        end = time.perf_counter()
        start = self.marks.get(since or "start", self._t0)
        self.durations_ms[name] = round((end - start) * 1000, 2)

    def snapshot(self) -> Dict[str, float]:
        total = round((time.perf_counter() - self._t0) * 1000, 2)
        out = dict(self.durations_ms)
        out["startup_total_ms"] = total
        return out


_STARTUP: Optional[StartupMetrics] = None


def get_startup_metrics() -> StartupMetrics:
    global _STARTUP
    if _STARTUP is None:
        _STARTUP = StartupMetrics()
        _STARTUP.mark("start")
    return _STARTUP


def reset_startup_metrics() -> StartupMetrics:
    global _STARTUP
    _STARTUP = StartupMetrics()
    _STARTUP.mark("start")
    return _STARTUP
