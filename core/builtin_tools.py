"""Adapters that wrap existing Elian modules as ToolRegistry entries."""

from __future__ import annotations

import psutil

from AppLauncher import find_start_menu_app, launch_application
from Config import get_config_value
from HaActions import execute_device_command, resolve_entity
from Skills import Music, System
from WebActions import open_website, search_web, search_web_structured
import ComputerControl

from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from core.web_fetch import fetch_web_page_text
from core.web_search_provider import provider_available


def _resolved_device_data(
    *,
    target="",
    domain="",
    entity_id="",
    last_action="",
    extra=None,
):
    """Resolve HA entity and expose canonical ToolResult metadata."""
    data = dict(extra or {})
    if target:
        data.setdefault("alias", target)
        data.setdefault("target", target)
    entity, error = resolve_entity(target=target, domain=domain, entity_id=entity_id)
    if entity is None:
        if error:
            data["resolve_error"] = error
        return data, None, error
    caps = []
    try:
        from HaActions import entity_supports_brightness

        if entity.domain == "light":
            caps = ["on_off"]
            if entity_supports_brightness(entity):
                caps.append("brightness")
            if getattr(entity, "color_modes", None):
                caps.append("color")
        elif entity.domain == "climate":
            caps = ["temperature", "on_off"]
            try:
                from core.climate_profile import profile_from_ha_entity

                profile = profile_from_ha_entity(entity)
                for key, flag in profile.controllable_capabilities.items():
                    if flag and key not in caps:
                        caps.append(key)
                compact = profile.to_compact()
                data.update(
                    {
                        "hvac_modes": compact.get("hvac_modes") or [],
                        "fan_modes": compact.get("fan_modes") or [],
                        "swing_modes": compact.get("swing_modes") or [],
                        "swing_horizontal_modes": compact.get("swing_horizontal_modes") or [],
                        "preset_modes": compact.get("preset_modes") or [],
                        "min_temp": compact.get("min_temp"),
                        "max_temp": compact.get("max_temp"),
                        "target_temp_step": compact.get("target_temp_step"),
                    }
                )
            except Exception:
                pass
        elif entity.domain == "media_player":
            caps = ["volume", "on_off"]
        else:
            caps = ["on_off"]
    except Exception:
        caps = ["on_off"]
    data.update(
        {
            "resolved_entity_id": entity.entity_id,
            "resolved_target": entity.entity_id,
            "resolved_domain": entity.domain,
            "resolved_name": entity.display_name(),
            "entity_id": entity.entity_id,
            "domain": entity.domain,
            "capabilities": caps,
            "last_action": last_action or "",
        }
    )
    try:
        from core.device_identity import identity_from_entity

        ident = identity_from_entity(entity)
        data["spoken_name"] = ident.spoken_name
        data["canonical_name"] = ident.canonical_name
        data["friendly_name"] = ident.friendly_name
    except Exception:
        pass
    return data, entity, None


def _message_success(message):
    lowered = str(message or "").lower()
    failure_markers = (
        "nie udało",
        "nie znalazłem",
        "nie mam",
        "nie wykonam",
        "nieprawidł",
        "nie obsługuj",
        "nie obsluguj",
        "nie rozpoznałem",
        "nie rozpoznaje",
        "pozwala ustawić maksymalnie",
        "pozwala ustawic maksymalnie",
        "pozwala ustawić minimalnie",
        "nie udostępnia",
        "nie udostepnia",
        "które",
        "ktore",
        "mam włączyć",
        "mam wlaczyc",
        "nie mogę potwierdzić",
        "nie moge potwierdzic",
        "nie mogę teraz połączyć",
        "nie moge teraz polaczyc",
        "nie mogę połączyć",
        "nie moge polaczyc",
        "błąd połączenia",
        "blad polaczenia",
        "błąd polaczenia",
        "błąd połączenia z",
        "timed out",
        "timeout",
        "connecttimeout",
        "unreachable",
        "connection refused",
        "connection error",
        "błąd sieci",
        "blad sieci",
        "wysłałem polecenie, ale",
        "wyslalem polecenie, ale",
    )
    return bool(message) and not any(marker in lowered for marker in failure_markers)


def _tool_result_from_verify(message, *, tool_name="", data=None):
    from core.ha_verify import take_last_verify
    from core.tool_result import ToolResult

    payload = dict(data or {})
    verify = take_last_verify()
    if verify is not None:
        payload.update(verify.data or {})
        outcome = str(verify.outcome or "")
        text = str(verify.message or message or "")
        if outcome == "UNKNOWN":
            return ToolResult.unknown_side_effect(text, tool_name=tool_name, data=payload)
        if outcome in ("SUCCESS", "ALREADY_SATISFIED"):
            return ToolResult.ok(text, data=payload, tool_name=tool_name)
        return ToolResult.fail(text, tool_name=tool_name, data=payload)
    return _tool_result_from_ha_message(message, tool_name=tool_name, data=payload)


