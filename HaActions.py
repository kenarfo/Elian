from Config import config
from ConversationContext import get as get_context, update as update_context
from DeviceResolver import infer_domain, reject_domain_mismatch, resolve_device
from HomeAssistant import api_request, get_registry
import math


COLORS = {
    "czerwony": [255, 0, 0],
    "czerwona": [255, 0, 0],
    "zielony": [0, 255, 0],
    "zielona": [0, 255, 0],
    "niebieski": [0, 0, 255],
    "niebieska": [0, 0, 255],
    "żółty": [255, 255, 0],
    "żółta": [255, 255, 0],
    "fioletowy": [160, 32, 240],
    "fioletowa": [160, 32, 240],
}

# Canonical relative step for "trochę ciemno" / jasniej intents.
DEFAULT_BRIGHTNESS_STEP = 15

DEFAULT_COLOR_TEMPERATURES = {
    "very_warm": 2200,
    "warm": 2700,
    "ciepłe": 2700,
    "ciepla": 2700,
    "cieple": 2700,
    "neutral": 4000,
    "neutralne": 4000,
    "neutralna": 4000,
    "daylight": 5000,
    "dzienne": 5000,
    "cool": 6500,
    "zimne": 6500,
    "zimna": 6500,
}


def _color_temperature_map():
    configured = config.get("smart_home", {}).get("color_temperatures", {})
    mapping = dict(DEFAULT_COLOR_TEMPERATURES)
    if isinstance(configured, dict):
        for key, value in configured.items():
            try:
                mapping[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
    # Polish aliases for configured keys
    mapping.setdefault("ciepłe", mapping.get("warm", 2700))
    mapping.setdefault("cieple", mapping.get("warm", 2700))
    mapping.setdefault("neutralne", mapping.get("neutral", 4000))
    mapping.setdefault("zimne", mapping.get("cool", 6500))
    mapping.setdefault("dzienne", mapping.get("daylight", 5000))
    mapping.setdefault("bardzo ciepłe", mapping.get("very_warm", 2200))
    return mapping


def _call_service(domain, service, entity_id, data=None):
    payload = {"entity_id": entity_id}
    if data:
        payload.update(data)
    response = api_request(
        "POST",
        f"/api/services/{domain}/{service}",
        json=payload,
    )
    if response is not None and 200 <= response.status_code < 300:
        return True
    if response is not None:
        print("Błąd sterowania HA:", response.status_code)
    return False


def _supports_light_mode(entity, mode):
    if not entity.color_modes:
        return True
    if mode == "brightness":
        return bool(set(entity.color_modes) - {"onoff"})
    if mode == "color_temp":
        return "color_temp" in entity.color_modes or not entity.color_modes
    if mode == "rgb":
        return bool({"rgb", "hs", "xy", "rgbw", "rgbww"} & set(entity.color_modes))
    return True


def entity_supports_brightness(entity) -> bool:
    if entity is None or getattr(entity, "domain", "") != "light":
        return False
    return _supports_light_mode(entity, "brightness")


def _remember(entity, action, value=None):
    update_context(
        last_device=entity.entity_id,
        last_device_name=entity.display_name(),
        last_domain=entity.domain,
        last_action=action,
        last_value=value if value is not None else "",
    )


def _refresh_entity(entity):
    registry = get_registry()
    registry.maybe_refresh()
    return registry.get(entity.entity_id) or entity


def _clamp_kelvin(entity, kelvin):
    minimum = int(entity.attributes.get("min_color_temp_kelvin", 2000) or 2000)
    maximum = int(entity.attributes.get("max_color_temp_kelvin", 6500) or 6500)
    return max(minimum, min(maximum, int(kelvin)))


def _current_brightness(entity):
    raw = entity.attributes.get("brightness")
    if raw is None:
        context_value = get_context().get("last_value")
        if isinstance(context_value, (int, float)) and get_context().get("last_domain") == "light":
            return int(context_value)
        return 50 if entity.state == "on" else 0
    return int(round(int(raw) * 100 / 255))


def _temperature_unit(entity):
    """Return 'C' or 'F' for a climate entity."""
    attributes = entity.attributes if entity is not None else {}
    from core.climate_profile import infer_temperature_unit

    inferred = infer_temperature_unit(attributes)
    if inferred in ("C", "F"):
        return inferred

    configured = str(
        config.get("smart_home", {}).get("temperature_unit", "")
    ).strip().upper()
    if configured.startswith("F"):
        return "F"
    return "C"


def celsius_to_fahrenheit(celsius):
    return (float(celsius) * 9.0 / 5.0) + 32.0


def fahrenheit_to_celsius(fahrenheit):
    return (float(fahrenheit) - 32.0) * 5.0 / 9.0


def _to_celsius(value, unit):
    value = float(value)
    if unit == "F":
        return fahrenheit_to_celsius(value)
    return value


def _from_celsius(value_c, unit):
    value_c = float(value_c)
    if unit == "F":
        return celsius_to_fahrenheit(value_c)
    return value_c


def snap_temperature(native, step):
    """Snap to HA target_temp_step with half-up (not banker's, not floor)."""
    step = float(step or 1) or 1.0
    units = float(native) / step
    snapped = math.floor(units + 0.5) * step
    return round(snapped, 1)


def describe_temperature_conversion(celsius, unit, step):
    """Diagnostic record — no HA calls."""
    unit = "F" if str(unit or "").upper().find("F") >= 0 else "C"
    raw_native = _from_celsius(float(celsius), unit)
    payload = snap_temperature(raw_native, step)
    return {
        "requested_celsius": float(celsius),
        "converted_fahrenheit": raw_native if unit == "F" else None,
        "ha_service_payload": payload,
        "converted_back_celsius": _to_celsius(payload, unit),
        "rounding_policy": "nearest_step_half_up",
        "unit": unit,
        "step": float(step or 1) or 1.0,
    }


def _native_temperature(entity):
    for key in ("temperature", "target_temp_high", "target_temperature"):
        value = entity.attributes.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    value = entity.attributes.get("current_temperature")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _current_temperature_celsius(entity):
    unit = _temperature_unit(entity)
    native = _native_temperature(entity)
    if native is not None:
        return _to_celsius(native, unit)

    context_value = get_context().get("last_value")
    if isinstance(context_value, (int, float)) and get_context().get("last_domain") == "climate":
        # Context stores spoken Celsius values.
        return float(context_value)
    return None


def _format_spoken_celsius(celsius):
    value = float(celsius)
    rounded = round(value, 1)
    if abs(rounded - round(rounded)) < 0.05:
        number = str(int(round(rounded)))
    else:
        number = f"{rounded:.1f}".replace(".", ",")
    return f"{number} stopni"


def _current_temperature(entity):
    # Backwards-compatible helper: returns Celsius for relative math.
    return _current_temperature_celsius(entity)


def resolve_entity(target="", domain="", entity_id=""):
    if entity_id:
        registry = get_registry()
        entity = registry.get(entity_id)
        if entity is None:
            registry.maybe_refresh(force=True)
            entity = registry.get(entity_id)
        if entity is not None:
            mismatch = reject_domain_mismatch(domain, entity) if domain else None
            if mismatch:
                return None, mismatch
            return entity, None
        return None, f"Nie znalazłem urządzenia {entity_id}."

    requested_domain = domain or infer_domain(target)
    match = resolve_device(target, domain=requested_domain)
    if match.status == "ambiguous":
        return None, match.message
    if match.status in ("none", "mismatch"):
        return None, match.message
    if match.entity is None:
        return None, "Nie znalazłem takiego urządzenia w Home Assistant."
    mismatch = reject_domain_mismatch(requested_domain, match.entity)
    if mismatch:
        return None, mismatch
    return match.entity, None


def execute_on_entity(action, entity, value=None, extra="", utterance=""):
    domain = entity.domain
    name = entity.display_name()
    entity = _refresh_entity(entity)
    spoken_utt = str(utterance or extra or "")

    # Alias abstract relative actions → canonical adjust_brightness / light_brightness.
    if action in ("brightness_up", "increase_brightness", "brightness_increase"):
        return execute_on_entity(
            "adjust_brightness",
            entity,
            value=int(value or DEFAULT_BRIGHTNESS_STEP),
            utterance=spoken_utt,
        )
    if action in ("brightness_down", "decrease_brightness", "brightness_decrease"):
        step = int(value or DEFAULT_BRIGHTNESS_STEP)
        return execute_on_entity(
            "adjust_brightness",
            entity,
            value=-abs(step),
            utterance=spoken_utt,
        )

    if action in ("device_on", "light_on", "turn_on"):
        if domain in ("light", "switch"):
            from core.ha_verify import execute_verified

            return execute_verified(
                action, entity, utterance=spoken_utt
            ).message
        service = "turn_on"
        if domain == "cover":
            service = "open_cover"
        if domain == "media_player":
            service = "media_play"
        if domain == "climate" and "heat" not in entity.hvac_modes and "cool" in entity.hvac_modes:
            # Prefer turning climate on via HVAC mode when available.
            if _call_service("climate", "turn_on", entity.entity_id) or _call_service(
                "climate",
                "set_hvac_mode",
                entity.entity_id,
                {"hvac_mode": "cool"},
            ):
                _remember(entity, action)
                return f"Włączam {name}."
            return f"Nie udało mi się włączyć: {name}."
        if _call_service(domain, service, entity.entity_id):
            _remember(entity, action)
            return f"Włączam {name}."
        return f"Nie udało mi się włączyć: {name}."

    if action in ("device_off", "light_off", "turn_off"):
        if domain in ("light", "switch"):
            from core.ha_verify import execute_verified

            return execute_verified(
                action, entity, utterance=spoken_utt
            ).message
        service = "turn_off"
        if domain == "cover":
            service = "close_cover"
        if domain == "media_player":
            service = "media_stop"
        if _call_service(domain, service, entity.entity_id):
            _remember(entity, action)
            return f"Wyłączam {name}."
        return f"Nie udało mi się wyłączyć: {name}."

    if action in ("light_status", "device_status"):
        try:
            from core.device_identity import identity_from_entity

            name = identity_from_entity(entity, utterance=spoken_utt).spoken_name or name
            if name:
                name = name[0].upper() + name[1:]
        except Exception:
            pass
        if domain == "climate":
            current_c = _current_temperature_celsius(entity)
            temp_bit = (
                f"Temperatura ustawiona na {_format_spoken_celsius(current_c)}."
                if current_c is not None
                else "Nie mam aktualnej temperatury z Home Assistanta."
            )
            if entity.state in ("on", "cool", "heat", "auto", "heat_cool", "dry", "fan_only"):
                return f"{name} jest włączone. {temp_bit}"
            if entity.state in ("off", "idle"):
                last = (
                    f"Ostatnia temperatura: {_format_spoken_celsius(current_c)}."
                    if current_c is not None
                    else temp_bit
                )
                return f"{name} jest wyłączone. {last}"
            return f"Stan {name}: {entity.state}. {temp_bit}"
        if entity.state in ("on", "cool", "heat", "auto", "open", "playing", "heat_cool"):
            return f"{name} jest włączone."
        if entity.state in ("off", "closed", "idle", "paused"):
            return f"{name} jest wyłączone."
        return f"Stan {name}: {entity.state}."

    if action == "light_brightness":
        if domain != "light":
            return reject_domain_mismatch("light", entity) or (
                f"{name} nie obsługuje jasności."
            )
        if not _supports_light_mode(entity, "brightness"):
            return f"{name} nie obsługuje regulacji jasności."
        from core.ha_verify import execute_verified

        return execute_verified(
            action, entity, value=value, utterance=spoken_utt
        ).message

    if action == "adjust_brightness":
        if domain != "light":
            return reject_domain_mismatch("light", entity)
        current = _current_brightness(entity)
        brightness = max(0, min(100, current + int(value or 0)))
        return execute_on_entity(
            "light_brightness",
            entity,
            value=brightness,
            utterance=spoken_utt,
        )

    if action == "light_color":
        if domain != "light" or not _supports_light_mode(entity, "rgb"):
            return f"{name} nie obsługuje kolorów."
        color_name = str(extra or value or "").strip().lower()
        rgb = COLORS.get(color_name)
        if rgb is None:
            return "Nie rozpoznałem koloru."
        from core.ha_verify import execute_verified

        return execute_verified(
            action, entity, extra=color_name, utterance=spoken_utt
        ).message

    if action == "light_temperature":
        if domain != "light" or not _supports_light_mode(entity, "color_temp"):
            return f"{name} nie obsługuje temperatury światła."
        phrase = str(extra or "").strip().lower()
        mapping = _color_temperature_map()
        kelvin = mapping.get(phrase) or mapping.get(phrase.split()[0] if phrase else "")
        if kelvin is None:
            try:
                kelvin = int(value or 0)
            except (TypeError, ValueError):
                kelvin = 0
        if not kelvin:
            return "Nie rozpoznałem temperatury światła."
        kelvin = _clamp_kelvin(entity, kelvin)
        if _call_service(
            "light",
            "turn_on",
            entity.entity_id,
            {"color_temp_kelvin": kelvin},
        ):
            _remember(entity, action, kelvin)
            label = phrase or "temperaturę światła"
            return f"Ustawiam {label}."
        return "Nie udało mi się ustawić temperatury światła."

    if action == "set_temperature":
        if domain != "climate":
            return reject_domain_mismatch("climate", entity) or (
                f"{name} nie jest klimatyzacją."
            )

        unit = _temperature_unit(entity)
        spoken_unit = str(extra or "").strip().lower()
        if spoken_unit in ("f", "fahrenheit", "°f", "farenheit"):
            celsius = fahrenheit_to_celsius(float(value))
        else:
            # Polish spoken values default to Celsius.
            celsius = float(value)

        native_raw = _from_celsius(celsius, unit)
        minimum = float(entity.attributes.get("min_temp", 60 if unit == "F" else 16))
        maximum = float(entity.attributes.get("max_temp", 86 if unit == "F" else 30))
        step = float(entity.attributes.get("target_temp_step") or 1) or 1.0
        native = snap_temperature(native_raw, step)
        if native > maximum + 0.05:
            spoken_max = _to_celsius(maximum, unit)
            spoken_max = int(spoken_max) if abs(spoken_max - round(spoken_max)) < 0.05 else spoken_max
            return f"Ta klimatyzacja pozwala ustawić maksymalnie {spoken_max} stopni."
        if native < minimum - 0.05:
            spoken_min = _to_celsius(minimum, unit)
            spoken_min = int(spoken_min) if abs(spoken_min - round(spoken_min)) < 0.05 else spoken_min
            return f"Ta klimatyzacja pozwala ustawić minimalnie {spoken_min} stopni."

        spoken_c = _to_celsius(native, unit)
        diag = describe_temperature_conversion(celsius, unit, step)
        print(
            "[CLIMATE TEMP] "
            f"requested_celsius={diag['requested_celsius']} "
            f"converted_fahrenheit={diag['converted_fahrenheit']} "
            f"HA_service_payload={native} "
            f"converted_back_celsius={diag['converted_back_celsius']} "
            f"rounding_policy={diag['rounding_policy']}"
        )
        if _call_service(
            "climate",
            "set_temperature",
            entity.entity_id,
            {"temperature": native},
        ):
            _remember(entity, action, round(spoken_c, 1))
            return f"Ustawiam {name} na {_format_spoken_celsius(spoken_c)}."
        return f"Nie udało mi się ustawić temperatury {name}."

    if action == "adjust_temperature":
        if domain != "climate":
            return reject_domain_mismatch("climate", entity)
        current_c = _current_temperature_celsius(entity)
        if current_c is None:
            return "Nie mam aktualnej temperatury z Home Assistanta."
        return execute_on_entity(
            "set_temperature",
            entity,
            value=current_c + float(value or 0),
        )

    if action in ("comfort_cold", "comfort_hot"):
        if domain != "climate":
            return reject_domain_mismatch("climate", entity)
        state = (entity.state or "").lower()
        delta = int(value or (2 if action == "comfort_cold" else -2))
        if state in ("cool", "cooling"):
            # User is cold while cooling -> raise setpoint; hot -> lower.
            return execute_on_entity("adjust_temperature", entity, value=delta)
        if state in ("heat", "heating"):
            return execute_on_entity("adjust_temperature", entity, value=delta)
        if state in ("off", "idle", "fan_only"):
            if action == "comfort_cold":
                if "heat" in entity.hvac_modes:
                    return (
                        f"{name} jest wyłączona. "
                        "Mam włączyć ogrzewanie?"
                    )
                return execute_on_entity("adjust_temperature", entity, value=delta)
            if "cool" in entity.hvac_modes:
                return (
                    f"{name} jest wyłączona. "
                    "Mam włączyć chłodzenie?"
                )
            return execute_on_entity("adjust_temperature", entity, value=delta)
        # heat_cool / auto / unknown: adjust temperature without guessing mode
        return execute_on_entity("adjust_temperature", entity, value=delta)

    if action in ("comfort_dark", "comfort_bright"):
        if domain != "light":
            return reject_domain_mismatch("light", entity)
        if entity.state == "off" and action == "comfort_dark":
            if _call_service("light", "turn_on", entity.entity_id, {"brightness_pct": 40}):
                _remember(entity, "device_on", 40)
                return f"Włączam {name}."
            return f"Nie udało mi się włączyć: {name}."
        return execute_on_entity("adjust_brightness", entity, value=int(value or 0))

    if action == "fan_speed":
        if domain != "fan":
            return reject_domain_mismatch("fan", entity)
        percent = max(0, min(100, int(value or 0)))
        if _call_service("fan", "set_percentage", entity.entity_id, {"percentage": percent}):
            _remember(entity, action, percent)
            return f"Ustawiam {name} na {percent} procent."
        return f"Nie udało mi się ustawić prędkości {name}."

    if action == "media_volume":
        if domain != "media_player":
            return reject_domain_mismatch("media_player", entity)
        volume = max(0, min(100, int(value or 0))) / 100
        if _call_service(
            "media_player",
            "volume_set",
            entity.entity_id,
            {"volume_level": volume},
        ):
            _remember(entity, action, int(value or 0))
            return f"Ustawiam głośność {name} na {int(value or 0)} procent."
        return f"Nie udało mi się zmienić głośności {name}."

    if action == "media_pause":
        if _call_service("media_player", "media_pause", entity.entity_id):
            _remember(entity, action)
            return f"Pauzuję {name}."
        return f"Nie udało mi się zapauzować {name}."

    if action == "cover_stop":
        if _call_service("cover", "stop_cover", entity.entity_id):
            _remember(entity, action)
            return f"Zatrzymuję {name}."
        return f"Nie udało mi się zatrzymać {name}."

    if action == "set_hvac_mode":
        if domain != "climate":
            return reject_domain_mismatch("climate", entity)
        mode = str(extra or value or "").strip()
        allowed = [str(item) for item in (entity.hvac_modes or entity.attributes.get("hvac_modes") or [])]
        if mode not in allowed and mode.lower() not in {item.lower() for item in allowed}:
            return "Ta klimatyzacja nie udostępnia tego trybu przez Home Assistant."
        canonical = next((item for item in allowed if item.lower() == mode.lower()), mode)
        if _call_service("climate", "set_hvac_mode", entity.entity_id, {"hvac_mode": canonical}):
            _remember(entity, action, canonical)
            return f"Ustawiam tryb {name} na {canonical}."
        return f"Nie udało mi się ustawić trybu {name}."

    if action == "set_fan_mode":
        if domain != "climate":
            return reject_domain_mismatch("climate", entity)
        mode = str(extra or value or "").strip()
        allowed = [str(item) for item in (entity.attributes.get("fan_modes") or [])]
        if allowed and mode.lower() not in {item.lower() for item in allowed}:
            return "Ta klimatyzacja nie udostępnia tego nawiewu przez Home Assistant."
        canonical = next((item for item in allowed if item.lower() == mode.lower()), mode)
        if _call_service("climate", "set_fan_mode", entity.entity_id, {"fan_mode": canonical}):
            _remember(entity, action, canonical)
            return f"Ustawiam nawiew {name} na {canonical}."
        return f"Nie udało mi się ustawić nawiewu {name}."

    if action == "set_swing_mode":
        if domain != "climate":
            return reject_domain_mismatch("climate", entity)
        mode = str(extra or value or "").strip()
        allowed = [str(item) for item in (entity.attributes.get("swing_modes") or [])]
        if allowed and mode.lower() not in {item.lower() for item in allowed}:
            return "Ta klimatyzacja nie udostępnia tego ustawienia żaluzji przez Home Assistant."
        canonical = next((item for item in allowed if item.lower() == mode.lower()), mode)
        if _call_service("climate", "set_swing_mode", entity.entity_id, {"swing_mode": canonical}):
            _remember(entity, action, canonical)
            return f"Ustawiam żaluzje {name} na {canonical}."
        return f"Nie udało mi się ustawić żaluzji {name}."

    if action == "set_swing_horizontal_mode":
        if domain != "climate":
            return reject_domain_mismatch("climate", entity)
        mode = str(extra or value or "").strip()
        allowed = [str(item) for item in (entity.attributes.get("swing_horizontal_modes") or [])]
        if allowed and mode.lower() not in {item.lower() for item in allowed}:
            return (
                "Ta klimatyzacja obsługuje tę funkcję, ale Home Assistant "
                "nie udostępnia mi obecnie sterowania nią."
            )
        canonical = next((item for item in allowed if item.lower() == mode.lower()), mode)
        if _call_service(
            "climate",
            "set_swing_horizontal_mode",
            entity.entity_id,
            {"swing_horizontal_mode": canonical},
        ):
            _remember(entity, action, canonical)
            return f"Ustawiam poziomy nawiew {name} na {canonical}."
        return f"Nie udało mi się ustawić poziomego nawiewu {name}."

    if action == "set_preset_mode":
        if domain != "climate":
            return reject_domain_mismatch("climate", entity)
        mode = str(extra or value or "").strip()
        allowed = [str(item) for item in (entity.attributes.get("preset_modes") or [])]
        if allowed and mode.lower() not in {item.lower() for item in allowed}:
            return "Ta klimatyzacja nie udostępnia tego presetu przez Home Assistant."
        canonical = next((item for item in allowed if item.lower() == mode.lower()), mode)
        if _call_service("climate", "set_preset_mode", entity.entity_id, {"preset_mode": canonical}):
            _remember(entity, action, canonical)
            return f"Ustawiam preset {name} na {canonical}."
        return f"Nie udało mi się ustawić presetu {name}."

    if action == "press_button":
        if _call_service(domain or "button", "press", entity.entity_id):
            _remember(entity, action)
            return f"Uruchamiam {name}."
        return f"Nie udało mi się uruchomić: {name}."

    return None


def execute_device_command(
    action,
    target="",
    domain="",
    value=None,
    extra="",
    fallback_entity=None,
    entity_id="",
    utterance="",
):
    entity, error = resolve_entity(target=target, domain=domain, entity_id=entity_id)
    if error:
        return error
    if entity is None:
        entity = fallback_entity
    if entity is None:
        return "Nie znalazłem takiego urządzenia w Home Assistant."

    result = execute_on_entity(
        action,
        entity,
        value=value,
        extra=extra or target,
        utterance=utterance or extra or target,
    )
    if result:
        return result
    return f"Nie obsługuję tej czynności dla {entity.display_name()}."


def summarize_results(results):
    successes = []
    failures = []
    clarifications = []
    for result in results:
        if not result:
            continue
        lowered = result.lower()
        if "?" in result and any(
            marker in lowered
            for marker in ("które", "ktore", "czy", "mam włączyć", "mam wlaczyc")
        ):
            clarifications.append(result)
        elif any(
            marker in lowered
            for marker in (
                "nie udało",
                "nie znalazłem",
                "nie mam",
                "niedostęp",
                "nie obsługuje",
                "nie wykonam",
                "nie rozpoznałem",
            )
        ):
            failures.append(result)
        else:
            successes.append(result)

    if clarifications and not successes and not failures:
        return clarifications[0]
    if len(results) == 1:
        return results[0]
    if successes and not failures and not clarifications:
        return " ".join(successes)
    if successes and failures:
        return (
            " ".join(successes)
            + " "
            + " ".join(failures).replace("Nie udało mi się", "Nie udało się")
        ).strip()
    if failures and not successes:
        return " ".join(failures)
    parts = successes + failures + clarifications
    return " ".join(parts)
