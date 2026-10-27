# ==========================
# ELIAN — canonical device context (Phase 12)
# ==========================
"""Structured active_device + domain-locked percent follow-ups."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


_PERCENT = re.compile(
    r"(?:"
    r"(?:ustaw|zmie[nń]|zrob|zrób|daj|na)\s+"
    r"(?:ją|ja|go|je|nią|nia|to|to\s+)?\s*"
    r"(?:na|do)?\s*"
    r"|"
    r"(?:teraz|jeszcze)\s+(?:na|do)\s+"
    r"|"
    r"^(?:na|do)\s+"
    r")"
    r"(\d{1,3})\s*(?:%|procent)?\b"
    r"|"
    r"\b(\d{1,3})\s*(?:%|procent)\b",
    re.I,
)

_VALUE_ONLY = re.compile(
    r"^(?:"
    r"(?:na|do|teraz|jeszcze|daj|ustaw)\s+"
    r"(?:ją|ja|go|je|nią|nia|to\s+)?"
    r"(?:na|do\s+)?"
    r")?"
    r".{0,40}"
    r"(?:\d{1,3}|[a-ząćęłńóśźż]+(?:\s+[a-ząćęłńóśźż]+)?)"
    r"(?:\s*(?:%|procent))?"
    r"[.!?…]*$",
    re.I,
)

_SET_PERCENT = re.compile(
    r"(?:"
    r"\bustaw\b|\bzmie[nń]\b|\bzrob\b|\bzrób\b|\bdaj\b|"
    r"\bteraz\b|\bjeszcze\b|\bna\b|\bdo\b|"
    r"\b\d{1,3}\s*(?:%|procent)?\b|"
    r"%|procent"
    r")",
    re.I,
)

_SCALAR_ACTIONS = frozenset(
    {
        "light_brightness",
        "adjust_brightness",
        "media_volume",
        "set_temperature",
        "adjust_temperature",
        "fan_speed",
        "set_fan_mode",
        "climate_temperature",
        "climate_temperature_delta",
        "climate_fan_mode",
        "climate_fan_step",
    }
)


@dataclass
class ActiveDevice:
    entity_id: str = ""
    domain: str = ""
    name: str = ""
    alias: str = ""
    capabilities: List[str] = field(default_factory=list)
    last_action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "entity_id": self.entity_id,
            "domain": self.domain,
            "name": self.name,
            "capabilities": list(self.capabilities),
            "last_action": self.last_action,
        }
        if self.alias:
            data["alias"] = self.alias
        return data

    @property
    def is_valid(self) -> bool:
        return bool(self.entity_id and self.domain)


@dataclass
class LastDeviceOperation:
    entity_id: str = ""
    domain: str = ""
    action: str = ""
    value: Any = None
    unit: str = ""
    tool: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "domain": self.domain,
            "action": self.action,
            "value": self.value,
            "unit": self.unit,
            "tool": self.tool,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["LastDeviceOperation"]:
        if not isinstance(data, dict) or not data.get("entity_id"):
            return None
        return cls(
            entity_id=str(data.get("entity_id") or ""),
            domain=str(data.get("domain") or ""),
            action=str(data.get("action") or ""),
            value=data.get("value"),
            unit=str(data.get("unit") or ""),
            tool=str(data.get("tool") or ""),
        )

    @property
    def is_scalar(self) -> bool:
        return self.action in _SCALAR_ACTIONS or self.unit in (
            "percent",
            "celsius",
            "kelvin",
        )


def active_device_from_entity(
    entity: Any,
    *,
    last_action: str = "",
    alias: str = "",
    capabilities: Optional[List[str]] = None,
) -> ActiveDevice:
    domain = str(getattr(entity, "domain", "") or "")
    caps = list(capabilities or [])
    if not caps:
        if domain == "light":
            caps = ["brightness", "color", "on_off"]
        elif domain == "climate":
            caps = ["temperature", "on_off"]
        elif domain == "media_player":
            caps = ["volume", "on_off"]
        else:
            caps = ["on_off"]
    name = ""
    if hasattr(entity, "display_name"):
        try:
            name = str(entity.display_name() or "")
        except Exception:
            name = ""
    if not name:
        name = str(getattr(entity, "friendly_name", "") or getattr(entity, "entity_id", ""))
    return ActiveDevice(
        entity_id=str(getattr(entity, "entity_id", "") or ""),
        domain=domain,
        name=name,
        alias=str(alias or ""),
        capabilities=caps,
        last_action=str(last_action or ""),
    )


def extract_resolved_device(
    observation: Optional[Dict[str, Any]] = None,
    arguments: Optional[Dict[str, Any]] = None,
) -> Optional[ActiveDevice]:
    """Prefer ToolResult resolved_* metadata over raw speech aliases."""
    data = dict((observation or {}).get("data") or {})
    args = dict(arguments or {})
    entity_id = str(
        data.get("resolved_entity_id")
        or data.get("resolved_target")
        or args.get("entity_id")
        or data.get("entity_id")
        or ""
    ).strip()
    domain = str(
        data.get("resolved_domain")
        or args.get("domain")
        or data.get("domain")
        or ""
    ).strip()
    name = str(
        data.get("resolved_name")
        or data.get("friendly_name")
        or ""
    ).strip()
    alias = str(
        data.get("alias")
        or args.get("target")
        or args.get("name")
        or ""
    ).strip()
    # Never treat bare alias as entity_id.
    if entity_id and "." not in entity_id:
        if not name:
            name = entity_id
        entity_id = ""
    if not entity_id:
        return None
    if not domain and "." in entity_id:
        domain = entity_id.split(".", 1)[0]
    if not name:
        name = entity_id
    action = str(
        data.get("last_action")
        or args.get("action")
        or observation.get("tool")
        or ""
    )
    caps = list(data.get("capabilities") or [])
    if not caps:
        if domain == "light":
            caps = ["brightness", "color", "on_off"]
        elif domain == "climate":
            caps = ["temperature", "on_off"]
        elif domain == "media_player":
            caps = ["volume", "on_off"]
    return ActiveDevice(
        entity_id=entity_id,
        domain=domain,
        name=name,
        alias=alias if alias.casefold() != name.casefold() else "",
        capabilities=caps,
        last_action=action,
    )


def get_active_device(memory) -> Optional[ActiveDevice]:
    if memory is None:
        return None
    short = getattr(memory, "short", None)
    if short is None:
        return None
    entity = short.get_entity("device")
    if entity is None:
        return None
    entity_id = str(getattr(entity, "id", "") or "").strip()
    domain = str(getattr(entity, "domain", "") or "").strip()
    name = str(
        getattr(entity, "canonical_name", "")
        or getattr(entity, "name", "")
        or ""
    ).strip()
    if not entity_id or "." not in entity_id:
        return None
    if not domain:
        domain = entity_id.split(".", 1)[0]
    caps = list(getattr(entity, "capabilities", None) or [])
    aliases = list(getattr(entity, "aliases", None) or [])
    meta = dict(getattr(entity, "meta", None) or {})
    return ActiveDevice(
        entity_id=entity_id,
        domain=domain,
        name=name or entity_id,
        alias=str(meta.get("alias") or (aliases[0] if aliases else "")),
        capabilities=caps,
        last_action=str(meta.get("last_action") or ""),
    )


def sync_active_device_to_conversation_context(device: ActiveDevice) -> None:
    if not device or not device.is_valid:
        return
    try:
        from ConversationContext import update as update_ha_context

        update_ha_context(
            last_device=device.entity_id,
            last_device_name=device.name,
            last_domain=device.domain,
            last_action=device.last_action or "",
        )
    except Exception:
        pass


def has_percent_unit(text: str) -> bool:
    return bool(re.search(r"(?:%|\bprocent\b)", str(text or ""), re.I))


def has_degree_unit(text: str) -> bool:
    return bool(re.search(r"(?:stopn\w*|°\s*c|\bcelsj\w*)", str(text or ""), re.I))


def parse_percent_value(text: str) -> Optional[int]:
    raw = str(text or "").strip()
    if not raw:
        return None
    if has_degree_unit(raw) and not has_percent_unit(raw):
        return None
    match = _PERCENT.search(raw)
    if match:
        value = match.group(1) or match.group(2)
        if value is not None:
            try:
                pct = int(value)
            except (TypeError, ValueError):
                pct = None
            if pct is not None and 0 <= pct <= 100:
                return pct
    from core.pl_numbers import extract_number

    parsed = extract_number(raw)
    if parsed is not None and 0 <= parsed <= 100:
        # Bare number words / digits in value-only follow-ups count as percent
        # when previous op was brightness/volume (caller decides unit).
        return parsed
    return None


def looks_like_value_only_followup(text: str) -> bool:
    """True for 'Na 60', 'Na sześćdziesiąt', '60.', 'teraz 70' without new target."""
    raw = str(text or "").strip()
    if not raw:
        return False
    # Must not name a new device / explicit on-off.
    if re.search(
        r"\b(?:w[lł][aą]cz|wy[lł][aą]cz|otw[oó]rz|lamp[eęa]?|świat[łl]|klimatyz)\w*\b",
        raw,
        re.I,
    ):
        # "Ustaw lampę na 30%" is a full command, not value-only continuation.
        if re.search(r"\b(?:lamp|świat|swiat|klimatyz)\w*\b", raw, re.I):
            return False
    value = parse_percent_value(raw)
    if value is None:
        return False
    # Short / value-centric utterances.
    if _VALUE_ONLY.match(raw):
        return True
    if re.match(
        r"^(?:na|do|teraz|jeszcze|daj)\b",
        raw,
        re.I,
    ):
        return True
    # Bare number / number word.
    tokens = re.findall(r"[a-ząćęłńóśźż0-9%]+", raw.lower())
    return len(tokens) <= 4 and value is not None


def looks_like_percent_followup(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if parse_percent_value(raw) is None:
        return False
    if looks_like_value_only_followup(raw):
        return True
    return bool(_SET_PERCENT.search(raw))


def build_last_device_operation(
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
    observation: Optional[Dict[str, Any]] = None,
) -> Optional[LastDeviceOperation]:
    args = dict(arguments or {})
    data = dict((observation or {}).get("data") or {})
    entity_id = str(
        data.get("resolved_entity_id")
        or data.get("resolved_target")
        or args.get("entity_id")
        or ""
    ).strip()
    if not entity_id or "." not in entity_id:
        return None
    domain = str(
        data.get("resolved_domain") or args.get("domain") or entity_id.split(".", 1)[0]
    )
    action = str(args.get("action") or data.get("action") or tool_name or "")
    if tool_name in ("turn_on_device", "turn_off_device"):
        action = tool_name
    value = args.get("value", data.get("value"))
    unit = ""
    if action in ("light_brightness", "adjust_brightness", "media_volume"):
        unit = "percent"
    elif action in (
        "set_temperature",
        "adjust_temperature",
        "climate_temperature",
        "climate_temperature_delta",
    ):
        unit = "celsius"
    elif action in ("set_fan_mode", "climate_fan_mode", "climate_fan_step"):
        unit = "fan"
    return LastDeviceOperation(
        entity_id=entity_id,
        domain=domain,
        action=action,
        value=value,
        unit=unit,
        tool=str(tool_name or ""),
    )


def get_last_device_operation(memory) -> Optional[LastDeviceOperation]:
    if memory is None:
        return None
    short = getattr(memory, "short", None)
    if short is None:
        return None
    stored = getattr(short, "last_device_operation", None)
    if isinstance(stored, dict):
        return LastDeviceOperation.from_dict(stored)
    if isinstance(stored, LastDeviceOperation):
        return stored
    return None


def inherit_scalar_followup(
    text: str,
    device: ActiveDevice,
    last_op: Optional[LastDeviceOperation],
) -> Optional[Dict[str, Any]]:
    """
    Value-only follow-up inherits previous scalar action (brightness/volume/…).
    Never maps to turn_on/off.
    """
    if not device or not device.is_valid:
        return None
    if not looks_like_value_only_followup(text) and not looks_like_percent_followup(text):
        climate_degree = device.domain == "climate" and (
            has_degree_unit(text)
            or (last_op and last_op.unit == "celsius" and last_op.entity_id == device.entity_id)
        )
        if not climate_degree:
            return None
    value = parse_percent_value(text)
    if value is None:
        match = re.search(r"(-?\d{1,2})", str(text or ""))
        if match:
            try:
                value = int(match.group(1))
            except (TypeError, ValueError):
                value = None
    if value is None:
        return None

    action = ""
    unit = "percent"
    if last_op and last_op.entity_id == device.entity_id and last_op.is_scalar:
        action = last_op.action
        unit = last_op.unit or unit
        # Normalize adjust_* → absolute set for bare value.
        if action == "adjust_brightness":
            action = "light_brightness"
        if action in ("adjust_temperature", "climate_temperature_delta"):
            action = "set_temperature"
            unit = "celsius"
        if action in ("climate_temperature",):
            action = "set_temperature"
            unit = "celsius"
    if has_degree_unit(text) and device.domain == "light":
        return {
            "target": device.entity_id,
            "entity_id": device.entity_id,
            "domain": device.domain,
            "unsupported": True,
            "unsupported_message": "To światło — stopni nie ustawiam jako jasności.",
            "resolved_context": True,
        }
    if has_percent_unit(text) and device.domain == "climate":
        return {
            "target": device.entity_id,
            "entity_id": device.entity_id,
            "domain": device.domain,
            "unsupported": True,
            "unsupported_message": (
                "Co w klimatyzacji chcesz ustawić na "
                f"{int(value)} procent?"
            ),
            "resolved_context": True,
        }
    if (last_op and last_op.unit == "celsius" and device.domain == "climate") or (
        device.domain == "climate" and has_degree_unit(text)
    ):
        action = "set_temperature"
        unit = "celsius"
    if not action:
        # Domain default when no prior scalar op.
        if device.domain == "light":
            action = "light_brightness"
            unit = "percent"
        elif device.domain == "media_player":
            action = "media_volume"
            unit = "percent"
        else:
            return None

    if action in ("light_brightness", "media_volume") and not (0 <= int(value) <= 100):
        return None

    return {
        "target": device.entity_id,
        "entity_id": device.entity_id,
        "domain": device.domain,
        "device_name": device.name,
        "percent": int(value) if unit == "percent" else None,
        "value": int(value),
        "unit": unit,
        "action": action,
        "resolved_context": True,
        "inherited_action": True,
        "last_operation": (last_op.to_dict() if last_op else {}),
    }


def domain_locked_percent_action(
    device: ActiveDevice,
    percent: int,
    *,
    action: str = "",
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Map bare percent to domain-locked set_device_value args.

    Returns (arguments, error_message). Climate never accepts bare %.
    """
    if not device or not device.is_valid:
        return None, "Brak aktywnego urządzenia."
    domain = device.domain
    pct = max(0, min(100, int(percent)))
    base = {
        "target": device.entity_id,
        "entity_id": device.entity_id,
        "domain": domain,
        "value": pct,
    }
    preferred = str(action or "")
    if preferred == "adjust_brightness":
        preferred = "light_brightness"
    if preferred in ("light_brightness", "media_volume") and domain in (
        "light",
        "media_player",
    ):
        if preferred == "light_brightness" and domain != "light":
            preferred = ""
        if preferred == "media_volume" and domain != "media_player":
            preferred = ""
    if preferred == "light_brightness" or (not preferred and domain == "light"):
        if device.capabilities and "brightness" not in device.capabilities:
            return None, f"{device.name} nie obsługuje regulacji jasności."
        return {**base, "action": "light_brightness"}, None
    if preferred == "media_volume" or (not preferred and domain == "media_player"):
        return {**base, "action": "media_volume"}, None
    if domain == "climate":
        return None, (
            "Procentów nie ustawiam jako temperatury. "
            "Powiedz ile stopni Celsjusza."
        )
    return None, f"Dla {device.name} nie wiem, co oznacza {pct}%."


