# ==========================
# ELIAN — UI Automation backend (Phase 5)
# ==========================

from __future__ import annotations

import time
from typing import List, Optional

from vision.models import Bounds, UIElement

_DESCENDANTS_SEARCHES = 0
last_descendants_searches = 0


def descendants_search_count() -> int:
    return _DESCENDANTS_SEARCHES


def reset_descendants_search_count() -> None:
    global _DESCENDANTS_SEARCHES, last_descendants_searches
    _DESCENDANTS_SEARCHES = 0
    last_descendants_searches = 0


class UIBackend:
    def list_elements(
        self, *, max_elements: int = 80, roles: Optional[tuple] = None, hwnd: int = 0
    ) -> List[UIElement]:
        raise NotImplementedError

    def query_editors(self, *, max_elements: int = 8, hwnd: int = 0) -> List[UIElement]:
        return [
            el
            for el in self.list_elements(max_elements=max_elements * 4, roles=("document", "textbox", "edit", "input"), hwnd=hwnd)
            if str(el.role or "").lower() in ("document", "textbox", "edit", "input")
        ][:max_elements]

    def read_value(self, element: UIElement) -> str:
        if element is None or element.is_password:
            return ""
        return str(element.value or element.text or "")

    def click(self, element: UIElement) -> bool:
        raise NotImplementedError

    def type_text(self, element: UIElement, text: str) -> bool:
        raise NotImplementedError

    def press_key(self, keys: str) -> bool:
        raise NotImplementedError

    def scroll(self, *, direction: str, amount: int = 3) -> bool:
        raise NotImplementedError

    @property
    def available(self) -> bool:
        return True


class FakeUIBackend(UIBackend):
    """In-memory UI tree for offline tests."""

    def __init__(self, elements: Optional[List[UIElement]] = None):
        self._elements: List[UIElement] = list(elements or [])
        self.clicks: List[str] = []
        self.typed: List[tuple] = []
        self.keys: List[str] = []
        self.scrolls: List[tuple] = []
        self.double_clicks: List[str] = []
        self.right_clicks: List[str] = []
        self.drags: List[tuple] = []
        self.screen_version = 1
        self.on_click = None

    def set_elements(self, elements: List[UIElement]) -> None:
        self._elements = list(elements)
        self.screen_version += 1

    def list_elements(
        self, *, max_elements: int = 80, roles: Optional[tuple] = None, hwnd: int = 0
    ) -> List[UIElement]:
        rows = list(self._elements)
        if roles:
            wanted = {str(r).lower() for r in roles}
            rows = [el for el in rows if str(el.role or "").lower() in wanted]
        return rows[:max_elements]

    def query_editors(self, *, max_elements: int = 8, hwnd: int = 0) -> List[UIElement]:
        return self.list_elements(
            max_elements=max_elements,
            roles=("document", "textbox", "edit", "input"),
        )

    def read_value(self, element: UIElement) -> str:
        if element is None or element.is_password:
            return ""
        for el in self._elements:
            if el.id == element.id:
                return str(el.value or el.text or "")
        return str(element.value or element.text or "")

    def click(self, element: UIElement) -> bool:
        self.clicks.append(element.id)
        if callable(self.on_click):
            self.on_click(element)
        return True

    def type_text(self, element: UIElement, text: str) -> bool:
        if element.is_password:
            self.typed.append((element.id, "***"))
        else:
            self.typed.append((element.id, text))
            element.value = str((element.value or "") + text)
            if not element.is_password:
                element.text = element.value
        return True

    def press_key(self, keys: str) -> bool:
        self.keys.append(keys)
        return True

    def double_click(self, element: UIElement) -> bool:
        self.double_clicks.append(element.id)
        return self.click(element)

    def right_click(self, element: UIElement) -> bool:
        self.right_clicks.append(element.id)
        return True

    def invoke(self, element: UIElement) -> bool:
        return self.click(element)

    def drag(self, start: UIElement, end: UIElement) -> bool:
        self.drags.append((start.id, end.id))
        return True

    def scroll(self, *, direction: str, amount: int = 3) -> bool:
        self.scrolls.append((direction, amount))
        self.screen_version += 1
        return True


