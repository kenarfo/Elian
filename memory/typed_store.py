# ==========================
# ELIAN — Memory 2.0 typed store
# ==========================
"""Unified entries with confidence, expiry, conflict, and cleanup candidates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional, Sequence

from memory.store import clean_text, is_expired, new_id, now_iso, parse_ts, query_terms, fold_pl
from memory.types import (
    CONFIDENCE_EXPLICIT,
    PERSISTENT_TYPES,
    STRONG_CONFIDENCE,
    WEAK_MARGIN,
    MemoryType,
    WriteResult,
    WriteStatus,
    clamp01,
    default_importance,
    normalize_type,
)

STALE_DAYS = 45
LOW_VALUE_IMPORTANCE = 0.45
LOW_VALUE_CONFIDENCE = 0.55
CLEANUP_MIN_AGE_DAYS = 14


def _now_iso(now_fn: Callable[[], str] | None) -> str:
    if now_fn:
        return str(now_fn())
    return now_iso()


def _norm_key(key) -> str:
    return clean_text(str(key or ""), 120).replace(" ", ".").casefold()


def _norm_value(value) -> str:
    return clean_text(str(value or ""), 500).casefold()


def _display_text(entry: dict[str, Any]) -> str:
    text = clean_text(str(entry.get("text") or ""), 500)
    if text:
        return text
    key = str(entry.get("key") or "").strip()
    value = str(entry.get("value") or "").strip()
    if key and value:
        return f"{key}: {value}"
    return value or key


class TypedMemoryStore:
    def __init__(self, entries=None, *, now_fn: Callable[[], str] | None = None):
        self.now_fn = now_fn
        self._items: list[dict[str, Any]] = []
        for item in entries or []:
            normalized = self._normalize_record(item)
            if normalized:
                self._items.append(normalized)

    def _stamp(self) -> str:
        return _now_iso(self.now_fn)

    def _normalize_record(self, item) -> Optional[dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        kind = normalize_type(item.get("type"))
        key = clean_text(str(item.get("key") or item.get("subject") or ""), 120)
        value = clean_text(str(item.get("value") or item.get("text") or ""), 500)
        if not key and not value:
            return None
        if not key:
            key = _norm_key(value)[:80] or "note"
        stamp = self._stamp()
        persistent = kind in PERSISTENT_TYPES and kind != MemoryType.SESSION
        if "persistent" in item:
            persistent = bool(item.get("persistent")) and kind != MemoryType.SESSION
        entry = {
            "id": str(item.get("id") or new_id(kind.value.lower())),
            "type": kind.value,
            "key": key,
            "subject": key,
            "value": value,
            "source": clean_text(str(item.get("source") or "user"), 40) or "user",
            "confidence": clamp01(item.get("confidence"), CONFIDENCE_EXPLICIT),
            "created_at": str(item.get("created_at") or stamp),
            "updated_at": str(item.get("updated_at") or stamp),
            "last_used_at": str(item.get("last_used_at") or item.get("updated_at") or stamp),
            "expires_at": item.get("expires_at") or None,
            "importance": clamp01(item.get("importance"), default_importance(kind)),
            "provenance": dict(item.get("provenance") or {}),
            "text": clean_text(str(item.get("text") or value), 500),
            "tags": [
                clean_text(tag, 40)
                for tag in (item.get("tags") or [])
                if clean_text(str(tag), 40)
            ],
            "explicit": bool(item.get("explicit", True)),
            "confirmations": max(1, int(item.get("confirmations") or 1)),
            "status": str(item.get("status") or "active"),
            "persistent": persistent,
        }
        if not entry["text"]:
            entry["text"] = _display_text(entry)
        return entry

    def all(self, *, include_session=False, include_expired=False) -> list[dict[str, Any]]:
        now = self._stamp()
        out = []
        for item in self._items:
            kind = normalize_type(item.get("type"))
            if kind == MemoryType.SESSION and not include_session:
                continue
            if not include_expired and is_expired(item.get("expires_at"), now=now):
                continue
            out.append(dict(item))
        return out

    def persistent_dump(self) -> list[dict[str, Any]]:
        now = self._stamp()
        out = []
        for item in self._items:
            kind = normalize_type(item.get("type"))
            if kind == MemoryType.SESSION or not item.get("persistent", True):
                continue
            if is_expired(item.get("expires_at"), now=now):
                continue
            out.append(dict(item))
        return out

    def get(self, entry_id: str) -> Optional[dict[str, Any]]:
        for item in self._items:
            if item.get("id") == entry_id:
                return dict(item)
        return None

    def find_by_key(self, key: str, *, type: MemoryType | str | None = None) -> Optional[dict[str, Any]]:
        needle = _norm_key(key)
        want = normalize_type(type) if type else None
        now = self._stamp()
        for item in reversed(self._items):
            if want and normalize_type(item.get("type")) != want:
                continue
            if is_expired(item.get("expires_at"), now=now):
                continue
            if _norm_key(item.get("key")) == needle:
                return dict(item)
        return None

    def upsert(
        self,
        *,
        type: MemoryType | str,
        key: str,
        value: str,
        source: str = "user",
        confidence: float | None = None,
        explicit: bool = True,
        expires_at: str | None = None,
        importance: float | None = None,
        provenance: dict[str, Any] | None = None,
        text: str = "",
        tags: Sequence[str] | None = None,
        ask_on_conflict: bool = True,
        entry_id: str | None = None,
    ) -> WriteResult:
        kind = normalize_type(type)
        key = clean_text(str(key or ""), 120) or _norm_key(value)[:80] or "note"
        value = clean_text(str(value or ""), 500)
        if not value and not key:
            return WriteResult(WriteStatus.REJECTED_WEAK, message="Nie mam czego zapamiętać.")

        if confidence is None:
            confidence = CONFIDENCE_EXPLICIT if explicit else 0.4
        confidence = clamp01(confidence)
        stamp = self._stamp()
        incoming_text = clean_text(text, 500) or value

        existing = None
        if entry_id:
            existing = next((item for item in self._items if item.get("id") == entry_id), None)
        if existing is None:
            existing = self._match_existing(kind, key, value, incoming_text)

        if existing is not None:
            return self._apply_existing(
                existing,
                kind=kind,
                key=key,
                value=value,
                source=source,
                confidence=confidence,
                explicit=explicit,
                expires_at=expires_at,
                importance=importance,
                provenance=provenance,
                text=incoming_text,
                tags=tags,
                ask_on_conflict=ask_on_conflict,
                stamp=stamp,
            )

        pref = self._preference_blocks_fact(kind, key, value)
        if pref is not None:
            return WriteResult(
                WriteStatus.REJECTED_WEAK,
                entry=dict(pref),
                existing=dict(pref),
                message="Nie nadpisuję preferencji słabą obserwacją.",
            )

        record = {
            "id": new_id(kind.value.lower()),
            "type": kind.value,
            "key": key,
            "subject": key,
            "value": value,
            "source": clean_text(source, 40) or "user",
            "confidence": confidence,
            "created_at": stamp,
            "updated_at": stamp,
            "last_used_at": stamp,
            "expires_at": expires_at,
            "importance": clamp01(importance, default_importance(kind)),
            "provenance": dict(provenance or {}),
            "text": incoming_text,
            "tags": [clean_text(tag, 40) for tag in (tags or []) if clean_text(str(tag), 40)],
            "explicit": bool(explicit),
            "confirmations": 1,
            "status": "active",
            "persistent": kind in PERSISTENT_TYPES and kind != MemoryType.SESSION,
        }
        self._items.append(record)
        return WriteResult(WriteStatus.CREATED, entry=dict(record), message="Zapamiętałem.")

    def _match_existing(self, kind: MemoryType, key: str, value: str, text: str) -> Optional[dict[str, Any]]:
        now = self._stamp()
        key_n = _norm_key(key)
        val_n = _norm_value(value)
        text_n = _norm_value(text)
        for item in self._items:
            if is_expired(item.get("expires_at"), now=now):
                continue
            item_kind = normalize_type(item.get("type"))
            if item_kind != kind:
                continue
            if _norm_key(item.get("key")) == key_n:
                return item
            if kind == MemoryType.FACT and (
                _norm_value(item.get("value")) == val_n
                or _norm_value(item.get("text")) == text_n
            ):
                return item
        return None

    def _preference_blocks_fact(self, kind: MemoryType, key: str, value: str) -> Optional[dict[str, Any]]:
        if kind not in (MemoryType.FACT, MemoryType.TEMPORARY):
            return None
        pref = self.find_by_key(key, type=MemoryType.PREFERENCE)
        if pref is None:
            return None
        if _norm_value(pref.get("value")) == _norm_value(value):
            return None
        if float(pref.get("confidence") or 0) >= STRONG_CONFIDENCE or pref.get("explicit"):
            return pref
        return None

    def _apply_existing(
        self,
        existing: dict[str, Any],
        *,
        kind: MemoryType,
        key: str,
        value: str,
        source: str,
        confidence: float,
        explicit: bool,
        expires_at: str | None,
        importance: float | None,
        provenance: dict[str, Any] | None,
        text: str,
        tags: Sequence[str] | None,
        ask_on_conflict: bool,
        stamp: str,
    ) -> WriteResult:
        old_value = _norm_value(existing.get("value"))
        new_value = _norm_value(value)
        old_conf = clamp01(existing.get("confidence"), 0.5)
        old_explicit = bool(existing.get("explicit"))
        same = old_value == new_value

        if same:
            existing["updated_at"] = stamp
            existing["last_used_at"] = stamp
            existing["confidence"] = max(old_conf, confidence)
            existing["explicit"] = old_explicit or bool(explicit)
            existing["confirmations"] = int(existing.get("confirmations") or 1) + 1
            existing["status"] = "active"
            if expires_at:
                existing["expires_at"] = expires_at
            if importance is not None:
                existing["importance"] = max(
                    clamp01(existing.get("importance"), 0.5),
                    clamp01(importance),
                )
            if provenance:
                merged = dict(existing.get("provenance") or {})
                merged.update(provenance)
                existing["provenance"] = merged
            if tags:
                have = {str(tag).casefold() for tag in (existing.get("tags") or [])}
                for tag in tags:
                    cleaned = clean_text(tag, 40)
                    if cleaned and cleaned.casefold() not in have:
                        existing.setdefault("tags", []).append(cleaned)
            return WriteResult(
                WriteStatus.MERGED,
                entry=dict(existing),
                existing=dict(existing),
                message="Zapamiętałem.",
            )

        # Preferences: explicit API always overwrites (user changed their mind).
        explicit_pref_overwrite = (
            kind == MemoryType.PREFERENCE and explicit and confidence >= STRONG_CONFIDENCE
        )
        incoming_stronger = confidence >= old_conf + WEAK_MARGIN or (
            explicit and not old_explicit and confidence >= old_conf
        )
        incoming_newer_better = confidence >= old_conf - 0.05 and (
            parse_ts(stamp) or datetime.now(timezone.utc)
        ) >= (parse_ts(existing.get("updated_at")) or datetime.now(timezone.utc))

        if old_conf >= STRONG_CONFIDENCE and confidence < old_conf - WEAK_MARGIN and not explicit_pref_overwrite:
            existing["status"] = "conflict"
            return WriteResult(
                WriteStatus.REJECTED_WEAK,
                entry=dict(existing),
                existing=dict(existing),
                message="Nie zmieniam — mam pewniejszą informację.",
            )

        both_strong = (
            ask_on_conflict
            and not explicit_pref_overwrite
            and old_explicit
            and explicit
            and old_conf >= STRONG_CONFIDENCE
            and confidence >= STRONG_CONFIDENCE
            and not incoming_stronger
        )
        if both_strong:
            existing["status"] = "conflict"
            return WriteResult(
                WriteStatus.CONFLICT_ASK,
                entry=dict(existing),
                existing=dict(existing),
                message=(
                    f"Mam zapamiętane „{existing.get('value')}”. "
                    f"Mam nadpisać na „{value}”?"
                ),
            )

        if incoming_stronger or incoming_newer_better or explicit_pref_overwrite:
            existing["key"] = key or existing.get("key")
            existing["subject"] = existing["key"]
            existing["value"] = value
            existing["text"] = text or value
            existing["source"] = clean_text(source, 40) or existing.get("source") or "user"
            existing["confidence"] = max(old_conf, confidence) if not incoming_stronger else confidence
            existing["explicit"] = old_explicit or bool(explicit)
            existing["updated_at"] = stamp
            existing["last_used_at"] = stamp
            existing["status"] = "active"
            if expires_at is not None:
                existing["expires_at"] = expires_at
            if importance is not None:
                existing["importance"] = clamp01(importance, existing.get("importance"))
            if provenance:
                merged = dict(existing.get("provenance") or {})
                merged.update(provenance)
                existing["provenance"] = merged
            return WriteResult(
                WriteStatus.UPDATED,
                entry=dict(existing),
                existing=dict(existing),
                message="Zaktualizowałem.",
            )

        existing["status"] = "conflict"
        return WriteResult(
            WriteStatus.CONFLICT_ASK,
            entry=dict(existing),
            existing=dict(existing),
            message=(
                f"Mam zapamiętane „{existing.get('value')}”. "
                f"Mam nadpisać na „{value}”?"
            ),
        )

    def retrieve(
        self,
        query: str = "",
        *,
        types: Iterable[MemoryType | str] | None = None,
        key: str | None = None,
        limit: int = 8,
        include_session: bool = False,
    ) -> list[dict[str, Any]]:
        now = self._stamp()
        type_set = None
        if types:
            type_set = {normalize_type(item).value for item in types}
        terms = query_terms(query)
        key_n = _norm_key(key) if key else ""
        scored = []
        for index, item in enumerate(self._items):
            kind = normalize_type(item.get("type"))
            if kind == MemoryType.SESSION and not include_session:
                continue
            if is_expired(item.get("expires_at"), now=now):
                continue
            status = str(item.get("status") or "active").lower()
            if status in ("expired", "invalidated", "cancelled", "forgotten", "cleanup_candidate"):
                continue
            if type_set and kind.value not in type_set:
                continue
            if key_n and _norm_key(item.get("key")) != key_n and key_n not in _norm_key(item.get("key")):
                blob_key = f"{item.get('key')} {item.get('value')} {item.get('text')}"
                if key_n not in blob_key.casefold().replace(" ", "."):
                    continue
            blob = f"{item.get('key')} {item.get('value')} {item.get('text')} {' '.join(item.get('tags') or [])}"
            blob_terms = query_terms(blob)
            overlap = _term_overlap(terms, blob_terms, blob, query)
            if terms and overlap == 0 and not key_n:
                continue
            recency = _recency_bonus(item.get("last_used_at") or item.get("updated_at"), now)
            type_bonus = 8 if kind == MemoryType.PREFERENCE else (5 if kind in (MemoryType.RELATION, MemoryType.VOCABULARY) else 0)
            score = (
                overlap * 10
                + float(item.get("confidence") or 0) * 6
                + float(item.get("importance") or 0) * 4
                + recency
                + type_bonus
                + (3 if item.get("explicit") else 0)
                + index / 1000
            )
            scored.append((score, item))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        selected = []
        for _score, item in scored[: max(1, int(limit))]:
            item["last_used_at"] = now
            selected.append(dict(item))
        return selected

    def touch(self, entry_id: str) -> bool:
        stamp = self._stamp()
        for item in self._items:
            if item.get("id") == entry_id:
                item["last_used_at"] = stamp
                return True
        return False

    def mark_status(self, entry_id: str, status: str) -> bool:
        """Set entry status (e.g. invalidated after soft CHANGE_OF_MIND)."""
        wanted = str(entry_id or "").strip()
        if not wanted:
            return False
        stamp = self._stamp()
        for item in self._items:
            if item.get("id") == wanted:
                item["status"] = str(status or "active")
                item["updated_at"] = stamp
                return True
        return False

    def forget(self, query: str) -> list[dict[str, Any]]:
        terms = query_terms(query)
        query_cf = str(query or "").casefold().strip()
        if not terms and not query_cf:
            return []
        removed = []
        remaining = []
        for item in self._items:
            blob = f"{item.get('key')} {item.get('value')} {item.get('text')}"
            blob_cf = blob.casefold()
            blob_terms = query_terms(blob)
            match = False
            if query_cf and (query_cf in blob_cf or query_cf in _norm_key(item.get("key"))):
                match = True
            elif terms and terms.issubset(blob_terms):
                match = True
            elif terms and len(terms & blob_terms) >= max(1, (len(terms) + 1) // 2):
                match = True
            if match:
                removed.append(dict(item))
            else:
                remaining.append(item)
        self._items = remaining
        return removed

    def forget_by_id(self, entry_id: str) -> Optional[dict[str, Any]]:
        for index, item in enumerate(self._items):
            if item.get("id") == entry_id:
                removed = self._items.pop(index)
                return dict(removed)
        return None

    def drop_type(self, kind: MemoryType | str) -> int:
        want = normalize_type(kind).value
        before = len(self._items)
        self._items = [item for item in self._items if item.get("type") != want]
        return before - len(self._items)

    def purge_expired(self) -> list[dict[str, Any]]:
        now = self._stamp()
        kept = []
        removed = []
        for item in self._items:
            kind = normalize_type(item.get("type"))
            if kind in (MemoryType.TEMPORARY, MemoryType.SESSION) and is_expired(
                item.get("expires_at"), now=now
            ):
                removed.append(dict(item))
                continue
            kept.append(item)
        self._items = kept
        return removed

    def cleanup_candidates(self, *, stale_days: int = STALE_DAYS) -> list[dict[str, Any]]:
        """Rarely used low-value entries. Never auto-deletes important data."""
        now = parse_ts(self._stamp()) or datetime.now(timezone.utc)
        out = []
        for item in self._items:
            kind = normalize_type(item.get("type"))
            if kind == MemoryType.PREFERENCE and (
                item.get("explicit") or float(item.get("confidence") or 0) >= STRONG_CONFIDENCE
            ):
                continue
            if float(item.get("importance") or 0) >= 0.8:
                continue
            if item.get("explicit") and kind in (MemoryType.RELATION, MemoryType.VOCABULARY, MemoryType.ROUTINE):
                continue
            last = parse_ts(item.get("last_used_at") or item.get("updated_at"))
            created = parse_ts(item.get("created_at"))
            if last is None:
                continue
            age_days = (now - last).total_seconds() / 86400
            created_days = (now - created).total_seconds() / 86400 if created else age_days
            low_value = (
                float(item.get("importance") or 0) <= LOW_VALUE_IMPORTANCE
                or float(item.get("confidence") or 0) <= LOW_VALUE_CONFIDENCE
            )
            if age_days >= stale_days and low_value and created_days >= CLEANUP_MIN_AGE_DAYS:
                candidate = dict(item)
                candidate["status"] = "cleanup_candidate"
                item["status"] = "cleanup_candidate"
                out.append(candidate)
        return out


def _term_overlap(terms, blob_terms, blob, query) -> int:
    if not terms:
        return 0
    overlap = len(terms & blob_terms)
    if overlap:
        return overlap
    folded_blob = fold_pl(blob)
    folded_query = fold_pl(query)
    if folded_query and folded_query in folded_blob:
        return 1
    for token in terms:
        if len(token) >= 4 and token in folded_blob:
            return 1
        stem = token[:4]
        if len(stem) < 4:
            continue
        for blob_token in blob_terms:
            if blob_token.startswith(stem) or token.startswith(blob_token[:4]):
                return 1
    return 0


def _recency_bonus(stamp: str, now_iso_s: str) -> float:
    then = parse_ts(stamp)
    now = parse_ts(now_iso_s)
    if then is None or now is None:
        return 0.0
    hours = max(0.0, (now - then).total_seconds() / 3600.0)
    if hours < 24:
        return 5.0
    if hours < 24 * 7:
        return 2.0
    if hours < 24 * 30:
        return 0.5
    return 0.0
