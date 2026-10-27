# ==========================
# ELIAN — ComputerAgent facade (Phase 13 Stage C)
# ==========================
"""Windows perception + action. Not a second planner/chat loop.

ConversationManager.handle → Planner → ToolRegistry → ComputerAgent.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.computer_observation import (
    CONFIDENCE_LOCK,
    UI_CONTEXT_TTL_SEC,
    VERIFY_BASIC,
    VERIFY_NONE,
    VERIFY_STRONG,
    WAITING_CONFIRMATION,
    ActiveWindowContext,
    ComputerObservation,
    UIContext,
    element_payload,
    monitors_payload,
    new_snapshot_id,
)
from core.computer_windows import (
    SW_MAXIMIZE,
    SW_MINIMIZE,
    SW_RESTORE,
    FakeWindowRecord,
    FakeWindowStore,
    WindowInfo,
    WindowStore,
    default_window_store,
)
from core.tool_result import ToolResult, ToolStatus

MAX_PLAN_STEPS = 8
MAX_REPAIR_PER_STEP = 2
MAX_TOTAL_TOOL_CALLS = 20
MAX_PLAN_SECONDS = 45.0
SNAPSHOT_REUSE_TTL_SEC = 5.0

UNSAVED_PATTERNS = re.compile(
    r"(?:save\s+changes|zapisa[cć]\s+zmiany|nie\s+zapisano|do\s+you\s+want\s+to\s+save)",
    re.I,
)
UAC_PATTERNS = re.compile(
    r"(?:user\s+account\s+control|kontrola\s+konta\s+u[zż]ytkownika|\buac\b)",
    re.I,
)
PASSWORD_ROLE = re.compile(r"password|has[lł]o", re.I)

_METRICS = {
    "fake_success": 0,
    "wrong_target": 0,
    "duplicate_side_effect": 0,
    "blind_coordinate_click": 0,
    "ambiguous_click_execution": 0,
    "unsaved_discard_without_confirmation": 0,
    "wrong_window_keyboard_action": 0,
    "verified_action_timeout": 0,
    "post_timeout_side_effect_race": 0,
    "duplicate_retry_after_uncertain_side_effect": 0,
}

KEYBOARD_EDIT_ACTIONS = frozenset(
    {
        "type_text",
        "select_all",
        "undo",
        "copy",
        "paste",
        "cut",
        "hotkey",
        "press_key",
        "delete_last_word",
    }
)

IDE_HINT = re.compile(
    r"(?:cursor(?:\.exe)?|code\.exe|devenv|pycharm|idea64|"
    r"windowsterminal|windows.?terminal|powershell|pwsh(?:\.exe)?|"
    r"cmd\.exe|conhost|visual\s*studio|\bvs\s*code\b)",
    re.I,
)

_APP_MATCH_ALIASES = {
    "notatnik": ("notepad", "notatnik"),
    "notepad": ("notepad", "notatnik"),
    "kalkulator": ("calc", "kalkulator"),
    "calculator": ("calc", "kalkulator"),
    "cursor": ("cursor",),
    "vs code": ("code", "vscode", "vs code"),
}


def window_matches_app(info: Optional[WindowInfo], name: str) -> bool:
    if info is None or not str(name or "").strip():
        return False
    hay = f"{info.app_name} {info.title} {info.process_name}".lower()
    needle = str(name).lower().strip()
    tokens = _APP_MATCH_ALIASES.get(needle, (needle,))
    return any(tok in hay for tok in tokens)


def is_ide_window(info: Optional[WindowInfo]) -> bool:
    if info is None:
        return False
    hay = f"{info.process_name} {info.app_name} {info.title}"
    return bool(IDE_HINT.search(hay))


def goal_mentions_app(goal: str, name: str) -> bool:
    if not name:
        return False
    lowered = str(goal or "").lower()
    needle = str(name).lower().strip()
    if needle in lowered:
        return True
    for tok in _APP_MATCH_ALIASES.get(needle, (needle,)):
        if tok and tok in lowered:
            return True
    return False


def computer_quality_metrics() -> Dict[str, int]:
    merged = dict(_METRICS)
    try:
        from core.perception.metrics import perception_metrics

        perc = perception_metrics()
        for key in (
            "stale_visual_action",
            "low_confidence_visual_action",
            "ambiguous_visual_action",
            "sensitive_screen_persistence",
            "unnecessary_vision_call",
            "direct_vision_side_effect",
            "stale_screen_claim_rate",
        ):
            merged[key] = int(perc.get(key) or 0)
    except Exception:
        pass
    try:
        from core.proactive import proactive_metrics

        merged["internal_event_tts_leak_rate"] = int(
            proactive_metrics().get("internal_event_tts_leak_rate") or 0
        )
    except Exception:
        merged.setdefault("internal_event_tts_leak_rate", 0)
    return merged


def reset_computer_metrics() -> None:
    for key in list(_METRICS):
        _METRICS[key] = 0
    try:
        from core.perception.metrics import reset_perception_metrics

        reset_perception_metrics()
    except Exception:
        pass
    try:
        from core.proactive import reset_proactive_metrics

        reset_proactive_metrics()
    except Exception:
        pass


def _obs(
    *,
    tool: str,
    action: str,
    target: str = "",
    success: bool,
    verification_level: str,
    state_before: Any = None,
    state_after: Any = None,
    evidence: Any = None,
    error: str = "",
    reversible: bool = False,
    side_effect_level: str = "low",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = {
        "tool_name": tool,
        "semantic_action": action,
        "target": target,
        "success": success,
        "verification_level": verification_level,
        "state_before": state_before if state_before is not None else {},
        "state_after": state_after if state_after is not None else {},
        "evidence": evidence if evidence is not None else {},
        "error": error,
        "reversible": reversible,
        "side_effect_level": side_effect_level,
    }
    if extra:
        payload.update(extra)
    return payload


def _result(
    message: str,
    *,
    ok: bool,
    tool: str,
    observation: Dict[str, Any],
    data: Optional[Dict[str, Any]] = None,
    status: ToolStatus | None = None,
    error: str = "",
) -> ToolResult:
    payload = dict(data or {})
    payload["computer_observation"] = observation
    payload["verification_level"] = observation.get("verification_level")
    payload["source_kind"] = "LOCAL_COMPUTER"
    if ok:
        return ToolResult.ok(message, data=payload, tool_name=tool)
    if status is ToolStatus.NEEDS_CONFIRMATION:
        return ToolResult.needs_confirmation(message, tool_name=tool, data=payload)
    return ToolResult.fail(message, tool_name=tool, data=payload, error=error)


@dataclass
class WorkingElementHandle:
    hwnd: int = 0
    window_id: str = ""
    element_id: str = ""
    automation_id: str = ""
    process_name: str = ""
    app_name: str = ""


LIFECYCLE_IDLE = "IDLE"
LIFECYCLE_RUNNING = "RUNNING"
LIFECYCLE_SUCCEEDED = "SUCCEEDED"
LIFECYCLE_FAILED = "FAILED"
LIFECYCLE_CANCELLED = "CANCELLED"
LIFECYCLE_UNKNOWN = "UNKNOWN_SIDE_EFFECT"

ACTION_GRACE_SEC = 2.0


class ComputerAgent:
    def __init__(self, store: Optional[WindowStore] = None):
        self.store = store or default_window_store()
        self.ui_context = UIContext()
        self.active_window = ActiveWindowContext(exists=False)
        self.last_observation: Optional[ComputerObservation] = None
        self.current_plan: Dict[str, Any] = {}
        self.current_step: Dict[str, Any] = {}
        self.tool_calls = 0
        self.plan_started_at = 0.0
        self._last_typed = ""
        self._opened_ids: set[str] = set()
        self.plan_working_window = ""
        self.plan_working_app = ""
        self.plan_working_element = ""
        self.last_working_window = ""
        self.last_working_app = ""
        self.last_working_element = ""
        self._verified_result: Optional[ToolResult] = None
        self._after_verified_hook = None
        self.full_scan_count = 0
        self.target_query_count = 0
        self.snapshot_reuse_count = 0
        self._perf: Dict[str, Any] = {}
        self.editor_handle: Optional[WorkingElementHandle] = None
        self.descendants_search_count = 0
        self.lifecycle = LIFECYCLE_IDLE
        self._deadline = 0.0
        self._action_started = False
        self._typed_payload = ""
        self._block_before_action = None
        self._block_after_action = None
        self._skip_duplicate_type = False
        self.vision_requests = 0
        self.reset_scan_counts()

    def reset_scan_counts(self) -> None:
        self.full_scan_count = 0
        self.target_query_count = 0
        self.snapshot_reuse_count = 0
        self.descendants_search_count = 0
        self.vision_requests = 0
        self._perf = {
            "window_check_ms": 0.0,
            "snapshot_reused": 0,
            "uia_target_query_ms": 0.0,
            "uia_full_scan_ms": 0.0,
            "action_ms": 0.0,
            "verify_ms": 0.0,
            "total_ms": 0.0,
        }

    def commit_verified(self, result: Optional[ToolResult]) -> None:
        if result is None or not result.success:
            return
        data = dict(result.data or {})
        obs = data.get("computer_observation") or {}
        level = str(data.get("verification_level") or obs.get("verification_level") or "")
        target_ok = bool(data.get("target_verified") or obs.get("target_verified"))
        if level not in (VERIFY_BASIC, VERIFY_STRONG):
            return
        if not target_ok and level != VERIFY_STRONG:
            return
        self._verified_result = result

    def take_verified_result(self) -> Optional[ToolResult]:
        return self._verified_result

    def begin_job(self, timeout: float) -> None:
        self._skip_duplicate_type = self.lifecycle == LIFECYCLE_UNKNOWN
        self.lifecycle = LIFECYCLE_RUNNING
        self._action_started = False
        self._verified_result = None
        limit = float(timeout or 0)
        self._deadline = time.time() + (limit if limit > 0 else 12.0)

    def signal_deadline(self) -> None:
        self._deadline = min(self._deadline or time.time(), time.time())

    def deadline_exceeded(self) -> bool:
        # Inclusive: signal_deadline() sets _deadline=now; strict > often misses same-tick.
        return bool(self._deadline) and time.time() >= float(self._deadline)

    def wait_until_idle(self, timeout: float = 30.0) -> bool:
        try:
            from core.computer_ui_worker import wait_computer_idle

            return wait_computer_idle(timeout)
        except Exception:
            return True

    def is_busy(self) -> bool:
        try:
            from core.computer_ui_worker import computer_worker_busy

            return bool(computer_worker_busy())
        except Exception:
            return False

    def editor_handle_valid(self, expected_element_id: str = "") -> bool:
        handle = self.editor_handle
        if handle is None or not handle.hwnd:
            return False
        active = self.store.get_active()
        if active is None or not active.exists:
            return False
        if int(active.hwnd or 0) != int(handle.hwnd):
            return False
        if handle.window_id and str(active.window_id) != str(handle.window_id):
            return False
        if expected_element_id and handle.element_id and expected_element_id != handle.element_id:
            if ":" in expected_element_id and expected_element_id.split(":", 1)[-1] != handle.element_id:
                return False
            if ":" not in expected_element_id:
                return False
        if isinstance(self.store, FakeWindowStore):
            rec = self.store.record(active.window_id)
            if rec is None:
                return False
            if handle.element_id:
                return any(
                    str(el.get("element_id") or "") == handle.element_id
                    for el in list(rec.elements or [])
                )
            return True
        try:
            from vision.session import get_vision_session

            ui = getattr(get_vision_session(), "ui", None)
            if ui is not None and hasattr(ui, "cached_editor_valid"):
                return bool(ui.cached_editor_valid(int(handle.hwnd)))
        except Exception:
            pass
        return True

    def invalidate_editor_handle(self) -> None:
        self.editor_handle = None
        try:
            from vision.session import get_vision_session

            ui = getattr(get_vision_session(), "ui", None)
            if ui is not None and hasattr(ui, "clear_editor_cache"):
                ui.clear_editor_cache()
        except Exception:
            pass

    def cache_editor_handle(self, editor: Optional[Dict[str, Any]], window: Optional[WindowInfo]) -> None:
        if editor is None or window is None:
            return
        self.editor_handle = WorkingElementHandle(
            hwnd=int(window.hwnd or 0),
            window_id=str(window.window_id or ""),
            element_id=str(editor.get("element_id") or ""),
            automation_id=str(editor.get("automation_id") or ""),
            process_name=str(window.process_name or ""),
            app_name=str(window.app_name or ""),
        )
        self.plan_working_element = self.editor_handle.element_id or self.plan_working_element
        try:
            from vision.session import get_vision_session

            ui = getattr(get_vision_session(), "ui", None)
            if ui is not None and hasattr(ui, "pin_editor"):
                ui.pin_editor(str(editor.get("element_id") or ""), int(window.hwnd or 0))
        except Exception:
            pass

    def _prepare_editor_context(self, window: Optional[WindowInfo]) -> None:
        if window is None:
            return
        if self.editor_handle_valid():
            return
        if isinstance(self.store, FakeWindowStore):
            rec = self.store.record(window.window_id)
            for el in list((rec.elements if rec else []) or []):
                role = str(el.get("role") or "").lower()
                if role in ("document", "textbox", "edit", "input"):
                    self.cache_editor_handle(el, window)
                    return
            return
        try:
            from vision.session import get_vision_session

            session = get_vision_session()
            ui = getattr(session, "ui", None)
            if ui is None or not hasattr(ui, "query_editors"):
                return
            t0 = time.perf_counter()
            editors = list(ui.query_editors(max_elements=4, hwnd=int(window.hwnd or 0)) or [])
            self._perf["uia_target_query_ms"] = float(self._perf.get("uia_target_query_ms") or 0) + (
                (time.perf_counter() - t0) * 1000.0
            )
            if not editors:
                return
            payload = element_payload(editors[0]) if not isinstance(editors[0], dict) else dict(editors[0])
            if not payload.get("element_id") and hasattr(editors[0], "id"):
                payload = element_payload(editors[0])
            self.cache_editor_handle(payload, window)
        except Exception:
            pass

    def resolve_uncertain_type(self, args: Dict[str, Any]) -> Optional[ToolResult]:
        text = str(args.get("text") or args.get("text_payload") or "").rstrip()
        if not text:
            return None
        expected = str(args.get("app_name") or self.plan_working_app or "")
        active = self._cheap_active()
        if active is None or (expected and not window_matches_app(active, expected)):
            return None
        editor = {"element_id": (self.editor_handle.element_id if self.editor_handle else "input_1")}
        observed = self._read_editor_value(editor, self.last_observation)
        if text in str(observed or ""):
            level = VERIFY_STRONG
            result = _result(
                "Gotowe.",
                ok=True,
                tool="type_text",
                observation=_obs(
                    tool="type_text",
                    action="type_text",
                    target=str(editor.get("element_id") or expected),
                    success=True,
                    verification_level=level,
                    extra={"target_verified": True, "resolved_after_uncertain": True},
                ),
                data={
                    "typed": True,
                    "verified": True,
                    "verification_level": level,
                    "target_verified": True,
                    "resolved_after_uncertain": True,
                },
            )
            self.lifecycle = LIFECYCLE_SUCCEEDED
            self.commit_verified(result)
            return result
        return None

    def _log_perf(self) -> None:
        p = self._perf or {}
        print(
            f"[COMPUTER PERF] window_check_ms={float(p.get('window_check_ms') or 0):.0f} "
            f"snapshot_reused={int(bool(p.get('snapshot_reused')))} "
            f"uia_target_query_ms={float(p.get('uia_target_query_ms') or 0):.0f} "
            f"uia_full_scan_ms={float(p.get('uia_full_scan_ms') or 0):.0f} "
            f"action_ms={float(p.get('action_ms') or 0):.0f} "
            f"verify_ms={float(p.get('verify_ms') or 0):.0f} "
            f"total_ms={float(p.get('total_ms') or 0):.0f}"
        )

    def _run_optional_diagnostics(self) -> None:
        """World-note / extra logging. Must never run before required verification."""
        if self.last_observation is not None:
            self._note_world(self.last_observation)
        hook = getattr(self, "_after_verified_hook", None)
        if callable(hook):
            hook()

    def _cheap_active(self) -> Optional[WindowInfo]:
        t0 = time.perf_counter()
        active = self.store.get_active()
        self._perf["window_check_ms"] = float(self._perf.get("window_check_ms") or 0) + (
            (time.perf_counter() - t0) * 1000.0
        )
        if active is not None:
            self.active_window = ActiveWindowContext.from_window(active)
        return active

    def _element_resolvable(self, element_id: str) -> bool:
        eid = str(element_id or "").strip()
        if not eid:
            return True
        elements = list(self.ui_context.visible_elements or [])
        if self.last_observation is not None:
            elements = list(self.last_observation.visible_elements or elements)
        for el in elements:
            if str(el.get("element_id") or "") == eid:
                return True
        if ":" in eid:
            _, short = eid.split(":", 1)
            return self._element_resolvable(short)
        return False

    def _snapshot_reusable(self, expected_element_id: str = "") -> bool:
        ctx = self.ui_context
        obs = self.last_observation
        if obs is None or not ctx.snapshot_id:
            return False
        if (time.time() - float(ctx.created_at or 0)) > SNAPSHOT_REUSE_TTL_SEC:
            return False
        if obs.modal:
            return False
        active = self.store.get_active()
        if active is None or not active.exists:
            return False
        if ctx.active_window and str(ctx.active_window) != str(active.window_id):
            return False
        if ctx.hwnd and int(active.hwnd or 0) and int(ctx.hwnd) != int(active.hwnd or 0):
            return False
        if ctx.window_token and active.token() != ctx.window_token:
            return False
        stored_bounds = dict(ctx.window_bounds or {})
        if stored_bounds and dict(active.bounds or {}) and stored_bounds != dict(active.bounds or {}):
            return False
        size = {"width": self.store.screen_size()[0], "height": self.store.screen_size()[1]}
        if ctx.screen_size and size != ctx.screen_size:
            return False
        gen = int(getattr(self.store, "generation", ctx.generation) or 0)
        if ctx.generation and gen and int(gen) != int(ctx.generation):
            return False
        if expected_element_id and not self._element_resolvable(expected_element_id):
            return False
        return True

    def _observe_keyboard(self, args: Dict[str, Any]) -> ComputerObservation:
        expected_el = str(args.get("element_id") or self.plan_working_element or "")
        active = self._cheap_active()
        if self.editor_handle is not None and active is not None:
            if int(active.hwnd or 0) != int(self.editor_handle.hwnd or 0):
                self.invalidate_editor_handle()
        if self.editor_handle_valid(expected_el):
            self._perf["snapshot_reused"] = 1
            self.snapshot_reuse_count += 1
            if self.last_observation is not None:
                return self.last_observation
            return self.inspect(use_vision=False, mode="targeted", expected_element_id=expected_el)
        if self._snapshot_reusable(expected_el):
            self._perf["snapshot_reused"] = 1
            self.snapshot_reuse_count += 1
            return self.last_observation  # type: ignore[return-value]
        return self.inspect(use_vision=False, mode="targeted", expected_element_id=expected_el)

    def log_computer(self) -> None:
        active = self.active_window
        print(
            f"[COMPUTER] active_window={active.title or '-'} "
            f"process={active.process_name or '-'} "
            f"snapshot_id={self.ui_context.snapshot_id or '-'}"
        )

    def begin_plan(self, plan_id: str = "", goal: str = "") -> None:
        self.current_plan = {"plan_id": plan_id, "goal": str(goal or "")}
        self.tool_calls = 0
        self.plan_started_at = time.time()
        self.plan_working_window = ""
        self.plan_working_app = ""
        self.plan_working_element = ""

    def bind_working_target(self, name: str = "", window: Optional[WindowInfo] = None) -> None:
        info = window
        token = str(name or "").strip()
        if info is None and token:
            info = self.store.get(token)
        if info is None:
            info = self.store.get_active()
        if info is None:
            return
        self.plan_working_window = str(info.window_id or "")
        self.plan_working_app = str(info.app_name or token or "")
        rec = self.store.record(info.window_id) if hasattr(self.store, "record") else None
        if rec is not None:
            for el in list(rec.elements or []):
                role = str(el.get("role") or "").lower()
                if role in ("document", "textbox", "edit", "input"):
                    self.plan_working_element = str(el.get("element_id") or "")
                    break
        self.last_working_window = self.plan_working_window
        self.last_working_app = self.plan_working_app
        self.last_working_element = self.plan_working_element
        self.current_plan["plan_working_window"] = self.plan_working_window
        self.current_plan["plan_working_app"] = self.plan_working_app
        self.current_plan["plan_working_element"] = self.plan_working_element
        print(
            f"[COMPUTER] bind working window={self.plan_working_window} "
            f"app={self.plan_working_app} element={self.plan_working_element or '-'}"
        )
        self._prepare_editor_context(info)

    def budget_exceeded(self, *, steps: int = 0) -> str:
        if steps > MAX_PLAN_STEPS:
            return "max_plan_steps"
        if self.tool_calls >= MAX_TOTAL_TOOL_CALLS:
            return "max_total_tool_calls"
        if self.plan_started_at and (time.time() - self.plan_started_at) > MAX_PLAN_SECONDS:
            return "max_plan_seconds"
        return ""

    def inspect(
        self,
        *,
        use_vision: bool = False,
        mode: str = "full",
        expected_element_id: str = "",
    ) -> ComputerObservation:
        kind = str(mode or "full").strip().lower() or "full"
        if kind in ("reuse", "auto") and self._snapshot_reusable(expected_element_id):
            self._perf["snapshot_reused"] = 1
            self.snapshot_reuse_count += 1
            return self.last_observation  # type: ignore[return-value]
        if kind == "cheap" and self.last_observation is not None and self._snapshot_reusable(
            expected_element_id
        ):
            self._perf["snapshot_reused"] = 1
            self.snapshot_reuse_count += 1
            windows = self.store.list_windows()
            active = self._cheap_active()
            obs = self.last_observation
            self.active_window = ActiveWindowContext.from_window(active)
            self.log_computer()
            return obs
        windows = self.store.list_windows()
        active = self.store.get_active()
        for item in windows:
            if item.window_id in self._opened_ids:
                item.opened_by_elian = True
        ctx = ActiveWindowContext.from_window(active)
        self.active_window = ctx
        width, height = self.store.screen_size()
        cx, cy = self.store.cursor_position()
        collect_mode = "targeted" if kind == "targeted" else "full"
        if kind == "cheap":
            collect_mode = "full"
        elements, snapshot_id, ui_available = self._collect_elements(
            active,
            use_vision=use_vision,
            mode=collect_mode,
            expected_element_id=expected_element_id,
        )
        focused = next((el for el in elements if el.get("focused")), {})
        modal = self._detect_modal(active, elements)
        obs = ComputerObservation(
            active_window=ctx,
            windows=[item.to_dict() for item in windows],
            focused_element=focused,
            visible_elements=elements,
            screen_size={"width": width, "height": height},
            cursor_position={"x": cx, "y": cy},
            app_process=ctx.process_name,
            app_name=ctx.app_name,
            window_title=ctx.title,
            ui_tree_available=ui_available,
            screenshot_available=False,
            snapshot_id=snapshot_id,
            monitors=monitors_payload(self.store.monitors()),
            modal=modal,
            window_token=ctx.window_token,
        )
        self.last_observation = obs
        self.ui_context = UIContext(
            snapshot_id=snapshot_id,
            active_window=ctx.window_id,
            window_token=ctx.window_token,
            visible_elements=elements,
            created_at=time.time(),
            screen_size={"width": width, "height": height},
            generation=getattr(self.store, "generation", 0),
            last_clicked_element=self.ui_context.last_clicked_element,
            last_typed_element=self.ui_context.last_typed_element,
            selected_element=self.ui_context.selected_element,
            result_list=[el for el in elements if el.get("role") in ("listitem", "list item")],
            hwnd=int(ctx.hwnd or (active.hwnd if active else 0) or 0),
            window_bounds=dict(ctx.bounds or (active.bounds if active else {}) or {}),
        )
        self.log_computer()
        self._sync_perception(obs)
        if collect_mode == "full":
            self._note_world(obs)
        return obs

    def observe_window(self, window_id: str = "") -> ComputerObservation:
        if window_id:
            self.store.focus(window_id)
        return self.inspect()

    def _collect_elements(
        self,
        active: Optional[WindowInfo],
        *,
        use_vision: bool,
        mode: str = "full",
        expected_element_id: str = "",
    ) -> Tuple[List[Dict[str, Any]], str, bool]:
        snapshot_id = new_snapshot_id()
        kind = str(mode or "full").strip().lower() or "full"
        if kind == "reuse" and self.last_observation is not None:
            prev = list(self.last_observation.visible_elements or [])
            return prev, self.last_observation.snapshot_id or snapshot_id, True
        if kind == "targeted" and self.editor_handle_valid(expected_element_id):
            handle = self.editor_handle
            payload = {
                "element_id": handle.element_id,
                "role": "document",
                "automation_id": handle.automation_id,
                "enabled": True,
                "visible": True,
                "focused": True,
            }
            if self.last_observation is not None:
                prev = list(self.last_observation.visible_elements or [])
                if prev:
                    return prev, self.last_observation.snapshot_id or snapshot_id, True
            return [payload], snapshot_id, True
        if isinstance(self.store, FakeWindowStore) and active is not None:
            rec = self.store.record(active.window_id)
            rows = []
            for item in list((rec.elements if rec else []) or []):
                row = dict(item)
                row.setdefault("element_id", row.get("id") or "")
                if rec and rec.editor_text and row.get("role") in ("document", "textbox", "edit"):
                    row["value"] = rec.editor_text
                    row["text"] = rec.editor_text
                rows.append(row)
            if rec and rec.modal == "unsaved":
                rows.append(
                    {
                        "element_id": "button_save",
                        "role": "button",
                        "name": "Zapisz",
                        "confidence": 0.95,
                        "enabled": True,
                        "visible": True,
                        "actions": ["invoke"],
                    }
                )
                rows.append(
                    {
                        "element_id": "button_dont_save",
                        "role": "button",
                        "name": "Nie zapisuj",
                        "confidence": 0.95,
                        "enabled": True,
                        "visible": True,
                        "actions": ["invoke"],
                    }
                )
            if kind == "targeted":
                t0 = time.perf_counter()
                self.target_query_count += 1
                self.descendants_search_count += 1
                self._perf["uia_target_query_ms"] = float(self._perf.get("uia_target_query_ms") or 0) + (
                    (time.perf_counter() - t0) * 1000.0
                )
                if expected_element_id:
                    hit = [row for row in rows if str(row.get("element_id") or "") == expected_element_id]
                    if hit:
                        return hit + [r for r in rows if r not in hit], snapshot_id, True
                return rows, snapshot_id, True
            t0 = time.perf_counter()
            self.full_scan_count += 1
            self.descendants_search_count += 1
            self._perf["uia_full_scan_ms"] = float(self._perf.get("uia_full_scan_ms") or 0) + (
                (time.perf_counter() - t0) * 1000.0
            )
            return rows, snapshot_id, True
        elements: List[Dict[str, Any]] = []
        ui_available = False
        try:
            from vision.session import get_vision_session

            session = get_vision_session()
            if kind == "targeted":
                t0 = time.perf_counter()
                editors = []
                ui = getattr(session, "ui", None)
                hwnd = int(active.hwnd or 0) if active is not None else 0
                if ui is not None and hasattr(ui, "query_editors"):
                    editors = list(ui.query_editors(max_elements=8, hwnd=hwnd) or [])
                self.target_query_count += 1
                try:
                    from vision import uia as uia_mod

                    self.descendants_search_count += int(getattr(uia_mod, "last_descendants_searches", 0) or 0)
                except Exception:
                    pass
                self._perf["uia_target_query_ms"] = float(self._perf.get("uia_target_query_ms") or 0) + (
                    (time.perf_counter() - t0) * 1000.0
                )
                if editors:
                    for el in editors:
                        payload = element_payload(el)
                        elements.append(payload)
                    ui_available = True
                    if self.last_observation is not None and not elements:
                        return (
                            list(self.last_observation.visible_elements or []),
                            self.last_observation.snapshot_id or snapshot_id,
                            True,
                        )
                    return elements, snapshot_id, ui_available
            t0 = time.perf_counter()
            snap = session.refresh(
                force=True,
                use_vision=bool(use_vision),
                use_ocr=False,
                use_uia=True,
                active_window_only=True,
            )
            self.full_scan_count += 1
            self._perf["uia_full_scan_ms"] = float(self._perf.get("uia_full_scan_ms") or 0) + (
                (time.perf_counter() - t0) * 1000.0
            )
            snapshot_id = snap.id
            ui_available = "uia" in (snap.sources or []) or bool(snap.elements)
            for el in snap.elements:
                payload = element_payload(el)
                payload["snapshot_id"] = snap.id
                elements.append(payload)
        except Exception as exc:
            print(f"[COMPUTER] UIA inspect failed: {exc}")
            if use_vision:
                self.vision_requests += 1
                try:
                    from core.perception.metrics import note_perception

                    note_perception("vision_requests")
                except Exception:
                    pass
                elements = self._vision_fallback_elements()
        return elements, snapshot_id, ui_available

    def _vision_fallback_elements(self) -> List[Dict[str, Any]]:
        try:
            from vision.session import get_vision_session

            snap = get_vision_session().refresh(
                force=True, use_vision=True, use_ocr=True, use_uia=False
            )
            rows = []
            for el in snap.elements:
                payload = element_payload(el)
                payload["confidence"] = min(float(payload.get("confidence") or 0.0), 0.75)
                payload["source"] = payload.get("source") or "vision"
                rows.append(payload)
            return rows
        except Exception:
            return []

    def _detect_modal(
        self,
        active: Optional[WindowInfo],
        elements: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if isinstance(self.store, FakeWindowStore) and active is not None:
            rec = self.store.record(active.window_id)
            if rec and rec.modal == "unsaved":
                return {
                    "kind": "unsaved",
                    "title": "Zapisać zmiany?",
                    "uac": False,
                }
            if rec and rec.modal == "uac":
                return {"kind": "uac", "title": "Kontrola konta użytkownika", "uac": True}
        title = (active.title if active else "") or ""
        names = " ".join(str(el.get("name") or "") for el in elements)
        blob = f"{title} {names}"
        if UAC_PATTERNS.search(blob):
            return {"kind": "uac", "title": title, "uac": True}
        if UNSAVED_PATTERNS.search(blob):
            return {"kind": "unsaved", "title": title, "uac": False}
        return {}

    def _resolve_deictic(self, phrase: str, obs: ComputerObservation, *, for_action: bool) -> Optional[Dict[str, Any]]:
        try:
            from core.perception.screen_runtime import get_screen_runtime
            from core.perception.visual_referent import VisualReferent
        except Exception:
            return None
        runtime = get_screen_runtime()
        cx, cy = self.store.cursor_position()
        cursor_el = None
        candidates: List[VisualReferent] = []
        for el in list(obs.visible_elements or []):
            bounds = el.get("bounds") or {}
            x = int(bounds.get("x") or 0)
            y = int(bounds.get("y") or 0)
            w = int(bounds.get("width") or bounds.get("w") or 0)
            h = int(bounds.get("height") or bounds.get("h") or 0)
            ref = VisualReferent(
                referent_id=str(el.get("element_id") or ""),
                label=str(el.get("name") or el.get("text") or ""),
                role=str(el.get("role") or ""),
                bbox=[x, y, x + w, y + h],
                uia_element_id=str(el.get("element_id") or ""),
                confidence=float(el.get("confidence") or 0.9),
                source=["LOCAL_COMPUTER_UIA"],
                frame_id=int(runtime.state.frame_id or 0),
                hwnd=int(getattr(obs.active_window, "hwnd", 0) or 0),
            )
            candidates.append(ref)
            if w and h and x <= cx <= x + w and y <= cy <= y + h:
                cursor_el = ref
        result = runtime.resolve_visual_referent(
            phrase,
            cursor_position=(cx, cy),
            candidates=candidates,
        )
        if cursor_el is not None and (
            "to" in phrase.lower() or "ten" in phrase.lower() or "tutaj" in phrase.lower()
        ):
            result = runtime.referents.resolve(
                phrase,
                candidates=candidates,
                cursor_element=cursor_el,
                for_action=for_action,
            )
        if result.get("clarify"):
            return {
                "ok": False,
                "clarify": True,
                "reason": "ambiguous",
                "candidates": result.get("candidates") or [],
                "message": result.get("message") or "Widzę dwa możliwe elementy. Górny czy dolny?",
            }
        if not result.get("ok"):
            return None
        ref = result.get("referent")
        element_id = getattr(ref, "uia_element_id", "") if ref is not None else ""
        element = next(
            (el for el in (obs.visible_elements or []) if str(el.get("element_id") or "") == str(element_id)),
            None,
        )
        if element is None and cursor_el is not None:
            element = next(
                (
                    el
                    for el in (obs.visible_elements or [])
                    if str(el.get("element_id") or "") == cursor_el.uia_element_id
                ),
                None,
            )
        if element is None:
            return None
        return {"ok": True, "element": element, "confidence": element.get("confidence"), "clarify": False}

    def find_element(self, query: str, *, for_action: bool = True) -> Dict[str, Any]:
        obs = self.last_observation or self.inspect()
        text = str(query or "").strip()
        deictic = bool(re.search(r"^(?:to|ten|to tutaj|ten tutaj|tutaj)\b", text, re.I)) or bool(
            re.search(r"\b(?:kliknij\s+)?(?:to|ten|ten przycisk|to tutaj)\b", text, re.I)
            and len(text.split()) <= 5
        )
        if deictic:
            resolved = self._resolve_deictic(text, obs, for_action=for_action)
            if resolved is not None:
                return resolved
        matches = self._score_elements(query, obs.visible_elements)
        print(
            f"[UI] candidates={len(matches)} selected="
            f"{(matches[0][0].get('element_id') if matches else '-')} "
            f"confidence={(matches[0][1] if matches else 0):.2f}"
        )
        if not matches:
            return {
                "ok": False,
                "clarify": False,
                "reason": "missing",
                "candidates": [],
                "message": f"Nie znalazłem elementu „{query}”.",
            }
        best_el, best = matches[0]
        native = float(best_el.get("confidence") or 0.0) or best
        similar = [
            (el, score)
            for el, score in matches
            if score >= CONFIDENCE_LOCK
            or (
                abs(score - best) < 0.12
                and score >= 0.5
                and _norm_name(el.get("name") or el.get("text"))
                == _norm_name(best_el.get("name") or best_el.get("text"))
            )
        ]
        if for_action and len(similar) >= 2:
            names = [str(el.get("name") or el.get("text") or el.get("element_id")) for el, _ in similar[:3]]
            return {
                "ok": False,
                "clarify": True,
                "reason": "ambiguous",
                "candidates": [el for el, _ in similar],
                "message": f"Widzę dwa przyciski „{names[0]}”. Który masz na myśli?",
            }
        if for_action and (best < CONFIDENCE_LOCK or native < CONFIDENCE_LOCK):
            return {
                "ok": False,
                "clarify": True,
                "reason": "low_confidence",
                "candidates": [el for el, _ in matches[:3]],
                "confidence": best,
                "message": "Nie jestem pewien, który element masz na myśli.",
            }
        return {
            "ok": True,
            "element": best_el,
            "confidence": best,
            "candidates": [el for el, _ in matches[:5]],
        }

    def _score_elements(
        self, query: str, elements: List[Dict[str, Any]]
    ) -> List[Tuple[Dict[str, Any], float]]:
        q = _norm_name(query)
        scored: List[Tuple[Dict[str, Any], float]] = []
        for el in elements:
            name = _norm_name(el.get("name") or el.get("text") or "")
            role = _norm_name(el.get("role") or "")
            eid = _norm_name(el.get("element_id") or "")
            base = float(el.get("confidence") or 0.0)
            score = 0.0
            if q and q == name:
                score = 0.98
            elif q and q in name:
                score = 0.92
            elif q and name and (q in name or name in q):
                score = 0.85
            elif q and any(tok and tok in name for tok in q.split() if len(tok) > 2):
                score = 0.7
            elif q and (q in role or q == eid):
                score = 0.6
            if "niebieski" in q and "blue" in str(el.get("meta") or {}).lower():
                score = max(score, 0.8)
            native = float(el.get("confidence") or 0.0) or 1.0
            final = score * native if score else 0.0
            if final > 0:
                scored.append((el, float(final)))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored

    def resolve_referent(self, text: str) -> Dict[str, Any]:
        raw = str(text or "").lower()
        ctx = self.ui_context
        if ctx.expired():
            self.inspect()
            ctx = self.ui_context
        elements = list(ctx.visible_elements or [])
        if re.search(r"\bdrug", raw) and elements:
            pick = elements[1] if len(elements) > 1 else None
            if pick:
                return {"ok": True, "element": pick}
        if re.search(r"\bpoprzedni\b", raw) and ctx.last_clicked_element:
            for el in elements:
                if el.get("element_id") == ctx.last_clicked_element:
                    return {"ok": True, "element": el}
        if re.search(r"\b(?:to pole|to|ten przycisk|je)\b", raw):
            eid = ctx.last_typed_element or ctx.selected_element or ctx.last_clicked_element
            for el in elements:
                if el.get("element_id") == eid:
                    return {"ok": True, "element": el}
            focused = (self.last_observation.focused_element if self.last_observation else {}) or {}
            if focused:
                return {"ok": True, "element": focused}
        if re.search(r"\bto okno\b", raw) and self.active_window.window_id:
            return {"ok": True, "window_id": self.active_window.window_id}
        return {"ok": False, "clarify": True, "message": "Nie wiem, który element masz na myśli."}

    def snapshot_stale(self, element_id: str = "") -> bool:
        ctx = self.ui_context
        if not ctx.snapshot_id or ctx.expired():
            return True
        if ":" in str(element_id or ""):
            snap_id, _ = str(element_id).split(":", 1)
            if snap_id and snap_id != ctx.snapshot_id:
                return True
        current = self.store.get_active()
        token = current.token() if current else ""
        if ctx.window_token and token and ctx.window_token != token:
            return True
        if current is not None and ctx.hwnd and int(current.hwnd or 0) and int(ctx.hwnd) != int(current.hwnd or 0):
            return True
        size = {"width": self.store.screen_size()[0], "height": self.store.screen_size()[1]}
        if ctx.screen_size and size != ctx.screen_size:
            return True
        gen = getattr(self.store, "generation", ctx.generation)
        if ctx.generation and gen and int(gen) != int(ctx.generation):
            return True
        return False

    def act(self, action: str, arguments: Optional[Dict[str, Any]] = None) -> ToolResult:
        args = dict(arguments or {})
        name = str(action or args.get("semantic_action") or "").strip()
        plan_id = str(args.get("plan_id") or self.current_plan.get("plan_id") or "")
        step_id = str(args.get("step_id") or "")
        attempt_id = int(args.get("attempt_id") or 1)
        self.tool_calls += 1
        print(f"[ACTION] plan_id={plan_id or '-'} step_id={step_id or '-'} attempt_id={attempt_id} tool={name}")
        t_total = time.perf_counter()
        if self.lifecycle != LIFECYCLE_RUNNING:
            self.lifecycle = LIFECYCLE_RUNNING
            self._action_started = False
        self._verified_result = None
        self._perf = {
            "window_check_ms": float((self._perf or {}).get("window_check_ms") or 0),
            "snapshot_reused": 0,
            "uia_target_query_ms": 0.0,
            "uia_full_scan_ms": 0.0,
            "action_ms": 0.0,
            "verify_ms": 0.0,
            "total_ms": 0.0,
        }
        if name in KEYBOARD_EDIT_ACTIONS:
            before = self._observe_keyboard(args)
        elif name in ("list_windows", "get_active_window", "window_exists"):
            before = self.inspect(use_vision=False, mode="cheap")
        elif name in ("inspect_ui", "describe_screen"):
            before = self.last_observation or self.inspect(use_vision=False, mode="cheap")
        else:
            before = self.inspect(use_vision=False, mode="full")
        if before.modal.get("uac"):
            return _result(
                "Windows prosi o uprawnienia administratora. Musisz to potwierdzić sam.",
                ok=False,
                tool=name,
                observation=_obs(
                    tool=name,
                    action=name,
                    success=False,
                    verification_level=VERIFY_STRONG,
                    state_before=before.planner_fragment(),
                    error="uac",
                ),
                error="uac",
            )
        handler = {
            "list_windows": self._act_list_windows,
            "get_active_window": self._act_active,
            "focus_window": self._act_focus,
            "switch_window": self._act_focus,
            "minimize_window": lambda a, b: self._act_show(a, b, SW_MINIMIZE, "minimize"),
            "maximize_window": lambda a, b: self._act_show(a, b, SW_MAXIMIZE, "maximize"),
            "restore_window": lambda a, b: self._act_show(a, b, SW_RESTORE, "restore"),
            "close_window": self._act_close,
            "close_app": self._act_close,
            "wait_for_window": self._act_wait_window,
            "window_exists": self._act_exists,
            "inspect_ui": lambda a, b: self._act_inspect(a, b, False),
            "describe_screen": lambda a, b: self._act_inspect(a, b, True),
            "find_ui_element": self._act_find,
            "click_ui_element": self._act_click,
            "click_referent": self._act_click,
            "double_click_ui_element": lambda a, b: self._act_click(a, b, clicks=2),
            "right_click_ui_element": lambda a, b: self._act_click(a, b, button="right"),
            "type_text": self._act_type,
            "press_key": self._act_press,
            "hotkey": self._act_press,
            "select_all": lambda a, b: self._act_press(a, {**b, "keys": "ctrl+a"}),
            "undo": lambda a, b: self._act_press(a, {**b, "keys": "ctrl+z"}),
            "copy": lambda a, b: self._act_press(a, {**b, "keys": "ctrl+c"}),
            "paste": lambda a, b: self._act_press(a, {**b, "keys": "ctrl+v"}),
            "cut": lambda a, b: self._act_press(a, {**b, "keys": "ctrl+x"}),
            "delete_last_word": self._act_delete_last_word,
            "scroll": self._act_scroll,
            "scroll_up": lambda a, b: self._act_scroll(a, {**b, "direction": "up"}),
            "scroll_down": lambda a, b: self._act_scroll(a, {**b, "direction": "down"}),
            "scroll_element": self._act_scroll,
            "drag_element": self._act_drag,
            "drag_to_element": self._act_drag,
            "drag_by": self._act_drag,
            "wait_for_element": self._act_wait_element,
            "wait_for_state": self._act_wait_state,
            "open_app": self._act_open_app,
            "focus_or_open": self._act_open_app,
            "find_file": self._act_find_file,
            "open_file": self._act_open_file,
            "list_folder": self._act_list_folder,
            "read_clipboard": self._act_clipboard,
            "lock_pc": lambda a, b: self._act_power(a, {**b, "action": "lock"}),
            "sleep_pc": lambda a, b: self._act_power(a, {**b, "action": "sleep"}),
            "shutdown_pc": lambda a, b: self._act_power(a, {**b, "action": "shutdown"}),
            "restart_pc": lambda a, b: self._act_power(a, {**b, "action": "restart"}),
        }.get(name)
        if handler is None:
            return _result(
                "Tej czynności komputerowej jeszcze nie wykonuję.",
                ok=False,
                tool=name or "computer",
                observation=_obs(
                    tool=name or "computer",
                    action=name,
                    success=False,
                    verification_level=VERIFY_NONE,
                    error="unmapped",
                ),
                error="unmapped",
            )
        result = handler(before, args)
        self._perf["total_ms"] = (time.perf_counter() - t_total) * 1000.0
        self._log_perf()
        if result.success:
            self.lifecycle = LIFECYCLE_SUCCEEDED
        elif str(result.error or "") == "unknown_side_effect":
            self.lifecycle = LIFECYCLE_UNKNOWN
        elif (not result.success) and self._action_started:
            self.lifecycle = LIFECYCLE_UNKNOWN
            data = dict(result.data or {})
            data["side_effect_may_have_occurred"] = True
            result.data = data
            if result.status == ToolStatus.TIMEOUT:
                result = ToolResult.unknown_side_effect(
                    result.message or "Nie potwierdziłem skutku tej czynności.",
                    tool_name=result.tool_name or name,
                    data=data,
                )
        elif not result.success:
            self.lifecycle = LIFECYCLE_FAILED if result.status != ToolStatus.TIMEOUT else LIFECYCLE_CANCELLED
        return result

    def verify(self, expected: str, observed: Any, *, tool: str = "verify") -> ToolResult:
        ok = bool(observed)
        status = "verified" if ok else "failed"
        print(f"[VERIFY] expected={expected} observed={observed} status={status}")
        return _result(
            "Gotowe." if ok else f"Nie potwierdziłem: {expected}.",
            ok=ok,
            tool=tool,
            observation=_obs(
                tool=tool,
                action="verify",
                success=ok,
                verification_level=VERIFY_STRONG if ok else VERIFY_NONE,
                evidence={"expected": expected, "observed": observed},
            ),
        )

    def _act_list_windows(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        after = self.last_observation or before
        titles = [w.get("title") or w.get("app_name") for w in after.windows if w.get("title") or w.get("app_name")]
        msg = "Otwarte okna: " + ", ".join(str(t) for t in titles[:8]) + "." if titles else "Nie widzę otwartych okien."
        return _result(
            msg,
            ok=True,
            tool="list_windows",
            observation=_obs(
                tool="list_windows",
                action="list_windows",
                success=True,
                verification_level=VERIFY_STRONG,
                state_after={"windows": titles},
                reversible=True,
                side_effect_level="none",
            ),
            data={"windows": after.windows, "source_kind": "LOCAL_COMPUTER"},
        )

    def _act_active(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        active = before.active_window
        if active is None or not active.exists:
            return _result(
                "Nie widzę aktywnego okna.",
                ok=False,
                tool="get_active_window",
                observation=_obs(
                    tool="get_active_window",
                    action="get_active_window",
                    success=False,
                    verification_level=VERIFY_NONE,
                    error="missing",
                ),
            )
        spoken = f"Aktywna aplikacja: {active.app_name or active.title}."
        return _result(
            spoken,
            ok=True,
            tool="get_active_window",
            observation=_obs(
                tool="get_active_window",
                action="get_active_window",
                success=True,
                verification_level=VERIFY_STRONG,
                state_after=active.to_dict(),
                reversible=True,
                side_effect_level="none",
            ),
            data={
                "active_window": active.title,
                "active_process": active.process_name,
                "app_name": active.app_name,
                "source_kind": "LOCAL_COMPUTER",
            },
        )

    def _target_window(self, args: Dict[str, Any]) -> Optional[WindowInfo]:
        target = str(args.get("window_id") or args.get("name") or args.get("app_name") or args.get("query") or "").strip()
        if target:
            found = self.store.get(target)
            if found is not None:
                return found
        return self.store.get_active()

    def _act_focus(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        info = self._target_window(args)
        if info is None:
            return _result(
                "Nie znalazłem tego okna.",
                ok=False,
                tool="focus_window",
                observation=_obs(
                    tool="focus_window",
                    action="focus_window",
                    success=False,
                    verification_level=VERIFY_NONE,
                    error="missing",
                ),
            )
        ok = self.store.focus(info.window_id)
        after = self.inspect()
        focused = bool(after.active_window and after.active_window.window_id == info.window_id)
        print(
            f"[VERIFY] expected=focused:{info.app_name} "
            f"observed={after.app_name} status={'verified' if focused else 'failed'}"
        )
        if ok and focused:
            self.bind_working_target(info.app_name, window=info)
        return _result(
            f"{info.app_name or info.title} jest teraz aktywny." if focused else "Nie udało się przełączyć okna.",
            ok=bool(ok and focused),
            tool="focus_window",
            observation=_obs(
                tool="focus_window",
                action="focus_window",
                target=info.app_name,
                success=bool(ok and focused),
                verification_level=VERIFY_STRONG,
                state_before=before.planner_fragment(),
                state_after=after.planner_fragment(),
                reversible=True,
            ),
        )

    def _act_show(
        self,
        before: ComputerObservation,
        args: Dict[str, Any],
        command: int,
        action: str,
    ) -> ToolResult:
        info = self._target_window(args)
        tool = f"{action}_window"
        if info is None:
            return _result(
                "Nie mam okna do tej operacji.",
                ok=False,
                tool=tool,
                observation=_obs(tool=tool, action=action, success=False, verification_level=VERIFY_NONE, error="missing"),
            )
        ok = self.store.show(info.window_id, command)
        after = self.inspect()
        current = self.store.get(info.window_id)
        verified = False
        if current is not None:
            if action == "minimize":
                verified = bool(current.minimized)
            elif action == "maximize":
                verified = bool(current.maximized)
            else:
                verified = (not current.minimized) and current.visible
        print(f"[VERIFY] expected={action} observed={verified} status={'verified' if verified else 'failed'}")
        spoken = {
            "minimize": "Zminimalizowane.",
            "maximize": "Zmaksymalizowane.",
            "restore": "Przywrócone.",
        }.get(action, "Gotowe.")
        return _result(
            spoken if ok and verified else "Nie udało się zmienić okna.",
            ok=bool(ok and verified),
            tool=tool,
            observation=_obs(
                tool=tool,
                action=action,
                target=info.app_name,
                success=bool(ok and verified),
                verification_level=VERIFY_STRONG,
                state_before=before.planner_fragment(),
                state_after=after.planner_fragment(),
                reversible=True,
            ),
        )

    def _act_close(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        info = self._target_window(args)
        if info is None:
            return _result(
                "Nie mam okna do zamknięcia.",
                ok=False,
                tool="close_window",
                observation=_obs(
                    tool="close_window",
                    action="close_window",
                    success=False,
                    verification_level=VERIFY_NONE,
                    error="missing",
                ),
            )
        result = self.store.close(info.window_id)
        after = self.inspect()
        if result.get("reason") == "unsaved" or after.modal.get("kind") == "unsaved":
            return _result(
                "To okno ma niezapisane zmiany. Mam zapisać, czy zamknąć bez zapisu?",
                ok=False,
                tool="close_window",
                observation=_obs(
                    tool="close_window",
                    action="close_window",
                    target=info.app_name,
                    success=False,
                    verification_level=VERIFY_STRONG,
                    state_after=after.planner_fragment(),
                    extra={"waiting": WAITING_CONFIRMATION, "modal": after.modal},
                    reversible=False,
                    side_effect_level="high",
                    error="unsaved",
                ),
                status=ToolStatus.NEEDS_CONFIRMATION,
                data={"waiting": WAITING_CONFIRMATION, "modal": after.modal},
                error="unsaved",
            )
        gone = self.store.get(info.window_id) is None
        return _result(
            f"{info.app_name or info.title} zamknięte." if gone or result.get("ok") else "Nie zamknąłem okna.",
            ok=bool(gone or result.get("ok")),
            tool="close_window",
            observation=_obs(
                tool="close_window",
                action="close_window",
                target=info.app_name,
                success=bool(gone or result.get("ok")),
                verification_level=VERIFY_STRONG,
                state_before=before.planner_fragment(),
                state_after=after.planner_fragment(),
                reversible=False,
                side_effect_level="high",
            ),
        )

    def _act_wait_window(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        name = str(args.get("name") or args.get("app_name") or args.get("query") or "").strip()
        timeout = float(args.get("timeout") or 4.0)
        deadline = time.time() + max(0.2, min(timeout, 20.0))
        found = None
        while time.time() < deadline:
            found = self.store.get(name) if name else self.store.get_active()
            if found is not None and found.exists:
                break
            if isinstance(self.store, FakeWindowStore):
                break
            time.sleep(0.15)
        after = self.inspect()
        ok = found is not None and found.exists
        return _result(
            f"{name} jest otwarte." if ok else f"Nie doczekałem się okna {name}.",
            ok=ok,
            tool="wait_for_window",
            observation=_obs(
                tool="wait_for_window",
                action="wait_for_window",
                target=name,
                success=ok,
                verification_level=VERIFY_STRONG if ok else VERIFY_NONE,
                state_after=after.planner_fragment(),
            ),
        )

    def _act_exists(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        name = str(args.get("name") or args.get("app_name") or args.get("query") or "").strip()
        found = self.store.get(name) if name else None
        exists = found is not None and found.exists
        label = name or "ta aplikacja"
        msg = f"Tak, {label} jest otwarty." if exists else f"Nie, {label} nie jest teraz otwarty."
        return _result(
            msg,
            ok=True,
            tool="window_exists",
            observation=_obs(
                tool="window_exists",
                action="window_exists",
                target=name,
                success=True,
                verification_level=VERIFY_STRONG,
                evidence={"exists": exists},
                side_effect_level="none",
            ),
            data={"exists": exists, "source_kind": "LOCAL_COMPUTER"},
        )

    def _act_inspect(
        self, before: ComputerObservation, args: Dict[str, Any], use_vision: bool
    ) -> ToolResult:
        after = self.inspect(use_vision=use_vision)
        names = [
            str(el.get("name") or el.get("text") or el.get("element_id"))
            for el in after.visible_elements[:8]
        ]
        editor = ""
        for el in after.visible_elements:
            role = str(el.get("role") or "").lower()
            if role in ("document", "textbox", "edit", "editor") and (el.get("value") or el.get("text")):
                if el.get("is_password"):
                    continue
                editor = str(el.get("value") or el.get("text") or "")
                break
        try:
            from core.jarvis_response import observation_from_screen
            from core.perception.screen_runtime import get_screen_runtime

            msg = observation_from_screen(
                get_screen_runtime().get_state(),
                extra={
                    "title": after.window_title,
                    "process": after.app_process or after.app_name,
                    "editor_text": editor,
                },
            )
        except Exception:
            msg = ""
        if not msg:
            if after.window_title:
                msg = f"Aktywne okno: {after.window_title}."
                if names:
                    msg += " Widzę: " + ", ".join(names[:5]) + "."
            else:
                msg = "Zrobiłem obserwację ekranu."
        return _result(
            msg,
            ok=True,
            tool="inspect_ui",
            observation=_obs(
                tool="inspect_ui",
                action="inspect_ui",
                success=True,
                verification_level=VERIFY_STRONG if after.ui_tree_available else VERIFY_BASIC,
                state_after=after.planner_fragment(),
                side_effect_level="none",
            ),
            data={"observation": after.to_dict(), "snapshot_id": after.snapshot_id},
        )

    def _act_find(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        query = str(args.get("query") or args.get("text") or args.get("name") or "").strip()
        found = self.find_element(query, for_action=False)
        if not found.get("ok") and not found.get("candidates"):
            return _result(
                found.get("message") or "Nie znalazłem elementu.",
                ok=False,
                tool="find_ui_element",
                observation=_obs(
                    tool="find_ui_element",
                    action="find_ui_element",
                    target=query,
                    success=False,
                    verification_level=VERIFY_NONE,
                    error="missing",
                ),
            )
        ambiguous = bool(found.get("clarify"))
        return _result(
            found.get("message") or "Znalazłem element.",
            ok=True,
            tool="find_ui_element",
            observation=_obs(
                tool="find_ui_element",
                action="find_ui_element",
                target=query,
                success=True,
                verification_level=VERIFY_BASIC,
                extra={"ambiguous": ambiguous},
            ),
            data={
                "ambiguous": ambiguous,
                "candidates": found.get("candidates") or [],
                "element": found.get("element"),
                "confidence": found.get("confidence"),
            },
        )

    def _act_click(
        self,
        before: ComputerObservation,
        args: Dict[str, Any],
        *,
        clicks: int = 1,
        button: str = "left",
    ) -> ToolResult:
        tool = "click_ui_element"
        if clicks == 2:
            tool = "double_click_ui_element"
        if button == "right":
            tool = "right_click_ui_element"
        element_id = str(args.get("element_id") or args.get("id") or "").strip()
        query = str(args.get("query") or args.get("name") or args.get("text") or "").strip()
        attempt = int(args.get("attempt_id") or 1)
        if self.snapshot_stale(element_id):
            print("[VERIFY] expected=fresh_snapshot observed=stale status=reobserve")
            self.inspect()
            element_id = ""
            if attempt > MAX_REPAIR_PER_STEP:
                return _result(
                    "Ekran się zmienił — potrzebuję świeżej obserwacji.",
                    ok=False,
                    tool=tool,
                    observation=_obs(
                        tool=tool,
                        action="click",
                        success=False,
                        verification_level=VERIFY_NONE,
                        error="stale_snapshot",
                    ),
                    error="stale_snapshot",
                    data={"needs_refresh": True, "clicked": False},
                )
        target = None
        if element_id:
            for el in (self.ui_context.visible_elements or []):
                if el.get("element_id") == element_id or el.get("id") == element_id:
                    target = el
                    break
            if target is None:
                return _result(
                    "Element pochodzi ze starego zrzutu. Odświeżam obserwację.",
                    ok=False,
                    tool=tool,
                    observation=_obs(
                        tool=tool,
                        action="click",
                        success=False,
                        verification_level=VERIFY_NONE,
                        error="stale_element",
                    ),
                    error="stale_element",
                    data={"needs_refresh": True, "clicked": False},
                )
            conf = float(target.get("confidence") or 0.0)
            if conf and conf < CONFIDENCE_LOCK:
                return _result(
                    "Nie jestem pewien, który element masz na myśli.",
                    ok=False,
                    tool=tool,
                    observation=_obs(
                        tool=tool,
                        action="click",
                        success=False,
                        verification_level=VERIFY_NONE,
                        error="low_confidence",
                    ),
                    error="low_confidence",
                    data={"clicked": False, "confidence": conf, "needs_clarification": True},
                )
        else:
            if re.search(r"\b(?:drug|poprzedni|to pole|ten przycisk)\b", query, re.I):
                ref = self.resolve_referent(query)
                if not ref.get("ok"):
                    return _result(
                        ref.get("message") or "Który element masz na myśli?",
                        ok=False,
                        tool=tool,
                        observation=_obs(
                            tool=tool,
                            action="click",
                            success=False,
                            verification_level=VERIFY_NONE,
                            error="ambiguous",
                        ),
                        data={"clicked": False, "needs_clarification": True},
                    )
                target = ref.get("element")
            found = self.find_element(query or " ", for_action=True) if (query or not target) else {"ok": True, "element": target}
            if not found.get("ok"):
                if found.get("clarify"):
                    return _result(
                        found.get("message") or "Który przycisk masz na myśli?",
                        ok=False,
                        tool=tool,
                        observation=_obs(
                            tool=tool,
                            action="click",
                            success=False,
                            verification_level=VERIFY_NONE,
                            error=str(found.get("reason") or "clarify"),
                        ),
                        data={
                            "clicked": False,
                            "needs_clarification": True,
                            "candidates": found.get("candidates") or [],
                        },
                    )
                return _result(
                    found.get("message") or "Nie znalazłem przycisku.",
                    ok=False,
                    tool=tool,
                    observation=_obs(
                        tool=tool,
                        action="click",
                        success=False,
                        verification_level=VERIFY_NONE,
                        error="missing",
                    ),
                    data={"clicked": False},
                )
            target = found.get("element") or target
        if not target:
            return _result(
                "Nie kliknąłem — nie mam pewnego elementu.",
                ok=False,
                tool=tool,
                observation=_obs(
                    tool=tool, action="click", success=False, verification_level=VERIFY_NONE, error="missing"
                ),
                data={"clicked": False},
            )
        name = str(target.get("name") or "")
        if UAC_PATTERNS.search(name):
            return _result(
                "Nie omijam zabezpieczeń Windows.",
                ok=False,
                tool=tool,
                observation=_obs(
                    tool=tool, action="click", success=False, verification_level=VERIFY_STRONG, error="uac"
                ),
            )
        if re.search(r"nie\s+zapisuj|don'?t\s+save", name, re.I):
            _METRICS["unsaved_discard_without_confirmation"] += 1
            return _result(
                "Nie zamknę bez zapisu bez Twojego potwierdzenia.",
                ok=False,
                tool=tool,
                observation=_obs(
                    tool=tool,
                    action="click",
                    success=False,
                    verification_level=VERIFY_STRONG,
                    error="unsaved_discard_blocked",
                ),
                data={"clicked": False, "waiting": WAITING_CONFIRMATION},
            )
        blocked = self._visual_click_block(target)
        if blocked is not None:
            return blocked
        dispatched = self._dispatch_click(target, clicks=clicks, button=button)
        self.ui_context.last_clicked_element = str(target.get("element_id") or "")
        after = self.inspect()
        if after.modal.get("kind") == "unsaved":
            return _result(
                "Pojawiło się pytanie o zapis zmian. Co mam zrobić?",
                ok=False,
                tool=tool,
                observation=_obs(
                    tool=tool,
                    action="click",
                    success=False,
                    verification_level=VERIFY_STRONG,
                    extra={"waiting": WAITING_CONFIRMATION},
                    error="unsaved",
                ),
                status=ToolStatus.NEEDS_CONFIRMATION,
                data={"clicked": True, "waiting": WAITING_CONFIRMATION},
            )
        print(
            f"[VERIFY] expected=click:{target.get('element_id')} "
            f"observed={dispatched} status={'verified' if dispatched else 'failed'}"
        )
        return _result(
            "Gotowe." if dispatched else "Nie udało mi się kliknąć.",
            ok=bool(dispatched),
            tool=tool,
            observation=_obs(
                tool=tool,
                action="click",
                target=str(target.get("element_id") or ""),
                success=bool(dispatched),
                verification_level=VERIFY_STRONG if dispatched else VERIFY_NONE,
                evidence={"element": target.get("element_id"), "clicked": dispatched},
                reversible=False,
            ),
            data={"clicked": bool(dispatched), "element_id": target.get("element_id")},
        )

    def _dispatch_click(self, target: Dict[str, Any], *, clicks: int, button: str) -> bool:
        if isinstance(self.store, FakeWindowStore):
            ui = getattr(self, "_fake_ui", None)
            if ui is not None:
                from vision.models import Bounds, UIElement

                el = UIElement(
                    id=str(target.get("element_id") or "el"),
                    role=str(target.get("role") or "button"),
                    text=str(target.get("name") or ""),
                    bounds=Bounds(10, 10, 40, 20),
                    source="fake",
                    confidence=float(target.get("confidence") or 1.0),
                )
                if button == "right" and hasattr(ui, "right_click"):
                    return bool(ui.right_click(el))
                if clicks == 2 and hasattr(ui, "double_click"):
                    return bool(ui.double_click(el))
                return bool(ui.click(el))
            return True
        try:
            from vision.session import get_vision_session

            session = get_vision_session()
            eid = str(target.get("element_id") or "")
            if session.current is None:
                session.refresh(force=True, use_vision=False, use_ocr=False)
            from vision.actions import click_element

            if clicks == 1 and button == "left":
                result = click_element(session, eid, confirmed=True, reobserve=False)
                return bool(result.success)
            ui = session.ui
            element = session.current.get_element(eid) if session.current else None
            if element is None:
                return False
            if button == "right" and hasattr(ui, "right_click"):
                return bool(ui.right_click(element))
            if clicks == 2 and hasattr(ui, "double_click"):
                return bool(ui.double_click(element))
            return bool(ui.click(element))
        except Exception as exc:
            print(f"[COMPUTER] click failed: {exc}")
            return False

    def _keyboard_blocked(
        self,
        *,
        tool: str,
        action: str,
        message: str,
        error: str,
        expected: str = "",
        actual: str = "",
        clarify: bool = False,
    ) -> ToolResult:
        payload = {
            "target_verified": False,
            "expected_target": expected,
            "actual_target": actual,
            "target_mismatch": bool(expected and actual and expected != actual),
            "keys_sent": False,
        }
        if clarify:
            payload["needs_clarification"] = True
        return _result(
            message,
            ok=False,
            tool=tool,
            observation=_obs(
                tool=tool,
                action=action,
                target=expected,
                success=False,
                verification_level=VERIFY_NONE,
                extra=payload,
                error=error,
            ),
            data=payload,
            error=error,
        )

    def _resolve_keyboard_app(self, args: Dict[str, Any]) -> Tuple[str, bool]:
        explicit = str(args.get("app_name") or args.get("name") or "").strip()
        if explicit:
            return explicit, True
        working = str(
            self.plan_working_app
            or args.get("plan_working_app")
            or self.last_working_app
            or ""
        ).strip()
        if working:
            return working, False
        return "", False

    def _ensure_keyboard_target(
        self,
        args: Dict[str, Any],
        *,
        action: str,
        tool: str,
    ) -> Tuple[str, Optional[ToolResult]]:
        expected, explicit = self._resolve_keyboard_app(args)
        goal = str(args.get("user_goal") or self.current_plan.get("goal") or args.get("query") or "")
        active = self.store.get_active()
        actual_name = ""
        if active is not None:
            actual_name = f"{active.app_name}|{active.process_name}"

        if not expected:
            windows = [w for w in self.store.list_windows() if w.exists and w.visible]
            if len(windows) > 1:
                return "", self._keyboard_blocked(
                    tool=tool,
                    action=action,
                    message="W którym oknie mam to zrobić?",
                    error="ambiguous_target",
                    actual=actual_name,
                    clarify=True,
                )
            if active is not None and is_ide_window(active) and not goal_mentions_app(goal, active.app_name or "cursor"):
                return "", self._keyboard_blocked(
                    tool=tool,
                    action=action,
                    message="To trafiłoby do edytora. Wskaż okno, na przykład Notatnik.",
                    error="ide_self_protect",
                    actual=actual_name,
                    clarify=True,
                )
            if active is None:
                return "", self._keyboard_blocked(
                    tool=tool,
                    action=action,
                    message="Nie mam pewnego okna — nie wykonuję tej czynności.",
                    error="no_target",
                    clarify=True,
                )
            expected = active.app_name
            explicit = False

        if is_ide_window(self.store.get(expected) or active) and not (
            explicit or goal_mentions_app(goal, expected)
        ):
            return "", self._keyboard_blocked(
                tool=tool,
                action=action,
                message="To trafiłoby do edytora. Wskaż okno, na przykład Notatnik.",
                error="ide_self_protect",
                expected=expected,
                actual=actual_name,
                clarify=True,
            )

        focused = self._focus_named_app(expected)
        after = self.store.get_active()
        if not focused or after is None or not window_matches_app(after, expected):
            return expected, self._keyboard_blocked(
                tool=tool,
                action=action,
                message="Nie mam pewnego okna — nie wysyłam skrótu.",
                error="focus_failed",
                expected=expected,
                actual=(f"{after.app_name}|{after.process_name}" if after else ""),
            )
        editor = self._editor_element(expected)
        if editor is None and action in KEYBOARD_EDIT_ACTIONS:
            return expected, self._keyboard_blocked(
                tool=tool,
                action=action,
                message="Nie znalazłem pola edycji w oczekiwanym oknie.",
                error="no_editor",
                expected=expected,
                actual=after.app_name,
            )
        if editor is not None:
            self.plan_working_element = str(editor.get("element_id") or self.plan_working_element)
        self.bind_working_target(expected, window=after)
        print(
            f"[COMPUTER] keyboard target expected={expected} "
            f"actual={after.app_name} process={after.process_name} verified=1"
        )
        return expected, None

    def _focus_named_app(self, app_name: str) -> bool:
        name = str(app_name or "").strip()
        if not name:
            return False
        info = self.store.get(name)
        if info is None:
            return False
        if info.process_name.lower().startswith("calc") and "notat" in name.lower():
            return False
        active = self.store.get_active()
        if (
            active is not None
            and window_matches_app(active, name)
            and str(active.window_id) == str(info.window_id)
        ):
            self.active_window = ActiveWindowContext.from_window(active)
            return True
        ok = self.store.focus(info.window_id)
        active = self.store.get_active()
        if active is None or not window_matches_app(active, name):
            _METRICS["wrong_target"] += 1
            return False
        self.active_window = ActiveWindowContext.from_window(active)
        return bool(ok)

    def _editor_element(self, app_name: str = "", *, refocus: bool = True) -> Optional[Dict[str, Any]]:
        if app_name and refocus:
            active = self.store.get_active()
            if active is None or not window_matches_app(active, app_name):
                if not self._focus_named_app(app_name):
                    return None
        expected_el = str(self.plan_working_element or "")
        if self.editor_handle_valid(expected_el or (self.editor_handle.element_id if self.editor_handle else "")):
            handle = self.editor_handle
            obs = self.last_observation
            if obs is not None:
                for el in obs.visible_elements:
                    if str(el.get("element_id") or "") == handle.element_id:
                        return el
            return {
                "element_id": handle.element_id,
                "role": "document",
                "automation_id": handle.automation_id,
                "enabled": True,
                "visible": True,
                "focused": True,
            }
        active = self.store.get_active()
        obs = self.last_observation
        obs_win = ""
        if obs is not None and obs.active_window is not None:
            obs_win = str(obs.active_window.window_id or "")
        window_mismatch = bool(
            active is not None and obs_win and obs_win != str(active.window_id or "")
        )
        if (
            obs is None
            or window_mismatch
            or (
                expected_el
                and not self._element_resolvable(expected_el)
                and not self._snapshot_reusable(expected_el)
            )
        ):
            obs = self.inspect(
                mode="targeted",
                expected_element_id=expected_el,
            )
        for el in obs.visible_elements:
            role = str(el.get("role") or "").lower()
            if role in ("document", "textbox", "edit", "input") and el.get("enabled", True):
                return el
        if obs.focused_element:
            return obs.focused_element
        if self.ui_context.last_typed_element:
            for el in obs.visible_elements:
                if el.get("element_id") == self.ui_context.last_typed_element:
                    return el
        if self.plan_working_element:
            for el in obs.visible_elements:
                if el.get("element_id") == self.plan_working_element:
                    return el
        return None

    def _act_type(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        text = str(args.get("text") or args.get("value") or args.get("text_payload") or "").rstrip()
        append = bool(args.get("append") or (args.get("slots") or {}).get("append"))
        if not text:
            return _result(
                "Jaki tekst mam wpisać?",
                ok=False,
                tool="type_text",
                observation=_obs(
                    tool="type_text", action="type_text", success=False, verification_level=VERIFY_NONE, error="missing_text"
                ),
            )
        expected, blocked = self._ensure_keyboard_target(args, action="type_text", tool="type_text")
        if blocked is not None:
            return blocked
        editor = self._editor_element(expected, refocus=False)
        if editor is None:
            return self._keyboard_blocked(
                tool="type_text",
                action="type_text",
                message="Otworzyłem okno, ale nie znalazłem pewnie pola edycji." if expected else "Nie znalazłem pola edycji.",
                error="no_editor",
                expected=expected,
            )
        if editor.get("is_password") or PASSWORD_ROLE.search(str(editor.get("role") or "") + str(editor.get("name") or "")):
            print("[COMPUTER] password field — not reading value")
        already = self._read_editor_value(editor, self.last_observation)
        if text and text in str(already or "") and (
            self.lifecycle == LIFECYCLE_UNKNOWN
            or bool(getattr(self, "_skip_duplicate_type", False))
            or bool(args.get("side_effect_may_have_occurred"))
        ):
            level = VERIFY_STRONG
            result = _result(
                "Gotowe.",
                ok=True,
                tool="type_text",
                observation=_obs(
                    tool="type_text",
                    action="type_text",
                    target=str(editor.get("element_id") or expected),
                    success=True,
                    verification_level=level,
                    extra={"target_verified": True, "resolved_after_uncertain": True},
                ),
                data={
                    "typed": True,
                    "verified": True,
                    "verification_level": level,
                    "target_verified": True,
                    "resolved_after_uncertain": True,
                },
            )
            self.lifecycle = LIFECYCLE_SUCCEEDED
            self.commit_verified(result)
            return result
        hook = getattr(self, "_block_before_action", None)
        if callable(hook):
            hook()
        if self.deadline_exceeded() and not self._action_started:
            self.lifecycle = LIFECYCLE_CANCELLED
            return _result(
                "Nie zdążyłem wpisać tekstu.",
                ok=False,
                tool="type_text",
                observation=_obs(
                    tool="type_text",
                    action="type_text",
                    success=False,
                    verification_level=VERIFY_NONE,
                    error="timeout",
                    extra={"side_effect_may_have_occurred": False},
                ),
                error="timeout",
            )
        t_action = time.perf_counter()
        self._action_started = True
        self._typed_payload = text
        typed = self._dispatch_type(editor, text, append=append)
        self._perf["action_ms"] = (time.perf_counter() - t_action) * 1000.0
        after_hook = getattr(self, "_block_after_action", None)
        if callable(after_hook):
            after_hook()
        t_verify = time.perf_counter()
        active = self._cheap_active()
        if typed and (active is None or not window_matches_app(active, expected)):
            _METRICS["wrong_window_keyboard_action"] += 1
            _METRICS["wrong_target"] += 1
            return self._keyboard_blocked(
                tool="type_text",
                action="type_text",
                message="Tekst nie trafił do oczekiwanego okna.",
                error="wrong_window",
                expected=expected,
                actual=(active.app_name if active else ""),
            )
        observed = self._read_editor_value(editor, self.last_observation)
        self._perf["verify_ms"] = (time.perf_counter() - t_verify) * 1000.0
        verified = bool(typed and observed and text in str(observed))
        target_ok = bool(typed and active is not None and window_matches_app(active, expected))
        if verified and target_ok:
            level = VERIFY_STRONG
        elif typed and target_ok:
            level = VERIFY_BASIC
        else:
            level = VERIFY_NONE
        print(f"[VERIFY] expected={text[:40]!r} observed={(str(observed)[:40] if observed else '')!r} status={level}")
        if self.deadline_exceeded() and self._action_started and not verified:
            if typed and target_ok and text in str(observed or ""):
                verified = True
                level = VERIFY_STRONG
            elif typed:
                data = {
                    "typed": True,
                    "verified": False,
                    "verification_level": VERIFY_NONE,
                    "target_verified": target_ok,
                    "side_effect_may_have_occurred": True,
                }
                self.lifecycle = LIFECYCLE_UNKNOWN
                return ToolResult.unknown_side_effect(
                    "Nie potwierdziłem skutku wpisywania tekstu.",
                    tool_name="type_text",
                    data={
                        **data,
                        "computer_observation": _obs(
                            tool="type_text",
                            action="type_text",
                            target=str(editor.get("element_id") or expected),
                            success=False,
                            verification_level=VERIFY_NONE,
                            extra={"side_effect_may_have_occurred": True, "target_verified": target_ok},
                        ),
                    },
                )
        if verified and target_ok:
            msg = "Gotowe."
        elif typed and target_ok:
            msg = "Wysłałem tekst, ale nie mogę pewnie potwierdzić zawartości pola."
        else:
            msg = "Nie udało mi się wpisać tekstu."
        # Honesty: typed+target without content proof is PARTIAL / unknown, never SUCCESS.
        if typed and target_ok and not verified:
            self.lifecycle = LIFECYCLE_UNKNOWN
            return ToolResult.unknown_side_effect(
                msg,
                tool_name="type_text",
                data={
                    "typed": True,
                    "verified": False,
                    "verification_level": level,
                    "target_verified": target_ok,
                    "side_effect_may_have_occurred": True,
                    "expected_target": expected,
                    "actual_target": active.app_name if active else "",
                    "computer_observation": _obs(
                        tool="type_text",
                        action="type_text",
                        target=str(editor.get("element_id") or expected),
                        success=False,
                        verification_level=level,
                        evidence={"requested": text, "observed": "" if editor.get("is_password") else observed},
                        extra={
                            "side_effect_may_have_occurred": True,
                            "target_verified": target_ok,
                            "expected_target": expected,
                            "actual_target": active.app_name if active else "",
                        },
                    ),
                },
            )
        if typed and target_ok:
            self.ui_context.last_typed_element = str(editor.get("element_id") or "")
            self._last_typed = (str(observed) if observed else ((self._last_typed + text) if append else text))
            self.bind_working_target(expected, window=active)
            self.cache_editor_handle(editor, active)
        result = _result(
            msg,
            ok=bool(typed and target_ok),
            tool="type_text",
            observation=_obs(
                tool="type_text",
                action="type_text",
                target=str(editor.get("element_id") or expected),
                success=bool(typed and target_ok),
                verification_level=level,
                evidence={"requested": text, "observed": "" if editor.get("is_password") else observed},
                extra={
                    "target_verified": target_ok,
                    "expected_target": expected,
                    "actual_target": active.app_name if active else "",
                },
            ),
            data={
                "typed": bool(typed and target_ok),
                "verified": verified,
                "verification_level": level,
                "target_verified": target_ok,
                "expected_target": expected,
            },
        )
        if typed and target_ok:
            self.commit_verified(result)
            self._run_optional_diagnostics()
        return result

    def _dispatch_type(self, editor: Dict[str, Any], text: str, *, append: bool) -> bool:
        if isinstance(self.store, FakeWindowStore):
            active = self.store.get_active()
            if active is None:
                return False
            rec = self.store.record(active.window_id)
            if rec is None:
                return False
            rec.editor_text = (rec.editor_text + text) if append else text
            rec.unsaved = True
            for el in rec.elements:
                if el.get("element_id") == editor.get("element_id") or el.get("role") in ("document", "textbox"):
                    el["value"] = rec.editor_text
            ui = getattr(self, "_fake_ui", None)
            if ui is not None:
                ui.type_text(_as_ui_element(editor), text)
            self.store.typed_into.append(active.window_id)
            return True
        try:
            from vision.session import get_vision_session

            session = get_vision_session()
            eid = str(editor.get("element_id") or "")
            ui = getattr(session, "ui", None)
            if ui is not None:
                el = None
                if session.current is not None:
                    try:
                        from vision.actions import resolve_element

                        el, _err = resolve_element(session, eid)
                    except Exception:
                        el = None
                if el is None:
                    el = _as_ui_element(editor)
                return bool(ui.type_text(el, text))
            from vision.actions import type_into_element

            if session.current is None:
                session.refresh(force=False, use_vision=False, use_ocr=False, use_uia=True)
            result = type_into_element(session, eid, text, confirmed=True, reobserve=False)
            return bool(result.success)
        except Exception as exc:
            print(f"[COMPUTER] type failed: {exc}")
            return False

    def _read_editor_value(self, editor: Dict[str, Any], after: Optional[ComputerObservation] = None) -> str:
        if isinstance(self.store, FakeWindowStore):
            active = self.store.get_active()
            rec = self.store.record(active.window_id) if active else None
            if rec is not None:
                return rec.editor_text
        t0 = time.perf_counter()
        try:
            from vision.session import get_vision_session

            session = get_vision_session()
            ui = getattr(session, "ui", None)
            if ui is not None and hasattr(ui, "read_value"):
                value = str(ui.read_value(_as_ui_element(editor)) or "")
                self._perf["verify_ms"] = float(self._perf.get("verify_ms") or 0) + (
                    (time.perf_counter() - t0) * 1000.0
                )
                if value:
                    return value
        except Exception:
            pass
        obs = after or self.last_observation
        if obs is not None:
            for el in obs.visible_elements:
                if el.get("element_id") == editor.get("element_id"):
                    if el.get("is_password"):
                        return ""
                    return str(el.get("value") or el.get("text") or "")
        return str(editor.get("value") or "")

    def _act_press(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        keys = str(args.get("keys") or args.get("key") or args.get("query") or "").strip().lower()
        action = str(args.get("semantic_action") or "")
        if action == "select_all":
            keys = "ctrl+a"
        if action == "undo":
            keys = "ctrl+z"
        if action == "copy":
            keys = "ctrl+c"
        if action == "paste":
            keys = "ctrl+v"
        if action == "cut":
            keys = "ctrl+x"
        expected, blocked = self._ensure_keyboard_target(
            args, action=action or "hotkey", tool="press_key"
        )
        if blocked is not None:
            return blocked
        hook = getattr(self, "_block_before_action", None)
        if callable(hook):
            hook()
        if self.deadline_exceeded() and not self._action_started:
            return _result(
                "Nie zdążyłem wysłać skrótu.",
                ok=False,
                tool="press_key",
                observation=_obs(
                    tool="press_key",
                    action=action or "hotkey",
                    success=False,
                    verification_level=VERIFY_NONE,
                    error="timeout",
                    extra={"side_effect_may_have_occurred": False},
                ),
                error="timeout",
            )
        t_action = time.perf_counter()
        self._action_started = True
        ok = self._dispatch_key(keys)
        self._perf["action_ms"] = (time.perf_counter() - t_action) * 1000.0
        t_verify = time.perf_counter()
        active = self._cheap_active()
        window_ok = bool(active is not None and window_matches_app(active, expected))
        if ok and not window_ok:
            _METRICS["wrong_window_keyboard_action"] += 1
            _METRICS["wrong_target"] += 1
            return self._keyboard_blocked(
                tool="press_key",
                action=action or "hotkey",
                message="Skrót nie trafił do oczekiwanego okna.",
                error="wrong_window",
                expected=expected,
                actual=(active.app_name if active else ""),
            )
        rec = None
        if isinstance(self.store, FakeWindowStore) and active is not None:
            rec = self.store.record(active.window_id)
        selection = False
        if keys == "ctrl+a" and rec is not None and getattr(rec, "supports_selection", True):
            rec.selected = True
            selection = True
        if keys == "ctrl+z" and rec is not None and rec.editor_text:
            rec.editor_text = rec.editor_text[: -max(1, len(self._last_typed) or 1)]
        editor = self._editor_element(expected, refocus=False) if window_ok else None
        editor_ok = editor is not None
        self._perf["verify_ms"] = (time.perf_counter() - t_verify) * 1000.0
        if selection and window_ok and editor_ok:
            level = VERIFY_STRONG
        elif ok and window_ok and editor_ok:
            level = VERIFY_BASIC
        else:
            level = VERIFY_NONE
        success = bool(ok and window_ok and editor_ok)
        if success:
            self.bind_working_target(expected, window=active)
        spoken = {
            "ctrl+a": "Gotowe.",
            "ctrl+z": "Jest.",
            "ctrl+c": "Gotowe.",
            "ctrl+v": "Gotowe.",
            "enter": "Gotowe.",
        }.get(keys, "Gotowe." if success else "Nie wysłałem skrótu.")
        if not success:
            spoken = "Nie potwierdziłem skrótu w oczekiwanym oknie."
        extra = {
            "target_verified": bool(window_ok and editor_ok),
            "expected_target": expected,
            "actual_target": active.app_name if active else "",
            "keys_sent": bool(ok),
            "selection_observed": selection,
        }
        print(
            f"[VERIFY] hotkey={keys} expected={expected} "
            f"actual={active.app_name if active else '-'} level={level}"
        )
        result = _result(
            spoken,
            ok=success,
            tool="press_key",
            observation=_obs(
                tool="press_key",
                action=action or "hotkey",
                target=expected,
                success=success,
                verification_level=level,
                reversible=keys in ("ctrl+z",),
                extra=extra,
            ),
            data={"keys": keys, "verification_level": level, **extra},
        )
        if success:
            self.commit_verified(result)
            self._run_optional_diagnostics()
        return result

    def _dispatch_key(self, keys: str) -> bool:
        if isinstance(self.store, FakeWindowStore):
            active = self.store.get_active()
            self.store.last_hotkey = keys
            self.store.last_hotkey_window = active.window_id if active else ""
            self.store.last_hotkey_process = active.process_name if active else ""
            ui = getattr(self, "_fake_ui", None)
            if ui is not None:
                return bool(ui.press_key(keys))
            return True
        try:
            from vision.session import get_vision_session
            from vision.actions import press_key

            return bool(press_key(get_vision_session(), keys, reobserve=False).success)
        except Exception:
            return False

    def _act_delete_last_word(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        expected, blocked = self._ensure_keyboard_target(
            args, action="delete_last_word", tool="type_text"
        )
        if blocked is not None:
            return blocked
        editor = self._editor_element(expected)
        if editor is None:
            return _result(
                "Nie mam aktywnego pola tekstu.",
                ok=False,
                tool="type_text",
                observation=_obs(
                    tool="type_text", action="delete_last_word", success=False, verification_level=VERIFY_NONE
                ),
            )
        current = self._read_editor_value(editor, before)
        trimmed = re.sub(r"\s+\S+\s*$", "", str(current or "")).rstrip()
        if isinstance(self.store, FakeWindowStore):
            active = self.store.get_active()
            rec = self.store.record(active.window_id) if active else None
            if rec is not None:
                rec.editor_text = trimmed
        else:
            self._dispatch_key("ctrl+backspace") if False else self._dispatch_key("ctrl+shift+left")
            self._dispatch_key("backspace")
        after = self.inspect()
        observed = self._read_editor_value(editor, after)
        ok = observed == trimmed if observed else True
        return _result(
            "Gotowe." if ok else "Nie usunąłem ostatniego słowa.",
            ok=ok,
            tool="type_text",
            observation=_obs(
                tool="type_text",
                action="delete_last_word",
                success=ok,
                verification_level=VERIFY_STRONG if observed else VERIFY_BASIC,
            ),
        )

    def _act_scroll(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        direction = str(args.get("direction") or "down")
        amount = args.get("amount") or args.get("scroll_amount") or 3
        if str(amount) in ("small", "trochę", "troche"):
            amount = 1
        if str(amount) in ("end", "bottom"):
            amount = 12
        try:
            amount_i = int(amount)
        except (TypeError, ValueError):
            amount_i = 3
        if isinstance(self.store, FakeWindowStore):
            self.store.scrolls.append((direction, amount_i))
            ok = True
        else:
            try:
                from vision.session import get_vision_session
                from vision.actions import scroll_screen

                ok = bool(scroll_screen(get_vision_session(), direction=direction, amount=amount_i).success)
            except Exception:
                ok = False
        after = self.inspect()
        return _result(
            "Gotowe." if ok else "Nie przewinęło się.",
            ok=ok,
            tool="scroll",
            observation=_obs(
                tool="scroll",
                action="scroll",
                success=ok,
                verification_level=VERIFY_BASIC,
                state_after=after.planner_fragment(),
                reversible=True,
            ),
            data={"direction": direction, "amount": amount_i},
        )

    def _act_drag(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        src = str(args.get("element_id") or args.get("from_id") or "").strip()
        dst = str(args.get("to_element_id") or args.get("to_id") or "").strip()
        if not src or not dst:
            return _result(
                "Potrzebuję pewnego początku i celu przeciągnięcia.",
                ok=False,
                tool="drag_element",
                observation=_obs(
                    tool="drag_element", action="drag", success=False, verification_level=VERIFY_NONE, error="unresolved"
                ),
                data={"dragged": False},
            )
        start = self.find_element(src, for_action=True)
        end = self.find_element(dst, for_action=True)
        if not start.get("ok") or not end.get("ok"):
            return _result(
                "Nie przeciągam — nie mam pewnych elementów.",
                ok=False,
                tool="drag_element",
                observation=_obs(
                    tool="drag_element", action="drag", success=False, verification_level=VERIFY_NONE, error="low_confidence"
                ),
                data={"dragged": False},
            )
        if isinstance(self.store, FakeWindowStore):
            self.store.drags.append((src, dst))
            ok = True
        else:
            ok = False
        return _result(
            "Gotowe." if ok else "Nie przeciągnąłem.",
            ok=ok,
            tool="drag_element",
            observation=_obs(
                tool="drag_element",
                action="drag",
                success=ok,
                verification_level=VERIFY_BASIC if ok else VERIFY_NONE,
                reversible=False,
                side_effect_level="medium",
            ),
            data={"dragged": ok},
        )

    def _act_wait_element(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        query = str(args.get("query") or args.get("name") or "").strip()
        timeout = float(args.get("timeout") or 3.0)
        deadline = time.time() + max(0.1, min(timeout, 15.0))
        found = None
        while time.time() < deadline:
            self.inspect()
            hit = self.find_element(query, for_action=True)
            if hit.get("ok"):
                found = hit.get("element")
                break
            if isinstance(self.store, FakeWindowStore):
                break
            time.sleep(0.1)
        return _result(
            "Jest." if found else f"Nie doczekałem się elementu {query}.",
            ok=bool(found),
            tool="wait_for_element",
            observation=_obs(
                tool="wait_for_element",
                action="wait_for_element",
                target=query,
                success=bool(found),
                verification_level=VERIFY_STRONG if found else VERIFY_NONE,
            ),
        )

    def _act_wait_state(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        return self._act_wait_window(before, args)

    def _act_open_app(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        name = str(args.get("name") or args.get("app_name") or args.get("query") or "").strip()
        existing = self.store.get(name) if name else None
        if existing is not None and existing.exists:
            self.store.focus(existing.window_id)
            after = self.inspect(use_vision=False, mode="cheap")
            self.bind_working_target(name, window=existing)
            return _result(
                f"{existing.app_name or name} jest już otwarty.",
                ok=True,
                tool="focus_window",
                observation=_obs(
                    tool="focus_window",
                    action="focus_or_open",
                    target=name,
                    success=True,
                    verification_level=VERIFY_STRONG,
                    state_after=after.planner_fragment(),
                    extra={"duplicate": False, "focused_existing": True},
                    reversible=True,
                ),
                data={"focused_existing": True, "duplicate": False},
            )
        launched = self.store.open_app(name)
        wait = self._act_wait_window(before, {"name": name, "timeout": args.get("timeout") or 4})
        after = self.last_observation or self.inspect()
        found = self.store.get(name)
        if found is not None:
            self._opened_ids.add(found.window_id)
            self.store.mark_opened_by_elian(found.window_id)
        ok = bool((launched or {}).get("ok") and found is not None)
        if ok and found is not None:
            self.bind_working_target(name, window=found)
        print(f"[VERIFY] expected=open:{name} observed={found.app_name if found else None} status={'verified' if ok else 'failed'}")
        return _result(
            f"{name} otwarte." if ok else wait.message,
            ok=ok,
            tool="open_app",
            observation=_obs(
                tool="open_app",
                action="open_app",
                target=name,
                success=ok,
                verification_level=VERIFY_STRONG if ok else VERIFY_NONE,
                state_before={"present": False},
                state_after=after.planner_fragment(),
                reversible=False,
                side_effect_level="medium",
            ),
        )

    def _act_find_file(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        query = str(args.get("query") or args.get("name") or "").strip()
        try:
            import ComputerControl

            results = ComputerControl.find_files(query, limit=5) if query else []
        except Exception:
            results = []
        if len(results) > 1:
            return _result(
                "Znalazłem kilka plików. Który otworzyć?",
                ok=False,
                tool="find_file",
                observation=_obs(
                    tool="find_file",
                    action="find_file",
                    success=False,
                    verification_level=VERIFY_STRONG,
                    error="ambiguous",
                ),
                data={"candidates": list(results), "needs_clarification": True},
            )
        if not results:
            return _result(
                "Nie znalazłem pliku.",
                ok=False,
                tool="find_file",
                observation=_obs(
                    tool="find_file", action="find_file", success=False, verification_level=VERIFY_NONE, error="missing"
                ),
            )
        return _result(
            "Znalazłem plik.",
            ok=True,
            tool="find_file",
            observation=_obs(
                tool="find_file",
                action="find_file",
                success=True,
                verification_level=VERIFY_STRONG,
                evidence={"path": str(results[0])},
            ),
            data={"path": str(results[0])},
        )

    def _act_open_file(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        path = str(args.get("path") or args.get("name") or "").strip()
        try:
            import ComputerControl

            message = ComputerControl.open_file_or_folder(path)
            ok = "nie" not in str(message or "").lower()[:12]
        except Exception as exc:
            message = str(exc)
            ok = False
        return _result(
            "Otwieram plik." if ok else (message or "Nie otworzyłem pliku."),
            ok=ok,
            tool="open_file",
            observation=_obs(
                tool="open_file",
                action="open_file",
                target=path,
                success=ok,
                verification_level=VERIFY_BASIC,
                reversible=False,
            ),
        )

    def _act_list_folder(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        path = str(args.get("path") or args.get("name") or "").strip()
        try:
            from pathlib import Path

            folder = Path(path).expanduser()
            names = [p.name for p in folder.iterdir()][:20] if folder.is_dir() else []
            ok = folder.is_dir()
        except Exception:
            names, ok = [], False
        return _result(
            "Zawartość: " + ", ".join(names[:8]) + "." if ok else "Nie mogę odczytać folderu.",
            ok=ok,
            tool="list_folder",
            observation=_obs(
                tool="list_folder",
                action="list_folder",
                success=ok,
                verification_level=VERIFY_STRONG if ok else VERIFY_NONE,
                side_effect_level="none",
            ),
            data={"entries": names},
        )

    def _act_clipboard(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        try:
            import ComputerControl

            text = ComputerControl.read_clipboard() if hasattr(ComputerControl, "read_clipboard") else ""
        except Exception:
            text = ""
        shown = str(text or "")
        if len(shown) > 120:
            shown = shown[:117] + "…"
        return _result(
            f"W schowku: {shown}" if shown else "Schowek jest pusty.",
            ok=True,
            tool="read_clipboard",
            observation=_obs(
                tool="read_clipboard",
                action="read_clipboard",
                success=True,
                verification_level=VERIFY_BASIC,
                evidence={"length": len(str(text or ""))},
                side_effect_level="none",
            ),
        )

    def _act_power(self, before: ComputerObservation, args: Dict[str, Any]) -> ToolResult:
        action = str(args.get("action") or "lock")
        if action in ("shutdown", "restart", "sleep") and not args.get("confirmed"):
            prompt = {
                "shutdown": "Mam wyłączyć komputer?",
                "restart": "Mam zrestartować komputer?",
                "sleep": "Mam uśpić komputer?",
            }[action]
            return _result(
                prompt,
                ok=False,
                tool=f"{action}_pc",
                observation=_obs(
                    tool=f"{action}_pc",
                    action=action,
                    success=False,
                    verification_level=VERIFY_NONE,
                    extra={"waiting": WAITING_CONFIRMATION},
                ),
                status=ToolStatus.NEEDS_CONFIRMATION,
                data={"waiting": WAITING_CONFIRMATION, "action": action},
            )
        if isinstance(self.store, FakeWindowStore):
            return _result(
                "Blokuję komputer." if action == "lock" else "Zlecam akcję zasilania.",
                ok=True,
                tool=f"{action}_pc",
                observation=_obs(
                    tool=f"{action}_pc",
                    action=action,
                    success=True,
                    verification_level=VERIFY_BASIC,
                    reversible=False,
                    side_effect_level="high",
                ),
            )
        try:
            import ComputerControl

            mapped = {"lock": "lock", "shutdown": "shutdown", "restart": "restart"}.get(action)
            if mapped:
                message = ComputerControl.power_action(mapped)
                return _result(
                    str(message or "Gotowe."),
                    ok=bool(message),
                    tool=f"{action}_pc",
                    observation=_obs(
                        tool=f"{action}_pc",
                        action=action,
                        success=bool(message),
                        verification_level=VERIFY_BASIC,
                        reversible=False,
                        side_effect_level="high",
                    ),
                )
        except Exception as exc:
            return _result(
                "Nie wykonuję tej akcji zasilania.",
                ok=False,
                tool=f"{action}_pc",
                observation=_obs(
                    tool=f"{action}_pc", action=action, success=False, verification_level=VERIFY_NONE, error=str(exc)
                ),
            )
        return _result(
            "Tej akcji zasilania nie wykonuję automatycznie.",
            ok=False,
            tool=f"{action}_pc",
            observation=_obs(
                tool=f"{action}_pc", action=action, success=False, verification_level=VERIFY_NONE, error="unavailable"
            ),
        )

    def _note_world(self, obs: ComputerObservation) -> None:
        try:
            from core.world_state import note_computer

            note_computer(
                {
                    "active_window": obs.planner_fragment(),
                    "snapshot_id": obs.snapshot_id,
                    "current_plan": dict(self.current_plan),
                    "current_step": dict(self.current_step),
                    "last_observation": obs.planner_fragment(),
                }
            )
        except Exception:
            pass

    def _sync_perception(self, obs: ComputerObservation) -> None:
        try:
            from core.perception.screen_runtime import get_screen_runtime

            runtime = get_screen_runtime()
            ctx = obs.active_window
            runtime.observe_window_meta(
                hwnd=int(getattr(ctx, "hwnd", 0) or 0),
                title=str(getattr(ctx, "title", "") or obs.window_title or ""),
                process=str(getattr(ctx, "process_name", "") or obs.app_process or ""),
                bounds=dict(getattr(ctx, "bounds", None) or {}),
                focused=obs.focused_element if isinstance(obs.focused_element, dict) else None,
            )
            runtime.fuse_ui(obs.visible_elements or [])
        except Exception:
            pass

    def _visual_click_block(self, target: Optional[Dict[str, Any]]) -> Optional[ToolResult]:
        if not target:
            return None
        sources = target.get("source") or []
        if isinstance(sources, str):
            sources = [sources]
        frame_id = target.get("frame_id")
        hwnd = int(target.get("hwnd") or getattr(self.active_window, "hwnd", 0) or 0)
        try:
            from Config import get_config_value
            from core.perception.metrics import note_perception
            from core.perception.screen_runtime import get_screen_runtime

            action_conf = float(get_config_value("vision", "action_confidence", default=0.90) or 0.90)
            if frame_id:
                runtime = get_screen_runtime()
                if not runtime.assert_frame_fresh(int(frame_id), hwnd=hwnd):
                    return _result(
                        "Ekran zdążył się zmienić. Sprawdzam jeszcze raz.",
                        ok=False,
                        tool="click_ui_element",
                        observation=_obs(
                            tool="click_ui_element",
                            action="click",
                            success=False,
                            verification_level=VERIFY_NONE,
                            error="stale_visual_frame",
                        ),
                        error="stale_visual_frame",
                        data={"clicked": False, "needs_refresh": True},
                    )
            conf = float(target.get("confidence") or 0.0)
            visionish = any(
                str(item).upper() in ("VISION_LOCAL", "VISION", "LOCAL_SCREEN_PIXELS")
                for item in sources
            )
            uia = any("UIA" in str(item).upper() or str(item) == "LOCAL_COMPUTER_UIA" for item in sources)
            if visionish and conf and conf < 0.80:
                note_perception("low_confidence_visual_action")
                return _result(
                    "Nie jestem pewien, który element wskazać.",
                    ok=False,
                    tool="click_ui_element",
                    observation=_obs(
                        tool="click_ui_element",
                        action="click",
                        success=False,
                        verification_level=VERIFY_NONE,
                        error="low_confidence",
                    ),
                    error="low_confidence",
                    data={"clicked": False, "needs_clarification": True, "confidence": conf},
                )
            if visionish and not uia and conf and conf < action_conf:
                note_perception("low_confidence_visual_action")
                return _result(
                    "Widzę kandydata, ale potrzebuję potwierdzenia.",
                    ok=False,
                    tool="click_ui_element",
                    observation=_obs(
                        tool="click_ui_element",
                        action="click",
                        success=False,
                        verification_level=VERIFY_NONE,
                        error="low_confidence",
                    ),
                    error="low_confidence",
                    data={"clicked": False, "needs_clarification": True, "confidence": conf},
                )
        except Exception:
            return None
        return None


def _norm_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _as_ui_element(payload: Dict[str, Any]):
    from vision.models import Bounds, UIElement

    b = payload.get("bounds") or {}
    return UIElement(
        id=str(payload.get("element_id") or "el"),
        role=str(payload.get("role") or "button"),
        text=str(payload.get("name") or payload.get("text") or ""),
        bounds=Bounds(int(b.get("x") or 10), int(b.get("y") or 10), int(b.get("width") or 40), int(b.get("height") or 20)),
        source=str(payload.get("source") or "fake"),
        confidence=float(payload.get("confidence") or 1.0),
        is_password=bool(payload.get("is_password")),
    )


_AGENT: Optional[ComputerAgent] = None


def take_stashed_verified_result() -> Optional[ToolResult]:
    agent = _AGENT
    if agent is None:
        return None
    return agent.take_verified_result()


def get_computer_agent() -> ComputerAgent:
    global _AGENT
    if _AGENT is None:
        _AGENT = ComputerAgent()
    return _AGENT


def set_computer_agent(agent: Optional[ComputerAgent]) -> None:
    global _AGENT
    _AGENT = agent


def reset_computer_agent_for_tests(agent: Optional[ComputerAgent] = None) -> ComputerAgent:
    reset_computer_metrics()
    created = agent if agent is not None else ComputerAgent(FakeWindowStore())
    set_computer_agent(created)
    return created


def notepad_fixture_store() -> FakeWindowStore:
    from core.computer_windows import WindowInfo

    notepad = FakeWindowRecord(
        info=WindowInfo(
            window_id="win_notepad",
            hwnd=1001,
            process_name="notepad.exe",
            app_name="Notatnik",
            title="Bez tytułu — Notatnik",
            bounds={"x": 40, "y": 40, "width": 800, "height": 600},
            focused=True,
            visible=True,
            opened_by_elian=True,
        ),
        elements=[
            {
                "element_id": "input_1",
                "role": "document",
                "name": "Text Editor",
                "automation_id": "15",
                "value": "",
                "enabled": True,
                "visible": True,
                "focused": True,
                "confidence": 0.97,
                "actions": ["focus", "type", "value"],
                "bounds": {"x": 50, "y": 90, "width": 760, "height": 520},
            }
        ],
    )
    calc = FakeWindowRecord(
        info=WindowInfo(
            window_id="win_calc",
            hwnd=1002,
            process_name="calculator.exe",
            app_name="Kalkulator",
            title="Kalkulator",
            bounds={"x": 900, "y": 40, "width": 400, "height": 500},
            focused=False,
            visible=True,
        ),
        elements=[
            {
                "element_id": "button_eq",
                "role": "button",
                "name": "=",
                "confidence": 0.95,
                "enabled": True,
                "visible": True,
            }
        ],
    )
    return FakeWindowStore([notepad, calc])


def cursor_window_record() -> FakeWindowRecord:
    from core.computer_windows import WindowInfo

    return FakeWindowRecord(
        info=WindowInfo(
            window_id="win_cursor",
            hwnd=1003,
            process_name="Cursor.exe",
            app_name="Cursor",
            title="Elian — Cursor",
            bounds={"x": 20, "y": 20, "width": 1100, "height": 700},
            focused=False,
            visible=True,
        ),
        elements=[
            {
                "element_id": "cursor_editor",
                "role": "document",
                "name": "Editor",
                "value": "",
                "enabled": True,
                "visible": True,
                "focused": True,
                "confidence": 0.99,
                "actions": ["focus", "type", "value"],
            }
        ],
    )


def add_cursor_window(store: FakeWindowStore) -> FakeWindowRecord:
    rec = cursor_window_record()
    store.add_window(rec)
    return rec
