# ==========================
# ELIAN — SituationEngine / Attention / Future Intent tests
# ==========================
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from core.attention_manager import AttentionManager, build_attention_context
from core.clock import FakeClock
from core.events import (
    BATTERY_CHARGING,
    BATTERY_LOW,
    CALENDAR_UPCOMING,
    CPU_SAMPLE,
    LEAVING_HOME,
    REMINDER_DUE,
    Event,
    Priority,
)
from core.future_intent import (
    FutureIntent,
    FutureIntentEngine,
    IntentCondition,
    IntentStatus,
    looks_like_cancel_future_intent,
    reset_future_intent_engine_for_tests,
)
from core.proactive import ProactiveManager
from core.proactive_policy import DEFER, SUGGEST, reset_policy_for_tests
from core.situation_engine import SituationEngine, reset_situation_engine_for_tests
from core.situation_types import (
    AttentionContext,
    AttentionDecision,
    Situation,
    SituationType,
)
from core.world_state import note_calendar, note_computer, note_conversation, reset_world_state


class SituationEngineTests(unittest.TestCase):
    def setUp(self):
        reset_policy_for_tests()
        reset_world_state()
        reset_situation_engine_for_tests()
        self.clock = FakeClock()
        self.spoken = []
        self.pm = ProactiveManager(
            clock=self.clock,
            speak_fn=lambda m: self.spoken.append(m),
            busy_check=lambda: False,
            preference_get=lambda k, d=None: None,
            max_notifications_per_window=20,
            rate_window_seconds=600,
        )
        self.engine = self.pm.situation_engine

    def tearDown(self):
        reset_policy_for_tests()
        reset_world_state()
        reset_situation_engine_for_tests()

    def _evt(self, typ, **kwargs):
        kwargs.setdefault("source", "system")
        kwargs.setdefault("timestamp", self.clock.time())
        kwargs.setdefault("ttl_seconds", 600)
        return Event(type=typ, **kwargs)

    def test_laptop_battery_plus_meeting_correlates(self):
        note_calendar({"next": "Standup", "minutes": 25})
        d = self.pm.handle_event(
            self._evt(
                BATTERY_LOW,
                data={
                    "percent": 15,
                    "plugged": False,
                    "active_use": True,
                    "device": "laptop",
                },
                priority=Priority.HIGH,
                dedup_key="bat:15",
            )
        )
        self.assertEqual(d.action, "notify")
        self.assertEqual(d.policy, SUGGEST)
        self.assertEqual(len(self.spoken), 1)
        msg = self.spoken[0]
        self.assertIn("15%", msg)
        self.assertIn("Standup", msg)
        self.assertIn("oszczędzania", msg.lower())
        # One coherent suggestion — not separate battery + calendar lines
        self.assertEqual(len(self.spoken), 1)

    def test_phone_battery_plus_departure(self):
        d = self.pm.handle_event(
            self._evt(
                BATTERY_LOW,
                data={
                    "percent": 12,
                    "device": "phone",
                    "plugged": False,
                    "leaving": True,
                },
                priority=Priority.HIGH,
                dedup_key="phone:12",
            )
        )
        self.assertEqual(d.action, "notify")
        self.assertEqual(len(self.spoken), 1)
        self.assertIn("Telefon", self.spoken[0])
        self.assertIn("12%", self.spoken[0])

    def test_low_battery_alone_legacy(self):
        d = self.pm.handle_event(
            self._evt(
                BATTERY_LOW,
                data={"percent": 8, "plugged": False},
                priority=Priority.HIGH,
                dedup_key="bat:alone",
            )
        )
        self.assertEqual(d.action, "notify")
        self.assertIn("baterii", self.spoken[0])
        self.assertNotIn("oszczędzania", self.spoken[0].lower())

    def test_calendar_alone_legacy(self):
        d = self.pm.handle_event(
            self._evt(
                CALENDAR_UPCOMING,
                data={"title": "Dentysta", "minutes": 15},
                priority=Priority.NORMAL,
                dedup_key="cal:1",
            )
        )
        self.assertEqual(d.action, "notify")
        self.assertIn("Dentysta", self.spoken[0])
        self.assertIn("15", self.spoken[0])

    def test_correlated_situation_not_three_messages(self):
        note_calendar({"next": "Review", "minutes": 20})
        self.pm.handle_event(
            self._evt(
                BATTERY_LOW,
                data={"percent": 14, "plugged": False, "active_use": True},
                priority=Priority.HIGH,
                dedup_key="bat:corr",
            )
        )
        # Second signal should absorb / not spam another story
        again = self.pm.handle_event(
            self._evt(
                CALENDAR_UPCOMING,
                data={"title": "Review", "minutes": 19},
                priority=Priority.NORMAL,
                dedup_key="cal:corr",
            )
        )
        self.assertEqual(len(self.spoken), 1)
        self.assertIn(
            again.action,
            ("silent", "suppress", "notify", "queue"),
        )

    def test_duplicates_and_cooldown_situation(self):
        note_calendar({"next": "Sync", "minutes": 30})
        first = self.pm.handle_event(
            self._evt(
                BATTERY_LOW,
                data={"percent": 15, "plugged": False},
                priority=Priority.HIGH,
                dedup_key="bat:dup1",
            )
        )
        self.assertEqual(first.action, "notify")
        second = self.pm.handle_event(
            self._evt(
                BATTERY_LOW,
                data={"percent": 14, "plugged": False},
                priority=Priority.HIGH,
                dedup_key="bat:dup2",
            )
        )
        self.assertIn(second.action, ("silent", "suppress"))
        self.assertEqual(len(self.spoken), 1)

    def test_situation_resolved_before_notification(self):
        note_calendar({"next": "Call", "minutes": 25})
        # Build correlated situation but resolve via charging before a second speak
        first = self.pm.handle_event(
            self._evt(
                BATTERY_LOW,
                data={"percent": 15, "plugged": False},
                priority=Priority.HIGH,
                dedup_key="bat:res1",
            )
        )
        self.assertEqual(first.action, "notify")
        self.assertIn("Call", self.spoken[0])
        self.spoken.clear()
        self.pm._seen_dedup.clear()
        self.pm.handle_event(
            self._evt(
                BATTERY_CHARGING,
                priority=Priority.NORMAL,
                dedup_key="bat:charge",
            )
        )
        # After charge, battery observations cleared — re-alert alone is legacy copy
        reset_world_state()
        self.clock.advance(1000)  # expire correlation window leftovers
        alone = self.pm.handle_event(
            self._evt(
                BATTERY_LOW,
                data={"percent": 15, "plugged": False},
                priority=Priority.HIGH,
                dedup_key="bat:res2",
            )
        )
        self.assertEqual(alone.action, "notify")
        self.assertIn("baterii", self.spoken[-1])
        self.assertNotIn("Call", self.spoken[-1])

    def test_unavailable_phone(self):
        d = self.pm.handle_event(
            self._evt(
                BATTERY_LOW,
                data={
                    "percent": 10,
                    "device": "phone",
                    "unavailable": True,
                    "status": "UNAVAILABLE",
                },
                priority=Priority.HIGH,
                dedup_key="phone:na",
            )
        )
        self.assertEqual(d.action, "silent")
        self.assertEqual(d.reason, "phone_unavailable")
        self.assertEqual(self.spoken, [])

    def test_unavailable_presence(self):
        d = self.pm.handle_event(
            self._evt(
                LEAVING_HOME,
                data={"presence_unavailable": True},
                priority=Priority.NORMAL,
                dedup_key="leave:na",
            )
        )
        self.assertEqual(d.action, "silent")
        self.assertEqual(d.reason, "presence_unavailable")

    def test_stale_event_expired(self):
        evt = self._evt(
            BATTERY_LOW,
            data={"percent": 9},
            priority=Priority.HIGH,
            dedup_key="bat:stale",
            ttl_seconds=10,
        )
        self.clock.advance(30)
        d = self.pm.handle_event(evt)
        self.assertEqual(d.action, "ignore")
        self.assertEqual(d.reason, "expired")


