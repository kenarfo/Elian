# ==========================
# ELIAN — read-only GREE climate capability diagnostic (Phase 12.3)
# ==========================
"""No HA service calls. No device mutations."""

from __future__ import annotations

from typing import Optional

from core.climate_profile import (
    GREE_PULAR_GWH09,
    ClimateCapabilityProfile,
    discover_live_climate_profiles,
    preferred_climate_profile,
)


def _fmt_list(items) -> str:
    values = [str(item) for item in (items or []) if str(item).strip()]
    return ", ".join(values) if values else "(none)"


def _fmt_related(profile: ClimateCapabilityProfile, domain: str) -> str:
    rows = [
        item
        for item in profile.related_entities
        if item.domain == domain
    ]
    if not rows:
        return "(none)"
    return "\n".join(
        f"- {item.entity_id}  name={item.name}  state={item.state}  kind={item.kind or '-'}"
        for item in rows
    )


def _bool(value: bool) -> str:
    return "True" if value else "False"


def _fmt_c(value) -> str:
    if value is None:
        return "(none)"
    rounded = round(float(value), 1)
    if abs(rounded - round(rounded)) < 0.05:
        return str(int(round(rounded)))
    return f"{rounded:.1f}"


def format_climate_capabilities(profile: Optional[ClimateCapabilityProfile]) -> str:
    physical = GREE_PULAR_GWH09
    caps = physical["physical_capabilities"]
    lines = [
        "==================================================",
        "GREE CLIMATE CAPABILITIES",
        "==================================================",
        "",
        "Manufacturer:",
        physical["manufacturer"],
        "",
        "Physical model:",
        physical["physical_model"],
        "",
        "Model ID:",
        physical["model_id"],
        "",
        "Model Version:",
        physical["model_version"],
        "",
    ]
    if profile is None:
        lines.extend(
            [
                "HA DEVICE:",
                "(not discovered — Home Assistant unavailable or no climate entity)",
                "",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "HA DEVICE:",
            f"device_id={profile.device_id or '(unknown)'}",
            f"entity_id={profile.entity_id}",
            f"friendly_name={profile.friendly_name}",
            "",
            "HVAC:",
            f"current_mode={profile.hvac_mode or profile.state}",
            f"available_modes={_fmt_list(profile.hvac_modes)}",
            "",
            "TEMPERATURE:",
            f"unit={profile.temperature_unit or '(unknown)'}",
            f"current_temperature={profile.current_temperature}",
            f"target_temperature={profile.target_temperature}",
            f"min={profile.min_temp}",
            f"max={profile.max_temp}",
            f"step={profile.target_temp_step}",
            f"spoken_celsius_current={_fmt_c(profile.spoken_current_c())}",
            f"spoken_celsius_target={_fmt_c(profile.spoken_target_c())}",
            f"spoken_celsius_min={_fmt_c(profile.spoken_min_c())}",
            f"spoken_celsius_max={_fmt_c(profile.spoken_max_c())}",
            "",
            "FAN:",
            f"current={profile.fan_mode or '(none)'}",
            f"available_modes={_fmt_list(profile.fan_modes)}",
            "",
            "VERTICAL SWING:",
            f"current={profile.swing_mode or '(none)'}",
            f"available_modes={_fmt_list(profile.swing_modes)}",
            "",
            "HORIZONTAL SWING:",
            f"current={profile.swing_horizontal_mode or '(none)'}",
            f"available_modes={_fmt_list(profile.swing_horizontal_modes)}",
            "",
            "PRESETS:",
            f"current={profile.preset_mode or '(none)'}",
            f"available={_fmt_list(profile.preset_modes)}",
            "",
            "RELATED SWITCHES:",
            _fmt_related(profile, "switch"),
            "",
            "RELATED SELECTS:",
            _fmt_related(profile, "select"),
            "",
            "RELATED SENSORS:",
            _fmt_related(profile, "sensor"),
            "",
            "RELATED BUTTONS:",
            _fmt_related(profile, "button"),
            "",
            "PHYSICAL PROFILE:",
            f"cooling={_bool(caps.get('cooling'))}",
            f"heating={_bool(caps.get('heating'))}",
            f"dry={_bool(caps.get('dry'))}",
            f"fan_only={_bool(caps.get('fan_only'))}",
            f"vertical_louver={_bool(caps.get('vertical_louver'))}",
            f"horizontal_louver={_bool(caps.get('horizontal_louver'))}",
            f"quiet_possible={_bool(caps.get('quiet_possible'))}",
            f"sleep_possible={_bool(caps.get('sleep_possible'))}",
            f"8c_heat_possible={_bool(caps.get('8c_heat_possible'))}",
            f"self_clean_possible={_bool(caps.get('self_clean_possible'))}",
            "",
            "ELIAN CONTROLLABLE:",
        ]
    )
    controllable = [key for key, value in profile.controllable_capabilities.items() if value]
    lines.append(_fmt_list(controllable) if controllable else "(none)")
    lines.extend(["", "PHYSICALLY SUPPORTED BUT NOT EXPOSED BY HA:"])
    missing = []
    mapping = {
        "quiet_possible": "quiet",
        "sleep_possible": "sleep",
        "8c_heat_possible": "8c_heat",
        "self_clean_possible": "self_clean",
        "vertical_louver": "vertical_swing",
        "horizontal_louver": "horizontal_swing",
        "turbo_possible": "turbo",
        "display_possible": "display",
        "health_possible": "health",
        "xfan_possible": "xfan",
        "beeper_possible": "beeper",
        "eco_possible": "eco",
        "fresh_air_possible": "fresh_air",
        "anti_direct_blow_possible": "anti_direct",
    }
    for physical_key, ha_key in mapping.items():
        if caps.get(physical_key) and not profile.ha_exposes(ha_key):
            missing.append(ha_key)
    lines.append(_fmt_list(missing) if missing else "(none)")
    lines.extend(["", "UNSUPPORTED / UNKNOWN:"])
    unknown = []
    if not profile.device_id:
        unknown.append("ha_device_id")
    if not profile.fan_modes:
        unknown.append("fan_modes")
    if not profile.swing_horizontal_modes:
        unknown.append("horizontal_swing")
    lines.append(_fmt_list(unknown) if unknown else "(none)")
    lines.append("")
    return "\n".join(lines)


def run_climate_capabilities() -> int:
    print("=== Elian climate-capabilities (read-only) ===")
    profiles = discover_live_climate_profiles()
    profile = preferred_climate_profile(profiles)
    text = format_climate_capabilities(profile)
    print(text)
    if profile is None:
        print("FAIL — no climate entity discovered")
        return 1
    print(f"DISCOVERED {len(profiles)} climate entit(y/ies)")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_climate_capabilities())
