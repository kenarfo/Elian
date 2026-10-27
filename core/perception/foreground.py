# ==========================
# ELIAN — foreground window truth (C.5.3)
# ==========================
"""Cheap HWND metadata. No vision, no UIA tree, no side effects."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass
class ForegroundInfo:
    hwnd: int = 0
    title: str = ""
    process: str = ""
    pid: int = 0
    bounds: Dict[str, int] = field(default_factory=dict)
    source: str = "foreground"
    timestamp: float = 0.0
    ok: bool = False

    def to_window_dict(self) -> Dict[str, Any]:
        return {
            "hwnd": int(self.hwnd or 0),
            "title": self.title,
            "process": self.process,
            "pid": int(self.pid or 0),
            "bounds": dict(self.bounds or {}),
        }


class ForegroundProvider:
    def current(self) -> ForegroundInfo:
        raise NotImplementedError

    def start(self, on_change: Callable[[ForegroundInfo], None]) -> None:
        pass

    def stop(self) -> None:
        pass


class FakeForegroundProvider(ForegroundProvider):
    """Tests / offline: inject HWND changes as if the user clicked another window."""

    def __init__(self, **seed: Any):
        self._info = ForegroundInfo(timestamp=time.time(), **_seed_info(seed))
        self._on_change: Optional[Callable[[ForegroundInfo], None]] = None
        self.started = False

    def current(self) -> ForegroundInfo:
        return self._info

    def set(self, **kwargs: Any) -> ForegroundInfo:
        data = self._info.to_window_dict()
        data.update({k: v for k, v in kwargs.items() if k != "ok"})
        self._info = ForegroundInfo(
            hwnd=int(data.get("hwnd") or 0),
            title=str(data.get("title") or ""),
            process=str(data.get("process") or ""),
            pid=int(data.get("pid") or 0),
            bounds=dict(data.get("bounds") or {}),
            source="foreground",
            timestamp=time.time(),
            ok=bool(int(data.get("hwnd") or 0)),
        )
        if self._on_change is not None:
            self._on_change(self._info)
        return self._info

    def start(self, on_change: Callable[[ForegroundInfo], None]) -> None:
        self._on_change = on_change
        self.started = True

    def stop(self) -> None:
        self.started = False
        self._on_change = None


def _seed_info(seed: Dict[str, Any]) -> Dict[str, Any]:
    hwnd = int(seed.get("hwnd") or 0)
    return {
        "hwnd": hwnd,
        "title": str(seed.get("title") or ""),
        "process": str(seed.get("process") or ""),
        "pid": int(seed.get("pid") or 0),
        "bounds": dict(seed.get("bounds") or {}),
        "ok": bool(hwnd),
        "source": "foreground",
    }


class Win32ForegroundProvider(ForegroundProvider):
    """GetForegroundWindow + optional EVENT_SYSTEM_FOREGROUND / 8 Hz poll fallback."""

    EVENT_SYSTEM_FOREGROUND = 0x0003
    WINEVENT_OUTOFCONTEXT = 0x0000
    WINEVENT_SKIPOWNPROCESS = 0x0002

    def __init__(self, *, poll_hz: float = 8.0):
        self.poll_hz = max(4.0, min(10.0, float(poll_hz or 8.0)))
        self._on_change: Optional[Callable[[ForegroundInfo], None]] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hook = None
        self._proc = None
        self._last_hwnd = 0
        self.hook_active = False
        self.poll_active = False

    def current(self) -> ForegroundInfo:
        return read_foreground_window()

    def start(self, on_change: Callable[[ForegroundInfo], None]) -> None:
        self._on_change = on_change
        self._stop.clear()
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop,
            name="elian-foreground",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._unhook()
        self._on_change = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def _emit_if_changed(self, info: ForegroundInfo) -> None:
        hwnd = int(info.hwnd or 0)
        if not hwnd or hwnd == self._last_hwnd:
            return
        self._last_hwnd = hwnd
        cb = self._on_change
        if cb is not None:
            try:
                cb(info)
            except Exception:
                pass

    def _loop(self) -> None:
        hooked = self._try_hook()
        self.hook_active = bool(hooked)
        self.poll_active = True
        interval = 1.0 / self.poll_hz
        while not self._stop.is_set():
            try:
                info = self.current()
                self._emit_if_changed(info)
            except Exception:
                pass
            if hooked:
                self._pump_messages(timeout_ms=int(interval * 1000))
            else:
                self._stop.wait(timeout=interval)
        self._unhook()

    def _try_hook(self) -> bool:
        if os.name != "nt":
            return False
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            WinEventProc = ctypes.WINFUNCTYPE(
                None,
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.HWND,
                ctypes.c_long,
                ctypes.c_long,
                wintypes.DWORD,
                wintypes.DWORD,
            )

            def _callback(h_win_event_hook, event, hwnd, id_object, id_child, thread, time_ms):
                if int(hwnd or 0):
                    self._emit_if_changed(read_foreground_window(hwnd=int(hwnd)))

            self._proc = WinEventProc(_callback)
            self._hook = user32.SetWinEventHook(
                self.EVENT_SYSTEM_FOREGROUND,
                self.EVENT_SYSTEM_FOREGROUND,
                0,
                self._proc,
                0,
                0,
                self.WINEVENT_OUTOFCONTEXT | self.WINEVENT_SKIPOWNPROCESS,
            )
            return bool(self._hook)
        except Exception:
            self._hook = None
            self._proc = None
            return False

    def _pump_messages(self, timeout_ms: int = 125) -> None:
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            msg = wintypes.MSG()
            user32.MsgWaitForMultipleObjects(0, None, False, int(timeout_ms), 0x04FF)
            while user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            self._stop.wait(timeout=max(0.05, timeout_ms / 1000.0))

    def _unhook(self) -> None:
        if not self._hook:
            return
        try:
            import ctypes

            ctypes.windll.user32.UnhookWinEvent(self._hook)
        except Exception:
            pass
        self._hook = None
        self._proc = None


def _is_window(user32, hwnd: int) -> bool:
    try:
        user32.IsWindow.argtypes = [__import__("ctypes").wintypes.HWND]
        user32.IsWindow.restype = __import__("ctypes").wintypes.BOOL
        return bool(user32.IsWindow(int(hwnd)))
    except Exception:
        return bool(hwnd)


def _hwnd_from_ctypes() -> int:
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetForegroundWindow.argtypes = []
        handle = int(user32.GetForegroundWindow() or 0)
        if handle and _is_window(user32, handle):
            return handle
    except Exception:
        return 0
    return 0


def _hwnd_from_pywin32() -> int:
    try:
        import win32gui

        handle = int(win32gui.GetForegroundWindow() or 0)
        if handle:
            try:
                import win32gui as g

                if g.IsWindow(handle):
                    return handle
            except Exception:
                return handle
    except Exception:
        return 0
    return 0


def _hwnd_from_windows_observation() -> int:
    try:
        from core.computer_windows import WindowsComputerBackend

        win = WindowsComputerBackend().get_active()
        return int(getattr(win, "hwnd", 0) or 0)
    except Exception:
        return 0


def _hwnd_from_uia() -> int:
    try:
        from vision.uia import get_foreground_hwnd

        return int(get_foreground_hwnd() or 0)
    except Exception:
        try:
            from vision.uia import live_backend

            backend = live_backend()
            hwnd = getattr(backend, "foreground_hwnd", None)
            if callable(hwnd):
                return int(hwnd() or 0)
        except Exception:
            return 0
    return 0


def resolve_foreground_hwnd(*, hwnd: int = 0) -> tuple[int, str]:
    """ctypes → pywin32 → local window observation → UIA. Never trust hwnd=0."""
    if int(hwnd or 0):
        return int(hwnd), "given"
    chain = (
        ("ctypes_user32", _hwnd_from_ctypes),
        ("pywin32", _hwnd_from_pywin32),
        ("windows_observation", _hwnd_from_windows_observation),
        ("uia_active", _hwnd_from_uia),
    )
    for name, fn in chain:
        try:
            handle = int(fn() or 0)
        except Exception:
            handle = 0
        if handle:
            return handle, name
    return 0, "none"


def read_foreground_window(*, hwnd: int = 0) -> ForegroundInfo:
    if os.name != "nt" and not int(hwnd or 0):
        return ForegroundInfo(timestamp=time.time(), ok=False, source="foreground")
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        handle, source = resolve_foreground_hwnd(hwnd=hwnd)
        if not handle:
            return ForegroundInfo(
                timestamp=time.time(), source="foreground", ok=False
            )
        length = int(user32.GetWindowTextLengthW(handle) or 0)
        title = ""
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buf, length + 1)
            title = str(buf.value or "").strip()
        rect = wintypes.RECT()
        user32.GetWindowRect(handle, ctypes.byref(rect))
        bounds = {
            "x": int(rect.left),
            "y": int(rect.top),
            "width": int(rect.right - rect.left),
            "height": int(rect.bottom - rect.top),
        }
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        process = ""
        if pid.value:
            try:
                import psutil

                process = str(psutil.Process(int(pid.value)).name() or "")
            except Exception:
                process = ""
        return ForegroundInfo(
            hwnd=handle,
            title=title,
            process=process,
            pid=int(pid.value or 0),
            bounds=bounds,
            source=source or "foreground",
            timestamp=time.time(),
            ok=True,
        )
    except Exception:
        return ForegroundInfo(timestamp=time.time(), source="foreground", ok=False)


def live_foreground_provider() -> ForegroundProvider:
    if os.name == "nt":
        return Win32ForegroundProvider()
    return FakeForegroundProvider()
