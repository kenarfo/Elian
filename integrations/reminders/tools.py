# ==========================
# ELIAN — reminder tools (Phase 7)
# ==========================

from __future__ import annotations

from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from integrations.datetime_parse import now_local, parse_when
from integrations.reminders.store import get_reminder_store


def _create_reminder(arguments):
    text = str(arguments.get("text") or arguments.get("message") or "").strip()
    when_raw = str(
        arguments.get("when")
        or arguments.get("due_at")
        or arguments.get("in")
        or ""
    ).strip()
    if not text:
        return ToolResult.fail("O czym mam przypomnieć?", tool_name="create_reminder")
    due = parse_when(when_raw) if when_raw else None
    if due is None and when_raw:
        # try "za godzinę" already in parse; else fail
        return ToolResult.fail(
            "Nie zrozumiałem terminu przypomnienia.",
            tool_name="create_reminder",
            data={"error_code": "NOT_FOUND"},
        )
    if due is None:
        return ToolResult.fail(
            "Na kiedy mam ustawić przypomnienie?",
            tool_name="create_reminder",
        )
    if due <= now_local():
        return ToolResult.fail(
            "Termin przypomnienia musi być w przyszłości.",
            tool_name="create_reminder",
        )
    store = get_reminder_store()
    item = store.create(text, due)
    print(f"[INTEGRATION] reminders.create id={item.id} status=success")
    return ToolResult.ok(
        f"Przypomnę o „{text}” o {due.strftime('%H:%M')} ({due.strftime('%Y-%m-%d')}).",
        tool_name="create_reminder",
        data={
            "reminder_id": item.id,
            "text": item.text,
            "due_at": item.due_at,
        },
    )


def _list_reminders(_arguments):
    store = get_reminder_store()
    items = store.list(include_done=False)
    if not items:
        return ToolResult.ok(
            "Nie masz aktywnych przypomnień.",
            tool_name="list_reminders",
            data={"reminders": []},
        )
    lines = []
    for item in items[:10]:
        lines.append(f"{item.text} ({item.due_at})")
    return ToolResult.ok(
        "Przypomnienia: " + "; ".join(lines) + ".",
        tool_name="list_reminders",
        data={
            "reminders": [
                {"id": i.id, "text": i.text, "due_at": i.due_at} for i in items
            ]
        },
    )


def _cancel_reminder(arguments):
    reminder_id = str(arguments.get("reminder_id") or arguments.get("id") or "").strip()
    store = get_reminder_store()
    if not reminder_id:
        # cancel by text match
        query = str(arguments.get("text") or arguments.get("query") or "").strip().lower()
        matches = [
            r for r in store.list() if query and query in r.text.lower()
        ]
        if len(matches) == 1:
            reminder_id = matches[0].id
        elif len(matches) > 1:
            return ToolResult.fail(
                "Kilka pasujących przypomnień — podaj dokładniej.",
                tool_name="cancel_reminder",
                data={"error_code": "AMBIGUOUS"},
            )
        else:
            return ToolResult.fail(
                "Które przypomnienie anulować?",
                tool_name="cancel_reminder",
            )
    item = store.cancel(reminder_id)
    if not item:
        return ToolResult.fail(
            "Nie znalazłem takiego przypomnienia.",
            tool_name="cancel_reminder",
            data={"error_code": "NOT_FOUND"},
        )
    print(f"[INTEGRATION] reminders.cancel id={item.id} status=success")
    return ToolResult.ok(
        "Anulowałem przypomnienie.",
        tool_name="cancel_reminder",
        data={"reminder_id": item.id},
    )


def _update_reminder(arguments):
    reminder_id = str(arguments.get("reminder_id") or arguments.get("id") or "").strip()
    when_raw = str(arguments.get("when") or arguments.get("due_at") or "").strip()
    if not reminder_id:
        return ToolResult.fail(
            "Które przypomnienie zmienić?",
            tool_name="update_reminder",
        )
    due = parse_when(when_raw)
    if due is None:
        return ToolResult.fail(
            "Nie zrozumiałem nowego terminu.",
            tool_name="update_reminder",
        )
    store = get_reminder_store()
    item = store.update_due(reminder_id, due)
    if not item:
        return ToolResult.fail(
            "Nie znalazłem aktywnego przypomnienia.",
            tool_name="update_reminder",
            data={"error_code": "NOT_FOUND"},
        )
    return ToolResult.ok(
        f"Przesunąłem przypomnienie na {due.strftime('%H:%M')}.",
        tool_name="update_reminder",
        data={"reminder_id": item.id, "due_at": item.due_at},
    )


def register_reminder_tools(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="create_reminder",
        description=(
            "Create a local reminder. Pass text and when "
            "(e.g. 'za godzinę', 'jutro o 17', ISO datetime). "
            "Survives restart. Does not use MemoryManager."
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "message": {"type": "string"},
                "when": {"type": "string"},
                "due_at": {"type": "string"},
                "in": {"type": "string"},
            },
            "required": ["text", "when"],
        },
        risk_level=RiskLevel.SAFE,
        handler=_create_reminder,
    ))
    registry.register(Tool(
        name="list_reminders",
        description="List pending local reminders.",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_list_reminders,
    ))
    registry.register(Tool(
        name="cancel_reminder",
        description="Cancel a pending reminder by reminder_id or text query.",
        parameters={
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string"},
                "id": {"type": "string"},
                "text": {"type": "string"},
                "query": {"type": "string"},
            },
        },
        risk_level=RiskLevel.SAFE,
        handler=_cancel_reminder,
    ))
    registry.register(Tool(
        name="update_reminder",
        description="Update due time of a pending reminder by reminder_id.",
        parameters={
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string"},
                "id": {"type": "string"},
                "when": {"type": "string"},
                "due_at": {"type": "string"},
            },
            "required": ["reminder_id", "when"],
        },
        risk_level=RiskLevel.SAFE,
        handler=_update_reminder,
    ))
