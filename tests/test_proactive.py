# ==========================
# ELIAN — Event Bus + Proactivity tests (Phase 6)
# Offline / deterministic clock — no sleep, no real HA
# ==========================

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from core.builtin_tools import build_default_registry
from core.clock import FakeClock
from core.conversation_manager import ConversationManager
from core.event_bus import EventBus, reset_event_bus_for_tests
from core.events import (
    BATTERY_LOW,
    CPU_HIGH_SUSTAINED,
    DISK_LOW,
    NETWORK_LOST,
    NETWORK_RESTORED,
    Event,
    Priority,
)
from core.model_client import FakeModelClient
from core.proactive import ProactiveManager
from core.runtime import ElianRuntime
from core.system_state import SystemStateStore, reset_system_state_for_tests
from core.tools import ToolRegistry
from memory.manager import MemoryManager
from watchers.home_assistant import FakeHAHealth, HomeAssistantWatcher
from watchers.system import SystemWatcher
from watchers.system_provider import FakeSystemProvider


class EventBusTests(unittest.TestCase):
    def test_publish_subscribe(self):
        clock = FakeClock()
        bus = EventBus(clock=clock)
        got = []
        bus.subscribe(lambda e: got.append(e.type))
        bus.publish(
            Event(type="ping", source="test", timestamp=clock.time())
        )
        self.assertEqual(got, ["ping"])


class BatteryWatcherTests(unittest.TestCase):
    def test_threshold_once_no_spam(self):
        clock = FakeClock()
        bus = EventBus(clock=clock)
        events = []
        bus.subscribe(lambda e: events.append(e))
        provider = FakeSystemProvider(
            has_battery=True,
            battery_percent=21,
            battery_plugged=False,
            cpu_percent=10,
            ram_percent=40,
            disk_free_gb=100,
            network_online=True,
        )
        watcher = SystemWatcher(bus, provider=provider, clock=clock)
        watcher.battery_thresholds = [20, 10, 5]
        watcher.interval_seconds = 0
        watcher.start()
        watcher.poll_once()
        self.assertEqual(events, [])

        provider.set(battery_percent=19)
        clock.advance(1)
        watcher.poll_once()
        battery_events = [e for e in events if e.type == BATTERY_LOW]
        self.assertEqual(len(battery_events), 1)
        self.assertEqual(battery_events[0].data.get("threshold"), 20)

        provider.set(battery_percent=18)
        clock.advance(1)
        watcher.poll_once()
        provider.set(battery_percent=17)
        clock.advance(1)
        watcher.poll_once()
        battery_events = [e for e in events if e.type == BATTERY_LOW]
        self.assertEqual(len(battery_events), 1)


class CpuDurationTests(unittest.TestCase):
    def test_spike_ignored_sustained_fires(self):
        clock = FakeClock()
        bus = EventBus(clock=clock)
        events = []
        bus.subscribe(lambda e: events.append(e.type))
        provider = FakeSystemProvider(
            has_battery=False,
            cpu_percent=99,
            ram_percent=40,
            disk_free_gb=100,
            network_online=True,
        )
        watcher = SystemWatcher(bus, provider=provider, clock=clock)
        watcher.cpu_threshold = 95
        watcher.cpu_duration = 120
        watcher.interval_seconds = 0
        watcher.start()

        watcher.poll_once()  # start timer
        clock.advance(10)
        watcher.poll_once()
        self.assertNotIn(CPU_HIGH_SUSTAINED, events)

        clock.advance(120)
        watcher.poll_once()
        self.assertIn(CPU_HIGH_SUSTAINED, events)


class NetworkTransitionTests(unittest.TestCase):
    def test_offline_online_transitions(self):
        clock = FakeClock()
        bus = EventBus(clock=clock)
        events = []
        bus.subscribe(lambda e: events.append(e.type))
        provider = FakeSystemProvider(
            has_battery=False,
            cpu_percent=10,
            ram_percent=40,
            disk_free_gb=100,
            network_online=True,
        )
        watcher = SystemWatcher(bus, provider=provider, clock=clock)
        watcher.network_debounce = 15
        watcher.interval_seconds = 0
        watcher.start()
        watcher.poll_once()  # seed online

        provider.set(network_online=False)
        clock.advance(1)
        watcher.poll_once()  # pending
        clock.advance(20)
        watcher.poll_once()  # commit lost
        self.assertIn(NETWORK_LOST, events)

        # still offline — no duplicate
        clock.advance(20)
        watcher.poll_once()
        self.assertEqual(events.count(NETWORK_LOST), 1)

        provider.set(network_online=True)
        clock.advance(1)
        watcher.poll_once()
        clock.advance(20)
        watcher.poll_once()
        self.assertIn(NETWORK_RESTORED, events)


class ProactivePolicyTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.spoken = []
        self.prefs = {}
        self.busy = False
        self.pm = ProactiveManager(
            clock=self.clock,
            speak_fn=lambda m: self.spoken.append(m),
            busy_check=lambda: self.busy,
            preference_get=lambda k, d=None: self.prefs.get(k, d),
            max_notifications_per_window=10,
            rate_window_seconds=600,
        )

    def _evt(self, typ, **kwargs):
        return Event(
            type=typ,
            source="system",
            timestamp=self.clock.time(),
            **kwargs,
        )

    def test_dedup_latch(self):
        e = self._evt(
            BATTERY_LOW,
            data={"percent": 5},
            priority=Priority.HIGH,
            dedup_key="system:battery_5",
        )
        self.pm.handle_event(e)
        self.assertEqual(len(self.spoken), 1)
        self.pm.handle_event(e)
        self.assertEqual(len(self.spoken), 1)

    def test_quiet_hours_queues_normal(self):
        with patch(
            "core.proactive._proactive_config",
            return_value={
                "enabled": True,
                "quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00"},
                "battery_enabled": True,
                "system_enabled": True,
            },
        ):
            self.clock.set_local(datetime(2026, 6, 15, 23, 0, 0))
            e = self._evt(
                CPU_HIGH_SUSTAINED,
                priority=Priority.NORMAL,
                dedup_key="system:cpu_high",
            )
            d = self.pm.handle_event(e)
            self.assertEqual(d.action, "queue")
            self.assertEqual(self.spoken, [])

    def test_user_busy_queues_then_delivers(self):
        self.busy = True
        e = self._evt(
            BATTERY_LOW,
            data={"percent": 5},
            priority=Priority.HIGH,
            dedup_key="system:battery_5",
            ttl_seconds=600,
        )
        d = self.pm.handle_event(e)
        self.assertEqual(d.action, "queue")
        self.assertEqual(self.spoken, [])
        self.busy = False
        note = self.pm.try_deliver()
        self.assertIsNotNone(note)
        self.assertEqual(len(self.spoken), 1)

    def test_ttl_expires_queued(self):
        self.busy = True
        e = self._evt(
            NETWORK_LOST,
            priority=Priority.HIGH,
            dedup_key="system:network_lost",
            ttl_seconds=30,
        )
        self.pm.handle_event(e)
        self.clock.advance(60)
        self.busy = False
        note = self.pm.try_deliver()
        self.assertIsNone(note)
        self.assertEqual(self.spoken, [])

    def test_invalidation_network(self):
        self.busy = True
        lost = self._evt(
            NETWORK_LOST,
            priority=Priority.HIGH,
            dedup_key="system:network_lost",
            ttl_seconds=300,
        )
        self.pm.handle_event(lost)
        self.assertEqual(self.pm.queue_size(), 1)
        restored = self._evt(
            NETWORK_RESTORED,
            priority=Priority.NORMAL,
            dedup_key="system:network_restored",
            invalidates=(NETWORK_LOST,),
            ttl_seconds=60,
        )
        # restored while busy → may queue; lost should be invalidated
        self.pm.handle_event(restored)
        types = [q.event.type for q in self.pm._queue]
        self.assertNotIn(NETWORK_LOST, types)

    def test_global_off(self):
        self.prefs["proactive.enabled"] = "false"
        e = self._evt(
            BATTERY_LOW,
            data={"percent": 5},
            priority=Priority.HIGH,
            dedup_key="system:battery_5",
        )
        d = self.pm.handle_event(e)
        self.assertEqual(d.action, "suppress")
        self.assertEqual(self.spoken, [])

    def test_cpu_pref_off(self):
        self.prefs["proactive.system.cpu"] = "false"
        e = self._evt(
            CPU_HIGH_SUSTAINED,
            priority=Priority.NORMAL,
            dedup_key="system:cpu_high",
        )
        d = self.pm.handle_event(e)
        self.assertEqual(d.action, "suppress")

    def test_disk_low_no_tools(self):
        registry = ToolRegistry()
        e = self._evt(
            DISK_LOW,
            data={"free_gb": 5},
            priority=Priority.NORMAL,
            dedup_key="system:disk_low",
        )
        self.pm.handle_event(e)
        self.assertEqual(len(registry.history), 0)
        self.assertTrue(any("dysku" in m.lower() for m in self.spoken))

    def test_queue_limit(self):
        self.busy = True
        for i in range(20):
            self.pm.handle_event(
                self._evt(
                    "info",
                    priority=Priority.LOW,
                    dedup_key=f"system:info_{i}",
                    ttl_seconds=600,
                    data={"message": f"m{i}"},
                )
            )
        self.assertLessEqual(self.pm.queue_size(), self.pm.queue_limit)

    def test_high_priority_first(self):
        self.busy = True
        self.pm.handle_event(
            self._evt(
                "noise",
                priority=Priority.LOW,
                dedup_key="a",
                ttl_seconds=600,
                data={"message": "low"},
            )
        )
        self.pm.handle_event(
            self._evt(
                BATTERY_LOW,
                priority=Priority.HIGH,
                dedup_key="b",
                ttl_seconds=600,
                data={"percent": 5, "message": "high"},
            )
        )
        self.busy = False
        note = self.pm.try_deliver()
        self.assertIsNotNone(note)
        self.assertIn("baterii", note.message.lower())


