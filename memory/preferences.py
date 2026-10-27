"""User preferences — one active value per key."""

from __future__ import annotations

from typing import Any

from memory.store import clean_text, new_id, now_iso


class PreferencesStore:
    def __init__(self, preferences=None):
        # key -> preference record
        self._items: dict[str, dict[str, Any]] = {}
        for item in preferences or []:
            if isinstance(item, dict) and item.get("key"):
                self._items[str(item["key"])] = dict(item)

    def all(self):
        return [dict(item) for item in self._items.values()]

    def set(self, key, value, *, source="user", explicit=True, confidence=None):
        key = clean_text(str(key or ""), 80).replace(" ", ".")
        value = clean_text(str(value or ""), 200)
        if not key or not value:
            return None

        if confidence is None:
            confidence = 1.0 if explicit else 0.4
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 1.0 if explicit else 0.4

        existing = self._items.get(key)
        stamp = now_iso()
        if existing:
            old_conf = float(existing.get("confidence") or (1.0 if existing.get("explicit") else 0.5))
            same = existing.get("value", "").casefold() == value.casefold()
            if same:
                existing["updated_at"] = stamp
                existing["last_used_at"] = stamp
                existing["explicit"] = bool(explicit) or bool(existing.get("explicit"))
                existing["confidence"] = max(old_conf, confidence)
                existing["confirmations"] = int(existing.get("confirmations") or 1) + 1
                return dict(existing)
            # Weak observation must not overwrite a strong preference.
            if (not explicit) and old_conf >= 0.8 and confidence < old_conf - 0.15:
                existing["last_used_at"] = stamp
                return dict(existing)

        entry = {
            "id": (existing or {}).get("id") or new_id("preference"),
            "key": key,
            "value": value,
            "explicit": bool(explicit) or bool((existing or {}).get("explicit")),
            "source": clean_text(source, 40) or "user",
            "confidence": confidence if explicit else max(confidence, float((existing or {}).get("confidence") or 0)),
            "created_at": (existing or {}).get("created_at") or stamp,
            "updated_at": stamp,
            "last_used_at": stamp,
            "confirmations": int((existing or {}).get("confirmations") or 1),
        }
        if explicit:
            entry["confidence"] = max(confidence, 0.8)
        self._items[key] = entry
        return dict(entry)

    def get(self, key, default=None):
        key = clean_text(str(key or ""), 80).replace(" ", ".")
        item = self._items.get(key)
        if not item:
            return default
        item["last_used_at"] = now_iso()
        return item.get("value", default)

    def get_record(self, key):
        key = clean_text(str(key or ""), 80).replace(" ", ".")
        item = self._items.get(key)
        return dict(item) if item else None

    def forget(self, key_or_query):
        key = clean_text(str(key_or_query or ""), 80).replace(" ", ".")
        query = str(key_or_query or "").casefold()
        removed = []
        if key in self._items:
            removed.append(dict(self._items.pop(key)))
            return removed

        for item_key, item in list(self._items.items()):
            blob = f"{item_key} {item.get('value', '')}".casefold()
            if query and query in blob:
                removed.append(dict(self._items.pop(item_key)))
        return removed

    def as_dict(self):
        return {key: item.get("value") for key, item in self._items.items()}
