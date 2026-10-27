# ==========================
# ELIAN — on-demand Jarvis situation briefing
# ==========================
"""Compose a short spoken status from existing read-only sources.

Not a ProcedureEngine scene, HUD, or missed-events timeline.
Failed or empty sources are omitted — never invent “wszystko OK.”
"""

from __future__ import annotations

import re
from typing import Any, Optional

from core.jarvis_response import polish_spoken
from core.tool_result import ToolResult

MAX_SPOKEN = 200
ARRIVAL_MAX_SPOKEN = 250
_EMPTY_SPOKEN = "Nie mam teraz nic konkretnego do zgłoszenia."
_ARRIVAL_PREFIX = "Witaj w domu."
_NEWS_TIMEOUT_SEC = 2.5

_BRIEFING = re.compile(
    r"^(?:"
    r"podsumuj\s+sytuacj[eę]|"
    r"jaka\s+(?:jest\s+)?sytuacja|"
    r"co\s+mnie\s+czeka|"
    r"jaki\s+mam\s+dzie[nń]|"
    r"wsta[lł]em|"
    r"zaczynamy\s+dzie[nń]|"
    r"(?:daj\s+(?:mi\s+)?)?briefing|"
    r"(?:przygotuj|ogarnij)(?:\s+mi)?(?:\s+mnie)?(?:\s+na)?\s+(?:jutro|dzie[nń]|poranek)|"
    r"przygotuj\s+mnie\s+na\s+jutro"
    r")[.!?…]*$",
    re.I,
)


