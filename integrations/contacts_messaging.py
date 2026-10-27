# ==========================
# ELIAN — contacts + messaging tools (Phase 7)
# ==========================

from __future__ import annotations

from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from integrations.base import AMBIGUOUS, NOT_CONFIGURED, NOT_FOUND, integration_enabled


def _find_contact(arguments):
    query = str(arguments.get("query") or arguments.get("name") or "").strip()
    if not query:
        return ToolResult.fail("Kogo mam znaleźć?", tool_name="find_contact")
    from Contacts import resolve_contact

    status, matches, message = resolve_contact(query)
    if status == "ambiguous":
        return ToolResult.fail(
            message,
            tool_name="find_contact",
            data={
                "error_code": AMBIGUOUS,
                "candidates": [
                    {"name": c.name, "channels": dict(c.channels)} for c in matches
                ],
            },
        )
    if status != "exact" or not matches:
        return ToolResult.fail(
            message or "Nie mam takiego kontaktu.",
            tool_name="find_contact",
            data={"error_code": NOT_FOUND},
        )
    contact = matches[0]
    return ToolResult.ok(
        f"Znalazłem kontakt: {contact.name}.",
        tool_name="find_contact",
        data={
            "name": contact.name,
            "aliases": list(contact.aliases),
            "channels": dict(contact.channels),
            "provider_id": contact.name,
        },
    )


def _send_message(arguments):
    if not integration_enabled("messaging"):
        return ToolResult.fail(
            "Wiadomości są wyłączone w konfiguracji.",
            tool_name="send_message",
            data={"error_code": "DISABLED"},
        )
    recipient = str(
        arguments.get("recipient") or arguments.get("contact") or arguments.get("name") or ""
    ).strip()
    content = str(arguments.get("message") or arguments.get("text") or "").strip()
    channel = str(arguments.get("channel") or "messaging").strip()
    confirmed = bool(arguments.get("confirmed", False))
    if not recipient or not content:
        return ToolResult.fail(
            "Podaj odbiorcę i treść wiadomości.",
            tool_name="send_message",
        )

    from Contacts import resolve_contact

    status, matches, message = resolve_contact(recipient)
    if status == "ambiguous":
        return ToolResult.fail(
            message,
            tool_name="send_message",
            data={"error_code": AMBIGUOUS},
        )
    contact = matches[0] if status == "exact" and matches else None
    display = contact.name if contact else recipient

    if not confirmed:
        return ToolResult.needs_confirmation(
            f"Wyślę do {display}: «{content}». Wysłać?",
            tool_name="send_message",
            data={
                "recipient": display,
                "message": content,
                "arguments": {
                    "recipient": display,
                    "message": content,
                    "channel": channel,
                    "confirmed": True,
                },
            },
        )

    from Integrations import send_message as legacy_send

    result_text = legacy_send(display, content, channel=channel)
    # Legacy returns Polish string; treat "nie mam jeszcze" as not configured
    if "nie mam jeszcze" in result_text.lower() or "nie skonfigurow" in result_text.lower():
        return ToolResult.fail(
            result_text,
            tool_name="send_message",
            data={"error_code": NOT_CONFIGURED},
        )
    if result_text.lower().startswith("błąd") or "nie udało" in result_text.lower():
        return ToolResult.fail(
            result_text,
            tool_name="send_message",
            data={"error_code": "TEMPORARY_FAILURE"},
            retryable=False,
        )
    print("[INTEGRATION] messaging.send status=success")
    return ToolResult.ok(
        result_text if result_text else "Wiadomość wysłana.",
        tool_name="send_message",
        data={"recipient": display, "sent": True},
    )


def register_contacts_messaging_tools(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="find_contact",
        description="Find a contact by name. Returns AMBIGUOUS if multiple matches.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["query"],
        },
        risk_level=RiskLevel.SAFE,
        handler=_find_contact,
    ))
    registry.register(Tool(
        name="send_message",
        description=(
            "Send a message to a contact. Requires confirmation. "
            "Does not pretend success when messaging is not configured."
        ),
        parameters={
            "type": "object",
            "properties": {
                "recipient": {"type": "string"},
                "contact": {"type": "string"},
                "name": {"type": "string"},
                "message": {"type": "string"},
                "text": {"type": "string"},
                "channel": {"type": "string"},
                "confirmed": {"type": "boolean"},
            },
            "required": ["recipient", "message"],
        },
        risk_level=RiskLevel.MEDIUM,
        handler=_send_message,
        confirmation_prompt="Mam wysłać tę wiadomość?",
        timeout=30,
    ))
