# ==========================
# ELIAN — reminder + calendar watchers (Phase 7)
# ==========================

from __future__ import annotations

from typing import Optional, Set

from Config import config
from core.clock import Clock
from core.event_bus import EventBus
from core.events import (
    CALENDAR_UPCOMING,
    REMINDER_DUE,
    Priority,
    Event,
)
from integrations.datetime_parse import ensure_aware, now_local
from integrations.reminders.store import ReminderStore, get_reminder_store
from watchers.base import Watcher


class ReminderWatcher(Watcher):
    name = "reminders"
    interval_seconds = 15.0

    def __init__(
        self,
        bus: EventBus,
        *,
        store: Optional[ReminderStore] = None,
        clock: Optional[Clock] = None,
        grace_seconds: float = 3600,
    ):
        super().__init__(bus, clock=clock)
        cfg = config.get("integrations", {})
        rem = cfg.get("reminders", {}) if isinstance(cfg, dict) else {}
        if isinstance(rem, dict):
            self.interval_seconds = float(rem.get("poll_seconds", 15))
            self.grace_seconds = float(rem.get("grace_seconds", grace_seconds))
        else:
            self.grace_seconds = grace_seconds
        self.store = store or get_reminder_store()
        self._emitted: Set[str] = set()

    def poll_once(self) -> None:
        # Use clock for tests when FakeClock is injected via store due check
        now = ensure_aware(now_local())
        # Allow clock override through event timestamp path
        try:
            from datetime import datetime, timezone

            now = datetime.fromtimestamp(self.clock.time(), tz=now.tzinfo)
        except Exception:
            pass
        due = self.store.due_pending(now=now, grace_seconds=self.grace_seconds)
        for item in due:
            if item.id in self._emitted:
                continue
            self._emitted.add(item.id)
            self.bus.publish(
                Event(
                    type=REMINDER_DUE,
                    source="reminders",
                    data={"reminder_id": item.id, "text": item.text, "due_at": item.due_at},
                    priority=Priority.HIGH,
                    timestamp=self.clock.time(),
                    dedup_key=f"reminder_due:{item.id}",
                    ttl_seconds=1800,
                )
            )
            # Delivered status is set after successful proactive speech
            # (core.proactive / proactive_reminders) so quiet-hours drops retry.


class CalendarWatcher(Watcher):
    name = "calendar"
    interval_seconds = 60.0

    def __init__(
        self,
        bus: EventBus,
        *,
        clock: Optional[Clock] = None,
        lead_minutes: int = 15,
        provider=None,
    ):
        super().__init__(bus, clock=clock)
        cfg = config.get("integrations", {})
        cal = cfg.get("calendar", {}) if isinstance(cfg, dict) else {}
        if isinstance(cal, dict):
            self.interval_seconds = float(cal.get("poll_seconds", 60))
            self.lead_minutes = int(cal.get("upcoming_minutes", lead_minutes))
        else:
            self.lead_minutes = lead_minutes
        self.provider = provider
        self._emitted: Set[str] = set()

    def poll_once(self) -> None:
        from datetime import timedelta

        from integrations.calendar.provider import get_calendar_provider

        provider = self.provider or get_calendar_provider()
        if provider.availability().value != "available":
            return
        now = ensure_aware(now_local())
        try:
            from datetime import datetime

            now = datetime.fromtimestamp(self.clock.time(), tz=now.tzinfo)
        except Exception:
            pass
        window_end = now + timedelta(minutes=self.lead_minutes)
        result = provider.get_events(now, window_end + timedelta(minutes=1))
        events = (result.data or {}).get("events") or []
        for row in events:
            event_id = str(row.get("id") or "")
            if not event_id:
                continue
            start = ensure_aware(
                __import__("datetime").datetime.fromisoformat(row["start"])
            )
            delta_min = (start - now).total_seconds() / 60.0
            if delta_min < 0 or delta_min > self.lead_minutes:
                continue
            key = f"calendar_upcoming:{event_id}:{self.lead_minutes}m"
            if key in self._emitted:
                continue
            self._emitted.add(key)
            self.bus.publish(
                Event(
                    type=CALENDAR_UPCOMING,
                    source="calendar",
                    data={
                        "event_id": event_id,
                        "title": row.get("title"),
                        "start": row.get("start"),
                        "minutes": int(max(0, delta_min)),
                    },
                    priority=Priority.NORMAL,
                    timestamp=self.clock.time(),
                    dedup_key=key,
                    ttl_seconds=900,
                )
            )
