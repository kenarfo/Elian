# ==========================
# ELIAN — planner tool shortlist (Phase 11)
# ==========================
"""Capability-oriented shortlist — not a closed command router."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

from core.intent_family import IntentClassification, IntentFamily


# Always useful for multi-step / general agent turns.
_CORE = ("web_search", "fetch_web_page", "open_website")

_FAMILY_HINTS: Dict[IntentFamily, tuple[str, ...]] = {
    IntentFamily.APP: ("open_app", "close_app", "check_app_installed"),
    IntentFamily.MEDIA: (
        "play_media",
        "pause_media",
        "resume_media",
        "next_track",
        "previous_track",
        "get_now_playing",
    ),
    IntentFamily.SMART_HOME: (
        "turn_on_device",
        "turn_off_device",
        "set_device_value",
        "get_device_state",
        "get_home_summary",
        "list_devices",
        "activate_scene",
    ),
    IntentFamily.VISION: (
        "describe_screen",
        "find_ui_element",
        "click_ui_element",
        "ask_about_screen",
    ),
    IntentFamily.COMPUTER: (
        "list_windows",
        "get_active_window",
        "inspect_ui",
        "type_text",
        "click_ui_element",
        "hotkey",
        "focus_window",
    ),
    IntentFamily.REMINDER: (
        "create_reminder",
        "update_reminder",
        "cancel_reminder",
        "list_reminders",
    ),
    IntentFamily.CALENDAR: (
        "create_calendar_event",
        "update_calendar_event",
        "delete_calendar_event",
        "get_next_event",
        "get_today_schedule",
        "get_calendar_events",
    ),
    IntentFamily.WEB: ("web_search", "fetch_web_page", "open_website"),
    IntentFamily.SYSTEM: ("volume_up", "volume_down", "get_system_status", "undo_last_action"),
    IntentFamily.PREFERENCE: ("set_preference", "get_preference", "remember_fact"),
    IntentFamily.KNOWLEDGE: (),
    IntentFamily.CONVERSATION_CONTROL: (),
    IntentFamily.CORRECTION: (),
    IntentFamily.PROCEDURE: (
        "procedure_run",
        "procedure_list",
        "procedure_save",
        "procedure_disable",
        "procedure_delete",
        "procedure_cancel",
    ),
    IntentFamily.UNKNOWN: (),
}

_KEYWORD_HINTS = (
    (r"\b(?:wyszuk|szukaj|google|internet|aktualn|dzisiaj|wczoraj|teraz|cena|koszt)\b",
     ("web_search", "fetch_web_page", "open_website")),
    (r"\b(?:kliknij|wpisz|przewi[nń]|zaznacz).{0,24}(?:stron|kart|link|przycisk)|"
     r"\b(?:na stronie|w karcie)\b",
     ("browser_read_page", "browser_find_element", "browser_click", "browser_type", "browser_open_url")),
    (r"\b(?:muzyk|spotify|pu[sś][cć]|pos[lł]uch|nast[eę]pn|poprzedni|co\s+gra)\b",
     ("play_media", "pause_media", "resume_media", "next_track", "previous_track", "get_now_playing")),
    (r"\b(?:[sś]wiat[lł]|lamp|jasno[sś]|ciem|temperatur|klimatyz|scen[aeę]|co\s+w\s+domu)\b",
     ("set_device_value", "turn_on_device", "turn_off_device", "get_home_summary", "activate_scene")),
    (r"\b(?:otw[oó]rz|uruchom|odpal|discord|chrome|aplikac)\b",
     ("open_app", "close_app", "check_app_installed")),
    (r"\b(?:przypomn|kalendarz|wydarzen|co\s+mam\s+dzi[sś])\b",
     ("create_reminder", "create_calendar_event", "list_reminders", "get_today_schedule")),
    (r"\b(?:pogod[aeyę]|ile\s+(?:jest\s+)?stopni|weather|"
     r"check\s+(?:the\s+)?weather|what(?:['’]?s|\s+is)\s+the\s+weather)\b",
     ("get_weather",)),
    (r"\b(?:cofnij|undo|cofnij\s+to|przywr[oó][cć]\s+jak\s+by[łl]o)\b",
     ("undo_last_action",)),
    (r"\b(?:podsumuj\s+sytuacj|co\s+mnie\s+czeka|jaki\s+mam\s+dzie[nń]|"
     r"wsta[lł]em|zaczynamy\s+dzie[nń]|briefing|"
     r"(?:ju[zż]\s+)?jestem\s+w\s+domu|wr[oó]ci[lł]em)\b",
     ("get_situation_briefing",)),
    (r"\b(?:jakie\s+mam\s+powiadom|odczytaj\s+powiadom|co\s+mi\s+przysz[lł]o)\b",
     ("list_os_notifications",)),
    (r"\b(?:jaki\s+masz\s+numer|czy\s+masz\s+numer|numer\s+telefon)\b",
     ("get_phone_status",)),
    (r"\b(?:ekran|kliknij|vision|ui|wpisz|zaznacz|okn)\b",
     ("describe_screen", "find_ui_element", "click_ui_element", "type_text", "inspect_ui")),
)

_PURE_TALK = re.compile(
    r"(?:"
    r"fajny\s+masz|"
    r"nudzę\s+się|nudze\s+sie|"
    r"serio\??|"
    r"czemu\??|"
    r"nie\s+wiem\s+co\s+robi[cć]|"
    r"masz\s+racj|"
    r"nie\s+zgadzam|"
    r"co\s+o\s+tym\s+my[sś]lisz|"
    r"to\s+by[lł]o\s+troch[eę]\s+dziwne|"
    r"jest\s+fajn|"
    r"nie\s+jest\s+(?:nudn|fajn)|"
    r"denerwuje\s+mnie"
    r")",
    re.I,
)



_ACTION_FAMILIES = (
    IntentFamily.APP,
    IntentFamily.MEDIA,
    IntentFamily.SMART_HOME,
    IntentFamily.VISION,
    IntentFamily.COMPUTER,
    IntentFamily.REMINDER,
    IntentFamily.CALENDAR,
    IntentFamily.WEB,
    IntentFamily.SYSTEM,
    IntentFamily.PREFERENCE,
    IntentFamily.PROCEDURE,
)


def _cap_usable_for_tool(name: str) -> bool:
    """True when CapabilityRegistry says the tool's Cap is usable (AVAILABLE/DEGRADED)."""
    try:
        from core.capability_registry import TOOL_CAPABILITY, get_capability_registry

        cap_id = TOOL_CAPABILITY.get(str(name or ""))
        if not cap_id:
            return False
        rec = get_capability_registry().get(cap_id)
        return bool(rec is not None and rec.usable)
    except Exception:
        return False


