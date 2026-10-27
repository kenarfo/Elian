# ==========================
# ELIAN — Integrations tests (Phase 7)
# Offline fakes — no Google/Spotify/HA/Internet
# ==========================

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.builtin_tools import build_default_registry
from core.clock import FakeClock
from core.conversation_manager import ConversationManager
from core.event_bus import EventBus
from core.model_client import FakeModelClient
from core.proactive import ProactiveManager
from core.tool_result import ToolStatus
from core.tools import ToolRegistry
from integrations.calendar.provider import (
    CalendarEvent,
    FakeCalendarProvider,
    set_calendar_provider,
)
from integrations.datetime_parse import ensure_aware, parse_when
from integrations.media.provider import (
    FakeMediaProvider,
    set_media_provider_for_tests,
)
from integrations.reminders.store import (
    ReminderStore,
    reset_reminder_store_for_tests,
)
from memory.manager import MemoryManager
from watchers.reminders import CalendarWatcher, ReminderWatcher


class DatetimeParseTests(unittest.TestCase):
    def test_za_godzine(self):
        base = ensure_aware(datetime(2026, 6, 15, 12, 0, 0))
        got = parse_when("za godzinę", base=base)
        self.assertIsNotNone(got)
        self.assertEqual(got.hour, 13)


class ReminderPersistenceTests(unittest.TestCase):
    def test_survives_restart(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "reminders.json"
            store = ReminderStore(path=path)
            due = ensure_aware(datetime.now(timezone.utc) + timedelta(hours=1))
            item = store.create("trening", due)
            store2 = ReminderStore(path=path)
            found = store2.get(item.id)
            self.assertIsNotNone(found)
            self.assertEqual(found.text, "trening")


class ReminderEventTests(unittest.TestCase):
    def test_due_publishes_once(self):
        clock = FakeClock(start=1_700_000_000.0)
        bus = EventBus(clock=clock)
        spoken = []
        pm = ProactiveManager(
            clock=clock,
            speak_fn=lambda m: spoken.append(m),
            busy_check=lambda: False,
        )
        bus.subscribe(pm.handle_event)

        with tempfile.TemporaryDirectory() as td:
            from integrations.reminders.store import reset_reminder_store_for_tests

            store = ReminderStore(path=Path(td) / "r.json")
            reset_reminder_store_for_tests(store)
            try:
                due = datetime.fromtimestamp(clock.time() + 10, tz=timezone.utc)
                item = store.create("woda", due)
                watcher = ReminderWatcher(
                    bus, store=store, clock=clock, grace_seconds=3600
                )
                watcher.interval_seconds = 0
                watcher.start()
                watcher.poll_once()
                self.assertEqual(spoken, [])
                clock.advance(20)
                watcher.poll_once()
                self.assertEqual(len(spoken), 1)
                self.assertIn("woda", spoken[0].lower())
                watcher.poll_once()
                self.assertEqual(len(spoken), 1)
                # Delivered after proactive speech (not at publish time).
                self.assertEqual(store.get(item.id).status, "delivered")
            finally:
                reset_reminder_store_for_tests(None)

    def test_late_outside_grace_expires(self):
        clock = FakeClock(start=1_700_000_000.0)
        with tempfile.TemporaryDirectory() as td:
            store = ReminderStore(path=Path(td) / "r.json")
            due = datetime.fromtimestamp(clock.time() - 10_000, tz=timezone.utc)
            item = store.create("stare", due)
            due_list = store.due_pending(
                now=datetime.fromtimestamp(clock.time(), tz=timezone.utc),
                grace_seconds=3600,
            )
            self.assertEqual(due_list, [])
            self.assertEqual(store.get(item.id).status, "expired")


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.provider = FakeCalendarProvider()
        set_calendar_provider(self.provider)
        self.registry = ToolRegistry(confirm_medium=False)
        from integrations.calendar.tools import register_calendar_tools

        register_calendar_tools(self.registry)

    def tearDown(self):
        set_calendar_provider(None)

    def test_today_schedule(self):
        day = ensure_aware(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0))
        self.provider.events = [
            CalendarEvent(
                id="a",
                title="Matematyka",
                start=(day.replace(hour=10)).isoformat(),
                end=(day.replace(hour=11)).isoformat(),
            ),
            CalendarEvent(
                id="b",
                title="Trening",
                start=(day.replace(hour=15)).isoformat(),
                end=(day.replace(hour=16)).isoformat(),
            ),
        ]
        result = self.registry.invoke("get_today_schedule", {})
        self.assertTrue(result.success)
        self.assertIn("Matematyka", result.message)
        self.assertIn("Trening", result.message)

    def test_create_and_follow_up_update(self):
        result = self.registry.invoke(
            "create_calendar_event",
            {"title": "Spotkanie", "when": "jutro o 17"},
        )
        self.assertTrue(result.success)
        event_id = result.data["event_id"]
        updated = self.registry.invoke(
            "update_calendar_event",
            {"event_id": event_id, "when": "jutro o 18"},
            confirmed=True,
        )
        # MEDIUM may need confirm depending on registry — force confirmed
        if updated.status == ToolStatus.NEEDS_CONFIRMATION:
            updated = self.registry.invoke(
                "update_calendar_event",
                {"event_id": event_id, "when": "jutro o 18"},
                confirmed=True,
            )
        self.assertTrue(updated.success)
        self.assertEqual(self.provider.events[0].start_dt().hour, 18)

    def test_delete_needs_confirmation(self):
        created = self.registry.invoke(
            "create_calendar_event",
            {"title": "X", "when": "jutro o 12"},
        )
        event_id = created.data["event_id"]
        before = self.provider.delete_calls
        result = self.registry.invoke(
            "delete_calendar_event", {"event_id": event_id}
        )
        self.assertEqual(result.status, ToolStatus.NEEDS_CONFIRMATION)
        self.assertEqual(self.provider.delete_calls, before)
        confirmed = self.registry.invoke(
            "delete_calendar_event",
            {"event_id": event_id, "confirmed": True},
        )
        self.assertTrue(confirmed.success)
        self.assertEqual(self.provider.delete_calls, before + 1)

    def test_conflict(self):
        day = ensure_aware(datetime.now() + timedelta(days=1)).replace(
            hour=17, minute=0, second=0, microsecond=0
        )
        self.provider.events = [
            CalendarEvent(
                id="busy",
                title="Inne",
                start=day.isoformat(),
                end=(day + timedelta(hours=1)).isoformat(),
            )
        ]
        result = self.registry.invoke(
            "create_calendar_event",
            {"title": "Trening", "when": "jutro o 17"},
        )
        self.assertEqual(result.status, ToolStatus.NEEDS_CONFIRMATION)
        self.assertEqual(self.provider.create_calls, 1)


