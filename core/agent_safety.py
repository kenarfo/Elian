# ==========================
# ELIAN — agent safety guards (Phase 10.2 / 10.3 / 10.5)
# ==========================
"""UNKNOWN / acknowledgement / preference / correction side-effect guards.

Phase 10.5: UNKNOWN no longer blanket-blocks all tools. Policy allows AI-chosen
tools with valid args; hard-blocks destructive / preference / empty-arg probes.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Set

from core.intent_family import (
    IntentClassification,
    IntentFamily,
    has_explicit_preference_intent,
)
from core.action_firewall import MUTATION_TOOLS as FIREWALL_MUTATIONS
from core.tool_result import ToolResult


SIDE_EFFECT_TOOLS: Set[str] = {
    "open_app",
    "close_app",
    "play_media",
    "pause_media",
    "resume_media",
    "next_track",
    "previous_track",
    "open_website",
    "web_search",
    "shutdown_pc",
    "shutdown_computer",
    "shutdown",
    "restart",
    "lock_computer",
    "turn_on_device",
    "turn_off_device",
    "set_device_value",
    "create_reminder",
    "update_reminder",
    "cancel_reminder",
    "create_calendar_event",
    "update_calendar_event",
    "delete_calendar_event",
    "click_ui_element",
    "type_into_element",
    "type_text",
    "press_keys",
    "drag_ui_element",
    "close_window",
    "send_message",
    "find_contact",
    "set_preference",
    "remember_fact",
    "activate_scene",
    "procedure_run",
    "procedure_save",
    "procedure_delete",
    "browser_click",
    "browser_type",
    "browser_select",
    "browser_check",
    "browser_open_url",
    "browser_search_service",
    "browser_select_result",
    "browser_play_media",
    "close_browser_tab",
}
SIDE_EFFECT_TOOLS = SIDE_EFFECT_TOOLS | set(FIREWALL_MUTATIONS)

# Always blocked under incomplete UNKNOWN (even with args).
UNKNOWN_HARD_BLOCK: Set[str] = {
    "shutdown_pc",
    "shutdown_computer",
    "shutdown",
    "restart",
    "lock_computer",
    "set_preference",
    "remember_fact",
    "click_ui_element",
    "type_into_element",
    "press_keys",
    "drag_ui_element",
    "send_message",
    "delete_calendar_event",
    "procedure_run",
    "procedure_save",
    "procedure_delete",
    "open_app",
    "close_app",
    "play_media",
    "pause_media",
    "resume_media",
    "turn_on_device",
    "turn_off_device",
    "set_device_value",
    "create_reminder",
    "update_reminder",
    "cancel_reminder",
    "create_calendar_event",
    "update_calendar_event",
    "type_text",
    "volume_up",
    "volume_down",
    "open_website",
    "browser_click",
    "browser_type",
    "browser_select",
    "browser_check",
    "browser_open_url",
    "browser_search_service",
    "browser_select_result",
    "browser_play_media",
    "close_browser_tab",
}

REQUIRED_ANY: Dict[str, tuple[str, ...]] = {
    "open_app": ("name", "app", "app_name"),
    "close_app": ("name", "app", "app_name"),
    "play_media": ("query", "title"),
    "open_website": ("url", "target"),
    "web_search": ("query", "q"),
    "fetch_web_page": ("url", "target"),
    "create_reminder": ("text", "message"),
    "click_ui_element": ("element_id", "id", "query"),
    "type_into_element": ("element_id", "id", "text"),
    "browser_open_url": ("url", "target"),
    "browser_type": ("text", "value", "query"),
    "browser_click": ("text", "name", "label", "query", "selector", "element_id", "role"),
    "send_message": ("message", "text", "recipient", "contact"),
    "set_preference": ("key", "value"),
}

HA_WRITE_TOOLS: Set[str] = {
    "turn_on_device",
    "turn_off_device",
    "set_device_value",
}


def is_acknowledgement(classification: IntentClassification) -> bool:
    if (classification.slots or {}).get("comprehension_repair"):
        return False
    return (
        classification.family == IntentFamily.CONVERSATION_CONTROL
        and bool((classification.slots or {}).get("acknowledgement"))
    )


def is_correction(classification: IntentClassification) -> bool:
    return classification.family == IntentFamily.CORRECTION or bool(
        (classification.slots or {}).get("rejection")
        or (classification.slots or {}).get("correction")
    )


def is_side_effect_tool(tool_name: str) -> bool:
    name = str(tool_name or "").strip().lower()
    if name in SIDE_EFFECT_TOOLS:
        return True
    if "shutdown" in name or name.startswith("turn_") or name.startswith("set_device"):
        return True
    if name.startswith("create_") or name.startswith("click_") or name.startswith("type_"):
        return True
    if name in ("set_preference", "remember_fact"):
        return True
    return False


def unknown_blocks_side_effects(classification: IntentClassification) -> bool:
    """True when classifier marked incomplete UNKNOWN (AI may still act via policy)."""
    return (
        classification.family == IntentFamily.UNKNOWN
        and not classification.is_complete
    )


def _arg_present(arguments: Optional[Dict[str, Any]], keys: tuple[str, ...]) -> bool:
    args = dict(arguments or {})
    for key in keys:
        value = args.get(key)
        if value is not None and str(value).strip():
            return True
    return False


def unknown_allows_tool(
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
    classification: IntentClassification | None = None,
) -> bool:
    """
    Incomplete UNKNOWN: mutations forbidden. Read-only web may pass with a query.
    """
    name = str(tool_name or "").strip().lower()
    args = dict(arguments or {})
    if not name:
        return False
    if name in UNKNOWN_HARD_BLOCK or "shutdown" in name:
        if (
            name == "play_media"
            and classification is not None
            and re.search(
                r"\b(?:pu[sś][cć]|odtw[oó]rz|pos[lł]uch)",
                str(classification.raw_text or ""),
                re.I,
            )
        ):
            required = REQUIRED_ANY.get(name)
            if not required or _arg_present(args, required):
                return True
        return False
    if name in HA_WRITE_TOOLS:
        return _arg_present(args, ("target", "entity_id", "name"))
    required = REQUIRED_ANY.get(name)
    if required and not _arg_present(args, required):
        return False
    # Catalog side-effects with satisfied required args (or no required keys).
    if is_side_effect_tool(name):
        return True
    return True


def preference_tool_blocked(
    classification: IntentClassification,
    tool_name: str,
) -> bool:
    """set_preference / remember_fact require explicit PREFERENCE intent."""
    name = str(tool_name or "").strip().lower()
    if name not in ("set_preference", "remember_fact"):
        return False
    if has_explicit_preference_intent(classification=classification):
        return False
    return True


def side_effect_blocked_for_intent(
    classification: IntentClassification,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> bool:
    name = str(tool_name or "").strip()
    if preference_tool_blocked(classification, name):
        return True
    if not is_side_effect_tool(name):
        return False
    if is_correction(classification):
        return True
    if is_acknowledgement(classification):
        return True
    if (classification.slots or {}).get("opinion_only"):
        return True
    if unknown_blocks_side_effects(classification):
        return not unknown_allows_tool(name, arguments, classification)
    return False


def validate_side_effect_arguments(
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> ToolResult | None:
    """Extra required-arg checks before executor (empty open_app {} etc.)."""
    name = str(tool_name or "").strip()
    args = dict(arguments or {})
    keys = REQUIRED_ANY.get(name)
    if not keys:
        return None
    for key in keys:
        value = args.get(key)
        if value is not None and str(value).strip():
            return None
    return ToolResult.fail(
        f"Brakuje wymaganego argumentu: {keys[0]}.",
        tool_name=name,
        error="missing_argument",
        data={"argument": keys[0], "accepted": list(keys)},
    )


def domain_lock_percent_against_climate(
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
    *,
    active_device: Optional[Dict[str, Any]] = None,
    raw_text: str = "",
) -> ToolResult | None:
    """
    Phase 12 safety: active light + bare percent must never become climate °C.
    """
    name = str(tool_name or "").strip()
    if name not in HA_WRITE_TOOLS:
        return None
    args = dict(arguments or {})
    device = dict(active_device or {})
    active_domain = str(device.get("domain") or "").strip()
    action = str(args.get("action") or "").strip()
    arg_domain = str(args.get("domain") or "").strip()
    target = str(args.get("entity_id") or args.get("target") or "")
    text = str(raw_text or "").lower()
    percentish = "%" in text or "procent" in text
    if active_domain == "light" and percentish:
        if arg_domain == "climate" or action in (
            "set_temperature",
            "adjust_temperature",
        ):
            return ToolResult.fail(
                "To światło — nie ustawiam temperatury klimatyzacji.",
                tool_name=name,
                error="domain_lock_light_percent",
                data={
                    "active_domain": "light",
                    "blocked_domain": "climate",
                    "target": target,
                },
            )
        if target.startswith("climate."):
            return ToolResult.fail(
                "To światło — nie przełączam na klimatyzację.",
                tool_name=name,
                error="domain_lock_light_percent",
                data={"active_domain": "light", "blocked_target": target},
            )
    return None
