# ==========================
# ELIAN — in-session notification inbox
# ==========================
"""Process-local inbox for OS toasts and inbound SMS. Not a post-exit timeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from core.jarvis_response import polish_spoken

_MAX = 24
_items: List["InboxItem"] = []


@dataclass
class InboxItem:
    id: str
    source: str
    app: str = ""
    title: str = ""
    body: str = ""
    sender: str = ""
    created_at: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def preview(self) -> str:
        text = str(self.body or self.title or "").strip()
        text = polish_spoken(text) or text
        if len(text) > 80:
            clipped = text[:77].rsplit(" ", 1)[0].strip()
            return clipped or text[:77]
        return text

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def reset_inbox_for_tests() -> None:
    _items.clear()


def note_inbox_item(item: InboxItem) -> InboxItem:
    existing = {row.id for row in _items}
    if item.id in existing:
        return item
    _items.append(item)
    del _items[:-_MAX]
    return item


def peek_inbox(limit: int = 8) -> List[InboxItem]:
    cap = max(1, int(limit or 8))
    return list(_items[-cap:])


def get_inbox_item(item_id: str) -> Optional[InboxItem]:
    for row in reversed(_items):
        if row.id == item_id:
            return row
    return None


def consume_inbox_item(item_id: str) -> Optional[InboxItem]:
    for index, row in enumerate(_items):
        if row.id == item_id:
            return _items.pop(index)
    return None
