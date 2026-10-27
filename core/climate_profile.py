# ==========================
# ELIAN — GREE climate capability profile (Phase 12.3)
# ==========================
"""PHYSICAL vs HA_EXPOSED vs ELIAN_CONTROLLABLE — HA is runtime truth."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


GREE_PULAR_GWH09 = {
    "manufacturer": "GREE",
    "series": "Pular",
    "physical_model": "GWH09AGAXB-K6DNA4B/I",
    "model_id": "10001",
    "model_version": "V20.8",
    "capacity_kw": 2.5,
    "physical_capabilities": {
        "cooling": True,
        "heating": True,
        "dry": True,
        "fan_only": True,
        "auto": True,
        "vertical_louver": True,
        "horizontal_louver": True,
        "quiet_possible": True,
        "sleep_possible": True,
        "8c_heat_possible": True,
        "self_clean_possible": True,
        "wifi": True,
        "display_possible": True,
        "health_possible": True,
        "xfan_possible": True,
        "fresh_air_possible": False,
        "anti_direct_blow_possible": True,
        "beeper_possible": True,
        "eco_possible": True,
        "turbo_possible": True,
    },
}

# ClimateEntityFeature bits (HA climate const).
_FEAT_TARGET_TEMP = 1
_FEAT_FAN_MODE = 8
_FEAT_PRESET_MODE = 16
_FEAT_SWING_MODE = 32
_FEAT_TURN_OFF = 128
_FEAT_TURN_ON = 256
_FEAT_SWING_HORIZONTAL = 1024

RELATED_KIND_PATTERNS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("display", ("display", "panel", "led", "ekran", "indicator")),
    ("beeper", ("beeper", "buzzer", "sound", "pikan", "dzwiek", "dźwięk")),
    ("health", ("health", "ion", "joni", "purif")),
    ("xfan", ("xfan", "x-fan", "x_fan", "evaporator")),
    ("quiet", ("quiet", "silent", "cich")),
    ("turbo", ("turbo", "boost", "powerful")),
    ("sleep", ("sleep", "night", "sen", "nocn")),
    ("eco", ("eco", "econom", "power_save", "powersave", "oszczed")),
    ("8c_heat", ("8c", "8_c", "frost", "freeze", "zamarz", "heat_8")),
    ("self_clean", ("self_clean", "self-clean", "selfclean", "czyszcz")),
    ("fresh_air", ("fresh_air", "freshair", "fresh")),
    ("anti_direct", ("anti_direct", "anti-direct", "antidirect", "blow")),
    ("outdoor_temp", ("outdoor", "outside", "zewn")),
    ("indoor_temp", ("indoor", "room_temp", "wewn")),
    ("humidity", ("humidity", "wilgot")),
)


@dataclass
class RelatedEntity:
    entity_id: str
    domain: str
    name: str
    state: str = ""
    kind: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "domain": self.domain,
            "name": self.name,
            "state": self.state,
            "kind": self.kind,
        }


@dataclass
class ClimateCapabilityProfile:
    manufacturer: str = ""
    physical_model: str = ""
    model_id: str = ""
    model_version: str = ""
    entity_id: str = ""
    friendly_name: str = ""
    device_id: str = ""
    state: str = ""
    physical_capabilities: Dict[str, bool] = field(default_factory=dict)
    ha_capabilities: Dict[str, bool] = field(default_factory=dict)
    controllable_capabilities: Dict[str, bool] = field(default_factory=dict)
    hvac_modes: List[str] = field(default_factory=list)
    fan_modes: List[str] = field(default_factory=list)
    swing_modes: List[str] = field(default_factory=list)
    swing_horizontal_modes: List[str] = field(default_factory=list)
    preset_modes: List[str] = field(default_factory=list)
    related_entities: List[RelatedEntity] = field(default_factory=list)
    current_temperature: Optional[float] = None
    target_temperature: Optional[float] = None
    min_temp: Optional[float] = None
    max_temp: Optional[float] = None
    target_temp_step: Optional[float] = None
    fan_mode: str = ""
    swing_mode: str = ""
    swing_horizontal_mode: str = ""
    preset_mode: str = ""
    hvac_mode: str = ""
    temperature_unit: str = "C"
    simulated: bool = False

    def _unit_is_f(self) -> bool:
        return "F" in str(self.temperature_unit or "").upper()

    def to_celsius(self, native: Optional[float]) -> Optional[float]:
        if native is None:
            return None
        value = float(native)
        if self._unit_is_f():
            return (value - 32.0) * 5.0 / 9.0
        return value

    def spoken_target_c(self) -> Optional[float]:
        return self.to_celsius(self.target_temperature)

    def spoken_current_c(self) -> Optional[float]:
        return self.to_celsius(self.current_temperature)

    def spoken_min_c(self) -> Optional[float]:
        return self.to_celsius(self.min_temp)

    def spoken_max_c(self) -> Optional[float]:
        return self.to_celsius(self.max_temp)

    def spoken_step_c(self) -> float:
        step = float(self.target_temp_step or 0) or 0.0
        if self._unit_is_f():
            # Polish deltas are Celsius; 1°F step is not a 1° spoken change.
            return 1.0
        return step if step > 0 else 1.0

    def related_by_kind(self, kind: str) -> Optional[RelatedEntity]:
        for item in self.related_entities:
            if item.kind == kind:
                return item
        return None

    def ha_exposes(self, key: str) -> bool:
        return bool(self.ha_capabilities.get(key))

    def controllable(self, key: str) -> bool:
        return bool(self.controllable_capabilities.get(key))

    def physical(self, key: str) -> bool:
        return bool(self.physical_capabilities.get(key))

    def to_compact(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "name": self.friendly_name,
            "manufacturer": self.manufacturer,
            "hvac_modes": list(self.hvac_modes),
            "fan_modes": list(self.fan_modes),
            "swing_modes": list(self.swing_modes),
            "swing_horizontal_modes": list(self.swing_horizontal_modes),
            "preset_modes": list(self.preset_modes),
            "min_temp": self.spoken_min_c(),
            "max_temp": self.spoken_max_c(),
            "target_temp_step": self.spoken_step_c(),
            "temperature_unit": self.temperature_unit,
            "controllable": {
                key: True
                for key, value in self.controllable_capabilities.items()
                if value
            },
            "related": [item.to_dict() for item in self.related_entities],
        }


def _list_attr(attributes: Dict[str, Any], key: str) -> List[str]:
    raw = attributes.get(key) or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def infer_temperature_unit(attributes: Dict[str, Any]) -> str:
    raw = str(
        attributes.get("temperature_unit")
        or attributes.get("unit_of_measurement")
        or ""
    ).strip().upper()
    if "F" in raw:
        return "F"
    if "C" in raw:
        return "C"
    try:
        minimum = float(attributes.get("min_temp"))
        maximum = float(attributes.get("max_temp"))
    except (TypeError, ValueError):
        return "C"
    # Typical Gree/HA F ranges are 46–86; C ranges are ~16–30.
    if maximum >= 40 or minimum >= 40:
        return "F"
    return "C"


def _float_attr(attributes: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def classify_related_kind(entity_id: str, name: str) -> str:
    blob = f"{entity_id} {name}".lower().replace("-", "_")
    for kind, tokens in RELATED_KIND_PATTERNS:
        if any(token in blob for token in tokens):
            return kind
    return ""


def _decode_features(bits: int) -> Dict[str, bool]:
    bits = int(bits or 0)
    return {
        "temperature": bool(bits & _FEAT_TARGET_TEMP) or bits == 0,
        "fan": bool(bits & _FEAT_FAN_MODE),
        "preset": bool(bits & _FEAT_PRESET_MODE),
        "vertical_swing": bool(bits & _FEAT_SWING_MODE),
        "horizontal_swing": bool(bits & _FEAT_SWING_HORIZONTAL),
        "on_off": bool(bits & (_FEAT_TURN_ON | _FEAT_TURN_OFF)) or bits == 0,
    }


def _ha_caps_from_entity(
    attributes: Dict[str, Any],
    hvac_modes: Sequence[str],
    related: Sequence[RelatedEntity],
) -> Dict[str, bool]:
    modes = {str(item).lower() for item in hvac_modes}
    fan_modes = [item.lower() for item in _list_attr(attributes, "fan_modes")]
    swing = _list_attr(attributes, "swing_modes")
    horiz = _list_attr(attributes, "swing_horizontal_modes")
    presets = [item.lower() for item in _list_attr(attributes, "preset_modes")]
    kinds = {item.kind for item in related if item.kind}
    features = _decode_features(int(attributes.get("supported_features") or 0))
    return {
        "on_off": True,
        "temperature": attributes.get("temperature") is not None
        or attributes.get("min_temp") is not None
        or features.get("temperature", False),
        "cool": "cool" in modes,
        "heat": "heat" in modes,
        "dry": "dry" in modes,
        "fan_only": "fan_only" in modes,
        "auto": "auto" in modes,
        "fan": bool(fan_modes) or features.get("fan", False),
        "vertical_swing": bool(swing) or features.get("vertical_swing", False),
        "horizontal_swing": bool(horiz) or features.get("horizontal_swing", False),
        "preset": bool(presets),
        "quiet": "quiet" in fan_modes
        or "quiet" in presets
        or "quiet" in kinds,
        "turbo": "turbo" in fan_modes
        or "boost" in presets
        or "turbo" in presets
        or "turbo" in kinds,
        "sleep": any("sleep" in item for item in presets) or "sleep" in kinds,
        "eco": "eco" in presets or "power_save" in presets or "eco" in kinds,
        "8c_heat": any(
            token in " ".join(presets)
            for token in ("8", "frost", "away")
        )
        or "8c_heat" in kinds,
        "display": "display" in kinds,
        "beeper": "beeper" in kinds,
        "health": "health" in kinds,
        "xfan": "xfan" in kinds,
        "self_clean": "self_clean" in kinds,
        "fresh_air": "fresh_air" in kinds,
        "anti_direct": "anti_direct" in kinds,
        "outdoor_temp": "outdoor_temp" in kinds,
        "indoor_temp": "indoor_temp" in kinds
        or attributes.get("current_temperature") is not None,
        "humidity": "humidity" in kinds,
    }


def profile_from_ha_entity(
    entity: Any,
    *,
    related: Optional[Sequence[RelatedEntity]] = None,
    device_id: str = "",
    physical: Optional[Dict[str, Any]] = None,
    simulated: bool = False,
) -> ClimateCapabilityProfile:
    physical = dict(physical or GREE_PULAR_GWH09)
    attributes = dict(getattr(entity, "attributes", None) or {})
    hvac_modes = list(getattr(entity, "hvac_modes", None) or _list_attr(attributes, "hvac_modes"))
    related_list = list(related or [])
    ha_caps = _ha_caps_from_entity(attributes, hvac_modes, related_list)
    physical_caps = dict(physical.get("physical_capabilities") or {})
    controllable = {key: bool(ha_caps.get(key)) for key in ha_caps}
    return ClimateCapabilityProfile(
        manufacturer=str(
            attributes.get("manufacturer") or physical.get("manufacturer") or ""
        ),
        physical_model=str(physical.get("physical_model") or ""),
        model_id=str(physical.get("model_id") or ""),
        model_version=str(physical.get("model_version") or ""),
        entity_id=str(getattr(entity, "entity_id", "") or ""),
        friendly_name=str(
            getattr(entity, "friendly_name", "")
            or (entity.display_name() if hasattr(entity, "display_name") else "")
        ),
        device_id=str(device_id or ""),
        state=str(getattr(entity, "state", "") or ""),
        physical_capabilities=physical_caps,
        ha_capabilities=ha_caps,
        controllable_capabilities=controllable,
        hvac_modes=hvac_modes,
        fan_modes=_list_attr(attributes, "fan_modes"),
        swing_modes=_list_attr(attributes, "swing_modes"),
        swing_horizontal_modes=_list_attr(attributes, "swing_horizontal_modes"),
        preset_modes=_list_attr(attributes, "preset_modes"),
        related_entities=related_list,
        current_temperature=_float_attr(attributes, "current_temperature"),
        target_temperature=_float_attr(
            attributes, "temperature", "target_temperature", "target_temp_high"
        ),
        min_temp=_float_attr(attributes, "min_temp"),
        max_temp=_float_attr(attributes, "max_temp"),
        target_temp_step=_float_attr(attributes, "target_temp_step") or 1.0,
        fan_mode=str(attributes.get("fan_mode") or ""),
        swing_mode=str(attributes.get("swing_mode") or ""),
        swing_horizontal_mode=str(attributes.get("swing_horizontal_mode") or ""),
        preset_mode=str(attributes.get("preset_mode") or ""),
        hvac_mode=str(getattr(entity, "state", "") or attributes.get("hvac_mode") or ""),
        temperature_unit=infer_temperature_unit(attributes),
        simulated=simulated,
    )


def gree_pular_fixture_states() -> List[Dict[str, Any]]:
    """SIMULATED HA EXPOSURE — not a claim about the live entity."""
    attrs = {
        "friendly_name": "Klimatyzacja GREE Pular",
        "manufacturer": "GREE",
        "hvac_modes": ["off", "auto", "cool", "dry", "fan_only", "heat"],
        "hvac_mode": "cool",
        "temperature": 22,
        "current_temperature": 24,
        "min_temp": 16,
        "max_temp": 30,
        "target_temp_step": 1,
        "fan_mode": "medium",
        "fan_modes": [
            "auto",
            "low",
            "medium-low",
            "medium",
            "medium-high",
            "high",
        ],
        "swing_mode": "off",
        "swing_modes": ["off", "auto", "upper", "middle", "lower"],
        "swing_horizontal_mode": "off",
        "swing_horizontal_modes": ["off", "auto", "left", "center", "right"],
        "preset_mode": "none",
        "preset_modes": ["none", "sleep", "eco"],
        "supported_features": (
            _FEAT_TARGET_TEMP
            | _FEAT_FAN_MODE
            | _FEAT_PRESET_MODE
            | _FEAT_SWING_MODE
            | _FEAT_SWING_HORIZONTAL
            | _FEAT_TURN_ON
            | _FEAT_TURN_OFF
        ),
    }
    return [
        {
            "entity_id": "climate.gree_pular",
            "state": "cool",
            "attributes": attrs,
        },
        _switch("switch.gree_pular_panel_light", "Panel", "on"),
        _switch("switch.gree_pular_beeper", "Beeper", "on"),
        _switch("switch.gree_pular_health", "Health", "off"),
        _switch("switch.gree_pular_xfan", "X-Fan", "off"),
        _switch("switch.gree_pular_quiet", "Quiet", "off"),
        _switch("switch.gree_pular_turbo", "Turbo", "off"),
        _switch("switch.gree_pular_sleep", "Sleep", "off"),
        {
            "entity_id": "button.gree_pular_self_clean",
            "state": "unknown",
            "attributes": {"friendly_name": "Self Clean"},
        },
        {
            "entity_id": "sensor.gree_pular_outdoor_temperature",
            "state": "18",
            "attributes": {
                "friendly_name": "Outdoor temperature",
                "unit_of_measurement": "°C",
            },
        },
        {
            "entity_id": "light.vizzini",
            "state": "on",
            "attributes": {
                "friendly_name": "Inspire Vizzini D56 Vizzini",
                "supported_color_modes": ["rgb", "color_temp", "brightness"],
                "brightness": 128,
            },
        },
    ]


def _switch(entity_id: str, name: str, state: str) -> Dict[str, Any]:
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": {"friendly_name": name},
    }


def related_from_states(
    climate_entity_id: str,
    states: Sequence[Dict[str, Any]],
    *,
    allowed_ids: Optional[Sequence[str]] = None,
) -> List[RelatedEntity]:
    """Attach only same-device entities — never light.vizzini via 'light' word."""
    climate_id = str(climate_entity_id or "")
    stem = climate_id.split(".", 1)[-1] if "." in climate_id else climate_id
    allowed = {str(item) for item in (allowed_ids or []) if item}
    related: List[RelatedEntity] = []
    for state in states:
        entity_id = str(state.get("entity_id") or "")
        if not entity_id or entity_id == climate_id:
            continue
        domain = entity_id.split(".", 1)[0]
        if domain not in (
            "switch",
            "sensor",
            "binary_sensor",
            "number",
            "select",
            "button",
            "light",
        ):
            continue
        object_id = entity_id.split(".", 1)[-1]
        same_device = False
        if allowed:
            same_device = entity_id in allowed
        elif stem and (
            object_id == stem
            or object_id.startswith(stem + "_")
            or object_id.startswith(stem.replace("_", "") )
        ):
            same_device = True
        if not same_device:
            continue
        if domain == "light" and "vizzini" in entity_id.lower():
            continue
        name = str((state.get("attributes") or {}).get("friendly_name") or object_id)
        kind = classify_related_kind(entity_id, name)
        related.append(
            RelatedEntity(
                entity_id=entity_id,
                domain=domain,
                name=name,
                state=str(state.get("state") or ""),
                kind=kind,
            )
        )
    return related


def render_ha_template(template: str) -> Optional[str]:
    """Read-only HA template — not a device service call."""
    try:
        from HomeAssistant import api_request

        response = api_request("POST", "/api/template", json={"template": template})
        if response is None or not (200 <= response.status_code < 300):
            return None
        text = response.text
        if text.startswith('"') and text.endswith('"'):
            try:
                import json

                return str(json.loads(text))
            except Exception:
                return text.strip('"')
        return text.strip()
    except Exception:
        return None


def discover_device_id(entity_id: str) -> str:
    raw = render_ha_template("{{ device_id('%s') }}" % entity_id)
    if not raw or raw.lower() in ("none", "null", ""):
        return ""
    return raw.strip()


def discover_related_ids(entity_id: str) -> List[str]:
    device_id = discover_device_id(entity_id)
    if not device_id:
        return []
    raw = render_ha_template(
        "{{ device_entities('%s') | join(',') }}" % device_id.replace("'", "\\'")
    )
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_raw_states() -> List[Dict[str, Any]]:
    try:
        from HomeAssistant import api_request

        response = api_request("GET", "/api/states")
        if response is None or response.status_code != 200:
            return []
        payload = response.json()
        return payload if isinstance(payload, list) else []
    except Exception:
        return []


def discover_live_climate_profiles() -> List[ClimateCapabilityProfile]:
    """Read-only discovery against the connected HA instance."""
    from HomeAssistant import entity_from_state, get_registry

    states = load_raw_states()
    if not states:
        registry = get_registry()
        entities = registry.all(domains="climate")
        profiles = []
        for entity in entities:
            profiles.append(profile_from_ha_entity(entity))
        return profiles

    profiles: List[ClimateCapabilityProfile] = []
    for state in states:
        if str(state.get("entity_id") or "").split(".", 1)[0] != "climate":
            continue
        entity = entity_from_state(state)
        if entity is None:
            continue
        related_ids = discover_related_ids(entity.entity_id)
        related = related_from_states(
            entity.entity_id,
            states,
            allowed_ids=related_ids or None,
        )
        device_id = discover_device_id(entity.entity_id)
        profiles.append(
            profile_from_ha_entity(
                entity,
                related=related,
                device_id=device_id,
            )
        )
    return profiles


def preferred_climate_profile(
    profiles: Sequence[ClimateCapabilityProfile],
) -> Optional[ClimateCapabilityProfile]:
    if not profiles:
        return None
    for item in profiles:
        blob = f"{item.entity_id} {item.friendly_name} {item.manufacturer}".lower()
        if "gree" in blob or "pular" in blob or "klima" in blob:
            return item
    return profiles[0]


def profile_for_entity_id(entity_id: str) -> Optional[ClimateCapabilityProfile]:
    entity_id = str(entity_id or "")
    if not entity_id:
        return None
    from HomeAssistant import get_registry

    registry = get_registry()
    entity = registry.get(entity_id)
    if entity is None:
        return None
    states = []
    try:
        states = [
            {
                "entity_id": item.entity_id,
                "state": item.state,
                "attributes": dict(item.attributes or {}),
            }
            for item in registry.all()
        ]
    except Exception:
        states = []
    related = related_from_states(entity_id, states)
    return profile_from_ha_entity(entity, related=related)
