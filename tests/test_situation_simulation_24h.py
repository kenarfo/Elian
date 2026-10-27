# ==========================
# ELIAN — 24h synthetic proactive timeline simulation
# ==========================
"""Drive a day of batteries, calendar, reminders, apps, idle, HA, phone.

Reports speak / suppressed / deferred counts. Offline FakeClock — no mic/HA.
"""

from __future__ import annotations

import unittest
from datetime import datetime

from core.clock import FakeClock
from core.events import (
    BATTERY_CHARGING,
    BATTERY_LOW,
    CALENDAR_UPCOMING,
    CPU_HIGH_SUSTAINED,
    CPU_SAMPLE,
    DISK_LOW,
    HA_UNAVAILABLE,
    HEARTBEAT,
    HOME_ARRIVAL,
    LEAVING_HOME,
    NETWORK_LOST,
    NETWORK_RESTORED,
    REMINDER_DUE,
    WORLD_STATE_REFRESH,
    Event,
    Priority,
)
from core.proactive import ProactiveManager
from core.proactive_policy import reset_policy_for_tests
from core.situation_engine import reset_situation_engine_for_tests
from core.world_state import note_calendar, note_computer, note_conversation, reset_world_state


class Timeline24hSimulation(unittest.TestCase):
    def setUp(self):
        reset_policy_for_tests()
        reset_world_state()
        reset_situation_engine_for_tests()
        self.clock = FakeClock(start=1_700_000_000.0)
        self.clock.set_local(datetime(2026, 6, 15, 7, 0, 0))
        self.spoken = []
        self.pm = ProactiveManager(
            clock=self.clock,
            speak_fn=lambda m: self.spoken.append(m),
            busy_check=lambda: False,
            preference_get=lambda k, d=None: None,
            max_notifications_per_window=8,
            rate_window_seconds=3600,
        )

    def tearDown(self):
        reset_policy_for_tests()
        reset_world_state()
        reset_situation_engine_for_tests()

    def _evt(self, typ, **kwargs):
        kwargs.setdefault("source", "sim")
        kwargs.setdefault("timestamp", self.clock.time())
        kwargs.setdefault("ttl_seconds", 1800)
        return Event(type=typ, **kwargs)

    def _advance(self, seconds: float) -> None:
        self.clock.advance(seconds)
        self.pm.try_deliver()

    def test_24h_no_spam_valuable_speak(self):
        decisions = []

        def handle(evt):
            d = self.pm.handle_event(evt)
            decisions.append(d)
            return d

        # Morning noise — heartbeats / refresh / CPU blips
        for i in range(40):
            handle(self._evt(HEARTBEAT, priority=Priority.LOW, dedup_key=f"hb:{i}"))
            handle(
                self._evt(
                    WORLD_STATE_REFRESH,
                    priority=Priority.LOW,
                    dedup_key=f"ws:{i}",
                )
            )
            handle(
                self._evt(
                    CPU_SAMPLE,
                    data={"percent": 90 + (i % 5)},
                    priority=Priority.LOW,
                    dedup_key=f"cpu_s:{i}",
                )
            )
            self._advance(60)

        # Useful: calendar upcoming alone
        handle(
            self._evt(
                CALENDAR_UPCOMING,
                data={"title": "Poranny sync", "minutes": 15},
                priority=Priority.NORMAL,
                dedup_key="cal:morning",
            )
        )
        self._advance(900)

        # Battery alone mid-morning
        handle(
            self._evt(
                BATTERY_LOW,
                data={"percent": 18, "plugged": False},
                priority=Priority.HIGH,
                dedup_key="bat:morning",
            )
        )
        self._advance(600)
        handle(
            self._evt(
                BATTERY_CHARGING,
                priority=Priority.NORMAL,
                dedup_key="bat:charge1",
            )
        )
        self._advance(1800)

        # Correlated: low battery + meeting + active use
        note_calendar({"next": "Client call", "minutes": 25})
        handle(
            self._evt(
                BATTERY_LOW,
                data={"percent": 15, "plugged": False, "active_use": True},
                priority=Priority.HIGH,
                dedup_key="bat:corr",
            )
        )
        # Duplicate spam attempts
        for i in range(5):
            handle(
                self._evt(
                    BATTERY_LOW,
                    data={"percent": 14 - i, "plugged": False},
                    priority=Priority.HIGH,
                    dedup_key=f"bat:spam:{i}",
                )
            )
            self._advance(30)

        self._advance(1200)
        handle(
            self._evt(
                BATTERY_CHARGING,
                priority=Priority.NORMAL,
                dedup_key="bat:charge2",
            )
        )

        # Reminder while "gaming"
        note_computer({"process": "game.exe", "fullscreen": True, "gaming": True})
        handle(
            self._evt(
                REMINDER_DUE,
                data={"text": "leki", "fullscreen": True, "gaming": True},
                priority=Priority.HIGH,
                dedup_key="rem:game",
            )
        )
        self._advance(60)
        # Clear gaming — deliver deferred
        note_computer({"process": "explorer.exe", "fullscreen": False, "gaming": False})
        self.pm.try_deliver()
        self._advance(300)

        # Phone connect/disconnect style events
        handle(
            self._evt(
                BATTERY_LOW,
                data={
                    "percent": 12,
                    "device": "phone",
                    "plugged": False,
                    "leaving": True,
                },
                priority=Priority.HIGH,
                dedup_key="phone:leave",
            )
        )
        handle(self._evt(LEAVING_HOME, priority=Priority.NORMAL, dedup_key="leave:1"))
        self._advance(600)

        # HA blip + restore noise
        handle(
            self._evt(
                HA_UNAVAILABLE,
                priority=Priority.NORMAL,
                dedup_key="ha:1",
            )
        )
        self._advance(120)
        # App usage chatter (window changes are silent)
        for i in range(20):
            handle(
                self._evt(
                    "ACTIVE_WINDOW_CHANGED",
                    data={"title": f"App{i}", "process": f"app{i}.exe"},
                    priority=Priority.LOW,
                    dedup_key=f"win:{i}",
                )
            )
            self._advance(45)

        # Idle afternoon + disk low once
        handle(
            self._evt(
                DISK_LOW,
                data={"free_gb": 4.2},
                priority=Priority.NORMAL,
                dedup_key="disk:1",
            )
        )
        self._advance(3600)

        # Short CPU spike vs sustained
        handle(
            self._evt(
                CPU_HIGH_SUSTAINED,
                data={"percent": 96, "sustained_seconds": 30},
                priority=Priority.NORMAL,
                dedup_key="cpu:short",
            )
        )
        self._advance(120)
        handle(
            self._evt(
                CPU_HIGH_SUSTAINED,
                data={"percent": 97, "sustained_seconds": 700},
                priority=Priority.NORMAL,
                dedup_key="cpu:long",
            )
        )
        self._advance(1800)

        # Network flap
        handle(self._evt(NETWORK_LOST, priority=Priority.HIGH, dedup_key="net:lost"))
        self._advance(20)
        handle(
            self._evt(NETWORK_RESTORED, priority=Priority.NORMAL, dedup_key="net:ok")
        )
        self._advance(1800)

        # Evening home arrival
        handle(
            self._evt(HOME_ARRIVAL, priority=Priority.NORMAL, dedup_key="home:1")
        )
        self._advance(3600)

        # Night quiet: more heartbeats
        for i in range(30):
            handle(self._evt(HEARTBEAT, priority=Priority.LOW, dedup_key=f"hb_n:{i}"))
            self._advance(120)

        spoken_count = len(self.spoken)
        suppressed = sum(
            1
            for d in decisions
            if d.action in ("silent", "suppress", "ignore")
            or d.outcome == "suppressed"
        )
        deferred = sum(
            1
            for d in decisions
            if d.action == "queue" or d.outcome == "deferred"
        )
        notify = sum(1 for d in decisions if d.action == "notify")

        # Hard invariants
        self.assertGreaterEqual(spoken_count, 3, "should speak when valuable")
        self.assertLessEqual(spoken_count, 20, "must not spam across 24h sim")
        self.assertGreater(suppressed, 50, "noise must be suppressed")
        # try_deliver may speak queued items without a fresh notify decision
        self.assertGreaterEqual(spoken_count, notify)

        # No repeated identical spam bursts
        unique = set(self.spoken)
        self.assertGreaterEqual(len(unique), max(1, spoken_count - 2))

        # Stale / resolved situations should not revive endlessly
        bat_lines = [s for s in self.spoken if "bater" in s.lower() or "Bateria" in s]
        self.assertLessEqual(len(bat_lines), 4)

        # Expose counts for the final Polish report (also asserted)
        self.pm._sim_report = {
            "spoken": spoken_count,
            "suppressed": suppressed,
            "deferred": deferred,
            "notify": notify,
            "lines": list(self.spoken),
        }
        print(
            f"[SIM24H] spoken={spoken_count} suppressed={suppressed} "
            f"deferred={deferred} lines={self.spoken}"
        )


if __name__ == "__main__":
    unittest.main()
