# ==========================
# ELIAN — debug action trace ring buffer (Phase 9)
# ==========================

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List


@dataclass
class ActionTrace:
    max_items: int = 32
    events: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=32))

    def record(self, kind: str, **payload: Any) -> None:
        entry = {"kind": kind, **payload}
        if len(self.events) >= self.max_items:
            self.events.popleft()
        self.events.append(entry)

    def snapshot(self) -> List[Dict[str, Any]]:
        return list(self.events)


_TRACE = ActionTrace()


def get_action_trace() -> ActionTrace:
    return _TRACE


def reset_action_trace() -> ActionTrace:
    global _TRACE
    _TRACE = ActionTrace()
    return _TRACE
