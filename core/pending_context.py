# ==========================
# ELIAN — unified pending facade (Wave C)
# ==========================
"""One view over clarification / confirm / executable / wait-held goal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class PendingContext:
    clarification: Optional[Dict[str, Any]] = None
    tool: Optional[Dict[str, Any]] = None
    executable_goal: Optional[Dict[str, Any]] = None
    wait_goal: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (
            self.clarification or self.tool or self.executable_goal or self.wait_goal
        )

    def clear(self) -> None:
        self.clarification = None
        self.tool = None
        self.executable_goal = None
        self.wait_goal = ""
        self.extra.clear()


def pending_from_manager(manager: Any) -> PendingContext:
    return PendingContext(
        clarification=getattr(manager, "pending_clarification", None),
        tool=getattr(manager, "pending_tool", None),
        executable_goal=getattr(manager, "pending_executable_goal", None),
        wait_goal=str(getattr(manager, "pending_wait_goal", "") or ""),
    )
