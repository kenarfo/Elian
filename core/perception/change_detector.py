# ==========================
# ELIAN — screen change detector (C.5)
# ==========================
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.perception.frame_provider import Frame


@dataclass
class ScreenChange:
    frame_id: int = 0
    timestamp: float = 0.0
    changed_ratio: float = 0.0
    changed_regions: List[Dict[str, Any]] = field(default_factory=list)
    cursor_changed: bool = False
    window_changed: bool = False
    modal_candidate: bool = False
    scene_hash: str = ""
    major: bool = False


class ChangeDetector:
    def __init__(self, major_ratio: float = 0.08):
        self._prev_hash = ""
        self._prev_hwnd = 0
        self.major_ratio = float(major_ratio)

    def observe(self, frame: Optional[Frame], *, hwnd: int = 0) -> ScreenChange:
        if frame is None:
            return ScreenChange(timestamp=time.time())
        scene = frame.hash_pixels()
        window_changed = bool(hwnd and self._prev_hwnd and int(hwnd) != int(self._prev_hwnd))
        changed = 0.0 if scene == self._prev_hash or not self._prev_hash else 1.0
        if scene != self._prev_hash and self._prev_hash:
            changed = 1.0
        major = changed >= self.major_ratio or window_changed
        regions = [{"x": 0, "y": 0, "w": frame.width, "h": frame.height}] if major else []
        change = ScreenChange(
            frame_id=int(frame.frame_id),
            timestamp=float(frame.timestamp or time.time()),
            changed_ratio=changed,
            changed_regions=regions,
            window_changed=window_changed,
            scene_hash=scene,
            major=major,
        )
        self._prev_hash = scene
        if hwnd:
            self._prev_hwnd = int(hwnd)
        return change
