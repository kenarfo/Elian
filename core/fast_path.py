"""Deterministic fast path: CommandParser → ToolRegistry (no LLM).

FastPath is an execution optimization after Semantic Executive.
It is not a semantic authority and never a regex command router.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from CommandParser import looks_multi_command, parse_commands, parse_natural_comfort
from Diagnostics import log_turn
from HaActions import summarize_results
from core.tool_result import ToolStatus
from core.tools import ToolRegistry


# Parser actions we can execute through the new tool registry today.
_DEVICE_ON = {"device_on", "light_on"}
_DEVICE_OFF = {"device_off", "light_off"}
_SET_VALUE = {
    "set_temperature",
    "light_brightness",
    "light_temperature",
    "light_color",
    "adjust_temperature",
    "adjust_brightness",
    "fan_speed",
    "media_volume",
    "comfort_cold",
    "comfort_hot",
    "comfort_dark",
    "comfort_bright",
    "set_hvac_mode",
    "set_fan_mode",
    "set_swing_mode",
    "set_swing_horizontal_mode",
    "set_preset_mode",
    "press_button",
}


class FastPathStatus(str, Enum):
    HANDLED_COMPLETE = "handled_complete"
    PARTIAL_MATCH = "partial_match"
    NOT_HANDLED = "not_handled"


@dataclass
class FastPathResult:
    status: FastPathStatus
    outcome: Any = None  # str message, ToolResult, or None


def _tool_call_for_command(command):
    action = command.action
    base = {
        "target": command.target,
        "domain": command.domain,
        "entity_id": command.entity_id,
        "extra": command.extra,
        "value": command.value,
    }
    if action in _DEVICE_ON:
        return "turn_on_device", {
            "target": command.target,
            "domain": command.domain,
            "entity_id": command.entity_id,
        }
    if action in _DEVICE_OFF:
        return "turn_off_device", {
            "target": command.target,
            "domain": command.domain,
            "entity_id": command.entity_id,
        }
    if action in _SET_VALUE:
        return "set_device_value", {
            "action": action,
            **base,
        }
    if action in ("device_status", "light_status"):
        return "get_device_state", {
            "target": command.target,
            "domain": command.domain,
            "entity_id": command.entity_id,
        }
    if action == "open_app" and (command.target or command.application):
        return "open_app", {"name": command.application or command.target}
    if action == "close_app" and (command.target or command.application):
        return "close_app", {"name": command.application or command.target}
    if action in ("volume_up", "volume_down"):
        return action, {}
    if action == "web_search" and command.query:
        return "web_search", {"query": command.query}
    if action == "open_website" and (command.target or command.query):
        return "open_website", {"target": command.target or command.query}
    return None, None


def _map_commands(parsed):
    mapped = []
    for command in parsed:
        if command.clarification:
            mapped.append(("clarification", command.clarification))
            continue
        tool_name, arguments = _tool_call_for_command(command)
        if not tool_name:
            mapped.append((None, None))
            continue
        mapped.append((tool_name, (tool_name, arguments, command)))
    return mapped


def _single_close_app(text) -> Optional[str]:
    try:
        from core.compound_plan import is_cross_domain_compound
        from core.intent_family import IntentFamily, classify_intent_family

        if is_cross_domain_compound(text):
            return None
        info = classify_intent_family(text)
        if info.family != IntentFamily.APP:
            return None
        if str((info.slots or {}).get("semantic_action") or "") not in (
            "close_app",
            "close_service",
        ):
            return None
        if not info.is_complete:
            return None
        name = str((info.slots or {}).get("app_name") or "").strip()
        return name or None
    except Exception:
        return None


def log_routing_trace(path: str, reason: str, **fields) -> None:
    extras = " ".join(f"{key}={value}" for key, value in fields.items() if value not in (None, "", []))
    line = f"[ROUTING TRACE] selected_path={path} reason={reason}"
    if extras:
        line = f"{line} {extras}"
    print(line)
    log_turn("routing_trace", selected_path=path, reason=reason, **{
        key: value for key, value in fields.items() if value not in (None, "")
    })


def _envelope_for(text, envelope=None):
    if envelope is not None:
        return envelope
    try:
        from core.semantic_executive import classify_pass1

        return classify_pass1(text)
    except Exception:
        return None


def evaluate_fast_path(text, envelope=None) -> FastPathResult:
    """
    Dry-run eligibility without side effects.
    Returns PARTIAL_MATCH when multi-intent input is only partly mappable.
    FastPath must not steal questions or conversation about an action word.
    Semantics are decided first; this only checks whether a deterministic
    execution shortcut is allowed.
    """
    try:
        from core.semantic_executive import fast_path_permitted

        if not fast_path_permitted(text, envelope):
            return FastPathResult(FastPathStatus.NOT_HANDLED)
    except Exception:
        pass
    env = _envelope_for(text, envelope)
    try:
        if env is not None and str(getattr(env, "intent", "") or "") in (
            "smart_home_status",
            "situation_briefing",
            "home_overview",
            "today_schedule",
            "list_reminders",
            "weather_status",
            "missed_digest",
            "home_arrival",
            "list_notifications",
            "phone_status",
            "learned_routine",
            "procedure_run",
            "undo_action",
        ):
            return FastPathResult(FastPathStatus.HANDLED_COMPLETE)
        if env is not None and str(getattr(env, "intent", "") or "") == "smart_home_control":
            try:
                from core.read_intents import looks_activate_scene

                if looks_activate_scene(text):
                    return FastPathResult(FastPathStatus.HANDLED_COMPLETE)
            except Exception:
                pass
    except Exception:
        pass
    if parse_natural_comfort(text) is not None and not looks_multi_command(text):
        return FastPathResult(FastPathStatus.HANDLED_COMPLETE)

    try:
        from core.compound_plan import is_cross_domain_compound
        from core.service_open import looks_explicit_website

        if is_cross_domain_compound(text):
            return FastPathResult(FastPathStatus.NOT_HANDLED)
        if looks_explicit_website(text):
            return FastPathResult(FastPathStatus.NOT_HANDLED)
        from core.intent_family import COMPOUND_CONNECTOR, SECOND_ACTION

        parts = [p for p in COMPOUND_CONNECTOR.split(str(text or "")) if str(p).strip()]
        if (
            len(parts) >= 2
            and SECOND_ACTION.search(parts[0])
            and SECOND_ACTION.search(parts[1])
        ):
            return FastPathResult(FastPathStatus.NOT_HANDLED)
    except Exception:
        pass

    if _single_close_app(text):
        return FastPathResult(FastPathStatus.HANDLED_COMPLETE)

    # High-confidence complete APP/MEDIA from intent_family (natural paraphrases).
    # Prefer this over expanding CommandParser verb lists.
    try:
        from core.intent_family import IntentFamily, classify_intent_family

        classification = classify_intent_family(text)
        slots = classification.slots or {}
        if (
            classification.is_complete
            and classification.family == IntentFamily.APP
            and slots.get("app_name")
            and not looks_multi_command(text)
        ):
            return FastPathResult(FastPathStatus.HANDLED_COMPLETE)
        if (
            classification.is_complete
            and classification.family == IntentFamily.MEDIA
            and (slots.get("media_query") or slots.get("artist") or slots.get("track"))
            and not looks_multi_command(text)
        ):
            return FastPathResult(FastPathStatus.HANDLED_COMPLETE)
    except Exception:
        pass

    parsed = parse_commands(text)
    if not parsed:
        return FastPathResult(FastPathStatus.NOT_HANDLED)

    multi = looks_multi_command(text) or len(parsed) > 1
    mapped = _map_commands(parsed)
    tool_rows = [row for row in mapped if row[0] not in (None, "clarification")]
    clarifications = [row[1] for row in mapped if row[0] == "clarification"]

    if clarifications:
        return FastPathResult(
            FastPathStatus.HANDLED_COMPLETE,
            outcome=clarifications[0],
        )

    if multi:
        if len(tool_rows) < len(parsed) or len(tool_rows) < 2:
            return FastPathResult(FastPathStatus.PARTIAL_MATCH)
    elif len(tool_rows) == 0:
        return FastPathResult(FastPathStatus.NOT_HANDLED)

    if not multi and len(tool_rows) == 1:
        return FastPathResult(FastPathStatus.HANDLED_COMPLETE)

    if multi and len(tool_rows) == len(parsed) and len(tool_rows) >= 2:
        return FastPathResult(FastPathStatus.HANDLED_COMPLETE)

    if not multi:
        first = parsed[0]
        if first.action in (
            *_DEVICE_ON,
            *_DEVICE_OFF,
            *_SET_VALUE,
            "open_app",
            "close_app",
            "volume_up",
            "volume_down",
            "web_search",
            "open_website",
        ):
            return FastPathResult(FastPathStatus.HANDLED_COMPLETE)

    return FastPathResult(FastPathStatus.NOT_HANDLED)


def can_fast_path(text, envelope=None):
    """True when CommandParser yields high-confidence tool-mappable commands."""
    result = evaluate_fast_path(text, envelope)
    return result.status == FastPathStatus.HANDLED_COMPLETE


def run_fast_path(text, registry: ToolRegistry, *, confirmed=False, memory=None, envelope=None):
    """
    Execute deterministic commands via tools.
    Returns spoken message string or ToolResult, or None if miss / partial.
    Semantics + ActionFirewall still apply.
    """
    evaluation = evaluate_fast_path(text, envelope)
    if evaluation.status == FastPathStatus.NOT_HANDLED:
        return None
    if evaluation.status == FastPathStatus.PARTIAL_MATCH:
        return None

    env = _envelope_for(text, envelope)
    log_routing_trace(
        "FASTPATH",
        "high_confidence_deterministic_action",
        base_intent=str(getattr(env, "intent", "") or "") if env is not None else "",
        confidence=(
            round(float(getattr(env, "confidence", 0) or 0), 2) if env is not None else ""
        ),
        continuation=bool(getattr(env, "continuation", False)) if env is not None else False,
        tool_permission="ActionFirewall",
    )

    def _allowed(tool_name: str) -> bool:
        try:
            from core.action_firewall import allow_tool, log_firewall

            allowed, reason = allow_tool(env, tool_name, text)
            log_firewall(
                tool_name,
                allowed,
                reason,
                base_intent=str(getattr(env, "intent", "") or ""),
            )
            return bool(allowed)
        except Exception:
            return True

    if env is not None and str(getattr(env, "intent", "") or "") == "undo_action":
        if not _allowed("undo_last_action"):
            return None
        try:
            from core.read_intents import undo_count

            count = undo_count(text)
        except Exception:
            count = 1
        try:
            count = int(getattr(env, "payload", "") or count or 1)
        except (TypeError, ValueError):
            pass
        return registry.invoke("undo_last_action", {"count": max(1, count)}, confirmed=True)

    if env is not None and str(getattr(env, "intent", "") or "") == "situation_briefing":
        if not _allowed("get_situation_briefing"):
            return None
        result = registry.invoke("get_situation_briefing", {}, confirmed=True)
        if memory is not None:
            memory.note_tool_success(
                "get_situation_briefing",
                {},
                result.to_observation(),
            )
        return result

    if env is not None and str(getattr(env, "intent", "") or "") == "home_arrival":
        if not _allowed("get_situation_briefing"):
            return None
        arrival_args = {"mode": "arrival"}
        result = registry.invoke("get_situation_briefing", arrival_args, confirmed=True)
        if memory is not None:
            memory.note_tool_success(
                "get_situation_briefing",
                arrival_args,
                result.to_observation(),
            )
        return result

    if env is not None and str(getattr(env, "intent", "") or "") == "home_overview":
        if not _allowed("get_home_summary"):
            return None
        return registry.invoke("get_home_summary", {}, confirmed=True)

    if env is not None and str(getattr(env, "intent", "") or "") == "today_schedule":
        if not _allowed("get_today_schedule"):
            return None
        return registry.invoke("get_today_schedule", {}, confirmed=True)

    if env is not None and str(getattr(env, "intent", "") or "") == "list_reminders":
        if not _allowed("list_reminders"):
            return None
        return registry.invoke("list_reminders", {}, confirmed=True)

    if env is not None and str(getattr(env, "intent", "") or "") == "weather_status":
        if not _allowed("get_weather"):
            return None
        return registry.invoke("get_weather", {}, confirmed=True)

    if env is not None and str(getattr(env, "intent", "") or "") == "learned_routine":
        try:
            from core.learned_routines import lookup_learned_routine

            routine = lookup_learned_routine(text)
        except Exception:
            routine = None
        if routine is not None and _allowed(routine.tool):
            result = registry.invoke(
                routine.tool, dict(routine.arguments or {}), confirmed=True
            )
            if memory is not None:
                memory.note_tool_success(
                    routine.tool,
                    dict(routine.arguments or {}),
                    result.to_observation(),
                )
            return result
        # Missed recipe — fall through to CommandParser (volume/app/HA), not Agent-only.

    if env is not None and str(getattr(env, "intent", "") or "") == "procedure_run":
        if not _allowed("procedure_run"):
            return None
        try:
            from core.procedure_run import run_named_procedure

            return run_named_procedure(
                str(text or ""),
                registry=registry,
                confirmed=False,
            )
        except Exception:
            return None

    if env is not None and str(getattr(env, "intent", "") or "") == "list_notifications":
        if not _allowed("list_os_notifications"):
            return None
        return registry.invoke("list_os_notifications", {}, confirmed=True)

    if env is not None and str(getattr(env, "intent", "") or "") == "phone_status":
        if not _allowed("get_phone_status"):
            return None
        return registry.invoke("get_phone_status", {}, confirmed=True)

    if env is not None and str(getattr(env, "intent", "") or "") == "missed_digest":
        from core.session_digest import consume_digest_spoken
        from core.tool_result import ToolResult as _TR

        return _TR.ok(consume_digest_spoken(), tool_name="get_situation_briefing")

    if env is not None and str(getattr(env, "intent", "") or "") == "smart_home_control":
        try:
            from core.read_intents import extract_scene_name, looks_activate_scene

            if looks_activate_scene(text) and _allowed("activate_scene"):
                return registry.invoke(
                    "activate_scene",
                    {"name": extract_scene_name(text) or text},
                    confirmed=True,
                )
        except Exception:
            pass

    if env is not None and str(getattr(env, "intent", "") or "") == "smart_home_status":
        if not _allowed("get_device_state"):
            return None
        lowered = str(text or "").lower()
        domain = "light"
        if any(
            marker in lowered
            for marker in (
                "klim",
                "temperatur",
                "nawiew",
                "stopn",
                "gree",
                "chłodz",
                "chlodz",
            )
        ):
            domain = "climate"
        if domain == "climate":
            return None
        result = registry.invoke(
            "get_device_state",
            {
                "target": text,
                "name": text,
                "utterance": text,
                "domain": domain,
            },
            confirmed=True,
        )
        if memory is not None:
            memory.note_tool_success(
                "get_device_state",
                {"target": text},
                result.to_observation(),
            )
        return result

    close_name = _single_close_app(text)
    if close_name:
        from core.app_close_policy import close_invoke_confirmed

        invoke_confirmed = close_invoke_confirmed(close_name, confirmed=confirmed)
        tool_name = "close_app"
        arguments = {"name": close_name}
        try:
            from core.active_service import KIND_WEBSITE, get_active_service

            service = get_active_service()
            if (
                service is not None
                and service.service_name.lower() == close_name.lower()
                and service.resolved_kind == KIND_WEBSITE
            ):
                tool_name = "close_browser_tab"
                arguments = {"name": close_name, "tab_id": service.browser_tab_id}
                invoke_confirmed = True
        except Exception:
            pass
        if not _allowed(tool_name):
            return None
        result = registry.invoke(
            tool_name,
            arguments,
            confirmed=invoke_confirmed,
        )
        if memory is not None:
            memory.note_tool_success(
                tool_name,
                arguments,
                result.to_observation(),
            )
        return result

    # Natural APP/MEDIA bridge (intent_family slots → tools).
    try:
        from core.intent_family import IntentFamily, classify_intent_family

        classification = classify_intent_family(text)
        slots = classification.slots or {}
        if (
            classification.is_complete
            and classification.family == IntentFamily.APP
            and slots.get("app_name")
            and not looks_multi_command(text)
            and not parse_commands(text)
        ):
            app = str(slots.get("app_name") or "")
            if app and _allowed("open_app"):
                result = registry.invoke(
                    "open_app", {"name": app}, confirmed=confirmed
                )
                if memory is not None:
                    memory.note_tool_success(
                        "open_app", {"name": app}, result.to_observation()
                    )
                return result
        if (
            classification.is_complete
            and classification.family == IntentFamily.MEDIA
            and (slots.get("media_query") or slots.get("artist") or slots.get("track"))
            and not looks_multi_command(text)
            and not parse_commands(text)
        ):
            query = str(
                slots.get("media_query")
                or slots.get("artist")
                or slots.get("track")
                or ""
            )
            if query and _allowed("play_media"):
                result = registry.invoke(
                    "play_media",
                    {"query": query},
                    confirmed=confirmed,
                )
                if memory is not None:
                    memory.note_tool_success(
                        "play_media",
                        {"query": query},
                        result.to_observation(),
                    )
                return result
    except Exception:
        pass

    if parse_natural_comfort(text) is not None and not looks_multi_command(text):
        parsed = [parse_natural_comfort(text)]
    else:
        parsed = parse_commands(text)
    if not parsed:
        return None

    # Two-pass: verify full mappability before any side effect.
    mapped = _map_commands(parsed)
    if any(row[0] is None for row in mapped):
        return None
    if looks_multi_command(text) or len(parsed) > 1:
        tool_count = sum(1 for row in mapped if row[0] not in (None, "clarification"))
        if tool_count < len(parsed) or tool_count < 2:
            return None

    messages = []
    last_result = None
    for command in parsed:
        if command.clarification:
            return command.clarification

        tool_name, arguments = _tool_call_for_command(command)
        if not tool_name:
            return None

        invoke_confirmed = confirmed
        if tool_name == "close_app":
            try:
                from core.app_close_policy import close_invoke_confirmed

                invoke_confirmed = close_invoke_confirmed(
                    (arguments or {}).get("name") or "",
                    confirmed=confirmed,
                )
            except Exception:
                invoke_confirmed = confirmed
        arguments = dict(arguments or {})
        if tool_name in (
            "turn_on_device",
            "turn_off_device",
            "set_device_value",
            "get_device_state",
        ):
            arguments.setdefault("utterance", text)
        if not _allowed(tool_name):
            return None
        result = registry.invoke(tool_name, arguments, confirmed=invoke_confirmed)
        last_result = result
        if result.status == ToolStatus.NEEDS_CONFIRMATION:
            return result
        if memory is not None:
            memory.note_tool_success(
                tool_name,
                arguments,
                result.to_observation(),
            )
        messages.append(result.message)
        if not result.success and "?" in (result.message or ""):
            break

    if not messages:
        return None
    if len(messages) == 1:
        # Prefer ToolResult so CM can keep resolved_* metadata.
        return last_result if last_result is not None else messages[0]
    return summarize_results(messages)
