# ==========================
# ELIAN — Jarvis Behavioral dry harness (P2)
# ==========================
"""ConversationManager + dry ToolRegistry. Never touches real Spotify/Chrome.

Respects app_lifecycle_guard / ELIAN_PROTECT_USER_APPS.
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional
from unittest.mock import patch

from core.app_lifecycle_guard import enable_protect_user_apps
from core.conversation_manager import ConversationManager, HandleResult
from core.model_client import FakeModelClient, reset_model_client, set_model_client
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from memory.manager import MemoryManager

from core.jarvis_behavioral.types import SIDE_EFFECT_TOOLS


_DEFAULT_LIGHT = {
    "entity_id": "light.desk",
    "state": "on",
    "attributes": {
        "friendly_name": "Lampa biurko",
        "supported_color_modes": ["brightness"],
        "brightness": 128,
    },
}


class DryBackend:
    """Configurable dry tool backends for inject modes."""

    def __init__(self) -> None:
        self.mode: str = "ok"  # ok|fail|timeout|partial|verify_fail|offline|no_change
        self.invoked: List[Dict[str, Any]] = []
        self.disabled: set[str] = set()
        self.partial_fail_tools: set[str] = set()
        self.volume_level: float = 40.0

    def reset_log(self) -> None:
        self.invoked.clear()

    def set_inject(self, mode: str) -> None:
        self.mode = str(mode or "ok").strip().lower() or "ok"


def _ok_data(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(args or {})
    if name in ("turn_on_device", "turn_off_device", "set_device_value"):
        data.setdefault("resolved_entity_id", args.get("entity_id") or "light.desk")
        data["ha_verify"] = {"verification": "SUCCESS"}
        data["ha_action"] = (
            "turn_off"
            if name == "turn_off_device"
            else ("turn_on" if name == "turn_on_device" else "adjust_brightness")
        )
    if name == "web_search":
        q = str(args.get("query") or "temat").strip() or "temat"
        data["results"] = [
            {
                "title": f"{q[:48]} — przegląd",
                "url": "https://example.com/1",
                "snippet": (
                    f"{q} to temat opisany w źródłach. "
                    "Krótki opis pozwala streścić wynik bez pustego tytułu."
                ),
            },
            {
                "title": f"{q[:40]} — dokumentacja",
                "url": "https://example.com/2",
                "snippet": "Uzupełniający kontekst z drugiego źródła o tym samym temacie.",
            },
        ]
        data["result_count"] = 2
    if name == "get_now_playing":
        data.update(
            {
                "title": "Billie Jean",
                "artist": "Michael Jackson",
                "playing": True,
            }
        )
    if name == "window_exists":
        data.update({"exists": True, "name": args.get("name") or args.get("query") or ""})
    if name == "get_volume":
        data.update({"level": 40.0, "scalar": 0.4, "mute": False})
    if name == "open_app":
        data.setdefault("name", args.get("name") or "")
        data["verified"] = True
        data["verification"] = "SUCCESS"
    if name in ("set_preference", "remember_fact"):
        data.setdefault("key", args.get("key") or "")
        data.setdefault("value", args.get("value") or "")
    if name == "get_home_summary":
        data["on"] = ["Lampa biurko"]
    if name == "get_situation_briefing":
        data["sources"] = ["home", "calendar"]
    if name == "get_device_state":
        data.setdefault("entity_id", args.get("entity_id") or "light.desk")
        data.setdefault("state", "on")
        data.setdefault("friendly_name", "Lampa biurko")
    return data


def build_dry_registry(backend: DryBackend) -> ToolRegistry:
    registry = ToolRegistry(confirm_medium=False)

    tool_names = sorted(
        SIDE_EFFECT_TOOLS
        | {
            "get_now_playing",
            "get_volume",
            "window_exists",
            "describe_screen",
            "list_reminders",
            "get_time",
            "get_weather",
            "get_home_summary",
            "get_situation_briefing",
            "get_device_state",
            "get_today_schedule",
            "hotkey",
            "select_all",
            "type_text",
        }
    )

    def _handler(name: str) -> Callable:
        def handler(args):
            args = dict(args or {})
            backend.invoked.append({"tool": name, "arguments": args})
            if name in backend.disabled:
                return ToolResult.fail(
                    "Narzędzie niedostępne.",
                    tool_name=name,
                    error_code="TOOL_UNAVAILABLE",
                    data={"availability": "UNAVAILABLE"},
                )
            mode = backend.mode
            if name in backend.partial_fail_tools:
                mode = "fail"
            if mode == "fail":
                return ToolResult.fail(
                    "Nie udało się.",
                    tool_name=name,
                    error_code="TOOL_FAILED",
                )
            if mode == "timeout":
                return ToolResult.fail(
                    "Timeout.",
                    tool_name=name,
                    error_code="TIMEOUT",
                    retryable=True,
                )
            if mode == "offline":
                return ToolResult.fail(
                    "Usługa offline.",
                    tool_name=name,
                    error_code="DEPENDENCY_OFFLINE",
                    data={"availability": "OFFLINE"},
                )
            if mode == "verify_fail":
                data = _ok_data(name, args)
                if name in ("turn_on_device", "turn_off_device", "set_device_value"):
                    data["ha_verify"] = {"verification": "FAILED"}
                return ToolResult.ok(
                    "Wysłano, bez potwierdzenia.",
                    tool_name=name,
                    data=data,
                )
            if mode == "no_change":
                data = _ok_data(name, args)
                data["state_changed"] = False
                data["ha_verify"] = {"verification": "NO_CHANGE"}
                return ToolResult.ok("Bez zmiany.", tool_name=name, data=data)
            if mode == "partial":
                # Caller marks some tools to fail; default ok.
                pass
            data = _ok_data(name, args)
            if name in ("set_preference", "remember_fact"):
                val = str(args.get("value") or "").strip()
                msg = f"Zapamiętałem: wolisz {val}." if val else "Zapamiętałem."
                return ToolResult.ok(msg, tool_name=name, data=data)
            if name == "get_home_summary":
                return ToolResult.ok(
                    "Włączone: Lampa biurko.",
                    tool_name=name,
                    data=data,
                )
            if name == "get_situation_briefing":
                return ToolResult.ok(
                    "Na jutro: spokojny dzień. W domu świeci Lampa biurko.",
                    tool_name=name,
                    data=data,
                )
            if name == "get_device_state":
                return ToolResult.ok(
                    "Lampa biurko jest włączona.",
                    tool_name=name,
                    data=data,
                )
            if name == "get_weather":
                return ToolResult.ok(
                    "Na zewnątrz około 18 stopni, bez opadów.",
                    tool_name=name,
                    data={**data, "temp_c": 18, "condition": "clear"},
                )
            if name == "get_today_schedule":
                return ToolResult.ok(
                    "Dziś nie mam wpisów w kalendarzu.",
                    tool_name=name,
                    data={**data, "events": []},
                )
            if name == "list_reminders":
                return ToolResult.ok(
                    "Nie masz aktywnych przypomnień.",
                    tool_name=name,
                    data={**data, "reminders": []},
                )
            if name == "get_time":
                return ToolResult.ok(
                    "Jest godzina 12:00.",
                    tool_name=name,
                    data=data,
                )
            if name == "get_volume":
                level = float(getattr(backend, "volume_level", 40.0) or 40.0)
                data = {**data, "level": level, "scalar": level / 100.0, "mute": False}
                return ToolResult.ok(
                    f"Głośność: {int(level)} procent.",
                    tool_name=name,
                    data=data,
                )
            if name == "volume_down":
                backend.volume_level = max(0.0, float(getattr(backend, "volume_level", 40.0)) - 10.0)
                data = {
                    **data,
                    "level": backend.volume_level,
                    "scalar": backend.volume_level / 100.0,
                }
                return ToolResult.ok("Ściszyłem.", tool_name=name, data=data)
            if name == "volume_up":
                backend.volume_level = min(100.0, float(getattr(backend, "volume_level", 40.0)) + 10.0)
                data = {
                    **data,
                    "level": backend.volume_level,
                    "scalar": backend.volume_level / 100.0,
                }
                return ToolResult.ok("Podgłośniłem.", tool_name=name, data=data)
            if name == "type_text":
                return ToolResult.ok("Wpisane.", tool_name=name, data=data)
            if name in ("hotkey", "select_all"):
                return ToolResult.ok("Zaznaczone.", tool_name=name, data=data)
            if name == "open_app":
                app = str(args.get("name") or data.get("name") or "").strip()
                if app:
                    label = app[:1].upper() + app[1:] if app else app
                    return ToolResult.ok(f"{label} otwarte.", tool_name=name, data=data)
                return ToolResult.ok("Aplikacja otwarta.", tool_name=name, data=data)
            return ToolResult.ok("ok", tool_name=name, data=data)

        return handler

    for name in tool_names:
        registry.register(
            Tool(
                name=name,
                description=name,
                handler=_handler(name),
                risk_level=RiskLevel.SAFE,
                parameters={"type": "object", "properties": {}, "required": []},
            )
        )
    registry._dry_backend = backend  # type: ignore[attr-defined]
    registry._dry_invoked = backend.invoked  # type: ignore[attr-defined]
    return registry


class AssistFakeModel(FakeModelClient):
    """Harness-only planner assist. Production semantic never sees this flag."""

    @staticmethod
    def _user_goal_blob(messages) -> str:
        """Inspect only user/goal text — never system prompt or planner instruction/examples."""
        import json as _json
        import re as _re

        parts: list[str] = []
        for m in messages or []:
            role = str((m or {}).get("role") or "").lower()
            if role in ("user", "human"):
                parts.append(str((m or {}).get("content") or ""))
        text = "\n".join(parts).strip()
        if not text and messages:
            text = str((messages[-1] or {}).get("content") or "")

        # Planner: instruction + JSON payload with "goal" / "raw_text".
        for chunk in reversed(parts or [text]):
            start = chunk.find("{")
            while start >= 0:
                try:
                    obj, _end = _json.JSONDecoder().raw_decode(chunk[start:])
                except Exception:
                    start = chunk.find("{", start + 1)
                    continue
                if isinstance(obj, dict) and (
                    obj.get("goal") is not None or obj.get("raw_text") is not None
                ):
                    raw = str(obj.get("raw_text") or "").strip()
                    goal = str(obj.get("goal") or "").strip()
                    return (raw or goal).casefold()
                start = chunk.find("{", start + 1)

        goal = _re.search(
            r"(?im)^(?:\[?agent\]?\s*)?(?:goal|cel|user_goal|u[zż]ytkownik)\s*[:=]\s*(.+)$",
            text,
        )
        if goal:
            return str(goal.group(1) or "").casefold()
        goal2 = _re.search(r"(?is)\bgoal\s*:\s*([^\n]+)", text)
        if goal2:
            return str(goal2.group(1) or "").casefold()
        # Last resort: last non-empty line only (avoid instruction + history blob).
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if lines:
            return lines[-1].casefold()
        return text.casefold()

    def chat(self, messages, *, temperature=0.2, format=None, model=None) -> str:
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "format": format,
                "model": model,
            }
        )
        if self._responses:
            item = self._responses.pop(0)
            if isinstance(item, dict):
                import json

                return json.dumps(item, ensure_ascii=False)
            return str(item)

        blob = self._user_goal_blob(messages)
        import json

        # Prefer final_answer for discussion / hypothetical cues in the USER goal only.
        if any(
            token in blob
            for token in (
                "hipotet",
                "gdybym",
                "co oznacza",
                "czy umiesz",
                "jak otworzyć",
                "nie otwieraj",
                "nie wyłączaj",
                "nie wylaczaj",
            )
        ):
            return json.dumps(
                {"type": "final_answer", "message": "To pytanie — nic nie robię."},
                ensure_ascii=False,
            )

        # Minimal tool suggestion when deterministic paths miss.
        open_m = None
        try:
            import re as _re

            open_m = _re.search(
                r"\b(?:odpal|otw[oó]rz|uruchom|open)\s+(?:mi\s+)?([a-z0-9][\w\- ]{0,40})",
                blob,
                _re.I,
            )
        except Exception:
            open_m = None
        if open_m and "coś" not in (open_m.group(0) or "") and "cos" not in (open_m.group(0) or ""):
            app = str(open_m.group(1) or "").strip()
            # Don't invent open_app for search-result referents.
            if any(
                tok in app.casefold()
                for tok in ("wynik", "link", "stron", "sensown", "pierwsz", "drug", "trzec")
            ):
                open_m = None
            else:
                app = app.split()[0].capitalize()
        if open_m:
            app = str(open_m.group(1) or "").strip().split()[0].capitalize()
            if any(t in blob for t in ("zamknij", "close", "zabij proces")):
                return json.dumps(
                    {"type": "tool_call", "tool": "close_app", "arguments": {"name": app}},
                    ensure_ascii=False,
                )
            return json.dumps(
                {"type": "tool_call", "tool": "open_app", "arguments": {"name": app}},
                ensure_ascii=False,
            )
        if any(t in blob for t in ("spotify", "chrome", "discord", "notatnik", "notepad")):
            app = "Spotify"
            for cand in ("Spotify", "Chrome", "Discord", "Notepad", "Notatnik"):
                if cand.casefold() in blob:
                    app = cand
                    break
            if any(t in blob for t in ("zamknij", "close")):
                return json.dumps(
                    {"type": "tool_call", "tool": "close_app", "arguments": {"name": app}},
                    ensure_ascii=False,
                )
            return json.dumps(
                {"type": "tool_call", "tool": "open_app", "arguments": {"name": app}},
                ensure_ascii=False,
            )
        if any(
            t in blob
            for t in ("ciszej", "przycisz", "zejdź z vol", "zejdz z vol", " vol", "ścisz", "scisz")
        ):
            return json.dumps(
                {"type": "tool_call", "tool": "volume_down", "arguments": {}},
                ensure_ascii=False,
            )
        if any(
            t in blob
            for t in (
                "łagodniej",
                "lagodniej",
                "spokojniejsz",
                "coś chill",
                "cos chill",
                "coś relaks",
            )
        ):
            return json.dumps(
                {
                    "type": "tool_call",
                    "tool": "play_media",
                    "arguments": {"query": "spokojna muzyka"},
                },
                ensure_ascii=False,
            )
        if any(t in blob for t in ("wyszukaj", "poszukaj", "szukaj", "internecie", "necie")):
            return json.dumps(
                {
                    "type": "tool_call",
                    "tool": "web_search",
                    "arguments": {"query": "zapytanie"},
                },
                ensure_ascii=False,
            )
        if any(t in blob for t in ("przypomnij", "reminder", "pingnij", "pingnac", "pingnąć")):
            return json.dumps(
                {
                    "type": "tool_call",
                    "tool": "create_reminder",
                    "arguments": {"text": "przypomnienie", "when": "za kwadrans"},
                },
                ensure_ascii=False,
            )
        if any(t in blob for t in ("puść", "pusc", "odtwórz", "odtworz", "michael", "piosenk")):
            return json.dumps(
                {
                    "type": "tool_call",
                    "tool": "play_media",
                    "arguments": {"query": "muzyka"},
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {"type": "final_answer", "message": "Jasne, słucham."},
            ensure_ascii=False,
        )


@contextmanager
def jarvis_session(
    *,
    inject: str = "ok",
    disabled_tools: Optional[List[str]] = None,
    partial_fail_tools: Optional[List[str]] = None,
    lights: Optional[List[Dict[str, Any]]] = None,
) -> Iterator[tuple[ConversationManager, DryBackend, List[Dict[str, Any]]]]:
    """Yield (manager, backend, tool_log) with user-app protection on."""
    enable_protect_user_apps(snapshot=True)
    reset_registry_for_tests()
    reset_model_client()
    try:
        from core.action_journal import reset_action_history

        reset_action_history()
    except Exception:
        pass

    backend = DryBackend()
    backend.set_inject(inject)
    if disabled_tools:
        backend.disabled = {str(x) for x in disabled_tools}
    if partial_fail_tools:
        backend.partial_fail_tools = {str(x) for x in partial_fail_tools}

    registry = build_dry_registry(backend)
    model = AssistFakeModel([])
    set_model_client(model)

    # Isolate HA / device catalog when possible.
    states = list(lights or [_DEFAULT_LIGHT])
    ha_patchers = []
    try:
        import HomeAssistant

        HomeAssistant.get_registry().load_states(states)
    except Exception:
        pass

    ambiguous = bool(lights and len(lights) >= 2)
    if ambiguous:
        from types import SimpleNamespace

        light_objs = [
            SimpleNamespace(
                domain="light",
                entity_id=str(s.get("entity_id") or ""),
                name=str((s.get("attributes") or {}).get("friendly_name") or s.get("entity_id") or ""),
            )
            for s in states
        ]
        try:
            ha_patchers.append(
                patch(
                    "core.device_catalog._light_entities_from_registry",
                    return_value=light_objs,
                )
            )
            ha_patchers.append(
                patch("core.device_catalog.resolve_alias", return_value=None)
            )
            ha_patchers.append(
                patch("core.device_catalog.default_fallback_entity", return_value="")
            )
        except Exception:
            pass

    # Patch real OS side effects that might leak past dry handlers.
    patch_targets = [
        ("ComputerControl.close_application", "ok"),
    ]
    # Optional targets — ignore if missing
    optional_patches = [
        ("ComputerControl.launch_application", (True, "ok")),
        ("core.builtin_tools.launch_application", (True, "ok")),
        ("Music.run", "ok"),
    ]
    ha_patchers = []
    for target, ret in patch_targets:
        try:
            p = patch(target, return_value=ret)
            p.start()
            ha_patchers.append(p)
        except Exception:
            pass
    for target, ret in optional_patches:
        try:
            p = patch(target, return_value=ret)
            p.start()
            ha_patchers.append(p)
        except Exception:
            pass

    tmp = tempfile.TemporaryDirectory()
    try:
        mem = MemoryManager(Path(tmp.name) / "mem.json")
        mgr = ConversationManager(
            registry=registry,
            model=model,
            memory_manager=mem,
            memory=[],
        )
        mgr.activate()
        yield mgr, backend, backend.invoked
    finally:
        try:
            mgr.deactivate(reason="jarvis_behavioral_end")  # type: ignore[name-defined]
        except Exception:
            pass
        for p in ha_patchers:
            try:
                p.stop()
            except Exception:
                pass
        reset_model_client()
        reset_registry_for_tests()
        tmp.cleanup()


def capture_trace(
    mgr: ConversationManager,
    backend: DryBackend,
    *,
    user: str,
    result: HandleResult,
    tools_before: int,
) -> Dict[str, Any]:
    tools = list(backend.invoked[tools_before:])
    env = getattr(mgr, "_decision_envelope", None)
    goal_store = getattr(mgr, "goal_store", None) or getattr(mgr, "_goal_store", None)
    active_goal = ""
    discourse: Dict[str, Any] = {}
    if goal_store is not None:
        cur = getattr(goal_store, "current", None)
        if cur is not None:
            active_goal = str(getattr(cur, "objective", "") or "")
        discourse = dict(getattr(goal_store, "discourse", {}) or {})
    uclass = getattr(mgr, "_last_utterance_class", None)
    uclass_s = str(getattr(uclass, "value", uclass) or "")
    return {
        "input": user,
        "response": str(result.text or ""),
        "source": str(result.source or ""),
        "semantic_mode": str(getattr(env, "mode", "") or ""),
        "semantic_intent": str(getattr(env, "intent", "") or ""),
        "utterance_class": uclass_s,
        "active_goal": active_goal,
        "discourse": discourse,
        "context": {},
        "worldstate": {},
        "memory_keys": [],
        "tools": tools,
        "observations": list(getattr(mgr, "last_observations", []) or [])[-6:],
        "capabilities": {},
        "plan": str(result.source or ""),
        "verify": "",
        "repair": "",
    }