def followup_intent_slots(
    text: str,
    device: ActiveDevice,
    last_op: Optional[LastDeviceOperation] = None,
) -> Optional[Dict[str, Any]]:
    if not device or not device.is_valid:
        return None
    inherited = inherit_scalar_followup(text, device, last_op)
    if inherited:
        if inherited.get("unit") == "celsius" or inherited.get("action") == "set_temperature":
            if device.domain != "climate":
                inherited["unsupported"] = True
                inherited["unsupported_message"] = (
                    "To światło — stopni nie ustawiam jako jasności."
                )
                return inherited
            inherited["action"] = "set_temperature"
            inherited["unit"] = "celsius"
            inherited["resolved_context"] = True
            return inherited
        args, err = domain_locked_percent_action(
            device,
            int(inherited.get("value") or 0),
            action=str(inherited.get("action") or ""),
        )
        if err:
            inherited["unsupported"] = True
            inherited["unsupported_message"] = err
            return inherited
        assert args is not None
        inherited["action"] = args["action"]
        if device.domain == "light":
            inherited["brightness"] = inherited.get("value")
        if device.domain == "media_player":
            inherited["volume"] = inherited.get("value")
        return inherited

    if not looks_like_percent_followup(text):
        return None
    percent = parse_percent_value(text)
    if percent is None:
        return None
    action_hint = ""
    if last_op and last_op.entity_id == device.entity_id and last_op.is_scalar:
        action_hint = last_op.action
    args, err = domain_locked_percent_action(
        device, percent, action=action_hint
    )
    slots = {
        "target": device.entity_id,
        "entity_id": device.entity_id,
        "domain": device.domain,
        "device_name": device.name,
        "percent": percent,
        "resolved_context": True,
    }
    if err:
        slots["unsupported"] = True
        slots["unsupported_message"] = err
        return slots
    assert args is not None
    slots["action"] = args["action"]
    slots["value"] = percent
    if device.domain == "light":
        slots["brightness"] = percent
    if device.domain == "media_player":
        slots["volume"] = percent
    return slots


