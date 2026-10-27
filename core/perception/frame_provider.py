# ==========================
# ELIAN — FrameProvider contract (C.5)
# ==========================
"""Replaceable capture. Frames stay in RAM. No default disk writes."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Frame:
    frame_id: int = 0
    timestamp: float = 0.0
    width: int = 0
    height: int = 0
    hwnd: int = 0
    monitor_id: int = 0
    pixels: Any = None
    scene_hash: str = ""
    window_bounds: Dict[str, int] = field(default_factory=dict)

    def hash_pixels(self) -> str:
        if self.scene_hash:
            return self.scene_hash
        data = self.pixels
        if data is None:
            return ""
        if isinstance(data, (bytes, bytearray)):
            return hashlib.md5(bytes(data)).hexdigest()[:16]
        try:
            raw = str(data).encode("utf-8", errors="ignore")
            return hashlib.md5(raw).hexdigest()[:16]
        except Exception:
            return ""


class FrameProvider:
    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def latest_frame(self) -> Optional[Frame]:
        return None

    def latest_frame_metadata(self) -> Dict[str, Any]:
        frame = self.latest_frame()
        if frame is None:
            return {}
        return {
            "frame_id": frame.frame_id,
            "timestamp": frame.timestamp,
            "width": frame.width,
            "height": frame.height,
            "hwnd": frame.hwnd,
        }

    def capture_window(self, hwnd: int) -> Optional[Frame]:
        return self.latest_frame()

    def capture_monitor(self, monitor_id: int) -> Optional[Frame]:
        return self.latest_frame()

    def health(self) -> Dict[str, Any]:
        return {"provider": type(self).__name__, "ok": True, "store_frames": False}

    def saved_to_disk(self) -> bool:
        return False


def create_default_frame_provider() -> FrameProvider:
    """Replaceable capture. auto → WGC if truly live, else DXGI, else memory."""
    try:
        from Config import get_config_value

        name = str(get_config_value("screen_perception", "provider", default="auto") or "auto").lower()
    except Exception:
        name = "auto"
    if name == "memory":
        return MemoryFrameProvider()
    if name in ("wgc", "windows_graphics_capture"):
        try:
            from core.perception.windows_graphics_capture import WindowsGraphicsCaptureProvider

            wgc = WindowsGraphicsCaptureProvider()
            if wgc.health().get("ok"):
                return wgc
        except Exception:
            pass
    if name in ("auto", "dxgi", "wgc", "windows_graphics_capture"):
        try:
            from core.perception.dxgi_capture import DxgiFrameProvider

            return DxgiFrameProvider()
        except Exception:
            pass
    return MemoryFrameProvider()


class MemoryFrameProvider(FrameProvider):
    """In-RAM frames for tests and when real capture is off."""

    def __init__(self):
        self._frame: Optional[Frame] = None
        self._seq = 0
        self._started = False
        self._disk = False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False
        self._frame = None

    def push(
        self,
        pixels: Any,
        *,
        width: int = 64,
        height: int = 64,
        hwnd: int = 0,
        window_bounds: Optional[Dict[str, int]] = None,
    ) -> Frame:
        self._seq += 1
        frame = Frame(
            frame_id=self._seq,
            timestamp=time.time(),
            width=width,
            height=height,
            hwnd=int(hwnd or 0),
            pixels=pixels,
            window_bounds=dict(window_bounds or {}),
        )
        frame.scene_hash = frame.hash_pixels()
        self._frame = frame
        return frame

    def latest_frame(self) -> Optional[Frame]:
        return self._frame

    def health(self) -> Dict[str, Any]:
        return {
            "provider": "memory",
            "ok": True,
            "started": self._started,
            "store_frames": False,
            "frame_id": self._frame.frame_id if self._frame else 0,
        }
