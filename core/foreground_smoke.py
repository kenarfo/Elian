# ==========================
# ELIAN — C.5.5 foreground smoke (read-only, no side effects)
# ==========================
"""Live mode is a bounded interactive observer. Mock stays offline."""
from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LIVE_DURATION_S = 40.0
MIN_UNIQUE_CHANGES = 3
WATCHER_READY_S = 1.0

PASS = "PASS"
PARTIAL = "PARTIAL"
FAIL = "FAIL"


@dataclass
class ForegroundSample:
    sequence: int
    hwnd: int
    process: str
    title: str
    bounds: dict
    source: str
    ok: bool
    timestamp: float


@dataclass
class ForegroundSmokeReport:
    result: str
    reason: str = ""
    initial: Optional[ForegroundSample] = None
    changes: List[ForegroundSample] = field(default_factory=list)
    hook_status: str = "unavailable"
    poll_status: str = "unavailable"
    watcher_ok: bool = False
    waited: bool = False
    duration_s: float = 0.0
    side_effects: int = 0

    @property
    def unique_changes(self) -> int:
        return len(self.changes)


class ForegroundSmokeController:
    """Dedupes hwnd, stores unique switches, never talks to ComputerAgent/HA."""

    def __init__(self):
        self.last_hwnd = 0
        self.initial: Optional[ForegroundSample] = None
        self.changes: List[ForegroundSample] = []
        self.emitted = 0
        self.side_effects = 0

    def seed_initial(self, info) -> ForegroundSample:
        sample = _sample_from_info(info, sequence=0)
        self.initial = sample
        self.last_hwnd = int(sample.hwnd or 0)
        return sample

    def note(self, info) -> Optional[ForegroundSample]:
        sample = _sample_from_info(info, sequence=len(self.changes) + 1)
        hwnd = int(sample.hwnd or 0)
        if not hwnd or hwnd == self.last_hwnd:
            return None
        self.last_hwnd = hwnd
        self.changes.append(sample)
        self.emitted += 1
        return sample


def _sample_from_info(info, *, sequence: int) -> ForegroundSample:
    if isinstance(info, dict):
        hwnd = int(info.get("hwnd") or 0)
        process = str(info.get("process") or "")
        title = str(info.get("title") or "")
        bounds = dict(info.get("bounds") or {})
        source = str(info.get("source") or "")
        ok = bool(info.get("ok", hwnd))
        ts = float(info.get("timestamp") or time.time())
    else:
        hwnd = int(getattr(info, "hwnd", 0) or 0)
        process = str(getattr(info, "process", "") or "")
        title = str(getattr(info, "title", "") or "")
        bounds = dict(getattr(info, "bounds", None) or {})
        source = str(getattr(info, "source", "") or "")
        ok = bool(getattr(info, "ok", hwnd))
        ts = float(getattr(info, "timestamp", 0) or time.time())
    return ForegroundSample(
        sequence=sequence,
        hwnd=hwnd,
        process=process,
        title=title,
        bounds=bounds,
        source=source,
        ok=ok,
        timestamp=ts,
    )


def process_family(process: str, title: str = "") -> str:
    blob = f"{process} {title}".lower()
    if "chrome" in blob:
        return "chrome"
    if "notepad" in blob or "notatnik" in blob:
        return "notepad"
    if "cursor" in blob:
        return "cursor"
    name = str(process or "").lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name


def classify_watcher(provider) -> Tuple[str, str, bool]:
    """Return (hook_status, poll_status, ok). Thread-alive alone is not a live mechanism."""
    hook_active = bool(getattr(provider, "hook_active", False))
    poll_active = bool(getattr(provider, "poll_active", False))
    started = bool(getattr(provider, "started", False))
    if hook_active:
        return "running", "not_needed", True
    if poll_active:
        return "failed", "fallback", True
    if started and not hasattr(provider, "hook_active"):
        return "unavailable", "running", True
    return "unavailable", "unavailable", False


def evaluate_foreground_smoke(
    *,
    initial: Optional[ForegroundSample],
    changes: List[ForegroundSample],
    hook_status: str,
    poll_status: str,
    watcher_ok: bool,
    min_changes: int = MIN_UNIQUE_CHANGES,
) -> tuple[str, str]:
    if initial is None or not int(initial.hwnd or 0) or not initial.ok:
        return FAIL, "initial_hwnd_invalid"
    if not (initial.process or initial.title):
        return FAIL, "initial_process_title_missing"
    live = hook_status == "running" or poll_status in ("running", "fallback")
    if not watcher_ok or not live:
        return FAIL, "watcher_and_poll_unavailable"
    if len(changes) < int(min_changes):
        return PARTIAL, "no_foreground_changes_observed"
    return PASS, "ok"


