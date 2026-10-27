# ==========================
# ELIAN — agent smoke test (Phase 10.1–10.5)
# ==========================
"""Offline agent contract smoke — no mic, dry-run tools."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List

from core.agent import run_agent
from core.conversation_manager import ConversationManager
from core.intent_family import classify_intent_family
from core.model_client import FakeModelClient, get_model_client, reset_model_client, set_model_client
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from memory.manager import MemoryManager


SMOKE_INPUTS = (
    "możesz odpalić mi spotify",
    "puść coś michaela jacksona",
    "co oznacza ai?",
    "Ok.",
    "Trochę tu ciemno.",
    "Nie, nie o to mi chodziło.",
    "A puścisz Michaela Jacksona?",
    "Dziękuję.",
    "Fajny masz głos.",
    "Nie wiem co robić.",
)


def _dry_registry() -> ToolRegistry:
    registry = ToolRegistry(confirm_medium=False)
    invoked: List[Dict[str, Any]] = []

    def _ok(name: str, message: str) -> Callable:
        def handler(args):
            payload = dict(args or {})
            invoked.append({"tool": name, "arguments": payload})
            data = dict(payload)
            if name == "set_device_value":
                data.setdefault(
                    "resolved_entity_id",
                    payload.get("entity_id") or "light.vizzini",
                )
                data["ha_verify"] = {"verification": "SUCCESS"}
                data["ha_action"] = str(payload.get("action") or "adjust_brightness")
            return ToolResult.ok(message, tool_name=name, data=data)

        return handler

    for spec in (
        ("open_app", "Uruchomiłem aplikację.", {"name": {"type": "string"}}, ["name"]),
        ("play_media", "Odtwarzam.", {"query": {"type": "string"}, "app": {"type": "string"}}, ["query"]),
        ("set_preference", "Zapisano.", {"key": {"type": "string"}, "value": {"type": "string"}}, ["key", "value"]),
        ("set_device_value", "OK.", {
            "action": {"type": "string"},
            "target": {"type": "string"},
            "domain": {"type": "string"},
            "value": {"type": ["integer", "number"]},
            "entity_id": {"type": "string"},
        }, []),
        ("describe_screen", "Ekran.", {}, []),
        ("create_reminder", "Przypomnienie.", {}, []),
    ):
        name, msg, props, required = spec
        registry.register(Tool(
            name=name,
            description=name,
            handler=_ok(name, msg),
            risk_level=RiskLevel.SAFE,
            parameters={
                "type": "object",
                "properties": props,
                "required": required,
            },
        ))

    # Verify tools required by plan_cycle.verify_spec_for — without these,
    # open_app/play_media skip verify (verify_tool_unregistered) and must not claim success.
    def _window_exists(args):
        invoked.append({"tool": "window_exists", "arguments": dict(args or {})})
        name = str((args or {}).get("name") or (args or {}).get("query") or "")
        return ToolResult.ok(
            f"Okno {name} jest.",
            tool_name="window_exists",
            data={"exists": True, "name": name},
        )

    def _get_now_playing(args):
        invoked.append({"tool": "get_now_playing", "arguments": dict(args or {})})
        return ToolResult.ok(
            "Odtwarzanie aktywne.",
            tool_name="get_now_playing",
            data={"title": "Michael Jackson", "artist": "Michael Jackson"},
        )

    registry.register(Tool(
        name="window_exists",
        description="Check whether a window/app is open.",
        handler=_window_exists,
        risk_level=RiskLevel.SAFE,
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}, "query": {"type": "string"}},
            "required": [],
        },
    ))
    registry.register(Tool(
        name="get_now_playing",
        description="Now playing media readback.",
        handler=_get_now_playing,
        risk_level=RiskLevel.SAFE,
        parameters={"type": "object", "properties": {}},
    ))
    def _get_volume(args):
        invoked.append({"tool": "get_volume", "arguments": dict(args or {})})
        return ToolResult.ok(
            "Głośność 40%.",
            tool_name="get_volume",
            data={"level": 40.0, "scalar": 0.4, "mute": False, "step": 20, "steps": 50},
        )

    registry.register(Tool(
        name="get_volume",
        description="OS volume readback.",
        handler=_get_volume,
        risk_level=RiskLevel.SAFE,
        parameters={"type": "object", "properties": {}},
    ))
    registry._dry_invoked = invoked  # type: ignore[attr-defined]
    return registry


def _bad_retry_client() -> FakeModelClient:
    """Simulates hardware: parse fail, open_app clarification, preference spam."""
    return FakeModelClient([
        "not valid json at all",
        {
            "type": "clarification",
            "question": "Czy chcesz otworzyć aplikację?",
        },
        {
            "type": "tool_call",
            "tool": "set_preference",
            "arguments": {"key": "music.default_app", "value": "Spotify"},
        },
        {"type": "tool_call", "tool": "open_app", "arguments": {}},
        {"type": "tool_call", "tool": "play_media", "arguments": {}},
    ])


def _preference_calls(invoked: List[Dict[str, Any]]) -> int:
    return sum(1 for item in invoked if item.get("tool") == "set_preference")


def _evaluate_talk_cm(text: str, registry: ToolRegistry) -> Dict[str, Any]:
    """Phase 10.5 — conversational UNKNOWN via CM + final_answer FakeModel."""
    if hasattr(registry, "_dry_invoked"):
        registry._dry_invoked.clear()  # type: ignore[attr-defined]
    lowered = text.strip().lower()
    if "głos" in lowered or "glos" in lowered:
        message = "Dzięki! Trochę nad nim pracowaliśmy."
    else:
        message = "Możemy pogadać albo coś odpalić — co wolisz?"
    talk_model = FakeModelClient([
        {"type": "final_answer", "message": message},
    ])
    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "mem.json")
        manager = ConversationManager(
            registry=registry,
            model=talk_model,
            memory_manager=mem,
        )
        manager.activate()
        handle = manager.handle(text)
    invoked = list(getattr(registry, "_dry_invoked", []) or [])
    spoken = (handle.text or "").lower()
    ok = (
        len(invoked) == 0
        and "nie zrozumiałem" not in spoken
        and bool(handle.text or handle.source in ("agent", "chat"))
    )
    return {
        "input": text,
        "family": classify_intent_family(text).family.value,
        "decision_type": handle.source or "talk",
        "tool": "",
        "clarification": "",
        "json_retry_count": 0,
        "tool_calls": len(invoked),
        "set_preference_calls": 0,
        "pass": ok,
        "message": handle.text or "",
        "steps": [],
    }


def _evaluate_input(text: str, registry: ToolRegistry, model) -> Dict[str, Any]:
    lowered = text.strip().lower()
    if "fajny masz" in lowered or "nie wiem co robić" in lowered:
        return _evaluate_talk_cm(text, registry)

    if hasattr(registry, "_dry_invoked"):
        registry._dry_invoked.clear()  # type: ignore[attr-defined]

    classification = classify_intent_family(text)
    result = run_agent(
        text,
        registry=registry,
        model=model,
        context={
            "raw_text": text,
            "intent_classification": classification,
            "preferences": {"music.default_app": "Spotify"},
        },
    )
    invoked = list(getattr(registry, "_dry_invoked", []) or [])
    pref_calls = _preference_calls(invoked)
    decision_type = ""
    tool = ""
    clarification = ""
    if result:
        if result.source in ("ack", "correction"):
            decision_type = result.source
        elif result.needs_clarification:
            decision_type = "clarification"
            clarification = result.clarification_question or result.message
        elif result.needs_knowledge:
            decision_type = "final_answer"
        elif result.steps:
            decision_type = "tool_call"
            tool = result.steps[-1].get("tool") or ""
        else:
            decision_type = "final_answer"
    json_retries = getattr(result, "json_retry_count", 0) if result else 0
    family = classification.family.value
    ok = False

    if lowered.rstrip(".!") in (
        "ok", "okej", "dobra", "dobrze", "spoko", "dzięki", "dzieki",
        "dziękuję", "dziekuje", "rozumiem", "jasne", "super",
    ) or text.strip() in ("Ok.", "OK.", "ok.", "Dziękuję.", "Dziekuje."):
        ok = (
            family == "CONVERSATION_CONTROL"
            and len(invoked) == 0
            and pref_calls == 0
            and (result is None or result.source in ("ack", "deterministic") or not result.steps)
        )
    elif text.startswith("możesz odpalić"):
        step0 = (result.steps[0] if result and result.steps else {}) or {}
        ver0 = step0.get("verification") or {}
        ok = bool(
            result
            and result.steps
            and step0.get("tool") == "open_app"
            and str((step0.get("arguments") or {}).get("name", "")).lower() == "spotify"
            and not result.needs_clarification
            and pref_calls == 0
            and (step0.get("observation") or {}).get("success") is True
            and str(ver0.get("status") or "").lower() == "passed"
            and str(ver0.get("reason") or "") != "verify_tool_unregistered"
        )
    elif text.startswith("puść coś"):
        step0 = (result.steps[0] if result and result.steps else {}) or {}
        ver0 = step0.get("verification") or {}
        ok = bool(
            result
            and not result.needs_clarification
            and result.steps
            and step0.get("tool") == "play_media"
            and "michael" in str(step0.get("arguments", {})).lower()
            and pref_calls == 0
            and (step0.get("observation") or {}).get("success") is True
            and str(ver0.get("status") or "").lower() == "passed"
            and str(ver0.get("reason") or "") != "verify_tool_unregistered"
        )
    elif "oznacza" in text:
        ok = bool(
            result
            and not result.needs_clarification
            and not result.steps
            and pref_calls == 0
            and (result.needs_knowledge or result.source in ("knowledge", "deterministic", "chat"))
        )
    elif "ciemno" in lowered:
        ok = (
            family == "SMART_HOME"
            and pref_calls == 0
            and not any(item.get("tool") == "open_app" for item in invoked)
            and (
                (result and result.needs_clarification and "świat" in (result.message or "").lower())
                or (result and "świat" in (result.message or "").lower())
                or (result and result.source == "deterministic")
            )
        )
    elif "nie o to" in lowered or "nie, nie" in lowered:
        ok = (
            family == "CORRECTION"
            and len(invoked) == 0
            and pref_calls == 0
            and (result is None or result.source in ("correction", "deterministic") or not result.steps)
        )
    elif "puścisz" in lowered or "puscisz" in lowered:
        ok = bool(
            family == "MEDIA"
            and pref_calls == 0
            and result
            and result.steps
            and result.steps[0].get("tool") == "play_media"
            and "michael" in str(result.steps[0].get("arguments", {})).lower()
        )

    return {
        "input": text,
        "family": family,
        "decision_type": decision_type,
        "tool": tool or (result.steps[0]["tool"] if result and result.steps else ""),
        "clarification": clarification,
        "json_retry_count": json_retries,
        "tool_calls": len(invoked),
        "set_preference_calls": pref_calls,
        "pass": ok,
        "message": result.message if result else "",
        "steps": result.steps if result else [],
    }


def _evaluate_clarify_followup(registry: ToolRegistry, model) -> Dict[str, Any]:
    """CM multi-turn: ciemno → clarify, then Lampę. must progress slots."""
    if hasattr(registry, "_dry_invoked"):
        registry._dry_invoked.clear()  # type: ignore[attr-defined]

    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "mem.json")
        manager = ConversationManager(
            registry=registry,
            model=model,
            memory_manager=mem,
        )
        manager.activate()
        first = manager.handle("Trochę tu ciemno.")
        pending = manager.pending_clarification or {}
        first_q = (first.text or pending.get("question") or "").strip().lower()
        second = manager.handle("Lampę.")
        second_q = (second.text or "").strip().lower()
        invoked = list(getattr(registry, "_dry_invoked", []) or [])
        missing = pending.get("missing_slots") or []
        progressed = False
        if second.source != "clarify":
            progressed = True
        elif second_q and second_q != first_q:
            progressed = True
        elif (manager.pending_clarification or {}).get("known_slots", {}).get("target_hint"):
            progressed = True
        elif (manager.pending_clarification or {}).get("known_slots", {}).get("target"):
            progressed = True
        ok = bool(
            first.should_speak
            and not any(item.get("tool") == "open_app" for item in invoked)
            and (
                # Ambiguous / multi-light: ask then progress on "Lampę."
                (
                    "świat" in first_q
                    and "target" in (pending.get("missing_slots") or ["target"])
                    and progressed
                )
                # Singleton registry: soft may brighten immediately (P0 unique bind).
                or any(
                    item.get("tool") in ("set_device_value", "turn_on_device", "turn_off_device")
                    for item in invoked
                )
            )
        )
        return {
            "input": "Trochę tu ciemno. → Lampę.",
            "family": "SMART_HOME",
            "decision_type": second.source or "clarify",
            "tool": "",
            "clarification": second.text if second.source == "clarify" else first.text,
            "json_retry_count": 0,
            "tool_calls": len(invoked),
            "set_preference_calls": 0,
            "pass": ok,
            "message": second.text or first.text,
            "steps": [],
        }


def run_agent_smoke(*, use_real_model: bool = False) -> int:
    print("=== ELIAN AGENT SMOKE (Phase 10.1–10.5) ===")
    print("Dry-run tools — no real side effects.")
    print()

    # Seed fixture HA world so DeviceResolver never blocks on live HA timeouts.
    try:
        from core.fake_ha_backend import DEFAULT_LIGHT, reset_fake_ha_backend
        from HomeAssistant import get_registry

        reset_fake_ha_backend()
        get_registry().load_states([DEFAULT_LIGHT])
    except Exception as exc:
        print(f"[agent-smoke] fake HA seed skipped: {exc}")

    registry = _dry_registry()
    reset_registry_for_tests(registry)

    if use_real_model:
        model = get_model_client()
        print(f"Model: {type(model).__name__}")
    else:
        model = _bad_retry_client()
        print("Model: FakeModelClient (json_retry + preference spam simulation)")
        set_model_client(model)

    rows: List[Dict[str, Any]] = []
    passed = 0
    for text in SMOKE_INPUTS:
        row = _evaluate_input(text, registry, model)
        rows.append(row)
        status = "PASS" if row["pass"] else "FAIL"
        if row["pass"]:
            passed += 1
        print(f"Input: {row['input']}")
        print(f"  family={row['family']}")
        print(f"  decision_type={row['decision_type']}")
        print(f"  tool={row['tool']}")
        print(f"  clarification={row['clarification'] or '-'}")
        print(f"  json_retry_count={row['json_retry_count']}")
        print(f"  tool_calls={row['tool_calls']}")
        print(f"  set_preference_calls={row['set_preference_calls']}")
        print(f"  {status}")
        print()

    follow = _evaluate_clarify_followup(registry, model)
    rows.append(follow)
    status = "PASS" if follow["pass"] else "FAIL"
    if follow["pass"]:
        passed += 1
    print(f"Input: {follow['input']}")
    print(f"  family={follow['family']}")
    print(f"  decision_type={follow['decision_type']}")
    print(f"  clarification={follow['clarification'] or '-'}")
    print(f"  tool_calls={follow['tool_calls']}")
    print(f"  {status}")
    print()

    total = len(SMOKE_INPUTS) + 1
    print(f"Result: {passed}/{total} PASS")
    print("=== END AGENT SMOKE ===")
    reset_registry_for_tests(None)
    if not use_real_model:
        reset_model_client()
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(run_agent_smoke())
