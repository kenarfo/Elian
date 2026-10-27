# ==========================
# ELIAN — calendar providers (Phase 7)
# ==========================

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from Config import DATA_DIR, config
from integrations.base import (
    Availability,
    IntegrationProvider,
    IntegrationResult,
    CONFLICT,
    NOT_CONFIGURED,
    NOT_FOUND,
    integration_enabled,
)
from integrations.datetime_parse import day_bounds, ensure_aware, now_local


@dataclass
class CalendarEvent:
    id: str
    title: str
    start: str
    end: str
    location: str = ""
    description: str = ""
    calendar: str = "default"

    def start_dt(self) -> datetime:
        return ensure_aware(datetime.fromisoformat(self.start))

    def end_dt(self) -> datetime:
        return ensure_aware(datetime.fromisoformat(self.end))


class CalendarProvider(IntegrationProvider):
    name = "calendar"
    label = "kalendarz"

    def get_events(
        self, start: datetime, end: datetime, *, calendar: str = ""
    ) -> IntegrationResult:
        raise NotImplementedError

    def create_event(
        self,
        title: str,
        start: datetime,
        end: datetime,
        *,
        location: str = "",
        description: str = "",
        calendar: str = "",
        allow_conflict: bool = False,
    ) -> IntegrationResult:
        raise NotImplementedError

    def update_event(self, event_id: str, **fields) -> IntegrationResult:
        raise NotImplementedError

    def delete_event(self, event_id: str) -> IntegrationResult:
        raise NotImplementedError

    def find_conflicts(
        self, start: datetime, end: datetime, *, calendar: str = ""
    ) -> List[CalendarEvent]:
        return []


class FakeCalendarProvider(CalendarProvider):
    def __init__(self, events: Optional[List[CalendarEvent]] = None):
        self.events: List[CalendarEvent] = list(events or [])
        self.delete_calls = 0
        self.create_calls = 0
        self._available = Availability.AVAILABLE

    def availability(self) -> Availability:
        return self._available

    def get_events(self, start, end, *, calendar: str = ""):
        start, end = ensure_aware(start), ensure_aware(end)
        items = [
            e
            for e in self.events
            if e.start_dt() < end and e.end_dt() > start
        ]
        items.sort(key=lambda e: e.start)
        return IntegrationResult(
            True,
            _format_events(items),
            data={"events": [asdict(e) for e in items]},
        )

    def find_conflicts(self, start, end, *, calendar: str = ""):
        start, end = ensure_aware(start), ensure_aware(end)
        return [
            e
            for e in self.events
            if e.start_dt() < end and e.end_dt() > start
        ]

    def create_event(
        self,
        title,
        start,
        end,
        *,
        location="",
        description="",
        calendar="",
        allow_conflict=False,
    ):
        self.create_calls += 1
        conflicts = self.find_conflicts(start, end)
        if conflicts and not allow_conflict:
            return IntegrationResult(
                False,
                f"Masz wtedy inne wydarzenie: {conflicts[0].title}. Dodać mimo to?",
                data={
                    "error_code": CONFLICT,
                    "conflicts": [asdict(c) for c in conflicts],
                    "pending": {
                        "title": title,
                        "start": ensure_aware(start).isoformat(),
                        "end": ensure_aware(end).isoformat(),
                    },
                },
                error_code=CONFLICT,
            )
        event = CalendarEvent(
            id=f"cal_{uuid.uuid4().hex[:10]}",
            title=title,
            start=ensure_aware(start).isoformat(),
            end=ensure_aware(end).isoformat(),
            location=location,
            description=description,
            calendar=calendar or "default",
        )
        self.events.append(event)
        return IntegrationResult(
            True,
            f"Dodałem: {title}.",
            data={"event_id": event.id, "event": asdict(event)},
        )

    def update_event(self, event_id, **fields):
        for event in self.events:
            if event.id != event_id:
                continue
            if "title" in fields and fields["title"]:
                event.title = str(fields["title"])
            if "start" in fields and fields["start"]:
                event.start = ensure_aware(fields["start"]).isoformat()
            if "end" in fields and fields["end"]:
                event.end = ensure_aware(fields["end"]).isoformat()
            if "location" in fields:
                event.location = str(fields.get("location") or "")
            return IntegrationResult(
                True,
                "Zaktualizowałem wydarzenie.",
                data={"event_id": event.id, "event": asdict(event)},
            )
        return IntegrationResult(
            False,
            "Nie znalazłem wydarzenia.",
            error_code=NOT_FOUND,
            data={"error_code": NOT_FOUND},
        )

    def delete_event(self, event_id):
        self.delete_calls += 1
        before = len(self.events)
        self.events = [e for e in self.events if e.id != event_id]
        if len(self.events) == before:
            return IntegrationResult(
                False, "Nie znalazłem wydarzenia.", error_code=NOT_FOUND
            )
        return IntegrationResult(True, "Usunąłem wydarzenie.", data={"event_id": event_id})