def looks_situation_briefing(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    return bool(_BRIEFING.match(raw))


def _ok_result(result: Any) -> Optional[ToolResult]:
    if result is None or not isinstance(result, ToolResult):
        return None
    if not result.success:
        return None
    return result


def _hour_from_when(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    iso = re.search(r"T(\d{1,2})(?::(\d{2}))?", raw)
    if iso:
        hour = int(iso.group(1))
        minute = iso.group(2)
        if minute and minute != "00":
            return f"{hour}.{minute}"
        return str(hour)
    clock = re.search(r"\b(\d{1,2}):(\d{2})\b", raw)
    if clock:
        hour = int(clock.group(1))
        minute = clock.group(2)
        if minute != "00":
            return f"{hour}.{minute}"
        return str(hour)
    return ""


def _slice_calendar() -> Optional[str]:
    try:
        from integrations.calendar.tools import _get_today_schedule

        today = _ok_result(_get_today_schedule({}))
    except Exception:
        today = None
    if today is not None:
        events = list((today.data or {}).get("events") or [])
        titles = []
        for row in events[:3]:
            item = dict(row or {})
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            hour = _hour_from_when(item.get("start"))
            titles.append(f"{title} o {hour}" if hour else title)
        if titles:
            if len(titles) == 1:
                return f"Dziś: {titles[0]}."
            return "Dziś: " + "; ".join(titles) + "."
    try:
        from integrations.calendar.tools import _get_next_event

        result = _ok_result(_get_next_event({}))
    except Exception:
        return None
    if result is None:
        return None
    event = dict((result.data or {}).get("event") or {})
    if not event:
        return None
    title = str(event.get("title") or "").strip()
    if not title:
        return None
    hour = _hour_from_when(event.get("start"))
    if hour:
        return f"Następne: {title} o {hour}."
    message = str(result.message or "").strip()
    if message.lower().startswith("następne:"):
        return message if message.endswith(".") else message + "."
    return f"Następne: {title}."


def _slice_now_playing() -> Optional[str]:
    try:
        from integrations.media.tools import _get_now_playing

        result = _ok_result(_get_now_playing({}))
    except Exception:
        return None
    if result is None:
        return None
    data = dict(result.data or {})
    title = str(data.get("title") or data.get("track") or "").strip()
    if not title:
        message = str(result.message or "").strip()
        if message and "nie" not in message.lower()[:8]:
            return message if message.endswith(".") else message + "."
        return None
    try:
        from core.world_state import note_media

        note_media({"title": title})
    except Exception:
        pass
    return f"Teraz: {title}."


def _slice_reminders() -> Optional[str]:
    try:
        from integrations.reminders.tools import _list_reminders

        result = _ok_result(_list_reminders({}))
    except Exception:
        return None
    if result is None:
        return None
    rows = list((result.data or {}).get("reminders") or [])
    if not rows:
        return None
    bits = []
    for row in rows[:3]:
        item = dict(row or {})
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        hour = _hour_from_when(item.get("due_at"))
        bits.append(f"{text} o {hour}" if hour else text)
    if not bits:
        return None
    if len(bits) == 1:
        return f"Przypomnienie: {bits[0]}."
    return "Przypomnienia: " + "; ".join(bits) + "."


def _slice_home() -> Optional[str]:
    try:
        from integrations.home_assistant.tools import _get_home_summary

        result = _ok_result(_get_home_summary({}))
    except Exception:
        return None
    if result is None:
        return None
    on_items = [str(item).strip() for item in ((result.data or {}).get("on") or []) if str(item).strip()]
    if not on_items:
        return None
    names = ", ".join(on_items[:4])
    return f"W domu włączone: {names}."


def _slice_weather() -> Optional[str]:
    try:
        from Config import get_config_value
        from Skills.Weather import get_weather

        city = str(get_config_value("location", "city", default="Łódź") or "Łódź").strip() or "Łódź"
        raw = str(get_weather(city) or "").strip()
    except Exception:
        return None
    if not raw or re.search(r"nie\s+udało", raw, re.I):
        return None
    temp = re.search(r"(\d+)\s+stopni", raw, re.I)
    desc = re.search(r"Pogoda:\s*([^.\n]+)", raw)
    if not temp:
        return None
    bits = [f"{temp.group(1)} stopni"]
    if desc:
        label = desc.group(1).strip().rstrip(".").lower()
        if label and label not in ("brak opisu", "?"):
            bits.append(label)
    return f"Pogoda w {city}: {', '.join(bits)}."


def _slice_system() -> Optional[str]:
    cpu = None
    ram = None
    try:
        from core.system_state import get_system_state_store

        state = get_system_state_store().state
        if state.updated_at:
            cpu = float(state.cpu_percent)
            ram = float(state.ram_percent)
    except Exception:
        pass
    if cpu is None:
        try:
            from core.builtin_tools import _get_system_status

            result = _ok_result(_get_system_status({}))
        except Exception:
            result = None
        if result is None:
            return None
        data = dict(result.data or {})
        if data.get("cpu_percent") is None:
            return None
        try:
            cpu = float(data.get("cpu_percent"))
            ram = float(data.get("ram_percent") or 0.0)
        except (TypeError, ValueError):
            return None
    if cpu < 40 and ram < 75:
        return "Na PC spokojnie."
    return f"CPU {int(cpu)} procent."


def _slice_digest_arrival() -> Optional[str]:
    try:
        from core.session_digest import consume_digest_lines, peek_digest_lines

        lines = [str(item).strip() for item in peek_digest_lines() if str(item).strip()]
    except Exception:
        return None
    if not lines:
        return None
    try:
        consume_digest_lines()
    except Exception:
        pass
    bits = [item.rstrip(".") for item in lines[:3] if item]
    if not bits:
        return None
    return f"Ominęło Cię: {'; '.join(bits)}."


def _slice_news() -> Optional[str]:
    try:
        from core.web_search_provider import provider_available, search_structured
    except Exception:
        return None
    try:
        if not provider_available():
            return None
    except Exception:
        return None
    result = None
    try:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                search_structured,
                "wiadomości Polska dzisiaj",
                max_results=1,
            )
            result = future.result(timeout=_NEWS_TIMEOUT_SEC)
    except Exception:
        return None
    if result is None or not getattr(result, "success", False):
        return None
    hits = list(getattr(result, "results", None) or [])
    if not hits:
        return None
    title = str(getattr(hits[0], "title", "") or "").strip()
    title = re.sub(r"\s+", " ", title).rstrip(".")
    if not title:
        return None
    if len(title) > 80:
        clipped = title[:77].rsplit(" ", 1)[0].strip()
        title = clipped or title[:77]
    return f"Wiadomość: {title}."


def _cap_spoken(text: str, max_len: int = MAX_SPOKEN) -> str:
    spoken = polish_spoken(text) or ""
    spoken = re.sub(r"\s+", " ", spoken).strip()
    limit = max(1, int(max_len or MAX_SPOKEN))
    if len(spoken) <= limit:
        return spoken
    clipped = spoken[:limit].rsplit(".", 1)[0].strip()
    if clipped:
        return clipped + "."
    return spoken[:limit].rstrip() + "."


def compose_situation_briefing(_arguments=None) -> ToolResult:
    args = dict(_arguments or {}) if isinstance(_arguments, dict) else {}
    arrival = str(args.get("mode") or "").strip().lower() == "arrival"
    max_len = ARRIVAL_MAX_SPOKEN if arrival else MAX_SPOKEN
    slices = []
    sources = {}
    collectors = [
        ("calendar", _slice_calendar),
        ("reminders", _slice_reminders),
        ("home", _slice_home),
        ("media", _slice_now_playing),
        ("weather", _slice_weather),
        ("system", _slice_system),
    ]
    if arrival:
        collectors.extend(
            (
                ("digest", _slice_digest_arrival),
                ("news", _slice_news),
            )
        )
    for name, collector in collectors:
        try:
            piece = collector()
        except Exception:
            piece = None
        if piece:
            slices.append(piece)
            sources[name] = piece
    if arrival:
        spoken = (
            _cap_spoken(f"{_ARRIVAL_PREFIX} {' '.join(slices)}", max_len)
            if slices
            else _ARRIVAL_PREFIX
        )
        try:
            from core.proactive_policy import mark_consumed

            mark_consumed("home_arrival")
        except Exception:
            pass
    else:
        spoken = _cap_spoken(" ".join(slices), max_len) if slices else _EMPTY_SPOKEN
    if not spoken:
        spoken = _ARRIVAL_PREFIX if arrival else _EMPTY_SPOKEN
    return ToolResult.ok(
        spoken,
        tool_name="get_situation_briefing",
        data={
            "sources": sources,
            "empty": not bool(slices),
            "mode": "arrival" if arrival else "briefing",
        },
    )
