# ==========================
# ELIAN — calendar tools (Phase 7)
# ==========================

from __future__ import annotations

from datetime import timedelta

from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from integrations.calendar.provider import get_calendar_provider
from integrations.datetime_parse import (
    day_bounds,
    ensure_aware,
    now_local,
    parse_duration_minutes,
    parse_when,
)
from integrations.base import CONFLICT


def _resolve_day(arguments):
    day_raw = str(arguments.get("day") or arguments.get("when") or "dziś").strip()
    if day_raw.lower() in ("dziś", "dzis", "today", ""):
        return now_local()
    if day_raw.lower() in ("jutro", "tomorrow"):
        return now_local() + timedelta(days=1)
    parsed = parse_when(day_raw)
    return parsed or now_local()


def _get_calendar_events(arguments):
    provider = get_calendar_provider()
    status = provider.availability()
    if status.value in ("not_configured", "disabled", "auth_required", "unavailable"):
        return ToolResult.fail(
            "Nie mam jeszcze połączonego kalendarza."
            if status.value == "not_configured"
            else f"Kalendarz niedostępny ({status.value}).",
            tool_name="get_calendar_events",
            data={"error_code": status.value.upper(), "availability": status.value},
        )
    day = _resolve_day(arguments)
    start, end = day_bounds(day)
    if arguments.get("start") and arguments.get("end"):
        s = parse_when(str(arguments["start"])) or start
        e = parse_when(str(arguments["end"])) or end
        start, end = ensure_aware(s), ensure_aware(e)
    result = provider.get_events(start, end)
    if not result.success:
        return ToolResult.fail(
            result.message,
            tool_name="get_calendar_events",
            data=result.to_tool_kwargs(),
        )
    return ToolResult.ok(
        result.message,
        tool_name="get_calendar_events",
        data=result.to_tool_kwargs(),
    )


def _get_today_schedule(arguments):
    arguments = dict(arguments or {})
    arguments["day"] = "dziś"
    return _get_calendar_events(arguments)


def _get_next_event(_arguments):
    provider = get_calendar_provider()
    if provider.availability().value != "available":
        return ToolResult.fail(
            "Nie mam jeszcze połączonego kalendarza.",
            tool_name="get_next_event",
            data={"error_code": "NOT_CONFIGURED"},
        )
    start = now_local()
    end = start + timedelta(days=7)
    result = provider.get_events(start, end)
    events = (result.data or {}).get("events") or []
    if not events:
        return ToolResult.ok(
            "Nie masz nadchodzących wydarzeń.",
            tool_name="get_next_event",
            data={"events": []},
        )
    first = events[0]
    return ToolResult.ok(
        f"Następne: {first.get('title')} o {first.get('start')}.",
        tool_name="get_next_event",
        data={"event": first, "event_id": first.get("id")},
    )


def _create_calendar_event(arguments):
    provider = get_calendar_provider()
    if provider.availability().value != "available":
        return ToolResult.fail(
            "Nie mam jeszcze połączonego kalendarza.",
            tool_name="create_calendar_event",
            data={"error_code": "NOT_CONFIGURED"},
        )
    title = str(arguments.get("title") or arguments.get("name") or "").strip()
    when_raw = str(arguments.get("start") or arguments.get("when") or "").strip()
    if not title:
        return ToolResult.fail("Jak nazwać wydarzenie?", tool_name="create_calendar_event")
    start = parse_when(when_raw)
    if start is None:
        return ToolResult.fail(
            "Nie zrozumiałem terminu wydarzenia.",
            tool_name="create_calendar_event",
        )
    duration = parse_duration_minutes(
        str(arguments.get("duration") or arguments.get("duration_minutes") or "60")
    )
    end_raw = str(arguments.get("end") or "").strip()
    end = parse_when(end_raw) if end_raw else start + timedelta(minutes=duration)
    allow = bool(arguments.get("allow_conflict") or arguments.get("confirmed"))
    result = provider.create_event(
        title,
        start,
        end,
        location=str(arguments.get("location") or ""),
        description=str(arguments.get("description") or ""),
        calendar=str(arguments.get("calendar") or ""),
        allow_conflict=allow,
    )
    if result.error_code == CONFLICT:
        return ToolResult.needs_confirmation(
            result.message,
            tool_name="create_calendar_event",
            data={
                **result.to_tool_kwargs(),
                "arguments": {
                    **arguments,
                    "allow_conflict": True,
                    "confirmed": True,
                },
            },
        )
    if not result.success:
        return ToolResult.fail(
            result.message, tool_name="create_calendar_event", data=result.to_tool_kwargs()
        )
    return ToolResult.ok(
        result.message,
        tool_name="create_calendar_event",
        data=result.to_tool_kwargs(),
    )


