# ==========================
# ELIAN — official post-STT text harness (2.0.0-rc1)
# ==========================
"""Inject text after STT into the SAME production ConversationManager.handle path.

Levels:
  L1 — text injection (this module; autonomous)
  L2 — simulated voice (`core/simulated_voice.py`, Piper→Whisper→CM)
  L3 — hardware mic — HARDWARE_UNVERIFIED

Does not invent a parallel Elian. Tools default to dry + ELIAN_PROTECT_USER_APPS.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

os.environ.setdefault("ELIAN_PROTECT_USER_APPS", "1")


def _argv_value(argv: List[str], flag: str) -> Optional[str]:
    if flag not in argv:
        return None
    idx = argv.index(flag)
    if idx + 1 >= len(argv):
        return None
    return argv[idx + 1]


def _print_struct(label: str, payload: Dict[str, Any]) -> None:
    parts = [f"[{label}]"]
    for key, value in payload.items():
        if value is None or value == "" or value == []:
            continue
        parts.append(f"{key}={value!r}")
    print(" ".join(parts))


def _build_dry_registry(*, ha_backend=None):
    from core.fake_ha_backend import HA_DEVICE_PROPS, SET_VALUE_PROPS, FakeHaBackend
    from core.risk import RiskLevel
    from core.tool_result import ToolResult
    from core.tools import Tool, ToolRegistry

    registry = ToolRegistry(confirm_medium=False)
    invoked: List[Dict[str, Any]] = []
    backend = ha_backend or FakeHaBackend()

    def _ok(name: str, message: str, data=None):
        def handler(args):
            payload = dict(args or {})
            invoked.append({"tool": name, "arguments": payload})
            body = data(payload) if callable(data) else (data if data is not None else payload)
            return ToolResult.ok(message, tool_name=name, data=body)

        return handler

    def _ha(name: str):
        def handler(args):
            payload = dict(args or {})
            invoked.append({"tool": name, "arguments": payload})
            return backend.handle(name, payload)

        return handler

    def web_data(args):
        q = str(args.get("query") or "").strip() or "temat"
        return {
            "query": q,
            "results": [
                {
                    "title": f"{q[:48]} — przegląd",
                    "url": "https://example.com/a",
                    "snippet": (
                        f"{q} to lokalny silnik lub narzędzie opisane w źródłach; "
                        "ułatwia szybkie wnioskowanie bez chmury."
                    ),
                    "source": "example.com",
                },
                {
                    "title": f"{q[:40]} — dokumentacja",
                    "url": "https://example.com/b",
                    "snippet": (
                        "Dokumentacja opisuje instalację, modele ONNX i typowe "
                        "scenariusze użycia."
                    ),
                    "source": "example.com",
                },
            ],
            "result_count": 2,
            "provider": "dry",
            "success": True,
        }

    specs = (
        ("open_app", "Uruchomiłem.", {"name": {"type": "string"}}, ["name"], None),
        ("close_app", "Zamknąłem.", {"name": {"type": "string"}}, ["name"], None),
        ("play_media", "Odtwarzam.", {"query": {"type": "string"}}, ["query"], None),
        ("open_website", "Otwieram.", {"url": {"type": "string"}, "target": {"type": "string"}}, [], None),
        ("web_search", "Znalazłem wyniki.", {"query": {"type": "string"}}, ["query"], web_data),
        ("fetch_web_page", "Pobrałem.", {"url": {"type": "string"}}, ["url"], None),
        ("volume_up", "Głośniej.", {}, [], None),
        ("volume_down", "Ciszej.", {}, [], None),
        ("get_volume", "Poziom głośności.", {}, [], lambda _a: {"level": 40.0, "scalar": 0.4, "mute": False}),
        ("get_now_playing", "Nic nie leci.", {}, [], lambda _a: {"playing": False}),
        # Computer dry stubs — test-owned only; never touch user desktop processes.
        ("type_text", "Wpisane.", {"text": {"type": "string"}, "app_name": {"type": "string"}}, ["text"], None),
        ("hotkey", "Skrót wysłany.", {"keys": {"type": "string"}, "app_name": {"type": "string"}}, ["keys"], None),
        ("select_all", "Zaznaczone.", {"app_name": {"type": "string"}}, [], None),
        ("list_windows", "Okna.", {}, [], lambda _a: {"windows": [{"title": "Notatnik", "app": "notatnik"}]}),
        ("get_active_window", "Aktywne okno.", {}, [], lambda _a: {"title": "Notatnik", "app": "notatnik"}),
        ("window_exists", "Jest.", {"name": {"type": "string"}, "query": {"type": "string"}}, [], lambda a: {"exists": True, "name": (a or {}).get("name") or ""}),
    )
    for name, msg, props, required, builder in specs:
        registry.register(
            Tool(
                name=name,
                description=name,
                handler=_ok(name, msg, builder),
                risk_level=RiskLevel.SAFE,
                parameters={"type": "object", "properties": props, "required": required},
            )
        )
    # Production-matching HA schemas (stateful fake — CREDENTIAL_UNAVAILABLE path).
    ha_tools = (
        ("turn_on_device", HA_DEVICE_PROPS),
        ("turn_off_device", HA_DEVICE_PROPS),
        ("set_device_value", SET_VALUE_PROPS),
        ("get_device_state", HA_DEVICE_PROPS),
    )
    for name, props in ha_tools:
        registry.register(
            Tool(
                name=name,
                description=name,
                handler=_ha(name),
                risk_level=RiskLevel.SAFE,
                parameters={"type": "object", "properties": props, "required": []},
            )
        )
    registry._dry_invoked = invoked  # type: ignore[attr-defined]
    registry._fake_ha = backend  # type: ignore[attr-defined]
    return registry


def _make_manager(*, live_tools: bool = False):
    from core.conversation_manager import ConversationManager
    from core.fake_ha_backend import reset_fake_ha_backend
    from core.model_client import reset_model_client, set_model_client
    from core.tools import reset_registry_for_tests
    from memory.manager import MemoryManager

    # Dry inject always uses stateful FakeHaBackend (no live HA writes).
    ha_backend = reset_fake_ha_backend()
    try:
        from Config import get_config_value, get_secret

        token = (
            get_secret("home_assistant_token", env_var="ELIAN_HA_TOKEN")
            or get_config_value("smart_home", "token", default="")
            or get_config_value("smart_home", "access_token", default="")
        )
        if not token:
            print(
                "[text_inject] HA token missing in secrets/env; "
                "dry FakeHaBackend light.vizzini loaded"
            )
        else:
            print(
                "[text_inject] dry FakeHaBackend seeded "
                "(live HA token present — tools still dry)"
            )
    except Exception as exc:
        print(f"[text_inject] fake HA seed note: {exc}")

    tmp = tempfile.TemporaryDirectory()
    mem = MemoryManager(Path(tmp.name) / "mem.json")
    if live_tools:
        from core.tools import get_registry

        registry = get_registry()
        model = None
    else:
        registry = _build_dry_registry(ha_backend=ha_backend)
        reset_registry_for_tests(registry)
        # Same assist model as jarvis_behavioral — never scripted web_search on HA goals.
        from core.jarvis_behavioral.harness import AssistFakeModel

        model = AssistFakeModel([])
        set_model_client(model)
    mgr = ConversationManager(
        registry=registry,
        model=model,
        memory_manager=mem,
        memory=[],
    )
    mgr.activate()
    # Keep tmp alive for session
    mgr._text_inject_tmp = tmp  # type: ignore[attr-defined]
    mgr._fake_ha = ha_backend  # type: ignore[attr-defined]
    return mgr


def _trace_result(mgr, result, *, barge_injected: bool = False) -> Dict[str, Any]:
    ug = getattr(mgr, "_user_goal", None)
    soft = str(getattr(ug, "soft_kind", "") or "") if ug else ""
    tools = []
    for obs in list(getattr(mgr, "last_observations", None) or [])[-6:]:
        if isinstance(obs, dict) and obs.get("tool"):
            tools.append(
                {
                    "tool": obs.get("tool"),
                    "success": obs.get("success"),
                    "message": str(obs.get("message") or "")[:120],
                }
            )
    env = getattr(mgr, "last_decision_envelope", None) or getattr(mgr, "_last_envelope", None)
    ha = getattr(mgr, "_fake_ha", None)
    ha_snap = None
    if ha is not None and hasattr(ha, "snapshot"):
        try:
            ha_snap = ha.snapshot("light.vizzini")
        except Exception:
            ha_snap = None
    return {
        "INPUT": None,  # filled by caller when printing
        "FINAL_RESPONSE": (getattr(result, "text", "") or "")[:240],
        "source": getattr(result, "source", ""),
        "text": (getattr(result, "text", "") or "")[:240],
        "should_speak": bool(getattr(result, "should_speak", False)),
        "active": bool(getattr(result, "active", False)),
        "ACTIVE_GOAL": (getattr(ug, "objective", "") or "")[:120] if ug else "",
        "soft_kind": soft,
        "UTTERANCE_CLASS": str(getattr(env, "utterance_class", "") or "") if env else "",
        "SEMANTIC": (
            {
                "mode": getattr(env, "mode", None),
                "intent": getattr(env, "intent", None),
                "confidence": getattr(env, "confidence", None),
            }
            if env
            else {}
        ),
        "TOOL": tools,
        "WORLDSTATE_HA": ha_snap,
        "barge_injected": barge_injected,
    }


def inject_barge_in(mgr) -> None:
    """Software barge-in event (not acoustic HARDWARE)."""
    try:
        if hasattr(mgr, "collab") and hasattr(mgr.collab, "note_barge_in"):
            mgr.collab.note_barge_in()
    except Exception:
        pass
    try:
        host = getattr(mgr, "proactive", None) or getattr(mgr, "_proactive", None)
        if host is not None and hasattr(host, "note_barge_in"):
            host.note_barge_in()
    except Exception:
        pass


def run_one_turn(
    mgr,
    text: str,
    *,
    debug: bool = True,
    barge_in: bool = False,
) -> Dict[str, Any]:
    if barge_in:
        inject_barge_in(mgr)
    if debug:
        _print_struct("INJECT", {"text": text, "barge_in": barge_in})
    result = mgr.handle(text)
    row = _trace_result(mgr, result, barge_injected=barge_in)
    row["INPUT"] = text
    if debug:
        _print_struct("RESULT", row)
    return row


def run_inject_text(
    text: str,
    *,
    live_tools: bool = False,
    debug: bool = True,
    barge_in: bool = False,
) -> int:
    from core.model_client import reset_model_client
    from core.tools import reset_registry_for_tests

    print("=== ELIAN TEXT INJECT (post-STT -> ConversationManager.handle) ===")
    print(f"protect_user_apps={os.environ.get('ELIAN_PROTECT_USER_APPS')}")
    print(f"live_tools={live_tools}")
    mgr = _make_manager(live_tools=live_tools)
    try:
        run_one_turn(mgr, text, debug=debug, barge_in=barge_in)
        return 0
    finally:
        try:
            mgr.deactivate(reason="text_inject_end")
        except Exception:
            pass
        reset_model_client()
        reset_registry_for_tests(None)
        tmp = getattr(mgr, "_text_inject_tmp", None)
        if tmp is not None:
            tmp.cleanup()


def run_text_input(*, live_tools: bool = False, debug: bool = True) -> int:
    from core.model_client import reset_model_client
    from core.tools import reset_registry_for_tests

    print("=== ELIAN TEXT REPL (post-STT) ===")
    print("Komendy: /quit  /barge  /end")
    print(f"protect_user_apps={os.environ.get('ELIAN_PROTECT_USER_APPS')} live_tools={live_tools}")
    mgr = _make_manager(live_tools=live_tools)
    barge_next = False
    try:
        while True:
            try:
                line = input("elian> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line.lower() in ("/quit", "/exit", "quit", "exit"):
                break
            if line.lower() == "/end":
                mgr.deactivate(reason="text_repl_end")
                mgr.activate()
                print("[session] restarted")
                continue
            if line.lower() == "/barge":
                barge_next = True
                print("[barge] next turn will inject barge-in event")
                continue
            run_one_turn(mgr, line, debug=debug, barge_in=barge_next)
            barge_next = False
        return 0
    finally:
        try:
            mgr.deactivate(reason="text_repl_end")
        except Exception:
            pass
        reset_model_client()
        reset_registry_for_tests(None)
        tmp = getattr(mgr, "_text_inject_tmp", None)
        if tmp is not None:
            tmp.cleanup()


def run_conversation_script(
    path: str,
    *,
    live_tools: bool = False,
    debug: bool = True,
) -> int:
    """JSON script: {\"turns\": [\"...\", {\"text\": \"...\", \"barge_in\": false}, ...]}"""
    from core.model_client import reset_model_client
    from core.tools import reset_registry_for_tests

    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    turns = data.get("turns") if isinstance(data, dict) else data
    if not isinstance(turns, list):
        print("conversation-script: expected list or {turns: [...]}")
        return 2

    print(f"=== ELIAN CONVERSATION SCRIPT: {path} ===")
    mgr = _make_manager(live_tools=live_tools)
    rows: List[Dict[str, Any]] = []
    try:
        for i, item in enumerate(turns):
            barge = False
            if isinstance(item, dict):
                text = str(item.get("text") or item.get("utterance") or "")
                barge = bool(item.get("barge_in") or item.get("barge"))
            else:
                text = str(item or "")
            if not text.strip():
                continue
            print(f"--- turn {i + 1} ---")
            rows.append(run_one_turn(mgr, text, debug=debug, barge_in=barge))
        out = Path("Data") / "diagnostics" / "text_inject_last_script.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"path": path, "turns": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"artifact={out}")
        return 0
    finally:
        try:
            mgr.deactivate(reason="conversation_script_end")
        except Exception:
            pass
        reset_model_client()
        reset_registry_for_tests(None)
        tmp = getattr(mgr, "_text_inject_tmp", None)
        if tmp is not None:
            tmp.cleanup()


def run_from_argv(argv: Optional[List[str]] = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    live = "--live-tools" in argv
    debug = "--quiet" not in argv
    if "--inject-text" in argv:
        text = _argv_value(argv, "--inject-text")
        if text is None:
            print("Usage: python main.py --inject-text \"...\"")
            return 2
        return run_inject_text(
            text,
            live_tools=live,
            debug=debug,
            barge_in="--barge-in" in argv,
        )
    if "--text-input" in argv:
        return run_text_input(live_tools=live, debug=debug)
    if "--conversation-script" in argv:
        path = _argv_value(argv, "--conversation-script")
        if not path:
            print("Usage: python main.py --conversation-script path.json")
            return 2
        return run_conversation_script(path, live_tools=live, debug=debug)
    print("No text-inject flag matched")
    return 2


if __name__ == "__main__":
    raise SystemExit(run_from_argv())
