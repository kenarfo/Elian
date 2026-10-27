# ==========================
# ELIAN — Fake Home Assistant backend (CREDENTIAL_UNAVAILABLE / dry)
# ==========================
"""In-memory HA contract for autonomous text/inject tests.

Not HARDWARE PASS. Mutates HomeAssistant fixture registry so soft-goals /
DeviceResolver see the same light.vizzini world as production.

Supports: entity registry, states, service-like mutations, verify payload,
unavailable / no_change / fail inject modes.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.tool_result import ToolResult


DEFAULT_LIGHT = {
    "entity_id": "light.vizzini",
    "state": "off",
    "attributes": {
        "friendly_name": "Vizzini",
        "supported_color_modes": ["brightness"],
        "brightness": 0,
        "supported_features": 1,
    },
}

HA_DEVICE_PROPS = {
    "target": {"type": "string"},
    "domain": {"type": "string"},
    "entity_id": {"type": "string"},
    "utterance": {"type": "string"},
    "name": {"type": "string"},
}

SET_VALUE_PROPS = {
    **HA_DEVICE_PROPS,
    "action": {"type": "string"},
    "value": {"type": ["integer", "number", "string"]},
    "extra": {"type": "string"},
    "semantic_action": {"type": "string"},
}


@dataclass
class FakeHaBackend:
    """Stateful fake HA world for dry ToolRegistry handlers."""

    mode: str = "ok"  # ok|fail|timeout|unavailable|no_change|verify_fail
    states: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    service_log: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.states:
            self.seed_default_world()

    def seed_default_world(self) -> None:
        self.states = {DEFAULT_LIGHT["entity_id"]: deepcopy(DEFAULT_LIGHT)}
        self._push_registry()

    def reset(self) -> None:
        self.mode = "ok"
        self.service_log.clear()
        self.seed_default_world()

    def _push_registry(self) -> None:
        try:
            from HomeAssistant import get_registry

            get_registry().load_states(list(self.states.values()))
        except Exception:
            pass

    def _entity_id(self, args: Dict[str, Any]) -> str:
        eid = str(args.get("entity_id") or "").strip()
        if eid and eid in self.states:
            return eid
        target = str(args.get("target") or args.get("name") or "").strip().casefold()
        if target:
            for key, row in self.states.items():
                attrs = row.get("attributes") or {}
                friendly = str(attrs.get("friendly_name") or "").casefold()
                oid = key.split(".", 1)[-1].replace("_", " ").casefold()
                if target in (key.casefold(), friendly, oid) or target in friendly:
                    return key
                if "lamp" in target or "świat" in target or "swiat" in target:
                    if key.startswith("light."):
                        return key
        # Unique light fallback (Jarvis single-light home).
        lights = [k for k in self.states if k.startswith("light.")]
        if len(lights) == 1:
            return lights[0]
        return eid or (lights[0] if lights else "light.vizzini")

    def _brightness_pct(self, entity_id: str) -> int:
        row = self.states.get(entity_id) or {}
        raw = (row.get("attributes") or {}).get("brightness")
        if raw is None:
            return 50 if str(row.get("state") or "") == "on" else 0
        try:
            return int(round(int(raw) * 100 / 255))
        except (TypeError, ValueError):
            return 0

    def _set_brightness_pct(self, entity_id: str, pct: int) -> None:
        pct = max(0, min(100, int(pct)))
        row = self.states.setdefault(
            entity_id,
            {
                "entity_id": entity_id,
                "state": "off",
                "attributes": {"friendly_name": entity_id, "brightness": 0},
            },
        )
        attrs = dict(row.get("attributes") or {})
        attrs["brightness"] = int(round(pct * 255 / 100))
        attrs.setdefault("supported_color_modes", ["brightness"])
        row["attributes"] = attrs
        row["state"] = "on" if pct > 0 else "off"
        self.states[entity_id] = row
        self._push_registry()

    def _set_on_off(self, entity_id: str, on: bool) -> None:
        row = self.states.setdefault(
            entity_id,
            {
                "entity_id": entity_id,
                "state": "off",
                "attributes": {"friendly_name": entity_id, "brightness": 0},
            },
        )
        attrs = dict(row.get("attributes") or {})
        if on:
            row["state"] = "on"
            if int(attrs.get("brightness") or 0) <= 0:
                attrs["brightness"] = int(round(40 * 255 / 100))
        else:
            row["state"] = "off"
            attrs["brightness"] = 0
        row["attributes"] = attrs
        self.states[entity_id] = row
        self._push_registry()

    def snapshot(self, entity_id: str) -> Dict[str, Any]:
        row = deepcopy(self.states.get(entity_id) or {})
        return {
            "entity_id": entity_id,
            "state": row.get("state"),
            "brightness_pct": self._brightness_pct(entity_id),
            "attributes": dict(row.get("attributes") or {}),
        }

    def handle(self, tool_name: str, args: Optional[Dict[str, Any]] = None) -> ToolResult:
        args = dict(args or {})
        entity_id = self._entity_id(args)
        before = self.snapshot(entity_id)
        self.service_log.append({"tool": tool_name, "arguments": args, "entity_id": entity_id})

        mode = str(self.mode or "ok").strip().lower()
        if mode == "fail":
            return ToolResult.fail(
                "Nie udało się wykonać komendy HA.",
                tool_name=tool_name,
                error_code="TOOL_FAILED",
            )
        if mode == "timeout":
            return ToolResult.fail(
                "Timeout Home Assistant.",
                tool_name=tool_name,
                error_code="TIMEOUT",
                retryable=True,
            )
        if mode == "unavailable":
            return ToolResult.fail(
                "Urządzenie niedostępne.",
                tool_name=tool_name,
                error_code="DEPENDENCY_UNAVAILABLE",
                data={"entity_id": entity_id, "availability": "UNAVAILABLE"},
            )

        if tool_name == "get_device_state":
            snap = self.snapshot(entity_id)
            return ToolResult.ok(
                f"Stan {entity_id}: {snap.get('state')}.",
                tool_name=tool_name,
                data={
                    **args,
                    "resolved_entity_id": entity_id,
                    "resolved_domain": entity_id.split(".", 1)[0],
                    "state": snap.get("state"),
                    "brightness_pct": snap.get("brightness_pct"),
                    "attributes": snap.get("attributes"),
                },
            )

        if mode == "no_change":
            data = {
                **args,
                "resolved_entity_id": entity_id,
                "resolved_domain": "light",
                "state_changed": False,
                "ha_verify": {"verification": "NO_CHANGE"},
                "before": before,
                "after": before,
            }
            return ToolResult.ok("Bez zmiany.", tool_name=tool_name, data=data)

        action = str(args.get("action") or "").strip()
        if tool_name == "turn_on_device":
            self._set_on_off(entity_id, True)
            ha_action = "turn_on"
        elif tool_name == "turn_off_device":
            self._set_on_off(entity_id, False)
            ha_action = "turn_off"
        elif tool_name == "set_device_value":
            if action in ("adjust_brightness", "brightness_up", "brightness_down", ""):
                delta = int(args.get("value") or 0)
                if action == "brightness_down" and delta > 0:
                    delta = -delta
                new_pct = max(0, min(100, self._brightness_pct(entity_id) + delta))
                if new_pct == 0 and delta < 0:
                    self._set_on_off(entity_id, False)
                else:
                    self._set_brightness_pct(entity_id, max(new_pct, 1) if delta > 0 else new_pct)
                ha_action = "adjust_brightness"
            elif action == "light_brightness":
                self._set_brightness_pct(entity_id, int(args.get("value") or 0))
                ha_action = "light_brightness"
            else:
                # Accept other set_* without climate simulation in this slim backend.
                ha_action = action or "set_device_value"
        else:
            return ToolResult.fail(
                f"Fake HA nie obsługuje {tool_name}.",
                tool_name=tool_name,
                error_code="UNSUPPORTED",
            )

        after = self.snapshot(entity_id)
        verify = "FAILED" if mode == "verify_fail" else "SUCCESS"
        data = {
            **args,
            "resolved_entity_id": entity_id,
            "resolved_domain": entity_id.split(".", 1)[0],
            "ha_action": ha_action,
            "ha_verify": {"verification": verify},
            "state_changed": before != after,
            "before": before,
            "after": after,
            "action": action or ha_action,
            "value": args.get("value"),
        }
        if mode == "verify_fail":
            return ToolResult.ok(
                "Wysłano, ale nie potwierdzam zmiany stanu.",
                tool_name=tool_name,
                data=data,
            )
        return ToolResult.ok("OK.", tool_name=tool_name, data=data)


_BACKEND: Optional[FakeHaBackend] = None


def get_fake_ha_backend() -> FakeHaBackend:
    global _BACKEND
    if _BACKEND is None:
        _BACKEND = FakeHaBackend()
    return _BACKEND


def reset_fake_ha_backend() -> FakeHaBackend:
    backend = get_fake_ha_backend()
    backend.reset()
    return backend
