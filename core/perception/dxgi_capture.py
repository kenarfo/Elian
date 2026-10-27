# ==========================
# ELIAN — DXGI / screenshot fallback FrameProvider (C.5)
# ==========================
"""On-demand capture via existing ScreenCaptureBackend. Not a 30 FPS disk writer."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from core.perception.frame_provider import Frame, FrameProvider


class DxgiFrameProvider(FrameProvider):
    def __init__(self, capture_backend=None):
        self._backend = capture_backend
        self._frame: Optional[Frame] = None
        self._seq = 0
        self._started = False

    def start(self) -> None:
        self._started = True
        if self._backend is None:
            try:
                from vision.capture import create_default_capture_backend

                self._backend = create_default_capture_backend()
            except Exception:
                self._backend = None

    def stop(self) -> None:
        self._started = False

    def latest_frame(self) -> Optional[Frame]:
        return self._frame

    def capture_monitor(self, monitor_id: int = 0) -> Optional[Frame]:
        if not self._started or self._backend is None:
            return self._frame
        try:
            cap = self._backend.capture(monitor=monitor_id)
        except Exception:
            return self._frame
        self._seq += 1
        pixels = getattr(cap, "image", None)
        frame = Frame(
            frame_id=self._seq,
            timestamp=time.time(),
            width=int(getattr(cap, "width", 0) or 0),
            height=int(getattr(cap, "height", 0) or 0),
            pixels=pixels,
        )
        frame.scene_hash = frame.hash_pixels()
        self._frame = frame
        return frame

    def health(self) -> Dict[str, Any]:
        return {
            "provider": "dxgi_fallback",
            "ok": self._backend is not None,
            "started": self._started,
            "store_frames": False,
        }
