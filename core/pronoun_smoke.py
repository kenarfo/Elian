# ==========================
# ELIAN — Phase 12.2 pronoun-first device smoke
# ==========================
"""Canonical light.vizzini + pronoun commands — fixtures only."""

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
        "brightness": 128,
    },
}


def run_pronoun_smoke() -> int:
    import ConversationContext
    import HaActions
    import HomeAssistant
    from core.action_claim import detect_action_claim
    from core.builtin_tools import build_default_registry
    from core.conversation_manager import ConversationManager
    from core.device_context import get_active_device
    from core.device_pronoun import has_device_pronoun
    from core.model_client import FakeModelClient, reset_model_client
    from core.tools import reset_registry_for_tests
    from memory.manager import MemoryManager

    reset_registry_for_tests()
    reset_model_client()
    ConversationContext.clear()
    HomeAssistant.get_registry().load_states([SAMPLE_LIGHT])

    cases = [
        ("Przyciemnij ją.", "set_device_value", "adjust_brightness", True),
        ("Rozjaśnij ją.", "set_device_value", "adjust_brightness", False),
        ("Wyłącz ją.", "turn_off_device", None, None),
        ("Włącz ją.", "turn_on_device", None, None),
        ("Ustaw ją na 40%.", "set_device_value", "light_brightness", None),
    ]

    failed = 0
    rows = []

    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")
        cm = ConversationManager(
            registry=build_default_registry(),
            model=FakeModelClient(
                [
                    {
                        "type": "final_answer",
                        "message": "Przyciemniam Inspire Vizzini D56 Vizzini.",
                    }
                ]
                * 8
            ),
            memory_manager=mem,
        )
        cm.activate()
        mem.note_tool_success(
            "turn_on_device",
            {"target": "lampę"},
            {
                "success": True,
                "status": "success",
                "tool": "turn_on_device",
                "message": "ok",
                "data": {
                    "resolved_entity_id": "light.vizzini",
                    "resolved_domain": "light",
                    "resolved_name": "Inspire Vizzini D56 Vizzini",
                    "capabilities": ["brightness", "on_off"],
                },
            },
        )

        climate = []

        def _svc(domain, service, entity_id, data=None):
            if domain == "climate":
                climate.append(entity_id)
            return True

        with patch.object(HaActions, "_call_service", side_effect=_svc):
            for utterance, expect_tool, expect_action, expect_neg in cases:
                before = get_active_device(cm.mem)
                result = cm.handle(utterance)
                last = cm.last_observations[-1] if cm.last_observations else {}
                data = dict(last.get("data") or {})
                ctx = cm._agent_context(raw_text=utterance, goal_text=utterance)
                clarification = result.source == "clarify" or "które światło" in (
                    result.text or ""
                ).lower()
                fake_blocked = bool((result.data or {}).get("fake_action_claim_blocked"))
                value = data.get("value")
                ok = (
                    (before and before.entity_id == "light.vizzini")
                    and last.get("tool") == expect_tool
                    and data.get("resolved_entity_id") == "light.vizzini"
                    and data.get("resolved_domain") == "light"
                    and ctx["intent_family"] == "SMART_HOME"
                    and ctx["intent_family_complete"]
                    and not clarification
                    and climate == []
                    and not fake_blocked
                )
                if expect_action:
                    ok = ok and data.get("action") == expect_action
                if expect_neg is True:
                    ok = ok and int(value or 0) < 0
                if expect_neg is False:
                    ok = ok and int(value or 0) > 0
                if expect_action == "light_brightness":
                    ok = ok and int(value or 0) == 40
                if detect_action_claim(result.text or "") and not last.get("success"):
                    ok = False

                rows.append(
                    {
                        "pass_": ok,
                        "input": utterance,
                        "active_device": before.entity_id if before else "",
                        "pronoun_detected": has_device_pronoun(utterance),
                        "resolved_entity_id": data.get("resolved_entity_id"),
                        "resolved_domain": data.get("resolved_domain"),
                        "intent_family": ctx["intent_family"],
                        "intent_complete": ctx["intent_family_complete"],
                        "tool": last.get("tool"),
                        "tool_args": {
                            "action": data.get("action"),
                            "value": data.get("value"),
                            "entity_id": data.get("resolved_entity_id"),
                        },
                        "successful_observation": bool(last.get("success")),
                        "fake_action_claim_blocked": fake_blocked,
                        "clarification": clarification,
                        "final": result.text,
                    }
                )
                if not ok:
                    failed += 1

    for row in rows:
        status = "PASS" if row["pass_"] else "FAIL"
        print(
            f"[{status}] input={row['input']!r}\n"
            f"  active_device={row['active_device']}\n"
            f"  pronoun_detected={row['pronoun_detected']}\n"
            f"  resolved_entity_id={row['resolved_entity_id']} "
            f"resolved_domain={row['resolved_domain']}\n"
            f"  intent_family={row['intent_family']} "
            f"intent_complete={row['intent_complete']}\n"
            f"  tool={row['tool']} tool_args={row['tool_args']}\n"
            f"  successful_observation={row['successful_observation']}\n"
            f"  fake_action_claim_blocked={row['fake_action_claim_blocked']}\n"
            f"  clarification={row['clarification']}\n"
            f"  final={row['final']!r}"
        )
    print(f"\n=== pronoun-smoke: {len(rows) - failed}/{len(rows)} PASS ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_pronoun_smoke())
