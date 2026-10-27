# ==========================
# ELIAN — ProcedureEngine 2.0
# ==========================
"""Declarative multi-step procedures. ToolRegistry only — no code exec.

Learned short phrase→single tool stays in learned_routines.
This module stores JSON steps under Data/procedures.json.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.jarvis_response import polish_spoken
from core.risk import RiskLevel
from core.tool_result import ToolResult, ToolStatus
from core.tools import Tool, ToolRegistry

_PATH = Path(__file__).resolve().parents[1] / "Data" / "procedures.json"
_MAX = 40
_TEST_STORE: Optional[List[Dict[str, Any]]] = None
_SLEEP_FN: Optional[Callable[[float], None]] = None
_MAX_INLINE_DELAY_SEC = 2.0

# Tools a procedure may name. Unknown names are rejected at compile/save.
ALLOWED_TOOLS = frozenset(
    {
        "turn_on_device",
        "turn_off_device",
        "set_device_value",
        "get_device_state",
        "activate_scene",
        "volume_up",
        "volume_down",
        "pause_media",
        "resume_media",
        "play_media",
        "next_track",
        "previous_track",
        "get_now_playing",
        "get_situation_briefing",
        "get_today_schedule",
        "get_calendar_events",
        "get_next_event",
        "get_weather",
        "list_reminders",
        "get_home_summary",
        "open_app",
        "close_app",
        "shutdown_pc",
        "sleep_pc",
        "restart_pc",
        "lock_pc",
        "undo_last_action",
        "create_reminder",
    }
)

FORBIDDEN_TOOLS = frozenset(
    {
        "remember_fact",
        "set_preference",
        "forget_fact",
        "type_text",
        "click_ui_element",
        "hotkey",
        "press_key",
        "press_keys",
        "send_message",
        "eval",
        "exec",
        "shell",
        "run_python",
        "os.system",
    }
)

DANGEROUS_TOOLS = frozenset(
    {
        "shutdown_pc",
        "sleep_pc",
        "restart_pc",
        "lock_pc",
    }
)

_ON_FAIL = frozenset({"stop", "continue", "rollback"})

_TEACH = re.compile(
    r"^(?:jak\s+(?:powiem|m[oó]wi[eę]|powiemy)|"
    r"od\s+teraz(?:\s+jak\s+powiem)?|"
    r"gdy\s+powiem)\s+"
    r"[«\"„]?(.+?)[»\"”]?\s+"
    r"(?:to\s+znaczy|znaczy|to)\s+"
    r"(.+)$",
    re.I,
)
_LIST = re.compile(
    r"^(?:jakie\s+mam\s+procedur|"
    r"lista\s+procedur|"
    r"poka[zż]\s+procedur|"
    r"wypisz\s+procedur)y?\b",
    re.I,
)
_RUN = re.compile(
    r"^(?:uruchom|odpal|zr[oó]b|wykonaj)\s+procedur[eęy]?\s+(.+)$",
    re.I,
)
_DISABLE = re.compile(
    r"^(?:wy[lł][aą]cz|zatrzymaj|dezaktywuj)\s+procedur[eęy]?\s+(.+)$",
    re.I,
)
_ENABLE = re.compile(
    r"^(?:w[lł][aą]cz|aktywuj)\s+procedur[eęy]?\s+(.+)$",
    re.I,
)
_DELETE = re.compile(
    r"^(?:usu[nń]|skasuj|wyrzu[cć])\s+procedur[eęy]?\s+(.+)$",
    re.I,
)
_EDIT = re.compile(
    r"^(?:edytuj|zmie[nń])\s+procedur[eęy]?\s+"
    r"[«\"„]?(.+?)[»\"”]?\s+"
    r"(?:to|:)\s+(.+)$",
    re.I,
)
_DELAY = re.compile(
    r"\bza\s+(\d{1,4})\s*(sekund(?:y|ę)?|minut(?:y|ę)?|s|m)\b",
    re.I,
)
_VOLUME_PCT = re.compile(
    r"\bg[lł]o[sś]no[sś][cć].{0,24}?(\d{1,3})\s*(?:%|procent)",
    re.I,
)
_LAMP_OFF = re.compile(
    r"\b(?:zga[sś]|wy[lł][aą]cz)\b.+\b(?:lamp|[sś]wiat[lł])",
    re.I,
)
_LAMP_ON = re.compile(
    r"\b(?:zapal|w[lł][aą]cz)\b.+\b(?:lamp|[sś]wiat[lł])",
    re.I,
)
_SHUTDOWN = re.compile(
    r"\b(?:wy[lł][aą]cz|zga[sś])\s+(?:komputer|pecet|pc|system)\b",
    re.I,
)
_SLEEP_PC = re.compile(r"\b(?:u[sś]pij)\s+(?:komputer|pc)\b", re.I)
_SCHEDULE = re.compile(
    r"\b(?:co\s+mam|jaki\s+mam\s+plan|plan\s+dnia|kalendarz)\b",
    re.I,
)
_PAUSE = re.compile(
    r"\b(?:zatrzymaj|pauza|wstrzymaj)\b.+\b(?:muzyk|spotify|utw[oó]r|media)?|"
    r"\bpauza\s+medi",
    re.I,
)
_IF = re.compile(r"^(?:je[sś]li|jeżeli)\s+(.+?)\s+to\s+(.+)$", re.I)
_CODEISH = re.compile(
    r"(?:__import__|os\.system|subprocess|eval\s*\(|exec\s*\(|"
    r"powershell|cmd\.exe|rm\s+-rf|format\s+c:)",
    re.I,
)

_RUN_LOCK = threading.Lock()
_RUN_STATE: Dict[str, Any] = {
    "active": False,
    "procedure_id": "",
    "trigger": "",
    "step_index": -1,
    "status": "idle",
    "cancel": False,
}


def reset_procedures_for_tests(rows: Optional[List[Dict[str, Any]]] = None) -> None:
    global _TEST_STORE
    _TEST_STORE = [] if rows is None else [dict(item) for item in rows]
    cancel_active_procedure()
    _reset_run_state()


def set_sleep_fn_for_tests(fn: Optional[Callable[[float], None]]) -> None:
    global _SLEEP_FN
    _SLEEP_FN = fn


def get_procedure_run_snapshot() -> Dict[str, Any]:
    """Small WorldState-readable API. Does not write WorldState."""
    with _RUN_LOCK:
        return {
            "active": bool(_RUN_STATE.get("active")),
            "procedure_id": str(_RUN_STATE.get("procedure_id") or ""),
            "trigger": str(_RUN_STATE.get("trigger") or ""),
            "step_index": int(_RUN_STATE.get("step_index") or -1),
            "status": str(_RUN_STATE.get("status") or "idle"),
            "cancel": bool(_RUN_STATE.get("cancel")),
        }


def cancel_active_procedure() -> bool:
    with _RUN_LOCK:
        if not _RUN_STATE.get("active"):
            _RUN_STATE["cancel"] = True
            return False
        _RUN_STATE["cancel"] = True
        _RUN_STATE["status"] = "cancelling"
        return True


def _reset_run_state() -> None:
    with _RUN_LOCK:
        _RUN_STATE.update(
            {
                "active": False,
                "procedure_id": "",
                "trigger": "",
                "step_index": -1,
                "status": "idle",
                "cancel": False,
            }
        )


def normalize_trigger(text: str) -> str:
    raw = str(text or "").strip().lower()
    raw = re.sub(r"[.!?…,;:]+$", "", raw)
    return re.sub(r"\s+", " ", raw).strip()


@dataclass
class ProcedureStep:
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    condition: Optional[Dict[str, Any]] = None
    delay_sec: float = 0.0
    timeout_sec: Optional[float] = None
    on_fail: str = "stop"
    confirm: bool = False
    verify: Optional[Dict[str, Any]] = None
    id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if not data.get("condition"):
            data["condition"] = None
        if not data.get("verify"):
            data["verify"] = None
        return data


@dataclass
class Procedure:
    id: str
    name: str
    trigger: str
    steps: List[ProcedureStep] = field(default_factory=list)
    enabled: bool = True
    builtin: bool = False
    procedure_version: int = 1
    updated_at: str = ""
    previous_version: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "trigger": self.trigger,
            "steps": [step.to_dict() for step in self.steps],
            "enabled": self.enabled,
            "builtin": self.builtin,
            "procedure_version": self.procedure_version,
            "updated_at": self.updated_at,
            "previous_version": self.previous_version,
        }


def _builtin_rows() -> List[Dict[str, Any]]:
    return [
        {
            "id": "morning",
            "name": "poranna",
            "trigger": "uruchom poranną",
            "enabled": True,
            "builtin": True,
            "procedure_version": 1,
            "updated_at": "",
            "previous_version": None,
            "steps": [
                {
                    "id": "briefing",
                    "tool": "get_situation_briefing",
                    "args": {},
                    "on_fail": "continue",
                }
            ],
        },
        {
            "id": "sleep",
            "name": "sen",
            "trigger": "idę spać",
            "enabled": True,
            "builtin": True,
            "procedure_version": 1,
            "updated_at": "",
            "previous_version": None,
            "steps": [
                {
                    "id": "pause",
                    "tool": "pause_media",
                    "args": {},
                    "on_fail": "continue",
                }
            ],
        },
        {
            "id": "gaming",
            "name": "gaming",
            "trigger": "ogarnij komputer do grania",
            "enabled": True,
            "builtin": True,
            "procedure_version": 1,
            "updated_at": "",
            "previous_version": None,
            "steps": [
                {
                    "id": "open_steam",
                    "tool": "open_app",
                    "args": {"name": "Steam"},
                    "on_fail": "continue",
                }
            ],
        },
    ]


def _load() -> List[Dict[str, Any]]:
    if _TEST_STORE is not None:
        return [dict(item) for item in _TEST_STORE]
    try:
        raw = json.loads(_PATH.read_text(encoding="utf-8"))
        rows = raw.get("procedures") if isinstance(raw, dict) else raw
        return [dict(item) for item in (rows or []) if isinstance(item, dict)]
    except Exception:
        return []


def _save(rows: List[Dict[str, Any]]) -> bool:
    global _TEST_STORE
    trimmed = rows[-_MAX:]
    if _TEST_STORE is not None:
        _TEST_STORE = trimmed
        return True
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(
            json.dumps({"version": 2, "procedures": trimmed}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception:
        return False


def _row_to_procedure(row: Dict[str, Any]) -> Optional[Procedure]:
    if not isinstance(row, dict):
        return None
    pid = str(row.get("id") or "").strip()
    trigger = normalize_trigger(str(row.get("trigger") or pid))
    if not pid or not trigger:
        return None
    steps: List[ProcedureStep] = []
    raw_steps = row.get("steps")
    if not isinstance(raw_steps, list):
        return None
    for index, item in enumerate(raw_steps):
        step = _parse_step(item, index)
        if step is None:
            return None
        steps.append(step)
    on_fail = str(row.get("on_fail") or "")
    del on_fail
    return Procedure(
        id=pid,
        name=str(row.get("name") or trigger),
        trigger=trigger,
        steps=steps,
        enabled=bool(row.get("enabled", True)),
        builtin=bool(row.get("builtin")),
        procedure_version=int(row.get("procedure_version") or 1),
        updated_at=str(row.get("updated_at") or ""),
        previous_version=row.get("previous_version") if isinstance(row.get("previous_version"), dict) else None,
    )


def _parse_step(item: Any, index: int) -> Optional[ProcedureStep]:
    if not isinstance(item, dict):
        return None
    tool = str(item.get("tool") or "").strip()
    if not tool or tool in FORBIDDEN_TOOLS:
        return None
    on_fail = str(item.get("on_fail") or "stop").strip().lower()
    if on_fail not in _ON_FAIL:
        on_fail = "stop"
    delay = item.get("delay_sec")
    try:
        delay_sec = float(delay or 0.0)
    except (TypeError, ValueError):
        return None
    timeout = item.get("timeout_sec")
    timeout_sec: Optional[float]
    try:
        timeout_sec = float(timeout) if timeout not in (None, "") else None
    except (TypeError, ValueError):
        return None
    args = item.get("args") if isinstance(item.get("args"), dict) else {}
    cond = item.get("condition") if isinstance(item.get("condition"), dict) else None
    verify = item.get("verify") if isinstance(item.get("verify"), dict) else None
    return ProcedureStep(
        id=str(item.get("id") or f"s{index + 1}"),
        tool=tool,
        args=dict(args),
        condition=dict(cond) if cond else None,
        delay_sec=max(0.0, delay_sec),
        timeout_sec=timeout_sec,
        on_fail=on_fail,
        confirm=bool(item.get("confirm")) or tool in DANGEROUS_TOOLS,
        verify=dict(verify) if verify else None,
    )


def list_procedures(*, include_disabled: bool = True) -> List[Procedure]:
    out: List[Procedure] = []
    user_ids: set[str] = set()
    user_triggers: set[str] = set()
    for row in _load():
        if bool(row.get("deleted")):
            user_ids.add(str(row.get("id") or ""))
            user_triggers.add(normalize_trigger(str(row.get("trigger") or "")))
            continue
        proc = _row_to_procedure(row)
        if proc is None:
            continue
        user_ids.add(proc.id)
        user_triggers.add(proc.trigger)
        if not include_disabled and not proc.enabled:
            continue
        out.append(proc)
    # Virtual builtins only when tests are not isolating the store, or when
    # the isolated store did not override that id/trigger.
    use_builtins = _TEST_STORE is None
    if use_builtins:
        for row in _builtin_rows():
            proc = _row_to_procedure(row)
            if proc is None:
                continue
            if proc.id in user_ids or proc.trigger in user_triggers:
                continue
            if not include_disabled and not proc.enabled:
                continue
            out.append(proc)
    return out


def get_procedure(procedure_id: str) -> Optional[Procedure]:
    key = str(procedure_id or "").strip().lower()
    if not key:
        return None
    for proc in list_procedures(include_disabled=True):
        if proc.id.lower() == key or proc.trigger == normalize_trigger(key):
            return proc
    return None


def find_by_trigger(text: str, *, enabled_only: bool = True) -> Optional[Procedure]:
    key = normalize_trigger(text)
    if not key:
        return None
    hits = [
        proc
        for proc in list_procedures(include_disabled=not enabled_only)
        if proc.trigger == key or proc.id.lower() == key
    ]
    if enabled_only:
        hits = [proc for proc in hits if proc.enabled]
    if not hits:
        # Builtin sleep natural phrases (not morning/wstałem — FastPath briefing owns those).
        try:
            from core.procedure_contract import classify_procedure

            classified = classify_procedure(text)
        except Exception:
            classified = ""
        if classified == "sleep":
            hits = [
                proc
                for proc in list_procedures(include_disabled=not enabled_only)
                if proc.id == "sleep" and (proc.enabled or not enabled_only)
            ]
        elif classified and classified not in ("morning",):
            hits = [
                proc
                for proc in list_procedures(include_disabled=not enabled_only)
                if proc.id == classified and (proc.enabled or not enabled_only)
            ]
    return hits[-1] if hits else None


def looks_procedure_teach(text: str) -> bool:
    return bool(_TEACH.match(str(text or "").strip()))


def parse_teach(text: str) -> Optional[tuple[str, str]]:
    match = _TEACH.match(str(text or "").strip())
    if not match:
        return None
    src = normalize_trigger(match.group(1))
    dest = str(match.group(2) or "").strip().rstrip(".!?")
    if not src or not dest or src == normalize_trigger(dest):
        return None
    return src, dest


def _clause_count(text: str) -> int:
    raw = str(text or "").strip()
    if not raw:
        return 0
    try:
        from CommandParser import looks_multi_command, _split_clauses

        if looks_multi_command(raw):
            return max(2, len(_split_clauses(raw)))
        return len(_split_clauses(raw)) or 1
    except Exception:
        verbs = len(
            re.findall(
                r"\b(?:zga[sś]|ustaw|powiedz|wy[lł][aą]cz|w[lł][aą]cz|otw[oó]rz|"
                r"pauza|uruchom|odpal)\b",
                raw,
                re.I,
            )
        )
        return verbs


def looks_procedure_definition(text: str) -> bool:
    parsed = parse_teach(text)
    if parsed is None:
        return False
    _, dest = parsed
    if _CODEISH.search(dest):
        return True
    if _clause_count(dest) >= 2:
        return True
    steps, _errors = compile_instruction(dest)
    return len(steps) >= 2


def parse_manage_utterance(text: str) -> Optional[tuple[str, str]]:
    raw = str(text or "").strip()
    if _LIST.search(raw):
        return "list", ""
    match = _EDIT.match(raw)
    if match:
        return "edit", f"{match.group(1).strip()}|||{match.group(2).strip()}"
    match = _RUN.match(raw)
    if match:
        return "run", match.group(1).strip()
    match = _ENABLE.match(raw)
    if match:
        return "enable", match.group(1).strip()
    match = _DISABLE.match(raw)
    if match:
        return "disable", match.group(1).strip()
    match = _DELETE.match(raw)
    if match:
        return "delete", match.group(1).strip()
    return None


def _split_instruction(text: str) -> List[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    parts: List[str] = []
    try:
        from CommandParser import _split_clauses

        parts = [str(p).strip() for p in _split_clauses(raw) if str(p).strip()]
    except Exception:
        parts = []
    if not parts:
        parts = [raw]
    extra = re.compile(
        r"\s+(?=(?:"
        r"powiedz\s+co\s+mam|"
        r"zga[sś]\s+|"
        r"ustaw\s+g[lł]o[sś]|"
        r"wy[lł][aą]cz\s+komputer|"
        r"wylacz\s+komputer"
        r"))",
        re.I,
    )
    out: List[str] = []
    for part in parts:
        bits = [b.strip(" ,.") for b in extra.split(part) if b.strip(" ,.")]
        out.extend(bits or [part])
    return out


def _extract_delay(text: str) -> tuple[str, float]:
    match = _DELAY.search(text)
    if not match:
        return text, 0.0
    amount = int(match.group(1))
    unit = str(match.group(2) or "").lower()
    seconds = float(amount)
    if unit.startswith("min") or unit == "m":
        seconds = float(amount) * 60.0
    cleaned = (_DELAY.sub("", text)).strip(" ,.")
    return cleaned, seconds


def _compile_clause(clause: str, registry: Optional[ToolRegistry]) -> Optional[ProcedureStep]:
    raw = str(clause or "").strip()
    if not raw:
        return None
    condition = None
    if_match = _IF.match(raw)
    if if_match:
        cond_text = if_match.group(1).strip()
        raw = if_match.group(2).strip()
        condition = {
            "kind": "tool_success",
            "tool": "get_device_state",
            "args": {"target": cond_text, "utterance": cond_text},
            "expect": {"success": True},
        }
    raw, delay_sec = _extract_delay(raw)
    if _CODEISH.search(raw):
        return None

    tool = ""
    args: Dict[str, Any] = {}
    on_fail = "stop"
    confirm = False
    verify = None

    vol = _VOLUME_PCT.search(raw)
    if vol and re.search(r"g[lł]o[sś]n", raw, re.I):
        pct = max(0, min(100, int(vol.group(1))))
        tool = "set_device_value"
        args = {"action": "media_volume", "value": pct, "utterance": raw}
    elif _LAMP_OFF.search(raw) or (
        re.search(r"\b(?:zga[sś]|wy[lł][aą]cz)\b", raw, re.I)
        and re.search(r"\b(?:lamp|[sś]wiat[lł])", raw, re.I)
    ):
        tool = "turn_off_device"
        args = {"target": "lampa", "utterance": raw}
    elif _LAMP_ON.search(raw):
        tool = "turn_on_device"
        args = {"target": "lampa", "utterance": raw}
    elif _SHUTDOWN.search(raw):
        tool = "shutdown_pc"
        args = {}
        confirm = True
    elif _SLEEP_PC.search(raw):
        tool = "sleep_pc"
        args = {}
        confirm = True
    elif _SCHEDULE.search(raw) or re.search(r"\bco\s+mam\s+jutro\b", raw, re.I):
        if re.search(r"\bjutro\b", raw, re.I):
            tool = "get_calendar_events"
            args = {"day": "jutro"}
        else:
            tool = "get_today_schedule"
            args = {}
        on_fail = "continue"
    elif re.search(r"\b(?:briefing|podsumuj(?:\s+sytuacj\w*)?|co\s+mnie\s+czeka)\b", raw, re.I):
        tool = "get_situation_briefing"
        args = {}
        on_fail = "continue"
    elif _PAUSE.search(raw) or re.search(r"\bpauz", raw, re.I):
        tool = "pause_media"
        args = {}
        on_fail = "continue"
    elif re.search(r"\b(?:g[lł]o[sś]niej)\b", raw, re.I):
        tool = "volume_up"
        args = {}
    elif re.search(r"\b(?:ciszej|[sś]cisz)\b", raw, re.I):
        tool = "volume_down"
        args = {}

    if not tool:
        try:
            from CommandParser import parse_commands
            from core.fast_path import _tool_call_for_command

            parsed = parse_commands(raw)
            if len(parsed) == 1:
                name, mapped = _tool_call_for_command(parsed[0])
                if name and isinstance(mapped, dict):
                    tool = name
                    args = dict(mapped)
        except Exception:
            pass

    if not tool:
        return None
    if tool in FORBIDDEN_TOOLS or tool not in ALLOWED_TOOLS:
        return None
    if registry is not None and not registry.has(tool):
        # Allow compile against a dry registry that omitted optional tools
        # only when the name is on the allow-list; execution still checks.
        pass
    if tool in DANGEROUS_TOOLS:
        confirm = True
    return ProcedureStep(
        tool=tool,
        args=args,
        condition=condition,
        delay_sec=delay_sec,
        on_fail=on_fail,
        confirm=confirm,
        verify=verify,
    )


def compile_instruction(
    text: str,
    registry: Optional[ToolRegistry] = None,
) -> tuple[List[ProcedureStep], List[str]]:
    errors: List[str] = []
    if _CODEISH.search(str(text or "")):
        return [], ["unsafe_code"]
    steps: List[ProcedureStep] = []
    for clause in _split_instruction(text):
        cleaned = str(clause or "").strip(" ,.")
        if not cleaned or cleaned.lower() in {"i", "oraz", "a"}:
            continue
        step = _compile_clause(cleaned, registry)
        if step is None:
            errors.append(clause[:80] or "unknown_step")
            continue
        if step.tool not in ALLOWED_TOOLS or step.tool in FORBIDDEN_TOOLS:
            errors.append(step.tool)
            continue
        steps.append(step)
    if not steps:
        errors = errors or ["empty"]
    return steps, errors


def _new_id(trigger: str) -> str:
    key = normalize_trigger(trigger)
    slug = re.sub(r"[^a-z0-9ąćęłńóśźż]+", "_", key, flags=re.I).strip("_")
    slug = slug[:40] or "proc"
    existing = {p.id for p in list_procedures(include_disabled=True)}
    if slug not in existing:
        return slug
    return f"{slug}_{uuid.uuid4().hex[:6]}"


def _registry_has(registry: Optional[ToolRegistry], name: str) -> bool:
    if registry is None:
        return True
    has = getattr(registry, "has", None)
    if callable(has):
        try:
            return bool(has(name))
        except Exception:
            return True
    get = getattr(registry, "get", None)
    if callable(get):
        try:
            return get(name) is not None
        except Exception:
            return True
    return True


def validate_steps_for_registry(
    steps: List[ProcedureStep],
    registry: Optional[ToolRegistry],
) -> Optional[str]:
    for step in steps:
        if step.tool in FORBIDDEN_TOOLS or step.tool not in ALLOWED_TOOLS:
            return step.tool
        if registry is not None and not _registry_has(registry, step.tool):
            return step.tool
    return None


def save_procedure(
    *,
    trigger: str,
    steps: List[ProcedureStep],
    name: str = "",
    procedure_id: str = "",
    replace: bool = False,
    registry: Optional[ToolRegistry] = None,
) -> ToolResult:
    trig = normalize_trigger(trigger)
    if not trig or len(trig) < 2:
        return ToolResult.fail("Nie wiem, jakie hasło ma ta procedura.", tool_name="procedure_save")
    if not steps:
        return ToolResult.fail("Nie umiem złożyć kroków tej procedury.", tool_name="procedure_save")
    bad = validate_steps_for_registry(steps, registry)
    if bad:
        return ToolResult.fail(
            "Tego nie zapiszę — nieznane lub niedozwolone narzędzie.",
            tool_name="procedure_save",
            error="unsafe_or_unknown_tool",
            data={"tool": bad},
        )
    existing = find_by_trigger(trig, enabled_only=False)
    if existing is not None and not replace:
        return ToolResult.needs_confirmation(
            polish_spoken(
                f"Procedura «{existing.trigger}» już istnieje. Mam ją nadpisać?"
            )
            or f"Procedura «{existing.trigger}» już istnieje. Mam ją nadpisać?",
            tool_name="procedure_save",
            data={
                "conflict": True,
                "existing_id": existing.id,
                "trigger": trig,
                "arguments": {
                    "trigger": trig,
                    "name": name or trig,
                    "replace": True,
                    "steps": [step.to_dict() for step in steps],
                    "procedure_id": existing.id,
                },
            },
        )
    rows = _load()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload_steps = [step.to_dict() for step in steps]
    if existing is not None and replace:
        updated = False
        for row in rows:
            if str(row.get("id") or "") == existing.id or normalize_trigger(
                str(row.get("trigger") or "")
            ) == trig:
                previous = dict(row)
                previous.pop("previous_version", None)
                row["name"] = name or existing.name or trig
                row["trigger"] = trig
                row["steps"] = payload_steps
                row["enabled"] = True
                row["builtin"] = False
                row["procedure_version"] = int(row.get("procedure_version") or 1) + 1
                row["updated_at"] = now
                row["previous_version"] = previous
                updated = True
                pid = str(row.get("id") or existing.id)
                break
        if not updated:
            pid = existing.id
            rows.append(
                {
                    "id": pid,
                    "name": name or trig,
                    "trigger": trig,
                    "steps": payload_steps,
                    "enabled": True,
                    "builtin": False,
                    "procedure_version": int(existing.procedure_version or 1) + 1,
                    "updated_at": now,
                    "previous_version": existing.to_dict(),
                }
            )
        if not _save(rows):
            return ToolResult.fail("Nie udało się zapisać procedury.", tool_name="procedure_save")
        spoken = polish_spoken(f"Nadpisałem procedurę {trig}.") or f"Nadpisałem procedurę {trig}."
        return ToolResult.ok(spoken, tool_name="procedure_save", data={"procedure_id": pid, "replaced": True})

    pid = str(procedure_id or "").strip() or _new_id(trig)
    rows.append(
        {
            "id": pid,
            "name": name or trig,
            "trigger": trig,
            "steps": payload_steps,
            "enabled": True,
            "builtin": False,
            "procedure_version": 1,
            "updated_at": now,
            "previous_version": None,
        }
    )
    if not _save(rows):
        return ToolResult.fail("Nie udało się zapisać procedury.", tool_name="procedure_save")
    spoken = polish_spoken(f"Zapamiętałem procedurę {trig}.") or f"Zapamiętałem procedurę {trig}."
    return ToolResult.ok(spoken, tool_name="procedure_save", data={"procedure_id": pid})


def define_from_teach(
    src: str,
    dest: str,
    *,
    replace: bool = False,
    registry: Optional[ToolRegistry] = None,
) -> ToolResult:
    steps, errors = compile_instruction(dest, registry)
    if errors and not steps:
        return ToolResult.fail(
            "Tego nie zapiszę — nieznane lub niebezpieczne polecenie.",
            tool_name="procedure_save",
            error="compile_failed",
            data={"errors": errors},
        )
    if errors:
        return ToolResult.fail(
            "Nie wszystkie kroki umiem zapisać.",
            tool_name="procedure_save",
            error="partial_compile",
            data={"errors": errors},
        )
    return save_procedure(trigger=src, steps=steps, name=src, replace=replace, registry=registry)


def list_spoken() -> str:
    procs = [p for p in list_procedures(include_disabled=True) if p.steps]
    if not procs:
        return "Nie mam jeszcze procedur."
    names = []
    for proc in procs:
        label = proc.trigger or proc.name
        if not proc.enabled:
            label = f"{label} (wyłączona)"
        names.append(label)
    if len(names) == 1:
        text = f"Masz procedurę {names[0]}."
    else:
        text = "Masz procedury: " + ", ".join(names) + "."
    return polish_spoken(text) or text


def set_enabled(selector: str, enabled: bool) -> ToolResult:
    proc = get_procedure(selector) or find_by_trigger(selector, enabled_only=False)
    if proc is None:
        return ToolResult.fail("Nie znam takiej procedury.", tool_name="procedure_disable")
    rows = _load()
    found = False
    for row in rows:
        if str(row.get("id") or "") == proc.id or normalize_trigger(str(row.get("trigger") or "")) == proc.trigger:
            row["enabled"] = bool(enabled)
            row["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            found = True
            break
    if not found:
        row = proc.to_dict()
        row["enabled"] = bool(enabled)
        row["builtin"] = False
        rows.append(row)
    if not _save(rows):
        return ToolResult.fail("Nie udało się zapisać procedury.", tool_name="procedure_disable")
    if enabled:
        text = f"Włączam procedurę {proc.trigger}."
    else:
        text = f"Wyłączam procedurę {proc.trigger}."
    return ToolResult.ok(polish_spoken(text) or text, tool_name="procedure_disable", data={"id": proc.id})


def delete_procedure(selector: str) -> ToolResult:
    proc = get_procedure(selector) or find_by_trigger(selector, enabled_only=False)
    if proc is None:
        return ToolResult.fail("Nie znam takiej procedury.", tool_name="procedure_delete")
    rows = [row for row in _load() if str(row.get("id") or "") != proc.id
            and normalize_trigger(str(row.get("trigger") or "")) != proc.trigger]
    if proc.builtin and not any(str(r.get("id") or "") == proc.id for r in _load()):
        # Disabling a virtual builtin: persist an empty disabled tombstone.
        rows.append(
            {
                "id": proc.id,
                "name": proc.name,
                "trigger": proc.trigger,
                "steps": [s.to_dict() for s in proc.steps],
                "enabled": False,
                "builtin": True,
                "deleted": True,
                "procedure_version": proc.procedure_version,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        if not _save(rows):
            return ToolResult.fail("Nie udało się usunąć procedury.", tool_name="procedure_delete")
    else:
        if not _save(rows):
            return ToolResult.fail("Nie udało się usunąć procedury.", tool_name="procedure_delete")
    text = f"Usunąłem procedurę {proc.trigger}."
    return ToolResult.ok(polish_spoken(text) or text, tool_name="procedure_delete", data={"id": proc.id})


def _sleep(seconds: float, cancel_check: Optional[Callable[[], bool]]) -> bool:
    if seconds <= 0:
        return True
    if _SLEEP_FN is not None:
        _SLEEP_FN(seconds)
        return not (cancel_check and cancel_check())
    inline = min(float(seconds), _MAX_INLINE_DELAY_SEC)
    end = time.monotonic() + inline
    while time.monotonic() < end:
        if cancel_check and cancel_check():
            return False
        time.sleep(0.05)
    return not (cancel_check and cancel_check())


def _expect_ok(result: ToolResult, expect: Optional[Dict[str, Any]]) -> bool:
    if expect is None:
        return bool(result.success)
    if "success" in expect and bool(result.success) != bool(expect.get("success")):
        return False
    path = str(expect.get("path") or "")
    if path:
        data = dict(result.data or {})
        value = data.get(path)
        if "equals" in expect and value != expect.get("equals"):
            return False
    return True


def _condition_met(
    step: ProcedureStep,
    registry: ToolRegistry,
    cancel_check: Optional[Callable[[], bool]],
) -> bool:
    cond = step.condition
    if not cond:
        return True
    kind = str(cond.get("kind") or "tool_success")
    if kind in ("always", "true"):
        return True
    if kind in ("never", "false"):
        return False
    tool = str(cond.get("tool") or "")
    if not tool or not _registry_has(registry, tool):
        return False
    if cancel_check and cancel_check():
        return False
    result = registry.invoke(tool, dict(cond.get("args") or {}), confirmed=True)
    return _expect_ok(result, cond.get("expect") if isinstance(cond.get("expect"), dict) else {"success": True})


def _procedure_needs_confirm(proc: Procedure, registry: Optional[ToolRegistry]) -> bool:
    for step in proc.steps:
        if step.confirm or step.tool in DANGEROUS_TOOLS:
            return True
        if registry is not None:
            get = getattr(registry, "get", None)
            tool = get(step.tool) if callable(get) else None
            if tool is not None and getattr(tool, "risk_level", None) in (RiskLevel.DANGEROUS,):
                return True
            confirm_medium = bool(getattr(registry, "confirm_medium", False))
            if (
                tool is not None
                and getattr(tool, "risk_level", None) is RiskLevel.MEDIUM
                and confirm_medium
            ):
                return True
    return False


def _rollback(registry: ToolRegistry) -> None:
    try:
        registry.invoke("undo_last_action", {}, confirmed=True)
    except Exception:
        pass


def execute_procedure(
    proc: Procedure,
    *,
    registry: Optional[ToolRegistry] = None,
    confirmed: bool = False,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> ToolResult:
    from core.tools import get_registry

    if not proc.enabled:
        return ToolResult.fail(
            f"Procedura {proc.trigger} jest wyłączona.",
            tool_name="procedure_run",
        )
    if not proc.steps:
        return ToolResult.fail("Ta procedura nie ma kroków.", tool_name="procedure_run")
    reg = registry or get_registry()
    bad = validate_steps_for_registry(proc.steps, reg)
    if bad:
        return ToolResult.fail(
            "Ta procedura jest uszkodzona albo woła nieznane narzędzie.",
            tool_name="procedure_run",
            error="unsafe_or_unknown_tool",
            data={"tool": bad},
        )
    if _procedure_needs_confirm(proc, reg) and not confirmed:
        return ToolResult.needs_confirmation(
            polish_spoken(
                f"Procedura «{proc.trigger}» ma ryzykowne kroki. Uruchomić?"
            )
            or f"Procedura «{proc.trigger}» ma ryzykowne kroki. Uruchomić?",
            tool_name="procedure_run",
            data={
                "arguments": {"procedure_id": proc.id, "trigger": proc.trigger},
                "risk_level": RiskLevel.DANGEROUS.value,
            },
        )

    def _cancelled() -> bool:
        if cancel_check and cancel_check():
            return True
        with _RUN_LOCK:
            return bool(_RUN_STATE.get("cancel"))

    with _RUN_LOCK:
        _RUN_STATE.update(
            {
                "active": True,
                "procedure_id": proc.id,
                "trigger": proc.trigger,
                "step_index": 0,
                "status": "running",
                "cancel": False,
            }
        )

    spoken: List[str] = []
    failed: List[str] = []
    completed = 0
    try:
        from core.action_firewall import allow_tool
        from core.semantic_executive import DecisionEnvelope, DecisionMode

        envelope = DecisionEnvelope(
            mode=DecisionMode.ACT.value,
            intent="procedure_run",
            user_goal=proc.trigger,
            confidence=0.95,
            needs_tools=True,
            allowed_domains=["SMART_HOME", "SYSTEM", "MEDIA", "APP", "COMPUTER"],
        )
        for index, step in enumerate(proc.steps):
            with _RUN_LOCK:
                _RUN_STATE["step_index"] = index
            if _cancelled():
                _reset_run_state()
                text = "Anulowano."
                return ToolResult.ok(
                    text,
                    tool_name="procedure_run",
                    data={"cancelled": True, "completed": completed, "procedure_id": proc.id},
                )
            if not _condition_met(step, reg, _cancelled):
                continue
            if not _sleep(step.delay_sec, _cancelled):
                _reset_run_state()
                return ToolResult.ok(
                    "Anulowano.",
                    tool_name="procedure_run",
                    data={"cancelled": True, "completed": completed, "procedure_id": proc.id},
                )
            allowed, reason = True, "ok"
            try:
                allowed, reason = allow_tool(envelope, step.tool, proc.trigger)
            except Exception:
                allowed = True
            if not allowed:
                failed.append(step.tool)
                if step.on_fail == "continue":
                    continue
                if step.on_fail == "rollback":
                    _rollback(reg)
                msg = polish_spoken("Ten krok jest zablokowany.") or "Ten krok jest zablokowany."
                return ToolResult.fail(
                    msg,
                    tool_name="procedure_run",
                    error=reason or "firewall",
                    data={"failed_tool": step.tool, "completed": completed},
                )
            invoke_confirmed = bool(confirmed or not step.confirm)
            if step.tool in DANGEROUS_TOOLS:
                invoke_confirmed = bool(confirmed)
            result = reg.invoke(
                step.tool,
                dict(step.args or {}),
                confirmed=invoke_confirmed,
            )
            if result.status == ToolStatus.NEEDS_CONFIRMATION:
                _reset_run_state()
                return result
            if step.verify:
                verify_tool = str(step.verify.get("tool") or "")
                if verify_tool and _registry_has(reg, verify_tool):
                    vres = reg.invoke(
                        verify_tool,
                        dict(step.verify.get("args") or {}),
                        confirmed=True,
                    )
                    if not _expect_ok(
                        vres,
                        step.verify.get("expect") if isinstance(step.verify.get("expect"), dict) else {"success": True},
                    ):
                        failed.append(step.tool)
                        if step.on_fail == "rollback":
                            _rollback(reg)
                        if step.on_fail != "continue":
                            text = polish_spoken("Nie potwierdziłem skutku kroku.") or "Nie potwierdziłem skutku kroku."
                            return ToolResult.fail(
                                text,
                                tool_name="procedure_run",
                                error="verification_failed",
                                data={
                                    "failed_tool": step.tool,
                                    "completed": completed,
                                    "procedure_id": proc.id,
                                },
                            )
                        continue
            if not result.success:
                failed.append(step.tool)
                if step.on_fail == "rollback":
                    _rollback(reg)
                if step.on_fail == "continue":
                    if result.message:
                        spoken.append(str(result.message).rstrip("."))
                    continue
                summary = str(result.message or "Krok się nie udał.")
                if completed:
                    summary = f"Część procedury się udała. {summary}"
                return ToolResult.fail(
                    polish_spoken(summary) or summary,
                    tool_name="procedure_run",
                    error=str(result.error or "step_failed"),
                    data={
                        "failed_tool": step.tool,
                        "completed": completed,
                        "partial": completed > 0,
                        "procedure_id": proc.id,
                    },
                )
            completed += 1
            try:
                from core.action_history import record_action

                record_action(step.tool, dict(step.args or {}), result)
            except Exception:
                pass
            if result.message:
                spoken.append(str(result.message).rstrip("."))
        _reset_run_state()
        if proc.id == "sleep" and not spoken:
            text = "Dobranoc."
        elif spoken:
            text = ". ".join(spoken) + "."
        else:
            text = f"Zrobiłem procedurę {proc.trigger}."
        payload = {
            "procedure_id": proc.id,
            "completed": completed,
            "failed": failed,
            "partial": bool(failed) and completed > 0,
        }
        if failed:
            text = (text.rstrip(".") + ". Część kroków się nie udała.") if spoken else "Część procedury się nie udała."
            return ToolResult.ok(polish_spoken(text) or text, tool_name="procedure_run", data=payload)
        return ToolResult.ok(polish_spoken(text) or text, tool_name="procedure_run", data=payload)
    except Exception as exc:
        _reset_run_state()
        return ToolResult.fail(
            "Nie udało się wykonać procedury.",
            tool_name="procedure_run",
            error=str(exc)[:120],
        )


def run_procedure(
    selector: str,
    *,
    registry: Optional[ToolRegistry] = None,
    confirmed: bool = False,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> ToolResult:
    proc = get_procedure(selector) or find_by_trigger(selector, enabled_only=False)
    if proc is None:
        from core.procedure_contract import procedure_unavailable_message

        return ToolResult.unavailable(
            procedure_unavailable_message(selector or "tej procedury"),
            tool_name="procedure_run",
            data={"procedure_id": selector, "deferred_stage": "E"},
        )
    if not proc.enabled:
        return ToolResult.fail(
            f"Procedura {proc.trigger} jest wyłączona.",
            tool_name="procedure_run",
        )
    return execute_procedure(
        proc,
        registry=registry,
        confirmed=confirmed,
        cancel_check=cancel_check,
    )


def handle_manage(
    action: str,
    payload: str,
    *,
    registry: Optional[ToolRegistry] = None,
    confirmed: bool = False,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> ToolResult:
    if action == "list":
        return ToolResult.ok(list_spoken(), tool_name="procedure_list")
    if action == "run":
        return run_procedure(
            payload,
            registry=registry,
            confirmed=confirmed,
            cancel_check=cancel_check,
        )
    if action == "disable":
        return set_enabled(payload, False)
    if action == "enable":
        return set_enabled(payload, True)
    if action == "delete":
        return delete_procedure(payload)
    if action == "edit":
        if "|||" not in payload:
            return ToolResult.fail("Powiedz, jak zmienić procedurę.", tool_name="procedure_save")
        trigger, dest = payload.split("|||", 1)
        steps, errors = compile_instruction(dest, registry)
        if not steps or errors:
            return ToolResult.fail(
                "Tego nie zapiszę — nieznane lub niebezpieczne polecenie.",
                tool_name="procedure_save",
                error="compile_failed",
                data={"errors": errors},
            )
        return save_procedure(
            trigger=trigger,
            steps=steps,
            name=trigger,
            replace=True,
            registry=registry,
        )
    if action == "cancel":
        if cancel_active_procedure():
            return ToolResult.ok("Anulowano.", tool_name="procedure_cancel")
        return ToolResult.ok("Nic teraz nie wykonuję.", tool_name="procedure_cancel")
    return ToolResult.fail("Nie wiem, co zrobić z procedurą.", tool_name="procedure_list")


def _tool_procedure_run(arguments):
    args = dict(arguments or {})
    selector = str(args.get("procedure_id") or args.get("trigger") or args.get("name") or "").strip()
    return run_procedure(selector, confirmed=bool(args.get("confirmed")))


def _tool_procedure_list(_arguments):
    return ToolResult.ok(list_spoken(), tool_name="procedure_list")


def _tool_procedure_save(arguments):
    args = dict(arguments or {})
    trigger = str(args.get("trigger") or args.get("name") or "").strip()
    dest = str(args.get("instruction") or args.get("text") or "").strip()
    raw_steps = args.get("steps")
    registry = None
    try:
        from core.tools import get_registry

        registry = get_registry()
    except Exception:
        registry = None
    steps: List[ProcedureStep] = []
    if isinstance(raw_steps, list) and raw_steps:
        for index, item in enumerate(raw_steps):
            parsed = _parse_step(item, index)
            if parsed is None:
                return ToolResult.fail(
                    "Tego nie zapiszę — uszkodzone kroki.",
                    tool_name="procedure_save",
                )
            steps.append(parsed)
    elif dest:
        steps, errors = compile_instruction(dest, registry)
        if not steps or errors:
            return ToolResult.fail(
                "Tego nie zapiszę — nieznane lub niebezpieczne polecenie.",
                tool_name="procedure_save",
            )
    else:
        return ToolResult.fail("Brakuje kroków procedury.", tool_name="procedure_save")
    return save_procedure(
        trigger=trigger,
        steps=steps,
        name=str(args.get("name") or trigger),
        procedure_id=str(args.get("procedure_id") or ""),
        replace=bool(args.get("replace")),
        registry=registry,
    )


def _tool_procedure_disable(arguments):
    args = dict(arguments or {})
    selector = str(args.get("trigger") or args.get("name") or args.get("procedure_id") or "").strip()
    enabled = args.get("enabled")
    if enabled is False or str(args.get("action") or "").lower() == "disable":
        return set_enabled(selector, False)
    if enabled is True:
        return set_enabled(selector, True)
    return set_enabled(selector, False)


def _tool_procedure_delete(arguments):
    args = dict(arguments or {})
    selector = str(args.get("trigger") or args.get("name") or args.get("procedure_id") or "").strip()
    return delete_procedure(selector)


def _tool_procedure_cancel(_arguments):
    if cancel_active_procedure():
        return ToolResult.ok("Anulowano.", tool_name="procedure_cancel")
    return ToolResult.ok("Nic teraz nie wykonuję.", tool_name="procedure_cancel")


def register_procedure_tools(registry: ToolRegistry) -> ToolRegistry:
    specs = (
        (
            "procedure_run",
            "Runs a saved declarative procedure by id or trigger. Never executes code.",
            {
                "procedure_id": {"type": "string"},
                "trigger": {"type": "string"},
                "name": {"type": "string"},
                "confirmed": {"type": "boolean"},
            },
            RiskLevel.SAFE,
            _tool_procedure_run,
        ),
        (
            "procedure_list",
            "Lists saved procedures.",
            {},
            RiskLevel.SAFE,
            _tool_procedure_list,
        ),
        (
            "procedure_save",
            "Saves or replaces a declarative procedure (JSON steps only).",
            {
                "trigger": {"type": "string"},
                "name": {"type": "string"},
                "instruction": {"type": "string"},
                "text": {"type": "string"},
                "steps": {"type": "array"},
                "replace": {"type": "boolean"},
                "procedure_id": {"type": "string"},
            },
            RiskLevel.SAFE,
            _tool_procedure_save,
        ),
        (
            "procedure_disable",
            "Enables or disables a saved procedure.",
            {
                "trigger": {"type": "string"},
                "name": {"type": "string"},
                "procedure_id": {"type": "string"},
                "enabled": {"type": "boolean"},
            },
            RiskLevel.SAFE,
            _tool_procedure_disable,
        ),
        (
            "procedure_delete",
            "Deletes a saved procedure.",
            {
                "trigger": {"type": "string"},
                "name": {"type": "string"},
                "procedure_id": {"type": "string"},
            },
            RiskLevel.MEDIUM,
            _tool_procedure_delete,
        ),
        (
            "procedure_cancel",
            "Cancels the procedure currently running.",
            {},
            RiskLevel.SAFE,
            _tool_procedure_cancel,
        ),
    )
    for name, description, properties, risk, handler in specs:
        if registry.get(name) is not None:
            registry.unregister(name)
        registry.register(
            Tool(
                name=name,
                description=description,
                parameters={"type": "object", "properties": properties},
                risk_level=risk,
                handler=handler,
                timeout=60 if name == "procedure_run" else 8,
            )
        )
    return registry
