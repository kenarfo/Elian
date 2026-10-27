# ==========================
# ELIAN — proactive inbox / OS toasts / Twilio hook
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.builtin_tools import build_default_registry
from core.clock import FakeClock
from core.conversation_manager import ConversationManager
from core.event_bus import EventBus
from core.events import INCOMING_MESSAGE, Event, Priority
from core.fast_path import FastPathStatus, evaluate_fast_path
from core.model_client import ModelClient, reset_model_client
from core.notification_inbox import (
    InboxItem,
    note_inbox_item,
    peek_inbox,
    reset_inbox_for_tests,
)
from core.os_notifications import (
    FakeOsNotificationProvider,
    OsNotificationSnapshot,
    compose_ask_spoken,
    reset_os_provider_for_tests,
)
from core.proactive import ProactiveManager
from core.proactive_offer import get_proactive_offer, reset_offer_for_tests
from core.read_intents import looks_list_notifications, looks_phone_status
from core.semantic_executive import decide_semantics, fast_path_permitted
from core.tools import reset_registry_for_tests
from integrations.twilio_provider import send_sms, twilio_status_spoken
from memory.manager import MemoryManager
from watchers.os_notifications import OsNotificationWatcher
from watchers.twilio_sms import TwilioSmsWatcher


class BoomClient(ModelClient):
    def chat(self, messages, *, temperature=0.2, format=None, model=None) -> str:
        raise AssertionError("planner LLM must not run for inbox tests")


def _cm(registry=None):
    mem = MemoryManager(Path(tempfile.mkdtemp()) / "m.json")
    cm = ConversationManager(
        registry=registry or build_default_registry(),
        model=BoomClient(),
        memory_manager=mem,
    )
    cm.activate()
    return cm


class InboxSemanticTests(unittest.TestCase):
    def test_list_and_phone_intents(self):
        self.assertTrue(looks_list_notifications("Jakie mam powiadomienia?"))
        self.assertTrue(looks_list_notifications("Co mi przyszło?"))
        self.assertFalse(looks_list_notifications("włącz powiadomienia"))
        self.assertTrue(looks_phone_status("Czy masz numer telefonu?"))
        env = decide_semantics("Jakie mam powiadomienia?", allow_llm=False)
        self.assertEqual(env.intent, "list_notifications")
        self.assertTrue(fast_path_permitted("Jakie mam powiadomienia?", env))
        self.assertEqual(
            evaluate_fast_path("Jakie mam powiadomienia?", env).status,
            FastPathStatus.HANDLED_COMPLETE,
        )
        phone = decide_semantics("Czy masz numer telefonu?", allow_llm=False)
        self.assertEqual(phone.intent, "phone_status")


class InboxWatcherTests(unittest.TestCase):
    def setUp(self):
        reset_inbox_for_tests()
        reset_offer_for_tests()
        reset_os_provider_for_tests(None)

    def tearDown(self):
        reset_inbox_for_tests()
        reset_offer_for_tests()
        reset_os_provider_for_tests(None)

    def test_os_watcher_publishes_and_asks(self):
        clock = FakeClock()
        bus = EventBus(clock=clock)
        events = []
        bus.subscribe(lambda e: events.append(e))
        provider = FakeOsNotificationProvider(
            [
                OsNotificationSnapshot(
                    id="n1",
                    app="WhatsApp",
                    title="Ania",
                    body="Wracasz na kolację?",
                    sender="Ania",
                )
            ]
        )
        watcher = OsNotificationWatcher(bus, provider=provider, clock=clock)
        with patch.object(watcher, "enabled", return_value=True):
            watcher.start()
            watcher.poll_once()
        incoming = [e for e in events if e.type == INCOMING_MESSAGE]
        self.assertEqual(len(incoming), 1)
        self.assertEqual(peek_inbox()[0].app, "WhatsApp")

        spoken = []

        def _pref(key, default=None):
            if key in ("proactive.enabled", "proactive.os_notifications.enabled"):
                return "true"
            return default

        manager = ProactiveManager(clock=clock, speak_fn=spoken.append, preference_get=_pref)
        decision = manager.handle_event(incoming[0])
        self.assertEqual(decision.action, "notify")
        self.assertIn("Odczytać", spoken[0])
        offer = get_proactive_offer()
        self.assertIsNotNone(offer)
        self.assertEqual(offer.kind, "read_inbox")

    def test_twilio_watcher_uses_fake_list(self):
        clock = FakeClock()
        bus = EventBus(clock=clock)
        events = []
        bus.subscribe(lambda e: events.append(e))
        watcher = TwilioSmsWatcher(
            bus,
            clock=clock,
            list_fn=lambda: [{"id": "SM1", "sender": "+48111", "body": "Jestem pod sklepem."}],
        )
        watcher.start()
        watcher.poll_once()
        self.assertEqual(events[0].type, INCOMING_MESSAGE)
        self.assertEqual(events[0].source, "twilio")
        ask = compose_ask_spoken(peek_inbox()[0])
        self.assertIn("SMS", ask)
        self.assertIn("Odczytać", ask)

    def test_twilio_not_configured_is_honest(self):
        spoken = twilio_status_spoken()
        self.assertIn("Twilio", spoken)
        self.assertNotIn("+48", spoken)
        result = send_sms("+48111", "hej")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "NOT_CONFIGURED")


class InboxHandleTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        reset_inbox_for_tests()
        reset_offer_for_tests()
        reset_os_provider_for_tests(
            FakeOsNotificationProvider(
                [
                    OsNotificationSnapshot(
                        id="n2",
                        app="Messenger",
                        body="Widzimy się o 18?",
                        sender="Kuba",
                    )
                ]
            )
        )

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()
        reset_inbox_for_tests()
        reset_offer_for_tests()
        reset_os_provider_for_tests(None)

    def test_handle_list_and_offer_yes_no(self):
        note_inbox_item(
            InboxItem(
                id="n2",
                source="windows",
                app="Messenger",
                body="Widzimy się o 18?",
                sender="Kuba",
            )
        )
        registry = build_default_registry()
        reset_registry_for_tests(registry)
        cm = _cm(registry)
        listed = cm.handle("Jakie mam powiadomienia?")
        self.assertEqual(listed.source, "fast_path")
        self.assertIn("Kuba", listed.text)
        self.assertNotIn("Chwila", listed.text)
        self.assertNotIn("Nie znam narzędzia", listed.text)

        clock = FakeClock()
        spoken = []

        def _pref(key, default=None):
            if key in ("proactive.enabled", "proactive.os_notifications.enabled"):
                return "true"
            return default

        manager = ProactiveManager(clock=clock, speak_fn=spoken.append, preference_get=_pref)
        manager.handle_event(
            Event(
                type=INCOMING_MESSAGE,
                source="os_notifications",
                data={
                    "item_id": "n2",
                    "app": "Messenger",
                    "body": "Widzimy się o 18?",
                    "sender": "Kuba",
                    "user_value": True,
                },
                priority=Priority.NORMAL,
                timestamp=clock.time(),
                dedup_key="incoming:n2",
            )
        )
        yes = cm.handle("tak")
        self.assertEqual(yes.source, "proactive_offer")
        self.assertIn("18", yes.text)

        manager.handle_event(
            Event(
                type=INCOMING_MESSAGE,
                source="os_notifications",
                data={
                    "item_id": "n2",
                    "app": "Messenger",
                    "body": "Widzimy się o 18?",
                    "sender": "Kuba",
                    "user_value": True,
                },
                priority=Priority.NORMAL,
                timestamp=clock.time(),
                dedup_key="incoming:n2b",
            )
        )
        no = cm.handle("nie")
        self.assertEqual(no.source, "proactive_offer")
        self.assertIn("pomijam", no.text.lower())

    def test_phone_status_handle(self):
        registry = build_default_registry()
        reset_registry_for_tests(registry)
        cm = _cm(registry)
        result = cm.handle("Czy masz numer telefonu?")
        self.assertEqual(result.source, "fast_path")
        self.assertIn("Twilio", result.text)
        self.assertNotIn("Chwila", result.text)

    def test_czesc_not_stolen(self):
        registry = build_default_registry()
        reset_registry_for_tests(registry)
        cm = _cm(registry)
        env = decide_semantics("cześć", allow_llm=False)
        self.assertEqual(env.intent, "social_smalltalk")
        result = cm.handle("cześć")
        self.assertNotEqual(result.source, "fast_path")
        self.assertNotIn("Chwila", result.text)


if __name__ == "__main__":
    unittest.main()
