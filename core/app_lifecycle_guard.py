# ==========================
# ELIAN — app lifecycle guard (automated tests / diagnostics only)
# ==========================
"""Protect pre-existing user apps (esp. Spotify) from suite teardown.

Voice "zamknij Spotify" remains unrestricted — this guard activates only when
ELIAN_PROTECT_USER_APPS=1 (unittest / diagnostics harness).
"""

from __future__ import annotations

import os
import threading
from typing import Dict, Optional, Set

_LOCK = threading.RLock()
_ENABLED = False
_PREEXISTING: Dict[str, Set[int]] = {}
_TEST_OWNED: Dict[str, Set[int]] = {}
_MEDIA_PLAYING_APPS: Set[str] = set()

# Apps never closed by automated tests unless this process suite started them.
_PROTECTED_USER_APPS = frozenset(
    {
        "spotify",
        "spotify.exe",
    }
)


def protect_user_apps_enabled() -> bool:
    if _ENABLED:
        return True
    flag = str(os.environ.get("ELIAN_PROTECT_USER_APPS") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def enable_protect_user_apps(*, snapshot: bool = True) -> None:
    """Turn on automated-suite protection (idempotent)."""
    global _ENABLED
    with _LOCK:
        _ENABLED = True
        os.environ["ELIAN_PROTECT_USER_APPS"] = "1"
        if snapshot:
            refresh_preexisting_snapshot()


def disable_protect_user_apps() -> None:
    global _ENABLED
    with _LOCK:
        _ENABLED = False
        os.environ.pop("ELIAN_PROTECT_USER_APPS", None)
        _PREEXISTING.clear()
        _TEST_OWNED.clear()
        _MEDIA_PLAYING_APPS.clear()


def _norm_app(name: str) -> str:
    raw = str(name or "").strip().lower()
    if raw.endswith(".exe"):
        raw = raw[:-4]
    return raw


def _is_protected_app(name: str) -> bool:
    key = _norm_app(name)
    if not key:
        return False
    if key in _PROTECTED_USER_APPS:
        return True
    return any(key == item or key.endswith(f" {item}") for item in _PROTECTED_USER_APPS)


def _pids_for_app(name: str) -> Set[int]:
    key = _norm_app(name)
    found: Set[int] = set()
    if not key:
        return found
    try:
        import psutil
    except Exception:
        return found
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pname = _norm_app(str((proc.info or {}).get("name") or ""))
            if pname == key:
                found.add(int(proc.info["pid"]))
        except Exception:
            continue
    return found


def refresh_preexisting_snapshot() -> Dict[str, Set[int]]:
    """Record currently running protected apps as USER_OWNED (never disposable)."""
    with _LOCK:
        for app in _PROTECTED_USER_APPS:
            if app.endswith(".exe"):
                continue
            pids = _pids_for_app(app)
            if pids:
                _PREEXISTING[app] = set(pids)
        return {k: set(v) for k, v in _PREEXISTING.items()}


def mark_media_playing(app: str, playing: bool = True) -> None:
    key = _norm_app(app)
    with _LOCK:
        if playing:
            _MEDIA_PLAYING_APPS.add(key)
        else:
            _MEDIA_PLAYING_APPS.discard(key)


def note_test_owned_launch(app: str, pids: Optional[Set[int]] = None) -> None:
    """Record that this suite started the app (may close only those PIDs)."""
    if not protect_user_apps_enabled():
        return
    key = _norm_app(app)
    if not _is_protected_app(key):
        return
    with _LOCK:
        owned = set(pids or ()) or (_pids_for_app(key) - set(_PREEXISTING.get(key) or ()))
        if owned:
            _TEST_OWNED.setdefault(key, set()).update(owned)


def should_block_close(
    app: str,
    *,
    explicit_user_request: bool = False,
    media_playing: Optional[bool] = None,
) -> tuple[bool, str]:
    """Return (block, reason). User voice close must pass explicit_user_request=True."""
    if explicit_user_request or not protect_user_apps_enabled():
        return False, ""
    key = _norm_app(app)
    if not _is_protected_app(key):
        return False, ""
    with _LOCK:
        preexisting = set(_PREEXISTING.get(key) or ())
        owned = set(_TEST_OWNED.get(key) or ())
        playing = (media_playing is True) or key in _MEDIA_PLAYING_APPS
        live = _pids_for_app(key)
        # Caller/suite marked media playing — never dispose, even if PID probe flakes.
        if playing:
            return True, "USER_OWNED_PLAYING"
        # Only close PIDs this suite can prove it started.
        disposable = live & owned
        if live and not disposable:
            return True, "USER_OWNED_PROCESS"
        if preexisting and live and not (live - preexisting):
            # All live PIDs were already running before the suite.
            if not (live & owned):
                return True, "USER_OWNED_PROCESS"
        if live and not owned:
            return True, "USER_OWNED_UNCERTAIN"
    return False, ""


def filter_close_pids(app: str, candidate_pids: Set[int]) -> Set[int]:
    """When guarding, keep only test-owned PIDs."""
    if not protect_user_apps_enabled():
        return set(candidate_pids)
    key = _norm_app(app)
    if not _is_protected_app(key):
        return set(candidate_pids)
    with _LOCK:
        owned = set(_TEST_OWNED.get(key) or ())
        preexisting = set(_PREEXISTING.get(key) or ())
    allowed = set(candidate_pids) & owned
    # Never touch pre-existing PIDs.
    allowed -= preexisting
    return allowed


def snapshot_status(app: str = "spotify") -> Dict[str, object]:
    key = _norm_app(app)
    live = _pids_for_app(key)
    with _LOCK:
        return {
            "app": key,
            "enabled": protect_user_apps_enabled(),
            "live_pids": sorted(live),
            "preexisting_pids": sorted(_PREEXISTING.get(key) or ()),
            "test_owned_pids": sorted(_TEST_OWNED.get(key) or ()),
            "media_playing": key in _MEDIA_PLAYING_APPS,
        }
