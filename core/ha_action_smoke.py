# ==========================
# ELIAN — C.5.5 HA action smoke (fake backend, no live HA)
# ==========================
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


SAMPLE_OFF = {
    "entity_id": "light.vizzini",
    "state": "off",
    "attributes": {
        "friendly_name": "Inspire Vizzini D56 Vizzini",
        "supported_color_modes": ["rgb", "color_temp", "brightness"],
        "brightness": 0,
    },
}
SAMPLE_ON = {
    "entity_id": "light.vizzini",
    "state": "on",
    "attributes": {
        "friendly_name": "Inspire Vizzini D56 Vizzini",
        "supported_color_modes": ["rgb", "color_temp", "brightness"],
        "brightness": 128,
    },
}
CLIMATE = {
    "entity_id": "climate.salon",
    "state": "heat",
    "attributes": {"friendly_name": "Klimatyzacja salon", "temperature": 22},
}


def run_ha_action_smoke() -> int:
    import HaActions
    import HomeAssistant
    from core.action_firewall import allow_tool
    from core.builtin_tools import build_default_registry
    from core.conversation_manager import ConversationManager
    from core.device_identity import dedup_device_name, has_duplicate_device_token
    from core.fast_path import FastPathStatus, evaluate_fast_path
    from core.ha_speech import ha_user_message, speak_ha_result
    from core.ha_verify import SUCCESS, UNKNOWN, clear_unverified, execute_verified
    from core.model_client import FakeModelClient, reset_model_client
    from core.semantic_executive import decide_semantics, fast_path_permitted
    from core.tool_result import ToolResult
    from core.tools import reset_registry_for_tests
    from memory.manager import MemoryManager

    reset_registry_for_tests()
    reset_model_client()
    clear_unverified()
    failed = 0
    rows = []

    def _row(name, ok, detail=""):
        rows.append((name, ok, detail))
        if not ok:
            print(f"FAIL {name}: {detail}")

    def _load(state):
        HomeAssistant.get_registry().load_states([state, CLIMATE])

    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")

        def _mgr():
            mgr = ConversationManager(
                registry=build_default_registry(confirm_medium=False),
                model=FakeModelClient([]),
                memory_manager=mem,
            )
            mgr.activate()
            return mgr

        env = decide_semantics("włącz światło", allow_llm=False)
        _row(
            "semantic_turn_on",
            env.mode == "ACT"
            and env.intent == "smart_home_control"
            and "SMART_HOME" in env.allowed_domains
            and fast_path_permitted("włącz światło", env)
            and evaluate_fast_path("włącz światło", env).status == FastPathStatus.HANDLED_COMPLETE,
            f"mode={env.mode} intent={env.intent}",
        )

        env_off = decide_semantics("wyłącz światło", allow_llm=False)
        _row(
            "semantic_turn_off",
            env_off.mode == "ACT" and env_off.intent == "smart_home_control",
            env_off.intent,
        )

        env_b = decide_semantics("ustaw światło na 50 procent", allow_llm=False)
        _row(
            "semantic_brightness",
            env_b.mode in ("ACT", "ASK") and env_b.needs_tools,
            env_b.mode,
        )

        env_neg = decide_semantics("Nie włączaj światła.", allow_llm=False)
        allowed, _ = allow_tool(env_neg, "turn_on_device", "Nie włączaj światła.")
        _row(
            "negation",
            env_neg.needs_tools is False and not allowed,
            env_neg.intent,
        )

        env_h = decide_semantics("Co się stanie, jeśli włączysz światło?", allow_llm=False)
        _row(
            "hypothetical",
            not env_h.needs_tools
            and evaluate_fast_path(
                "Co się stanie, jeśli włączysz światło?", env_h
            ).status
            == FastPathStatus.NOT_HANDLED,
            env_h.mode,
        )

        env_q = decide_semantics("Powiedziałem mu: włącz światło.", allow_llm=False)
        _row(
            "quoted_command",
            not env_q.needs_tools
            and evaluate_fast_path("Powiedziałem mu: włącz światło.", env_q).status
            == FastPathStatus.NOT_HANDLED,
            env_q.intent,
        )

        _load(SAMPLE_ON)
        entity = HomeAssistant.get_registry().get("light.vizzini")
        calls = []

        def _svc(domain, service, entity_id, data=None):
            calls.append((domain, service, entity_id))
            return True

        with patch.object(HaActions, "_call_service", side_effect=_svc):
            already = execute_verified("device_on", entity, utterance="włącz światło")
        _row(
            "already_on",
            already.outcome == "ALREADY_SATISFIED" and not calls and not already.mutated,
            already.outcome,
        )
        _row(
            "already_on_speech",
            "już" in already.message.lower() and "włączone" in already.message.lower(),
            already.message,
        )

        entity.state = "off"
        _load(SAMPLE_OFF)
        entity = HomeAssistant.get_registry().get("light.vizzini")
        with patch.object(HaActions, "_call_service", side_effect=_svc):
            off_already = execute_verified("device_off", entity, utterance="wyłącz światło")
        _row(
            "already_off",
            off_already.outcome == "ALREADY_SATISFIED" and not off_already.mutated,
            off_already.outcome,
        )

        _load(SAMPLE_OFF)
        entity = HomeAssistant.get_registry().get("light.vizzini")
        calls.clear()
        with patch.object(HaActions, "_call_service", side_effect=_svc):
            ok = execute_verified("device_on", entity, utterance="włącz światło")
        _row(
            "verify_success",
            ok.outcome == SUCCESS and ok.after == "on" and ok.mutated,
            f"{ok.outcome} after={ok.after}",
        )
        _row(
            "natural_response",
            "włączone" in ok.message.lower() and "włączam" not in ok.message.lower(),
            ok.message,
        )
        _row(
            "no_name_duplication",
            not has_duplicate_device_token(ok.message)
            and "vizzini" not in ok.message.lower()
            and "light.vizzini" not in ok.message,
            ok.message,
        )

        _load(SAMPLE_OFF)
        entity = HomeAssistant.get_registry().get("light.vizzini")
        clear_unverified()

        def _accept_no_change(domain, service, entity_id, data=None):
            return True

        with patch.object(HaActions, "_call_service", side_effect=_accept_no_change):
            fail_v = execute_verified(
                "device_on",
                entity,
                utterance="włącz światło",
                read_state=lambda eid: entity,
                timeout_s=0.2,
            )
        _row(
            "verify_failure",
            fail_v.outcome == UNKNOWN,
            fail_v.outcome,
        )
        _row(
            "verify_unknown",
            "nie mogę potwierdzić" in fail_v.message.lower(),
            fail_v.message,
        )

        retry_calls = []

        def _count(domain, service, entity_id, data=None):
            retry_calls.append(service)
            return True

        with patch.object(HaActions, "_call_service", side_effect=_count):
            again = execute_verified(
                "device_on",
                entity,
                utterance="włącz światło",
                read_state=lambda eid: entity,
                timeout_s=0.2,
            )
        _row(
            "unknown_no_blind_retry",
            not retry_calls and again.service_status == "skipped_unverified_retry",
            f"calls={retry_calls} status={again.service_status}",
        )

        env_joke = decide_semantics("opowiedz żart", allow_llm=False)
        _row(
            "social_no_ha",
            env_joke.mode == "TALK" and not env_joke.needs_tools,
            env_joke.intent,
        )
        env_poison = decide_semantics(
            "włącz światło",
            runtime_context={"active_service": {"service_name": "Spotify"}},
            allow_llm=False,
        )
        _row(
            "context_poison",
            env_poison.intent == "smart_home_control" and env_poison.mode == "ACT",
            env_poison.intent,
        )

        spoken = speak_ha_result(
            ToolResult.ok(
                "Włączam Inspire Vizzini D56 Vizzini.",
                data={
                    "verification": "SUCCESS",
                    "ha_outcome": "SUCCESS",
                    "ha_action": "device_on",
                    "spoken_name": "światło",
                    "entity_id": "light.vizzini",
                },
                tool_name="turn_on_device",
            ),
            utterance="włącz światło",
        )
        _row(
            "jarvis_not_raw_tool",
            "włączam" not in spoken.lower() and "inspire" not in spoken.lower(),
            spoken,
        )
        _row(
            "dedup_friendly",
            dedup_device_name("Inspire Vizzini D56 Vizzini") == "Inspire Vizzini D56",
            dedup_device_name("Inspire Vizzini D56 Vizzini"),
        )

        clear_unverified()
        _load(SAMPLE_OFF)
        with patch.object(HaActions, "_call_service", return_value=True):
            mgr = _mgr()
            result = mgr.handle("włącz światło")
        text = (result.text or "").lower()
        _row(
            "cm_natural_fastpath",
            "włączone" in text and "czy mogę pomóc" not in text and "light.vizzini" not in text,
            result.text,
        )

        unavailable = HomeAssistant.entity_from_state(
            {
                "entity_id": "light.vizzini",
                "state": "unavailable",
                "attributes": {"friendly_name": "Inspire Vizzini D56"},
            }
        )
        HomeAssistant.get_registry().load_states(
            [
                {
                    "entity_id": "light.vizzini",
                    "state": "unavailable",
                    "attributes": {"friendly_name": "Inspire Vizzini D56"},
                }
            ]
        )
        with patch.object(HaActions, "_call_service", return_value=True):
            un = execute_verified("device_on", unavailable, utterance="włącz światło")
        _row("entity_unavailable", un.outcome == "UNAVAILABLE", un.outcome)

        msg = ha_user_message(outcome="UNKNOWN", action="device_on", data={"spoken_name": "światło"})
        _row("honest_unknown_copy", "nie mogę potwierdzić" in msg.lower() and "gotowe" not in msg.lower(), msg)

    passed = sum(1 for _, ok, _ in rows if ok)
    failed = sum(1 for _, ok, _ in rows if not ok)
    print(f"ha-action-smoke {passed}/{len(rows)}")
    return 0 if failed == 0 else 1
