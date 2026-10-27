# ==========================
# ELIAN AI ASSISTANT
# SmartHome.py
# Version 1.2
# ==========================

import re

from Config import config
from DeviceResolver import infer_domain
from HaActions import COLORS, execute_device_command
from HomeAssistant import (
    api_request,
    get_headers,
    get_registry,
    test_connection,
)
from VizziniBluetooth import (
    POWER_STATUS_QUERY,
    build_brightness_command,
    build_color_temperature_command,
    build_power_command,
    build_rgb_color_command,
    control_light_sync,
)


SMART_HOME_CONFIG = config.get("smart_home", {})

ENTITY_ID = SMART_HOME_CONFIG.get("entity_id", "").strip()
DEVICE_NAME = SMART_HOME_CONFIG.get("device_name", "lampa").strip()

TURN_ON_COMMANDS = (
    "włącz światło",
    "zapal światło",
    "włącz lampę",
    "zapal lampę",
    "włącz lampkę",
    "zapal lampkę",
    "włącz klimatyzację",
    "włącz klimę",
    "włącz klime",
)

TURN_OFF_COMMANDS = (
    "wyłącz światło",
    "zgaś światło",
    "wyłącz lampę",
    "zgaś lampę",
    "wyłącz lampkę",
    "zgaś lampkę",
    "wyłącz klimatyzację",
    "wyłącz klimę",
    "zgaś klimatyzację",
)

STATE_COMMANDS = (
    "czy światło jest włączone",
    "czy lampa jest włączona",
    "czy lampka jest włączona",
    "jaki jest stan światła",
    "jaki jest stan lampy",
    "jaki jest stan klimatyzacji",
)

LIST_COMMANDS = (
    "lista świateł",
    "pokaż światła",
    "znajdź światła",
    "jakie światła",
    "jakie masz światła",
    "jakie urządzenia",
)


def is_enabled():
    return bool(SMART_HOME_CONFIG.get("enabled", False))


def get_system():
    return SMART_HOME_CONFIG.get("system", "home_assistant")


def control_vizzini(commands):
    return control_light_sync(
        commands,
        address=SMART_HOME_CONFIG.get("bluetooth_address", "").strip(),
    )


def run_vizzini(
    turn_on,
    turn_off,
    check_state,
    list_command,
    brightness,
    color,
    color_temperature,
):
    if list_command:
        return f"Znalazłem jedno światło: {DEVICE_NAME}."

    try:
        if brightness is not None:
            commands = [build_power_command(brightness > 0)]
            if brightness > 0:
                commands.append(build_brightness_command(brightness))
            control_vizzini(commands)
            return f"Ustawiam jasność lampy na {brightness} procent."

        if color_temperature is not None:
            phrase, kelvin = color_temperature
            control_vizzini([
                build_power_command(True),
                build_color_temperature_command(kelvin),
            ])
            return f"Ustawiam {phrase}."

        if color is not None:
            color_name, rgb = color
            control_vizzini([
                build_power_command(True),
                build_rgb_color_command(*rgb),
            ])
            return f"Ustawiam kolor lampy na {color_name}."

        if turn_on or turn_off:
            enabled = turn_on and not turn_off
            control_vizzini([build_power_command(enabled)])
            action = "Włączam" if enabled else "Wyłączam"
            return f"{action} urządzenie {DEVICE_NAME}."

        if check_state:
            result = control_vizzini([POWER_STATUS_QUERY])
            power = next(
                (
                    update.current
                    for update in reversed(result.updates)
                    if update.attribute == "power"
                ),
                None,
            )
            if power is True:
                return f"Urządzenie {DEVICE_NAME} jest włączone."
            if power is False:
                return f"Urządzenie {DEVICE_NAME} jest wyłączone."
            return f"Nie mogę sprawdzić stanu urządzenia {DEVICE_NAME}."

    except Exception as error:
        print("Błąd sterowania Bluetooth:", error)
        return "Nie udało mi się połączyć z lampą przez Bluetooth."

    return None


def list_lights():
    lights = get_registry().maybe_refresh()
    return [
        {
            "entity_id": entity.entity_id,
            "name": entity.display_name(),
            "color_modes": list(entity.color_modes),
        }
        for entity in lights
        if entity.domain == "light"
    ]


def parse_brightness(text):
    if not (
        "jasność" in text
        or "jasnosc" in text
        or (
            "procent" in text
            and ("lamp" in text or "świat" in text)
        )
    ):
        return None
    match = re.search(r"(\d{1,3})\s*(?:%|procent)", text)
    if not match:
        return None
    return max(0, min(100, int(match.group(1))))