def _message_unsupported(message):
    lowered = str(message or "").lower()
    return any(
        marker in lowered
        for marker in (
            "nie obsługuj",
            "nie obsluguj",
            "nie obsługuje regulacji",
            "nie obsluguje regulacji",
        )
    )


def _tool_result_from_ha_message(message, *, tool_name="", data=None):
    text = str(message or "")
    lowered = text.lower()
    if _message_unsupported(text):
        return ToolResult.unsupported(text, tool_name=tool_name, data=data)
    if "nie mogę potwierdzić" in lowered or "nie moge potwierdzic" in lowered:
        return ToolResult.unknown_side_effect(text, tool_name=tool_name, data=data)
    if _message_success(text):
        return ToolResult.ok(text, data=data, tool_name=tool_name)
    return ToolResult.fail(text, tool_name=tool_name, data=data)


def _check_app_installed(arguments):
    name = str(arguments.get("name") or arguments.get("app") or "").strip()
    if not name:
        return ToolResult.fail(
            "Nie podano nazwy aplikacji.",
            tool_name="check_app_installed",
        )
    path = find_start_menu_app(name)
    if path:
        return ToolResult.ok(
            f"{name} jest zainstalowane.",
            data={"app": name, "path": path, "installed": True},
            tool_name="check_app_installed",
        )
    return ToolResult.fail(
        f"Nie znalazłem aplikacji {name}.",
        data={"app": name, "installed": False},
        tool_name="check_app_installed",
        retryable=False,
    )


def _open_app(arguments):
    name = str(arguments.get("name") or arguments.get("app") or "").strip()
    if not name:
        return ToolResult.fail("Nie podano nazwy aplikacji.", tool_name="open_app")
    from core.official_services import canonical_service_name
    from core.service_open import (
        KIND_APP,
        ServiceOpenResult,
        looks_explicit_app_wording,
        looks_explicit_website,
        open_named_service,
    )

    text = str(arguments.get("text") or "")
    website_direct = bool(arguments.get("website_direct")) or looks_explicit_website(text)
    explicit_app = bool(arguments.get("explicit_app")) or looks_explicit_app_wording(text)
    canon = canonical_service_name(name)

    if website_direct:
        opened = open_named_service(name, text=text or name, website_direct=True)
        data = opened.to_dict()
        data["app"] = opened.canonical_name or name
        data["target"] = opened.official_url or name
        if opened.success:
            result = ToolResult.ok(opened.message, data=data, tool_name="open_app")
            _note_opened_service(arguments, result)
            return result
        return ToolResult.fail(
            opened.message or f"Nie udało mi się otworzyć {name}.",
            data=data,
            tool_name="open_app",
            retryable=False,
        )

    success, message = launch_application(name)
    if success:
        spoken = "Spotify otwarte." if canon.lower() == "spotify" else f"{canon} otwarte."
        data = ServiceOpenResult(
            requested_name=name,
            resolved_kind=KIND_APP,
            canonical_name=canon,
            app_found=True,
            launched=True,
            success=True,
            fallback_used=False,
            app_checked=True,
            message=spoken,
            tool="open_app",
        ).to_dict()
        data["app"] = canon
        result = ToolResult.ok(spoken, data=data, tool_name="open_app")
        _note_opened_service(arguments, result)
        return result

    opened = open_named_service(name, text=text or name, website_direct=True)
    opened.fallback_used = True
    opened.app_checked = True
    opened.app_found = False
    if opened.success:
        if explicit_app:
            opened.message = (
                f"Nie znalazłem aplikacji {canon}, więc otworzyłem oficjalną stronę {canon}."
            )
        else:
            opened.message = (
                f"Nie znalazłem aplikacji {canon}, więc otworzyłem jej oficjalną stronę."
            )
    data = opened.to_dict()
    data["app"] = opened.canonical_name or name
    data["target"] = opened.official_url or name
    if opened.success:
        result = ToolResult.ok(opened.message, data=data, tool_name="open_app")
        _note_opened_service(arguments, result)
        return result
    return ToolResult.fail(
        opened.message or message or f"Nie udało mi się otworzyć {name}.",
        data=data,
        tool_name="open_app",
        retryable=False,
    )


def _note_opened_service(arguments, result: ToolResult) -> None:
    try:
        from core.active_service import note_service_from_observation

        note_service_from_observation(
            tool_name="open_app",
            arguments=dict(arguments or {}),
            observation=result.to_observation(),
        )
    except Exception:
        pass
    try:
        from core.computer_agent import get_computer_agent

        name = str(
            arguments.get("name")
            or arguments.get("app")
            or (result.data or {}).get("app")
            or ""
        ).strip()
        if name and result.success:
            get_computer_agent().bind_working_target(name)
    except Exception:
        pass


