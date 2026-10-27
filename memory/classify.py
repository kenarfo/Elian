# ==========================
# ELIAN — Memory 2.0 utterance classify
# ==========================
"""Map explicit remember phrases onto types. Does not auto-save casual chat."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from memory.store import clean_text, parse_ts
from memory.types import MemoryType

_RELATION = re.compile(
    r"(?:moja|moj|mój)\s+([a-ząćęłńóśźż]+)\s+to\s+([a-z][a-z0-9_]*\.[a-z0-9_]+)",
    re.I,
)
_ENTITY_ID = re.compile(r"\b([a-z][a-z0-9_]*\.[a-z0-9_]+)\b", re.I)
_MORNING_SHORT = re.compile(
    r"wol[eę].{0,40}rano.{0,40}(?:kr[oó]tk|zwi[eę][zź]l|mniej gad)",
    re.I,
)
_TEMPORARY = re.compile(
    r"\b(jutro|pojutrze|dzi[sś]|w weekend)\b.{0,40}\b(mam|b[eę]d[eę]|jest)\b",
    re.I,
)
_FAVORITE = re.compile(
    r"(?:moja|m[oó]j)?\s*ulubion[aey]\s+([a-ząćęłńóśźż]+)\s+to\s+(.+)$",
    re.I,
)


def end_of_day_iso(when: datetime) -> str:
    local = when.astimezone(timezone.utc)
    end = local.replace(hour=23, minute=59, second=0, microsecond=0)
    return end.isoformat(timespec="seconds")


def infer_temporary_expiry(text: str, *, now: str | None = None) -> Optional[str]:
    lowered = str(text or "").casefold()
    base = parse_ts(now) or datetime.now(timezone.utc)
    if "pojutrze" in lowered:
        return end_of_day_iso(base + timedelta(days=2))
    if "jutro" in lowered:
        return end_of_day_iso(base + timedelta(days=1))
    if re.search(r"\bdzi[sś]\b", lowered):
        return end_of_day_iso(base)
    if "weekend" in lowered:
        return end_of_day_iso(base + timedelta(days=3))
    return None


def derive_key(text: str, kind: MemoryType) -> str:
    cleaned = clean_text(text, 200)
    if kind == MemoryType.RELATION:
        match = _RELATION.search(cleaned)
        if match:
            return f"alias.{match.group(1).casefold()}"
        entity = _ENTITY_ID.search(cleaned)
        if entity:
            return f"alias.{entity.group(1).casefold()}"
    fav = _FAVORITE.search(cleaned)
    if fav:
        return f"ulubiona.{fav.group(1).casefold()}"
    tokens = re.findall(r"[a-ząćęłńóśźż0-9]+", cleaned.casefold())
    keep = [tok for tok in tokens if len(tok) > 2][:6]
    return ".".join(keep) if keep else "note"


def classify_remember_text(text: str, *, now: str | None = None) -> dict[str, Any]:
    """Best-effort type for an explicit remember. Conservative — default FACT."""
    raw = clean_text(text, 500)
    lowered = raw.casefold()
    if not raw:
        return {
            "type": MemoryType.FACT,
            "key": "note",
            "value": "",
            "expires_at": None,
            "provenance": {},
        }

    if _MORNING_SHORT.search(lowered):
        return {
            "type": MemoryType.PREFERENCE,
            "key": "voice.morning_brief",
            "value": "short",
            "expires_at": None,
            "provenance": {"utterance": raw[:160]},
        }

    rel = _RELATION.search(raw)
    if rel:
        return {
            "type": MemoryType.RELATION,
            "key": f"alias.{rel.group(1).casefold()}",
            "value": rel.group(2),
            "expires_at": None,
            "text": raw,
            "provenance": {"kind": "device_alias"},
        }

    if _TEMPORARY.search(lowered):
        expiry = infer_temporary_expiry(raw, now=now)
        return {
            "type": MemoryType.TEMPORARY,
            "key": derive_key(raw, MemoryType.TEMPORARY),
            "value": raw,
            "expires_at": expiry,
            "text": raw,
            "provenance": {
                "kind": "user_said",
                "calendar": False,
                "note": "not a calendar source of truth",
            },
        }

    fav = _FAVORITE.search(raw)
    value = raw
    key = derive_key(raw, MemoryType.FACT)
    if fav:
        key = f"ulubiona.{fav.group(1).casefold()}"
        value = clean_text(fav.group(2), 200)
    return {
        "type": MemoryType.FACT,
        "key": key,
        "value": value,
        "text": raw,
        "expires_at": None,
        "provenance": {},
    }
