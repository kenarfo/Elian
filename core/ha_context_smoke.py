# ==========================
# ELIAN — Phase 12 HA context smoke (no destructive real HA writes)
# ==========================
"""Canonical active_device + percent follow-up — fixtures only."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


SAMPLE_LIGHT = {
    "entity_id": "light.vizzini",
    "state": "on",
    "attributes": {
        "friendly_name": "Inspire Vizzini D56 Vizzini",
        "supported_color_modes": ["rgb", "color_temp", "brightness"],
        "brightness": 128,
    },
}


def _load():
    import HomeAssistant

    return HomeAssistant.get_registry().load_states([SAMPLE_LIGHT])


def run_ha_context_smoke() -> int:
    import ConversationContext
    import HaActions
    import HomeAssistant
    from core.builtin_tools import build_default_registry
    from core.conversation_manager import ConversationManager
    from core.device_context import get_active_device
    from core.model_client import FakeModelClient, reset_model_client
    from core.tools import reset_registry_for_tests
    from memory.manager import MemoryManager

    reset_registry_for_tests()
    reset_model_client()
    ConversationContext.clear()
    _load()

    rows = []
    failed = 0

    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")
        registry = build_default_registry()
        cm = ConversationManager(
            registry=registry,
            model=FakeModelClient(
                [
                    {
                        "type": "final_answer",
                        "message": "Okej — o czym chcesz pogadać albo co mam zrobić?",
                    }
                ]
                * 5
            ),
            memory_manager=mem,
        )
        cm.activate()
        cm.session_language = "pl"

        climate_calls = []

        def _track_service(domain, service, entity_id, data=None):
            if domain == "climate":
                climate_calls.append(
                    {"service": service, "entity_id": entity_id, "data": data}
                )
                return False
            return True

        cases = [
            ("Wyłącz lampę.", "turn_off", None),
            ("Ustaw ją na 40%.", "brightness", 40),
            ("Ustaw na 55%.", "brightness", 55),
        ]

        with patch.object(HaActions, "_call_service", side_effect=_track_service):
            for utterance, expect_kind, expect_pct in cases:
                before = get_active_device(cm.mem)
                before_id = before.entity_id if before else ""
                before_domain = before.domain if before else ""
                climate_before = len(climate_calls)

                result = cm.handle(utterance)
                after = get_active_device(cm.mem)
                obs = (result.data or {}) if hasattr(result, "data") else {}
                data = dict(obs.get("data") or obs or {})
                tool = str(obs.get("tool") or "")
                args = {}
                # Prefer last observation for tool args
                if cm.last_observations:
                    last = cm.last_observations[-1]
                    tool = str(last.get("tool") or tool)
                    data = dict(last.get("data") or data)
                    # reconstruct args from data
                    if data.get("action"):
                        args = {
                            "action": data.get("action"),
                            "value": data.get("value"),
                            "entity_id": data.get("resolved_entity_id")
                            or data.get("entity_id"),
                            "domain": data.get("resolved_domain")
                            or data.get("domain"),
                        }

                climate_delta = len(climate_calls) - climate_before
                resolved_target = (
                    (after.entity_id if after else "")
                    or data.get("resolved_entity_id")
                    or ""
                )
                resolved_domain = (
                    (after.domain if after else "")
                    or data.get("resolved_domain")
                    or ""
                )

                ok = (
                    resolved_target == "light.vizzini"
                    and resolved_domain == "light"
                    and climate_delta == 0
                )
                if expect_kind == "turn_off":
                    ok = ok and (
                        "wyłącz" in (result.text or "").lower()
                        or "vizzini" in (result.text or "").lower()
                        or tool == "turn_off_device"
                    )
                if expect_kind == "brightness" and expect_pct is not None:
                    ok = ok and (
                        int(data.get("value") or args.get("value") or 0) == expect_pct
                        or str(expect_pct) in (result.text or "")
                        or tool == "set_device_value"
                    )
                    ok = ok and data.get("action", args.get("action")) in (
                        "light_brightness",
                        "",
                        None,
                    ) or tool in ("set_device_value", "turn_off_device", "")

                # Stricter brightness check
                if expect_kind == "brightness":
                    action = data.get("action") or args.get("action") or ""
                    value = int(data.get("value") or args.get("value") or -1)
                    ok = (
                        resolved_target == "light.vizzini"
                        and resolved_domain == "light"
                        and climate_delta == 0
                        and action == "light_brightness"
                        and value == expect_pct
                    )

                if not ok:
                    failed += 1
                status = "PASS" if ok else "FAIL"
                rows.append(
                    {
                        "status": status,
                        "input": utterance,
                        "raw_target": "lampę" if "lamp" in utterance.lower() else "",
                        "resolved_target": resolved_target,
                        "resolved_domain": resolved_domain,
                        "active_device_before": f"{before_id}/{before_domain}",
                        "active_device_after": (
                            f"{after.entity_id}/{after.domain}" if after else ""
                        ),
                        "intent_family": getattr(result, "data", {})
                        and (result.data or {}).get("intent_family", ""),
                        "tool": tool,
                        "tool_args": args or {
                            "action": data.get("action"),
                            "value": data.get("value"),
                            "entity_id": data.get("resolved_entity_id"),
                            "domain": data.get("resolved_domain"),
                        },
                        "climate_calls": climate_delta,
                    }
                )

    for row in rows:
        print(
            f"[{row['status']}] input={row['input']!r}\n"
            f"  raw_target={row['raw_target']!r}\n"
            f"  resolved_target={row['resolved_target']} "
            f"resolved_domain={row['resolved_domain']}\n"
            f"  active_device_before={row['active_device_before']}\n"
            f"  active_device_after={row['active_device_after']}\n"
            f"  intent_family={row['intent_family']!r}\n"
            f"  tool={row['tool']} tool_args={row['tool_args']}\n"
            f"  climate_calls={row['climate_calls']}"
        )

    print(f"\n=== ha-context-smoke: {len(rows) - failed}/{len(rows)} PASS ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_ha_context_smoke())