def _close_app(arguments):
    name = str(arguments.get("name") or arguments.get("app") or "").strip()
    if not name:
        return ToolResult.fail("Nie podano nazwy aplikacji.", tool_name="close_app")
    # Automated suite / diagnostics: never kill the user's real Spotify (etc.).
    # Normal voice/agent close keeps allow_user_owned=True.
    automated = bool(arguments.get("automated") or arguments.get("test_cleanup"))
    allow_user_owned = bool(arguments.get("allow_user_owned", not automated))
    if automated or not allow_user_owned:
        try:
            from core.app_lifecycle_guard import should_block_close

            playing = arguments.get("media_playing")
            block, reason = should_block_close(
                name,
                explicit_user_request=False,
                media_playing=playing if isinstance(playing, bool) else None,
            )
            if block:
                return ToolResult.fail(
                    f"Pomijam zamknięcie {name} ({reason}).",
                    data={"app": name, "blocked": reason, "protected_user_app": True},
                    tool_name="close_app",
                    retryable=False,
                )
        except Exception:
            pass
    try:
        from core.active_service import KIND_WEBSITE, get_active_service
        from core.browser_contract import close_browser_tab

        service = get_active_service()
        if (
            service is not None
            and service.service_name.lower() == name.lower()
            and service.resolved_kind == KIND_WEBSITE
        ):
            return close_browser_tab(
                {"name": name, "tab_id": service.browser_tab_id}
            )
    except Exception:
        pass
    message = ComputerControl.close_application(
        name, allow_user_owned=allow_user_owned
    )
    if _message_success(message):
        spoken = f"{name} zamknięte." if name else message
        return ToolResult.ok(spoken, data={"app": name}, tool_name="close_app")
    return ToolResult.fail(message or "Nie udało się zamknąć aplikacji.", tool_name="close_app")


def _read_system_volume():
    """OS master-volume snapshot for volume mutation readback (pycaw)."""
    try:
        from pycaw.pycaw import AudioUtilities

        endpoint = AudioUtilities.GetSpeakers().EndpointVolume
        scalar = float(endpoint.GetMasterVolumeLevelScalar())
        mute = bool(endpoint.GetMute())
        step = steps = None
        try:
            info = endpoint.GetVolumeStepInfo()
            step = int(info[0])
            steps = int(info[1])
        except Exception:
            pass
        return {
            "level": round(scalar * 100.0, 2),
            "scalar": scalar,
            "mute": mute,
            "step": step,
            "steps": steps,
        }
    except Exception as exc:
        return {"error": str(exc)[:120]}


def _volume_snapshot_ok(snap):
    return isinstance(snap, dict) and "level" in snap and "error" not in snap


def _volume_up(_arguments):
    before = _read_system_volume()
    message = Music.run("głośniej")
    after = _read_system_volume()
    data = {
        "action": "adjust_volume",
        "direction": "increase",
        "volume_before": before,
        "volume_after": after,
    }
    return ToolResult.ok(
        message or "Zwiększam głośność.",
        data=data,
        tool_name="volume_up",
        side_effect=True,
        verification_hint="get_volume",
    )


def _volume_down(_arguments):
    before = _read_system_volume()
    message = Music.run("ciszej")
    after = _read_system_volume()
    data = {
        "action": "adjust_volume",
        "direction": "decrease",
        "volume_before": before,
        "volume_after": after,
    }
    return ToolResult.ok(
        message or "Zmniejszam głośność.",
        data=data,
        tool_name="volume_down",
        side_effect=True,
        verification_hint="get_volume",
    )


def _get_volume(_arguments):
    """Read-only OS volume observation used by plan_cycle verify_spec."""
    snap = _read_system_volume()
    if not _volume_snapshot_ok(snap):
        err = ""
        if isinstance(snap, dict):
            err = str(snap.get("error") or "")
        return ToolResult.unavailable(
            "Nie mogę odczytać głośności systemu.",
            tool_name="get_volume",
            data=dict(snap or {}) if isinstance(snap, dict) else {},
            error_message=err or "volume_readback_unavailable",
        )
    level = snap["level"]
    mute = bool(snap.get("mute"))
    msg = f"Głośność {level:.0f}%."
    if mute:
        msg = f"Wyciszone (poziom {level:.0f}%)."
    return ToolResult.ok(
        msg,
        data=dict(snap),
        tool_name="get_volume",
        side_effect=False,
    )


