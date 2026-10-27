# ==========================
# ELIAN — Action Journal + multi-level undo
# ==========================
"""Session action history. Inverse is a ToolRegistry call, never a policy bypass.

Journal is action history, not memory (FACT/PREFERENCE). ProcedureEngine and
ToolRegistry share this API; undo always goes through the existing firewall.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.tool_result import ToolResult

MAX_HISTORY_DEFAULT = 32
BRIGHTNESS_TOLERANCE = 5
VOLUME_TOLERANCE = 2

_TLS = threading.local()


class EntryStatus(str, Enum):
    RECORDED = "RECORDED"
    UNDONE = "UNDONE"
    IRREVERSIBLE = "IRREVERSIBLE"


class EntryStatusName:
    """Stable names for ProcedureEngine / tests."""

    RECORDED = EntryStatus.RECORDED.value
    UNDONE = EntryStatus.UNDONE.value
    IRREVERSIBLE = EntryStatus.IRREVERSIBLE.value


# Inverse tool when before_state is missing (legacy / relative ops).
_SIMPLE_INVERSE = {
    "turn_on_device": ("turn_off_device", None),
    "turn_off_device": ("turn_on_device", None),
    "play_media": ("pause_media", {}),
    "pause_media": ("resume_media", {}),
    "resume_media": ("pause_media", {}),
    "volume_up": ("volume_down", {}),
    "volume_down": ("volume_up", {}),
    "next_track": ("previous_track", {}),
    "previous_track": ("next_track", {}),
}

_HA_TOOLS = frozenset(
    {"turn_on_device", "turn_off_device", "set_device_value"}
)
_MEDIA_TOOLS = frozenset(
    {"play_media", "pause_media", "resume_media", "next_track", "previous_track"}
)
_VOLUME_TOOLS = frozenset({"volume_up", "volume_down"})
_REMINDER_TOOLS = frozenset({"create_reminder", "cancel_reminder"})
_CALENDAR_REVERSIBLE = frozenset({"create_calendar_event"})

REVERSIBLE_TOOLS = (
    _HA_TOOLS
    | _MEDIA_TOOLS
    | _VOLUME_TOOLS
    | _REMINDER_TOOLS
    | _CALENDAR_REVERSIBLE
)

IRREVERSIBLE_TOOLS = frozenset(
    {
        "send_message",
        "open_website",
        "web_search",
        "shutdown_pc",
        "shutdown_computer",
        "shutdown",
        "restart",
        "lock_computer",
        "click_ui_element",
        "type_text",
        "type_into_element",
        "hotkey",
        "press_key",
        "press_keys",
        "remember_fact",
        "set_preference",
        "activate_scene",
        "open_app",
        "close_app",
        "browser_search_service",
        "browser_select_result",
        "browser_play_media",
        "close_browser_tab",
        "delete_file",
        "send_sms",
        "delete_calendar_event",
    }
)

_SKIP_JOURNAL = frozenset(
    {
        "undo_last_action",
        "get_device_state",
        "get_home_summary",
        "get_system_status",
        "get_situation_briefing",
        "get_weather",
        "get_now_playing",
        "get_today_schedule",
        "get_calendar_events",
        "get_next_event",
        "list_reminders",
        "list_devices",
        "list_os_notifications",
        "get_phone_status",
        "describe_screen",
        "find_ui_element",
        "inspect_ui",
        "ask_about_screen",
        "check_app_installed",
        "window_exists",
        "list_windows",
        "get_active_window",
        "get_preference",
    }
)

_ON_STATES = frozenset(
    {"on", "open", "playing", "cool", "heat", "auto", "heat_cool", "dry", "fan_only"}
)
_OFF_STATES = frozenset({"off", "closed", "idle", "paused"})


StateReader = Callable[["JournalEntry"], Optional[Dict[str, Any]]]


@dataclass
class JournalEntry:
    action_id: str
    timestamp: float
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    target: str = ""
    before_state: Optional[Dict[str, Any]] = None
    after_state: Optional[Dict[str, Any]] = None
    result: Dict[str, Any] = field(default_factory=dict)
    reversible: bool = False
    undo_action: Optional[Dict[str, Any]] = None
    verification: Optional[Dict[str, Any]] = None
    session_id: str = ""
    status: str = EntryStatus.RECORDED.value

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any] | None) -> Optional["JournalEntry"]:
        if not isinstance(raw, dict) or not raw.get("action_id"):
            return None
        known = {k: raw.get(k) for k in cls.__dataclass_fields__}
        known["args"] = dict(known.get("args") or {})
        known["result"] = dict(known.get("result") or {})
        return cls(**known)


@dataclass
class UndoResult:
    success: bool
    message: str
    undid: List[str] = field(default_factory=list)
    skipped_irreversible: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    already_undone: List[str] = field(default_factory=list)
    verification_failed: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)

    def to_tool_result(self) -> ToolResult:
        data = {
            "undid": list(self.undid),
            "skipped_irreversible": list(self.skipped_irreversible),
            "conflicts": list(self.conflicts),
            "failed": list(self.failed),
            "already_undone": list(self.already_undone),
            "verification_failed": list(self.verification_failed),
            "tools": list(self.tools),
            "inverse": self.tools[0] if self.tools else "",
        }
        if self.success:
            return ToolResult.ok(self.message, tool_name="undo_last_action", data=data)
        error = "undo_failed"
        if self.conflicts:
            error = "undo_conflict"
        elif self.skipped_irreversible and not self.undid:
            error = "irreversible"
        elif self.already_undone and not self.undid:
            error = "already_undone"
        elif self.verification_failed and not self.undid:
            error = "verification_failed"
        return ToolResult.fail(
            self.message,
            tool_name="undo_last_action",
            error=error,
            data=data,
        )


class ActionJournal:
    """Bounded multi-level undo stack. Thread-safe. Session-first."""

    def __init__(
        self,
        *,
        persist: bool = False,
        path: Optional[Path] = None,
        max_entries: int | None = None,
        state_reader: Optional[StateReader] = None,
        session_id: str = "",
        clock: Optional[Callable[[], float]] = None,
    ):
        self._lock = threading.RLock()
        self._entries: List[JournalEntry] = []
        self._persist = bool(persist)
        self._path = Path(path) if path is not None else _default_path()
        self._max = int(max_entries if max_entries is not None else _max_entries())
        self._state_reader = state_reader
        self._session_id = str(session_id or "")
        self._clock = clock or time.time
        if self._persist:
            self._load()

    def set_state_reader(self, reader: Optional[StateReader]) -> None:
        self._state_reader = reader

    def set_session_id(self, session_id: str) -> None:
        self._session_id = str(session_id or "")

    def reset(self, *, persist: bool = False) -> None:
        with self._lock:
            self._entries.clear()
            self._persist = bool(persist)
            self._state_reader = None

    def entries(self) -> List[JournalEntry]:
        with self._lock:
            return [JournalEntry(**asdict(item)) for item in self._entries]

    def get(self, action_id: str) -> Optional[JournalEntry]:
        aid = str(action_id or "")
        with self._lock:
            for item in self._entries:
                if item.action_id == aid:
                    return JournalEntry(**asdict(item))
        return None

    def last_recorded(self) -> Optional[JournalEntry]:
        with self._lock:
            for item in reversed(self._entries):
                if item.status != EntryStatus.UNDONE.value:
                    return JournalEntry(**asdict(item))
        return None

    def record(
        self,
        tool: str,
        args: Dict[str, Any] | None = None,
        *,
        result: Any = None,
        before_state: Optional[Dict[str, Any]] = None,
        after_state: Optional[Dict[str, Any]] = None,
        target: str = "",
        reversible: Optional[bool] = None,
        undo_action: Optional[Dict[str, Any]] = None,
        verification: Optional[Dict[str, Any]] = None,
        session_id: str = "",
        action_id: str = "",
    ) -> Optional[JournalEntry]:
        name = str(tool or "")
        if not name or name in _SKIP_JOURNAL:
            return None
        if _in_undo():
            return None
        success = True
        result_payload: Dict[str, Any] = {}
        if result is not None:
            success = bool(getattr(result, "success", True))
            if hasattr(result, "to_observation"):
                try:
                    result_payload = dict(result.to_observation() or {})
                except Exception:
                    result_payload = {}
            elif isinstance(result, dict):
                result_payload = dict(result)
            if not success:
                return None
        arguments = dict(args or {})
        data = dict((getattr(result, "data", None) or {}) if result is not None else {})
        before = dict(before_state or {}) or _before_from_result(data)
        after = dict(after_state or {}) or _after_from_result(name, arguments, data)
        target_id = str(
            target
            or data.get("entity_id")
            or arguments.get("entity_id")
            or arguments.get("target")
            or arguments.get("name")
            or arguments.get("query")
            or ""
        )
        is_rev = bool(reversible) if reversible is not None else _is_reversible(name, arguments, data)
        inverse = undo_action if undo_action is not None else _build_undo_action(
            name, arguments, data, before, after
        )
        if is_rev and not inverse:
            is_rev = False
        status = EntryStatus.RECORDED.value if is_rev else EntryStatus.IRREVERSIBLE.value
        if name in IRREVERSIBLE_TOOLS:
            is_rev = False
            inverse = None
            status = EntryStatus.IRREVERSIBLE.value
        entry = JournalEntry(
            action_id=str(action_id or uuid.uuid4().hex[:12]),
            timestamp=float(self._clock()),
            tool=name,
            args=arguments,
            target=target_id,
            before_state=before or None,
            after_state=after or None,
            result=result_payload,
            reversible=is_rev,
            undo_action=inverse,
            verification=verification or _verification_from_data(data),
            session_id=str(session_id or self._session_id or ""),
            status=status,
        )
        with self._lock:
            self._entries.append(entry)
            self._prune_locked()
            stored = JournalEntry(**asdict(entry))
            self._save_locked()
        _stamp_world_state(stored.action_id)
        return stored

    def undo(
        self,
        count: int = 1,
        *,
        registry: Any = None,
        action_id: str = "",
        confirmed: bool = True,
    ) -> UndoResult:
        n = max(1, int(count or 1))
        if action_id:
            return self._undo_one_id(str(action_id), registry=registry, confirmed=confirmed)
        window = self._pending_window(n)
        if not window:
            return UndoResult(success=False, message="Nie mam czego cofnąć.")
        undid: List[str] = []
        tools: List[str] = []
        skipped: List[str] = []
        conflicts: List[str] = []
        failed: List[str] = []
        already: List[str] = []
        verify_failed: List[str] = []
        for entry in window:
            if entry.status == EntryStatus.UNDONE.value:
                already.append(entry.action_id)
                continue
            if not entry.reversible or entry.status == EntryStatus.IRREVERSIBLE.value:
                skipped.append(entry.action_id)
                continue
            conflict = self.detect_conflict(entry)
            if conflict:
                conflicts.append(entry.action_id)
                break
            outcome = self._revert_entry(entry, registry=registry, confirmed=confirmed)
            if outcome.get("already"):
                already.append(entry.action_id)
                continue
            if not outcome.get("ok"):
                failed.append(entry.action_id)
                break
            self._mark_undone(entry.action_id)
            undid.append(entry.action_id)
            tools.append(str((entry.undo_action or {}).get("tool") or entry.tool))
            if outcome.get("verify_failed"):
                verify_failed.append(entry.action_id)
        message = _speak_undo(
            undid=undid,
            skipped=skipped,
            conflicts=conflicts,
            failed=failed,
            already=already,
            verify_failed=verify_failed,
            window_empty=not window,
        )
        success = bool(undid) and not conflicts and not failed and not verify_failed
        return UndoResult(
            success=success,
            message=message,
            undid=undid,
            skipped_irreversible=skipped,
            conflicts=conflicts,
            failed=failed,
            already_undone=already,
            verification_failed=verify_failed,
            tools=tools,
        )

    def detect_conflict(self, entry: JournalEntry) -> bool:
        """True when live state drifted from after_state. Never revert blindly."""
        expected = dict(entry.after_state or {})
        if not expected:
            return False
        current = self._read_live(entry)
        if current is None:
            if entry.tool in _HA_TOOLS:
                return True
            return False
        return states_conflict(expected, current)

    def _undo_one_id(self, action_id: str, *, registry: Any, confirmed: bool) -> UndoResult:
        entry = self.get(action_id)
        if entry is None:
            return UndoResult(success=False, message="Nie mam czego cofnąć.")
        if entry.status == EntryStatus.UNDONE.value:
            return UndoResult(
                success=False,
                message="To już zostało cofnięte.",
                already_undone=[entry.action_id],
            )
        if not entry.reversible or entry.status == EntryStatus.IRREVERSIBLE.value:
            return UndoResult(
                success=False,
                message="Tego nie umiem cofnąć.",
                skipped_irreversible=[entry.action_id],
            )
        if self.detect_conflict(entry):
            return UndoResult(
                success=False,
                message="Stan się zmienił od tamtej akcji. Nie cofam w ciemno.",
                conflicts=[entry.action_id],
            )
        outcome = self._revert_entry(entry, registry=registry, confirmed=confirmed)
        if outcome.get("already"):
            return UndoResult(
                success=False,
                message="To już zostało cofnięte.",
                already_undone=[entry.action_id],
            )
        if not outcome.get("ok"):
            return UndoResult(
                success=False,
                message="Nie udało się cofnąć.",
                failed=[entry.action_id],
            )
        self._mark_undone(entry.action_id)
        if outcome.get("verify_failed"):
            return UndoResult(
                success=False,
                message="Cofnąłem, ale nie potwierdziłem stanu.",
                undid=[entry.action_id],
                verification_failed=[entry.action_id],
                tools=[str((entry.undo_action or {}).get("tool") or "")],
            )
        return UndoResult(
            success=True,
            message="Cofnięte.",
            undid=[entry.action_id],
            tools=[str((entry.undo_action or {}).get("tool") or "")],
        )

    def _pending_window(self, count: int) -> List[JournalEntry]:
        with self._lock:
            pending: List[JournalEntry] = []
            for item in reversed(self._entries):
                if item.status == EntryStatus.UNDONE.value:
                    continue
                pending.append(JournalEntry(**asdict(item)))
                if len(pending) >= count:
                    break
        return pending

    def _mark_undone(self, action_id: str) -> None:
        with self._lock:
            for item in self._entries:
                if item.action_id == action_id:
                    item.status = EntryStatus.UNDONE.value
                    break
            self._save_locked()

    def _revert_entry(
        self,
        entry: JournalEntry,
        *,
        registry: Any,
        confirmed: bool,
    ) -> Dict[str, Any]:
        live = self.get(entry.action_id)
        if live is None:
            return {"ok": False}
        if live.status == EntryStatus.UNDONE.value:
            return {"ok": False, "already": True}
        inverse = dict(entry.undo_action or {})
        tool = str(inverse.get("tool") or "")
        args = dict(inverse.get("args") or {})
        if not tool:
            return {"ok": False}
        try:
            from core.tools import get_registry

            reg = registry if registry is not None else get_registry()
        except Exception:
            reg = registry
        if reg is None or not hasattr(reg, "invoke"):
            return {"ok": False}
        _set_undoing(True)
        try:
            result = reg.invoke(tool, args, confirmed=bool(confirmed))
        except Exception:
            return {"ok": False}
        finally:
            _set_undoing(False)
        if not bool(getattr(result, "success", False)):
            return {"ok": False}
        verify_failed = False
        before = dict(entry.before_state or {})
        if before:
            current = self._read_live(entry)
            if current is not None and states_conflict(before, current):
                verify_failed = True
            elif current is None and entry.tool in _HA_TOOLS:
                verify_failed = True
        return {"ok": True, "verify_failed": verify_failed}

    def _read_live(self, entry: JournalEntry) -> Optional[Dict[str, Any]]:
        reader = self._state_reader
        if reader is not None:
            try:
                got = reader(entry)
            except Exception:
                got = None
            if got is not None:
                return dict(got)
            return None
        return snapshot_current(entry)

    def _prune_locked(self) -> None:
        limit = max(4, int(self._max or MAX_HISTORY_DEFAULT))
        extra = len(self._entries) - limit
        if extra > 0:
            del self._entries[:extra]

    def _load(self) -> None:
        try:
            if not self._path.is_file():
                return
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            rows = raw.get("entries") if isinstance(raw, dict) else raw
            if not isinstance(rows, list):
                return
            loaded: List[JournalEntry] = []
            for item in rows:
                entry = JournalEntry.from_dict(item if isinstance(item, dict) else None)
                if entry is not None:
                    loaded.append(entry)
            self._entries = loaded[-max(4, int(self._max or MAX_HISTORY_DEFAULT)) :]
        except Exception:
            self._entries = []

    def _save_locked(self) -> None:
        if not self._persist:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "entries": [item.to_dict() for item in self._entries],
            }
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=0), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            pass


_JOURNAL: Optional[ActionJournal] = None
_JOURNAL_LOCK = threading.Lock()


def get_journal() -> ActionJournal:
    global _JOURNAL
    with _JOURNAL_LOCK:
        if _JOURNAL is None:
            _JOURNAL = ActionJournal(persist=_persist_enabled())
        return _JOURNAL


def reset_action_history() -> None:
    get_journal().reset(persist=False)


def set_session_id(session_id: str) -> None:
    get_journal().set_session_id(session_id)


def last_action() -> Optional[Dict[str, Any]]:
    entry = get_journal().last_recorded()
    if entry is None:
        return None
    return {"tool": entry.tool, "arguments": dict(entry.args), "action_id": entry.action_id}


def record_action(tool_name: str, arguments: Dict[str, Any] | None, result: Any = None) -> None:
    get_journal().record(str(tool_name or ""), dict(arguments or {}), result=result)


def begin_capture(tool_name: str, arguments: Dict[str, Any] | None) -> Optional[Dict[str, Any]]:
    """Best-effort before_state. Never raises. Never calls ToolRegistry.invoke."""
    name = str(tool_name or "")
    if not name or name in _SKIP_JOURNAL or _in_undo():
        return None
    if name not in REVERSIBLE_TOOLS and name not in IRREVERSIBLE_TOOLS:
        if name not in REVERSIBLE_TOOLS:
            return None
    try:
        return snapshot_before(name, dict(arguments or {}))
    except Exception:
        return None


def note_registry_result(
    tool_name: str,
    arguments: Dict[str, Any] | None,
    result: Any,
    *,
    before_state: Optional[Dict[str, Any]] = None,
) -> Optional[JournalEntry]:
    if _in_undo():
        return None
    return get_journal().record(
        str(tool_name or ""),
        dict(arguments or {}),
        result=result,
        before_state=before_state,
    )


def undo_last_action(arguments=None) -> ToolResult:
    args = dict(arguments or {})
    try:
        count = int(args.get("count") or args.get("n") or 1)
    except (TypeError, ValueError):
        count = 1
    action_id = str(args.get("action_id") or "")
    outcome = get_journal().undo(count, action_id=action_id)
    return outcome.to_tool_result()


def _in_undo() -> bool:
    return bool(getattr(_TLS, "undoing", False))


def _set_undoing(flag: bool) -> None:
    _TLS.undoing = bool(flag)


def _persist_enabled() -> bool:
    try:
        import sys

        if "unittest" in sys.modules:
            return False
    except Exception:
        pass
    try:
        from Config import get_config_value

        return bool(get_config_value("action_journal", "persist", default=False))
    except Exception:
        return False


def _max_entries() -> int:
    try:
        from Config import get_config_value

        return int(get_config_value("action_journal", "max_entries", default=MAX_HISTORY_DEFAULT) or MAX_HISTORY_DEFAULT)
    except Exception:
        return MAX_HISTORY_DEFAULT


def _default_path() -> Path:
    try:
        from Config import DATA_DIR

        return Path(DATA_DIR) / "action_journal.json"
    except Exception:
        return Path("Data") / "action_journal.json"


def _stamp_world_state(action_id: str) -> None:
    try:
        from core.world_state import note_last_action_id

        note_last_action_id(action_id)
    except Exception:
        pass


def _is_reversible(name: str, arguments: Dict[str, Any], data: Dict[str, Any]) -> bool:
    if name in IRREVERSIBLE_TOOLS:
        return False
    if name in REVERSIBLE_TOOLS:
        if name == "create_reminder":
            return bool(data.get("reminder_id") or arguments.get("reminder_id"))
        if name == "cancel_reminder":
            return bool(data.get("text") or arguments.get("text"))
        if name == "create_calendar_event":
            return bool(data.get("event_id") or data.get("id") or arguments.get("event_id"))
        return True
    return False


def _build_undo_action(
    name: str,
    arguments: Dict[str, Any],
    data: Dict[str, Any],
    before: Dict[str, Any],
    after: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    entity_id = str(
        data.get("entity_id")
        or arguments.get("entity_id")
        or before.get("entity_id")
        or after.get("entity_id")
        or ""
    )
    target = str(arguments.get("target") or data.get("target") or before.get("target") or "")
    if name in _HA_TOOLS:
        return _ha_undo_action(name, arguments, data, before, entity_id, target)
    if name == "create_reminder":
        rid = str(data.get("reminder_id") or arguments.get("reminder_id") or "")
        if not rid:
            return None
        return {"tool": "cancel_reminder", "args": {"reminder_id": rid}}
    if name == "cancel_reminder":
        text = str(data.get("text") or arguments.get("text") or "")
        when = str(data.get("due_at") or arguments.get("when") or arguments.get("due_at") or "")
        if not text:
            return None
        args = {"text": text}
        if when:
            args["when"] = when
        return {"tool": "create_reminder", "args": args}
    if name == "create_calendar_event":
        eid = str(data.get("event_id") or data.get("id") or arguments.get("event_id") or "")
        if not eid:
            return None
        return {"tool": "delete_calendar_event", "args": {"event_id": eid}}
    simple = _SIMPLE_INVERSE.get(name)
    if not simple:
        return None
    inverse, forced = simple
    args = dict(forced if forced is not None else arguments)
    return {"tool": inverse, "args": args}


def _ha_undo_action(
    name: str,
    arguments: Dict[str, Any],
    data: Dict[str, Any],
    before: Dict[str, Any],
    entity_id: str,
    target: str,
) -> Optional[Dict[str, Any]]:
    base = {}
    if entity_id:
        base["entity_id"] = entity_id
    if target:
        base["target"] = target
    domain = str(before.get("domain") or data.get("domain") or arguments.get("domain") or "")
    if domain:
        base["domain"] = domain
    prior = str(before.get("state") or "").lower()
    brightness = before.get("brightness")
    temperature = before.get("temperature") or before.get("target_temperature")
    if prior in _OFF_STATES or (name == "turn_on_device" and not prior):
        if name == "turn_on_device" and not prior:
            return {"tool": "turn_off_device", "args": dict(base)}
        if prior in _OFF_STATES:
            return {"tool": "turn_off_device", "args": dict(base)}
    if prior in _ON_STATES and brightness is not None:
        args = {
            **base,
            "action": "light_brightness",
            "value": int(brightness),
        }
        return {"tool": "set_device_value", "args": args}
    if prior in _ON_STATES and temperature is not None:
        args = {
            **base,
            "action": "set_temperature",
            "value": temperature,
        }
        return {"tool": "set_device_value", "args": args}
    if prior in _ON_STATES:
        return {"tool": "turn_on_device", "args": dict(base)}
    simple = _SIMPLE_INVERSE.get(name)
    if simple:
        inverse, forced = simple
        args = dict(forced if forced is not None else {**arguments, **base})
        if entity_id:
            args.setdefault("entity_id", entity_id)
        return {"tool": inverse, "args": args}
    return None


def _before_from_result(data: Dict[str, Any]) -> Dict[str, Any]:
    before = data.get("before")
    payload: Dict[str, Any] = {}
    if data.get("entity_id"):
        payload["entity_id"] = data.get("entity_id")
        payload["kind"] = "ha"
    if data.get("domain"):
        payload["domain"] = data.get("domain")
    if isinstance(before, dict):
        payload.update(before)
        return payload
    if before not in (None, ""):
        payload["state"] = before
        payload.setdefault("kind", "ha")
        if data.get("before_brightness") is not None:
            payload["brightness"] = data.get("before_brightness")
        return payload
    return payload


def _after_from_result(name: str, arguments: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    after = data.get("after")
    payload: Dict[str, Any] = {}
    if name in _VOLUME_TOOLS:
        payload = {
            "kind": "volume",
            "direction": "up" if name == "volume_up" else "down",
        }
        if data.get("level") is not None:
            payload["level"] = data.get("level")
        return payload
    if name in _MEDIA_TOOLS:
        payload = {"kind": "media", "tool": name}
        for key in ("title", "query", "status", "playing"):
            if data.get(key) is not None:
                payload[key] = data.get(key)
        if name == "play_media" and arguments.get("query"):
            payload.setdefault("query", arguments.get("query"))
        return payload
    if data.get("entity_id"):
        payload["entity_id"] = data.get("entity_id")
        payload["kind"] = "ha"
    if data.get("domain"):
        payload["domain"] = data.get("domain")
    if isinstance(after, dict):
        payload.update(after)
        return payload
    if after not in (None, ""):
        payload["state"] = after
        payload.setdefault("kind", "ha")
        if data.get("requested_brightness") is not None:
            payload["brightness"] = data.get("requested_brightness")
        return payload
    if name in _HA_TOOLS:
        if name == "turn_on_device":
            payload["state"] = "on"
        elif name == "turn_off_device":
            payload["state"] = "off"
        if arguments.get("value") is not None and str(arguments.get("action") or "") in (
            "light_brightness",
            "adjust_brightness",
        ):
            payload["brightness"] = arguments.get("value")
        return payload
    return payload


def _verification_from_data(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not data:
        return None
    if data.get("verification") or data.get("ha_outcome"):
        return {
            "verification": data.get("verification") or "",
            "ha_outcome": data.get("ha_outcome") or "",
        }
    return None


def snapshot_before(tool_name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    name = str(tool_name or "")
    if name in _HA_TOOLS:
        return snapshot_ha(arguments)
    if name in _MEDIA_TOOLS:
        return snapshot_media()
    if name in _VOLUME_TOOLS:
        return snapshot_volume(name)
    return None


def snapshot_current(entry: JournalEntry) -> Optional[Dict[str, Any]]:
    name = str(entry.tool or "")
    args = dict(entry.args or {})
    if entry.target and not args.get("entity_id"):
        args.setdefault("entity_id", entry.target)
    if name in _HA_TOOLS:
        return snapshot_ha(args, hint=entry.after_state or entry.before_state)
    if name in _MEDIA_TOOLS:
        return snapshot_media()
    if name in _VOLUME_TOOLS:
        return snapshot_volume(name)
    return None


def snapshot_ha(
    arguments: Dict[str, Any],
    hint: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    entity_id = str(
        (arguments or {}).get("entity_id")
        or (hint or {}).get("entity_id")
        or ""
    )
    entity = None
    try:
        from core.ha_verify import read_entity_state

        if entity_id:
            entity = read_entity_state(entity_id)
    except Exception:
        entity = None
    if entity is None:
        try:
            from HaActions import resolve_entity

            entity, _err = resolve_entity(
                target=str((arguments or {}).get("target") or ""),
                domain=str((arguments or {}).get("domain") or ""),
                entity_id=entity_id,
            )
        except Exception:
            entity = None
    if entity is None:
        return None
    brightness = None
    try:
        from core.ha_verify import _brightness_pct

        brightness = _brightness_pct(entity)
    except Exception:
        raw = (getattr(entity, "attributes", None) or {}).get("brightness")
        if raw is not None:
            try:
                brightness = int(round(int(raw) * 100 / 255))
            except Exception:
                brightness = None
    attrs = getattr(entity, "attributes", None) or {}
    payload = {
        "kind": "ha",
        "entity_id": getattr(entity, "entity_id", entity_id),
        "domain": getattr(entity, "domain", ""),
        "state": str(getattr(entity, "state", "") or "").lower(),
        "target": str((arguments or {}).get("target") or ""),
    }
    if brightness is not None:
        payload["brightness"] = brightness
    temp = attrs.get("temperature") or attrs.get("current_temperature")
    if temp is not None:
        payload["temperature"] = temp
    return payload


def snapshot_media() -> Optional[Dict[str, Any]]:
    try:
        from integrations.media.provider import get_primary_media_provider

        result = get_primary_media_provider().now_playing()
        data = dict(getattr(result, "data", None) or {})
        if hasattr(result, "to_tool_kwargs"):
            try:
                data.update(result.to_tool_kwargs() or {})
            except Exception:
                pass
        payload = {"kind": "media"}
        for key in ("title", "query", "status", "playing", "artist"):
            if data.get(key) is not None:
                payload[key] = data.get(key)
        return payload
    except Exception:
        return None


def snapshot_volume(tool_name: str = "") -> Optional[Dict[str, Any]]:
    payload: Dict[str, Any] = {
        "kind": "volume",
        "direction": "up" if tool_name == "volume_up" else "down" if tool_name == "volume_down" else "",
    }
    level = _windows_volume_pct()
    if level is not None:
        payload["level"] = level
    return payload


def _windows_volume_pct() -> Optional[float]:
    """Best-effort numeric volume. Missing reader is an honest gap, not a fake."""
    try:
        from ctypes import POINTER, cast

        try:
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        except Exception:
            return None
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        level = float(volume.GetMasterVolumeLevelScalar())
        return round(level * 100.0, 1)
    except Exception:
        return None


def states_conflict(expected: Dict[str, Any], current: Dict[str, Any]) -> bool:
    if not expected or not current:
        return False
    exp_id = str(expected.get("entity_id") or "")
    cur_id = str(current.get("entity_id") or "")
    if exp_id and cur_id and exp_id != cur_id:
        return True
    eb = expected.get("brightness")
    cb = current.get("brightness")
    if eb is not None and cb is not None:
        try:
            if abs(int(eb) - int(cb)) > BRIGHTNESS_TOLERANCE:
                return True
            return False
        except (TypeError, ValueError):
            return True
    es = _norm_power(expected.get("state"))
    cs = _norm_power(current.get("state"))
    if es and cs and es != cs:
        return True
    et = str(expected.get("title") or expected.get("query") or "").strip().lower()
    ct = str(current.get("title") or current.get("query") or "").strip().lower()
    if et and ct and et != ct:
        return True
    ev = expected.get("level")
    cv = current.get("level")
    if ev is not None and cv is not None:
        try:
            if abs(float(ev) - float(cv)) > VOLUME_TOLERANCE:
                return True
        except (TypeError, ValueError):
            return True
    return False


def _norm_power(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in _ON_STATES or text in ("true", "1"):
        return "on"
    if text in _OFF_STATES or text in ("false", "0"):
        return "off"
    return text


def _speak_undo(
    *,
    undid: List[str],
    skipped: List[str],
    conflicts: List[str],
    failed: List[str],
    already: List[str],
    verify_failed: List[str],
    window_empty: bool,
) -> str:
    if window_empty:
        return "Nie mam czego cofnąć."
    if conflicts and not undid:
        return "Stan się zmienił od tamtej akcji. Nie cofam w ciemno."
    if skipped and not undid and not conflicts and not failed:
        return "Tego nie umiem cofnąć."
    if already and not undid and not skipped:
        return "To już zostało cofnięte."
    if failed and not undid:
        return "Nie udało się cofnąć."
    if verify_failed and not failed and not conflicts:
        if undid:
            return "Cofnąłem, ale nie potwierdziłem stanu."
        return "Nie potwierdziłem cofnięcia."
    if skipped and undid:
        return "Cofnięte, ale części nie umiem cofnąć."
    if len(undid) > 1:
        return "Cofnięte."
    if undid:
        return "Cofnięte."
    return "Nie mam czego cofnąć."
