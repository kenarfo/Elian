# ==========================
# ELIAN — Phase 12.3.2 local device status smoke (mock HA, non-destructive)
# ==========================
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run_local_status_smoke() -> int:
    import ConversationContext
    import HaActions
    import HomeAssistant
    from core.builtin_tools import build_default_registry
    from core.climate_control import plan_climate_utterance
    from core.climate_profile import gree_pular_fixture_states
    from core.conversation_manager import ConversationManager
    from core.device_context import ActiveDevice, sync_active_device_to_conversation_context
    from core.freshness import analyze_freshness
    from core.intent_family import classify_intent_family
    from core.local_device_status import classify_local_request
    from core.model_client import FakeModelClient, reset_model_client
    from core.tool_result import ToolResult
    from core.tools import reset_registry_for_tests
    from memory.manager import MemoryManager

    reset_registry_for_tests()
    reset_model_client()
    ConversationContext.clear()
    HomeAssistant.get_registry().load_states(gree_pular_fixture_states())

    cases = [
        ("Jaki jest teraz tryb klimatyzacji?", "LOCAL_HA", "SMART_HOME", "climate_status_hvac", False),
        ("Jaka jest ustawiona temperatura?", "LOCAL_HA", "SMART_HOME", "climate_status_target_temperature", False),
        ("Jaka jest temperatura w pokoju?", "LOCAL_HA", "SMART_HOME", "climate_status_current_temperature", False),
        ("Jaki jest nawiew?", "LOCAL_HA", "SMART_HOME", "climate_status_fan", False),
        ("Czy tryb cichy jest włączony?", "LOCAL_HA", "SMART_HOME", "climate_status_quiet", False),
        ("Co teraz robi klimatyzacja?", "LOCAL_HA", "SMART_HOME", "climate_status_summary", False),
        ("Co oznacza tryb auto w klimatyzacji?", "KNOWLEDGE", "KNOWLEDGE", "knowledge", False),
        ("Kto jest teraz premierem Wielkiej Brytanii?", "WEB", "WEB", "web_search", True),
    ]

    calls = []
    web_calls = []

    def _svc(domain, service, entity_id, data=None):
        calls.append((domain, service, entity_id, dict(data or {})))
        return True

    failed = 0
    rows = []

    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")
        mem.set_preference("music.default_app", "Spotify", explicit=True, source="user")
        mem.short.set_entity("website", "i jest teraz tryb klimatyzacji")
        device = ActiveDevice(
            entity_id="climate.gree_pular",
            domain="climate",
            name="Klimatyzacja",
        )
        mem.short.set_entity(
            "device",
            "Klimatyzacja",
            entity_id=device.entity_id,
            domain="climate",
        )
        sync_active_device_to_conversation_context(device)
        registry = build_default_registry()
        original_invoke = registry.invoke

        def _invoke(name, arguments=None, confirmed=False):
            if str(name) == "web_search":
                web_calls.append(dict(arguments or {}))
                return ToolResult.ok(
                    "Keir Starmer jest premierem Wielkiej Brytanii.",
                    tool_name="web_search",
                    data={"hits": [{"title": "UK PM", "snippet": "Keir Starmer"}]},
                )
            return original_invoke(name, arguments, confirmed=confirmed)

        registry.invoke = _invoke  # type: ignore[method-assign]
        cm = ConversationManager(
            registry=registry,
            model=FakeModelClient(
                [
                    {
                        "type": "final_answer",
                        "message": "Tryb Auto oznacza automatyczny dobór mocy.",
                    }
                ]
                * 12
            ),
            memory_manager=mem,
        )
        cm.activate()
        with patch.object(HaActions, "_call_service", side_effect=_svc), patch(
            "core.conversation_manager.ask_ai",
            return_value="Tryb Auto oznacza, że urządzenie samo dobiera parametry.",
        ):
            for utterance, source_kind, family, semantic, expect_web in cases:
                before_ha = list(calls)
                before_web = list(web_calls)
                local = classify_local_request(utterance, active_domain="climate")
                info = classify_intent_family(utterance, active_domain="climate")
                plan = plan_climate_utterance(utterance, device)
                result = cm.handle(utterance)
                new_ha = calls[len(before_ha):]
                new_web = web_calls[len(before_web):]
                mutating = any(
                    item[1] in (
                        "set_hvac_mode",
                        "turn_on",
                        "turn_off",
                        "set_temperature",
                        "set_fan_mode",
                        "set_preset_mode",
                        "set_swing_mode",
                    )
                    for item in new_ha
                )
                web_used = bool(new_web) or analyze_freshness(utterance).freshness_required and family == "WEB"
                if expect_web:
                    web_used = True if new_web or info.family.value == "WEB" else False
                got_source = local.kind if local.kind != "UNKNOWN" else (
                    "WEB" if info.family.value == "WEB" else local.kind
                )
                got_semantic = (plan.semantic_action if plan and plan.handled else "") or local.semantic_action
                entity_id = (plan.entity_id if plan and plan.handled else "") or (
                    "climate.gree_pular" if source_kind == "LOCAL_HA" else ""
                )
                tool = "get_device_state" if source_kind == "LOCAL_HA" else (
                    "web_search" if expect_web else None
                )
                ok = info.family.value == family
                ok = ok and (got_semantic == semantic or (expect_web and family == "WEB"))
                if source_kind == "LOCAL_HA":
                    ok = ok and got_source == "LOCAL_HA" and not new_web and not mutating
                    ok = ok and bool(plan and plan.status_text and not plan.steps)
                elif family == "KNOWLEDGE":
                    ok = ok and not mutating and not new_web
                    ok = ok and (plan is None or not plan.handled)
                elif expect_web:
                    ok = ok and analyze_freshness(utterance).freshness_required
                    ok = ok and not mutating
                rows.append(
                    {
                        "ok": ok,
                        "input": utterance,
                        "source_kind": got_source,
                        "intent_family": info.family.value,
                        "semantic_action": got_semantic,
                        "entity_id": entity_id,
                        "web_used": bool(new_web) or (expect_web and info.family.value == "WEB"),
                        "tool": tool if source_kind == "LOCAL_HA" or expect_web else None,
                        "mutating": mutating,
                        "response": (result.text or "")[:120],
                    }
                )
                if not ok:
                    failed += 1

    print("==================================================")
    print("LOCAL STATUS SMOKE")
    print("==================================================")
    for row in rows:
        flag = "PASS" if row["ok"] else "FAIL"
        print(f"input={row['input']}")
        print(f"source_kind={row['source_kind']}")
        print(f"intent_family={row['intent_family']}")
        print(f"semantic_action={row['semantic_action']}")
        print(f"entity_id={row['entity_id']}")
        print(f"web_used={row['web_used']}")
        print(f"tool={row['tool']}")
        print(f"mutating={row['mutating']}")
        print(f"response={row['response']}")
        print(flag)
        print("---")
    total = len(rows)
    passed = total - failed
    print(f"{passed}/{total} {'PASS' if failed == 0 else 'FAIL'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_local_status_smoke())