def _get_system_status(_arguments):
    # Prefer fresh runtime SystemState when available (no extra wait).
    try:
        from core.system_state import get_system_state_store

        state = get_system_state_store().state
        if state.updated_at:
            return ToolResult.ok(
                state.summary_text(),
                data=state.to_dict(),
                tool_name="get_system_status",
            )
    except Exception:
        pass

    parts = []
    cpu = System.run("użycie procesora")
    ram = System.run("ile mam ramu")
    if cpu:
        parts.append(cpu)
    if ram:
        parts.append(ram)
    try:
        battery = psutil.sensors_battery()
        if battery is not None:
            plugged = "na zasilaniu" if battery.power_plugged else "na baterii"
            parts.append(f"Bateria {int(battery.percent)} procent, {plugged}.")
    except Exception:
        pass
    if not parts:
        return ToolResult.fail("Nie udało się odczytać stanu systemu.", tool_name="get_system_status")
    return ToolResult.ok(
        " ".join(parts),
        data={"cpu_percent": psutil.cpu_percent(interval=0.0)},
        tool_name="get_system_status",
    )


def _get_situation_briefing(arguments):
    from core.situation_briefing import compose_situation_briefing

    return compose_situation_briefing(arguments)


def _list_os_notifications(_arguments):
    from core.notification_inbox import peek_inbox
    from core.os_notifications import (
        compose_inbox_spoken,
        get_os_notification_provider,
        snapshots_to_inbox,
    )

    provider = get_os_notification_provider()
    access = provider.status()
    live = []
    if access == "available":
        try:
            live = snapshots_to_inbox(provider.list_recent(limit=6))
        except Exception:
            live = []
    merged = list(peek_inbox(limit=8))
    seen = {item.id for item in merged}
    for item in live:
        if item.id not in seen:
            merged.append(item)
    spoken = compose_inbox_spoken(merged, access=access if not merged else "available")
    return ToolResult.ok(
        spoken,
        tool_name="list_os_notifications",
        data={"count": len(merged), "access": access},
    )


def _get_phone_status(_arguments):
    from integrations.twilio_provider import twilio_status_spoken

    return ToolResult.ok(twilio_status_spoken(), tool_name="get_phone_status")


def _get_weather(arguments):
    city = "Łódź"
    try:
        from Config import get_config_value
        from Skills.Weather import get_weather

        city = str(
            (arguments or {}).get("city")
            or get_config_value("location", "city", default="Łódź")
            or "Łódź"
        ).strip() or "Łódź"
        raw = str(get_weather(city) or "").strip()
    except Exception:
        raw = ""
    if not raw or "Nie udało" in raw:
        return ToolResult.fail(
            "Nie udało mi się pobrać pogody.",
            tool_name="get_weather",
        )
    return ToolResult.ok(raw, tool_name="get_weather", data={"city": city})


def _turn_on_device(arguments):
    target = str(arguments.get("target") or arguments.get("name") or "").strip()
    domain = str(arguments.get("domain") or "").strip()
    entity_id = str(arguments.get("entity_id") or "").strip()
    utterance = str(arguments.get("utterance") or arguments.get("extra") or target)
    data, _entity, resolve_error = _resolved_device_data(
        target=target,
        domain=domain,
        entity_id=entity_id,
        last_action="turn_on",
    )
    if resolve_error and _entity is None and not (
        data.get("resolved_entity_id") or entity_id
    ):
        # Still attempt execute — HA may resolve differently; keep fail path.
        pass
    message = execute_device_command(
        "device_on",
        target=target,
        domain=domain,
        entity_id=entity_id or data.get("resolved_entity_id") or "",
        utterance=utterance,
    )
    # Re-resolve after success if first pass missed (legacy paths).
    if _message_success(message) and not data.get("resolved_entity_id"):
        data, _, _ = _resolved_device_data(
            target=target,
            domain=domain,
            entity_id=entity_id,
            last_action="turn_on",
            extra=data,
        )
    return _tool_result_from_verify(
        message,
        tool_name="turn_on_device",
        data=data,
    )


def _turn_off_device(arguments):
    target = str(arguments.get("target") or arguments.get("name") or "").strip()
    domain = str(arguments.get("domain") or "").strip()
    entity_id = str(arguments.get("entity_id") or "").strip()
    utterance = str(arguments.get("utterance") or arguments.get("extra") or target)
    data, _entity, resolve_error = _resolved_device_data(
        target=target,
        domain=domain,
        entity_id=entity_id,
        last_action="turn_off",
    )
    message = execute_device_command(
        "device_off",
        target=target,
        domain=domain,
        entity_id=entity_id or data.get("resolved_entity_id") or "",
        utterance=utterance,
    )
    if _message_success(message) and not data.get("resolved_entity_id"):
        data, _, _ = _resolved_device_data(
            target=target,
            domain=domain,
            entity_id=entity_id,
            last_action="turn_off",
            extra=data,
        )
    return _tool_result_from_verify(
        message,
        tool_name="turn_off_device",
        data=data,
    )


