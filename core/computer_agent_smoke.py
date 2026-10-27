# ==========================
# ELIAN — Phase 13 Stage C computer-agent smoke (mocked, no mic)
# ==========================
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run_computer_agent_smoke() -> int:
    import HaActions
    import HomeAssistant
    from core.builtin_tools import build_default_registry
    from core.computer_agent import (
        ComputerAgent,
        computer_quality_metrics,
        notepad_fixture_store,
        reset_computer_agent_for_tests,
        reset_computer_metrics,
    )
    from core.compound_plan import parse_compound_goal
    from core.intent_family import classify_intent_family
    from core.model_client import FakeModelClient, reset_model_client
    from core.planner import Planner
    from core.tool_result import ToolStatus
    from core.tools import reset_registry_for_tests
    from vision.uia import FakeUIBackend

    reset_registry_for_tests()
    reset_model_client()
    reset_computer_metrics()
    HomeAssistant.get_registry().load_states(
        [
            {
                "entity_id": "light.vizzini",
                "state": "on",
                "attributes": {"friendly_name": "Lampa"},
            }
        ]
    )

    store = notepad_fixture_store()
    agent = ComputerAgent(store)
    agent._fake_ui = FakeUIBackend()
    reset_computer_agent_for_tests(agent)
    registry = build_default_registry(confirm_medium=False)

    rows = []

    def check(name, ok, detail=""):
        rows.append((name, bool(ok), str(detail or "")))

    r = agent.act("list_windows", {})
    check("list_windows", r.success and "Notatnik" in r.message, r.message)

    r = agent.act("get_active_window", {})
    check("active_window", r.success and "Notatnik" in r.message, r.message)

    r = agent.act("focus_window", {"name": "Kalkulator"})
    active = store.get_active()
    check(
        "focus_window",
        r.success and active is not None and "kalk" in (active.app_name + active.process_name).lower(),
        r.message,
    )
    agent.act("focus_window", {"name": "Notatnik"})

    r = agent.act("minimize_window", {"name": "Notatnik"})
    rec = store.get("Notatnik")
    check("minimize", r.success and rec is not None and rec.minimized, r.message)

    r = agent.act("restore_window", {"name": "Notatnik"})
    rec = store.get("Notatnik")
    check("restore", r.success and rec is not None and not rec.minimized, r.message)

    r = agent.act("maximize_window", {"name": "Notatnik"})
    rec = store.get("Notatnik")
    check("maximize", r.success and rec is not None and rec.maximized, r.message)
    agent.act("restore_window", {"name": "Notatnik"})

    r = agent.act("open_app", {"name": "Discord"})
    found = store.get("Discord")
    check("open_app_verify", r.success and found is not None and found.exists, r.message)

    r = agent.act("close_window", {"name": "Discord"})
    check("close_app_verify", r.success and store.get("Discord") is None, r.message)

    r = agent.act("inspect_ui", {})
    check(
        "inspect_ui",
        r.success and bool((r.data.get("observation") or {}).get("snapshot_id")),
        r.message,
    )

    found_el = agent.find_element("Text Editor", for_action=True)
    check("find_exact", bool(found_el.get("ok") and found_el.get("element")), found_el)

    np = store.record("win_notepad")
    clicks_before = list(agent._fake_ui.clicks)
    np.elements = [
        {"element_id": "b1", "role": "button", "name": "Pobierz", "confidence": 0.95, "enabled": True, "visible": True},
        {"element_id": "b2", "role": "button", "name": "Pobierz", "confidence": 0.94, "enabled": True, "visible": True},
    ]
    agent.inspect()
    r = agent.act("click_ui_element", {"query": "Pobierz"})
    check(
        "ambiguity",
        (not r.success) and r.data.get("needs_clarification") and agent._fake_ui.clicks == clicks_before,
        r.message,
    )

    np.elements = [
        {"element_id": "b3", "role": "button", "name": "Dalej", "confidence": 0.72, "enabled": True, "visible": True},
    ]
    agent.inspect()
    r = agent.act("click_ui_element", {"query": "Dalej"})
    check(
        "confidence_lock",
        (not r.success) and r.data.get("needs_clarification") and "b3" not in agent._fake_ui.clicks,
        r.message,
    )

    np.elements = [
        {
            "element_id": "b_save",
            "role": "button",
            "name": "Zapisz",
            "confidence": 0.96,
            "enabled": True,
            "visible": True,
            "actions": ["invoke"],
        },
        {
            "element_id": "input_1",
            "role": "document",
            "name": "Text Editor",
            "confidence": 0.97,
            "enabled": True,
            "visible": True,
            "focused": True,
            "value": "",
        },
    ]
    agent.inspect()
    r = agent.act("click_ui_element", {"query": "Zapisz"})
    check("click", r.success and r.data.get("clicked") and "b_save" in agent._fake_ui.clicks, r.message)

    agent.act("focus_window", {"name": "Notatnik"})
    r = agent.act("type_text", {"text": "Elian działa jak agent", "app_name": "Notatnik"})
    recn = store.record("win_notepad")
    check(
        "typing",
        r.success
        and recn is not None
        and "Elian działa jak agent" in recn.editor_text
        and r.data.get("verification_level") == "STRONG",
        r.message,
    )

    r = agent.act("select_all", {"keys": "ctrl+a", "semantic_action": "select_all"})
    recn = store.record("win_notepad")
    check("hotkey", r.success and recn is not None and recn.selected, r.message)

    agent.inspect()
    agent.reset_scan_counts()
    reused = agent._snapshot_reusable("input_1")
    r = agent.act("type_text", {"text": " perf", "app_name": "Notatnik", "append": True})
    check(
        "snapshot_reuse_no_full_scan",
        r.success and reused and agent.full_scan_count == 0 and agent.snapshot_reuse_count >= 1,
        f"scans={agent.full_scan_count} reused={agent.snapshot_reuse_count} {r.message}",
    )

    hwnd_ok = False
    recn = store.record("win_notepad")
    if recn is not None:
        agent.inspect()
        was_valid = agent._snapshot_reusable("input_1")
        old_hwnd = recn.info.hwnd
        recn.info.hwnd = 4242
        hwnd_ok = was_valid and not agent._snapshot_reusable("input_1")
        recn.info.hwnd = old_hwnd
    check("hwnd_invalidates_snapshot", hwnd_ok, "hwnd reuse guard")

    from core.computer_semantics import overlay_raw_type_payload, parse_computer_goal
    from core.text_pipeline import prepare_user_text

    typed_goal = parse_computer_goal("Wpisz Elian działa jak agent.")
    lowered_info = classify_intent_family("wpisz elian działa jak agent.")
    over = overlay_raw_type_payload(lowered_info, "Wpisz Elian działa jak agent.")
    check(
        "raw_type_payload_casing",
        typed_goal is not None
        and typed_goal.text == "Elian działa jak agent"
        and over.slots.get("text_payload") == "Elian działa jak agent"
        and lowered_info.family.value == "COMPUTER",
        f"{getattr(typed_goal, 'text', None)} {over.slots.get('text_payload')}",
    )

    live_raw = "Hej Elian, otwórz notatnik i wpisz Elian działa jak agent i zaznacz cały tekst."
    live_plan = parse_compound_goal(prepare_user_text(live_raw).raw_text or live_raw)
    live_type = next((s for s in live_plan.steps if s.semantic_action == "type_text"), None)
    check(
        "live_compound_raw_payload",
        live_type is not None
        and (live_type.slots or {}).get("text_payload") == "Elian działa jak agent",
        str((live_type.slots or {}).get("text_payload") if live_type else None),
    )

    agent.inspect()
    agent.act("type_text", {"text": "cache-hit", "app_name": "Notatnik"})
    agent.reset_scan_counts()
    cached_sel = agent.act("select_all", {"semantic_action": "select_all", "app_name": "Notatnik"})
    check(
        "editor_handle_cache",
        cached_sel.success and agent.descendants_search_count == 0,
        f"descendants={agent.descendants_search_count} {cached_sel.message}",
    )

    from WakeWord import extract_command_from_transcript
    from core.computer_agent import add_cursor_window

    wake_raw = "Hej Elian, otwórz Notatnik i wpisz Elian działa jak agent i zaznacz cały tekst."
    wake_cmd = extract_command_from_transcript(wake_raw)
    prepared = prepare_user_text(wake_raw)
    check(
        "wake_preserve_elian",
        wake_cmd == "otwórz Notatnik i wpisz Elian działa jak agent i zaznacz cały tekst"
        and "elian działa jak agent" in prepared.semantic_text.lower()
        and not prepared.semantic_text.lower().startswith("działa"),
        wake_cmd,
    )

    agent.begin_plan("smoke-bind", goal="Otwórz Notatnik, wpisz Elian działa jak agent i zaznacz cały tekst.")
    agent.act("focus_window", {"name": "Notatnik"})
    typed_bind = agent.act("type_text", {"text": "bind-check"})
    sel_bind = agent.act("select_all", {"semantic_action": "select_all"})
    check(
        "same_plan_bind",
        typed_bind.success
        and sel_bind.success
        and agent.plan_working_window == "win_notepad"
        and store.last_hotkey_window == "win_notepad",
        f"{agent.plan_working_window} {store.last_hotkey_window}",
    )

    add_cursor_window(store)
    store.focus("win_cursor")
    store.last_hotkey = ""
    keys_before = list(agent._fake_ui.keys)
    r = agent.act("select_all", {"semantic_action": "select_all", "app_name": "Notatnik"})
    check(
        "wrong_window_guard",
        r.success
        and store.last_hotkey_window == "win_notepad"
        and "cursor" not in (store.last_hotkey_process or "").lower(),
        f"{store.last_hotkey_window} {store.last_hotkey_process} {r.message}",
    )

    rec_np = store.record("win_notepad")
    rec_np.supports_selection = False
    rec_np.selected = False
    r = agent.act("select_all", {"semantic_action": "select_all", "app_name": "Notatnik"})
    check(
        "hotkey_verification",
        r.success
        and r.data.get("target_verified")
        and r.data.get("verification_level") == "BASIC",
        str(r.data.get("verification_level")),
    )

    store._windows["win_notepad"].info.exists = False
    store._windows["win_calc"].info.exists = False
    store.focus("win_cursor")
    agent.begin_plan("smoke-ide")
    agent.last_working_app = ""
    agent.last_working_window = ""
    agent.last_working_element = ""
    store.last_hotkey = ""
    keys_before = list(agent._fake_ui.keys)
    r = agent.act("select_all", {"semantic_action": "select_all"})
    check(
        "ide_self_protect",
        (not r.success) and store.last_hotkey == "" and agent._fake_ui.keys == keys_before,
        r.message,
    )

    store._windows["win_notepad"].info.exists = True
    store._windows["win_calc"].info.exists = True
    explicit = parse_computer_goal("W Cursorze zaznacz wszystko.")
    r = agent.act(
        "select_all",
        {
            "semantic_action": "select_all",
            "app_name": explicit.app_name if explicit else "Cursor",
            "user_goal": "W Cursorze zaznacz wszystko.",
        },
    )
    check(
        "explicit_cursor_allowed",
        bool(explicit and explicit.app_name == "Cursor")
        and r.success
        and store.last_hotkey_window == "win_cursor",
        f"{getattr(explicit, 'app_name', None)} {store.last_hotkey_window} {r.message}",
    )

    rec_np.supports_selection = True
    store.focus("win_notepad")
    recn = store.record("win_notepad")

    r = agent.act("scroll_down", {"amount": 2})
    check("scroll", r.success and bool(store.scrolls), r.message)

    agent.inspect()
    clicks_before = list(agent._fake_ui.clicks)
    store.bump_window("win_notepad")
    r = agent.act("click_ui_element", {"element_id": "oldsnap:input_1", "attempt_id": 1})
    check(
        "stale_snapshot",
        (not r.success) and (not r.data.get("clicked")) and agent._fake_ui.clicks == clicks_before,
        f"{r.error} {r.message}",
    )

    recn = store.record("win_notepad")
    recn.unsaved = True
    r = agent.act("close_window", {"name": "Notatnik"})
    check(
        "unsaved_dialog",
        (not r.success)
        and r.status == ToolStatus.NEEDS_CONFIRMATION
        and store.dont_save_clicks == 0
        and recn.info.exists,
        r.message,
    )

    text = "Otwórz Notatnik, wpisz test i wyłącz lampę."
    plan = parse_compound_goal(text)
    families = [s.family for s in plan.steps]
    with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
        with patch.object(HaActions, "_call_service", return_value=True):
            result = Planner(registry, model=FakeModelClient([])).run(text)
    check(
        "cross_domain",
        plan.detected and families[:3] == ["APP", "COMPUTER", "SMART_HOME"] and result.source == "compound",
        str(families),
    )

    np.elements = []
    agent.inspect()
    r = agent.act("click_ui_element", {"query": "NieistniejącyPrzyciskXYZ"})
    check(
        "no_fake_success",
        (not r.success) and "klikną" not in (r.message or "").lower(),
        r.message,
    )

    import time
    from core.risk import RiskLevel
    from core.tool_result import ToolResult, ToolStatus
    from core.tools import Tool

    agent._after_verified_hook = lambda: time.sleep(0.25)
    type_tool = registry.get("type_text")
    old_timeout = type_tool.timeout if type_tool is not None else 12
    if type_tool is not None:
        type_tool.timeout = 0.05
    store.focus("win_notepad")
    rec_alive = store.record("win_notepad")
    if rec_alive is not None:
        rec_alive.info.exists = True
        rec_alive.elements = [
            {
                "element_id": "input_1",
                "role": "document",
                "name": "Text Editor",
                "enabled": True,
                "visible": True,
                "focused": True,
                "confidence": 0.97,
                "value": rec_alive.editor_text,
            }
        ]
    agent.last_observation = None
    agent.inspect()
    late = registry.invoke("type_text", {"text": "late", "app_name": "Notatnik"})
    if type_tool is not None:
        type_tool.timeout = old_timeout
    agent._after_verified_hook = None
    check(
        "late_verified_not_timeout",
        late.success and late.status != ToolStatus.TIMEOUT,
        f"{late.status} {late.message}",
    )

    def slow(_arguments):
        time.sleep(0.2)
        return ToolResult.ok("late", tool_name="slow_unverified")

    registry.register(
        Tool(
            name="slow_unverified",
            description="slow",
            handler=slow,
            timeout=0.05,
            risk_level=RiskLevel.SAFE,
        )
    )
    genuine = registry.invoke("slow_unverified", {})
    check(
        "genuine_timeout",
        (not genuine.success) and genuine.status == ToolStatus.TIMEOUT,
        f"{genuine.status} {genuine.message}",
    )

    for name, ok, detail in rows:
        print(f"[COMPUTER-SMOKE] {'PASS' if ok else 'FAIL'} {name}: {detail}")

    info = classify_intent_family("Jaka aplikacja jest teraz aktywna?")
    status_ok = info.family.value == "COMPUTER" and str((info.slots or {}).get("source_kind") or "") == "LOCAL_COMPUTER"
    print(f"[COMPUTER-SMOKE] {'PASS' if status_ok else 'FAIL'} status_local_computer: {info.family.value}")

    metrics = computer_quality_metrics()
    metrics_ok = all(int(v) == 0 for v in metrics.values())
    print(f"[METRICS] {metrics}")

    passed = sum(1 for _n, ok, _d in rows if ok)
    print(f"computer-agent-smoke {passed}/{len(rows)}")
    if passed == len(rows) and status_ok and metrics_ok:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(run_computer_agent_smoke())
