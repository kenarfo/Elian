# ==========================
# ELIAN — compound step → ToolRegistry (Phase 13 Stage B)
# ==========================
"""Map ordered semantic steps to tools. Unavailable BrowserAgent stays honest."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from core.active_service import (
    CAP_CLOSE,
    CAP_PLAY_TRACK,
    KIND_WEBSITE,
    get_active_service,
    has_capability,
)
from core.intent_family import IntentFamily
from core.tool_compat import volume_direction_from_text

LOCAL_COMPUTER = "LOCAL_COMPUTER"
LOCAL_HA = "LOCAL_HA"
LOCAL_MEDIA = "LOCAL_MEDIA"
LOCAL_VISION = "LOCAL_VISION"
SOURCE_WEB = "WEB"
SOURCE_KNOWLEDGE = "KNOWLEDGE"


def source_kind_for_step(family: str, semantic_action: str, text: str = "") -> str:
    fam = str(family or "")
    action = str(semantic_action or "")
    if fam == IntentFamily.SMART_HOME.value or action in (
        "turn_off",
        "turn_on",
        "set_temperature",
        "get_device_state",
        "climate_status",
    ):
        return LOCAL_HA
    if fam == IntentFamily.APP.value or action in ("open_service", "close_service"):
        return LOCAL_COMPUTER
    if fam == IntentFamily.SYSTEM.value or action == "adjust_volume":
        return LOCAL_COMPUTER
    if fam == IntentFamily.COMPUTER.value or fam == IntentFamily.VISION.value:
        return LOCAL_COMPUTER
    if fam == IntentFamily.MEDIA.value or action in ("play_media", "play_track"):
        return LOCAL_MEDIA
    if fam == IntentFamily.WEB.value or action.startswith("browser_"):
        return SOURCE_WEB
    if fam == IntentFamily.KNOWLEDGE.value:
        return SOURCE_KNOWLEDGE
    if "klima" in str(text or "").lower() and action.startswith("climate"):
        return LOCAL_HA
    return fam or LOCAL_COMPUTER


def resolve_step_tool(
    step,
    *,
    context: Dict[str, Any] | None = None,
) -> Tuple[str, Dict[str, Any], str]:
    """Return (tool_name, arguments, unavailable_reason)."""
    ctx = dict(context or {})
    action = str(getattr(step, "semantic_action", "") or "")
    slots = dict(getattr(step, "slots", None) or {})
    family = str(getattr(step, "family", "") or "")
    text = str(getattr(step, "text", "") or "")
    target = str(slots.get("target") or slots.get("app_name") or "").strip()

    if action == "open_service":
        arguments = {"name": target, "text": text}
        if slots.get("website_direct"):
            arguments["website_direct"] = True
        if slots.get("explicit_app"):
            arguments["explicit_app"] = True
        return "open_app", arguments, ""

    if action == "close_service":
        service = get_active_service()
        kind = ""
        if service and service.service_name.lower() == target.lower():
            kind = service.resolved_kind
        elif service and not target:
            target = service.service_name
            kind = service.resolved_kind
        if kind == KIND_WEBSITE or (
            kind and not has_capability(kind, CAP_CLOSE, service_name=target)
        ):
            return (
                "close_browser_tab",
                {"name": target, "tab_id": getattr(service, "browser_tab_id", "") if service else ""},
                "browser_tab_close",
            )
        return "close_app", {"name": target}, ""

    if action == "adjust_volume":
        direction = str(slots.get("direction") or volume_direction_from_text(text) or "increase")
        tool = "volume_down" if direction == "decrease" else "volume_up"
        return tool, {}, ""

    if action == "turn_off":
        domain = str(slots.get("domain") or "")
        entity_id = str(slots.get("entity_id") or "")
        return (
            "turn_off_device",
            {
                "target": target or slots.get("target") or text,
                "domain": domain,
                "entity_id": entity_id,
            },
            "",
        )

    if action == "turn_on":
        return (
            "turn_on_device",
            {
                "target": target or slots.get("target") or text,
                "domain": str(slots.get("domain") or ""),
                "entity_id": str(slots.get("entity_id") or ""),
            },
            "",
        )

    if action in ("set_temperature", "climate_temperature"):
        value = slots.get("value") or slots.get("temperature") or 0
        return (
            "set_device_value",
            {
                "action": "set_temperature",
                "target": target or "klima",
                "domain": "climate",
                "entity_id": str(slots.get("entity_id") or ""),
                "value": value,
                "semantic_action": "climate_temperature",
            },
            "",
        )

    if action in ("play_media", "play_track"):
        query = str(slots.get("query") or slots.get("media_query") or slots.get("artist") or text)
        args = {"query": query}
        if slots.get("app_name") or slots.get("preferred_service"):
            args["app"] = str(slots.get("app_name") or slots.get("preferred_service"))
        service = get_active_service()
        kind = str(getattr(service, "resolved_kind", "") or "")
        if kind == KIND_WEBSITE and not has_capability(
            kind, CAP_PLAY_TRACK, service_name=getattr(service, "service_name", "") or target
        ):
            return (
                "browser_play_media",
                {"query": query, "name": target or getattr(service, "service_name", "")},
                "browser_play_media",
            )
        return "play_media", args, ""

    if action in (
        "browser_search_service",
        "browser_select_result",
        "browser_play_media",
        "close_browser_tab",
    ):
        return action, {"query": slots.get("query") or text, "name": target}, action

    if action in ("get_device_state", "climate_status") or (
        family == IntentFamily.SMART_HOME.value and "co " in text.lower()
    ):
        return (
            "get_device_state",
            {
                "target": target or "klima",
                "entity_id": str(slots.get("entity_id") or ""),
            },
            "",
        )

    if family == IntentFamily.VISION.value:
        return "describe_screen", {}, ""

    if family == "PROCEDURE" or str(action).startswith("procedure_"):
        pid = slots.get("procedure_id") or action
        if str(pid).startswith("procedure_"):
            pid = str(pid)[len("procedure_") :]
        return (
            "procedure_run",
            {"procedure_id": pid},
            "",
        )

    if family == IntentFamily.COMPUTER.value or action in (
        "type_text",
        "select_all",
        "undo",
        "click_ui_element",
        "inspect_ui",
        "focus_window",
        "minimize_window",
        "maximize_window",
        "restore_window",
        "close_window",
        "list_windows",
        "get_active_window",
        "scroll",
        "hotkey",
        "press_key",
        "window_exists",
    ):
        tool_map = {
            "type_text": "type_text",
            "select_all": "hotkey",
            "undo": "hotkey",
            "copy": "hotkey",
            "paste": "hotkey",
            "cut": "hotkey",
            "click_ui_element": "click_ui_element",
            "click_referent": "click_ui_element",
            "inspect_ui": "inspect_ui",
            "inspect_screen": "inspect_ui",
            "focus_window": "focus_window",
            "minimize_window": "minimize_window",
            "maximize_window": "maximize_window",
            "restore_window": "restore_window",
            "close_window": "close_window",
            "list_windows": "list_windows",
            "get_active_window": "get_active_window",
            "window_exists": "window_exists",
            "scroll": "scroll_down" if "up" not in str(slots.get("direction") or "").lower() else "scroll_up",
            "press_key": "hotkey",
            "hotkey": "hotkey",
            "delete_last_word": "type_text",
        }
        tool = tool_map.get(action, action or "inspect_ui")
        arguments = {
            "text": slots.get("text_payload") or "",
            "query": slots.get("query") or text,
            "app_name": slots.get("app_name") or target,
            "keys": {
                "select_all": "ctrl+a",
                "undo": "ctrl+z",
                "copy": "ctrl+c",
                "paste": "ctrl+v",
                "cut": "ctrl+x",
            }.get(action, str(slots.get("query") or "enter")),
            "direction": slots.get("direction") or "",
            "semantic_action": action,
        }
        if tool in ("focus_window", "minimize_window", "maximize_window", "restore_window", "close_window", "window_exists", "wait_for_window"):
            arguments["name"] = target or slots.get("app_name") or ""
        if tool == "type_text":
            raw_span = str(slots.get("raw_clause") or text or "")
            normalized_span = str(slots.get("normalized_clause") or raw_span.lower())
            tool_payload = str(slots.get("text_payload") or "")
            if raw_span:
                try:
                    from core.computer_semantics import extract_type_payload

                    extracted = extract_type_payload(raw_span)
                    if extracted:
                        tool_payload = extracted
                except Exception:
                    pass
            print(
                f"[RAW PAYLOAD] raw_span={raw_span!r} "
                f"normalized_span={normalized_span!r} tool_payload={tool_payload!r}"
            )
            arguments = {
                "text": tool_payload,
                "app_name": slots.get("app_name") or target,
                "append": bool(slots.get("append")),
                "semantic_action": action,
            }
        if tool == "hotkey":
            arguments = {
                "keys": arguments["keys"],
                "semantic_action": action,
                "app_name": slots.get("app_name") or target,
            }
        if tool == "click_ui_element":
            arguments = {"query": slots.get("query") or text, "semantic_action": action}
        return tool, arguments, ""

    if family == IntentFamily.KNOWLEDGE.value or action in (
        "talk",
        "knowledge",
        "creative_humor",
        "answer",
    ):
        return "_talk", {"text": text, "intent": action or "talk"}, ""