def _set_device_value(arguments):
    action = str(arguments.get("action") or "set_temperature").strip()
    target = str(arguments.get("target") or arguments.get("name") or "").strip()
    domain = str(arguments.get("domain") or "").strip()
    entity_id = str(arguments.get("entity_id") or "").strip()
    extra = str(arguments.get("extra") or "").strip()
    semantic_action = str(arguments.get("semantic_action") or "").strip()
    raw_value = arguments.get("value", 0)
    mode_actions = (
        "set_hvac_mode",
        "set_fan_mode",
        "set_swing_mode",
        "set_swing_horizontal_mode",
        "set_preset_mode",
        "press_button",
    )
    if action in ("set_temperature", "adjust_temperature"):
        try:
            value = float(raw_value or 0)
        except (TypeError, ValueError):
            value = 0.0
    elif action in mode_actions:
        value = extra or raw_value
    else:
        try:
            value = int(raw_value or 0)
        except (TypeError, ValueError):
            value = 0
    data, entity, _resolve_error = _resolved_device_data(
        target=target,
        domain=domain,
        entity_id=entity_id,
        last_action=action,
        extra={"action": action, "value": value, "semantic_action": semantic_action, "color_name": extra if action == "light_color" else ""},
    )
    # Domain lock: never silently map bare light percent onto climate.
    resolved_domain = str(data.get("resolved_domain") or domain or "")
    if (
        action in ("set_temperature", "adjust_temperature")
        and resolved_domain == "light"
    ):
        return ToolResult.fail(
            "To światło — procentów nie ustawiam jako temperatury.",
            tool_name="set_device_value",
            data=data,
            error="domain_lock",
        )
    if (
        action in ("light_brightness", "adjust_brightness")
        and resolved_domain == "climate"
    ):
        return ToolResult.fail(
            "To klimatyzacja — nie ustawiam jasności.",
            tool_name="set_device_value",
            data=data,
            error="domain_lock",
        )
    # Capability: light brightness unsupported → fail, never climate fallback.
    if entity is not None and action in ("light_brightness", "adjust_brightness"):
        from HaActions import entity_supports_brightness

        if entity.domain == "light" and not entity_supports_brightness(entity):
            msg = f"{entity.display_name()} nie obsługuje regulacji jasności."
            return ToolResult.unsupported(msg, tool_name="set_device_value", data=data)

    message = execute_device_command(
        action,
        target=target,
        domain=domain or resolved_domain,
        value=value,
        extra=extra,
        entity_id=entity_id or data.get("resolved_entity_id") or "",
        utterance=str(arguments.get("utterance") or extra or target),
    )
    if (_message_success(message) or _message_unsupported(message)) and not data.get(
        "resolved_entity_id"
    ):
        data, _, _ = _resolved_device_data(
            target=target,
            domain=domain,
            entity_id=entity_id,
            last_action=action,
            extra=data,
        )
    if action in (
        "light_brightness",
        "adjust_brightness",
        "device_on",
        "light_on",
        "device_off",
        "light_off",
        "light_color",
        "light_temperature",
    ):
        return _tool_result_from_verify(
            message,
            tool_name="set_device_value",
            data=data,
        )
    return _tool_result_from_ha_message(
        message,
        tool_name="set_device_value",
        data=data,
    )


def _get_device_state(arguments):
    target = str(arguments.get("target") or arguments.get("name") or "").strip()
    domain = str(arguments.get("domain") or "").strip()
    entity_id = str(arguments.get("entity_id") or "").strip()
    utterance = str(arguments.get("utterance") or target)
    data, _entity, _err = _resolved_device_data(
        target=target,
        domain=domain,
        entity_id=entity_id,
        last_action="device_status",
    )
    message = execute_device_command(
        "device_status",
        target=target,
        domain=domain,
        entity_id=entity_id or data.get("resolved_entity_id") or "",
        utterance=utterance,
    )
    payload = dict(data)
    payload["mutated"] = False
    payload["ha_action"] = "device_status"
    return _tool_result_from_ha_message(
        message,
        tool_name="get_device_state",
        data=payload,
    )


def _open_website_tool(arguments):
    target = str(arguments.get("url") or arguments.get("target") or arguments.get("name") or "").strip()
    if not target:
        return ToolResult.fail("Nie podano strony.", tool_name="open_website")
    success, message = open_website(target)
    if success:
        return ToolResult.ok(message, data={"target": target}, tool_name="open_website")
    return ToolResult.fail(message, tool_name="open_website", retryable=True)