def parse_color(text):
    if not any(marker in text for marker in ("kolor", "ustaw", "świat", "lamp")):
        return None
    for name, rgb in COLORS.items():
        if name in text:
            return name, rgb
    return None


def parse_color_temperature(text):
    for phrase, kelvin in (
        ("ciepłe światło", 2700),
        ("ciepła biel", 2700),
        ("neutralne światło", 4000),
        ("neutralna biel", 4000),
        ("zimne światło", 6500),
        ("zimna biel", 6500),
    ):
        if phrase in text:
            return phrase, kelvin
    return None


def parse_temperature(text):
    match = re.search(
        r"(?:na|do)\s+(\d{1,2})\s*(?:st(?:opni(?:e)?)?)?",
        text,
    )
    if match is None:
        return None
    if "klim" not in text and "stopn" not in text and "temperatur" not in text:
        if infer_domain(text) != "climate":
            return None
    return int(match.group(1))


def match(text):
    text = text.lower().strip()
    from CommandParser import looks_multi_command, parse_natural_comfort

    # Multi-command and comfort phrases are handled by IntentRouter.
    if looks_multi_command(text) or parse_natural_comfort(text) is not None:
        return False

    return any((
        any(command in text for command in TURN_ON_COMMANDS),
        any(command in text for command in TURN_OFF_COMMANDS),
        any(command in text for command in STATE_COMMANDS),
        any(command in text for command in LIST_COMMANDS),
        parse_brightness(text) is not None,
        parse_color(text) is not None,
        parse_color_temperature(text) is not None,
        parse_temperature(text) is not None,
        infer_domain(text) in ("light", "climate", "fan", "cover", "media_player", "switch")
        and any(
            verb in text
            for verb in ("włącz", "wlacz", "wyłącz", "wylacz", "ustaw", "zgaś", "zgas", "ścisz", "scisz")
        ),
    ))


def run(text):
    text = text.lower().strip()
    turn_on = any(command in text for command in TURN_ON_COMMANDS)
    turn_off = any(command in text for command in TURN_OFF_COMMANDS)
    check_state = any(command in text for command in STATE_COMMANDS)
    list_command = any(command in text for command in LIST_COMMANDS)
    brightness = parse_brightness(text)
    color = parse_color(text)
    color_temperature = parse_color_temperature(text)
    climate_temp = parse_temperature(text)

    if not match(text):
        return None

    if not is_enabled():
        return "Sterowanie inteligentnym domem jest wyłączone w konfiguracji."

    if get_system() == "vizzini_bluetooth":
        return run_vizzini(
            turn_on,
            turn_off,
            check_state,
            list_command,
            brightness,
            color,
            color_temperature,
        )

    if get_headers() is None:
        return "Home Assistant nie jest skonfigurowany. Brakuje tokenu dostępu."

    if list_command:
        lights = list_lights()
        if not lights:
            devices = get_registry().maybe_refresh()
            if not devices:
                return "Home Assistant nie udostępnia żadnych urządzeń."
            names = ", ".join(item.display_name() for item in devices[:8])
            return f"Znalazłem {len(devices)} urządzeń: {names}."
        names = ", ".join(light["name"] for light in lights[:8])
        return f"Znalazłem {len(lights)} świateł: {names}."

    domain = infer_domain(text)
    target = text

    if climate_temp is not None:
        return execute_device_command(
            "set_temperature",
            target=target,
            domain="climate",
            value=climate_temp,
        )
    if brightness is not None:
        return execute_device_command(
            "light_brightness",
            target=target,
            domain="light",
            value=brightness,
        )
    if color_temperature is not None:
        return execute_device_command(
            "light_temperature",
            target="światło",
            domain="light",
            extra=color_temperature[0],
            value=color_temperature[1],
        )
    if color is not None:
        return execute_device_command(
            "light_color",
            target="lampa" if "lamp" in text else "światło",
            domain="light",
            extra=color[0],
        )
    if turn_on:
        return execute_device_command(
            "device_on",
            target=target,
            domain=domain or "light",
        )
    if turn_off:
        return execute_device_command(
            "device_off",
            target=target,
            domain=domain or "light",
        )
    if check_state:
        return execute_device_command(
            "device_status",
            target=target,
            domain=domain or "light",
        )

    if domain:
        action = "device_on" if "włącz" in text or "wlacz" in text else "device_off"
        return execute_device_command(action, target=target, domain=domain)

    return None
