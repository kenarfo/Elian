# ==========================
# ELIAN — HA tools via existing HaActions/registry (Phase 7)
# Does NOT rewrite HomeAssistant / DeviceResolver.
# ==========================

from __future__ import annotations

from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from integrations.base import integration_enabled


def _ha_enabled():
    from Config import get_config_value

    return bool(get_config_value("smart_home", "enabled", default=False)) and integration_enabled(
        "home_assistant"
    )


def _get_device_state(arguments):
    if not _ha_enabled():
        return ToolResult.unavailable(
            "Funkcja Home Assistant jest wyłączona.",
            tool_name="get_device_state",
            data={"error_code": "CAPABILITY_DISABLED", "capability": "HOME_ASSISTANT"},
            error_code="CAPABILITY_DISABLED",
        )
    from DeviceResolver import resolve_device
    from HomeAssistant import get_registry

    target = str(arguments.get("target") or arguments.get("name") or "").strip()
    entity_id = str(arguments.get("entity_id") or "").strip()
    domain = str(arguments.get("domain") or "").strip()
    registry = get_registry()
    registry.maybe_refresh()
    entity = None
    if entity_id:
        entity = registry.get(entity_id)
    if entity is None and target:
        match = resolve_device(target, domain=domain)
        status = ""
        message = ""
        resolved = None
        if isinstance(match, (tuple, list)):
            status = str(match[0] if match else "")
            entities = match[1] if len(match) > 1 else []
            message = str(match[2] if len(match) > 2 else "")
            if entities:
                resolved = entities[0]
        else:
            status = str(getattr(match, "status", "") or "")
            message = str(getattr(match, "message", "") or "")
            resolved = getattr(match, "entity", None)
        if status == "exact" and resolved is not None:
            entity = resolved
        elif status == "ambiguous":
            return ToolResult.fail(
                message or "Które urządzenie?",
                tool_name="get_device_state",
                data={"error_code": "AMBIGUOUS"},
            )
        else:
            return ToolResult.fail(
                message or "Nie znalazłem urządzenia.",
                tool_name="get_device_state",
                data={"error_code": "NOT_FOUND"},
            )
    if entity is None:
        return ToolResult.fail(
            "Jakie urządzenie sprawdzić?",
            tool_name="get_device_state",
        )
    name = entity.display_name()
    state = getattr(entity, "state", None) or "?"
    return ToolResult.ok(
        f"{name} jest w stanie: {state}.",
        tool_name="get_device_state",
        data={
            "entity_id": entity.entity_id,
            "name": name,
            "state": state,
            "domain": entity.domain,
        },
    )


def _list_devices(arguments):
    if not _ha_enabled():
        return ToolResult.unavailable(
            "Funkcja Home Assistant jest wyłączona.",
            tool_name="list_devices",
            data={"error_code": "CAPABILITY_DISABLED", "capability": "HOME_ASSISTANT"},
            error_code="CAPABILITY_DISABLED",
        )
    from HomeAssistant import CONTROLLABLE_DOMAINS, get_registry

    domain = str(arguments.get("domain") or "").strip().lower()
    registry = get_registry()
    registry.maybe_refresh()
    try:
        entities = list(registry.all())
    except Exception:
        entities = []

    filtered = []
    for ent in entities:
        d = getattr(ent, "domain", "")
        if domain and d != domain:
            continue
        if not domain and d not in CONTROLLABLE_DOMAINS and d != "scene":
            continue
        filtered.append(ent)

    if not filtered:
        return ToolResult.ok(
            "Nie widzę urządzeń.",
            tool_name="list_devices",
            data={"devices": []},
        )
    names = [e.display_name() for e in filtered[:20]]
    return ToolResult.ok(
        "Urządzenia: " + ", ".join(names) + ".",
        tool_name="list_devices",
        data={
            "devices": [
                {
                    "entity_id": e.entity_id,
                    "name": e.display_name(),
                    "domain": e.domain,
                    "state": getattr(e, "state", ""),
                }
                for e in filtered[:40]
            ]
        },
    )