def _web_search_tool(arguments):
    from core.web_grounding import clean_web_query, rank_hits

    query = str(arguments.get("query") or arguments.get("q") or "").strip()
    query = clean_web_query(query)
    if not query:
        return ToolResult.fail("Co mam wyszukać?", tool_name="web_search")
    try:
        max_results = int(arguments.get("max_results") or 5)
    except (TypeError, ValueError):
        max_results = 5
    if not provider_available():
        return ToolResult.unavailable(
            "Nie mogę teraz połączyć się z wyszukiwaniem internetowym. "
            "Mogę odpowiedzieć na podstawie tego, co już wiem.",
            tool_name="web_search",
            data={"query": query, "available": False, "capability": "WEB_SEARCH"},
            error_code="CAPABILITY_UNAVAILABLE",
            error_message="web search provider unavailable",
            retryable=True,
        )
    result = search_web_structured(query, max_results=max_results)
    data = result.to_dict()
    ranked = rank_hits(result.results, query=query)
    data["ranked_results"] = [item.to_dict() for item in ranked]
    data["query"] = query
    if not result.success or not result.results:
        raw_error = result.error or "No results found."
        print(f"[WEB] provider_error={raw_error!r}")
        data["provider_error"] = raw_error
        # User-facing Polish/neutral — never raw English provider strings.
        message = "Nie udało mi się teraz znaleźć wyników. Mogę spróbować ponownie."
        return ToolResult.fail(
            message,
            tool_name="web_search",
            data=data,
            retryable=True,
            error=raw_error,
        )
    top = ranked[0] if ranked else result.results[0]
    message = (
        f"Znalazłem {len(result.results)} wyników. "
        f"Pierwszy: {getattr(top, 'title', None) or getattr(top, 'source', '')}."
    )
    return ToolResult.ok(message, data=data, tool_name="web_search")


def _fetch_web_page_tool(arguments):
    url = str(arguments.get("url") or arguments.get("target") or "").strip()
    if not url:
        return ToolResult.fail("Nie podano adresu strony.", tool_name="fetch_web_page")
    ok, text_or_error, meta = fetch_web_page_text(url)
    if not ok:
        return ToolResult.fail(
            text_or_error,
            tool_name="fetch_web_page",
            data=meta,
            retryable=True,
        )
    return ToolResult.ok(
        "Pobrałem treść strony.",
        data={**meta, "text": text_or_error},
        tool_name="fetch_web_page",
    )