class HaWatcherTests(unittest.TestCase):
    def test_transition_only(self):
        clock = FakeClock()
        bus = EventBus(clock=clock)
        events = []
        bus.subscribe(lambda e: events.append(e.type))
        health = FakeHAHealth(True)
        watcher = HomeAssistantWatcher(
            bus, health_fn=health, clock=clock, state_store=SystemStateStore()
        )
        watcher.interval_seconds = 0
        watcher.start()
        watcher.poll_once()
        health.available = False
        clock.advance(1)
        watcher.poll_once()
        self.assertEqual(events.count("ha_unavailable"), 1)
        clock.advance(1)
        watcher.poll_once()
        self.assertEqual(events.count("ha_unavailable"), 1)
        health.available = True
        clock.advance(1)
        watcher.poll_once()
        self.assertIn("ha_restored", events)


class RuntimeShutdownTests(unittest.TestCase):
    def test_clean_shutdown(self):
        clock = FakeClock()
        bus = EventBus(clock=clock)
        provider = FakeSystemProvider(
            has_battery=False,
            cpu_percent=1,
            ram_percent=1,
            disk_free_gb=100,
            network_online=True,
        )
        store = SystemStateStore()
        watcher = SystemWatcher(
            bus, provider=provider, clock=clock, state_store=store
        )
        watcher.interval_seconds = 60
        runtime = ElianRuntime(
            bus=bus,
            watchers=[watcher],
            clock=clock,
            state_store=store,
            speak_fn=lambda m: None,
            busy_check=lambda: False,
            start_background=True,
            poll_sleep=0.05,
        )
        runtime.start()
        self.assertTrue(runtime.threads_alive())
        runtime.stop()
        self.assertFalse(runtime.threads_alive())


class FastPathUnaffectedTests(unittest.TestCase):
    def test_glosniej_no_model(self):
        model = FakeModelClient(responses=[])
        registry = build_default_registry(confirm_medium=False)
        manager = ConversationManager(registry=registry, model=model)
        manager.activate()
        with patch("core.builtin_tools.Music.run", return_value="Gotowe."):
            result = manager.handle("głośniej")
        self.assertIn(result.source, ('fast_path', 'action_loop', 'agent'))
        self.assertEqual(len(model.calls), 0)


class PreferenceCommandTests(unittest.TestCase):
    def test_disable_notifications(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            mem = MemoryManager(path=Path(td) / "mem.json")
            manager = ConversationManager(
                registry=ToolRegistry(),
                model=FakeModelClient(responses=[]),
                memory_manager=mem,
            )
            manager.activate()
            result = manager.handle("wyłącz powiadomienia")
            self.assertEqual(result.source, "proactive_pref")
            self.assertEqual(
                str(mem.get_preference("proactive.enabled")).lower(),
                "false",
            )


def tearDownModule():
    reset_event_bus_for_tests(None)
    reset_system_state_for_tests(None)


if __name__ == "__main__":
    unittest.main()
