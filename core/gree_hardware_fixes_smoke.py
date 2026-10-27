# ==========================
# ELIAN — Phase 12.3.1 GREE hardware-fix smoke (mock HA)
# ==========================
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _live_like_states():
    from core.climate_profile import gree_pular_fixture_states

    states = gree_pular_fixture_states()
    climate = dict(states[0])
    attrs = dict(climate["attributes"])
    attrs["swing_modes"] = [
        "default",
        "full_swing",
        "fixed_upper",
        "fixed_upper_middle",
        "fixed_middle",
        "fixed_lower_middle",
        "fixed_lower",
        "swing_upper",
        "swing_upper_middle",
        "swing_middle",
        "swing_lower_middle",
        "swing_lower",
    ]
    attrs["swing_horizontal_modes"] = [
        "default",
        "full_swing",
        "left",
        "left_center",
        "center",
        "right_center",
        "right",
    ]
    attrs["preset_modes"] = ["eco", "away", "boost", "none", "sleep"]
    climate["attributes"] = attrs
    return [climate] + list(states[1:])


def run_gree_hardware_fixes_smoke() -> int:
    import ConversationContext
    import HaActions
    import HomeAssistant
    from core.builtin_tools import build_default_registry
    from core.climate_control import wants_climate_eco
    from core.conversation_manager import ConversationManager
    from core.intent_family import classify_intent_family
    from core.model_client import FakeModelClient, reset_model_client
    from core.tools import reset_registry_for_tests
    from memory.manager import MemoryManager

    reset_registry_for_tests()
    reset_model_client()
    ConversationContext.clear()
    HomeAssistant.get_registry().load_states(_live_like_states())

    cases = [
        ("Włącz eko.", "climate_eco", "eco", None),
        ("Włącz tryb echo.", "climate_eco", "eco", None),
        ("Włącz tryb echo na klimatyzacji.", "climate_eco", "eco", None),
        ("Ustaw nawiew do góry.", "climate_vertical_swing", "fixed_upper", "vertical"),
        ("Włącz swing.", "climate_vertical_swing", "full_swing", "vertical"),
        ("Ustaw poziomy nawiew na lewo.", "climate_horizontal_swing", "left", "horizontal"),
    ]

    calls = []

    def _svc(domain, service, entity_id, data=None):
        calls.append((domain, service, entity_id, dict(data or {})))
        return domain != "light"

    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")
        cm = ConversationManager(
            registry=build_default_registry(),
            model=FakeModelClient(
                [{"type": "final_answer", "message": "Nie wykonałem jeszcze tej czynności."}] * 20
            ),
            memory_manager=mem,
        )
        cm.activate()
        cm.handle("Włącz klimatyzację.")
        with patch.object(HaActions, "_call_service", side_effect=_svc):
            for utterance, semantic, target, axis in cases:
                before = len(calls)
                info = classify_intent_family(utterance, active_domain="climate")
                result = cm.handle(utterance)
                new_calls = calls[before:]
                last = cm.last_observations[-1] if cm.last_observations else {}
                data = dict(last.get("data") or {})
                family = info.family.value
                tool = last.get("tool")
                success = bool(last.get("success"))
                mode = None
                for item in new_calls:
                    mode = (
                        item[3].get("preset_mode")
                        or item[3].get("swing_mode")
                        or item[3].get("swing_horizontal_mode")
                        or mode
                    )
                mode = mode or data.get("target_mode")
                app_hit = any(item[0] not in ("climate", "switch") for item in new_calls)
                ok = not app_hit and family != "APP"
                if semantic == "climate_eco":
                    ok = ok and mode == "eco" and any(
                        item[1] == "set_preset_mode" for item in new_calls
                    )
                    ok = ok and wants_climate_eco(utterance, climate_context=True)
                else:
                    ok = ok and mode == target
                status = "PASS" if ok else "FAIL"
                if not ok:
                    failed += 1
                print(
                    f"[{status}] input={utterance!r}\n"
                    f"  active_device=climate.gree_pular\n"
                    f"  normalized_intent={semantic}\n"
                    f"  intent_family={family}\n"
                    f"  semantic_action={data.get('semantic_action') or semantic}\n"
                    f"  axis={data.get('axis') or axis}\n"
                    f"  target_mode={mode}\n"
                    f"  tool={tool} tool_args={data.get('action')}={data.get('value') or data.get('extra')}\n"
                    f"  tool_success={success}\n"
                    f"  state_after={mode}\n"
                    f"  tool_result={result.text!r}"
                )
    print(f"\n=== gree-hardware-fixes-smoke: {len(cases) - failed}/{len(cases)} PASS ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_gree_hardware_fixes_smoke())
