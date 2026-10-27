# ==========================
# ELIAN — Playwright worker thread (Phase 13 Stage D)
# ==========================
"""One thread owns Playwright sync API. ToolRegistry still owns public invoke."""

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


def _pool() -> ThreadPoolExecutor:
    global _POOL
    with _LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="elian-browser",
                initializer=_initializer,
            )
        return _POOL


def browser_worker_thread_id() -> int:
    return int(_THREAD_ID or 0)


def browser_worker_busy() -> bool:
    return _BUSY.is_set()


def wait_browser_idle(timeout: float = 30.0) -> bool:
    return _IDLE.wait(timeout=max(0.1, float(timeout or 0)))


def submit_browser_job(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
    def _run():
        _IDLE.clear()
        _BUSY.set()
        try:
            return fn(*args, **kwargs)
        finally:
            _BUSY.clear()
            _IDLE.set()

    return _pool().submit(_run)