def build_default_registry(confirm_medium=None):
    if confirm_medium is None:
        confirm_medium = bool(
            get_config_value(
                "computer_control",
                "confirm_risky_actions",
                default=True,
            )
        )
    registry = ToolRegistry(confirm_medium=confirm_medium)

    registry.register(Tool(
        name="open_app",
        description=(
            "Uruchamia aplikację na komputerze (Discord, Spotify, Chrome itd.). "
            "Wymaga arguments.name."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "website_direct": {"type": "boolean"},
                "explicit_app": {"type": "boolean"},
                "text": {"type": "string"},
            },
            "required": ["name"],
        },
        risk_level=RiskLevel.SAFE,
        handler=_open_app,
        timeout=20,
    ))
    registry.register(Tool(
        name="check_app_installed",
        description="Sprawdza, czy aplikacja jest zainstalowana (Start Menu).",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        risk_level=RiskLevel.SAFE,
        handler=_check_app_installed,
        timeout=15,
    ))
    registry.register(Tool(
        name="close_app",
        description="Zamyka uruchomioną aplikację.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "automated": {
                    "type": "boolean",
                    "description": "Suite/diagnostics cleanup — respect user-app guard",
                },
                "test_cleanup": {"type": "boolean"},
                "allow_user_owned": {
                    "type": "boolean",
                    "description": "False blocks pre-existing Spotify under ELIAN_PROTECT_USER_APPS",
                },
                "media_playing": {"type": "boolean"},
            },
            "required": ["name"],
        },
        risk_level=RiskLevel.MEDIUM,
        handler=_close_app,
        confirmation_prompt="Czy na pewno mam zamknąć tę aplikację?",
        timeout=15,
    ))
    try:
        from core.browser_tools import register_browser_tools

        register_browser_tools(registry)
    except Exception as error:
        print("Błąd rejestracji browser tools:", error)
    registry.register(Tool(
        name="volume_up",
        description="Zwiększa głośność systemu.",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_volume_up,
    ))
    registry.register(Tool(
        name="volume_down",
        description="Zmniejsza głośność systemu.",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_volume_down,
    ))
    registry.register(Tool(
        name="get_volume",
        description="Odczytuje bieżącą głośność systemu (readback / verify).",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_get_volume,
        timeout=5,
    ))
    registry.register(Tool(
        name="get_system_status",
        description="Podaje użycie CPU, RAM i stan baterii.",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_get_system_status,
        timeout=10,
    ))
    registry.register(Tool(
        name="get_situation_briefing",
        description=(
            "Read-only Jarvis briefing: next calendar event, reminders, "
            "home devices that are on, optional weather, and PC load. "
            "Does not change any device or calendar state."
        ),
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_get_situation_briefing,
        timeout=20,
    ))
    registry.register(Tool(
        name="list_os_notifications",
        description="Read recent Windows or inbox notifications. Read-only.",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_list_os_notifications,
        timeout=8,
    ))
    registry.register(Tool(
        name="get_phone_status",
        description="Say whether Elian has a Twilio number. Read-only. Never invents a number.",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_get_phone_status,
        timeout=4,
    ))
    registry.register(Tool(
        name="get_weather",
        description="Current weather for the configured city. Read-only.",
        parameters={
            "type": "object",
            "properties": {"city": {"type": "string"}},
        },
        risk_level=RiskLevel.SAFE,
        handler=_get_weather,
        timeout=8,
    ))
    registry.register(Tool(
        name="undo_last_action",
        description=(
            "Cofa ostatnie odwracalne akcje (HA, media, głośność, wybrane "
            "przypomnienia/kalendarz). count=N cofa N ostatnich. "
            "Nie udaje cofnięcia operacji nieodwracalnych."
        ),
        parameters={
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
                "n": {"type": "integer"},
                "action_id": {"type": "string"},
            },
        },
        risk_level=RiskLevel.SAFE,
        handler=lambda arguments: __import__(
            "core.action_history", fromlist=["undo_last_action"]
        ).undo_last_action(arguments),
        timeout=20,
    ))
    registry.register(Tool(
        name="turn_on_device",
        description="Włącza urządzenie smart home (Home Assistant).",
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "domain": {"type": "string"},
                "entity_id": {"type": "string"},
                "semantic_action": {"type": "string"},
                "utterance": {"type": "string"},
            },
        },
        risk_level=RiskLevel.SAFE,
        handler=_turn_on_device,
        timeout=15,
    ))
    registry.register(Tool(
        name="turn_off_device",
        description="Wyłącza urządzenie smart home (Home Assistant).",
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "domain": {"type": "string"},
                "entity_id": {"type": "string"},
                "semantic_action": {"type": "string"},
                "utterance": {"type": "string"},
            },
        },
        risk_level=RiskLevel.SAFE,
        handler=_turn_off_device,
        timeout=15,
    ))
    registry.register(Tool(
        name="set_device_value",
        description=(
            "Steruje wartością urządzenia smart-home (Home Assistant): "
            "jasność światła (adjust_brightness / light_brightness), temperatura itd. "
            "Wymaga target lub entity_id oraz domain gdy znane."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "target": {"type": "string"},
                "domain": {"type": "string"},
                "value": {"type": ["integer", "number", "string"]},
                "entity_id": {"type": "string"},
                "extra": {"type": "string"},
                "semantic_action": {"type": "string"},
                "utterance": {"type": "string"},
            },
        },
        # Kept SAFE to preserve current HA UX (no confirm on light/climate tweaks).
        # Future planner can require confirmation per-action when needed.
        risk_level=RiskLevel.SAFE,
        handler=_set_device_value,
        timeout=15,
    ))
    registry.register(Tool(
        name="get_device_state",
        description="Odczytuje stan urządzenia smart home bez zmiany stanu.",
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "domain": {"type": "string"},
                "entity_id": {"type": "string"},
                "utterance": {"type": "string"},
            },
        },
        risk_level=RiskLevel.SAFE,
        handler=_get_device_state,
        timeout=10,
    ))
    registry.register(Tool(
        name="open_website",
        description=(
            "Opens a public website or known service in the default browser "
            "by URL or service name. Does not perform structured search."
        ),
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string"}, "target": {"type": "string"}},
        },
        risk_level=RiskLevel.SAFE,
        handler=_open_website_tool,
        timeout=10,
    ))
    registry.register(Tool(
        name="web_search",
        description=(
            "Searches the public web and returns structured hits "
            "(title, url, snippet, domain). Use for current facts, news, prices, "
            "scores, product research. Requires arguments.query. "
            "After success, synthesize from data.results — never pretend to search."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["query"],
        },
        risk_level=RiskLevel.SAFE,
        handler=_web_search_tool,
        timeout=25,
    ))
    registry.register(Tool(
        name="fetch_web_page",
        description=(
            "Fetches readable plain text from a public HTTP(S) page (no JS). "
            "Use when web_search snippets are insufficient. Requires arguments.url. "
            "Blocks localhost and private networks."
        ),
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string"}, "target": {"type": "string"}},
            "required": ["url"],
        },
        risk_level=RiskLevel.SAFE,
        handler=_fetch_web_page_tool,
        timeout=20,
    ))

    def _remember_fact(arguments):
        from memory.manager import get_memory_manager
        text = str(arguments.get("text") or arguments.get("fact") or "").strip()
        ok, message = get_memory_manager().remember_fact(text, explicit=True, source="agent")
        if ok:
            return ToolResult.ok(message, data={"text": text}, tool_name="remember_fact")
        return ToolResult.fail(message, tool_name="remember_fact")

    def _set_preference(arguments):
        from memory.manager import get_memory_manager
        key = str(arguments.get("key") or "").strip()
        value = str(arguments.get("value") or "").strip()
        ok, message = get_memory_manager().set_preference(key, value, explicit=True, source="agent")
        if ok:
            return ToolResult.ok(message, data={"key": key, "value": value}, tool_name="set_preference")
        return ToolResult.fail(message, tool_name="set_preference")

    def _get_preference(arguments):
        from memory.manager import get_memory_manager
        key = str(arguments.get("key") or "").strip()
        value = get_memory_manager().get_preference(key)
        if value is None:
            return ToolResult.fail(
                f"Nie mam preferencji {key}.",
                tool_name="get_preference",
                data={"key": key},
            )
        return ToolResult.ok(
            f"{key} = {value}",
            data={"key": key, "value": value},
            tool_name="get_preference",
        )

    def _get_memory(arguments):
        from memory.manager import get_memory_manager
        query = str(arguments.get("query") or "").strip()
        facts = get_memory_manager().search_facts(query, limit=5)
        if not facts:
            return ToolResult.fail(
                "Nie mam zapisanych informacji na ten temat.",
                tool_name="get_memory",
            )
        texts = [fact.get("text") for fact in facts]
        return ToolResult.ok(
            "; ".join(texts),
            data={"facts": texts},
            tool_name="get_memory",
        )

    def _forget_fact(arguments):
        from memory.manager import get_memory_manager
        query = str(arguments.get("query") or arguments.get("text") or "").strip()
        removed = get_memory_manager().forget(query)
        total = len(removed["facts"]) + len(removed["preferences"])
        if total == 0:
            return ToolResult.fail("Nie znalazłem takiego wpisu.", tool_name="forget_fact")
        return ToolResult.ok(
            "Zapomniałem.",
            data={"removed": total},
            tool_name="forget_fact",
        )

    registry.register(Tool(
        name="remember_fact",
        description="Zapisuje trwały fakt o użytkowniku. Tylko jawne, przydatne informacje — nigdy haseł/tokenów.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        risk_level=RiskLevel.SAFE,
        handler=_remember_fact,
    ))
    registry.register(Tool(
        name="set_preference",
        description=(
            "Ustawia preferencję TYLKO gdy użytkownik jawnie prosi "
            "(np. 'Zapamiętaj, że wolę Spotify'). NIGDY przy samym play_media."
        ),
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["key", "value"],
        },
        risk_level=RiskLevel.SAFE,
        handler=_set_preference,
    ))
    registry.register(Tool(
        name="get_preference",
        description="Odczytuje preferencję użytkownika po kluczu.",
        parameters={
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
        risk_level=RiskLevel.SAFE,
        handler=_get_preference,
    ))
    registry.register(Tool(
        name="get_memory",
        description="Wyszukuje zapisane fakty o użytkowniku. Nie zgaduj — tylko istniejące wpisy.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        risk_level=RiskLevel.SAFE,
        handler=_get_memory,
    ))
    registry.register(Tool(
        name="forget_fact",
        description="Usuwa fakt lub preferencję na podstawie zapytania użytkownika.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        risk_level=RiskLevel.MEDIUM,
        handler=_forget_fact,
        confirmation_prompt="Czy na pewno mam to zapomnieć?",
    ))

    if bool(get_config_value("ai", "vision_enabled", default=True)):
        try:
            from vision.tools import register_vision_tools

            register_vision_tools(registry)
        except Exception as error:
            print("Błąd rejestracji vision tools:", error)

    try:
        from core.computer_tools import register_computer_tools

        register_computer_tools(registry)
    except Exception as error:
        print("Błąd rejestracji computer tools:", error)

    try:
        from integrations.reminders.tools import register_reminder_tools
        from integrations.calendar.tools import register_calendar_tools
        from integrations.media.tools import register_media_tools
        from integrations.home_assistant.tools import register_ha_integration_tools
        from integrations.contacts_messaging import register_contacts_messaging_tools

        register_reminder_tools(registry)
        register_calendar_tools(registry)
        register_media_tools(registry)
        register_ha_integration_tools(registry)
        register_contacts_messaging_tools(registry)
    except Exception as error:
        print("Błąd rejestracji integration tools:", error)

    try:
        from core.procedure_engine import register_procedure_tools

        register_procedure_tools(registry)
    except Exception as error:
        print("Błąd rejestracji procedure tools:", error)

    try:
        from core.capability_registry import get_capability_registry

        get_capability_registry().apply_tool_availability(registry)
    except Exception as error:
        print("Błąd sync capability→tool.available:", error)

    return registry
