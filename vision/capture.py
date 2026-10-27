# ==========================
# ELIAN — screen capture (Phase 5)
# ==========================

from __future__ import annotations

import time
from typing import Any, Optional, Tuple

from vision.models import ScreenCapture


class ScreenCaptureBackend:
    def capture(
        self,
        *,
        monitor: int = 0,
        region: Optional[Tuple[int, int, int, int]] = None,
        active_window_only: bool = False,
    ) -> ScreenCapture:
        raise NotImplementedError

    def get_active_window_info(self) -> Tuple[str, str]:
        return "", ""


class FakeScreenCapture(ScreenCaptureBackend):
    """Offline capture — no monitor required."""

    def __init__(
        self,
        *,
        width: int = 1920,
        height: int = 1080,
        left: int = 0,
        top: int = 0,
        active_window: str = "Fake Window",
        active_process: str = "fake.exe",
        monitor: int = 0,
        image: Any = None,
    ):
        self.width = width
        self.height = height
        self.left = left
        self.top = top
        self.active_window = active_window
        self.active_process = active_process
        self.monitor = monitor
        self.image = image
        self.calls = 0

    def get_active_window_info(self) -> Tuple[str, str]:
        return self.active_window, self.active_process

    def capture(
        self,
        *,
        monitor: int = 0,
        region: Optional[Tuple[int, int, int, int]] = None,
        active_window_only: bool = False,
    ) -> ScreenCapture:
        self.calls += 1
        left, top, width, height = self.left, self.top, self.width, self.height
        if region:
            left, top, width, height = region
        return ScreenCapture(
            image=self.image,
            width=width,
            height=height,
            monitor=monitor if monitor else self.monitor,
            left=left,
            top=top,
            active_window=self.active_window,
            active_process=self.active_process,
            captured_at=time.time(),
        )


class PyAutoGuiCapture(ScreenCaptureBackend):
    """Real capture via pyautogui (+ optional active window via Win32)."""

    def get_active_window_info(self) -> Tuple[str, str]:
        if __import__("os").name != "nt":
            return "", ""
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""

            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = ""
            if pid.value:
                try:
                    import psutil

                    process = psutil.Process(pid.value).name()
                except Exception:
                    process = ""
            return title, process
        except Exception:
            return "", ""

    def capture(
        self,
        *,
        monitor: int = 0,
        region: Optional[Tuple[int, int, int, int]] = None,
        active_window_only: bool = False,
    ) -> ScreenCapture:
        import pyautogui

        t0 = time.perf_counter()
        title, process = self.get_active_window_info()

        if active_window_only and __import__("os").name == "nt":
            region = self._active_window_region() or region

        if region:
            left, top, width, height = region
            image = pyautogui.screenshot(region=(left, top, width, height))
        else:
            # pyautogui captures virtual desktop across monitors
            image = pyautogui.screenshot()
            left, top = 0, 0
            width, height = image.size

        # Multi-monitor: all_screens may include negative offsets on Win
        try:
            import pyautogui as pag

            sizes = getattr(pag, "size", None)
            if sizes and region is None:
                width, height = image.size
        except Exception:
            pass

        print(
            f"[VISION] capture {width}x{height} "
            f"window={title!r} {(time.perf_counter() - t0) * 1000:.0f}ms"
        )
        return ScreenCapture(
            image=image,
            width=int(width),
            height=int(height),
            monitor=monitor,
            left=int(left),
            top=int(top),
            active_window=title,
            active_process=process,
        )

    def _active_window_region(self):
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
            left, top = int(rect.left), int(rect.top)
            width = max(1, int(rect.right - rect.left))
            height = max(1, int(rect.bottom - rect.top))
            return left, top, width, height
        except Exception:
            return None


def create_default_capture_backend() -> ScreenCaptureBackend:
    try:
        import pyautogui  # noqa: F401

        return PyAutoGuiCapture()
    except Exception as exc:
        print(f"[VISION] Capture backend unavailable: {exc}")
        return FakeScreenCapture(active_window="(unavailable)")


def capture_screen(
    *,
    backend: Optional[ScreenCaptureBackend] = None,
    monitor: int = 0,
    region: Optional[Tuple[int, int, int, int]] = None,
    active_window_only: bool = False,
) -> ScreenCapture:
    backend = backend or create_default_capture_backend()
    return backend.capture(
        monitor=monitor,
        region=region,
        active_window_only=active_window_only,
    )
