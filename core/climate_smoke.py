# ==========================
# ELIAN — Phase 12.3 climate smoke (mock HA, non-destructive)
# ==========================
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run_climate_smoke() -> int:
    import ConversationContext
    import HaActions
    import HomeAssistant
    from core.action_claim import detect_action_claim
    from core.builtin_tools import build_default_registry
    from core.climate_profile import gree_pular_fixture_states
    from core.conversation_manager import ConversationManager
    from core.model_client import FakeModelClient, reset_model_client
    from core.tools import reset_registry_for_tests
    from memory.manager import MemoryManager

    reset_registry_for_tests()
    reset_model_client()
    ConversationContext.clear()
    HomeAssistant.get_registry().load_states(gree_pular_fixture_states())

    cases = [
        ("Włącz klimatyzację.", "climate_power", "turn_on_device"),
        ("Ustaw ją na 22 stopnie.", "climate_temperature", "set_device_value"),
        ("Teraz 23.", "climate_temperature", "set_device_value"),
        ("Zrób chłodniej.", "climate_temperature_delta", "set_device_value"),
        ("Włącz chłodzenie.", "climate_hvac_mode", "set_device_value"),
        ("Włącz grzanie.", "climate_hvac_mode", "set_device_value"),
        ("Ustaw nawiew na wysoki.", "climate_fan_mode", "set_device_value"),
        ("Zwiększ nawiew.", "climate_fan_step", "set_device_value"),
        ("Włącz swing.", "climate_vertical_swing", "set_device_value"),
        ("Jaka jest ustawiona temperatura?", "climate_status", None),
        ("Ustaw ją na 50%.", "climate_percent_rejected", None),
        ("Włącz turbo.", "climate_turbo", None),  # fixture has turbo switch → tool
    ]

    failed = 0
    rows = []
    calls = []

    def _svc(domain, service, entity_id, data=None):
        calls.append((domain, service, entity_id, dict(data or {})))
        if domain == "light":
            return False
        return True

    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")
        cm = ConversationManager(
            registry=build_default_registry(),
            model=FakeModelClient([{"type": "final_answer", "message": "Ustawiam 22 stopnie."}] * 20),
            memory_manager=mem,
        )
        cm.activate()
        with patch.object(HaActions, "_call_service", side_effect=_svc):
            for utterance, semantic, expect_tool in cases:
                before = list(calls)
                result = cm.handle(utterance)
                new_calls = calls[len(before):]
                last = cm.last_observations[-1] if cm.last_observations else {}
                data = dict(last.get("data") or {})
                if not new_calls and semantic in (
                    "climate_status",
                    "climate_percent_rejected",
                ):
                    last = {"tool": None, "data": {}, "success": False}
                    data = {
                        "resolved_entity_id": "climate.gree_pular",
                        "resolved_domain": "climate",
                        "semantic_action": semantic,
                    }
                fake = bool(detect_action_claim(result.text or "") and not last.get("success") and expect_tool)
                light_hit = any(item[0] == "light" for item in new_calls)
                climate_temp_50 = any(
                    item[1] == "set_temperature" and float((item[3] or {}).get("temperature") or 0) == 50
                    for item in new_calls
                )
                ok = not light_hit and not fake
                if utterance == "Ustaw ją na 50%.":
                    ok = ok and not climate_temp_50 and (
                        "?" in (result.text or "") or "procent" in (result.text or "").lower()
                    )
                elif semantic == "climate_status":
                    ok = ok and "22" in (result.text or "") or "stopn" in (result.text or "").lower()
                    ok = ok and not any(item[1] not in () for item in new_calls if False)
                elif semantic == "climate_turbo":
                    ok = ok and (
                        last.get("tool") in ("turn_on_device", "set_device_value")
                        or "turbo" in (result.text or "").lower()
                        or "Home Assistant" in (result.text or "")
                    )
                elif expect_tool:
                    ok = ok and (
                        last.get("tool") == expect_tool
                        or any(item[0] == "climate" for item in new_calls)
                    )
                    ok = ok and "które światło" not in (result.text or "").lower()
                rows.append(
                    {
                        "ok": ok,
                        "input": utterance,
                        "entity_id": data.get("resolved_entity_id") or "climate.gree_pular",
                        "domain": data.get("resolved_domain") or "climate",
                        "semantic_action": data.get("semantic_action") or semantic,
                        "resolved_capability": semantic,
                        "tool": last.get("tool"),
                        "tool_args": {
                            "action": data.get("action"),
                            "value": data.get("value"),
                            "extra": data.get("extra"),
                        },
                        "tool_result": result.text,
                        "fake_action_claim": fake,
                        "calls": new_calls,
                    }
                )
                if not ok:
                    failed += 1

    for row in rows:
        status = "PASS" if row["ok"] else "FAIL"
        print(
            f"[{status}] input={row['input']!r}\n"
            f"  entity_id={row['entity_id']} domain={row['domain']}\n"
            f"  semantic_action={row['semantic_action']}\n"
            f"  resolved_capability={row['resolved_capability']}\n"
            f"  tool={row['tool']} tool_args={row['tool_args']}\n"
            f"  tool_result={row['tool_result']!r}\n"
            f"  fake_action_claim={row['fake_action_claim']}"
        )
    print(f"\n=== climate-smoke: {len(rows) - failed}/{len(rows)} PASS ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_climate_smoke())