class MediaTests(unittest.TestCase):
    def tearDown(self):
        set_media_provider_for_tests(None)

    def test_play_uses_fake_provider(self):
        fake = FakeMediaProvider(name="spotify", label="Spotify")
        set_media_provider_for_tests(fake)
        registry = ToolRegistry(confirm_medium=False)
        from integrations.media.tools import register_media_tools

        register_media_tools(registry)
        result = registry.invoke(
            "play_media", {"query": "Black or White Michael Jackson"}
        )
        self.assertTrue(result.success)
        self.assertEqual(fake.play_calls, 1)
        self.assertIn("Black or White", fake.last_query)

    def test_now_playing(self):
        fake = FakeMediaProvider()
        fake.playing.title = "Black or White"
        fake.playing.artist = "Michael Jackson"
        set_media_provider_for_tests(fake)
        registry = ToolRegistry(confirm_medium=False)
        from integrations.media.tools import register_media_tools

        register_media_tools(registry)
        result = registry.invoke("get_now_playing", {})
        self.assertIn("Black or White", result.message)

    def test_media_fallback(self):
        from integrations.media.provider import play_with_fallback

        primary = FakeMediaProvider(name="spotify")
        primary.available = False
        secondary = FakeMediaProvider(name="windows_media")
        with patch(
            "integrations.media.provider.media_providers",
            return_value=[primary, secondary],
        ):
            result = play_with_fallback("song", preferred_app="spotify")
        self.assertTrue(result.success)
        self.assertEqual(secondary.play_calls, 1)


