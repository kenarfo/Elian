# ==========================
# ELIAN — natural SMART_HOME speech (C.5.5)
# ==========================
"""Jarvis response for HA mutations. ToolResult.message is not TTS."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from core.device_identity import (
    dedup_device_name,
    has_duplicate_device_token,
    spoken_name_for,
    strip_entity_ids,
)
from core.followup_policy import has_generic_closer, strip_generic_closers
from core.speech_guard import guard_user_facing_speech


def _cap(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    return text[0].upper() + text[1:]


def _power_adjective(name: str, *, on: bool) -> str:
    key = str(name or "").casefold()
    if key.startswith("lamp"):
        return "włączona" if on else "wyłączona"
    return "włączone" if on else "wyłączone"


def _name(data: Optional[Dict[str, Any]], utterance: str = "") -> str:
    payload = dict(data or {})
    spoken = str(payload.get("spoken_name") or "").strip()
    if spoken:
        return spoken
    return spoken_name_for(
        entity_id=str(payload.get("entity_id") or payload.get("resolved_entity_id") or ""),
        friendly_name=str(payload.get("friendly_name") or ""),
        domain=str(payload.get("domain") or ""),
        utterance=utterance,
    )


def ha_user_message(
    *,
    outcome: str,
    action: str,
    data: Optional[Dict[str, Any]] = None,
    utterance: str = "",
) -> str:
    name = _name(data, utterance)
    action = str(action or "")
    outcome = str(outcome or "").upper()
    on_adj = _power_adjective(name, on=True)
    off_adj = _power_adjective(name, on=False)
    if outcome == "ALREADY_SATISFIED":
        if action in ("device_off", "light_off", "turn_off"):
            return "Już jest wyłączone."
        if action in ("light_brightness", "adjust_brightness"):
            return "Już jest tak ustawione."
        if action in ("light_color", "light_temperature"):
            return "Już jest tak ustawione."
        return "Już jest włączone."
    if outcome == "SUCCESS":
        if action in ("device_off", "light_off", "turn_off"):
            return f"{_cap(name)} {off_adj}." if name else "Wyłączone."
        if action in ("light_brightness", "adjust_brightness"):
            pct = (data or {}).get("requested_brightness")
            label = name or "światła"
            # Avoid awkward "Jasność światło" — use genitive-ish phrasing.
            if pct is not None:
                if label.casefold() in ("światło", "swiatlo", "light"):
                    return f"Jasność ustawiona na {int(pct)} procent."
                return f"Jasność {label}: {int(pct)} procent."
            return "Jasność zmieniona."
        if action == "light_color":
            color = str((data or {}).get("color_name") or "").strip()
            if color:
                return f"Kolor {name}: {color}." if name else f"Kolor: {color}."
            return "Kolor ustawiony."
        if action == "light_temperature":
            return "Temperatura światła ustawiona."
        label = _cap(name) if name else "Światło"
        return f"{label} {on_adj}."
    if outcome in ("UNKNOWN", "PARTIAL"):
        if action in ("device_off", "light_off", "turn_off"):
            return (
                "Wysłałem polecenie, ale nie mogę potwierdzić, czy "
                f"{name or 'światło'} się wyłączyło."
            )
        if action in ("light_brightness", "adjust_brightness"):
            return (
                "Wysłałem polecenie, ale nie mogę potwierdzić jasności "
                f"{name or 'światła'}."
            )
        if action == "light_color":
            return (
                "Wysłałem polecenie, ale nie mogę potwierdzić koloru "
                f"{name or 'światła'}."
            )
        return (
            "Wysłałem polecenie, ale nie mogę potwierdzić, czy "
            f"{name or 'światło'} się włączyło."
        )
    if outcome == "UNAVAILABLE":
        return f"Nie mogę teraz połączyć się z {name or 'lampą'}."
    if outcome == "FAILED":
        return f"Nie udało się sterować: {name or 'urządzeniem'}."
    return ""


def speak_ha_result(result: Any, *, utterance: str = "") -> str:
    data = dict(getattr(result, "data", None) or {})
    action = str(data.get("ha_action") or data.get("last_action") or "")
    tool = str(getattr(result, "tool_name", "") or "")
    _HA_TOOLS = (
        "turn_on_device",
        "turn_off_device",
        "set_device_value",
        "get_device_state",
    )
    # Never invent SMART_HOME speech for preference / media / app tools.
    if tool and tool not in _HA_TOOLS and not action:
        return ""
    if tool == "get_device_state" or action in ("device_status", "light_status"):
        spoken = str(getattr(result, "message", "") or "").strip()
        spoken = dedup_device_name(strip_entity_ids(spoken))
        spoken = strip_generic_closers(spoken)
        spoken = guard_user_facing_speech(spoken)
        return spoken
    outcome = str(data.get("verification") or data.get("ha_outcome") or "")
    if not outcome:
        nested = data.get("ha_verify") if isinstance(data.get("ha_verify"), dict) else {}
        outcome = str(nested.get("verification") or nested.get("status") or "")
    raw_msg = str(getattr(result, "message", "") or "")
    if not outcome and re.search(r"nie mog[eę] potwierdzi[cć]", raw_msg, re.I):
        outcome = "UNKNOWN"
    action = str(data.get("ha_action") or data.get("last_action") or "")
    if not action:
        tool = str(getattr(result, "tool_name", "") or "")
        if tool == "turn_off_device":
            action = "turn_off"
        elif tool == "turn_on_device":
            action = "turn_on"
        elif tool == "set_device_value":
            action = str(data.get("action") or "adjust_brightness")
    if not outcome:
        status = str(getattr(result, "status", "") or "")
        if status in ("partial", "PARTIAL"):
            outcome = "UNKNOWN"
        elif getattr(result, "success", False):
            outcome = "SUCCESS"
        else:
            outcome = "FAILED"
    spoken = ha_user_message(
        outcome=outcome,
        action=action,
        data=data,
        utterance=utterance,
    )
    if not spoken:
        spoken = str(getattr(result, "message", "") or "").strip()
    spoken = dedup_device_name(strip_entity_ids(spoken))
    spoken = strip_generic_closers(spoken)
    spoken = guard_user_facing_speech(spoken)
    if has_generic_closer(spoken):
        spoken = strip_generic_closers(spoken)
    if has_duplicate_device_token(spoken):
        spoken = dedup_device_name(spoken)
    return spoken