def _eligible_catalog(catalog: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Catalog rows eligible for shortlist.

    Cap AVAILABLE/DEGRADED wins over a stale Tool.available=False flag so a
    cold Cap refresh cannot empty the shortlist while Cap still reports AVAILABLE.
    """
    eligible: List[Dict[str, Any]] = []
    for item in catalog:
        name = str(item.get("name") or "")
        if not name:
            continue
        if item.get("available", True):
            eligible.append(item)
            continue
        if _cap_usable_for_tool(name):
            row = dict(item)
            row["available"] = True
            eligible.append(row)
    return eligible


_WEB_NEED_RE = re.compile(
    r"\b(?:wyszuk\w*|szukaj\w*|google|internet|aktualn\w*|cena|koszt)\b",
    re.I,
)


def goal_needs_shortlist_tools(
    goal: str,
    classification: IntentClassification | None,
    *,
    envelope: Any | None = None,
) -> bool:
    """True when intent/goal/Cap require a non-empty tool shortlist.

    Architecture invariant: Cap AVAILABLE + registered tool + needs-it intent
    must not collapse to talk_only / empty shortlist.

    Narrow on purpose: media/app chit-chat ("Spotify jest fajne") must NOT
    override talk_only. Only actionable families, ACT/PLAN, or explicit web
    search intent/keywords when Cap is usable.
    """
    if classification is not None and classification.family in _ACTION_FAMILIES:
        return True
    if envelope is not None:
        mode = str(getattr(envelope, "mode", "") or "").upper()
        if mode in ("ACT", "PLAN"):
            return True
        if bool(getattr(envelope, "needs_tools", False)):
            return True
        domains = [
            str(item or "").upper()
            for item in (getattr(envelope, "allowed_domains", None) or [])
        ]
        if any(
            domain in {
                "WEB",
                "SMART_HOME",
                "MEDIA",
                "APP",
                "SYSTEM",
                "VISION",
                "COMPUTER",
                "REMINDER",
                "CALENDAR",
            }
            for domain in domains
        ):
            return True
        intent = str(getattr(envelope, "intent", "") or "").lower()
        if intent in {"web", "web_search", "search", "browse"}:
            return True
    if _WEB_NEED_RE.search(str(goal or "")):
        return True
    return False


def looks_like_pure_talk(goal: str, classification: IntentClassification | None) -> bool:
    """True when the turn should get an empty tool shortlist."""
    text = str(goal or "").strip().split("\n(", 1)[0].strip()
    if not text:
        return True
    try:
        from core.semantic_executive import looks_creative_humor, looks_discussion_not_action

        if looks_creative_humor(text) or looks_discussion_not_action(text):
            return True
    except Exception:
        pass
    try:
        from core.freshness import analyze_freshness
        from core.meta_ack import analyze_meta, is_meta_utterance

        if is_meta_utterance(text) or analyze_meta(text).is_meta_ack:
            return True
        fresh = analyze_freshness(text)
        if fresh.freshness_required:
            return False
        if fresh.is_opinion or fresh.is_meta:
            return True
    except Exception:
        pass
    if classification is not None:
        family = classification.family
        if family == IntentFamily.CONVERSATION_CONTROL and classification.is_complete:
            return True
        if family == IntentFamily.KNOWLEDGE and classification.is_complete:
            # Knowledge still may shortlist web on freshness keywords.
            if _match_keywords(text):
                return False
            return True
        if family in (
            IntentFamily.APP,
            IntentFamily.MEDIA,
            IntentFamily.SMART_HOME,
            IntentFamily.WEB,
            IntentFamily.VISION,
            IntentFamily.COMPUTER,
            IntentFamily.REMINDER,
            IntentFamily.CALENDAR,
            IntentFamily.SYSTEM,
            IntentFamily.PREFERENCE,
        ):
            return False
    if _PURE_TALK.search(text) and not _match_keywords(text):
        # Opinions / chit-chat without action keywords.
        action = re.search(
            r"\b(?:otw[oó]rz|uruchom|wy[lł][aą]cz|w[lł][aą]cz|pu[sś][cć]|szukaj|znajd[zź])\b",
            text,
            re.I,
        )
        return action is None
    return False


def _match_keywords(text: str) -> List[str]:
    lowered = str(text or "").lower()
    names: List[str] = []
    for pattern, tools in _KEYWORD_HINTS:
        if re.search(pattern, lowered, re.I):
            names.extend(tools)
    return names


def _finish(
    ordered: List[str],
    by_name: Dict[str, Dict[str, Any]],
    max_tools: int,
) -> List[Dict[str, Any]]:
    selected = [by_name[name] for name in ordered[:max_tools] if name in by_name]
    try:
        from core.capability_registry import filter_catalog_by_capabilities

        selected = filter_catalog_by_capabilities(selected, include_alternatives=True)
        by_sel = {str(item.get("name") or ""): item for item in selected}
        ordered_final: List[str] = [n for n in ordered if n in by_sel]
        for name in by_sel:
            if name not in ordered_final:
                ordered_final.append(name)
        return [by_sel[n] for n in ordered_final[:max_tools]]
    except Exception:
        return selected


def shortlist_tools(
    catalog: Sequence[Dict[str, Any]],
    *,
    goal: str = "",
    classification: IntentClassification | None = None,
    max_tools: int = 10,
    talk_only: bool = False,
    active_device: Dict[str, Any] | None = None,
    envelope: Any | None = None,
) -> List[Dict[str, Any]]:
    """Return a capped capability shortlist for the planner prompt."""
    if talk_only and not goal_needs_shortlist_tools(goal, classification, envelope=envelope):
        return []
    available = _eligible_catalog(catalog)
    by_name = {str(item.get("name") or ""): item for item in available}
    ordered: List[str] = []

    def _add(names: Sequence[str]):
        for name in names:
            if name in by_name and name not in ordered:
                ordered.append(name)

    try:
        from core.read_intents import (
            looks_home_arrival,
            looks_list_notifications,
            looks_phone_status,
        )
        from core.situation_briefing import looks_situation_briefing

        if looks_situation_briefing(goal) or looks_home_arrival(goal):
            _add(("get_situation_briefing",))
            return _finish(ordered, by_name, max_tools)
        if looks_list_notifications(goal):
            _add(("list_os_notifications",))
            return _finish(ordered, by_name, max_tools)
        if looks_phone_status(goal):
            _add(("get_phone_status",))
            return _finish(ordered, by_name, max_tools)
    except Exception:
        pass

    slots = dict((classification.slots if classification else None) or {})
    device = dict(active_device or {})
    if slots.get("compound") or (classification and (classification.slots or {}).get("compound")):
        for family in slots.get("families") or []:
            try:
                _add(_FAMILY_HINTS.get(IntentFamily(family), ()))
            except Exception:
                pass
        ha_tools = {"turn_on_device", "turn_off_device", "set_device_value", "get_device_state"}
        families = [str(item) for item in (slots.get("families") or [])]
        if IntentFamily.SMART_HOME.value not in families:
            ordered[:] = [name for name in ordered if name not in ha_tools]
        if IntentFamily.WEB.value not in families:
            ordered[:] = [
                name
                for name in ordered
                if name not in ("web_search", "fetch_web_page")
            ]
        if IntentFamily.VISION.value not in families and IntentFamily.COMPUTER.value not in families:
            ordered[:] = [
                name
                for name in ordered
                if name not in ("describe_screen", "find_ui_element", "click_ui_element")
            ]
        if IntentFamily.COMPUTER.value in families:
            _add(
                (
                    "list_windows",
                    "get_active_window",
                    "inspect_ui",
                    "type_text",
                    "hotkey",
                    "focus_window",
                    "click_ui_element",
                )
            )
        if IntentFamily.REMINDER.value not in families:
            ordered[:] = [name for name in ordered if not str(name).endswith("reminder")]
        if IntentFamily.APP.value in families:
            _add(("open_app", "close_app", "check_app_installed", "open_website"))
        compound_steps = [
            step for step in (slots.get("compound_steps") or []) if isinstance(step, dict)
        ]
        has_browser = any(
            str(step.get("semantic_action") or "").startswith("browser_")
            for step in compound_steps
        )
        has_web_search = any(
            str(step.get("semantic_action") or "") == "web_search" for step in compound_steps
        )
        if has_browser:
            _add(("browser_search_service", "browser_select_result", "browser_play_media", "close_browser_tab"))
            if not has_web_search:
                ordered[:] = [
                    name
                    for name in ordered
                    if name not in ("web_search", "fetch_web_page")
                ]
        directions = {
            str((step.get("slots") or {}).get("direction") or "")
            for step in (slots.get("compound_steps") or [])
            if isinstance(step, dict)
        }
        if "decrease" in directions and "increase" not in directions:
            ordered[:] = [name for name in ordered if name != "volume_up"]
            _add(("volume_down",))
        elif "increase" in directions and "decrease" not in directions:
            ordered[:] = [name for name in ordered if name != "volume_down"]
            _add(("volume_up",))
        elif IntentFamily.SYSTEM.value in families:
            _add(("volume_up", "volume_down"))
        if slots.get("website_direct"):
            _add(("open_website",))
        return _finish(ordered, by_name, max_tools)
    try:
        from core.local_device_status import (
            INTENT_LOCAL_DEVICE_CAPABILITY,
            INTENT_LOCAL_DEVICE_STATUS,
            classify_local_request,
        )

        local = classify_local_request(
            goal,
            active_domain=str(device.get("domain") or slots.get("domain") or ""),
        )
        if local.intent in (INTENT_LOCAL_DEVICE_STATUS, INTENT_LOCAL_DEVICE_CAPABILITY):
            _add(("get_device_state",))
            return _finish(ordered, by_name, max_tools)
    except Exception:
        pass
    try:
        from core.climate_control import looks_climate_louver, wants_climate_eco

        climate_ctx = str(device.get("domain") or slots.get("domain") or "") == "climate"
        climate_named = bool(
            re.search(
                r"\b(?:klima|klimy|klimę|klime|klimatyz\w*|gree)\b",
                str(goal or ""),
                re.I,
            )
        )
        if wants_climate_eco(goal, climate_context=climate_ctx or climate_named):
            _add(_FAMILY_HINTS[IntentFamily.SMART_HOME])
            return _finish(ordered, by_name, max_tools)
        if looks_climate_louver(goal) and (climate_ctx or climate_named or device.get("domain") == "climate"):
            _add(("set_device_value", "turn_on_device", "turn_off_device"))
            return _finish(ordered, by_name, max_tools)
    except Exception:
        pass
    # Phase 12 — resolved smart-home follow-up: device tools only.
    if (
        (classification and classification.family == IntentFamily.SMART_HOME)
        and (
            slots.get("resolved_context")
            or slots.get("entity_id")
            or device.get("entity_id")
        )
    ):
        _add(_FAMILY_HINTS[IntentFamily.SMART_HOME])
        return _finish(ordered, by_name, max_tools)

    if classification is not None:
        family = classification.family
        if family in (
            IntentFamily.KNOWLEDGE,
            IntentFamily.CONVERSATION_CONTROL,
            IntentFamily.CORRECTION,
        ) and classification.is_complete:
            # Pure talk/knowledge — empty shortlist unless keywords demand web/tools.
            _add(_match_keywords(goal))
            return _finish(ordered, by_name, max_tools)
        _add(_FAMILY_HINTS.get(family, ()))
        if str(slots.get("semantic_action") or "") in ("close_app", "close_service"):
            ordered[:] = [name for name in ordered if name == "close_app"]
            _add(("close_app",))
            return _finish(ordered, by_name, max_tools)
    _add(_match_keywords(goal))
    if classification is not None and classification.family == IntentFamily.UNKNOWN:
        try:
            from core.intent_family import MEDIA_SIGNAL

            if MEDIA_SIGNAL.search(str(goal or "")):
                _add(("play_media",))
        except Exception:
            pass
        allowed_unknown = {"web_search", "fetch_web_page", "play_media", "get_situation_briefing"}
        ordered[:] = [name for name in ordered if name in allowed_unknown]
        return _finish(ordered, by_name, max_tools)
    if classification is None:
        _add(_CORE)
    # Always allow a tiny safety valve of web when goal looks fresh.
    lowered = str(goal or "").lower()
    local_status = False
    try:
        from core.local_device_status import looks_local_device_status

        local_status = looks_local_device_status(
            goal,
            active_domain=str((active_device or {}).get("domain") or ""),
        )
    except Exception:
        local_status = False
    if (
        not local_status
        and any(token in lowered for token in ("teraz", "dzisiaj", "wczoraj", "aktual", "cena", "mecz"))
    ):
        _add(("web_search", "fetch_web_page"))
    if not ordered:
        _add(_CORE)
    return _finish(ordered, by_name, max_tools)
