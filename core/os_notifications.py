# ==========================
# ELIAN — Windows notification listener
# ==========================
"""Best-effort toast access. Unpackaged Python often gets Denied — be honest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol
from uuid import uuid4

from core.jarvis_response import polish_spoken
from core.notification_inbox import InboxItem


@dataclass
class OsNotificationSnapshot:
    id: str
    app: str = ""
    title: str = ""
    body: str = ""
    sender: str = ""


class OsNotificationProvider(Protocol):
    def status(self) -> str:
        """available | denied | unavailable"""

    def list_recent(self, limit: int = 8) -> List[OsNotificationSnapshot]:
        ...


class FakeOsNotificationProvider:
    def __init__(self, items: List[OsNotificationSnapshot] | None = None, status: str = "available"):
        self._items = list(items or [])
        self._status = status

    def status(self) -> str:
        return self._status

    def list_recent(self, limit: int = 8) -> List[OsNotificationSnapshot]:
        if self._status != "available":
            return []
        return list(self._items[: max(1, int(limit or 8))])

    def push(self, item: OsNotificationSnapshot) -> None:
        self._items.insert(0, item)


class WinRTOsNotificationProvider:
    def status(self) -> str:
        listener = _listener()
        if listener is None:
            return "unavailable"
        try:
            access = str(getattr(listener, "get_access_status", lambda: "")() or "")
            lowered = access.lower()
            if "denied" in lowered:
                return "denied"
            if "unspecified" in lowered or "allowed" in lowered or access == "":
                return "available" if _can_list(listener) else "denied"
            return "available"
        except Exception:
            return "unavailable"

    def list_recent(self, limit: int = 8) -> List[OsNotificationSnapshot]:
        listener = _listener()
        if listener is None:
            return []
        try:
            rows = _list_from_listener(listener, max(1, int(limit or 8)))
        except Exception:
            return []
        return rows


_PROVIDER: OsNotificationProvider | None = None


def reset_os_provider_for_tests(provider: OsNotificationProvider | None = None) -> None:
    global _PROVIDER
    _PROVIDER = provider


def get_os_notification_provider() -> OsNotificationProvider:
    if _PROVIDER is not None:
        return _PROVIDER
    return WinRTOsNotificationProvider()


def snapshots_to_inbox(rows: List[OsNotificationSnapshot]) -> List[InboxItem]:
    items = []
    for row in rows:
        items.append(
            InboxItem(
                id=str(row.id or f"os_{uuid4().hex[:10]}"),
                source="windows",
                app=str(row.app or "").strip(),
                title=str(row.title or "").strip(),
                body=str(row.body or "").strip(),
                sender=str(row.sender or "").strip(),
            )
        )
    return items


def compose_inbox_spoken(items: List[InboxItem], *, access: str = "available") -> str:
    if access == "denied":
        return (
            "Windows nie daje mi dostępu do centrum powiadomień. "
            "Włącz nasłuch w ustawieniach powiadomień dla Pythona."
        )
    if access == "unavailable" and not items:
        return "Nie mam jeszcze dostępu do tostów Windows na tym komputerze."
    if not items:
        return "Nie widzę teraz nowych powiadomień."
    bits = []
    for item in items[:3]:
        who = item.sender or item.app or "aplikacja"
        preview = item.preview()
        if preview:
            bits.append(f"{who}: {preview}")
        else:
            bits.append(str(who))
    spoken = "Powiadomienia: " + "; ".join(bits) + "."
    return polish_spoken(spoken) or spoken


def compose_ask_spoken(item: InboxItem) -> str:
    who = item.sender or item.app or "aplikacja"
    preview = item.preview()
    if item.source == "twilio":
        if preview:
            return polish_spoken(f"Masz SMS od {who}: {preview}. Odczytać?") or ""
        return polish_spoken(f"Masz SMS od {who}. Odczytać?") or ""
    if preview:
        return polish_spoken(f"Masz powiadomienie z {who}: {preview}. Odczytać?") or ""
    return polish_spoken(f"Masz powiadomienie z {who}. Odczytać?") or ""


def compose_read_spoken(item: InboxItem) -> str:
    body = polish_spoken(str(item.body or item.title or "").strip())
    if body:
        return body if body.endswith(".") else body + "."
    return "Nie mam już treści tego powiadomienia."


def _listener():
    try:
        from winrt.windows.ui.notifications.management import UserNotificationListener

        return UserNotificationListener.current
    except Exception:
        return None


def _can_list(listener) -> bool:
    try:
        return bool(_list_from_listener(listener, 1) is not None)
    except Exception:
        return False


def _list_from_listener(listener, limit: int) -> List[OsNotificationSnapshot]:
    kinds = None
    try:
        from winrt.windows.ui.notifications import NotificationKinds

        kinds = NotificationKinds.TOAST
    except Exception:
        kinds = 1
    getter = getattr(listener, "get_notifications", None) or getattr(
        listener, "get_notifications_async", None
    )
    if getter is None:
        return []
    raw = getter(kinds)
    rows = list(raw or [])
    out: List[OsNotificationSnapshot] = []
    for note in rows[:limit]:
        app = ""
        title = ""
        body = ""
        try:
            app = str(getattr(getattr(note, "app_info", None), "display_info", None) or "")
        except Exception:
            app = ""
        try:
            binding = note.notification.visual.get_binding("ToastGeneric")
            texts = list(binding.get_text_elements() or [])
            if texts:
                title = str(getattr(texts[0], "text", "") or "")
            if len(texts) > 1:
                body = str(getattr(texts[1], "text", "") or "")
        except Exception:
            pass
        nid = str(getattr(note, "id", "") or f"os_{uuid4().hex[:10]}")
        out.append(OsNotificationSnapshot(id=nid, app=app, title=title, body=body))
    return out
