# ==========================
# ELIAN — Windows notification watcher
# ==========================

from __future__ import annotations

from typing import Optional, Set

from Config import get_config_value
from core.clock import Clock
from core.event_bus import EventBus
from core.events import INCOMING_MESSAGE, Priority, Event
from core.notification_inbox import note_inbox_item
from core.os_notifications import (
    get_os_notification_provider,
    snapshots_to_inbox,
)
from watchers.base import Watcher


class OsNotificationWatcher(Watcher):
    name = "os_notifications"
    interval_seconds = 8.0

    def __init__(
        self,
        bus: EventBus,
        *,
        provider=None,
        clock: Optional[Clock] = None,
    ):
        super().__init__(bus, clock=clock)
        self.provider = provider
        self._seen: Set[str] = set()
        cfg = get_config_value("proactive", "os_notifications", default={}) or {}
        if isinstance(cfg, dict):
            self.interval_seconds = float(cfg.get("poll_seconds", 8))

    def enabled(self) -> bool:
        try:
            from memory.manager import get_memory_manager

            pref = get_memory_manager().get_preference(
                "proactive.os_notifications.enabled",
                None,
            )
            if pref is not None:
                return str(pref).lower() in ("1", "true", "yes", "on", "tak")
        except Exception:
            pass
        cfg = get_config_value("proactive", "os_notifications", default={}) or {}
        if isinstance(cfg, dict) and "enabled" in cfg:
            return bool(cfg.get("enabled"))
        return False

    def poll_once(self) -> None:
        if not self.enabled():
            return
        provider = self.provider or get_os_notification_provider()
        if provider.status() != "available":
            return
        rows = provider.list_recent(limit=6)
        for item in snapshots_to_inbox(rows):
            if item.id in self._seen:
                continue
            self._seen.add(item.id)
            note_inbox_item(item)
            self.bus.publish(
                Event(
                    type=INCOMING_MESSAGE,
                    source="os_notifications",
                    data={
                        "item_id": item.id,
                        "app": item.app,
                        "title": item.title,
                        "body": item.body,
                        "sender": item.sender,
                        "channel": "windows",
                        "user_value": True,
                        "user_relevance": True,
                    },
                    priority=Priority.NORMAL,
                    timestamp=self.clock.time(),
                    dedup_key=f"incoming:{item.id}",
                    ttl_seconds=600,
                )
            )