def wait_for_watcher(provider, *, timeout_s: float = WATCHER_READY_S, sleep: Callable[[float], None] = time.sleep) -> None:
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while time.monotonic() < deadline:
        _, _, ok = classify_watcher(provider)
        if ok:
            return
        sleep(0.05)


def wait_bounded(
    duration_s: float,
    stop_event: threading.Event,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Block until duration elapses or stop_event. Returns True if waited (did not return instantly)."""
    seconds = max(0.0, float(duration_s))
    if seconds <= 0:
        return False
    end = time.monotonic() + seconds
    waited = False
    while time.monotonic() < end and not stop_event.is_set():
        waited = True
        remaining = end - time.monotonic()
        sleep(min(0.1, max(0.0, remaining)))
    return waited


def print_foreground_change(sample: ForegroundSample) -> None:
    print("[FOREGROUND CHANGE]")
    print(f"sequence={sample.sequence}")
    print(f"source={sample.source}")
    print(f"hwnd={sample.hwnd}")
    print(f"IsWindow={1 if sample.ok and sample.hwnd else 0}")
    print(f"process={sample.process}")
    print(f"title={sample.title}")
    print(f"bounds={sample.bounds}")
    print(f"timestamp={sample.timestamp:.3f}")


def _arm_enter(stop_event: threading.Event) -> None:
    try:
        if not sys.stdin or not sys.stdin.isatty():
            return
    except Exception:
        return

    def _wait():
        try:
            sys.stdin.readline()
            stop_event.set()
        except Exception:
            pass

    threading.Thread(target=_wait, name="elian-fg-smoke-enter", daemon=True).start()


def run_interactive_session(
    provider,
    *,
    duration_s: float = LIVE_DURATION_S,
    min_changes: int = MIN_UNIQUE_CHANGES,
    sleep: Callable[[float], None] = time.sleep,
    arm_enter: bool = True,
    initial_info=None,
    watcher_ready_s: float = WATCHER_READY_S,
    on_unique: Optional[Callable[[ForegroundSample], None]] = print_foreground_change,
    quiet: bool = False,
) -> ForegroundSmokeReport:
    from core.perception.foreground import read_foreground_window

    controller = ForegroundSmokeController()
    stop_event = threading.Event()
    info = initial_info if initial_info is not None else (
        provider.current() if hasattr(provider, "current") else read_foreground_window()
    )
    initial = controller.seed_initial(info)

    def _on_change(changed):
        sample = controller.note(changed)
        if sample is not None and on_unique is not None:
            on_unique(sample)

    provider.start(_on_change)
    wait_for_watcher(provider, timeout_s=watcher_ready_s, sleep=sleep)
    hook_status, poll_status, watcher_ok = classify_watcher(provider)
    if not quiet:
        print(f"hook_status={hook_status}")
        print(f"poll_status={poll_status}")

    waited = False
    try:
        if not watcher_ok:
            result, reason = evaluate_foreground_smoke(
                initial=initial,
                changes=controller.changes,
                hook_status=hook_status,
                poll_status=poll_status,
                watcher_ok=watcher_ok,
                min_changes=min_changes,
            )
            return ForegroundSmokeReport(
                result=result,
                reason=reason,
                initial=initial,
                changes=list(controller.changes),
                hook_status=hook_status,
                poll_status=poll_status,
                watcher_ok=watcher_ok,
                waited=False,
                duration_s=float(duration_s),
                side_effects=controller.side_effects,
            )
        if arm_enter:
            _arm_enter(stop_event)
        waited = wait_bounded(duration_s, stop_event, sleep=sleep)
    except KeyboardInterrupt:
        stop_event.set()
        waited = True
        print("Stopped early (Ctrl+C).")
    finally:
        try:
            provider.stop()
        except Exception:
            pass

    result, reason = evaluate_foreground_smoke(
        initial=initial,
        changes=controller.changes,
        hook_status=hook_status,
        poll_status=poll_status,
        watcher_ok=watcher_ok,
        min_changes=min_changes,
    )
    return ForegroundSmokeReport(
        result=result,
        reason=reason,
        initial=initial,
        changes=list(controller.changes),
        hook_status=hook_status,
        poll_status=poll_status,
        watcher_ok=watcher_ok,
        waited=waited,
        duration_s=float(duration_s),
        side_effects=controller.side_effects,
    )


def _print_initial(sample: ForegroundSample, *, source: str = "") -> None:
    print("Initial foreground:")
    print("provider_chain=ctypes_user32>pywin32>windows_observation>uia_active")
    if source:
        print(f"resolved_source={source}")
    print(f"hwnd={sample.hwnd}")
    print(f"IsWindow={1 if sample.ok and sample.hwnd else 0}")
    print(f"process={sample.process}")
    print(f"title={sample.title}")
    print(f"bounds={sample.bounds}")


def run_foreground_smoke(*, mock: bool = False, duration_s: float = LIVE_DURATION_S) -> int:
    from core.perception.foreground import (
        live_foreground_provider,
        read_foreground_window,
        resolve_foreground_hwnd,
    )

    print("=== ELIAN FOREGROUND SMOKE ===")
    print(f"mode={'mock' if mock else 'live-readonly'}")

    if mock:
        return _run_mock()

    _hwnd, source = resolve_foreground_hwnd()
    info = read_foreground_window()
    provider = live_foreground_provider()
    sample = _sample_from_info(info, sequence=0)
    _print_initial(sample, source=source)
    print()
    print(f"Monitoring foreground changes for {int(duration_s)} seconds...")
    print("Switch: Chrome → Notepad → Cursor → Chrome")
    print("Press Enter or Ctrl+C to finish early.")
    print()

    report = run_interactive_session(
        provider,
        duration_s=duration_s,
        initial_info=info,
    )
    _print_verdict(report)
    if report.result == PASS:
        return 0
    return 1


def _run_mock() -> int:
    from core.perception.foreground import FakeForegroundProvider

    failed = 0

    def _ok(name, cond, detail=""):
        nonlocal failed
        if cond:
            print(f"  PASS  {name}")
        else:
            failed += 1
            print(f"  FAIL  {name}: {detail}")

    fg = FakeForegroundProvider(hwnd=11, title="Inbox - Google Chrome", process="chrome.exe")
    controller = ForegroundSmokeController()
    initial = controller.seed_initial(fg.current())
    before_hook, before_poll, before_ok = classify_watcher(fg)
    _ok("watcher_idle_before_start", not before_ok, f"{before_hook}/{before_poll}")

    def _on_change(info):
        sample = controller.note(info)
        if sample is not None:
            print_foreground_change(sample)

    fg.start(_on_change)
    _ok("fake_provider_started", fg.started)
    hook_status, poll_status, watcher_ok = classify_watcher(fg)
    _ok("mock_watcher_active", watcher_ok, f"hook={hook_status} poll={poll_status}")
    _ok("watcher_started_transition", (not before_ok) and watcher_ok)
    sequence = (
        (11, "Inbox - Google Chrome", "chrome.exe", "Chrome"),
        (22, "Bez tytułu — Notatnik", "notepad.exe", "Notepad"),
        (33, "Elian - Cursor", "Cursor.exe", "Cursor"),
        (11, "Inbox - Google Chrome", "chrome.exe", "Chrome"),
    )
    for hwnd, title, process, label in sequence:
        fg.set(hwnd=hwnd, title=title, process=process)
        _ok(f"switch_{label.lower()}_applied", int(fg.current().hwnd) == hwnd, str(fg.current().hwnd))
    fg.stop()
    _ok("unique_changes_ge_3", len(controller.changes) >= 3, str(len(controller.changes)))
    families = [process_family(s.process, s.title) for s in controller.changes]
    _ok(
        "mock_sequence_notepad_cursor_chrome",
        "notepad" in families and "cursor" in families and families[-1] == "chrome",
        str(families),
    )
    result, reason = evaluate_foreground_smoke(
        initial=initial,
        changes=controller.changes,
        hook_status=hook_status,
        poll_status=poll_status,
        watcher_ok=watcher_ok,
    )
    _ok("mock_verdict_pass", result == PASS, f"{result} {reason}")
    print(f"hook_status={hook_status}")
    print(f"poll_status={poll_status}")
    print(f"foreground-smoke result={result} changes={len(controller.changes)} failed={failed}")
    return 0 if failed == 0 and result == PASS else 1


def _print_verdict(report: ForegroundSmokeReport) -> None:
    print()
    print(f"hook_status={report.hook_status}")
    print(f"poll_status={report.poll_status}")
    print(f"unique_changes={report.unique_changes}")
    print(f"waited={1 if report.waited else 0}")
    if report.result == PARTIAL:
        print("foreground-smoke result=PARTIAL")
        print(f"reason={report.reason}")
    elif report.result == FAIL:
        print("foreground-smoke result=FAIL")
        print(f"reason={report.reason}")
    else:
        print("foreground-smoke result=PASS")
    families = [process_family(s.process, s.title) for s in report.changes]
    if families:
        print(f"observed_families={families}")
