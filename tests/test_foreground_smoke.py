# ==========================
# ELIAN — foreground-smoke interactive observer
# ==========================
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from core.foreground_smoke import (
    FAIL,
    PARTIAL,
    PASS,
    ForegroundSmokeController,
    classify_watcher,
    evaluate_foreground_smoke,
    process_family,
    run_interactive_session,
    wait_bounded,
)
from core.perception.foreground import FakeForegroundProvider, ForegroundInfo


def _info(hwnd, process, title, source="ctypes_user32"):
    return ForegroundInfo(
        hwnd=hwnd,
        process=process,
        title=title,
        source=source,
        ok=bool(hwnd),
        timestamp=time.time(),
        bounds={"x": 0, "y": 0, "width": 100, "height": 100},
    )


class _HookProvider:
    def __init__(self, *, hook_active=False, poll_active=False):
        self.hook_active = hook_active
        self.poll_active = poll_active
        self.started = False
        self.start_calls = 0
        self.stop_calls = 0
        self._on_change = None
        self._info = _info(111, "Cursor.exe", "Elian - Cursor")

    def current(self):
        return self._info

    def start(self, on_change):
        self.start_calls += 1
        self._on_change = on_change
        self.started = True

    def stop(self):
        self.stop_calls += 1
        self.started = False


class _DeadProvider:
    def current(self):
        return _info(111, "Cursor.exe", "Elian - Cursor")

    def start(self, on_change):
        return None

    def stop(self):
        return None


class ForegroundSmokeControllerTests(unittest.TestCase):
    def test_unchanged_hwnd_deduplicated(self):
        ctl = ForegroundSmokeController()
        ctl.seed_initial(_info(11, "chrome.exe", "Inbox - Google Chrome"))
        self.assertIsNone(ctl.note(_info(11, "chrome.exe", "Inbox - Google Chrome")))
        self.assertIsNone(ctl.note(_info(11, "chrome.exe", "other tab")))
        self.assertEqual(ctl.changes, [])
        self.assertEqual(ctl.emitted, 0)

    def test_changed_hwnd_emitted(self):
        ctl = ForegroundSmokeController()
        ctl.seed_initial(_info(11, "chrome.exe", "Inbox - Google Chrome"))
        sample = ctl.note(_info(22, "notepad.exe", "Bez tytułu — Notatnik"))
        self.assertIsNotNone(sample)
        self.assertEqual(sample.sequence, 1)
        self.assertEqual(sample.hwnd, 22)
        self.assertEqual(sample.process, "notepad.exe")

    def test_sequence_stored(self):
        ctl = ForegroundSmokeController()
        ctl.seed_initial(_info(11, "chrome.exe", "A"))
        ctl.note(_info(22, "notepad.exe", "B"))
        ctl.note(_info(33, "Cursor.exe", "C"))
        ctl.note(_info(11, "chrome.exe", "A"))
        self.assertEqual([s.hwnd for s in ctl.changes], [22, 33, 11])
        self.assertEqual(
            [process_family(s.process, s.title) for s in ctl.changes],
            ["notepad", "cursor", "chrome"],
        )


class ForegroundSmokeWatcherTests(unittest.TestCase):
    def test_hook_success(self):
        hook, poll, ok = classify_watcher(_HookProvider(hook_active=True, poll_active=True))
        self.assertEqual(hook, "running")
        self.assertEqual(poll, "not_needed")
        self.assertTrue(ok)

    def test_hook_fail_polling_fallback(self):
        hook, poll, ok = classify_watcher(_HookProvider(hook_active=False, poll_active=True))
        self.assertEqual(hook, "failed")
        self.assertEqual(poll, "fallback")
        self.assertTrue(ok)
        result, reason = evaluate_foreground_smoke(
            initial=_sample(),
            changes=[_sample(hwnd=2), _sample(hwnd=3), _sample(hwnd=4)],
            hook_status=hook,
            poll_status=poll,
            watcher_ok=ok,
        )
        self.assertEqual(result, PASS)

    def test_neither_available_fail(self):
        hook, poll, ok = classify_watcher(_DeadProvider())
        self.assertEqual(hook, "unavailable")
        self.assertEqual(poll, "unavailable")
        self.assertFalse(ok)
        result, reason = evaluate_foreground_smoke(
            initial=_sample(),
            changes=[_sample(hwnd=2), _sample(hwnd=3), _sample(hwnd=4)],
            hook_status=hook,
            poll_status=poll,
            watcher_ok=ok,
        )
        self.assertEqual(result, FAIL)
        self.assertEqual(reason, "watcher_and_poll_unavailable")


def _sample(hwnd=111, process="Cursor.exe", title="Elian - Cursor"):
    from core.foreground_smoke import ForegroundSample

    return ForegroundSample(
        sequence=0,
        hwnd=hwnd,
        process=process,
        title=title,
        bounds={},
        source="ctypes_user32",
        ok=True,
        timestamp=1.0,
    )


