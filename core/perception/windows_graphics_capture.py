# ==========================
# ELIAN — Windows.Graphics.Capture probe (C.5)
# ==========================
"""Preferred live capture. Optional; absence must not break ComputerAgent."""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.perception.frame_provider import Frame, FrameProvider


class WindowsGraphicsCaptureProvider(FrameProvider):
    """Probe-only unless a native WGC binding is present."""

    def __init__(self):
        self._available = False
        self._started = False
        self._error = ""
        try:
            import windows_capture  # type: ignore  # noqa: F401

            self._available = True
        except Exception as exc:
            self._error = str(exc or "wgc_unavailable")
            self._available = False

    def start(self) -> None:
        self._started = bool(self._available)

    def stop(self) -> None:
        self._started = False

    def latest_frame(self) -> Optional[Frame]:
        return None

    def health(self) -> Dict[str, Any]:
        return {
            "provider": "windows_graphics_capture",
            "ok": self._available,
            "started": self._started,
            "error": self._error,
            "store_frames": False,
        }
