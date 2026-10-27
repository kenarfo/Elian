# ==========================
# ELIAN — vision data models (Phase 5)
# ==========================

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Bounds:
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> Tuple[int, int]:
        return (
            int(self.x + self.width / 2),
            int(self.y + self.height / 2),
        )

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def as_dict(self) -> Dict[str, int]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    def contains_point(self, px: int, py: int) -> bool:
        return self.x <= px < self.right and self.y <= py < self.bottom


@dataclass
class ScreenCapture:
    """In-memory screen capture (prefer RAM; path optional)."""

    image: Any  # PIL.Image or None in tests
    width: int
    height: int
    monitor: int = 0
    left: int = 0
    top: int = 0
    active_window: str = ""
    active_process: str = ""
    scale_factor: float = 1.0
    path: Optional[str] = None
    captured_at: float = field(default_factory=time.time)

    @property
    def screen_bounds(self) -> Bounds:
        return Bounds(self.left, self.top, self.width, self.height)


@dataclass
class UIElement:
    id: str
    role: str
    text: str
    bounds: Bounds
    source: str  # uia | ocr | vision | fake
    snapshot_id: str = ""
    confidence: float = 1.0
    enabled: bool = True
    visible: bool = True
    is_password: bool = False
    automation_id: str = ""
    value: str = ""
    focused: bool = False
    actions: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def label_for_log(self) -> str:
        if self.is_password:
            return f'{self.role} [password]'
        return f'{self.role} "{self.text}"'

    def to_planner_dict(self) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "role": self.role,
            "text": "" if self.is_password else self.text,
            "bounds": self.bounds.as_dict(),
            "source": self.source,
            "confidence": round(float(self.confidence), 3),
            "enabled": self.enabled,
        }
        if self.is_password:
            data["is_password"] = True
        return data


@dataclass
class ScreenSnapshot:
    id: str
    capture: ScreenCapture
    elements: List[UIElement] = field(default_factory=list)
    summary: str = ""
    visible_text: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    valid: bool = True

    @classmethod
    def new_id(cls) -> str:
        return f"screen_{uuid.uuid4().hex[:8]}"

    def invalidate(self) -> None:
        self.valid = False

    def get_element(self, element_id: str) -> Optional[UIElement]:
        raw = str(element_id or "").strip()
        if not raw:
            return None
        # Accept "screen_x:button_1" or "button_1"
        if ":" in raw:
            snap, local = raw.split(":", 1)
            if snap != self.id:
                return None
            raw = local
        for element in self.elements:
            if element.id == raw or f"{self.id}:{element.id}" == element_id:
                return element
        return None

    def observation_for_planner(self, *, max_elements: int = 40) -> str:
        lines = [
            f"SNAPSHOT: {self.id} ({'valid' if self.valid else 'STALE'})",
            f"ACTIVE WINDOW: {self.capture.active_window or '(unknown)'}",
        ]
        if self.capture.active_process:
            lines.append(f"PROCESS: {self.capture.active_process}")
        lines.append(f"SCREEN: {self.capture.width}x{self.capture.height} monitor={self.capture.monitor}")
        if self.summary:
            lines.append(f"SUMMARY: {self.summary}")
        if self.visible_text:
            preview = "; ".join(self.visible_text[:20])
            lines.append(f"VISIBLE TEXT: {preview}")
        lines.append("ELEMENTS:")
        ranked = sorted(
            [e for e in self.elements if e.visible],
            key=lambda e: (
                0 if e.role in ("button", "link", "menuitem", "textbox", "edit") else 1,
                -float(e.confidence),
                e.id,
            ),
        )[:max_elements]
        if not ranked:
            lines.append("  (none)")
        for element in ranked:
            text = "[password]" if element.is_password else (element.text or "")
            lines.append(
                f"  {element.id}: {element.role} \"{text}\" "
                f"conf={element.confidence:.2f} src={element.source}"
            )
        if self.sources:
            lines.append(f"SOURCES: {','.join(self.sources)}")
        return "\n".join(lines)

    def to_result_data(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.id,
            "summary": self.summary,
            "visible_text": [
                t for t in self.visible_text if t
            ][:40],
            "elements": [
                e.to_planner_dict()
                for e in self.elements
                if e.visible and not e.is_password
            ][:40],
            "active_window": self.capture.active_window,
            "active_process": self.capture.active_process,
            "width": self.capture.width,
            "height": self.capture.height,
            "monitor": self.capture.monitor,
            "sources": list(self.sources),
            "valid": self.valid,
        }