class ForegroundSmokeVerdictTests(unittest.TestCase):
    def test_fewer_than_three_changes_partial(self):
        result, reason = evaluate_foreground_smoke(
            initial=_sample(),
            changes=[_sample(hwnd=2), _sample(hwnd=3)],
            hook_status="running",
            poll_status="not_needed",
            watcher_ok=True,
        )
        self.assertEqual(result, PARTIAL)
        self.assertEqual(reason, "no_foreground_changes_observed")

    def test_no_changes_partial(self):
        result, reason = evaluate_foreground_smoke(
            initial=_sample(),
            changes=[],
            hook_status="running",
            poll_status="not_needed",
            watcher_ok=True,
        )
        self.assertEqual(result, PARTIAL)
        self.assertEqual(reason, "no_foreground_changes_observed")

    def test_three_changes_pass(self):
        result, reason = evaluate_foreground_smoke(
            initial=_sample(),
            changes=[_sample(hwnd=2), _sample(hwnd=3), _sample(hwnd=4)],
            hook_status="running",
            poll_status="not_needed",
            watcher_ok=True,
        )
        self.assertEqual(result, PASS)
        self.assertEqual(reason, "ok")


class ForegroundSmokeSessionTests(unittest.TestCase):
    def test_controller_waits_instead_of_immediate_exit(self):
        stop = threading.Event()
        t0 = time.monotonic()
        waited = wait_bounded(0.25, stop)
        elapsed = time.monotonic() - t0
        self.assertTrue(waited)
        self.assertGreaterEqual(elapsed, 0.20)

        fg = FakeForegroundProvider(hwnd=11, title="Elian - Cursor", process="Cursor.exe")
        t1 = time.monotonic()
        report = run_interactive_session(
            fg,
            duration_s=0.25,
            arm_enter=False,
            watcher_ready_s=0.05,
            on_unique=None,
            quiet=True,
        )
        elapsed = time.monotonic() - t1
        self.assertTrue(report.waited)
        self.assertGreaterEqual(elapsed, 0.20)
        self.assertFalse(fg.started)

    def test_session_starts_provider(self):
        provider = _HookProvider(hook_active=True)
        report = run_interactive_session(
            provider,
            duration_s=0.05,
            arm_enter=False,
            watcher_ready_s=0.01,
            on_unique=None,
            quiet=True,
        )
        self.assertGreaterEqual(provider.start_calls, 1)
        self.assertGreaterEqual(provider.stop_calls, 1)
        self.assertTrue(report.waited)

    def test_neither_available_fails_without_waiting(self):
        t0 = time.monotonic()
        report = run_interactive_session(
            _DeadProvider(),
            duration_s=20.0,
            arm_enter=False,
            watcher_ready_s=0.05,
            on_unique=None,
            quiet=True,
        )
        elapsed = time.monotonic() - t0
        self.assertEqual(report.result, FAIL)
        self.assertEqual(report.reason, "watcher_and_poll_unavailable")
        self.assertFalse(report.waited)
        self.assertLess(elapsed, 2.0)

    def test_no_side_effects(self):
        fg = FakeForegroundProvider(hwnd=11, title="Elian - Cursor", process="Cursor.exe")
        with patch("core.computer_agent.ComputerAgent") as agent:
            agent.side_effect = AssertionError("computer agent must not run")
            report = run_interactive_session(
                fg,
                duration_s=0.05,
                arm_enter=False,
                watcher_ready_s=0.01,
                on_unique=None,
                quiet=True,
            )
        self.assertEqual(report.side_effects, 0)
        agent.assert_not_called()

    def test_session_records_injected_changes(self):
        fg = FakeForegroundProvider(hwnd=11, title="Inbox - Google Chrome", process="chrome.exe")
        seen = []

        def _emit(sample):
            seen.append(sample)

        def _drive():
            time.sleep(0.05)
            fg.set(hwnd=11, title="Inbox - Google Chrome", process="chrome.exe")
            fg.set(hwnd=22, title="Bez tytułu — Notatnik", process="notepad.exe")
            fg.set(hwnd=33, title="Elian - Cursor", process="Cursor.exe")
            fg.set(hwnd=11, title="Inbox - Google Chrome", process="chrome.exe")

        threading.Thread(target=_drive, daemon=True).start()
        report = run_interactive_session(
            fg,
            duration_s=0.35,
            arm_enter=False,
            watcher_ready_s=0.01,
            on_unique=_emit,
            quiet=True,
        )
        self.assertGreaterEqual(len(report.changes), 3)
        self.assertEqual([s.hwnd for s in report.changes[:3]], [22, 33, 11])
        self.assertEqual(report.result, PASS)


if __name__ == "__main__":
    unittest.main()