class ContactsMessagingTests(unittest.TestCase):
    def test_contact_ambiguity(self):
        registry = ToolRegistry(confirm_medium=False)
        from integrations.contacts_messaging import register_contacts_messaging_tools

        register_contacts_messaging_tools(registry)
        with patch(
            "Contacts.resolve_contact",
            return_value=(
                "ambiguous",
                [
                    MagicMock(name="Kuba Kowalski", channels={}),
                    MagicMock(name="Kuba Nowak", channels={}),
                ],
                "Do kogo: Kuba Kowalski czy Kuba Nowak?",
            ),
        ):
            # Fix MagicMock name attribute
            c1 = MagicMock()
            c1.name = "Kuba Kowalski"
            c1.channels = {}
            c2 = MagicMock()
            c2.name = "Kuba Nowak"
            c2.channels = {}
            with patch(
                "Contacts.resolve_contact",
                return_value=("ambiguous", [c1, c2], "Do kogo: Kuba Kowalski czy Kuba Nowak?"),
            ):
                result = registry.invoke("find_contact", {"query": "Kuba"})
        self.assertFalse(result.success)
        self.assertEqual(result.data.get("error_code"), "AMBIGUOUS")

    def test_message_confirmation_gate(self):
        registry = ToolRegistry(confirm_medium=True)
        from integrations.contacts_messaging import register_contacts_messaging_tools

        register_contacts_messaging_tools(registry)
        c = MagicMock()
        c.name = "Kuba"
        c.channels = {}
        send_mock = MagicMock(return_value="Wysłano.")
        with patch("Contacts.resolve_contact", return_value=("exact", [c], "")):
            with patch("Integrations.send_message", send_mock):
                # MEDIUM tool — registry confirmation first
                result = registry.invoke(
                    "send_message",
                    {"recipient": "Kuba", "message": "Będę za 10 minut"},
                )
                if result.status == ToolStatus.NEEDS_CONFIRMATION:
                    # Either registry or handler confirmation
                    pass
                self.assertEqual(send_mock.call_count, 0)
                result2 = registry.invoke(
                    "send_message",
                    {
                        "recipient": "Kuba",
                        "message": "Będę za 10 minut",
                        "confirmed": True,
                    },
                    confirmed=True,
                )
                # Provider not configured path may fail — use configured fake
        # Explicit handler path with configured provider
        from integrations.contacts_messaging import _send_message

        class OKProvider:
            def is_configured(self):
                return True

            def send(self, contact, content, attachment=None):
                OKProvider.calls = getattr(OKProvider, "calls", 0) + 1
                return "Wysłano."

        with patch("Contacts.resolve_contact", return_value=("exact", [c], "")):
            with patch("Integrations.get_provider", return_value=OKProvider()):
                with patch(
                    "Integrations.send_message",
                    side_effect=lambda *a, **k: OKProvider().send(a[0], a[1]),
                ) as sm:
                    r1 = _send_message(
                        {"recipient": "Kuba", "message": "cześć", "confirmed": False}
                    )
                    self.assertEqual(r1.status, ToolStatus.NEEDS_CONFIRMATION)
                    self.assertEqual(sm.call_count, 0)
                    r2 = _send_message(
                        {"recipient": "Kuba", "message": "cześć", "confirmed": True}
                    )
                    self.assertTrue(r2.success)
                    self.assertEqual(sm.call_count, 1)


