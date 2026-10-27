# ==========================
# ELIAN — persistent ComputerAgent UI / COM worker
# ==========================
"""One thread, one COM apartment. ToolRegistry still owns the public invoke."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional

_POOL: Optional[ThreadPoolExecutor] = None
_LOCK = threading.Lock()
_THREAD_ID = 0
_BUSY = threading.Event()
_IDLE = threading.Event()
_IDLE.set()


def _initializer() -> None:
    global _THREAD_ID
    _THREAD_ID = threading.get_ident()
    try:
        import pythoncom  # type: ignore

        pythoncom.CoInitializeEx(0)
    except Exception:
        pass
    try:
        from pywinauto.timings import Timings  # type: ignore

        Timings.window_find_timeout = 0.35
        Timings.exists_timeout = 0.12
        Timings.child_window_timeout = 0.35
        Timings.app_start_timeout = 1.0
        Timings.after_click_wait = 0.01
        Timings.after_sendkeys_key_wait = 0.01
    except Exception:
        pass


def get_computer_ui_pool() -> ThreadPoolExecutor:
    global _POOL
    with _LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(
                max_workers=1,
                initializer=_initializer,
                thread_name_prefix="elian-uia",
            )
        return _POOL


def is_computer_ui_thread() -> bool:
    return _THREAD_ID != 0 and threading.get_ident() == _THREAD_ID


def computer_worker_busy() -> bool:
    return _BUSY.is_set()


def wait_computer_idle(timeout: float = 30.0) -> bool:
    return _IDLE.wait(timeout=max(0.05, float(timeout or 0.05)))


def submit_computer_job(fn: Callable[..., Any], *args, **kwargs) -> Future:
    pool = get_computer_ui_pool()

    def wrapped():
        _IDLE.clear()
        _BUSY.set()
        try:
            return fn(*args, **kwargs)
        finally:
            _BUSY.clear()
            _IDLE.set()

    return pool.submit(wrapped)


def reset_computer_ui_worker_for_tests() -> None:
    """Keep the pool; just mark idle. Do not shutdown (COM stays warm)."""
    _BUSY.clear()
    _IDLE.set()