class AttentionManagerTests(unittest.TestCase):
    def setUp(self):
        self.am = AttentionManager()
        self.sit = Situation(
            type=SituationType.BATTERY_LOW.value,
            message="Masz 10% baterii.",
            importance=0.7,
            urgency=0.5,
            relevance=0.7,
            deferrable=True,
            first_seen=1.0,
            last_updated=1.0,
            expiry=10_000.0,
        )

    def test_user_speaking_defers(self):
        ctx = AttentionContext(now=100.0, user_speaking=True, proactive_enabled=True)
        v = self.am.decide(self.sit, ctx)
        self.assertEqual(v.decision, AttentionDecision.SPEAK_AFTER_TURN)
        self.assertEqual(v.reason, "user_speaking")

    def test_tts_speaking_defers(self):
        ctx = AttentionContext(now=100.0, tts_active=True, proactive_enabled=True)
        v = self.am.decide(self.sit, ctx)
        self.assertEqual(v.decision, AttentionDecision.DEFER)
        self.assertEqual(v.reason, "tts_active")

    def test_fullscreen_game_defers(self):
        ctx = AttentionContext(
            now=100.0, fullscreen=True, gaming=True, proactive_enabled=True
        )
        v = self.am.decide(self.sit, ctx)
        self.assertEqual(v.decision, AttentionDecision.DEFER)
        self.assertEqual(v.reason, "fullscreen_game")

    def test_quiet_hours_defers(self):
        ctx = AttentionContext(now=100.0, quiet_hours=True, proactive_enabled=True)
        v = self.am.decide(self.sit, ctx)
        self.assertEqual(v.decision, AttentionDecision.DEFER)
        self.assertEqual(v.reason, "quiet_hours")

    def test_critical_interrupt(self):
        self.sit.critical = True
        self.sit.urgency = 1.0
        ctx = AttentionContext(now=100.0, user_speaking=True, proactive_enabled=True)
        v = self.am.decide(self.sit, ctx)
        self.assertEqual(v.decision, AttentionDecision.INTERRUPT_NOW)

    def test_deferred_notification_via_manager(self):
        clock = FakeClock()
        spoken = []
        busy = True
        pm = ProactiveManager(
            clock=clock,
            speak_fn=lambda m: spoken.append(m),
            busy_check=lambda: busy,
            max_notifications_per_window=10,
        )
        note_calendar({"next": "Meet", "minutes": 20})
        d = pm.handle_event(
            Event(
                type=BATTERY_LOW,
                source="system",
                data={"percent": 15, "plugged": False, "active_use": True},
                priority=Priority.HIGH,
                timestamp=clock.time(),
                dedup_key="att:busy",
                ttl_seconds=600,
            )
        )
        self.assertEqual(d.action, "queue")
        self.assertEqual(spoken, [])
        busy = False
        note = pm.try_deliver()
        self.assertIsNotNone(note)
        self.assertEqual(len(spoken), 1)

    def test_cpu_blip_silent_value(self):
        from core.interesting_event import expected_value_of_interruption

        ev = Event(
            type=CPU_SAMPLE,
            source="system",
            data={"percent": 95},
            priority=Priority.LOW,
            timestamp=1.0,
        )
        self.assertLess(expected_value_of_interruption(ev, sustained_seconds=2), 0.2)


class FutureIntentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "future_intents.json"
        self.clock = FakeClock()
        self.engine = reset_future_intent_engine_for_tests(path=path)
        self.engine.set_clock(self.clock.time)

    def tearDown(self):
        self.tmp.cleanup()

    def test_at_time_and_before_event(self):
        intent = FutureIntent(
            condition=IntentCondition.AT_TIME.value,
            description="Przypomnij o herbacie",
            at_time=self.clock.time() + 60,
            message="Czas na herbatę.",
        )
        self.engine.add(intent)
        self.assertEqual(self.engine.evaluate(), [])
        self.clock.advance(61)
        got = self.engine.evaluate()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].status, IntentStatus.TRIGGERED.value)

        before = FutureIntent(
            condition=IntentCondition.BEFORE_EVENT.value,
            description="Przed spotkaniem",
            before_event_id="standup",
            before_minutes=15,
            message="Za chwilę standup.",
        )
        self.engine.add(before)
        fired = self.engine.evaluate(
            world={"calendar": {"next": "standup", "minutes": 10}}
        )
        self.assertTrue(any(i.id == before.id for i in fired))

    def test_if_not_completed_and_cancel(self):
        intent = FutureIntent(
            condition=IntentCondition.IF_NOT_COMPLETED.value,
            description="Wyślij raport",
            completed_key="report",
            deadline=self.clock.time() + 100,
            payload={"lead_seconds": 50},
            message="Raport nadal niegotowy.",
        )
        self.engine.add(intent)
        self.clock.advance(60)
        fired = self.engine.evaluate()
        self.assertEqual(len(fired), 1)

        self.assertTrue(looks_like_cancel_future_intent("już nie musisz"))
        other = FutureIntent(
            condition=IntentCondition.AT_TIME.value,
            description="Coś innego",
            at_time=self.clock.time() + 9999,
        )
        self.engine.add(other)
        cancelled = self.engine.cancel(all_active=True)
        self.assertTrue(cancelled)
        # Persist across new engine instance
        path = self.engine.path
        reloaded = FutureIntentEngine(path=path, clock=self.clock.time)
        active = reloaded.list_active()
        self.assertEqual(active, [])

    def test_presence_unavailable_intent(self):
        intent = FutureIntent(
            condition=IntentCondition.ON_DEPARTURE.value,
            description="Przy wyjściu",
            message="Drzwi.",
        )
        self.engine.add(intent)
        result = self.engine.evaluate(
            world={"session": {"presence_unavailable": True}}
        )
        self.assertEqual(result, [])
        stored = self.engine._intents[intent.id]
        self.assertEqual(stored.status, IntentStatus.UNAVAILABLE.value)


class ProactiveTurnIngressTests(unittest.TestCase):
    def test_handle_proactive_turn(self):
        from core.conversation_manager import ConversationManager
        from core.model_client import FakeModelClient
        from core.tools import ToolRegistry
        from memory.manager import MemoryManager

        with tempfile.TemporaryDirectory() as tmp:
            cm = ConversationManager(
                memory_manager=MemoryManager(path=str(Path(tmp) / "mem.json")),
                registry=ToolRegistry(),
                model=FakeModelClient(responses=[]),
            )
            result = cm.handle_proactive_turn(
                "Bateria niska — podłączyć tryb oszczędzania?",
                source="proactive",
            )
            self.assertTrue(result.should_speak)
            self.assertIn("Bateria", result.text)
            self.assertEqual(result.source, "proactive")
            self.assertTrue(result.data.get("proactive"))


if __name__ == "__main__":
    unittest.main()