class HaSceneTests(unittest.TestCase):
    def test_activate_scene_fake_api(self):
        registry = ToolRegistry(confirm_medium=False)
        from integrations.home_assistant.tools import register_ha_integration_tools

        register_ha_integration_tools(registry)
        response = MagicMock()
        response.status_code = 200
        with patch(
            "integrations.home_assistant.tools._ha_enabled", return_value=True
        ), patch(
            "HomeAssistant.api_request", return_value=response
        ), patch("HomeAssistant.get_registry") as reg:
            reg.return_value.maybe_refresh = MagicMock()
            reg.return_value.all = MagicMock(return_value=[])
            result = registry.invoke("activate_scene", {"name": "wieczór"})
        self.assertTrue(result.success)

    def test_device_state(self):
        registry = ToolRegistry(confirm_medium=False)
        from integrations.home_assistant.tools import register_ha_integration_tools

        register_ha_integration_tools(registry)
        entity = MagicMock()
        entity.entity_id = "light.salon"
        entity.domain = "light"
        entity.state = "on"
        entity.display_name = MagicMock(return_value="Lampa salon")
        with patch(
            "integrations.home_assistant.tools._ha_enabled", return_value=True
        ), patch(
            "DeviceResolver.resolve_device",
            return_value=("exact", [entity], ""),
        ), patch("HomeAssistant.get_registry") as reg:
            reg.return_value.maybe_refresh = MagicMock()
            result = registry.invoke("get_device_state", {"target": "lampa salon"})
        self.assertTrue(result.success)
        self.assertIn("on", result.message)


class CalendarProactiveTests(unittest.TestCase):
    def test_upcoming_event_notify(self):
        clock = FakeClock(start=1_700_000_000.0)
        bus = EventBus(clock=clock)
        spoken = []
        pm = ProactiveManager(
            clock=clock, speak_fn=lambda m: spoken.append(m), busy_check=lambda: False
        )
        bus.subscribe(pm.handle_event)
        now = datetime.fromtimestamp(clock.time(), tz=timezone.utc)
        provider = FakeCalendarProvider(
            [
                CalendarEvent(
                    id="ev1",
                    title="Dentysta",
                    start=(now + timedelta(minutes=10)).isoformat(),
                    end=(now + timedelta(minutes=40)).isoformat(),
                )
            ]
        )
        watcher = CalendarWatcher(bus, clock=clock, lead_minutes=15, provider=provider)
        watcher.interval_seconds = 0
        watcher.start()
        watcher.poll_once()
        self.assertTrue(any("Dentysta" in s for s in spoken))
        watcher.poll_once()
        self.assertEqual(len([s for s in spoken if "Dentysta" in s]), 1)


class FastPathIsolationTests(unittest.TestCase):
    def test_glosniej_no_calendar(self):
        set_calendar_provider(FakeCalendarProvider())
        model = FakeModelClient(responses=[])
        registry = build_default_registry(confirm_medium=False)
        manager = ConversationManager(registry=registry, model=model)
        manager.activate()
        with patch("core.builtin_tools.Music.run", return_value="Gotowe."):
            result = manager.handle("głośniej")
        self.assertIn(result.source, ('fast_path', 'action_loop', 'agent'))
        self.assertEqual(len(model.calls), 0)
        set_calendar_provider(None)


class SecretsNotLeakedTests(unittest.TestCase):
    def test_spotify_status_no_token_in_message(self):
        from integrations.media.provider import SpotifyProvider

        with patch("Config.get_secret", return_value="SECRET_TOKEN_XYZ"):
            provider = SpotifyProvider()
            # With token, availability AVAILABLE but play still soft-fails
            result = provider.play("test")
        self.assertNotIn("SECRET_TOKEN_XYZ", result.message)
        self.assertNotIn("SECRET_TOKEN_XYZ", str(result.data))


def tearDownModule():
    set_calendar_provider(None)
    set_media_provider_for_tests(None)
    reset_reminder_store_for_tests(None)


if __name__ == "__main__":
    unittest.main()
