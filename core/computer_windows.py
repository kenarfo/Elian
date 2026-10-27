# ==========================
# ELIAN — Windows window store (Phase 13 Stage C)
# ==========================
"""Structured window list. Fake store for tests; Win32 EnumWindows in production."""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

SW_RESTORE = 9
SW_MINIMIZE = 6
SW_MAXIMIZE = 3
SW_SHOW = 5


@dataclass
class WindowInfo:
    window_id: str
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
    monitor_id: int = 0
    exists: bool = True

    def token(self) -> str:
        b = self.bounds or {}
        return (
            f"{self.hwnd}|{self.title}|{self.process_name}|"
            f"{b.get('x', 0)},{b.get('y', 0)},{b.get('width', 0)},{b.get('height', 0)}|"
            f"{int(self.minimized)}|{int(self.maximized)}"
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["window_token"] = self.token()
        return data


@dataclass
class MonitorInfo:
    monitor_id: int
    bounds: Dict[str, int]
    primary: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class WindowStore:
    def list_windows(self) -> List[WindowInfo]:
        raise NotImplementedError

    def get_active(self) -> Optional[WindowInfo]:
        raise NotImplementedError

    def get(self, window_id: str) -> Optional[WindowInfo]:
        raise NotImplementedError

    def focus(self, window_id: str) -> bool:
        raise NotImplementedError

    def show(self, window_id: str, command: int) -> bool:
        raise NotImplementedError

    def close(self, window_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def open_app(self, name: str) -> Dict[str, Any]:
        raise NotImplementedError

    def monitors(self) -> List[MonitorInfo]:
        return [
            MonitorInfo(
                monitor_id=0,
                bounds={"x": 0, "y": 0, "width": 1920, "height": 1080},
                primary=True,
            )
        ]

    def screen_size(self) -> Tuple[int, int]:
        mons = self.monitors()
        if not mons:
            return (1920, 1080)
        b = mons[0].bounds
        return (int(b.get("width") or 1920), int(b.get("height") or 1080))

    def cursor_position(self) -> Tuple[int, int]:
        return (0, 0)

    def mark_opened_by_elian(self, window_id: str) -> None:
        return None


@dataclass
class FakeWindowRecord:
    info: WindowInfo
    editor_text: str = ""
    selected: bool = False
    unsaved: bool = False
    modal: str = ""
    supports_selection: bool = True
    elements: List[Dict[str, Any]] = field(default_factory=list)
    generation: int = 1


class FakeWindowStore(WindowStore):
    """Deterministic in-memory Windows for Stage C fixtures / smoke."""

    def __init__(self, windows: Optional[List[FakeWindowRecord]] = None):
        self._windows: Dict[str, FakeWindowRecord] = {}
        self.closed: List[str] = []
        self.dont_save_clicks: int = 0
        self.opened: List[str] = []
        self.typed_into: List[str] = []
        self.last_hotkey: str = ""
        self.last_hotkey_window: str = ""
        self.last_hotkey_process: str = ""
        self.scrolls: List[tuple] = []
        self.drags: List[tuple] = []
        self.generation: int = 1
        self.resolution: Tuple[int, int] = (1920, 1080)
        self._cursor: Tuple[int, int] = (100, 100)
        self._monitors = [
            MonitorInfo(0, {"x": 0, "y": 0, "width": 1920, "height": 1080}, True)
        ]
        for rec in windows or []:
            self._windows[rec.info.window_id] = rec

    def add_window(self, rec: FakeWindowRecord) -> None:
        self._windows[rec.info.window_id] = rec
        self.generation += 1

    def list_windows(self) -> List[WindowInfo]:
        return [rec.info for rec in self._windows.values() if rec.info.exists]

    def get_active(self) -> Optional[WindowInfo]:
        for rec in self._windows.values():
            if rec.info.exists and rec.info.focused:
                return rec.info
        visible = [rec.info for rec in self._windows.values() if rec.info.exists]
        return visible[0] if visible else None

    def get(self, window_id: str) -> Optional[WindowInfo]:
        rec = self._lookup(window_id)
        return rec.info if rec and rec.info.exists else None

    def _lookup(self, window_id: str) -> Optional[FakeWindowRecord]:
        raw = str(window_id or "").strip()
        if raw in self._windows:
            return self._windows[raw]
        lowered = raw.lower()
        for rec in self._windows.values():
            info = rec.info
            if str(info.hwnd) == raw:
                return rec
            if info.app_name.lower() == lowered or info.process_name.lower() == lowered:
                return rec
            if info.title.lower() == lowered or lowered in info.title.lower():
                return rec
        return None

    def focus(self, window_id: str) -> bool:
        rec = self._lookup(window_id)
        if rec is None or not rec.info.exists:
            return False
        for other in self._windows.values():
            other.info.focused = False
        rec.info.focused = True
        rec.info.minimized = False
        rec.info.visible = True
        rec.info.last_action = "focus"
        rec.info.updated_at = time.time()
        rec.generation += 1
        self.generation += 1
        return True

    def show(self, window_id: str, command: int) -> bool:
        rec = self._lookup(window_id)
        if rec is None or not rec.info.exists:
            return False
        if command == SW_MINIMIZE:
            rec.info.minimized = True
            rec.info.maximized = False
            rec.info.focused = False
            rec.info.last_action = "minimize"
        elif command == SW_MAXIMIZE:
            rec.info.maximized = True
            rec.info.minimized = False
            rec.info.visible = True
            rec.info.last_action = "maximize"
        elif command in (SW_RESTORE, SW_SHOW):
            rec.info.minimized = False
            rec.info.maximized = False
            rec.info.visible = True
            rec.info.last_action = "restore"
        rec.info.updated_at = time.time()
        rec.generation += 1
        self.generation += 1
        return True

    def close(self, window_id: str) -> Dict[str, Any]:
        rec = self._lookup(window_id)
        if rec is None or not rec.info.exists:
            return {"ok": False, "reason": "missing"}
        if rec.unsaved:
            rec.modal = "unsaved"
            rec.info.last_action = "close_blocked_unsaved"
            rec.generation += 1
            self.generation += 1
            return {
                "ok": False,
                "reason": "unsaved",
                "modal": "unsaved",
                "title": "Zapisać zmiany?",
            }
        rec.info.exists = False
        rec.info.visible = False
        rec.info.focused = False
        rec.info.last_action = "close"
        self.closed.append(rec.info.window_id)
        self.generation += 1
        return {"ok": True, "reason": "closed"}

    def click_dont_save(self, window_id: str = "") -> None:
        self.dont_save_clicks += 1

    def open_app(self, name: str) -> Dict[str, Any]:
        target = str(name or "").strip()
        existing = self._lookup(target)
        if existing is not None and existing.info.exists:
            self.focus(existing.info.window_id)
            return {
                "ok": True,
                "duplicate": False,
                "focused_existing": True,
                "window_id": existing.info.window_id,
            }
        wid = f"win_{len(self._windows) + 1}"
        info = WindowInfo(
            window_id=wid,
            hwnd=4000 + len(self._windows),
            process_name=_process_for_app(target),
            app_name=target,
            title=target,
            bounds={"x": 80, "y": 80, "width": 800, "height": 600},
            focused=True,
            visible=True,
            opened_by_elian=True,
            last_action="open",
        )
        for other in self._windows.values():
            other.info.focused = False
        rec = FakeWindowRecord(
            info=info,
            elements=_default_elements_for(target),
        )
        self._windows[wid] = rec
        self.opened.append(target)
        self.generation += 1
        return {"ok": True, "duplicate": False, "window_id": wid, "focused_existing": False}

    def mark_opened_by_elian(self, window_id: str) -> None:
        rec = self._lookup(window_id)
        if rec is not None:
            rec.info.opened_by_elian = True

    def monitors(self) -> List[MonitorInfo]:
        return list(self._monitors)

    def screen_size(self) -> Tuple[int, int]:
        return self.resolution

    def cursor_position(self) -> Tuple[int, int]:
        return self._cursor

    def bump_window(self, window_id: str) -> None:
        rec = self._lookup(window_id)
        if rec is None:
            return
        rec.info.title = (rec.info.title or "Window") + " *"
        rec.generation += 1
        self.generation += 1

    def set_editor_text(self, window_id: str, text: str, *, selected: bool = False) -> None:
        rec = self._lookup(window_id)
        if rec is None:
            return
        rec.editor_text = str(text or "")
        rec.selected = bool(selected)
        rec.unsaved = True
        rec.generation += 1
        self.typed_into.append(rec.info.window_id)

    def record(self, window_id: str) -> Optional[FakeWindowRecord]:
        return self._lookup(window_id)


def _process_for_app(name: str) -> str:
    lowered = str(name or "").strip().lower()
    mapping = {
        "notatnik": "notepad.exe",
        "notepad": "notepad.exe",
        "kalkulator": "calculator.exe",
        "calculator": "calculator.exe",
        "chrome": "chrome.exe",
        "discord": "discord.exe",
        "spotify": "spotify.exe",
    }
    return mapping.get(lowered, f"{lowered.replace(' ', '')}.exe" if lowered else "app.exe")


def _default_elements_for(name: str) -> List[Dict[str, Any]]:
    lowered = str(name or "").lower()
    if "notatnik" in lowered or "notepad" in lowered:
        return [
            {
                "element_id": "input_1",
                "role": "document",
                "name": "Text Editor",
                "automation_id": "15",
                "value": "",
                "enabled": True,
                "visible": True,
                "focused": True,
                "bounds": {"x": 90, "y": 120, "width": 700, "height": 500},
                "actions": ["focus", "type", "value"],
                "confidence": 0.96,
            }
        ]
    return [
        {
            "element_id": "el_1",
            "role": "window",
            "name": name,
            "enabled": True,
            "visible": True,
            "bounds": {"x": 80, "y": 80, "width": 800, "height": 600},
            "actions": ["focus"],
            "confidence": 0.9,
        }
    ]


class Win32WindowStore(WindowStore):
    """Live Windows enumeration via user32. No click(x,y) here."""

    def list_windows(self) -> List[WindowInfo]:
        if os.name != "nt":
            return []
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetForegroundWindow.argtypes = []
        results: List[WindowInfo] = []
        foreground = int(user32.GetForegroundWindow() or 0)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(hwnd, _lp):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = str(buf.value or "").strip()
                if not title:
                    return True
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                process_name = ""
                app_name = title
                if pid.value:
                    try:
                        import psutil

                        proc = psutil.Process(int(pid.value))
                        process_name = str(proc.name() or "")
                        app_name = process_name.rsplit(".", 1)[0]
                    except Exception:
                        process_name = ""
                hwnd_i = int(hwnd)
                results.append(
                    WindowInfo(
                        window_id=str(hwnd_i),
                        hwnd=hwnd_i,
                        process_name=process_name,
                        app_name=app_name,
                        title=title,
                        bounds={
                            "x": int(rect.left),
                            "y": int(rect.top),
                            "width": int(rect.right - rect.left),
                            "height": int(rect.bottom - rect.top),
                        },
                        focused=hwnd_i == foreground,
                        minimized=bool(user32.IsIconic(hwnd)),
                        maximized=bool(user32.IsZoomed(hwnd)),
                        visible=True,
                        monitor_id=0,
                    )
                )
            except Exception:
                return True
            return True

        user32.EnumWindows(callback_type(visit), 0)
        return results

    def get_active(self) -> Optional[WindowInfo]:
        for item in self.list_windows():
            if item.focused:
                return item
        return None

    def get(self, window_id: str) -> Optional[WindowInfo]:
        raw = str(window_id or "").strip().lower()
        for item in self.list_windows():
            if item.window_id == str(window_id) or str(item.hwnd) == str(window_id):
                return item
            if raw and (
                raw == item.app_name.lower()
                or raw == item.process_name.lower()
                or raw in item.title.lower()
            ):
                return item
        return None

    def focus(self, window_id: str) -> bool:
        info = self.get(window_id)
        if info is None or not info.hwnd:
            return False
        if os.name != "nt":
            return False
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = int(info.hwnd)
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        return bool(user32.SetForegroundWindow(hwnd))

    def show(self, window_id: str, command: int) -> bool:
        info = self.get(window_id)
        if info is None or not info.hwnd or os.name != "nt":
            return False
        import ctypes

        return bool(ctypes.windll.user32.ShowWindow(int(info.hwnd), int(command)))

    def close(self, window_id: str) -> Dict[str, Any]:
        info = self.get(window_id)
        if info is None or not info.hwnd or os.name != "nt":
            return {"ok": False, "reason": "missing"}
        import ctypes

        WM_CLOSE = 0x0010
        ctypes.windll.user32.PostMessageW(int(info.hwnd), WM_CLOSE, 0, 0)
        return {"ok": True, "reason": "posted_close"}

    def open_app(self, name: str) -> Dict[str, Any]:
        existing = self.get(name)
        if existing is not None and existing.exists:
            self.focus(existing.window_id)
            return {
                "ok": True,
                "focused_existing": True,
                "window_id": existing.window_id,
            }
        try:
            from AppLauncher import launch_application

            ok, message = launch_application(name)
            return {"ok": bool(ok), "message": message, "focused_existing": False}
        except Exception as exc:
            return {"ok": False, "message": str(exc), "focused_existing": False}

    def monitors(self) -> List[MonitorInfo]:
        if os.name != "nt":
            return super().monitors()
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        rows: List[MonitorInfo] = []

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.POINTER(RECT),
            ctypes.c_double,
        )

        def _enum(hmon, _hdc, lprect, _data):
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                rc = info.rcMonitor
                rows.append(
                    MonitorInfo(
                        monitor_id=len(rows),
                        bounds={
                            "x": int(rc.left),
                            "y": int(rc.top),
                            "width": int(rc.right - rc.left),
                            "height": int(rc.bottom - rc.top),
                        },
                        primary=bool(info.dwFlags & 1),
                    )
                )
            return 1

        try:
            user32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(_enum), 0)
        except Exception:
            return super().monitors()
        return rows or super().monitors()

    def cursor_position(self) -> Tuple[int, int]:
        if os.name != "nt":
            return (0, 0)
        import ctypes
        from ctypes import wintypes

        pt = wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return (int(pt.x), int(pt.y))
        return (0, 0)


def default_window_store() -> WindowStore:
    if os.name == "nt":
        return Win32WindowStore()
    return FakeWindowStore()
