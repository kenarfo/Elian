# ==========================
# ELIAN — ProactivePolicy multi-signal tests
# ==========================
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from core.clock import FakeClock
from core.conversation_manager import ConversationManager
from core.events import (
    BATTERY_LOW,
    CALENDAR_UPCOMING,
    CPU_HIGH_SUSTAINED,
    CPU_SAMPLE,
    HA_UNAVAILABLE,
    HEARTBEAT,
    HOME_ARRIVAL,
    LEAVING_HOME,
    REMINDER_DUE,
    WORLD_STATE_REFRESH,
    Event,
    Priority,
)
from core.model_client import FakeModelClient
from core.proactive import ProactiveManager
from core.proactive_offer import get_proactive_offer, reset_offer_for_tests
from core.proactive_policy import (
    ASK,
    DIGEST,
    IGNORE,
    NOTIFY,
    group_digest_spoken,
    mark_consumed,
    reset_policy_for_tests,
)
from core.session_digest import reset_digest_for_tests
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from core.world_state import (
    note_computer,
    note_conversation,
    note_smart_home,
    reset_world_state,
)
from memory.manager import MemoryManager


class ProactivePolicyTests(unittest.TestCase):
    def setUp(self):
        reset_policy_for_tests()
        reset_offer_for_tests()
        reset_digest_for_tests()
        reset_world_state()
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

    def tearDown(self):
        reset_policy_for_tests()
        reset_offer_for_tests()
        reset_digest_for_tests()
        reset_world_state()

    def _evt(self, typ, **kwargs):
        kwargs.setdefault("source", "system")
        kwargs.setdefault("timestamp", self.clock.time())
        kwargs.setdefault("ttl_seconds", 600)
        return Event(type=typ, **kwargs)

    def test_duplicate_suppression(self):
        e = self._evt(
            BATTERY_LOW,
            data={"percent": 8},
            priority=Priority.HIGH,
            dedup_key="system:battery_8",
        )
        self.pm.handle_event(e)
        self.assertEqual(len(self.spoken), 1)
        again = self.pm.handle_event(e)
        self.assertEqual(again.action, "suppress")
        self.assertEqual(again.reason, "dedup_latch")
        self.assertEqual(len(self.spoken), 1)

        similar = self._evt(
            CPU_HIGH_SUSTAINED,
            priority=Priority.NORMAL,
            dedup_key="system:cpu_a",
        )
        other = self._evt(
            CPU_HIGH_SUSTAINED,
            priority=Priority.NORMAL,
            dedup_key="system:cpu_b",
        )
        first = self.pm.handle_event(similar)
        self.assertEqual(first.action, "notify")
        second = self.pm.handle_event(other)
        self.assertEqual(second.action, "suppress")
        self.assertEqual(second.reason, "duplicate_message")
        self.assertEqual(second.policy, IGNORE)

    def test_cooldown(self):
        e = self._evt(
            CPU_HIGH_SUSTAINED,
            priority=Priority.NORMAL,
            dedup_key="system:cpu_cool",
        )
        self.pm.handle_event(e)
        self.pm._seen_dedup.clear()
        self.pm._last_spoken_norm = ""
        blocked = self.pm.handle_event(e)
        self.assertEqual(blocked.action, "suppress")
        self.assertEqual(blocked.reason, "cooldown")
        self.assertEqual(len(self.spoken), 1)

    def test_quiet_hours(self):
        with patch(
            "core.proactive._proactive_config",
            return_value={
                "enabled": True,
                "quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00"},
                "system_enabled": True,
            },
        ):
            self.clock.set_local(datetime(2026, 6, 15, 23, 15, 0))
            d = self.pm.handle_event(
                self._evt(
                    CPU_HIGH_SUSTAINED,
                    priority=Priority.NORMAL,
                    dedup_key="system:cpu_quiet",
                )
            )
            self.assertEqual(d.action, "queue")
            self.assertEqual(d.reason, "quiet_hours")
            self.assertEqual(d.policy, DIGEST)
            self.assertEqual(self.spoken, [])

    def test_home_arrival(self):
        fake = ToolResult.ok(
            "Witaj w domu. Przypomnienie: leki.",
            tool_name="get_situation_briefing",
        )
        with patch(
            "core.situation_briefing.compose_situation_briefing",
            return_value=fake,
        ):
            d = self.pm.handle_event(
                self._evt(
                    HOME_ARRIVAL,
                    priority=Priority.NORMAL,
                    dedup_key="presence:home_1",
                )
            )
        self.assertEqual(d.action, "notify")
        self.assertEqual(d.policy, NOTIFY)
        self.assertEqual(d.rule, "home_arrival")
        self.assertTrue(self.spoken)
        self.assertIn("Witaj w domu", self.spoken[0])
        self.assertIn("leki", self.spoken[0])
        again = self.pm.handle_event(
            self._evt(
                HOME_ARRIVAL,
                priority=Priority.NORMAL,
                dedup_key="presence:home_2",
            )
        )
        self.assertEqual(again.action, "silent")
        self.assertEqual(again.reason, "consumed")
        self.assertEqual(len(self.spoken), 1)

    def test_home_arrival_skips_if_briefing_already_spoke(self):
        mark_consumed("home_arrival", now=self.clock.time())
        d = self.pm.handle_event(
            self._evt(HOME_ARRIVAL, priority=Priority.NORMAL, dedup_key="presence:home_3")
        )
        self.assertEqual(d.action, "silent")
        self.assertEqual(self.spoken, [])

    def test_reminder_due(self):
        d = self.pm.handle_event(
            self._evt(
                REMINDER_DUE,
                data={"text": "leki"},
                priority=Priority.HIGH,
                dedup_key="reminder:leki",
            )
        )
        self.assertEqual(d.action, "notify")
        self.assertEqual(d.policy, NOTIFY)
        self.assertIsNone(get_proactive_offer())
        self.assertIn("Przypomnienie", self.spoken[0])
        self.assertIn("leki", self.spoken[0])

    def test_ha_unavailable(self):
        idle = self.pm.handle_event(
            self._evt(
                HA_UNAVAILABLE,
                priority=Priority.NORMAL,
                dedup_key="ha:down_idle",
            )
        )
        self.assertEqual(idle.action, "notify")
        self.assertIn("Home Assistant", self.spoken[0])
        self.assertNotIn("wszystko OK", self.spoken[0].lower())

        self.spoken.clear()
        self.pm._seen_dedup.clear()
        self.pm._last_spoken_norm = ""
        note_conversation("last_intent", "turn_off_device")
        note_conversation("ha_topic", True)
        self.pm.conversation_active = True
        during = self.pm.handle_event(
            self._evt(
                HA_UNAVAILABLE,
                priority=Priority.NORMAL,
                dedup_key="ha:down_lights",
            )
        )
        self.assertEqual(during.action, "notify")
        self.assertEqual(during.rule, "ha_during_light")
        self.assertIn("Sterowanie domu", self.spoken[0])
        self.assertIn("niedostępne", self.spoken[0])

    def test_low_battery(self):
        d = self.pm.handle_event(
            self._evt(
                BATTERY_LOW,
                data={"percent": 7},
                priority=Priority.HIGH,
                dedup_key="system:battery_7",
            )
        )
        self.assertEqual(d.action, "notify")
        self.assertEqual(d.policy, NOTIFY)
        self.assertIn("baterii", self.spoken[0])
        self.assertIn("7", self.spoken[0])

        self.spoken.clear()
        reset_world_state()
        note_conversation("sleeping", True)
        asleep = self.pm.handle_event(
            self._evt(
                BATTERY_LOW,
                data={"percent": 6},
                priority=Priority.HIGH,
                dedup_key="system:battery_sleep",
            )
        )
        self.assertEqual(asleep.action, "queue")
        self.assertEqual(asleep.reason, "sleeping")
        self.assertEqual(asleep.policy, DIGEST)
        self.assertEqual(self.spoken, [])

    def test_irrelevant_cpu_event(self):
        d = self.pm.handle_event(
            self._evt(
                CPU_SAMPLE,
                data={"percent": 18},
                priority=Priority.LOW,
                dedup_key="system:cpu_sample",
            )
        )
        self.assertEqual(d.action, "silent")
        self.assertEqual(self.spoken, [])
        beat = self.pm.handle_event(
            self._evt(HEARTBEAT, priority=Priority.LOW, dedup_key="rt:hb")
        )
        self.assertEqual(beat.action, "silent")
        refresh = self.pm.handle_event(
            self._evt(WORLD_STATE_REFRESH, priority=Priority.LOW, dedup_key="rt:ws")
        )
        self.assertEqual(refresh.action, "silent")
        self.assertEqual(self.spoken, [])

    def test_active_tts(self):
        self.busy = True
        d = self.pm.handle_event(
            self._evt(
                BATTERY_LOW,
                data={"percent": 9},
                priority=Priority.HIGH,
                dedup_key="system:battery_tts",
            )
        )
        self.assertEqual(d.action, "queue")
        self.assertEqual(d.reason, "user_busy")
        self.assertEqual(self.spoken, [])
        self.busy = False
        self.pm.important_tts = True
        note = self.pm.try_deliver()
        self.assertIsNone(note)
        self.assertEqual(self.spoken, [])
        self.pm.important_tts = False
        note = self.pm.try_deliver()
        self.assertIsNotNone(note)
        self.assertEqual(len(self.spoken), 1)

    def test_active_conversation(self):
        self.pm.conversation_active = True
        d = self.pm.handle_event(
            self._evt(
                CPU_HIGH_SUSTAINED,
                priority=Priority.NORMAL,
                dedup_key="system:cpu_talk",
            )
        )
        self.assertEqual(d.action, "queue")
        self.assertEqual(d.reason, "conversation_active")
        self.assertEqual(d.policy, DIGEST)
        self.assertEqual(self.spoken, [])
        self.assertIsNone(self.pm.try_deliver())
        self.pm.conversation_active = False
        note = self.pm.try_deliver()
        self.assertIsNotNone(note)
        self.assertTrue(self.spoken)

    def test_digest_batching(self):
        self.pm.conversation_active = True
        for i, text in enumerate(("Sieć wróciła.", "Mało miejsca na dysku.", "RAM zajęty.")):
            self.pm.handle_event(
                self._evt(
                    "info",
                    data={"message": text},
                    priority=Priority.NORMAL,
                    dedup_key=f"info:{i}",
                )
            )
        self.assertEqual(self.spoken, [])
        self.assertGreaterEqual(self.pm.queue_size(), 2)
        self.pm.conversation_active = False
        note = self.pm.try_deliver()
        self.assertIsNotNone(note)
        self.assertEqual(len(self.spoken), 1)
        self.assertIn("Kilka rzeczy", self.spoken[0])
        self.assertEqual(self.pm.queue_size(), 0)

    def test_ask_vs_notify(self):
        notify = self.pm.handle_event(
            self._evt(
                REMINDER_DUE,
                data={"text": "herbata"},
                priority=Priority.HIGH,
                dedup_key="reminder:tea",
            )
        )
        self.assertEqual(notify.policy, NOTIFY)
        self.assertIsNone(get_proactive_offer())
        self.assertNotIn("czy chcesz coś jeszcze", self.spoken[0].lower())

        reset_world_state()
        self.spoken.clear()
        note_smart_home(
            {
                "entity_id": "light.salon",
                "name": "Lampa",
                "state": "on",
                "domain": "light",
            }
        )
        ask = self.pm.handle_event(
            self._evt(
                LEAVING_HOME,
                data={"presence": "leaving"},
                priority=Priority.NORMAL,
                dedup_key="presence:leave_1",
            )
        )
        self.assertEqual(ask.action, "notify")
        self.assertEqual(ask.policy, ASK)
        self.assertEqual(ask.rule, "leaving_lights")
        self.assertEqual(self.spoken[0], "Lampa nadal świeci. Zgasić?")
        offer = get_proactive_offer()
        self.assertIsNotNone(offer)
        self.assertEqual(offer.kind, "turn_off_light")
        self.assertEqual(offer.extra.get("tool"), "turn_off_device")

        reset_world_state()
        silent_leave = self.pm.handle_event(
            self._evt(
                LEAVING_HOME,
                priority=Priority.NORMAL,
                dedup_key="presence:leave_empty",
            )
        )
        self.assertEqual(silent_leave.action, "silent")
        self.assertEqual(silent_leave.reason, "leaving_no_lights")

    def test_preference_opt_out(self):
        self.prefs["proactive.enabled"] = "false"
        d = self.pm.handle_event(
            self._evt(
                BATTERY_LOW,
                data={"percent": 4},
                priority=Priority.HIGH,
                dedup_key="system:battery_off",
            )
        )
        self.assertEqual(d.action, "suppress")
        self.assertEqual(d.reason, "proactive_disabled")
        self.assertEqual(self.spoken, [])

        self.prefs.clear()
        self.prefs["proactive.battery.enabled"] = "false"
        blocked = self.pm.handle_event(
            self._evt(
                BATTERY_LOW,
                data={"percent": 3},
                priority=Priority.HIGH,
                dedup_key="system:battery_cat",
            )
        )
        self.assertEqual(blocked.action, "suppress")
        self.assertIn("category_disabled", blocked.reason)

    def test_calendar_plus_windows_update(self):
        note_computer({"windows_update": True, "update_starting": True})
        d = self.pm.handle_event(
            self._evt(
                CALENDAR_UPCOMING,
                data={"title": "Spotkanie", "minutes": 20},
                priority=Priority.NORMAL,
                dedup_key="cal:meet",
            )
        )
        self.assertEqual(d.action, "notify")
        self.assertEqual(d.rule, "calendar_windows_update")
        self.assertIn("20", self.spoken[0])
        self.assertIn("Spotkanie", self.spoken[0])
        self.assertIn("aktualizacja Windows", self.spoken[0])

    def test_decision_log_shape(self):
        self.pm.handle_event(
            self._evt(
                REMINDER_DUE,
                data={"text": "log"},
                priority=Priority.HIGH,
                dedup_key="reminder:log",
            )
        )
        row = self.pm.decisions_log[-1]
        self.assertEqual(row.get("event") or row.get("type"), REMINDER_DUE)
        self.assertEqual(row.get("policy"), NOTIFY)
        self.assertEqual(row.get("decision") or row.get("action"), "notify")
        self.assertEqual(row.get("outcome"), "spoken")

    def test_group_digest_spoken(self):
        text = group_digest_spoken(["Raz.", "Dwa.", "Trzy."])
        self.assertTrue(text.startswith("Kilka rzeczy:"))
        self.assertIn("Raz", text)

    def test_ask_offer_yes_invokes_tool(self):
        note_smart_home(
            {
                "entity_id": "light.kuchnia",
                "name": "Lampa",
                "state": "on",
                "domain": "light",
            }
        )
        self.pm.handle_event(
            self._evt(LEAVING_HOME, priority=Priority.NORMAL, dedup_key="presence:leave_yes")
        )
        called = []

        def _off(arguments):
            called.append(dict(arguments or {}))
            return ToolResult.ok("Zgaszona.", tool_name="turn_off_device")

        registry = ToolRegistry(confirm_medium=False)
        registry.register(
            Tool(name="turn_off_device", description="off", handler=_off)
        )
        mem = MemoryManager(Path(tempfile.mkdtemp()) / "m.json")
        cm = ConversationManager(
            registry=registry,
            model=FakeModelClient(responses=[]),
            memory_manager=mem,
        )
        cm.activate()
        yes = cm.handle("tak")
        self.assertEqual(yes.source, "proactive_offer")
        self.assertEqual(called[0].get("entity_id"), "light.kuchnia")
        self.assertIn("Zgaszona", yes.text)
        self.assertIsNone(get_proactive_offer())


if __name__ == "__main__":
    unittest.main()