class PyAutoGuiUIBackend(UIBackend):
    """Fallback executor using pyautogui (no accessibility tree)."""

    def list_elements(
        self, *, max_elements: int = 80, roles: Optional[tuple] = None, hwnd: int = 0
    ) -> List[UIElement]:
        return []

    @property
    def available(self) -> bool:
        try:
            import pyautogui  # noqa: F401

            return True
        except Exception:
            return False

    def click(self, element: UIElement) -> bool:
        import pyautogui

        x, y = element.bounds.center
        pyautogui.click(x, y)
        return True

    def type_text(self, element: UIElement, text: str) -> bool:
        import pyautogui

        x, y = element.bounds.center
        pyautogui.click(x, y)
        time.sleep(0.05)
        try:
            pyautogui.write(text)
        except Exception:
            pyautogui.typewrite(text, interval=0.02)
        return True

    def press_key(self, keys: str) -> bool:
        import pyautogui

        parts = [p.strip().lower() for p in str(keys).replace("-", "+").split("+") if p.strip()]
        if not parts:
            return False
        if len(parts) == 1:
            pyautogui.press(parts[0])
        else:
            pyautogui.hotkey(*parts)
        return True

    def scroll(self, *, direction: str, amount: int = 3) -> bool:
        import pyautogui

        clicks = abs(int(amount)) * 100
        if direction in ("up", "góra", "gora"):
            pyautogui.scroll(clicks)
        else:
            pyautogui.scroll(-clicks)
        return True


def _foreground_hwnd() -> int:
    try:
        import win32gui  # type: ignore

        handle = int(win32gui.GetForegroundWindow() or 0)
        if handle and win32gui.IsWindow(handle):
            return handle
        return 0
    except Exception:
        return 0


def get_foreground_hwnd() -> int:
    return _foreground_hwnd()


def _hwnd_alive(hwnd: int) -> bool:
    if not hwnd:
        return False
    try:
        import win32gui  # type: ignore

        return bool(win32gui.IsWindow(int(hwnd)))
    except Exception:
        return True