def _activate_scene(arguments):
    if not _ha_enabled():
        return ToolResult.unavailable(
            "Funkcja Home Assistant jest wyłączona.",
            tool_name="activate_scene",
            data={"error_code": "CAPABILITY_DISABLED", "capability": "HOME_ASSISTANT"},
            error_code="CAPABILITY_DISABLED",
        )
    from HomeAssistant import api_request, get_registry

    name = str(arguments.get("name") or arguments.get("scene") or arguments.get("target") or "").strip()
    entity_id = str(arguments.get("entity_id") or "").strip()
    registry = get_registry()
    registry.maybe_refresh()

    if not entity_id and name:
        # search scenes
        entities = []
        try:
            entities = list(getattr(registry, "entities", {}).values())
        except Exception:
            entities = []
        name_l = name.lower()
        matches = [
            e
            for e in entities
            if getattr(e, "domain", "") == "scene"
            and (
                name_l in e.display_name().lower()
                or name_l in e.entity_id.lower()
            )
        ]
        if len(matches) == 1:
            entity_id = matches[0].entity_id
        elif len(matches) > 1:
            return ToolResult.fail(
                "Kilka scen pasuje — doprecyzuj.",
                tool_name="activate_scene",
                data={"error_code": "AMBIGUOUS"},
            )
        else:
            # allow scene.evening style
            slug = name_l.replace(" ", "_")
            entity_id = slug if slug.startswith("scene.") else f"scene.{slug}"

    if not entity_id:
        return ToolResult.fail("Jaką scenę włączyć?", tool_name="activate_scene")

    ok = False
    response = api_request(
        "POST",
        "/api/services/scene/turn_on",
        json={"entity_id": entity_id},
    )
    if response is not None and 200 <= response.status_code < 300:
        ok = True
    if not ok:
        return ToolResult.fail(
            f"Nie udało się włączyć sceny {entity_id}.",
            tool_name="activate_scene",
            data={"error_code": "UNAVAILABLE", "entity_id": entity_id},
            retryable=True,
        )
    print(f"[INTEGRATION] ha.scene entity_id={entity_id} status=success")
    return ToolResult.ok(
        f"Włączam scenę {name or entity_id}.",
        tool_name="activate_scene",
        data={"entity_id": entity_id},
    )


def _get_home_summary(_arguments):
    if not _ha_enabled():
        return ToolResult.unavailable(
            "Funkcja Home Assistant jest wyłączona.",
            tool_name="get_home_summary",
            data={"error_code": "CAPABILITY_DISABLED", "capability": "HOME_ASSISTANT"},
            error_code="CAPABILITY_DISABLED",
        )
    from HomeAssistant import CONTROLLABLE_DOMAINS, get_registry

    registry = get_registry()
    registry.maybe_refresh()
    try:
        entities = list(registry.all())
    except Exception:
        entities = []
    on_items = []
    for ent in entities:
        if getattr(ent, "domain", "") not in CONTROLLABLE_DOMAINS:
            continue
        state = str(getattr(ent, "state", "")).lower()
        if state in ("on", "open", "playing", "home"):
            on_items.append(ent.display_name())
    if not on_items:
        return ToolResult.ok(
            "Nie widzę włączonych urządzeń.",
            tool_name="get_home_summary",
            data={"on": []},
        )
    return ToolResult.ok(
        "Włączone: " + ", ".join(on_items[:15]) + ".",
        tool_name="get_home_summary",
        data={"on": on_items[:30]},
    )


def register_ha_integration_tools(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="get_device_state",
        description="Get Home Assistant device state by name or entity_id.",
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "name": {"type": "string"},
                "entity_id": {"type": "string"},
                "domain": {"type": "string"},
                "utterance": {"type": "string"},
            },
        },
        risk_level=RiskLevel.SAFE,
        handler=_get_device_state,
        timeout=15,
    ))
    registry.register(Tool(
        name="list_devices",
        description="List controllable HA devices (optional domain filter).",
        parameters={
            "type": "object",
            "properties": {"domain": {"type": "string"}},
        },
        risk_level=RiskLevel.SAFE,
        handler=_list_devices,
        timeout=15,
    ))
    registry.register(Tool(
        name="activate_scene",
        description="Activate a Home Assistant scene by name or entity_id (e.g. wieczór / scene.evening).",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "scene": {"type": "string"},
                "target": {"type": "string"},
                "entity_id": {"type": "string"},
            },
        },
        risk_level=RiskLevel.SAFE,
        handler=_activate_scene,
        timeout=15,
    ))
    registry.register(Tool(
        name="get_home_summary",
        description="Short summary of devices that appear ON in Home Assistant.",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_get_home_summary,
        timeout=15,
    ))
