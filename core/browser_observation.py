# ==========================
# ELIAN — BrowserAgent observation + action result (Phase 13 Stage D)
# ==========================
"""Session-only. Never dump full DOM, pixels, or password values into the LLM prompt."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
NEEDS_LOGIN = "NEEDS_LOGIN"
BLOCKED = "BLOCKED"
FAILED = "FAILED"
SUCCESS = "SUCCESS"


class BrowserStatus(str, Enum):
    AVAILABLE = AVAILABLE
    UNAVAILABLE = UNAVAILABLE
    NEEDS_LOGIN = NEEDS_LOGIN
    BLOCKED = BLOCKED
    FAILED = FAILED
    SUCCESS = SUCCESS


_SECRET_HINT = ("password", "passwd", "token", "secret", "api_key", "cookie")


def scrub_browser_payload(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    cleaned = dict(payload or {})
    for key in ("screenshot", "pixels", "png", "image", "frame"):
        cleaned.pop(key, None)
    for key in list(cleaned.keys()):
        low = str(key).lower()
        if any(part in low for part in _SECRET_HINT):
            cleaned.pop(key, None)
    return cleaned


@dataclass
class ActionableElement:
    element_id: str = ""
    role: str = ""
    name: str = ""
    text: str = ""
    placeholder: str = ""
    value: str = ""
    checked: Optional[bool] = None
    href: str = ""
    is_password: bool = False
    disabled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.is_password:
            data["value"] = ""
            data["text"] = ""
        return data


@dataclass
class BrowserObservation:
    url: str = ""
    title: str = ""
    visible_text: str = ""
    actionable: List[ActionableElement] = field(default_factory=list)
    active_tab: str = ""
    tabs: List[Dict[str, str]] = field(default_factory=list)
    loading: bool = False
    errors: List[str] = field(default_factory=list)
    status: str = AVAILABLE
    timestamp: float = field(default_factory=time.time)

    def to_dict(self, *, max_elements: int = 16) -> Dict[str, Any]:
        text = str(self.visible_text or "")
        if len(text) > 800:
            text = text[:797] + "..."
        return {
            "url": self.url,
            "title": self.title,
            "visible_text": text,
            "actionable": [el.to_dict() for el in self.actionable[: max(1, int(max_elements))]],
            "active_tab": self.active_tab,
            "tabs": list(self.tabs)[:12],
            "loading": bool(self.loading),
            "errors": list(self.errors)[:8],
            "status": self.status,
            "timestamp": self.timestamp,
        }

    def planner_fragment(self) -> Dict[str, Any]:
        """Small fragment for ContextSelector / WorldState — not a DOM dump."""
        return {
            "url": self.url,
            "title": self.title,
            "active_tab": self.active_tab,
            "loading": bool(self.loading),
            "tab_count": len(self.tabs),
            "status": self.status,
        }


@dataclass
class BrowserActionResult:
    success: bool
    action: str
    target: str = ""
    before: Optional[BrowserObservation] = None
    after: Optional[BrowserObservation] = None
    verification: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    status: str = SUCCESS
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": bool(self.success),
            "action": self.action,
            "target": self.target,
            "before": self.before.planner_fragment() if self.before else {},
            "after": self.after.planner_fragment() if self.after else {},
            "verification": dict(self.verification or {}),
            "error": self.error,
            "status": self.status,
            "message": self.message,
        }
