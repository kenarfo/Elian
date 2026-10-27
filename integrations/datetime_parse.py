# ==========================
# ELIAN — natural datetime helpers (Phase 7)
# NLP stays out of cloud providers; tools may normalize here.
# ==========================

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from zoneinfo import ZoneInfo


def local_tz():
    try:
        return datetime.now().astimezone().tzinfo or timezone.utc
    except Exception:
        return timezone.utc


def ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=local_tz())
    return dt


def now_local() -> datetime:
    return datetime.now(tz=local_tz())


def parse_when(text: str, *, base: Optional[datetime] = None) -> Optional[datetime]:
    """
    Parse common Polish relative/absolute phrases into aware datetime.
    Returns None if unparseable.
    """
    base = ensure_aware(base or now_local())
    raw = str(text or "").strip().lower()
    if not raw:
        return None

    # ISO-ish
    try:
        if "t" in raw or re.match(r"^\d{4}-\d{2}-\d{2}", raw):
            dt = datetime.fromisoformat(raw.replace("z", "+00:00"))
            return ensure_aware(dt)
    except ValueError:
        pass

    # za X minut/godzin
    m = re.match(
        r"^za\s+(\d+)\s*(minut[ey]?|min|godzin[ey]?|h|sec|sekund[ey]?)$",
        raw,
    )
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("min"):
            return base + timedelta(minutes=n)
        if unit.startswith(("godzin", "h")):
            return base + timedelta(hours=n)
        return base + timedelta(seconds=n)

    if raw in ("za godzinę", "za godzine", "za h"):
        return base + timedelta(hours=1)
    if raw == "za kwadrans":
        return base + timedelta(minutes=15)
    if raw in ("za pół godziny", "za pol godziny"):
        return base + timedelta(minutes=30)

    # dziś / jutro + optional hour
    day = base
    if raw.startswith("jutro"):
        day = (base + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        rest = raw[5:].strip(" ,")
    elif raw.startswith("dziś") or raw.startswith("dzis"):
        day = base.replace(second=0, microsecond=0)
        rest = re.sub(r"^dzi[sś]\s*", "", raw).strip(" ,")
    else:
        rest = raw

    hm = re.search(r"(?:o\s+)?(\d{1,2})(?::(\d{2}))?", rest)
    if hm and (raw.startswith("jutro") or raw.startswith("dzi") or "o " in raw):
        hour = int(hm.group(1))
        minute = int(hm.group(2) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            if raw.startswith("jutro"):
                target = (base + timedelta(days=1)).replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
            elif raw.startswith("dzi"):
                target = base.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
            else:
                target = base.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
                if target <= base:
                    target = target + timedelta(days=1)
            return ensure_aware(target)

    # bare "o 17" / "17:30"
    hm2 = re.match(r"^(?:o\s+)?(\d{1,2})(?::(\d{2}))?$", raw)
    if hm2:
        hour = int(hm2.group(1))
        minute = int(hm2.group(2) or 0)
        target = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= base:
            target += timedelta(days=1)
        return ensure_aware(target)

    return None


def parse_duration_minutes(text: str, default: int = 60) -> int:
    raw = str(text or "").strip().lower()
    m = re.match(r"^(\d+)\s*(min|minut[ey]?|h|godzin[ey]?)?$", raw)
    if not m:
        return default
    n = int(m.group(1))
    unit = m.group(2) or "min"
    if unit.startswith(("h", "godzin")):
        return n * 60
    return n


def day_bounds(day: datetime) -> Tuple[datetime, datetime]:
    day = ensure_aware(day)
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end
