# ==========================
# ELIAN — reminders store (Phase 7)
# Separate from MemoryManager facts.
# ==========================

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from Config import DATA_DIR
from integrations.datetime_parse import ensure_aware, now_local


DEFAULT_PATH = Path(DATA_DIR) / "reminders.json"


@dataclass
class Reminder:
    id: str
    text: str
    due_at: str  # ISO aware
    created_at: str
    status: str = "pending"  # pending | delivered | cancelled | expired
    delivered_at: str = ""

    def due_dt(self) -> datetime:
        return ensure_aware(datetime.fromisoformat(self.due_at))


class ReminderStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_PATH
        self._lock = threading.RLock()
        self._items: List[Reminder] = []
        self.load()

    def load(self) -> None:
        from core.persistence import safe_read_json

        with self._lock:
            self._items = []
            payload, recovered = safe_read_json(self.path, default={"reminders": []})
            if recovered:
                print(f"[REMINDERS] Recovered from backup/default: {self.path.name}")
            rows = payload.get("reminders", payload) if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                return
            for row in rows:
                if not isinstance(row, dict) or not row.get("id"):
                    continue
                self._items.append(
                    Reminder(
                        id=str(row["id"]),
                        text=str(row.get("text") or ""),
                        due_at=str(row.get("due_at") or ""),
                        created_at=str(row.get("created_at") or ""),
                        status=str(row.get("status") or "pending"),
                        delivered_at=str(row.get("delivered_at") or ""),
                    )
                )

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            payload = {
                "reminders": [asdict(item) for item in self._items],
            }
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.path)

    def create(self, text: str, due_at: datetime) -> Reminder:
        due_at = ensure_aware(due_at)
        item = Reminder(
            id=f"rem_{uuid.uuid4().hex[:10]}",
            text=str(text or "").strip(),
            due_at=due_at.isoformat(),
            created_at=now_local().isoformat(),
            status="pending",
        )
        with self._lock:
            self._items.append(item)
            self.save()
        return item

    def list(self, *, include_done: bool = False) -> List[Reminder]:
        with self._lock:
            if include_done:
                return list(self._items)
            return [r for r in self._items if r.status == "pending"]

    def get(self, reminder_id: str) -> Optional[Reminder]:
        with self._lock:
            for item in self._items:
                if item.id == reminder_id:
                    return item
        return None

    def cancel(self, reminder_id: str) -> Optional[Reminder]:
        with self._lock:
            for item in self._items:
                if item.id == reminder_id and item.status == "pending":
                    item.status = "cancelled"
                    self.save()
                    return item
        return None

    def update_due(self, reminder_id: str, due_at: datetime) -> Optional[Reminder]:
        with self._lock:
            for item in self._items:
                if item.id == reminder_id and item.status == "pending":
                    item.due_at = ensure_aware(due_at).isoformat()
                    self.save()
                    return item
        return None

    def mark_delivered(self, reminder_id: str) -> None:
        with self._lock:
            for item in self._items:
                if item.id == reminder_id:
                    item.status = "delivered"
                    item.delivered_at = now_local().isoformat()
                    self.save()
                    return

    def mark_expired(self, reminder_id: str) -> None:
        with self._lock:
            for item in self._items:
                if item.id == reminder_id:
                    item.status = "expired"
                    self.save()
                    return

    def due_pending(
        self,
        *,
        now: Optional[datetime] = None,
        grace_seconds: float = 3600,
    ) -> List[Reminder]:
        """Pending reminders that are due, within grace window after due time."""
        now = ensure_aware(now or now_local())
        due = []
        with self._lock:
            for item in self._items:
                if item.status != "pending":
                    continue
                when = item.due_dt()
                if when <= now:
                    late = (now - when).total_seconds()
                    if late <= grace_seconds:
                        due.append(item)
                    else:
                        item.status = "expired"
            self.save()
        return due


_store: Optional[ReminderStore] = None


def get_reminder_store(path: Optional[Path] = None) -> ReminderStore:
    global _store
    if path is not None:
        return ReminderStore(path=path)
    if _store is None:
        _store = ReminderStore()
    return _store


def reset_reminder_store_for_tests(store: Optional[ReminderStore] = None) -> None:
    global _store
    _store = store
