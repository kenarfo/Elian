# ==========================
# ELIAN — Proactive reminders facade
# ==========================
"""Thin, fail-open bridge onto the existing ReminderStore + speech path.

Persistence lives in ``Data/reminders.json`` via
``integrations.reminders.store.ReminderStore`` — this module does not invent
a second store. ConversationManager / Runtime call ``poll_and_deliver`` for a
cheap due-check that speaks through ``handle_proactive_turn``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Status model mirrors ReminderStore: pending | delivered | cancelled | expired
STATUS_PENDING = "pending"
STATUS_DELIVERED = "delivered"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"


def _store(path: Optional[Path] = None):
    from integrations.reminders.store import ReminderStore, get_reminder_store

    if path is not None:
        return ReminderStore(path=path)
    return get_reminder_store()


def schedule_reminder(
    text: str,
    *,
    when_iso: str = "",
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Create a pending reminder. Returns a compact status dict (fail-open)."""
    try:
        from integrations.datetime_parse import ensure_aware, now_local

        body = str(text or "").strip()
        if not body:
            return {"ok": False, "error": "empty_text"}
        when_raw = str(when_iso or "").strip()
        if not when_raw:
            return {"ok": False, "error": "missing_when"}
        due = ensure_aware(datetime.fromisoformat(when_raw))
        if due <= now_local():
            return {"ok": False, "error": "not_in_future"}
        item = _store(path).create(body, due)
        return {
            "ok": True,
            "id": item.id,
            "text": item.text,
            "due_at": item.due_at,
            "status": item.status,
        }
    except Exception as exc:
        return {"ok": False, "error": f"schedule_failed:{exc}"}


def list_reminders(
    *,
    include_done: bool = False,
    path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    try:
        items = _store(path).list(include_done=include_done)
        return [
            {
                "id": r.id,
                "text": r.text,
                "due_at": r.due_at,
                "status": r.status,
                "delivered_at": r.delivered_at,
            }
            for r in items
        ]
    except Exception:
        return []


def pending_reminders(*, path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Active (pending) reminders — not necessarily due yet."""
    return list_reminders(include_done=False, path=path)


def due_reminders(
    *,
    now: Optional[datetime] = None,
    grace_seconds: float = 3600.0,
    path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Pending reminders that are due within the grace window."""
    try:
        items = _store(path).due_pending(now=now, grace_seconds=grace_seconds)
        return [
            {
                "id": r.id,
                "text": r.text,
                "due_at": r.due_at,
                "status": r.status,
            }
            for r in items
        ]
    except Exception:
        return []


def mark_delivered(reminder_id: str, *, path: Optional[Path] = None) -> bool:
    try:
        rid = str(reminder_id or "").strip()
        if not rid:
            return False
        store = _store(path)
        before = store.get(rid)
        if before is None or before.status != STATUS_PENDING:
            return False
        store.mark_delivered(rid)
        after = store.get(rid)
        return bool(after and after.status == STATUS_DELIVERED)
    except Exception:
        return False


def mark_cancelled(reminder_id: str, *, path: Optional[Path] = None) -> bool:
    try:
        item = _store(path).cancel(str(reminder_id or "").strip())
        return item is not None
    except Exception:
        return False


def compose_spoken(reminder: Dict[str, Any]) -> str:
    text = str((reminder or {}).get("text") or "").strip() or "przypomnienie"
    return f"Przypomnienie: {text}."


def poll_and_deliver(
    conversation_manager: Any,
    *,
    path: Optional[Path] = None,
    grace_seconds: float = 3600.0,
    max_deliveries: int = 3,
) -> List[Dict[str, Any]]:
    """
    Cheap due-check → speak via ConversationManager.handle_proactive_turn.

    Fail-open: any store/CM error returns []. Marks delivered only after a
    successful speak attempt so restarts can retry a failed delivery.
    """
    delivered: List[Dict[str, Any]] = []
    if conversation_manager is None:
        return delivered
    try:
        due = due_reminders(path=path, grace_seconds=grace_seconds)
    except Exception:
        return delivered
    if not due:
        return delivered

    speak = getattr(conversation_manager, "handle_proactive_turn", None)
    if not callable(speak):
        return delivered

    for item in due[: max(1, int(max_deliveries))]:
        rid = str(item.get("id") or "")
        spoken = compose_spoken(item)
        try:
            result = speak(
                spoken,
                source="proactive_reminder",
                data={
                    "reminder_id": rid,
                    "text": item.get("text") or "",
                    "due_at": item.get("due_at") or "",
                },
            )
        except Exception:
            continue
        # Treat any non-exception result as delivered (HandleResult or None)
        if mark_delivered(rid, path=path):
            delivered.append(
                {
                    "id": rid,
                    "text": item.get("text") or "",
                    "spoken": spoken,
                    "result_source": getattr(result, "source", "proactive_reminder"),
                }
            )
    return delivered