class WindowsUIABackend(UIBackend):
    """
    Best-effort UI Automation via pywinauto if installed.

    Targeted editor lookup connects by hwnd and walks a shallow children()
    BFS. It must not call Desktop().window(active_only=True) (default find
    timeout ~5s) or descendants() of the whole window on the hot path.
    """

    def __init__(self):
        self._available = False
        self._Application = None
        self._wrappers = {}
        self._pinned_hwnd = 0
        self._pinned_id = ""
        self._connected_hwnd = 0
        self._window = None
        self._timings_applied = False
        try:
            from pywinauto import Application  # type: ignore

            self._Application = Application
            self._available = True
        except Exception:
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def cached_editor_valid(self, hwnd: int = 0) -> bool:
        global last_descendants_searches
        last_descendants_searches = 0
        handle = int(hwnd or self._pinned_hwnd or 0)
        if not handle or handle != int(self._pinned_hwnd or 0):
            return False
        if not self._pinned_id or self._pinned_id not in self._wrappers:
            return False
        return _hwnd_alive(handle)

    def clear_editor_cache(self) -> None:
        self._pinned_hwnd = 0
        self._pinned_id = ""

    def pin_editor(self, element_id: str, hwnd: int = 0) -> None:
        eid = str(element_id or "")
        if not eid or eid not in self._wrappers:
            return
        self._pinned_id = eid
        self._pinned_hwnd = int(hwnd or self._connected_hwnd or 0)

    def query_editors(self, *, max_elements: int = 8, hwnd: int = 0) -> List[UIElement]:
        global last_descendants_searches
        handle = int(hwnd or 0)
        if self.cached_editor_valid(handle):
            last_descendants_searches = 0
            cached = self._element_from_wrapper(
                self._wrappers[self._pinned_id],
                self._pinned_id,
                role="textbox",
            )
            return [cached] if cached is not None else []
        return self.list_elements(
            max_elements=max_elements,
            roles=("edit", "document", "textbox", "input"),
            hwnd=handle,
        )

    def read_value(self, element: UIElement) -> str:
        if element is None or element.is_password:
            return ""
        ctrl = self._wrappers.get(element.id)
        if ctrl is None and self._pinned_id:
            ctrl = self._wrappers.get(self._pinned_id)
        if ctrl is None:
            return str(element.value or element.text or "")
        try:
            return str(ctrl.get_value() or "")
        except Exception:
            try:
                return str(ctrl.window_text() or "")
            except Exception:
                return str(element.value or element.text or "")

    def _apply_fast_timings(self) -> None:
        if self._timings_applied:
            return
        self._timings_applied = True
        try:
            from pywinauto.timings import Timings  # type: ignore

            Timings.window_find_timeout = 0.25
            Timings.exists_timeout = 0.08
            Timings.child_window_timeout = 0.25
            Timings.app_start_timeout = 1.0
            Timings.after_click_wait = 0.01
            Timings.after_sendkeys_key_wait = 0.01
        except Exception:
            pass

    def _connect_window(self, hwnd: int):
        self._apply_fast_timings()
        handle = int(hwnd or 0) or _foreground_hwnd()
        if not handle or self._Application is None:
            return None
        if self._window is not None and int(self._connected_hwnd or 0) == handle:
            return self._window
        try:
            app = self._Application(backend="uia").connect(handle=handle, timeout=0.25)
            window = app.window(handle=handle)
            self._window = window
            self._connected_hwnd = handle
            return window
        except Exception:
            self._window = None
            self._connected_hwnd = 0
            return None

    def _element_from_wrapper(self, ctrl, eid: str, *, role: str = "textbox") -> Optional[UIElement]:
        try:
            info = ctrl.element_info
            name = (getattr(info, "name", None) or "").strip()
            ctrl_type = str(getattr(info, "control_type", "") or "").lower()
            rect = ctrl.rectangle()
            if rect.width() <= 0 or rect.height() <= 0:
                return None
            is_password = "password" in ctrl_type or "password" in name.lower()
            auto_id = str(getattr(info, "automation_id", "") or "")
            self._wrappers[eid] = ctrl
            return UIElement(
                id=eid,
                role=role,
                text="" if is_password else name,
                bounds=Bounds(
                    int(rect.left),
                    int(rect.top),
                    int(rect.width()),
                    int(rect.height()),
                ),
                source="uia",
                confidence=0.92,
                is_password=is_password,
                automation_id=auto_id,
                value="",
                actions=["focus"],
                enabled=bool(getattr(info, "enabled", True)),
                visible=True,
            )
        except Exception:
            return None

    def _bfs_editors(self, window, *, max_elements: int = 8, max_depth: int = 3):
        from collections import deque

        found = []
        try:
            queue = deque([(window, 0)])
        except Exception:
            return found
        seen = 0
        while queue and len(found) < max_elements and seen < 80:
            ctrl, depth = queue.popleft()
            if depth >= max_depth:
                continue
            try:
                children = ctrl.children()
            except Exception:
                continue
            for child in children:
                seen += 1
                try:
                    ctype = str(getattr(child.element_info, "control_type", "") or "")
                except Exception:
                    ctype = ""
                if ctype in ("Edit", "Document"):
                    found.append(child)
                    if len(found) >= max_elements:
                        break
                if depth + 1 < max_depth:
                    queue.append((child, depth + 1))
        return found

    def list_elements(
        self, *, max_elements: int = 80, roles: Optional[tuple] = None, hwnd: int = 0
    ) -> List[UIElement]:
        global _DESCENDANTS_SEARCHES, last_descendants_searches
        if not self._available:
            last_descendants_searches = 0
            return []
        t0 = time.perf_counter()
        elements: List[UIElement] = []
        wanted_roles = {str(r).lower() for r in (roles or ()) if str(r).strip()}
        editor_only = bool(wanted_roles) and wanted_roles <= {"edit", "document", "textbox", "input"}
        handle = int(hwnd or 0) or _foreground_hwnd()
        if editor_only and self.cached_editor_valid(handle):
            last_descendants_searches = 0
            cached = self._element_from_wrapper(
                self._wrappers[self._pinned_id],
                self._pinned_id,
                role="textbox",
            )
            return [cached] if cached is not None else []
        try:
            window = self._connect_window(handle)
            if window is None:
                print("[VISION] UIA connect skipped — no hwnd")
                last_descendants_searches = 0
                return []
            last_descendants_searches = 1
            _DESCENDANTS_SEARCHES += 1
            counters = {"button": 0, "textbox": 0, "link": 0, "other": 0}
            controls = []
            if editor_only:
                controls = self._bfs_editors(window, max_elements=max_elements, max_depth=3)
            else:
                try:
                    controls = list(window.children())
                except Exception:
                    controls = []
            for ctrl in controls:
                if len(elements) >= max_elements:
                    break
                try:
                    info = ctrl.element_info
                    name = (getattr(info, "name", None) or "").strip()
                    ctrl_type = str(getattr(info, "control_type", "") or "").lower()
                    rect = ctrl.rectangle()
                    if rect.width() <= 0 or rect.height() <= 0:
                        continue
                    role = "other"
                    if "button" in ctrl_type:
                        role = "button"
                        counters["button"] += 1
                        eid = f"button_{counters['button']}"
                    elif "edit" in ctrl_type or "document" in ctrl_type:
                        role = "textbox"
                        counters["textbox"] += 1
                        eid = f"input_{counters['textbox']}"
                    elif "hyperlink" in ctrl_type or "link" in ctrl_type:
                        role = "link"
                        counters["link"] += 1
                        eid = f"link_{counters['link']}"
                    else:
                        if editor_only or not name:
                            continue
                        counters["other"] += 1
                        eid = f"el_{counters['other']}"
                        role = ctrl_type or "element"

                    if wanted_roles and role not in wanted_roles and not (
                        role == "textbox" and (wanted_roles & {"edit", "document", "input"})
                    ):
                        continue

                    is_password = "password" in ctrl_type or "password" in name.lower()
                    auto_id = str(getattr(info, "automation_id", "") or "")
                    # Do not call get_value() during listing — ValuePattern can block.
                    self._wrappers[eid] = ctrl
                    elements.append(
                        UIElement(
                            id=eid,
                            role=role,
                            text="" if is_password else name,
                            bounds=Bounds(
                                int(rect.left),
                                int(rect.top),
                                int(rect.width()),
                                int(rect.height()),
                            ),
                            source="uia",
                            confidence=0.92,
                            is_password=is_password,
                            automation_id=auto_id,
                            value="",
                            actions=["focus"],
                            enabled=bool(getattr(info, "enabled", True)),
                            visible=True,
                        )
                    )
                except Exception:
                    continue
            if editor_only and elements:
                self.pin_editor(elements[0].id, handle)
        except Exception as exc:
            print(f"[VISION] UIA list failed: {exc}")
            last_descendants_searches = 0
            return []

        print(
            f"[VISION] UIA elements={len(elements)} "
            f"{(time.perf_counter() - t0) * 1000:.0f}ms"
        )
        return elements

    def click(self, element: UIElement) -> bool:
        ctrl = self._wrappers.get(element.id)
        if ctrl is not None:
            try:
                ctrl.invoke()
                return True
            except Exception:
                try:
                    ctrl.click_input()
                    return True
                except Exception:
                    pass
        return PyAutoGuiUIBackend().click(element)

    def double_click(self, element: UIElement) -> bool:
        ctrl = self._wrappers.get(element.id)
        if ctrl is not None:
            try:
                ctrl.double_click_input()
                return True
            except Exception:
                pass
        import pyautogui

        x, y = element.bounds.center
        pyautogui.doubleClick(x, y)
        return True

    def right_click(self, element: UIElement) -> bool:
        ctrl = self._wrappers.get(element.id)
        if ctrl is not None:
            try:
                ctrl.right_click_input()
                return True
            except Exception:
                pass
        import pyautogui

        x, y = element.bounds.center
        pyautogui.rightClick(x, y)
        return True

    def type_text(self, element: UIElement, text: str) -> bool:
        ctrl = self._wrappers.get(element.id)
        if ctrl is not None:
            try:
                ctrl.set_focus()
            except Exception:
                pass
            try:
                ctrl.type_keys(text, with_spaces=True, pause=0.02)
                return True
            except Exception:
                try:
                    ctrl.set_edit_text(text)
                    return True
                except Exception:
                    pass
        return PyAutoGuiUIBackend().type_text(element, text)

    def press_key(self, keys: str) -> bool:
        return PyAutoGuiUIBackend().press_key(keys)

    def scroll(self, *, direction: str, amount: int = 3) -> bool:
        return PyAutoGuiUIBackend().scroll(direction=direction, amount=amount)


def create_default_ui_backend() -> UIBackend:
    uia = WindowsUIABackend()
    if uia.available:
        return uia
    pag = PyAutoGuiUIBackend()
    if pag.available:
        print("[VISION] UIA unavailable — pyautogui action backend only")
        return pag
    print("[VISION] UI backend unavailable — using fake")
    return FakeUIBackend()