class LocalCalendarProvider(CalendarProvider):
    """File-backed local calendar — works offline without Google."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else Path(DATA_DIR) / "calendar.json"
        self._lock = threading.RLock()
        self.events: List[CalendarEvent] = []
        self.load()

    def availability(self) -> Availability:
        if not integration_enabled("calendar"):
            return Availability.DISABLED
        return Availability.AVAILABLE

    def load(self):
        from core.persistence import safe_read_json

        with self._lock:
            self.events = []
            payload, recovered = safe_read_json(self.path, default={"events": []})
            if recovered:
                print(f"[CALENDAR] Recovered from backup/default: {self.path.name}")
            rows = payload.get("events", []) if isinstance(payload, dict) else []
            for row in rows:
                if isinstance(row, dict) and row.get("id"):
                    self.events.append(CalendarEvent(**{
                        k: row.get(k, "")
                        for k in (
                            "id",
                            "title",
                            "start",
                            "end",
                            "location",
                            "description",
                            "calendar",
                        )
                    }))

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(
                    {"events": [asdict(e) for e in self.events]},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp.replace(self.path)

    def get_events(self, start, end, *, calendar: str = ""):
        start, end = ensure_aware(start), ensure_aware(end)
        items = [
            e
            for e in self.events
            if e.start_dt() < end and e.end_dt() > start
        ]
        items.sort(key=lambda e: e.start)
        return IntegrationResult(
            True,
            _format_events(items),
            data={"events": [asdict(e) for e in items]},
        )

    def find_conflicts(self, start, end, *, calendar: str = ""):
        start, end = ensure_aware(start), ensure_aware(end)
        return [
            e
            for e in self.events
            if e.start_dt() < end and e.end_dt() > start
        ]

    def create_event(
        self,
        title,
        start,
        end,
        *,
        location="",
        description="",
        calendar="",
        allow_conflict=False,
    ):
        conflicts = self.find_conflicts(start, end)
        if conflicts and not allow_conflict:
            return IntegrationResult(
                False,
                f"Masz wtedy inne wydarzenie: {conflicts[0].title}. Dodać mimo to?",
                data={
                    "error_code": CONFLICT,
                    "conflicts": [asdict(c) for c in conflicts],
                    "pending": {
                        "title": title,
                        "start": ensure_aware(start).isoformat(),
                        "end": ensure_aware(end).isoformat(),
                        "location": location,
                        "description": description,
                    },
                },
                error_code=CONFLICT,
            )
        event = CalendarEvent(
            id=f"cal_{uuid.uuid4().hex[:10]}",
            title=title,
            start=ensure_aware(start).isoformat(),
            end=ensure_aware(end).isoformat(),
            location=location,
            description=description,
            calendar=calendar or "default",
        )
        with self._lock:
            self.events.append(event)
            self.save()
        print(f"[INTEGRATION] calendar.create event_id={event.id} status=success")
        return IntegrationResult(
            True,
            f"Dodałem: {title}.",
            data={"event_id": event.id, "event": asdict(event)},
        )

    def update_event(self, event_id, **fields):
        with self._lock:
            for event in self.events:
                if event.id != event_id:
                    continue
                if fields.get("title"):
                    event.title = str(fields["title"])
                if fields.get("start"):
                    event.start = ensure_aware(fields["start"]).isoformat()
                if fields.get("end"):
                    event.end = ensure_aware(fields["end"]).isoformat()
                if "location" in fields:
                    event.location = str(fields.get("location") or "")
                self.save()
                print(f"[INTEGRATION] calendar.update event_id={event.id} status=success")
                return IntegrationResult(
                    True,
                    "Zaktualizowałem wydarzenie.",
                    data={"event_id": event.id, "event": asdict(event)},
                )
        return IntegrationResult(
            False, "Nie znalazłem wydarzenia.", error_code=NOT_FOUND
        )

    def delete_event(self, event_id):
        with self._lock:
            before = len(self.events)
            self.events = [e for e in self.events if e.id != event_id]
            if len(self.events) == before:
                return IntegrationResult(
                    False, "Nie znalazłem wydarzenia.", error_code=NOT_FOUND
                )
            self.save()
        print(f"[INTEGRATION] calendar.delete event_id={event_id} status=success")
        return IntegrationResult(
            True, "Usunąłem wydarzenie.", data={"event_id": event_id}
        )


def _format_events(items: List[CalendarEvent]) -> str:
    if not items:
        return "Brak wydarzeń w tym okresie."
    parts = []
    for event in items:
        t = event.start_dt().strftime("%H:%M")
        parts.append(f"{t} {event.title}")
    return "Masz: " + "; ".join(parts) + "."


_calendar: Optional[CalendarProvider] = None


def get_calendar_provider() -> CalendarProvider:
    global _calendar
    if _calendar is not None:
        return _calendar
    if not integration_enabled("calendar"):
        provider = FakeCalendarProvider()
        provider._available = Availability.DISABLED
        return provider
    # Prefer local file calendar; Google/etc. later via config provider name
    provider_name = ""
    block = config.get("integrations", {})
    if isinstance(block, dict):
        cal = block.get("calendar", {})
        if isinstance(cal, dict):
            provider_name = str(cal.get("provider", "local"))
    if provider_name in ("fake", "test"):
        _calendar = FakeCalendarProvider()
    else:
        _calendar = LocalCalendarProvider()
    return _calendar


def set_calendar_provider(provider: Optional[CalendarProvider]) -> None:
    global _calendar
    _calendar = provider