def _update_calendar_event(arguments):
    provider = get_calendar_provider()
    event_id = str(arguments.get("event_id") or arguments.get("id") or "").strip()
    if not event_id:
        return ToolResult.fail(
            "Które wydarzenie zmienić? Podaj event_id.",
            tool_name="update_calendar_event",
        )
    fields = {}
    if arguments.get("title"):
        fields["title"] = arguments["title"]
    if arguments.get("start") or arguments.get("when"):
        fields["start"] = parse_when(
            str(arguments.get("start") or arguments.get("when"))
        )
    if arguments.get("end"):
        fields["end"] = parse_when(str(arguments["end"]))
    if "location" in arguments:
        fields["location"] = arguments.get("location")
    # If only start moved, keep 1h duration if end missing
    if fields.get("start") and not fields.get("end"):
        fields["end"] = fields["start"] + timedelta(hours=1)
    result = provider.update_event(event_id, **fields)
    if not result.success:
        return ToolResult.fail(
            result.message, tool_name="update_calendar_event", data=result.to_tool_kwargs()
        )
    return ToolResult.ok(
        result.message, tool_name="update_calendar_event", data=result.to_tool_kwargs()
    )


def _delete_calendar_event(arguments):
    provider = get_calendar_provider()
    event_id = str(arguments.get("event_id") or arguments.get("id") or "").strip()
    if not event_id:
        return ToolResult.fail(
            "Które wydarzenie usunąć?",
            tool_name="delete_calendar_event",
        )
    confirmed = bool(arguments.get("confirmed", False))
    if not confirmed:
        return ToolResult.needs_confirmation(
            "Na pewno usunąć to wydarzenie z kalendarza?",
            tool_name="delete_calendar_event",
            data={
                "event_id": event_id,
                "arguments": {"event_id": event_id, "confirmed": True},
            },
        )
    result = provider.delete_event(event_id)
    if not result.success:
        return ToolResult.fail(
            result.message, tool_name="delete_calendar_event", data=result.to_tool_kwargs()
        )
    return ToolResult.ok(
        result.message, tool_name="delete_calendar_event", data=result.to_tool_kwargs()
    )


def register_calendar_tools(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="get_calendar_events",
        description=(
            "Read calendar events for a day or range. "
            "day: dziś/jutro or when phrase. Returns titles and times."
        ),
        parameters={
            "type": "object",
            "properties": {
                "day": {"type": "string"},
                "when": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
        },
        risk_level=RiskLevel.SAFE,
        handler=_get_calendar_events,
    ))
    registry.register(Tool(
        name="get_today_schedule",
        description="Shortcut: today's calendar schedule.",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_get_today_schedule,
    ))
    registry.register(Tool(
        name="get_next_event",
        description="Get the next upcoming calendar event.",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_get_next_event,
    ))
    registry.register(Tool(
        name="create_calendar_event",
        description=(
            "Create a calendar event. title + start/when required. "
            "Optional duration minutes, end, location. "
            "Conflicts may need confirmation (allow_conflict)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "name": {"type": "string"},
                "start": {"type": "string"},
                "when": {"type": "string"},
                "end": {"type": "string"},
                "duration": {"type": "string"},
                "duration_minutes": {"type": "integer"},
                "location": {"type": "string"},
                "description": {"type": "string"},
                "calendar": {"type": "string"},
                "allow_conflict": {"type": "boolean"},
                "confirmed": {"type": "boolean"},
            },
            "required": ["title", "when"],
        },
        risk_level=RiskLevel.SAFE,
        handler=_create_calendar_event,
    ))
    registry.register(Tool(
        name="update_calendar_event",
        description="Update calendar event by event_id (prefer ID from create/read).",
        parameters={
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "id": {"type": "string"},
                "title": {"type": "string"},
                "start": {"type": "string"},
                "when": {"type": "string"},
                "end": {"type": "string"},
                "location": {"type": "string"},
            },
            "required": ["event_id"],
        },
        risk_level=RiskLevel.MEDIUM,
        handler=_update_calendar_event,
        confirmation_prompt="Mam zmienić to wydarzenie w kalendarzu?",
    ))
    registry.register(Tool(
        name="delete_calendar_event",
        description="Delete calendar event by event_id. Requires confirmation.",
        parameters={
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "id": {"type": "string"},
                "confirmed": {"type": "boolean"},
            },
            "required": ["event_id"],
        },
        risk_level=RiskLevel.SAFE,  # semantic confirmation in handler
        handler=_delete_calendar_event,
    ))