def structured_goal_overlay(text: str, device: ActiveDevice, slots: Dict[str, Any]) -> str:
    """Natural-language goal with structured identity — not raw alias only."""
    user = str(text or "").strip()
    pct = slots.get("percent")
    action = slots.get("action") or ""
    return (
        f"{user}\n"
        f"(resolved_target={device.entity_id}; domain={device.domain}; "
        f"name={device.name}; action={action}; value={pct}; unit=percent)"
    )


def canonical_tool_signature(
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
    observation: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Semantic side-effect identity for repeat guards.

    Aliases / display names collapse to entity_id.
    """
    args = dict(arguments or {})
    data = dict((observation or {}).get("data") or {})
    tool = str(tool_name or "").strip()
    entity_id = str(
        data.get("resolved_entity_id")
        or data.get("resolved_target")
        or args.get("entity_id")
        or ""
    ).strip()
    if not entity_id or "." not in entity_id:
        # Last resort: target that already looks like entity_id.
        target = str(args.get("target") or data.get("target") or "").strip()
        if "." in target and " " not in target:
            entity_id = target
    action = str(args.get("action") or data.get("action") or "").strip()
    value = args.get("value", data.get("value"))
    if tool in ("turn_on_device", "turn_off_device"):
        return f"{tool}|entity={entity_id or '?'}"
    if tool == "set_device_value":
        return f"{tool}|entity={entity_id or '?'}|action={action}|value={value}"
    # Generic: tool + sorted canonical keys
    keys = sorted(
        k
        for k in ("entity_id", "name", "app", "query", "url")
        if args.get(k) not in (None, "")
    )
    parts = [f"{k}={args.get(k)}" for k in keys]
    if entity_id:
        parts.insert(0, f"entity={entity_id}")
    return f"{tool}|" + "|".join(parts)
