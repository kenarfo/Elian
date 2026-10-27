# ==========================
# ELIAN — Phase 13 C.5.2 proactive event speech isolation
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.builtin_tools import build_default_registry
from core.clock import FakeClock
from core.conversation_manager import ConversationManager
from core.events import (
    ACTIVE_WINDOW_CHANGED,
    BATTERY_LOW,
    REMINDER_DUE,
    SCREEN_FRAME_UPDATED,
    VISION_COMPLETED,
    Event,
    Priority,
)
from core.jarvis_response import polish_spoken, social_reply, summarize_compound_steps
from core.model_client import FakeModelClient, reset_model_client
from core.proactive import ProactiveCandidate, ProactiveManager
from core.speech_guard import guard_user_facing_speech, looks_like_internal_identifier
from core.tools import reset_registry_for_tests
from core.world_state import reset_world_state
from memory.manager import MemoryManager
from WakeWord import extract_wake


class Phase13C52ProactiveSpeechTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.spoken = []
        self.busy = False
        self.pm = ProactiveManager(
            clock=self.clock,
            speak_fn=lambda m: self.spoken.append(m),
            busy_check=lambda: self.busy,
            preference_get=lambda k, d=None: d,
            max_notifications_per_window=10,
            rate_window_seconds=600,
        )

    def _evt(self, typ, **kwargs):
        kwargs.setdefault("source", "perception")
        kwargs.setdefault("timestamp", self.clock.time())
        kwargs.setdefault("ttl_seconds", 300)
        return Event(type=typ, **kwargs)

    def test_active_window_changed_user_busy_never_raw_tts(self):
        self.busy = True
        event = self._evt(
            ACTIVE_WINDOW_CHANGED,
            data={"hwnd": 2, "title": "Notatnik"},
            priority=Priority.NORMAL,
        )
        decision = self.pm.handle_event(event)
        self.assertEqual(decision.action, "silent")
        self.assertEqual(decision.reason, "state_only")
        self.assertEqual(self.pm.queue_size(), 0)
        self.assertEqual(self.spoken, [])
        self.assertNotEqual(decision.message, ACTIVE_WINDOW_CHANGED)

    def test_active_window_changed_after_idle_timeout_no_speech(self):
        self.busy = True
        self.pm.handle_event(
            self._evt(ACTIVE_WINDOW_CHANGED, data={"hwnd": 9, "title": "Notatnik"})
        )
        self.assertEqual(self.pm.queue_size(), 0)
        self.busy = False
        # Poison-pill: old queue shape must still be dropped on idle flush.
        self.pm._queue.append(
            ProactiveCandidate(
                event=self._evt(ACTIVE_WINDOW_CHANGED, data={"hwnd": 9}),
                message=ACTIVE_WINDOW_CHANGED,
                user_relevance=True,
                created_at=self.clock.time(),
            )
        )
        note = self.pm.try_deliver()
        self.assertIsNone(note)
        self.assertEqual(self.spoken, [])
        self.assertEqual(self.pm.queue_size(), 0)

    def test_screen_frame_updated_silent(self):
        decision = self.pm.handle_event(
            self._evt(SCREEN_FRAME_UPDATED, data={"frame_id": 3}, priority=Priority.LOW)
        )
        self.assertEqual(decision.action, "silent")
        self.assertEqual(self.spoken, [])

    def test_vision_completed_no_spoken_enum(self):
        decision = self.pm.handle_event(self._evt(VISION_COMPLETED, data={"confidence": 0}))
        self.assertEqual(decision.action, "silent")
        self.assertEqual(self.spoken, [])
        self.assertNotIn(VISION_COMPLETED, self.spoken)

    def test_focus_steal_semantic_message_allowed(self):
        self.busy = False
        event = self._evt(
            ACTIVE_WINDOW_CHANGED,
            data={
                "hwnd": 7,
                "title": "Cursor",
                "blocker": "Cursor",
                "goal_blocked": True,
                "user_relevance": True,
                "observation_confirmed": True,
                "blocked_action": "wpisywanie",
            },
        )
        decision = self.pm.handle_event(event)
        self.assertEqual(decision.action, "notify")
        self.assertEqual(len(self.spoken), 1)
        spoken = self.spoken[0]
        self.assertNotIn(ACTIVE_WINDOW_CHANGED, spoken)
        self.assertIn("Cursor", spoken)
        self.assertIn("fokus", spoken.lower())

    def test_internal_enum_sanitizer_blocks(self):
        self.assertTrue(looks_like_internal_identifier(ACTIVE_WINDOW_CHANGED))
        self.assertEqual(guard_user_facing_speech(ACTIVE_WINDOW_CHANGED), "")
        self.assertEqual(polish_spoken(ACTIVE_WINDOW_CHANGED), "")
        self.assertEqual(polish_spoken("VISION_COMPLETED"), "")
        self.assertEqual(polish_spoken("UNKNOWN_SIDE_EFFECT"), "")
        self.assertEqual(polish_spoken("LOCAL_COMPUTER"), "")
        self.assertTrue(polish_spoken("Notatnik otwarty, tekst wpisany i zaznaczony."))

    def test_real_proactive_notification_still_speaks(self):
        event = Event(
            type=BATTERY_LOW,
            source="system",
            data={"percent": 5},
            priority=Priority.HIGH,
            timestamp=self.clock.time(),
            dedup_key="system:battery_5",
            ttl_seconds=600,
        )
        decision = self.pm.handle_event(event)
        self.assertEqual(decision.action, "notify")
        self.assertEqual(len(self.spoken), 1)
        self.assertIn("baterii", self.spoken[0].lower())
        self.assertNotEqual(self.spoken[0], BATTERY_LOW)

    def test_reminder_proactive_still_speaks(self):
        event = Event(
            type=REMINDER_DUE,
            source="reminders",
            data={"text": "leki"},
            priority=Priority.HIGH,
            timestamp=self.clock.time(),
            ttl_seconds=600,
        )
        decision = self.pm.handle_event(event)
        self.assertEqual(decision.action, "notify")
        self.assertTrue(self.spoken)
        self.assertIn("Przypomnienie", self.spoken[0])
        self.assertIn("leki", self.spoken[0])

    def test_social_unaffected(self):
        self.assertEqual(social_reply("co tam?"), "Spokojnie. Jestem, słucham.")
        reset_registry_for_tests()
        reset_model_client()
        reset_world_state()
        tmp = tempfile.TemporaryDirectory()
        try:
            mem = MemoryManager(Path(tmp.name) / "m.json")
            registry = build_default_registry(confirm_medium=False)
            planner_spoken = None
            from core.planner import Planner

            planner = Planner(registry, model=FakeModelClient([]))
            result = planner.run("co tam?")
            planner_spoken = result.message
            self.assertEqual(result.source, "social")
            self.assertNotIn(ACTIVE_WINDOW_CHANGED, planner_spoken)
            self.assertNotIn("VISION", planner_spoken)
        finally:
            tmp.cleanup()
            reset_registry_for_tests(None)

    def test_c51_compound_response_unchanged(self):
        spoken = summarize_compound_steps(
            successful_steps=[
                {
                    "tool": "open_app",
                    "semantic_action": "open_service",
                    "success": True,
                    "data": {"app": "Notatnik"},
                    "message": "Notatnik otwarte.",
                },
                {
                    "tool": "type_text",
                    "semantic_action": "type_text",
                    "success": True,
                    "message": "Gotowe.",
                },
                {
                    "tool": "select_all",
                    "semantic_action": "select_all",
                    "success": True,
                    "message": "Gotowe.",
                },
            ],
            failed_steps=[],
        )
        self.assertEqual(spoken, "Notatnik otwarty, tekst wpisany i zaznaczony.")
        wake = extract_wake(
            "Hej Elian, otwórz notatnik i wpisz Elian Działek Agent i zaznacz cały tekst."
        )
        self.assertIn("Elian Działek Agent", wake.raw_command)

    def test_idle_deactivate_does_not_flush_enums(self):
        self.busy = True
        self.pm.handle_event(self._evt(ACTIVE_WINDOW_CHANGED, data={"title": "Notatnik"}))
        manager = ConversationManager(
            registry=build_default_registry(confirm_medium=False),
            model=FakeModelClient([]),
        )
        manager.activate()
        manager.deactivate(reason="idle_timeout")
        self.busy = False
        self.pm.try_deliver()
        self.assertEqual(self.spoken, [])
        self.assertFalse(manager.active)

    def test_message_for_never_returns_event_type(self):
        event = self._evt(ACTIVE_WINDOW_CHANGED, data={"hwnd": 1, "title": "Notatnik"})
        self.assertEqual(self.pm.message_for(event), "")
        self.assertNotEqual(self.pm.message_for(event), event.type)


if __name__ == "__main__":
    unittest.main()
