# ==========================
# ELIAN — Active execution context (goal-oriented Planner)
# ==========================
"""Persistent goal state for PLAN → EXECUTE → VERIFY → REPAIR / REPLAN.

Compatible with pending_goal + world_state active_plan. Does not own semantics.
Capability Registry (if present) is consumed for capability_missing diagnosis.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from core.plan_state import (
    PLAN_COMPLETED,
    PLAN_FAILED,
    PLAN_PARTIAL,
    PLAN_RUNNING,
    STEP_FAILED,
    STEP_SKIPPED,
    STEP_SUCCEEDED,
)


class FailureCategory(str, Enum):
    INVALID_ARGS = "invalid_args"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    TIMEOUT = "timeout"
    STATE_NOT_CHANGED = "state_not_changed"
    PERMISSION_DENIED = "permission_denied"
    CAPABILITY_MISSING = "capability_missing"
    AMBIGUOUS_TARGET = "ambiguous_target"
    TOOL_EXCEPTION = "tool_exception"
    VERIFICATION_FAILED = "verification_failed"
    UNKNOWN = "unknown"


class GoalPhase(str, Enum):
    PLAN = "plan"
    EXECUTE = "execute"
    OBSERVE = "observe"
    VERIFY = "verify"
    REPAIR = "repair"
    REPLAN = "replan"
    COMPLETE = "complete"
    FAIL = "fail"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class CriterionKind(str, Enum):
    DEVICE_STATE = "device_state"
    NOW_PLAYING = "now_playing"
    WINDOW = "window"
    WEB_RESULTS = "web_results"
    PROCESS = "process"
    TOOL_OK = "tool_ok"
    CUSTOM = "custom"


@dataclass
class SuccessCriterion:
    kind: CriterionKind | str
    tool: str = ""
    expected: Dict[str, Any] = field(default_factory=dict)
    required: bool = True
    label: str = ""

    def to_dict(self) -> Dict[str, Any]:
        kind = self.kind.value if isinstance(self.kind, CriterionKind) else str(self.kind)
        return {
            "kind": kind,
            "tool": self.tool,
            "expected": dict(self.expected or {}),
            "required": bool(self.required),
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "SuccessCriterion":
        data = dict(raw or {})
        kind = str(data.get("kind") or CriterionKind.TOOL_OK.value)
        try:
            kind_enum: CriterionKind | str = CriterionKind(kind)
        except ValueError:
            kind_enum = kind
        return cls(
            kind=kind_enum,
            tool=str(data.get("tool") or ""),
            expected=dict(data.get("expected") or {}),
            required=bool(data.get("required", True)),
            label=str(data.get("label") or ""),
        )


@dataclass
class FailureDiagnosis:
    category: FailureCategory = FailureCategory.UNKNOWN
    tool: str = ""
    message: str = ""
    retryable: bool = False
    fingerprint: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value if isinstance(self.category, FailureCategory) else str(self.category),
            "tool": self.tool,
            "message": self.message,
            "retryable": bool(self.retryable),
            "fingerprint": self.fingerprint,
            "detail": dict(self.detail or {}),
        }

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "FailureDiagnosis":
        data = dict(raw or {})
        cat = str(data.get("category") or FailureCategory.UNKNOWN.value)
        try:
            category = FailureCategory(cat)
        except ValueError:
            category = FailureCategory.UNKNOWN
        return cls(
            category=category,
            tool=str(data.get("tool") or ""),
            message=str(data.get("message") or ""),
            retryable=bool(data.get("retryable")),
            fingerprint=str(data.get("fingerprint") or ""),
            detail=dict(data.get("detail") or {}),
        )


def action_fingerprint(tool: str, arguments: Optional[Dict[str, Any]] = None) -> str:
    args = dict(arguments or {})
    keys = sorted(f"{k}={args.get(k)}" for k in args.keys())
    return f"{str(tool or '').strip().lower()}|{','.join(keys)}"


def default_constraints() -> List[str]:
    return [
        "no_close_unsaved_without_confirm",
        "no_restart_without_consent",
        "no_risky_without_confirm",
    ]


def criteria_for_tool(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> List[SuccessCriterion]:
    """Map mutation tools to SuccessCriteria. Tool success alone is not enough."""
    name = str(tool_name or "").strip()
    args = dict(arguments or {})
    if name == "turn_on_device":
        return [
            SuccessCriterion(
                kind=CriterionKind.DEVICE_STATE,
                tool="get_device_state",
                expected={
                    "state": "on",
                    "entity_id": str(args.get("entity_id") or args.get("target") or ""),
                },
                label="device_on",
            )
        ]
    if name == "turn_off_device":
        return [
            SuccessCriterion(
                kind=CriterionKind.DEVICE_STATE,
                tool="get_device_state",
                expected={
                    "state": "off",
                    "entity_id": str(args.get("entity_id") or args.get("target") or ""),
                },
                label="device_off",
            )
        ]
    if name == "set_device_value":
        return [
            SuccessCriterion(
                kind=CriterionKind.DEVICE_STATE,
                tool="get_device_state",
                expected={
                    "entity_id": str(args.get("entity_id") or args.get("target") or ""),
                    "value": args.get("value"),
                },
                label="device_value",
            )
        ]
    if name == "play_media":
        return [
            SuccessCriterion(
                kind=CriterionKind.NOW_PLAYING,
                tool="get_now_playing",
                expected={"query": str(args.get("query") or args.get("title") or "")},
                label="now_playing",
            )
        ]
    if name in ("pause_media", "resume_media"):
        return [
            SuccessCriterion(
                kind=CriterionKind.NOW_PLAYING,
                tool="get_now_playing",
                expected={},
                label="media_state",
            )
        ]
    if name == "open_app":
        app = str(args.get("name") or args.get("app") or args.get("app_name") or "")
        return [
            SuccessCriterion(
                kind=CriterionKind.WINDOW,
                tool="window_exists",
                expected={"app": app},
                label="app_window",
            )
        ]
    if name == "web_search":
        return [
            SuccessCriterion(
                kind=CriterionKind.WEB_RESULTS,
                tool="",
                expected={"query": str(args.get("query") or "")},
                label="web_hits",
            )
        ]
    if name in ("volume_up", "volume_down"):
        direction = "increase" if name == "volume_up" else "decrease"
        return [
            SuccessCriterion(
                kind=CriterionKind.CUSTOM,
                tool="get_volume",
                expected={"direction": direction},
                label="volume_level_changed",
            )
        ]
    return [
        SuccessCriterion(
            kind=CriterionKind.TOOL_OK,
            tool=name,
            expected={},
            required=False,
            label="tool_reported_ok",
        )
    ]


@dataclass
class ActiveExecutionContext:
    """Persistent active goal for resume / retry / cancel / partial honesty."""

    goal_id: str = ""
    goal: str = ""
    constraints: List[str] = field(default_factory=default_constraints)
    success_criteria: List[SuccessCriterion] = field(default_factory=list)
    status: str = PLAN_RUNNING
    phase: str = GoalPhase.PLAN.value
    plan_id: str = ""
    steps: List[Dict[str, Any]] = field(default_factory=list)
    completed_step_ids: List[str] = field(default_factory=list)
    remaining_step_ids: List[str] = field(default_factory=list)
    failed_step_ids: List[str] = field(default_factory=list)
    last_failure: Optional[FailureDiagnosis] = None
    failed_fingerprints: List[str] = field(default_factory=list)
    repair_count: int = 0
    replan_count: int = 0
    retry_count: int = 0
    force_alternate: bool = False
    started_at: float = 0.0
    updated_at: float = 0.0
    deadline_at: float = 0.0
    max_steps: int = 8
    max_repair: int = 2
    max_replan: int = 2
    source_pending: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.goal_id:
            self.goal_id = uuid.uuid4().hex[:8]
        now = time.monotonic()
        if not self.started_at:
            self.started_at = now
        self.updated_at = now

    @property
    def is_active(self) -> bool:
        return self.status in (PLAN_RUNNING, PLAN_PARTIAL, PLAN_FAILED)

    @property
    def failed_path_set(self) -> Set[str]:
        return {str(item) for item in self.failed_fingerprints if item}

    def touch(self) -> None:
        self.updated_at = time.monotonic()

    def note_failure(self, diagnosis: FailureDiagnosis) -> None:
        self.last_failure = diagnosis
        fp = str(diagnosis.fingerprint or "")
        if fp and fp not in self.failed_fingerprints:
            self.failed_fingerprints.append(fp)
        self.touch()

    def sync_from_steps(self, steps: List[Dict[str, Any]], *, status: str = "") -> None:
        self.steps = [dict(item) for item in (steps or []) if isinstance(item, dict)]
        outcomes = [step_row_outcome(item) for item in self.steps]
        self.completed_step_ids = [
            str(item.get("step_id") or "")
            for item, outcome in zip(self.steps, outcomes)
            if outcome in (STEP_SUCCEEDED, STEP_SKIPPED)
        ]
        self.failed_step_ids = [
            str(item.get("step_id") or "")
            for item, outcome in zip(self.steps, outcomes)
            if outcome == STEP_FAILED
        ]
        self.remaining_step_ids = [
            str(item.get("step_id") or "")
            for item, outcome in zip(self.steps, outcomes)
            if outcome not in (STEP_SUCCEEDED, STEP_SKIPPED)
            and str(item.get("step_id") or "")
        ]
        if status:
            self.status = status
        elif self.failed_step_ids and self.completed_step_ids:
            self.status = PLAN_PARTIAL
        elif self.failed_step_ids:
            self.status = PLAN_FAILED
        elif any(not o for o in outcomes):
            # Incomplete / bad-shape rows: never auto-COMPLETED.
            self.status = PLAN_PARTIAL if self.completed_step_ids else PLAN_RUNNING
        elif self.steps and not self.remaining_step_ids:
            self.status = PLAN_COMPLETED
            self.phase = GoalPhase.COMPLETE.value
        self.touch()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "goal": self.goal,
            "constraints": list(self.constraints),
            "success_criteria": [c.to_dict() for c in self.success_criteria],
            "status": self.status,
            "phase": self.phase,
            "plan_id": self.plan_id,
            "steps": list(self.steps),
            "completed_step_ids": list(self.completed_step_ids),
            "remaining_step_ids": list(self.remaining_step_ids),
            "failed_step_ids": list(self.failed_step_ids),
            "last_failure": self.last_failure.to_dict() if self.last_failure else None,
            "failed_fingerprints": list(self.failed_fingerprints),
            "repair_count": int(self.repair_count),
            "replan_count": int(self.replan_count),
            "retry_count": int(self.retry_count),
            "force_alternate": bool(self.force_alternate),
            "started_at": float(self.started_at),
            "updated_at": float(self.updated_at),
            "deadline_at": float(self.deadline_at),
            "max_steps": int(self.max_steps),
            "max_repair": int(self.max_repair),
            "max_replan": int(self.max_replan),
            "source_pending": dict(self.source_pending or {}),
        }

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> Optional["ActiveExecutionContext"]:
        if not isinstance(raw, dict) or not raw:
            return None
        last = raw.get("last_failure")
        criteria = [
            SuccessCriterion.from_dict(item)
            for item in (raw.get("success_criteria") or [])
            if isinstance(item, dict)
        ]
        ctx = cls(
            goal_id=str(raw.get("goal_id") or ""),
            goal=str(raw.get("goal") or raw.get("original_goal") or ""),
            constraints=list(raw.get("constraints") or default_constraints()),
            success_criteria=criteria,
            status=str(raw.get("status") or PLAN_RUNNING),
            phase=str(raw.get("phase") or GoalPhase.PLAN.value),
            plan_id=str(raw.get("plan_id") or ""),
            steps=[dict(item) for item in (raw.get("steps") or []) if isinstance(item, dict)],
            completed_step_ids=list(raw.get("completed_step_ids") or []),
            remaining_step_ids=list(raw.get("remaining_step_ids") or []),
            failed_step_ids=list(raw.get("failed_step_ids") or []),
            last_failure=FailureDiagnosis.from_dict(last) if isinstance(last, dict) else None,
            failed_fingerprints=list(raw.get("failed_fingerprints") or []),
            repair_count=int(raw.get("repair_count") or 0),
            replan_count=int(raw.get("replan_count") or 0),
            retry_count=int(raw.get("retry_count") or 0),
            force_alternate=bool(raw.get("force_alternate")),
            started_at=float(raw.get("started_at") or 0.0),
            updated_at=float(raw.get("updated_at") or 0.0),
            deadline_at=float(raw.get("deadline_at") or 0.0),
            max_steps=int(raw.get("max_steps") or 8),
            max_repair=int(raw.get("max_repair") or 2),
            max_replan=int(raw.get("max_replan") or 2),
            source_pending=dict(raw.get("source_pending") or {}),
        )
        return ctx

    def to_pending_goal(self) -> Optional[Dict[str, Any]]:
        """Bridge to pending_goal contract used by ConversationManager."""
        from core.pending_goal import pending_from_plan

        pending = pending_from_plan(
            plan_id=self.plan_id or self.goal_id,
            original_goal=self.goal,
            status=self.status,
            steps=self.steps,
            last_failure_reason=(
                self.last_failure.category.value if self.last_failure else "step_failed"
            ),
            retry_count=self.retry_count,
        )
        if pending is None and self.status in (PLAN_RUNNING, PLAN_PARTIAL):
            pending = {
                "plan_id": self.plan_id or self.goal_id,
                "original_goal": self.goal,
                "original_user_text": self.goal,
                "normalized_goal": str(self.goal or "").strip().lower(),
                "status": self.status,
                "steps": list(self.steps),
                "semantic_steps": list(self.steps),
                "failed_steps": [
                    item for item in self.steps if str(item.get("status") or "") == STEP_FAILED
                ],
                "successful_steps": [
                    item
                    for item in self.steps
                    if str(item.get("status") or "") == STEP_SUCCEEDED
                ],
                "successful_step_ids": list(self.completed_step_ids),
                "failed_step_ids": list(self.failed_step_ids),
                "successful_idempotency_keys": [
                    str(item.get("idempotency_key") or "")
                    for item in self.steps
                    if str(item.get("status") or "") == STEP_SUCCEEDED
                    and item.get("idempotency_key")
                ],
                "created_at": time.monotonic(),
                "retry_count": int(self.retry_count or 0),
                "last_failure_reason": (
                    self.last_failure.category.value if self.last_failure else ""
                ),
            }
        if pending is None:
            return None
        pending["execution_context"] = self.to_dict()
        pending["goal_id"] = self.goal_id
        pending["force_alternate"] = bool(self.force_alternate)
        pending["failed_fingerprints"] = list(self.failed_fingerprints)
        pending["replan_count"] = int(self.replan_count)
        pending["constraints"] = list(self.constraints)
        return pending

    @classmethod
    def from_pending(cls, pending: Optional[Dict[str, Any]]) -> Optional["ActiveExecutionContext"]:
        if not isinstance(pending, dict):
            return None
        nested = pending.get("execution_context")
        if isinstance(nested, dict) and nested:
            ctx = cls.from_dict(nested)
            if ctx is not None:
                ctx.source_pending = dict(pending)
                return ctx
        ctx = cls(
            goal_id=str(pending.get("goal_id") or pending.get("plan_id") or ""),
            goal=str(pending.get("original_goal") or pending.get("original_user_text") or ""),
            plan_id=str(pending.get("plan_id") or ""),
            status=str(pending.get("status") or PLAN_FAILED),
            steps=[dict(item) for item in (pending.get("steps") or []) if isinstance(item, dict)],
            retry_count=int(pending.get("retry_count") or 0),
            force_alternate=bool(pending.get("force_alternate")),
            failed_fingerprints=list(pending.get("failed_fingerprints") or []),
            replan_count=int(pending.get("replan_count") or 0),
            constraints=list(pending.get("constraints") or default_constraints()),
            source_pending=dict(pending),
        )
        if pending.get("last_failure") and isinstance(pending.get("last_failure"), dict):
            ctx.last_failure = FailureDiagnosis.from_dict(pending.get("last_failure"))
        elif pending.get("last_failure_reason"):
            try:
                cat = FailureCategory(str(pending.get("last_failure_reason")))
            except ValueError:
                cat = FailureCategory.UNKNOWN
            ctx.last_failure = FailureDiagnosis(
                category=cat,
                message=str(pending.get("last_failure_reason") or ""),
            )
        ctx.sync_from_steps(ctx.steps, status=ctx.status)
        return ctx


_ACTIVE: Optional[ActiveExecutionContext] = None


def get_active_execution() -> Optional[ActiveExecutionContext]:
    return _ACTIVE


def set_active_execution(ctx: Optional[ActiveExecutionContext]) -> Optional[ActiveExecutionContext]:
    global _ACTIVE
    _ACTIVE = ctx
    return _ACTIVE


def clear_active_execution() -> None:
    set_active_execution(None)


def budgets_from_config() -> Dict[str, Any]:
    max_steps, max_repair, max_replan, timeout_s = 8, 2, 2, 45.0
    try:
        from Config import get_config_value

        max_steps = int(get_config_value("ai", "agent_max_steps", default=8) or 8)
        max_repair = int(get_config_value("ai", "agent_max_repair", default=2) or 2)
        max_replan = int(get_config_value("ai", "agent_max_replan", default=2) or 2)
        timeout_s = float(get_config_value("ai", "agent_plan_timeout_seconds", default=45) or 45)
    except Exception:
        pass
    return {
        "max_steps": max(1, max_steps),
        "max_repair": max(1, max_repair),
        "max_replan": max(0, max_replan),
        "timeout_s": float(timeout_s),
    }


def new_execution_context(
    goal: str,
    *,
    plan_id: str = "",
    constraints: Optional[List[str]] = None,
    success_criteria: Optional[List[SuccessCriterion]] = None,
) -> ActiveExecutionContext:
    budgets = budgets_from_config()
    now = time.monotonic()
    return ActiveExecutionContext(
        goal=str(goal or ""),
        plan_id=str(plan_id or ""),
        constraints=list(constraints or default_constraints()),
        success_criteria=list(success_criteria or []),
        max_steps=int(budgets["max_steps"]),
        max_repair=int(budgets["max_repair"]),
        max_replan=int(budgets["max_replan"]),
        started_at=now,
        deadline_at=now + float(budgets["timeout_s"]),
        phase=GoalPhase.PLAN.value,
        status=PLAN_RUNNING,
    )


def step_row_outcome(row: Dict[str, Any] | None) -> str:
    """Normalize execute-row shapes to STEP_* or '' if incomplete/unknown.

    Canonical plan-cycle rows use ``status``. Alternate callers may only set
    ``success`` / ``verified`` / ``verified_success`` / ``observation.success``.
    Empty string means incomplete — must never claim full success ("Gotowe.").
    """
    if not isinstance(row, dict):
        return ""
    raw = str(row.get("status") or "").strip().upper()
    if raw in (STEP_SUCCEEDED, STEP_FAILED, STEP_SKIPPED):
        return raw
    if raw in ("SUCCESS", "OK", "COMPLETED", "VERIFIED", "STEP_SUCCEEDED"):
        return STEP_SUCCEEDED
    if raw in ("FAIL", "ERROR", "STEP_FAILED"):
        return STEP_FAILED
    if raw in ("SKIP", "STEP_SKIPPED"):
        return STEP_SKIPPED

    obs = row.get("observation") if isinstance(row.get("observation"), dict) else {}
    obs_status = str(obs.get("status") or "").strip().upper()
    if obs_status in (STEP_SUCCEEDED, "SUCCESS", "OK", "COMPLETED", "VERIFIED"):
        if obs.get("success") is False:
            return STEP_FAILED
        return STEP_SUCCEEDED
    if obs_status in (STEP_FAILED, "FAIL", "ERROR"):
        return STEP_FAILED

    if "success" in row:
        if row.get("success") is True:
            return STEP_SUCCEEDED
        if row.get("success") is False:
            return STEP_FAILED
    if "verified_success" in row:
        if row.get("verified_success") is True:
            return STEP_SUCCEEDED
        if row.get("verified_success") is False:
            return STEP_FAILED
    if "verified" in row and "success" not in row:
        if row.get("verified") is True:
            return STEP_SUCCEEDED
        if row.get("verified") is False:
            return STEP_FAILED
    if isinstance(obs, dict) and "success" in obs:
        if obs.get("success") is True:
            return STEP_SUCCEEDED
        if obs.get("success") is False:
            return STEP_FAILED
    return ""


def rows_indicate_partial_or_incomplete(
    steps: List[Dict[str, Any]] | None = None,
    observations: List[Any] | None = None,
) -> bool:
    """True when outcomes are mixed, failed, or incomplete — block full COMPLETED/Gotowe."""
    rows = [item for item in (steps or []) if isinstance(item, dict)]
    if rows:
        outcomes = [step_row_outcome(item) for item in rows]
        if any(o == STEP_FAILED for o in outcomes):
            return True
        if any(not o for o in outcomes):
            return True
    obs_list = [item for item in (observations or []) if isinstance(item, dict)]
    if obs_list:
        flags = [bool(item.get("success")) for item in obs_list]
        if flags and (any(flags) and not all(flags)):
            return True
        if flags and not any(flags):
            return True
    return False



def partial_success_message(steps: List[Dict[str, Any]] | None) -> str:
    """Honest Polish summary — never 'Gotowe.' on mixed/incomplete/bad-shape outcomes."""
    from core.pending_goal import honest_failed_message

    rows = [item for item in (steps or []) if isinstance(item, dict)]
    if not rows:
        return "Nie udało mi się tego dokończyć."

    normalized: List[Dict[str, Any]] = []
    outcomes: List[str] = []
    for row in rows:
        outcome = step_row_outcome(row)
        outcomes.append(outcome)
        copy = dict(row)
        if outcome and not str(copy.get("status") or "").strip():
            copy["status"] = outcome
        normalized.append(copy)

    ok = [r for r, o in zip(normalized, outcomes) if o == STEP_SUCCEEDED]
    bad = [r for r, o in zip(normalized, outcomes) if o == STEP_FAILED]
    unknown = [o for o in outcomes if not o]

    if bad:
        return honest_failed_message(normalized, offer_retry=True)
    if unknown:
        # Bad-shape / incomplete observations: fail closed (not vacuous Gotowe).
        return "Nie udało mi się tego dokończyć."
    if ok and all(o in (STEP_SUCCEEDED, STEP_SKIPPED) for o in outcomes):
        return "Gotowe."
    return "Nie udało mi się tego dokończyć."


def capability_missing_for_tool(tool_name: str) -> bool:
    """Best-effort: True only when capability is UNAVAILABLE/BLOCKED (not DISABLED).

    DISABLED is a config choice handled by tool handlers; treating it as
    capability_missing would short-circuit plan-cycle retries incorrectly.
    """
    name = str(tool_name or "").strip()
    if not name:
        return False
    try:
        from core.capability_registry import CapStatus, TOOL_CAPABILITY, get_capability_registry

        cap_id = TOOL_CAPABILITY.get(name)
        if cap_id:
            reg = get_capability_registry()
            reg.refresh()
            rec = reg.get(cap_id)
            if rec is not None and rec.status in (
                CapStatus.UNAVAILABLE,
                CapStatus.BLOCKED,
            ):
                return True
            if rec is not None:
                return False
    except Exception:
        pass
    try:
        from core.capability_map import snapshot_capabilities, UNAVAILABLE

        snap = snapshot_capabilities()
        mapping = {
            "turn_on_device": "HomeAssistant",
            "turn_off_device": "HomeAssistant",
            "set_device_value": "HomeAssistant",
            "get_device_state": "HomeAssistant",
            "web_search": "Internet",
            "open_app": "ComputerControl",
            "close_app": "ComputerControl",
            "window_exists": "ComputerControl",
            "focus_window": "ComputerControl",
            "hotkey": "ComputerControl",
            "type_text": "ComputerControl",
            "describe_screen": "ScreenPerception",
        }
        cap_id = mapping.get(name)
        if not cap_id:
            return False
        status = snap.get(cap_id)
        if status is None:
            return False
        return str(getattr(status, "status", "") or "") == UNAVAILABLE
    except Exception:
        return False
