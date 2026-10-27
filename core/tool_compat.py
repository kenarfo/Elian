# ==========================
# ELIAN — semantic tool compatibility (Phase 13 Stage A hardware)
# ==========================
"""Reject LLM tools that contradict close_service / volume direction."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Set

from core.agent_types import AgentDecision
from core.intent_family import IntentClassification, IntentFamily

_VOLUME_DOWN = re.compile(
    r"(?:\b(?:ciszej|volume\s+down|przycisz|vol|quieter)\b|"
    r"\bturn\s+(?:the\s+)?volume\s+down\b|"
    r"\bturn\s+down\s+(?:the\s+)?volume\b|"
    r"\bzejd[zź]\s+z\s+(?:vol|g[lł]o[sś])|"
    r"\bzmniejsz\s+g[lł]o[sś]|"
    r"\b[sś]cisz)",
    re.I,
)
_VOLUME_UP = re.compile(
    r"(?:\b(?:g[lł]o[sś]niej|ustaw\s+głośniej|podgłośn|volume\s+up|louder)\b|"
    r"\bturn\s+(?:the\s+)?volume\s+up\b|"
    r"\bturn\s+up\s+(?:the\s+)?volume\b|"
    r"\bzwi[eę]ksz\s+g[lł]o[sś])",
    re.I,
)
# Soft state desires (feature for UserGoal / SYSTEM family — not FastPath dump).
_VOLUME_TOO_LOUD = re.compile(
    r"(?:\bza\s+g[lł]o[sś]no\b|\bza\s+du[zż]o\s+g[lł]o[sś]u\b|\bog[lł]usz|\btoo\s+loud\b)",
    re.I,
)
_VOLUME_TOO_QUIET = re.compile(
    r"(?:\bza\s+cicho\b|\bs[lł]abo\s+s[lł]ycha[cć]\b|\btoo\s+quiet\b|\bcan'?t\s+hear\b)",
    re.I,
)


def volume_direction_from_text(text: str) -> str:
    raw = str(text or "")
    if _VOLUME_TOO_LOUD.search(raw) and not _VOLUME_TOO_QUIET.search(raw):
        return "decrease"
    if _VOLUME_TOO_QUIET.search(raw) and not _VOLUME_TOO_LOUD.search(raw):
        return "increase"
    if _VOLUME_DOWN.search(raw) and not _VOLUME_UP.search(raw):
        return "decrease"
    if _VOLUME_UP.search(raw) and not _VOLUME_DOWN.search(raw):
        return "increase"
    if _VOLUME_DOWN.search(raw):
        return "decrease"
    if _VOLUME_UP.search(raw):
        return "increase"
    return ""


def _tools_for_semantic_action(action: str, direction: str = "") -> Set[str]:
    act = str(action or "").strip().lower()
    if act in ("close_service", "close_app"):
        return {"close_app", "close_browser_tab"}
    if act in ("open_service", "open_app"):
        return {"open_app", "open_website"}
    if act == "adjust_volume":
        if direction == "decrease":
            return {"volume_down"}
        if direction == "increase":
            return {"volume_up"}
        return {"volume_up", "volume_down"}
    if act in ("turn_off",):
        return {"turn_off_device"}
    if act in ("turn_on",):
        return {"turn_on_device"}
    if act in ("set_temperature", "climate_temperature"):
        return {"set_device_value"}
    if act in ("get_device_state", "climate_status"):
        return {"get_device_state"}
    if act in ("play_media", "play_track"):
        return {"play_media", "browser_play_media"}
    if act in (
        "type_text",
        "select_all",
        "undo",
        "click_ui_element",
        "inspect_ui",
        "focus_window",
        "list_windows",
        "get_active_window",
        "minimize_window",
        "maximize_window",
        "hotkey",
        "scroll",
    ):
        return {
            "type_text",
            "hotkey",
            "click_ui_element",
            "inspect_ui",
            "focus_window",
            "list_windows",
            "get_active_window",
            "minimize_window",
            "maximize_window",
            "restore_window",
            "close_window",
            "scroll_up",
            "scroll_down",
            "window_exists",
        }
    if act in (
        "browser_search_service",
        "browser_select_result",
        "browser_play_media",
        "close_browser_tab",
    ):
        return {act}
    return set()


def allowed_tools_for(
    goal: str,
    classification: IntentClassification | None,
) -> Optional[Set[str]]:
    """None = no extra lock. Empty set should not happen."""
    slots = dict((classification.slots if classification else None) or {})
    goal_text = str(goal or classification.raw_text if classification else goal or "")
    allowed: Set[str] = set()
    if slots.get("compound"):
        for step in slots.get("compound_steps") or []:
            if not isinstance(step, dict):
                continue
            step_slots = step.get("slots") if isinstance(step.get("slots"), dict) else {}
            direction = str(step_slots.get("direction") or "")
            if str(step.get("semantic_action") or "") == "adjust_volume" and not direction:
                direction = volume_direction_from_text(str(step.get("text") or goal_text))
            allowed |= _tools_for_semantic_action(
                str(step.get("semantic_action") or ""),
                direction,
            )
        if allowed:
            return allowed
    semantic = str(slots.get("semantic_action") or "")
    if semantic in ("close_app", "close_service"):
        return {"close_app", "close_browser_tab"}
    if semantic in ("open_app", "open_service"):
        return {"open_app", "open_website"}
    direction = volume_direction_from_text(goal_text)
    if classification and classification.family == IntentFamily.SYSTEM and direction:
        return {"volume_down"} if direction == "decrease" else {"volume_up"}
    if direction and not (
        classification and classification.family == IntentFamily.SMART_HOME
    ):
        return {"volume_down"} if direction == "decrease" else {"volume_up"}
    return None


def _compound_has_web(slots: Dict[str, Any]) -> bool:
    if not slots.get("compound"):
        return False
    for step in slots.get("compound_steps") or []:
        if not isinstance(step, dict):
            continue
        fam = str(step.get("family") or step.get("intent_family") or "").upper()
        act = str(step.get("semantic_action") or "").lower()
        if fam == "WEB" or act in ("web_search", "fetch_web_page", "open_website"):
            return True
    return False


def is_tool_compatible(
    tool_name: str,
    *,
    goal: str = "",
    classification: IntentClassification | None = None,
) -> bool:
    name = str(tool_name or "")
    slots = dict((classification.slots if classification else None) or {})
    family = classification.family if classification else None
    # Pure SMART_HOME must not invent web tools (common FakeModel / LLM failure).
    if (
        family == IntentFamily.SMART_HOME
        and name in ("web_search", "fetch_web_page", "open_website")
        and not _compound_has_web(slots)
    ):
        return False
    # Same for pure MEDIA / SYSTEM / APP when web is not part of the goal.
    if (
        family
        in (
            IntentFamily.MEDIA,
            IntentFamily.SYSTEM,
            IntentFamily.APP,
        )
        and name in ("web_search", "fetch_web_page")
        and not _compound_has_web(slots)
    ):
        return False
    allowed = allowed_tools_for(goal, classification)
    if allowed is None:
        return True
    return name in allowed


def repair_incompatible_tool(
    tool_name: str,
    *,
    goal: str,
    classification: IntentClassification,
    arguments: Dict[str, Any] | None = None,
) -> Optional[AgentDecision]:
    name = str(tool_name or "")
    if is_tool_compatible(name, goal=goal, classification=classification):
        return None
    slots = dict(classification.slots or {})
    family = classification.family
    # SMART_HOME + accidental web → remap to device action from slots/hint.
    if family == IntentFamily.SMART_HOME and name in (
        "web_search",
        "fetch_web_page",
        "open_website",
    ):
        hint = str(slots.get("semantic_action") or "").lower()
        try:
            from core.device_pronoun import detect_device_action_hint

            if not hint:
                hint = detect_device_action_hint(str(goal or classification.raw_text or ""))
        except Exception:
            pass
        entity = str(
            slots.get("entity_id")
            or slots.get("resolved_entity_id")
            or slots.get("target")
            or ""
        ).strip()
        args: Dict[str, Any] = {}
        if entity:
            args["entity_id"] = entity
            args["target"] = entity
        if hint in ("turn_off", "device_off", "light_off"):
            return AgentDecision(
                type="tool_call",
                tool="turn_off_device",
                arguments=args,
                raw={"incompatible_tool_repaired": True, "rejected": name},
            )
        if hint in ("turn_on", "device_on", "light_on"):
            return AgentDecision(
                type="tool_call",
                tool="turn_on_device",
                arguments=args,
                raw={"incompatible_tool_repaired": True, "rejected": name},
            )
        if hint in ("brightness_decrease", "brightness_increase", "light_brightness"):
            args["action"] = "adjust_brightness"
            args["domain"] = "light"
            args["value"] = -20 if "decrease" in hint else 20
            return AgentDecision(
                type="tool_call",
                tool="set_device_value",
                arguments=args,
                raw={"incompatible_tool_repaired": True, "rejected": name},
            )
        return AgentDecision(
            type="final_answer",
            message="Nie wyszukuję tego w internecie — to dotyczy domu. Powiedz jasno: włącz, zgaś albo przyciemnij.",
            raw={"incompatible_tool_blocked": True, "rejected": name},
        )
    allowed = allowed_tools_for(goal, classification) or set()
    volume_tools = {"volume_up", "volume_down"}
    if name in volume_tools:
        if "volume_down" in allowed and "volume_up" not in allowed:
            return AgentDecision(
                type="tool_call",
                tool="volume_down",
                arguments={},
                raw={"incompatible_tool_repaired": True, "rejected": name},
            )
        if "volume_up" in allowed and "volume_down" not in allowed:
            return AgentDecision(
                type="tool_call",
                tool="volume_up",
                arguments={},
                raw={"incompatible_tool_repaired": True, "rejected": name},
            )
        return AgentDecision(
            type="final_answer",
            message="Nie zrobię tego w ten sposób.",
            raw={"incompatible_tool_blocked": True, "rejected": name},
        )
    if "close_app" in allowed:
        app = str(slots.get("app_name") or slots.get("target") or "").strip()
        if not app:
            for step in slots.get("compound_steps") or []:
                if str(step.get("semantic_action") or "") in ("close_service", "close_app"):
                    step_slots = step.get("slots") if isinstance(step.get("slots"), dict) else {}
                    app = str(step_slots.get("target") or step_slots.get("app_name") or "").strip()
                    break
        if app:
            return AgentDecision(
                type="tool_call",
                tool="close_app",
                arguments={"name": app},
                raw={"incompatible_tool_repaired": True, "rejected": name},
            )
    if allowed == {"volume_down"}:
        return AgentDecision(
            type="tool_call",
            tool="volume_down",
            arguments={},
            raw={"incompatible_tool_repaired": True, "rejected": name},
        )
    if allowed == {"volume_up"}:
        return AgentDecision(
            type="tool_call",
            tool="volume_up",
            arguments={},
            raw={"incompatible_tool_repaired": True, "rejected": name},
        )
    return AgentDecision(
        type="final_answer",
        message="Nie zrobię tego w ten sposób.",
        raw={"incompatible_tool_blocked": True, "rejected": name},
    )


_INVENTED_TOOL = re.compile(
    r"(?:[ąćęłńóśźż]|^(?:ustaw|wlacz|wylacz|zapal|zgas)_)",
    re.I,
)


def remap_invented_ha_tool(
    tool_name: str,
    *,
    goal: str,
    arguments: Dict[str, Any] | None = None,
) -> Optional[AgentDecision]:
    """Map LLM-invented Polish tool names onto CommandParser → set_device_value."""
    name = str(tool_name or "").strip()
    if not name or not _INVENTED_TOOL.search(name):
        return None
    try:
        from CommandParser import parse_commands

        commands = parse_commands(goal)
    except Exception:
        return None
    if not commands:
        return None
    command = commands[0]
    action = str(command.action or "")
    if action in (
        "light_color",
        "light_brightness",
        "light_temperature",
        "adjust_brightness",
        "set_temperature",
    ):
        return AgentDecision(
            type="tool_call",
            tool="set_device_value",
            arguments={
                "action": action,
                "target": command.target,
                "domain": command.domain or "light",
                "entity_id": command.entity_id,
                "extra": command.extra,
                "value": command.value,
                "utterance": goal,
            },
            raw={"invented_tool_remapped": True, "rejected": name},
        )
    if action in ("device_on", "light_on"):
        return AgentDecision(
            type="tool_call",
            tool="turn_on_device",
            arguments={
                "target": command.target,
                "domain": command.domain or "light",
                "entity_id": command.entity_id,
                "utterance": goal,
            },
            raw={"invented_tool_remapped": True, "rejected": name},
        )
    if action in ("device_off", "light_off"):
        return AgentDecision(
            type="tool_call",
            tool="turn_off_device",
            arguments={
                "target": command.target,
                "domain": command.domain or "light",
                "entity_id": command.entity_id,
                "utterance": goal,
            },
            raw={"invented_tool_remapped": True, "rejected": name},
        )
    return None
