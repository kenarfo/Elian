# ==========================
# ELIAN — computer observation + short-lived UI context (Phase 13 Stage C)
# ==========================
"""Session-only. Never dump the full UI tree into the LLM prompt."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from core.computer_windows import MonitorInfo, WindowInfo

UI_CONTEXT_TTL_SEC = 30.0
CONFIDENCE_LOCK = 0.8


@dataclass
class ActiveWindowContext:
    window_id: str = ""
    hwnd: int = 0
    process_name: str = ""
    app_name: str = ""
    title: str = ""
    bounds: Dict[str, int] = field(default_factory=dict)
    focused: bool = False
    minimized: bool = False
    maximized: bool = False
    visible: bool = True
    opened_by_elian: bool = False
    last_action: str = ""
    updated_at: float = field(default_factory=time.time)
    window_token: str = ""
    exists: bool = True
    monitor_id: int = 0

    @classmethod
    def from_window(cls, info: WindowInfo | None) -> "ActiveWindowContext":
        if info is None:
            return cls(exists=False)
        return cls(
            window_id=info.window_id,
            hwnd=int(info.hwnd or 0),
            process_name=info.process_name,
            app_name=info.app_name,
            title=info.title,
            bounds=dict(info.bounds or {}),
            focused=bool(info.focused),
            minimized=bool(info.minimized),
            maximized=bool(info.maximized),
            visible=bool(info.visible),
            opened_by_elian=bool(info.opened_by_elian),
            last_action=info.last_action,
            updated_at=float(info.updated_at or time.time()),
            window_token=info.token(),
            exists=bool(info.exists),
            monitor_id=int(info.monitor_id or 0),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ComputerObservation:
    timestamp: float = field(default_factory=time.time)
    active_window: Optional[ActiveWindowContext] = None
    windows: List[Dict[str, Any]] = field(default_factory=list)
    focused_element: Dict[str, Any] = field(default_factory=dict)
    visible_elements: List[Dict[str, Any]] = field(default_factory=list)
    screen_size: Dict[str, int] = field(default_factory=dict)
    cursor_position: Dict[str, int] = field(default_factory=dict)
    app_process: str = ""
    app_name: str = ""
    window_title: str = ""
    ui_tree_available: bool = False
    screenshot_available: bool = False
    snapshot_id: str = ""
    monitors: List[Dict[str, Any]] = field(default_factory=list)
    modal: Dict[str, Any] = field(default_factory=dict)
    window_token: str = ""

    def to_dict(self, *, max_elements: int = 12) -> Dict[str, Any]:
        active = self.active_window.to_dict() if self.active_window else {}
        return {
            "timestamp": self.timestamp,
            "active_window": active,
            "windows": list(self.windows)[:24],
            "focused_element": dict(self.focused_element or {}),
            "visible_elements": list(self.visible_elements)[: max(1, int(max_elements))],
            "screen_size": dict(self.screen_size),
            "cursor_position": dict(self.cursor_position),
            "app_process": self.app_process,
            "app_name": self.app_name,
            "window_title": self.window_title,
            "ui_tree_available": self.ui_tree_available,
            "screenshot_available": self.screenshot_available,
            "snapshot_id": self.snapshot_id,
            "monitors": list(self.monitors),
            "modal": dict(self.modal or {}),
            "window_token": self.window_token,
        }

    def planner_fragment(self) -> Dict[str, Any]:
        """Small fragment for ContextSelector — not a UI tree dump."""
        active = self.active_window
        return {
            "active_window": active.title if active else "",
            "app_name": self.app_name,
            "process": self.app_process,
            "snapshot_id": self.snapshot_id,
            "window_count": len(self.windows),
            "focused_element": (self.focused_element or {}).get("name")
            or (self.focused_element or {}).get("element_id")
            or "",
            "modal": bool(self.modal),
        }


@dataclass
class UIContext:
    snapshot_id: str = ""
    active_window: str = ""
    window_token: str = ""
    visible_elements: List[Dict[str, Any]] = field(default_factory=list)
    selected_element: str = ""
    last_clicked_element: str = ""
    last_typed_element: str = ""
    result_list: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    screen_size: Dict[str, int] = field(default_factory=dict)
    generation: int = 0
    hwnd: int = 0
    window_bounds: Dict[str, int] = field(default_factory=dict)

    def expired(self, now: float | None = None, ttl: float = UI_CONTEXT_TTL_SEC) -> bool:
        stamp = float(now if now is not None else time.time())
        return (stamp - float(self.created_at or 0)) > float(ttl)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "active_window": self.active_window,
            "window_token": self.window_token,
            "visible_elements": list(self.visible_elements)[:20],
            "selected_element": self.selected_element,
            "last_clicked_element": self.last_clicked_element,
            "last_typed_element": self.last_typed_element,
            "result_list": list(self.result_list)[:12],
            "created_at": self.created_at,
            "screen_size": dict(self.screen_size),
            "generation": self.generation,
            "hwnd": self.hwnd,
            "window_bounds": dict(self.window_bounds),
        }


def new_snapshot_id() -> str:
    return f"ui_{uuid.uuid4().hex[:8]}"


def element_payload(element: Any) -> Dict[str, Any]:
    if isinstance(element, dict):
        data = dict(element)
        data.setdefault("element_id", data.get("id") or "")
        if data.get("is_password"):
            data["value"] = ""
            data["name"] = data.get("name") or data.get("text") or ""
            data["text"] = ""
        return data
    is_password = bool(getattr(element, "is_password", False))
    text = "" if is_password else str(getattr(element, "text", "") or "")
    value = "" if is_password else str(getattr(element, "value", "") or text)
    bounds = getattr(element, "bounds", None)
    bound_dict = bounds.as_dict() if bounds is not None and hasattr(bounds, "as_dict") else {}
    return {
        "element_id": str(getattr(element, "id", "") or ""),
        "role": str(getattr(element, "role", "") or ""),
        "name": text,
        "text": text,
        "automation_id": str(getattr(element, "automation_id", "") or ""),
        "value": value,
        "bounds": bound_dict,
        "enabled": bool(getattr(element, "enabled", True)),
        "visible": bool(getattr(element, "visible", True)),
        "focused": bool(getattr(element, "focused", False)),
        "confidence": float(getattr(element, "confidence", 1.0) or 0.0),
        "actions": list(getattr(element, "actions", None) or []),
        "source": str(getattr(element, "source", "") or ""),
        "is_password": is_password,
        "monitor_id": int((getattr(element, "meta", None) or {}).get("monitor_id") or 0),
    }


def monitors_payload(monitors: List[MonitorInfo] | None) -> List[Dict[str, Any]]:
    return [item.to_dict() for item in (monitors or [])]


WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
VERIFY_STRONG = "STRONG"
VERIFY_BASIC = "BASIC"
VERIFY_NONE = "NONE"
