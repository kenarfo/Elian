# ==========================
# ELIAN — inbound Twilio SMS watcher
# ==========================

from __future__ import annotations

from typing import Callable, List, Optional, Set

from Config import get_config_value
from core.clock import Clock
from core.event_bus import EventBus
from core.events import INCOMING_MESSAGE, Priority, Event
from core.notification_inbox import InboxItem, note_inbox_item
from watchers.base import Watcher


class TwilioSmsWatcher(Watcher):
    name = "twilio_sms"
    interval_seconds = 30.0

    def __init__(
        self,
        bus: EventBus,
        *,
        clock: Optional[Clock] = None,
        list_fn: Optional[Callable[[], List[dict]]] = None,
    ):
        super().__init__(bus, clock=clock)
        self.list_fn = list_fn
        self._seen: Set[str] = set()
        block = get_config_value("integrations", "twilio", default={}) or {}
        if isinstance(block, dict):
            self.interval_seconds = float(block.get("poll_seconds", 30))

    def poll_once(self) -> None:
        rows: List[dict] = []
        try:
            if self.list_fn is not None:
                rows = list(self.list_fn() or [])
            else:
                from integrations.twilio_provider import list_inbound_sms, twilio_configured

                if not twilio_configured():
                    return
                rows = list_inbound_sms(limit=5)
        except Exception:
            return
        for raw in rows:
            item_id = str(raw.get("id") or "").strip()
            if not item_id or item_id in self._seen:
                continue
            self._seen.add(item_id)
            item = InboxItem(
                id=item_id,
                source="twilio",
                app="SMS",
                title="",
                body=str(raw.get("body") or "").strip(),
                sender=str(raw.get("sender") or "").strip(),
                created_at=self.clock.time(),
            )
            note_inbox_item(item)
            self.bus.publish(
                Event(
                    type=INCOMING_MESSAGE,
                    source="twilio",
                    data={
                        "item_id": item.id,
                        "app": "SMS",
                        "body": item.body,
                        "sender": item.sender,
                        "channel": "twilio",
                        "user_value": True,
                        "user_relevance": True,
                    },
                    priority=Priority.HIGH,
                    timestamp=self.clock.time(),
                    dedup_key=f"incoming:{item.id}",
                    ttl_seconds=1800,
                )
            )
