# ==========================
# ELIAN — learned “when I say X, do Y”
# ==========================
"""Persist successful tool recipes. Never bypasses ActionFirewall. No secrets."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.jarvis_response import polish_spoken

_PATH = Path(__file__).resolve().parents[1] / "Data" / "learned_routines.json"
_MAX = 80
_TEST_STORE: Optional[List[Dict[str, Any]]] = None

LEARNABLE_TOOLS = frozenset(
    {
        "open_app",
        "close_app",
        "play_media",
        "pause_media",
        "resume_media",
        "next_track",
        "previous_track",
        "volume_up",
        "volume_down",
        "get_situation_briefing",
        "get_home_summary",
        "list_reminders",
        "get_today_schedule",
        "get_weather",
        "list_os_notifications",
        "get_phone_status",
        "create_reminder",
        "turn_on_device",
        "turn_off_device",
        "set_device_value",
        "activate_scene",
    }
)

_TEACH = re.compile(
    r"^(?:jak\s+(?:powiem|m[oó]wi[eę]|powiemy)|"
    r"od\s+teraz(?:\s+jak\s+powiem)?|"
    r"gdy\s+powiem)\s+"
    r"[«\"„]?(.+?)[»\"”]?\s+"
    r"(?:to\s+znaczy|znaczy|to)\s+"
    r"(.+)$",
    re.I,
)
_REQUESTISH = re.compile(
    r"\b(?:"
    r"zr[oó]b|zrob|ustaw|odpal|w[lł][aą]cz|wy[lł][aą]cz|otw[oó]rz|"
    r"zamknij|pu[sś][cć]|daj|poka[zż]|sprawd[zź]|przypomn|"
    r"dodaj|uruchom|zga[sś]|przyciemnij|rozja[sś]nij|"
    r"mo[zż]esz|m[oó]g[lł]by[sś]|poprosz[eę]|chcia[lł]bym"
    r")\b",
    re.I,
)


@dataclass
class LearnedRoutine:
    phrase: str
    tool: str
    arguments: Dict[str, Any]
    intent: str = ""
    count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_phrase(text: str) -> str:
    raw = str(text or "").strip().lower()
    raw = re.sub(r"[.!?…,;:]+$", "", raw)
    return re.sub(r"\s+", " ", raw).strip()


def looks_teach_phrase(text: str) -> bool:
    return bool(_TEACH.match(str(text or "").strip()))


def parse_teach_phrase(text: str) -> Optional[tuple[str, str]]:
    match = _TEACH.match(str(text or "").strip())
    if not match:
        return None
    src = normalize_phrase(match.group(1))
    dest = str(match.group(2) or "").strip().rstrip(".!?")
    if not src or not dest or src == normalize_phrase(dest):
        return None
    if len(src) < 3 or len(dest) < 3:
        return None
    return src, dest


def should_auto_learn(utterance: str, intent: str = "") -> bool:
    label = str(intent or "")
    if label in (
        "situation_briefing",
        "home_arrival",
        "home_overview",
        "social_smalltalk",
        "list_reminders",
        "today_schedule",
        "weather_status",
        "missed_digest",
        "list_notifications",
        "phone_status",
        "system_volume",
        "app",
        "open_app",
        "media",
        "play_media",
        "media_playback",
        "smart_home_control",
        "creative_humor",
        "knowledge_explanation",
        "general_conversation",
    ):
        return False
    raw = str(utterance or "")
    if re.search(
        r"\b(?:w[lł][aą]cz|wy[lł][aą]cz|ustaw|otw[oó]rz|odpal|zamknij|pu[sś][cć])\b",
        raw,
        re.I,
    ) and re.search(
        r"lamp|[sś]wiat[lł]|vizzini|spotify|notatnik|chrome|discord|"
        r"g[lł]o[sś]n|michael|jackson",
        raw,
        re.I,
    ):
        return False
    try:
        from CommandParser import looks_multi_command

        if looks_multi_command(raw):
            return False
    except Exception:
        pass
    try:
        from core.read_intents import (
            looks_home_arrival,
            looks_home_overview,
            looks_list_notifications,
            looks_list_reminders,
            looks_missed_digest,
            looks_phone_status,
            looks_today_schedule,
            looks_weather,
        )
        from core.situation_briefing import looks_situation_briefing

        raw = str(utterance or "")
        if (
            looks_situation_briefing(raw)
            or looks_home_arrival(raw)
            or looks_home_overview(raw)
            or looks_list_reminders(raw)
            or looks_today_schedule(raw)
            or looks_weather(raw)
            or looks_missed_digest(raw)
            or looks_list_notifications(raw)
            or looks_phone_status(raw)
        ):
            return False
    except Exception:
        pass
    return True


def looks_unclassified_request(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw or len(raw) < 6:
        return False
    try:
        from core.semantic_executive import looks_creative_humor, looks_discussion_not_action
        from core.utterance_noise import looks_noise_like

        if looks_creative_humor(raw) or looks_discussion_not_action(raw):
            return False
        if looks_noise_like(raw):
            return False
    except Exception:
        pass
    try:
        from core.context_selection import looks_small_talk

        if looks_small_talk(raw):
            return False
    except Exception:
        pass
    return bool(_REQUESTISH.search(raw))


def reset_routines_for_tests(rows: Optional[List[Dict[str, Any]]] = None) -> None:
    global _TEST_STORE
    _TEST_STORE = [] if rows is None else list(rows)


def _load() -> List[Dict[str, Any]]:
    if _TEST_STORE is not None:
        return list(_TEST_STORE)
    try:
        raw = json.loads(_PATH.read_text(encoding="utf-8"))
        rows = raw.get("routines") if isinstance(raw, dict) else raw
        return [dict(item) for item in (rows or []) if isinstance(item, dict)]
    except Exception:
        return []


def _save(rows: List[Dict[str, Any]]) -> None:
    global _TEST_STORE
    trimmed = rows[-_MAX:]
    if _TEST_STORE is not None:
        _TEST_STORE = trimmed
        return
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(
            json.dumps({"routines": trimmed}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def lookup_learned_routine(text: str) -> Optional[LearnedRoutine]:
    key = normalize_phrase(text)
    if not key:
        return None
    for row in reversed(_load()):
        phrase = normalize_phrase(str(row.get("phrase") or ""))
        tool = str(row.get("tool") or "").strip()
        if phrase and phrase == key and tool in LEARNABLE_TOOLS:
            return LearnedRoutine(
                phrase=phrase,
                tool=tool,
                arguments=dict(row.get("arguments") or {}),
                intent=str(row.get("intent") or ""),
                count=int(row.get("count") or 1),
            )
    return None


def note_learned_routine(
    utterance: str,
    tool: str,
    arguments: Optional[Dict[str, Any]] = None,
    *,
    intent: str = "",
    force: bool = False,
) -> bool:
    phrase = normalize_phrase(utterance)
    name = str(tool or "").strip()
    if name not in LEARNABLE_TOOLS:
        return False
    if not phrase or len(phrase) < (3 if force else 6):
        return False
    if phrase in ("tak", "nie", "cześć", "czesc", "ok", "dobra"):
        return False
    if looks_teach_phrase(utterance):
        return False
    try:
        from memory.sensitive import looks_sensitive

        if looks_sensitive(phrase) or looks_sensitive(json.dumps(arguments or {})):
            return False
    except Exception:
        pass
    args = dict(arguments or {})
    rows = _load()
    for row in rows:
        if normalize_phrase(str(row.get("phrase") or "")) == phrase and str(row.get("tool") or "") == name:
            row["arguments"] = args
            row["intent"] = intent or str(row.get("intent") or "")
            row["count"] = int(row.get("count") or 1) + 1
            _save(rows)
            return True
    rows.append(
        {
            "phrase": phrase,
            "tool": name,
            "arguments": args,
            "intent": intent,
            "count": 1,
        }
    )
    _save(rows)
    return True


def arguments_for_learn(tool: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(data or {})
    args = dict(payload.get("arguments") or {})
    if args:
        return args
    name = str(tool or "")
    if name == "open_app":
        value = payload.get("app") or payload.get("name") or payload.get("target")
        return {"name": value} if value else {}
    if name == "close_app":
        value = payload.get("app") or payload.get("name")
        return {"name": value} if value else {}
    if name == "play_media":
        query = payload.get("query") or payload.get("title") or payload.get("track")
        app = payload.get("app")
        out = {}
        if query:
            out["query"] = query
        if app:
            out["app"] = app
        return out
    if name in ("turn_on_device", "turn_off_device", "set_device_value"):
        entity = payload.get("entity_id") or payload.get("target")
        out = {}
        if entity:
            out["entity_id"] = entity
        if name == "set_device_value" and payload.get("value") is not None:
            out["value"] = payload.get("value")
        return out
    if name == "create_reminder":
        text = payload.get("text") or payload.get("message")
        return {"text": text} if text else {}
    if name == "get_situation_briefing" and payload.get("mode"):
        return {"mode": payload.get("mode")}
    return {}


def teach_spoken(src: str, dest: str) -> str:
    spoken = polish_spoken(f"Zapamiętam. Jak powiesz «{src}», zrobię: {dest}.")
    return spoken or f"Zapamiętam. Jak powiesz «{src}», zrobię: {dest}."
