# ==========================
# ELIAN — pronoun-first device resolution (Phase 12.2)
# ==========================
"""Bind device anaphora to canonical active_device BEFORE intent completeness."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core.device_context import (
    ActiveDevice,
    followup_intent_slots,
    has_degree_unit,
    has_percent_unit,
    parse_percent_value,
)


_PRONOUN = re.compile(
    r"\b(?:ją|ja|nią|nia|go|niego|je|to|ten|tę|te|tą|ta)\b",
    re.I,
)

_DIM = re.compile(
    r"\b(?:przyciemn\w*|ściemn\w*|sciemn\w*|ciemniej(?:sz\w*)?|ciemniejsz\w*|przyga[sś]\w*)\b",
    re.I,
)
_BRIGHTEN = re.compile(
    r"\b(?:rozja[sś]n\w*|ja[sś]niej)\b",
    re.I,
)
_OFF = re.compile(
    r"(?:"
    r"\b(?:wy[lł][aą]cz|z?ga[sś]|wyga[sś]|odetnij\s+(?:pr[aą]d|zasilanie))\b|"
    r"\b(?:ściemn|sciemn|przyciemn)\w*\b.{0,16}\b(?:totaln|zupełn|całkowit|calkowit|do\s+zera|na\s+zero)\w*\b"
    r")",
    re.I,
)
_ON = re.compile(r"\b(?:w[lł][aą]cz|zapal)\b", re.I)
_SET = re.compile(r"\b(?:ustaw|zmie[nń]|daj)\b", re.I)

_EXPLICIT_OTHER_DEVICE = re.compile(
    r"\b(?:klimatyz\w*|piec|grzejnik|telewizor|tv|wentylator)\b",
    re.I,
)


ACTION_DIM = "brightness_decrease"
ACTION_BRIGHTEN = "brightness_increase"
ACTION_ON = "turn_on"
ACTION_OFF = "turn_off"
ACTION_SET_PERCENT = "light_brightness"
ACTION_SET_TEMP = "set_temperature"
ACTION_DELTA_TEMP = "adjust_temperature"

_DELTA_TEMP = re.compile(
    r"\b(?:chłodniej|chlodniej|cieplej|zimniej)\b",
    re.I,
)


def detect_device_action_hint(text: str) -> str:
    raw = str(text or "")
    # OFF before ON — "wyłącz" contains a similar stem.
    if _OFF.search(raw):
        return ACTION_OFF
    if _ON.search(raw):
        return ACTION_ON
    if _DIM.search(raw) and not _BRIGHTEN.search(raw):
        return ACTION_DIM
    if _BRIGHTEN.search(raw):
        return ACTION_BRIGHTEN
    if _DELTA_TEMP.search(raw):
        return ACTION_DELTA_TEMP
    if has_degree_unit(raw):
        return ACTION_SET_TEMP
    if parse_percent_value(raw) is not None and (
        _SET.search(raw) or has_device_pronoun(raw)
    ):
        if has_percent_unit(raw):
            return ACTION_SET_PERCENT
        return ACTION_SET_PERCENT
    return ""


@dataclass
class PronounDeviceResolution:
    pronoun_detected: bool = False
    entity_id: str = ""
    domain: str = ""
    name: str = ""
    action: str = ""
    tool: str = ""
    value: Any = None
    compatible: bool = False
    complete: bool = False
    slots: Dict[str, Any] = field(default_factory=dict)

    def to_slots(self) -> Dict[str, Any]:
        return dict(self.slots)


def has_device_pronoun(text: str) -> bool:
    return bool(_PRONOUN.search(str(text or "")))


def action_compatible_with_domain(action: str, domain: str) -> bool:
    domain = str(domain or "")
    if action in (ACTION_DIM, ACTION_BRIGHTEN, ACTION_SET_PERCENT):
        return domain == "light"
    if action in (ACTION_SET_TEMP, ACTION_DELTA_TEMP):
        return domain == "climate"
    if action in (ACTION_ON, ACTION_OFF):
        return bool(domain)
    return False


def resolve_pronoun_device_action(
    text: str,
    device: Optional[ActiveDevice],
    *,
    last_op=None,
) -> Optional[PronounDeviceResolution]:
    """
    Structured pronoun → canonical entity bind.

    Does not stringify entity_id into speech aliases.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    pronoun = has_device_pronoun(raw)
    action = detect_device_action_hint(raw)
    if not action:
        return None
    # Bind only on anaphora — named targets stay on FastPath / 12.1.
    if not pronoun:
        return None
    if _EXPLICIT_OTHER_DEVICE.search(raw):
        return PronounDeviceResolution(
            pronoun_detected=pronoun,
            action=action,
            compatible=False,
            complete=False,
        )
    if device is None or not device.is_valid:
        if pronoun:
            return PronounDeviceResolution(
                pronoun_detected=True,
                action=action,
                compatible=False,
                complete=False,
            )
        return None

    if not action_compatible_with_domain(action, device.domain):
        if action == ACTION_SET_PERCENT and device.domain == "climate":
            message = "Co w klimatyzacji chcesz ustawić na procentach?"
            if parse_percent_value(raw) is not None:
                message = (
                    "Co w klimatyzacji chcesz ustawić na "
                    f"{parse_percent_value(raw)} procent?"
                )
        elif action in (ACTION_SET_TEMP, ACTION_DELTA_TEMP) and device.domain == "light":
            message = "To światło — stopni nie ustawiam jako jasności."
        else:
            message = f"{device.name} nie obsługuje tej czynności."
        return PronounDeviceResolution(
            pronoun_detected=pronoun,
            entity_id=device.entity_id,
            domain=device.domain,
            name=device.name,
            action=action,
            compatible=False,
            complete=False,
            slots={
                "unsupported": True,
                "unsupported_message": message,
                "entity_id": device.entity_id,
                "domain": device.domain,
                "resolved_context": True,
            },
        )

    # Percent follow-up keeps existing inheritance contract.
    if action == ACTION_SET_PERCENT:
        slots = followup_intent_slots(raw, device, last_op)
        if not slots:
            return None
        return PronounDeviceResolution(
            pronoun_detected=pronoun,
            entity_id=device.entity_id,
            domain=device.domain,
            name=device.name,
            action=str(slots.get("action") or ACTION_SET_PERCENT),
            tool="set_device_value",
            value=slots.get("value"),
            compatible=True,
            complete=not slots.get("unsupported"),
            slots=slots,
        )

    if action in (ACTION_SET_TEMP, ACTION_DELTA_TEMP) and device.domain == "climate":
        from core.climate_control import parse_clause
        from core.climate_profile import profile_for_entity_id

        profile = profile_for_entity_id(device.entity_id)
        if profile is None:
            return None
        plan = parse_clause(raw, profile, last_op=last_op)
        if plan is None:
            return None
        slots = {
            "target": device.entity_id,
            "entity_id": device.entity_id,
            "domain": "climate",
            "device_name": device.name,
            "resolved_context": True,
            "pronoun_resolved": True,
            "semantic_action": plan.semantic_action,
        }
        if plan.unsupported_message or plan.clarification:
            slots["unsupported"] = True
            slots["unsupported_message"] = plan.unsupported_message or plan.clarification
            return PronounDeviceResolution(
                pronoun_detected=pronoun,
                entity_id=device.entity_id,
                domain="climate",
                name=device.name,
                action=plan.semantic_action or action,
                compatible=False,
                complete=False,
                slots=slots,
            )
        if not plan.steps:
            return None
        step = plan.steps[0]
        slots.update(step.arguments)
        return PronounDeviceResolution(
            pronoun_detected=pronoun,
            entity_id=device.entity_id,
            domain="climate",
            name=device.name,
            action=str(step.arguments.get("action") or step.ha_action),
            tool=step.tool,
            value=step.arguments.get("value"),
            compatible=True,
            complete=True,
            slots=slots,
        )

    from HaActions import DEFAULT_BRIGHTNESS_STEP

    slots: Dict[str, Any] = {
        "target": device.entity_id,
        "entity_id": device.entity_id,
        "domain": device.domain,
        "device_name": device.name,
        "resolved_context": True,
        "pronoun_resolved": True,
        "pronoun_detected": pronoun,
    }

    if action == ACTION_DIM:
        if device.capabilities and "brightness" not in device.capabilities:
            slots["unsupported"] = True
            slots["unsupported_message"] = (
                f"{device.name} nie obsługuje regulacji jasności."
            )
            return PronounDeviceResolution(
                pronoun_detected=pronoun,
                entity_id=device.entity_id,
                domain=device.domain,
                name=device.name,
                action=action,
                compatible=False,
                complete=False,
                slots=slots,
            )
        delta = -int(DEFAULT_BRIGHTNESS_STEP)
        slots.update(
            {
                "action": "adjust_brightness",
                "value": delta,
                "desired": "darker",
            }
        )
        return PronounDeviceResolution(
            pronoun_detected=pronoun,
            entity_id=device.entity_id,
            domain=device.domain,
            name=device.name,
            action="adjust_brightness",
            tool="set_device_value",
            value=delta,
            compatible=True,
            complete=True,
            slots=slots,
        )

    if action == ACTION_BRIGHTEN:
        if device.capabilities and "brightness" not in device.capabilities:
            slots["unsupported"] = True
            slots["unsupported_message"] = (
                f"{device.name} nie obsługuje regulacji jasności."
            )
            return PronounDeviceResolution(
                pronoun_detected=pronoun,
                entity_id=device.entity_id,
                domain=device.domain,
                name=device.name,
                action=action,
                compatible=False,
                complete=False,
                slots=slots,
            )
        delta = int(DEFAULT_BRIGHTNESS_STEP)
        slots.update(
            {
                "action": "adjust_brightness",
                "value": delta,
                "desired": "brighter",
                "implicit_light": True,
            }
        )
        return PronounDeviceResolution(
            pronoun_detected=pronoun,
            entity_id=device.entity_id,
            domain=device.domain,
            name=device.name,
            action="adjust_brightness",
            tool="set_device_value",
            value=delta,
            compatible=True,
            complete=True,
            slots=slots,
        )

    if action == ACTION_ON:
        slots["action"] = "turn_on"
        return PronounDeviceResolution(
            pronoun_detected=pronoun,
            entity_id=device.entity_id,
            domain=device.domain,
            name=device.name,
            action="turn_on",
            tool="turn_on_device",
            compatible=True,
            complete=True,
            slots=slots,
        )

    if action == ACTION_OFF:
        slots["action"] = "turn_off"
        return PronounDeviceResolution(
            pronoun_detected=pronoun,
            entity_id=device.entity_id,
            domain=device.domain,
            name=device.name,
            action="turn_off",
            tool="turn_off_device",
            compatible=True,
            complete=True,
            slots=slots,
        )

    return None


def tool_args_from_resolution(res: PronounDeviceResolution) -> Dict[str, Any]:
    args = {
        "target": res.entity_id,
        "entity_id": res.entity_id,
        "domain": res.domain,
    }
    if res.tool != "set_device_value":
        return args
    raw = res.value if res.value is not None else res.slots.get("value")
    extra = str(res.slots.get("extra") or "")
    if extra and not isinstance(raw, (int, float)):
        raw = extra or raw
    value: Any = raw
    if isinstance(raw, str) and extra:
        value = extra
    elif raw is not None and not isinstance(raw, str):
        try:
            value = int(raw) if float(raw).is_integer() else float(raw)
        except (TypeError, ValueError):
            value = raw
    args.update(
        {
            "action": str(res.slots.get("action") or res.action),
            "value": value if value is not None else 0,
        }
    )
    semantic = str(res.slots.get("semantic_action") or "")
    if semantic:
        args["semantic_action"] = semantic
    if extra:
        args["extra"] = extra
    return args
