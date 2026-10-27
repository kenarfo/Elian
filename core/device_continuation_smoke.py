# ==========================
# ELIAN — Phase 12.1 device continuation smoke
# ==========================
"""Value inheritance + repeat block + compound action — fixtures only."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


SAMPLE_LIGHT = {
    "entity_id": "light.vizzini",
    "state": "on",
    "attributes": {
        "friendly_name": "Inspire Vizzini D56 Vizzini",
        "supported_color_modes": ["rgb", "color_temp", "brightness"],
        "brightness": 76,
    },
}


def run_device_continuation_smoke() -> int:
    import ConversationContext
    import HaActions
    import HomeAssistant
    from core.builtin_tools import build_default_registry
    from core.conversation_manager import ConversationManager
    from core.device_context import (
        canonical_tool_signature,
        get_active_device,
        get_last_device_operation,
        parse_percent_value,
    )
    from core.model_client import FakeModelClient, reset_model_client
    from core.planner import Planner
    from core.tool_result import ToolResult
    from core.tools import reset_registry_for_tests
    from memory.manager import MemoryManager

    reset_registry_for_tests()
    reset_model_client()
    ConversationContext.clear()
    HomeAssistant.get_registry().load_states([SAMPLE_LIGHT])

    rows = []
    failed = 0

    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")
        registry = build_default_registry()
        cm = ConversationManager(
            registry=registry,
            model=FakeModelClient(
                [{"type": "final_answer", "message": "Okej — o czym chcesz pogadać?"}]
                * 4
            ),
            memory_manager=mem,
        )
        cm.activate()
        cm.session_language = "pl"

        with patch.object(HaActions, "_call_service", return_value=True):
            # 1) set 30%
            r1 = cm.handle("Ustaw lampę na 30%.")
            active1 = get_active_device(cm.mem)
            op1 = get_last_device_operation(cm.mem)
            last1 = cm.last_observations[-1] if cm.last_observations else {}
            ok1 = (
                active1
                and active1.entity_id == "light.vizzini"
                and op1
                and op1.action == "light_brightness"
                and int(op1.value or 0) == 30
                and last1.get("tool") == "set_device_value"
            )
            rows.append(
                {
                    "input": "Ustaw lampę na 30%.",
                    "active_device": active1.entity_id if active1 else "",
                    "last_device_operation": op1.to_dict() if op1 else {},
                    "parsed_value": 30,
                    "inherited_action": "",
                    "tool": last1.get("tool"),
                    "canonical_tool_signature": canonical_tool_signature(
                        last1.get("tool") or "",
                        {},
                        last1,
                    ),
                    "repeat_blocked": False,
                    "tool_calls": 1,
                    "final": r1.text,
                    "pass_": ok1,
                }
            )

            # 2) Na sześćdziesiąt
            r2 = cm.handle("Na sześćdziesiąt.")
            active2 = get_active_device(cm.mem)
            op2 = get_last_device_operation(cm.mem)
            last2 = cm.last_observations[-1] if cm.last_observations else {}
            parsed = parse_percent_value("Na sześćdziesiąt.")
            ok2 = (
                parsed == 60
                and last2.get("tool") == "set_device_value"
                and (last2.get("data") or {}).get("action") == "light_brightness"
                and int((last2.get("data") or {}).get("value") or 0) == 60
                and last2.get("tool") != "turn_off_device"
            )
            rows.append(
                {
                    "input": "Na sześćdziesiąt.",
                    "active_device": active2.entity_id if active2 else "",
                    "last_device_operation": op2.to_dict() if op2 else {},
                    "parsed_value": parsed,
                    "inherited_action": "light_brightness",
                    "tool": last2.get("tool"),
                    "canonical_tool_signature": canonical_tool_signature(
                        last2.get("tool") or "",
                        {"action": (last2.get("data") or {}).get("action"), "value": 60},
                        last2,
                    ),
                    "repeat_blocked": False,
                    "tool_calls": 1,
                    "final": r2.text,
                    "pass_": ok2,
                }
            )

        # 3) mock planner identical turn_off ×3
        calls = []

        def turn_off(args):
            calls.append(dict(args))
            return ToolResult.ok(
                "Wyłączam Inspire Vizzini D56 Vizzini.",
                tool_name="turn_off_device",
                data={
                    "resolved_entity_id": "light.vizzini",
                    "resolved_domain": "light",
                    "resolved_name": "Inspire Vizzini D56 Vizzini",
                },
            )

        reg2 = build_default_registry()
        reg2._tools["turn_off_device"].handler = turn_off  # type: ignore[attr-defined]
        model = FakeModelClient(
            [
                {
                    "type": "tool_call",
                    "tool": "turn_off_device",
                    "arguments": {"target": "lampe"},
                },
                {
                    "type": "tool_call",
                    "tool": "turn_off_device",
                    "arguments": {
                        "target": "Inspire Vizzini D56 Vizzini",
                        "entity_id": "light.vizzini",
                    },
                },
                {
                    "type": "tool_call",
                    "tool": "turn_off_device",
                    "arguments": {"target": "lampe", "entity_id": "light.vizzini"},
                },
                {"type": "final_answer", "message": "Gotowe."},
            ]
        )
        planner = Planner(registry=reg2, model=model, max_steps=6)
        result3 = planner.run(
            "wyłącz lampę",
            context={
                "active_device": {
                    "entity_id": "light.vizzini",
                    "domain": "light",
                    "name": "Inspire Vizzini D56 Vizzini",
                },
                "raw_text": "wyłącz lampę",
            },
        )
        ok3 = len(calls) == 1
        rows.append(
            {
                "input": "(mock turn_off ×3)",
                "active_device": "light.vizzini",
                "last_device_operation": {},
                "parsed_value": None,
                "inherited_action": "",
                "tool": "turn_off_device",
                "canonical_tool_signature": "turn_off_device|entity=light.vizzini",
                "repeat_blocked": result3.source == "repeat_blocked" or len(calls) == 1,
                "tool_calls": len(calls),
                "final": result3.message,
                "pass_": ok3,
            }
        )

        # 4) Nieważne. Włącz lampę.
        mem4 = MemoryManager(Path(tmp) / "m4.json")
        reg4 = build_default_registry()
        cm4 = ConversationManager(
            registry=reg4,
            model=FakeModelClient(
                [{"type": "final_answer", "message": "Włączam lampę."}]
            ),
            memory_manager=mem4,
        )
        cm4.activate()
        on_calls = []

        def _svc(domain, service, entity_id, data=None):
            if service == "turn_on":
                on_calls.append(entity_id)
            return True

        with patch.object(HaActions, "_call_service", side_effect=_svc):
            light = HomeAssistant.get_registry().get("light.vizzini")
            if light is not None:
                light.state = "off"
            r4 = cm4.handle("Nieważne. Włącz lampę.")
        ok4 = (
            on_calls == ["light.vizzini"]
            and any(o.get("tool") == "turn_on_device" for o in cm4.last_observations)
            and "nie wykonałem" not in (r4.text or "").lower()
        )
        rows.append(
            {
                "input": "Nieważne. Włącz lampę.",
                "active_device": "light.vizzini",
                "last_device_operation": {},
                "parsed_value": None,
                "inherited_action": "",
                "tool": "turn_on_device",
                "canonical_tool_signature": "turn_on_device|entity=light.vizzini",
                "repeat_blocked": False,
                "tool_calls": len(on_calls),
                "final": r4.text,
                "pass_": ok4,
            }
        )

    for row in rows:
        if not row["pass_"]:
            failed += 1
        status = "PASS" if row["pass_"] else "FAIL"
        print(
            f"[{status}] input={row['input']!r}\n"
            f"  active_device={row['active_device']}\n"
            f"  last_device_operation={row['last_device_operation']}\n"
            f"  parsed_value={row['parsed_value']} "
            f"inherited_action={row['inherited_action']!r}\n"
            f"  tool={row['tool']} sig={row['canonical_tool_signature']}\n"
            f"  repeat_blocked={row['repeat_blocked']} tool_calls={row['tool_calls']}\n"
            f"  final={row['final']!r}"
        )

    print(
        f"\n=== device-continuation-smoke: {len(rows) - failed}/{len(rows)} PASS ==="
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_device_continuation_smoke())
