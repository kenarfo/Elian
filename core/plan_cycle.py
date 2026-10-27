# ==========================
# ELIAN — PLAN → EXECUTE → VERIFY → REPAIR cycle (Agent/Planner)
# ==========================
"""Extend the existing Planner. Not a second agent. All actions via ToolRegistry.

action_firewall + agent_safety still apply. No third policy layer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from Diagnostics import log_turn

from core.action_firewall import MUTATION_TOOLS, allow_tool
from core.agent_safety import (
    REQUIRED_ANY,
    is_side_effect_tool,
    side_effect_blocked_for_intent,
    validate_side_effect_arguments,
)
from core.intent_family import IntentClassification, IntentFamily
from core.plan_state import (
    PLAN_COMPLETED,
    PLAN_FAILED,
    PLAN_PARTIAL,
    PLAN_RUNNING,
    STEP_FAILED,
    STEP_SKIPPED,
    STEP_SUCCEEDED,
)
from core.tool_result import ToolResult, ToolStatus
from core.tools import ToolRegistry


class CyclePhase(str, Enum):
    PLAN = "plan"
    EXECUTE = "execute"
    VERIFY = "verify"
    REPAIR = "repair"
    COMPLETE = "complete"
    FAIL = "fail"
    ASK = "ask"


class VerifyStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


class RepairAction(str, Enum):
    RETRY = "retry"
    ALTERNATE = "alternate"
    ASK = "ask"
    SKIP = "skip"
    FAIL = "fail"


# Absolute / idempotent mutations — retrying after verify-fail is safe-ish.
IDEMPOTENT_MUTATIONS = frozenset(
    {
        "open_app",
        "close_app",
        "play_media",
        "pause_media",
        "resume_media",
        "turn_on_device",
        "turn_off_device",
        "set_device_value",
        "focus_window",
    }
)

# Relative mutations must not re-run if execute already succeeded.
RELATIVE_MUTATIONS = frozenset({"volume_up", "volume_down", "next_track", "previous_track"})

# Read-only verify tools — never count as plan mutations.
VERIFY_TOOLS = frozenset(
    {
        "get_now_playing",
        "get_device_state",
        "window_exists",
        "list_windows",
        "get_active_window",
        "get_volume",
    }
)

_ON_STATES = frozenset(
    {"on", "open", "playing", "cool", "heat", "auto", "heat_cool", "dry", "fan_only", True, "true"}
)
_OFF_STATES = frozenset({"off", "closed", "idle", "paused", False, "false"})


def cycle_budgets() -> Dict[str, Any]:
    try:
        from Config import get_config_value

        max_steps = int(get_config_value("ai", "agent_max_steps", default=6) or 6)
        max_repair = int(get_config_value("ai", "agent_max_repair", default=2) or 2)
        max_replan = int(get_config_value("ai", "agent_max_replan", default=2) or 2)
        timeout_s = float(get_config_value("ai", "agent_plan_timeout_seconds", default=45) or 45)
    except Exception:
        max_steps, max_repair, max_replan, timeout_s = 6, 2, 2, 45.0
    try:
        from core.computer_agent import MAX_PLAN_SECONDS, MAX_PLAN_STEPS, MAX_REPAIR_PER_STEP

        max_steps = max(max_steps, int(MAX_PLAN_STEPS))
        max_repair = max(1, min(max_repair, int(MAX_REPAIR_PER_STEP)))
        timeout_s = max(timeout_s, float(MAX_PLAN_SECONDS))
    except Exception:
        pass
    return {
        "max_steps": max(1, max_steps),
        "max_repair_per_step": max(1, max_repair),
        "max_repair_per_plan": max(1, max_repair * 2),
        "max_replan": max(0, int(max_replan)),
        "timeout_s": float(timeout_s),
    }


def consume_world_snapshot(context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Immutable compact planner view. Never mutate WorldState.

    If Canonical WorldState exposes compact_planner_view / planner_snapshot, overlay
    a shallow copy on context fragments. Empty compact keys do not wipe context.
    """
    ctx = dict(context or {})
    computer = ctx.get("computer") if isinstance(ctx.get("computer"), dict) else {}
    fallback = {
        "services": dict(ctx.get("active_service") or {}),
        "smart_home": dict(ctx.get("active_device") or {}),
        "computer": dict(computer or {}),
        "media": dict(ctx.get("media") or {}),
        "active_plan": dict(ctx.get("active_plan") or {}),
        "source": "context_fallback",
    }
    compact = None
    try:
        from core import world_state as ws

        for name in ("compact_planner_view", "planner_snapshot", "to_planner_view"):
            fn = getattr(ws, name, None)
            if callable(fn):
                view = fn()
                if isinstance(view, dict) and view:
                    compact = dict(view)
                    break
    except Exception:
        compact = None
    if not compact:
        return fallback
    merged = dict(fallback)
    for key, value in compact.items():
        if value in (None, {}, []):
            continue
        merged[key] = dict(value) if isinstance(value, dict) else value
    merged["source"] = "world_state"
    return merged


@dataclass
class PlanStep:
    step_id: str = ""
    family: str = ""
    semantic_action: str = ""
    tool: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    mutation: bool = True
    expected: Dict[str, Any] = field(default_factory=dict)
    status: str = "PENDING"
    text: str = ""
    slots: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "family": self.family,
            "semantic_action": self.semantic_action,
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "mutation": self.mutation,
            "expected": dict(self.expected),
            "status": self.status,
        }


@dataclass
class Plan:
    plan_id: str = ""
    goal: str = ""
    steps: List[PlanStep] = field(default_factory=list)
    status: str = PLAN_RUNNING
    repair_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "status": self.status,
            "repair_count": self.repair_count,
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass
class ExecutionResult:
    success: bool
    tool: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    observation: Dict[str, Any] = field(default_factory=dict)
    skipped: bool = False
    blocked: bool = False
    block_reason: str = ""
    retryable: bool = False
    result: Optional[ToolResult] = None


@dataclass
class VerificationResult:
    status: VerifyStatus = VerifyStatus.SKIPPED
    tool: str = ""
    observation: Dict[str, Any] = field(default_factory=dict)
    matches_expected: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value if isinstance(self.status, VerifyStatus) else str(self.status),
            "tool": self.tool,
            "matches_expected": self.matches_expected,
            "reason": self.reason,
            "observation": dict(self.observation or {}),
        }


@dataclass
class RepairDecision:
    action: RepairAction = RepairAction.FAIL
    reason: str = ""
    tool: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    question: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value if isinstance(self.action, RepairAction) else str(self.action),
            "reason": self.reason,
            "tool": self.tool,
            "question": self.question,
        }


@dataclass
class StepCycleOutcome:
    execution: ExecutionResult
    verification: VerificationResult
    repair: Optional[RepairDecision] = None
    repairs_used: int = 0
    ask: bool = False
    ask_question: str = ""
    blocked: bool = False
    trace: List[Dict[str, Any]] = field(default_factory=list)
    verified_success: bool = False
    diagnosis: Optional[Any] = None
    replanned: bool = False


def guard_before_execute(
    *,
    tool_name: str,
    arguments: Dict[str, Any],
    classification: IntentClassification,
    context: Optional[Dict[str, Any]] = None,
    goal: str = "",
) -> Optional[StepCycleOutcome]:
    """ASK / safety / firewall before a mutation. None → proceed to ToolRegistry."""
    name = str(tool_name or "")
    args = dict(arguments or {})
    ctx = dict(context or {})
    envelope = ctx.get("decision_envelope")
    trace: List[Dict[str, Any]] = []

    unsure = material_uncertainty(name, args)
    if unsure:
        question = "Możesz sprecyzować, co mam zrobić?"
        if name == "play_media":
            question = "Co mam puścić?"
        elif name == "open_app":
            question = "Którą aplikację mam otworzyć?"
        elif name in ("turn_on_device", "turn_off_device", "set_device_value"):
            question = "Które urządzenie masz na myśli?"
        trace.append(_trace(CyclePhase.ASK.value, tool=name, reason="material_uncertainty"))
        return StepCycleOutcome(
            execution=ExecutionResult(
                success=False,
                tool=name,
                arguments=args,
                blocked=True,
                block_reason="material_uncertainty",
            ),
            verification=VerificationResult(status=VerifyStatus.SKIPPED, reason="ask"),
            repair=RepairDecision(action=RepairAction.ASK, reason="material_uncertainty", question=question),
            ask=True,
            ask_question=question,
            trace=trace,
        )

    if side_effect_blocked_for_intent(classification, name, args):
        trace.append(_trace(CyclePhase.FAIL.value, tool=name, reason="safety_block"))
        return StepCycleOutcome(
            execution=ExecutionResult(
                success=False,
                tool=name,
                arguments=args,
                blocked=True,
                block_reason="safety_block",
            ),
            verification=VerificationResult(status=VerifyStatus.SKIPPED, reason="safety_block"),
            repair=RepairDecision(action=RepairAction.FAIL, reason="safety_block"),
            blocked=True,
            trace=trace,
        )

    allowed, fw_reason = _firewall_allows(envelope, name, str(goal or ""))
    if not allowed and name in MUTATION_TOOLS:
        trace.append(_trace(CyclePhase.FAIL.value, tool=name, reason=f"firewall:{fw_reason}"))
        return StepCycleOutcome(
            execution=ExecutionResult(
                success=False,
                tool=name,
                arguments=args,
                blocked=True,
                block_reason=fw_reason,
            ),
            verification=VerificationResult(status=VerifyStatus.SKIPPED, reason=fw_reason),
            repair=RepairDecision(action=RepairAction.FAIL, reason=f"firewall:{fw_reason}"),
            blocked=True,
            trace=trace,
        )
    return None


InvokeFn = Callable[[str, Dict[str, Any], bool], ToolResult]


def material_uncertainty(
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Missing critical args — ASK, never guess."""
    missing = validate_side_effect_arguments(tool_name or "", arguments)
    if missing is not None:
        return str(missing.message or "Brakuje wymaganego argumentu.")
    keys = REQUIRED_ANY.get(str(tool_name or "").strip())
    if not keys:
        return None
    args = dict(arguments or {})
    for key in keys:
        value = args.get(key)
        if value is not None and str(value).strip():
            return None
    return f"Brakuje wymaganego argumentu: {keys[0]}."


def _firewall_allows(envelope: Any, tool_name: str, text: str) -> tuple[bool, str]:
    if envelope is None:
        return True, "no_envelope"
    mode = str(getattr(envelope, "mode", "") or "")
    if not mode:
        return True, "empty_envelope"
    try:
        return allow_tool(envelope, tool_name, text)
    except Exception:
        return True, "firewall_error"




# SKIPPED verify may claim success ONLY for these intentional absences of a verify tool.
# verify_tool_unregistered is NOT listed — missing required verify ≠ success evidence.
_SKIP_SUCCESS_CLAIM_REASONS = frozenset(
    {
        # no_verify_spec intentionally OMITTED — mutations without observation
        # must not claim verified_success (S38/S42 volume; close_app etc.).
        "non_mutation",  # read-only / non side-effect tools
    }
)


def verification_allows_success_claim(verification: Optional["VerificationResult"]) -> bool:
    """True when SKIPPED/UNKNOWN verify may still count as success evidence.

    Default deny. Only intentional non_mutation skips may claim.
    no_verify_spec / verify_tool_unregistered → cannot claim success (fail-closed).
    """
    if verification is None:
        return False
    status = getattr(verification, "status", None)
    if status == VerifyStatus.PASSED:
        return True
    if status not in (VerifyStatus.SKIPPED, VerifyStatus.UNKNOWN):
        return False
    reason = str(getattr(verification, "reason", "") or "")
    if reason == "verify_tool_unregistered":
        return False
    return reason in _SKIP_SUCCESS_CLAIM_REASONS


def verification_missing_capability(verification: Any = None, *, reason: str = "") -> bool:
    """True when required verify could not run (not an observed effect failure).

    Tool observation.success must stay intact; speech claims stay fail-closed via
    verified_success / never_fake_success — do not rewrite tool success→fail.
    """
    why = str(reason or "").strip()
    if not why and verification is not None:
        if isinstance(verification, dict):
            why = str(verification.get("reason") or "")
        else:
            why = str(getattr(verification, "reason", "") or "")
    return why in ("verify_tool_unregistered", "no_verify_spec")


def verification_proved_effect_failure(verification: Any = None) -> bool:
    """True when verify observed that the side-effect did not take hold."""
    if verification is None:
        return False
    if isinstance(verification, dict):
        status = str(verification.get("status") or "").lower()
        reason = str(verification.get("reason") or "")
    else:
        status_raw = getattr(verification, "status", None)
        status = str(
            getattr(status_raw, "value", None) or status_raw or ""
        ).lower()
        reason = str(getattr(verification, "reason", "") or "")
    if verification_missing_capability(reason=reason):
        return False
    if reason in ("execute_failed", "non_mutation"):
        return False
    return status == "failed"


def expected_from_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    name = str(tool_name or "")
    args = dict(arguments or {})
    if name == "play_media":
        return {"query": str(args.get("query") or args.get("title") or "")}
    if name == "open_app":
        return {"app": str(args.get("name") or args.get("app") or args.get("app_name") or "")}
    if name == "turn_on_device":
        return {"state": "on", "entity_id": str(args.get("entity_id") or args.get("target") or "")}
    if name == "turn_off_device":
        return {"state": "off", "entity_id": str(args.get("entity_id") or args.get("target") or "")}
    if name == "set_device_value":
        return {
            "entity_id": str(args.get("entity_id") or args.get("target") or ""),
            "value": args.get("value"),
            "action": str(args.get("action") or ""),
        }
    if name == "volume_up":
        return {"direction": "increase"}
    if name == "volume_down":
        return {"direction": "decrease"}
    return {}


def verify_spec_for(tool_name: str, arguments: Dict[str, Any]) -> Optional[tuple[str, Dict[str, Any]]]:
    name = str(tool_name or "")
    args = dict(arguments or {})
    # HA mutations already run ha_verify inside the tool — verify from observation.
    if name in ("turn_on_device", "turn_off_device", "set_device_value"):
        return None
    if name == "close_app":
        return None
    if name in ("volume_up", "volume_down"):
        return "get_volume", {}
    if name == "play_media":
        return "get_now_playing", {}
    if name in ("pause_media", "resume_media"):
        return "get_now_playing", {}
    if name == "open_app":
        app = str(args.get("name") or args.get("app") or args.get("app_name") or "")
        return "window_exists", {"name": app, "query": app}
    if name == "web_search":
        return None
    return None


def _volume_level(snap: Any) -> Optional[float]:
    if isinstance(snap, (int, float)):
        return float(snap)
    if not isinstance(snap, dict):
        return None
    if "level" in snap and snap.get("level") is not None:
        try:
            return float(snap["level"])
        except (TypeError, ValueError):
            return None
    if "scalar" in snap and snap.get("scalar") is not None:
        try:
            return float(snap["scalar"]) * 100.0
        except (TypeError, ValueError):
            return None
    return None


def _volume_step(snap: Any) -> Optional[int]:
    if not isinstance(snap, dict) or snap.get("step") is None:
        return None
    try:
        return int(snap["step"])
    except (TypeError, ValueError):
        return None


def volume_direction_observed(
    *,
    direction: str,
    before: Any,
    after: Any,
    epsilon: float = 0.05,
) -> Optional[bool]:
    """True/False when before/after prove relative volume change; None if unknown."""
    want = str(direction or "").strip().lower()
    if want in ("up", "increase", "louder"):
        want = "increase"
    elif want in ("down", "decrease", "quieter"):
        want = "decrease"
    else:
        return None
    b = _volume_level(before)
    a = _volume_level(after)
    if b is None or a is None:
        return None
    sb = _volume_step(before)
    sa = _volume_step(after)
    if want == "increase":
        if a > b + epsilon:
            return True
        if sb is not None and sa is not None and sa > sb:
            return True
        # Already at ceiling — key press is a no-op but effect is satisfied.
        if a >= 99.5 and b >= 99.5:
            return True
        if sb is not None and sa is not None and sa == sb:
            steps = None
            if isinstance(after, dict) and after.get("steps") is not None:
                try:
                    steps = int(after["steps"])
                except (TypeError, ValueError):
                    steps = None
            if steps is not None and sa >= max(0, steps - 1):
                return True
        return False
    # decrease
    if a < b - epsilon:
        return True
    if sb is not None and sa is not None and sa < sb:
        return True
    if a <= 0.5 and b <= 0.5:
        return True
    if sb is not None and sa is not None and sa == sb and sa <= 0:
        return True
    return False


def success_criteria_met_from_observation(
    tool_name: str,
    arguments: Dict[str, Any],
    observation: Dict[str, Any],
) -> Optional[bool]:
    """True/False when observation alone proves SuccessCriteria; None → need verify tool."""
    name = str(tool_name or "")
    data = dict(observation.get("data") or {}) if isinstance(observation, dict) else {}
    if name == "web_search":
        results = data.get("ranked_results") or data.get("results") or data.get("hits") or []
        if isinstance(results, list):
            return len(results) > 0
        return False
    if name in ("turn_on_device", "turn_off_device", "set_device_value"):
        outcome = str(data.get("verification") or data.get("outcome") or "").upper()
        if outcome in ("SUCCESS", "ALREADY_SATISFIED"):
            return True
        if outcome in ("FAILED", "UNAVAILABLE"):
            return False
        after = str(data.get("after") or data.get("state") or "").lower()
        expected = expected_from_tool(name, arguments)
        want = str(expected.get("state") or "").lower()
        if want and after:
            if want == "on":
                return after in {str(s).lower() for s in _ON_STATES}
            if want == "off":
                return after in {str(s).lower() for s in _OFF_STATES}
        if observation.get("success") and outcome:
            return outcome in ("SUCCESS", "ALREADY_SATISFIED")
    if name in ("volume_up", "volume_down"):
        direction = str(data.get("direction") or ("increase" if name == "volume_up" else "decrease"))
        return volume_direction_observed(
            direction=direction,
            before=data.get("volume_before") or data.get("before"),
            after=data.get("volume_after") or data.get("after"),
        )
    return None


def _blob(data: Any) -> str:
    if isinstance(data, dict):
        return " ".join(_blob(v) for v in data.values()).lower()
    if isinstance(data, (list, tuple)):
        return " ".join(_blob(v) for v in data).lower()
    return str(data or "").lower()


def _matches_expected(
    tool_name: str,
    expected: Dict[str, Any],
    observation: Dict[str, Any],
) -> bool:
    data = dict(observation.get("data") or {})
    blob = _blob(observation)
    if tool_name in ("get_now_playing", "play_media"):
        query = str(expected.get("query") or "").strip().lower()
        if not query:
            return bool(observation.get("success"))
        tokens = [t for t in query.replace("-", " ").split() if len(t) > 2]
        if not tokens:
            return query in blob
        return any(token in blob for token in tokens)
    if tool_name in ("window_exists", "open_app"):
        app = str(expected.get("app") or "").strip().lower()
        if observation.get("success") and (not app or app in blob or bool(data.get("exists", True))):
            return True
        return False
    if tool_name in ("get_device_state", "turn_on_device", "turn_off_device", "set_device_value"):
        state = str(data.get("state") or data.get("after") or data.get("value") or "").lower()
        want = str(expected.get("state") or "").lower()
        if want == "on":
            return state in {str(s).lower() for s in _ON_STATES} or observation.get("success", False) and "on" in blob
        if want == "off":
            return state in {str(s).lower() for s in _OFF_STATES} or "off" in blob
        value = expected.get("value")
        if value is not None and str(value) and str(value) in blob:
            return True
        return bool(observation.get("success"))
    if tool_name in ("get_volume", "volume_up", "volume_down"):
        direction = str(expected.get("direction") or "").strip().lower()
        before = expected.get("volume_before") or expected.get("before")
        after = data if ("level" in data or "scalar" in data) else (
            expected.get("volume_after") or expected.get("after") or data
        )
        # Prefer fresh get_volume observation as "after".
        observed = volume_direction_observed(direction=direction, before=before, after=after)
        if observed is not None:
            return bool(observed)
        # Absolute level check when expected carries target level.
        want_level = expected.get("level")
        got = _volume_level(data)
        if want_level is not None and got is not None:
            try:
                return abs(float(want_level) - float(got)) <= 1.0
            except (TypeError, ValueError):
                return False
        return bool(observation.get("success")) and bool(before)
    return bool(observation.get("success"))


def verify_effect(
    registry: ToolRegistry,
    *,
    tool_name: str,
    arguments: Dict[str, Any],
    expected: Optional[Dict[str, Any]] = None,
    invoke: Optional[InvokeFn] = None,
    confirmed: bool = True,
    execution_observation: Optional[Dict[str, Any]] = None,
) -> VerificationResult:
    """Read-only check of important effects. Missing verify tool → SKIPPED / obs-based."""
    name = str(tool_name or "")
    args = dict(arguments or {})
    expected = dict(expected or expected_from_tool(name, args))
    exec_obs = dict(execution_observation or {})
    exec_data = dict(exec_obs.get("data") or {}) if isinstance(exec_obs, dict) else {}
    if name in ("volume_up", "volume_down"):
        expected.setdefault(
            "direction",
            exec_data.get("direction")
            or ("increase" if name == "volume_up" else "decrease"),
        )
        if exec_data.get("volume_before") is not None:
            expected.setdefault("volume_before", exec_data.get("volume_before"))
        if exec_data.get("before") is not None:
            expected.setdefault("before", exec_data.get("before"))
        if exec_data.get("volume_after") is not None:
            expected.setdefault("volume_after", exec_data.get("volume_after"))
        if exec_data.get("after") is not None:
            expected.setdefault("after", exec_data.get("after"))
    obs_check = success_criteria_met_from_observation(
        name, args, exec_obs
    )
    if name == "web_search":
        if obs_check is True:
            return VerificationResult(
                status=VerifyStatus.PASSED,
                reason="web_results_present",
                matches_expected=True,
                observation=dict(execution_observation or {}),
            )
        if obs_check is False:
            return VerificationResult(
                status=VerifyStatus.FAILED,
                reason="web_results_missing",
                observation=dict(execution_observation or {}),
            )
        return VerificationResult(status=VerifyStatus.SKIPPED, reason="no_web_observation")

    spec = verify_spec_for(name, args)
    if spec is None:
        if obs_check is True:
            return VerificationResult(
                status=VerifyStatus.PASSED,
                reason="observation_criteria_met",
                matches_expected=True,
                observation=dict(execution_observation or {}),
            )
        if obs_check is False:
            return VerificationResult(
                status=VerifyStatus.FAILED,
                reason="observation_criteria_failed",
                observation=dict(execution_observation or {}),
            )
        return VerificationResult(status=VerifyStatus.SKIPPED, reason="no_verify_spec")

    verify_tool, verify_args = spec
    if registry.get(verify_tool) is None:
        # HA: fall back to embedded ha_verify in execute observation.
        if obs_check is True:
            return VerificationResult(
                status=VerifyStatus.PASSED,
                tool=verify_tool,
                reason="ha_embedded_verify",
                matches_expected=True,
                observation=dict(execution_observation or {}),
            )
        if obs_check is False:
            return VerificationResult(
                status=VerifyStatus.FAILED,
                tool=verify_tool,
                reason="ha_embedded_verify_failed",
                observation=dict(execution_observation or {}),
            )
        # Missing required verify tool is not an intentional skip — fail closed.
        return VerificationResult(
            status=VerifyStatus.FAILED,
            tool=verify_tool,
            reason="verify_tool_unregistered",
        )
    call = invoke or (lambda n, a, c: registry.invoke(n, a, confirmed=c))
    try:
        result = call(verify_tool, dict(verify_args), True if confirmed else True)
    except Exception as exc:
        if obs_check is True:
            return VerificationResult(
                status=VerifyStatus.PASSED,
                tool=verify_tool,
                reason="verify_error_but_obs_ok",
                matches_expected=True,
                observation=dict(execution_observation or {}),
            )
        return VerificationResult(
            status=VerifyStatus.UNKNOWN,
            tool=verify_tool,
            reason=f"verify_error:{exc}"[:120],
        )
    observation = result.to_observation() if hasattr(result, "to_observation") else {}
    if result.status in (ToolStatus.UNAVAILABLE, ToolStatus.UNSUPPORTED):
        if obs_check is True:
            return VerificationResult(
                status=VerifyStatus.PASSED,
                tool=verify_tool,
                observation=observation,
                matches_expected=True,
                reason="verify_unavailable_obs_ok",
            )
        return VerificationResult(
            status=VerifyStatus.UNKNOWN,
            tool=verify_tool,
            observation=observation,
            reason="verify_unavailable",
        )
    if not result.success:
        if obs_check is True and name in ("turn_on_device", "turn_off_device", "set_device_value"):
            return VerificationResult(
                status=VerifyStatus.PASSED,
                tool=verify_tool,
                observation=observation,
                matches_expected=True,
                reason="ha_obs_fallback_after_verify_fail",
            )
        return VerificationResult(
            status=VerifyStatus.FAILED,
            tool=verify_tool,
            observation=observation,
            reason=str(result.error or "verify_tool_failed"),
        )
    matched = _matches_expected(verify_tool, expected, observation)
    return VerificationResult(
        status=VerifyStatus.PASSED if matched else VerifyStatus.FAILED,
        tool=verify_tool,
        observation=observation,
        matches_expected=matched,
        reason="matched" if matched else "effect_mismatch",
    )


def diagnose_failure(
    *,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
    execution: Optional[ExecutionResult] = None,
    verification: Optional[VerificationResult] = None,
    classification: Optional[IntentClassification] = None,
) -> "FailureDiagnosis":
    """Map execute/verify outcome → FailureCategory for repair policy."""
    from core.execution_context import (
        FailureCategory,
        FailureDiagnosis,
        action_fingerprint,
        capability_missing_for_tool,
    )

    name = str(tool_name or "")
    args = dict(arguments or {})
    fp = action_fingerprint(name, args)
    unsure = material_uncertainty(name, args)
    if unsure or (
        execution
        and getattr(execution, "blocked", False)
        and str(getattr(execution, "block_reason", "") or "") == "material_uncertainty"
    ):
        return FailureDiagnosis(
            category=FailureCategory.INVALID_ARGS,
            tool=name,
            message=str(unsure or "Brakuje argumentu."),
            retryable=False,
            fingerprint=fp,
        )
    if execution and getattr(execution, "blocked", False):
        reason = str(getattr(execution, "block_reason", "") or "")
        if "ambiguous" in reason or reason == "material_uncertainty":
            cat = FailureCategory.AMBIGUOUS_TARGET
        elif "permission" in reason or "firewall" in reason or "safety" in reason:
            cat = FailureCategory.PERMISSION_DENIED
        else:
            cat = FailureCategory.PERMISSION_DENIED
        return FailureDiagnosis(
            category=cat,
            tool=name,
            message=reason,
            retryable=False,
            fingerprint=fp,
        )

    result = getattr(execution, "result", None) if execution else None
    status = getattr(result, "status", None) if result is not None else None
    error = str(getattr(result, "error", "") or getattr(execution, "block_reason", "") or "")
    obs = dict(getattr(execution, "observation", None) or {}) if execution else {}
    data = dict(obs.get("data") or {})

    if capability_missing_for_tool(name) or status == ToolStatus.UNSUPPORTED:
        return FailureDiagnosis(
            category=FailureCategory.CAPABILITY_MISSING,
            tool=name,
            message=error or "capability_missing",
            retryable=False,
            fingerprint=fp,
        )
    if status == ToolStatus.UNAVAILABLE or "unavailable" in error.lower() or "offline" in error.lower():
        return FailureDiagnosis(
            category=FailureCategory.DEPENDENCY_UNAVAILABLE,
            tool=name,
            message=error or "dependency_unavailable",
            retryable=True,
            fingerprint=fp,
        )
    if "timeout" in error.lower() or "timed out" in error.lower():
        return FailureDiagnosis(
            category=FailureCategory.TIMEOUT,
            tool=name,
            message=error or "timeout",
            retryable=True,
            fingerprint=fp,
        )
    if "permission" in error.lower() or "denied" in error.lower() or "access" in error.lower():
        return FailureDiagnosis(
            category=FailureCategory.PERMISSION_DENIED,
            tool=name,
            message=error or "permission_denied",
            retryable=False,
            fingerprint=fp,
        )
    if "ambiguous" in error.lower() or "które" in str(obs.get("message") or "").lower():
        return FailureDiagnosis(
            category=FailureCategory.AMBIGUOUS_TARGET,
            tool=name,
            message=error or "ambiguous_target",
            retryable=False,
            fingerprint=fp,
        )
    if status == ToolStatus.ERROR or "exception" in error.lower() or "traceback" in error.lower():
        return FailureDiagnosis(
            category=FailureCategory.TOOL_EXCEPTION,
            tool=name,
            message=error or "tool_exception",
            retryable=True,
            fingerprint=fp,
        )

    ver_status = getattr(verification, "status", None) if verification else None
    execute_ok = bool(getattr(execution, "success", False)) if execution else False

    if execution and not execute_ok:
        if data.get("side_effect_may_have_occurred"):
            return FailureDiagnosis(
                category=FailureCategory.STATE_NOT_CHANGED,
                tool=name,
                message=error or "state_uncertain",
                retryable=False,
                fingerprint=fp,
            )
        return FailureDiagnosis(
            category=FailureCategory.TOOL_EXCEPTION if error else FailureCategory.UNKNOWN,
            tool=name,
            message=error or "execute_failed",
            retryable=bool(getattr(execution, "retryable", False)),
            fingerprint=fp,
        )

    if ver_status == VerifyStatus.FAILED:
        reason = str(getattr(verification, "reason", "") or "")
        if reason in ("effect_mismatch", "ha_embedded_verify_failed") or "mismatch" in reason:
            return FailureDiagnosis(
                category=FailureCategory.STATE_NOT_CHANGED,
                tool=name,
                message=reason,
                retryable=name in IDEMPOTENT_MUTATIONS,
                fingerprint=fp,
                detail={"verification": getattr(verification, "to_dict", lambda: {})()},
            )
        return FailureDiagnosis(
            category=FailureCategory.VERIFICATION_FAILED,
            tool=name,
            message=reason or "verification_failed",
            retryable=name in IDEMPOTENT_MUTATIONS,
            fingerprint=fp,
        )

    _ = classification  # reserved for family-aware diagnosis
    return FailureDiagnosis(
        category=FailureCategory.UNKNOWN,
        tool=name,
        message=error or "unknown",
        retryable=False,
        fingerprint=fp,
    )


def service_already_active(world: Dict[str, Any], app: str) -> bool:
    name = str(app or "").strip().lower()
    if not name:
        return False
    services = world.get("services") or {}
    if isinstance(services, dict):
        if name in {str(k).lower() for k in services.keys()}:
            return True
        payload = services if "service_name" in services else {}
        svc_name = str(payload.get("service_name") or payload.get("name") or "").lower()
        if name and name == svc_name:
            return True
        for value in services.values():
            if isinstance(value, dict):
                sn = str(value.get("service_name") or value.get("name") or "").lower()
                if name == sn or name in sn:
                    return True
    computer = world.get("computer") or {}
    blob = _blob(computer)
    return bool(name and name in blob)


def enrich_compound_preconditions(plan, *, world: Optional[Dict[str, Any]] = None, registry: Optional[ToolRegistry] = None):
    """check app → open if needed. Skip when WorldState already shows it running."""
    from core.compound_plan import CompoundStep

    world = dict(world or {})
    steps = list(getattr(plan, "steps", None) or [])
    opened: set[str] = set()
    new_steps = []
    for step in steps:
        action = str(getattr(step, "semantic_action", "") or "")
        slots = dict(getattr(step, "slots", None) or {})
        family = str(getattr(step, "family", "") or "")
        if action in ("open_service",) or family == IntentFamily.APP.value:
            target = str(slots.get("target") or slots.get("app_name") or "").strip().lower()
            if target:
                opened.add(target)
        if action in ("play_media", "play_track"):
            app = str(slots.get("app_name") or slots.get("preferred_service") or "").strip()
            key = app.lower()
            if app and key not in opened:
                if service_already_active(world, app):
                    opened.add(key)
                elif registry is None or registry.get("open_app") is not None:
                    new_steps.append(
                        CompoundStep(
                            family=IntentFamily.APP.value,
                            semantic_action="open_service",
                            text=f"Otwórz {app}",
                            complete=True,
                            slots={
                                "target": app,
                                "app_name": app,
                                "precondition": True,
                            },
                        )
                    )
                    opened.add(key)
        new_steps.append(step)
    plan.steps = new_steps
    return plan


def _trace(phase: str, **fields: Any) -> Dict[str, Any]:
    row = {"phase": phase, **{k: v for k, v in fields.items() if v is not None}}
    print(
        "[CYCLE] "
        + " ".join(f"{key}={value}" for key, value in row.items() if key != "observation")
    )
    log_turn("plan_cycle", **{k: v for k, v in row.items() if k != "observation"})
    return row


def run_execute_verify_repair(
    registry: ToolRegistry,
    *,
    tool_name: str,
    arguments: Dict[str, Any],
    classification: IntentClassification,
    context: Optional[Dict[str, Any]] = None,
    goal: str = "",
    invoke: Optional[InvokeFn] = None,
    confirmed: bool = False,
    prior_verified: bool = False,
    repair_used: int = 0,
    max_repair: int = 2,
    execute_result: Optional[ToolResult] = None,
    force_alternate: bool = False,
    blocked_fingerprints: Optional[List[str]] = None,
) -> StepCycleOutcome:
    """EXECUTE → VERIFY → controlled REPAIR. Never re-mutate a verified success."""
    ctx = dict(context or {})
    args = dict(arguments or {})
    name = str(tool_name or "")
    call = invoke or (lambda n, a, c: registry.invoke(n, a, confirmed=c))
    envelope = ctx.get("decision_envelope")
    budgets = cycle_budgets()
    max_repair = max(1, int(max_repair or budgets["max_repair_per_step"]))
    force_alternate = bool(force_alternate or ctx.get("force_alternate"))
    blocked_fps = {
        str(item)
        for item in (blocked_fingerprints or ctx.get("failed_fingerprints") or [])
        if item
    }
    trace: List[Dict[str, Any]] = []

    if prior_verified:
        skip_exec = ExecutionResult(
            success=True,
            tool=name,
            arguments=args,
            skipped=True,
        )
        skip_ver = VerificationResult(status=VerifyStatus.PASSED, reason="prior_verified")
        trace.append(_trace(CyclePhase.EXECUTE.value, tool=name, skipped=True, reason="verified_success"))
        return StepCycleOutcome(
            execution=skip_exec,
            verification=skip_ver,
            repairs_used=0,
            verified_success=True,
            trace=trace,
        )

    skip_pre_guards = execute_result is not None

    if skip_pre_guards:
        unsure = None
    else:
        unsure = material_uncertainty(name, args)
    if unsure:
        question = "Możesz sprecyzować, co mam zrobić?"
        if name == "play_media":
            question = "Co mam puścić?"
        elif name == "open_app":
            question = "Którą aplikację mam otworzyć?"
        elif name in ("turn_on_device", "turn_off_device", "set_device_value"):
            question = "Które urządzenie masz na myśli?"
        trace.append(_trace(CyclePhase.ASK.value, tool=name, reason="material_uncertainty"))
        blocked_exec = ExecutionResult(
            success=False,
            tool=name,
            arguments=args,
            blocked=True,
            block_reason="material_uncertainty",
        )
        diagnosis = diagnose_failure(
            tool_name=name,
            arguments=args,
            execution=blocked_exec,
            classification=classification,
        )
        return StepCycleOutcome(
            execution=blocked_exec,
            verification=VerificationResult(status=VerifyStatus.SKIPPED, reason="ask"),
            repair=RepairDecision(action=RepairAction.ASK, reason="material_uncertainty", question=question),
            ask=True,
            ask_question=question,
            diagnosis=diagnosis,
            trace=trace,
        )

    if not skip_pre_guards and side_effect_blocked_for_intent(classification, name, args):
        trace.append(_trace(CyclePhase.FAIL.value, tool=name, reason="safety_block"))
        blocked_exec = ExecutionResult(
            success=False,
            tool=name,
            arguments=args,
            blocked=True,
            block_reason="safety_block",
        )
        diagnosis = diagnose_failure(
            tool_name=name,
            arguments=args,
            execution=blocked_exec,
            classification=classification,
        )
        return StepCycleOutcome(
            execution=blocked_exec,
            verification=VerificationResult(status=VerifyStatus.SKIPPED, reason="safety_block"),
            repair=RepairDecision(action=RepairAction.FAIL, reason="safety_block"),
            blocked=True,
            diagnosis=diagnosis,
            trace=trace,
        )

    allowed, fw_reason = (True, "post_execute") if skip_pre_guards else _firewall_allows(
        envelope, name, str(goal or "")
    )
    if not allowed and name in MUTATION_TOOLS:
        trace.append(_trace(CyclePhase.FAIL.value, tool=name, reason=f"firewall:{fw_reason}"))
        blocked_exec = ExecutionResult(
            success=False,
            tool=name,
            arguments=args,
            blocked=True,
            block_reason=fw_reason,
        )
        diagnosis = diagnose_failure(
            tool_name=name,
            arguments=args,
            execution=blocked_exec,
            classification=classification,
        )
        return StepCycleOutcome(
            execution=blocked_exec,
            verification=VerificationResult(status=VerifyStatus.SKIPPED, reason=fw_reason),
            repair=RepairDecision(action=RepairAction.FAIL, reason=f"firewall:{fw_reason}"),
            blocked=True,
            diagnosis=diagnosis,
            trace=trace,
        )

    from core.execution_context import action_fingerprint

    current_fp = action_fingerprint(name, args)
    if force_alternate and current_fp in blocked_fps and execute_result is None:
        fake_exec = ExecutionResult(
            success=False,
            tool=name,
            arguments=args,
            retryable=True,
            observation={"success": False, "error": "duplicate_failed_path"},
        )
        fake_ver = VerificationResult(status=VerifyStatus.FAILED, reason="duplicate_failed_path")
        diagnosis = diagnose_failure(
            tool_name=name,
            arguments=args,
            execution=fake_exec,
            verification=fake_ver,
            classification=classification,
        )
        from core.agent_repair import decide_execution_repair

        decision = decide_execution_repair(
            tool_name=name,
            arguments=args,
            execution=fake_exec,
            verification=fake_ver,
            classification=classification,
            goal=goal,
            repair_used=repair_used,
            max_repair=max_repair,
            diagnosis=diagnosis,
            force_alternate=True,
            blocked_fingerprints=list(blocked_fps),
        )
        trace.append(
            _trace(
                CyclePhase.REPAIR.value,
                action=decision.action.value,
                reason="force_alternate_skip_duplicate",
                tool=decision.tool or name,
            )
        )
        if decision.action in (RepairAction.RETRY, RepairAction.ALTERNATE):
            alt_tool = decision.tool or name
            alt_args = dict(decision.arguments or args)
            if action_fingerprint(alt_tool, alt_args) == current_fp:
                return StepCycleOutcome(
                    execution=fake_exec,
                    verification=fake_ver,
                    repair=RepairDecision(action=RepairAction.FAIL, reason="no_alternate_path"),
                    diagnosis=diagnosis,
                    verified_success=False,
                    trace=trace,
                )
            name = alt_tool
            args = alt_args
        else:
            return StepCycleOutcome(
                execution=fake_exec,
                verification=fake_ver,
                repair=decision,
                diagnosis=diagnosis,
                ask=decision.action == RepairAction.ASK,
                ask_question=decision.question,
                verified_success=False,
                trace=trace,
            )

    result = execute_result
    if result is None:
        result = call(name, args, confirmed)
    observation = result.to_observation() if hasattr(result, "to_observation") else {}
    execution = ExecutionResult(
        success=bool(result.success),
        tool=name,
        arguments=args,
        observation=observation,
        retryable=bool(getattr(result, "retryable", False) or result.status == ToolStatus.RETRYABLE),
        result=result,
    )
    trace.append(
        _trace(
            CyclePhase.EXECUTE.value,
            tool=name,
            success=execution.success,
            error=str(getattr(result, "error", "") or ""),
        )
    )

    if result.status == ToolStatus.NEEDS_CONFIRMATION:
        return StepCycleOutcome(
            execution=execution,
            verification=VerificationResult(status=VerifyStatus.SKIPPED, reason="needs_confirmation"),
            trace=trace,
        )

    mutation = is_side_effect_tool(name) or name in MUTATION_TOOLS or name == "web_search"
    verification = VerificationResult(status=VerifyStatus.SKIPPED, reason="non_mutation")
    if mutation and execution.success:
        verification = verify_effect(
            registry,
            tool_name=name,
            arguments=args,
            expected=expected_from_tool(name, args),
            invoke=call,
            execution_observation=observation,
        )
        trace.append(
            _trace(
                CyclePhase.VERIFY.value,
                tool=verification.tool or name,
                status=verification.status.value,
                reason=verification.reason,
            )
        )
    elif mutation and not execution.success:
        verification = VerificationResult(
            status=VerifyStatus.FAILED,
            reason=str(getattr(result, "error", "") or "execute_failed"),
        )
        trace.append(
            _trace(CyclePhase.VERIFY.value, tool=name, status=VerifyStatus.FAILED.value, reason="execute_failed")
        )

    if verification.status == VerifyStatus.PASSED:
        return StepCycleOutcome(
            execution=execution,
            verification=verification,
            verified_success=True,
            trace=trace,
        )
    # SKIPPED/UNKNOWN must NOT become verified_success unless intentionally allowlisted
    # (non_mutation only). no_verify_spec / verify_tool_unregistered never qualify.
    if (
        verification.status in (VerifyStatus.SKIPPED, VerifyStatus.UNKNOWN)
        and execution.success
        and verification_allows_success_claim(verification)
    ):
        return StepCycleOutcome(
            execution=execution,
            verification=verification,
            verified_success=True,
            trace=trace,
        )
    if (
        execution.success
        and str(getattr(verification, "reason", "") or "") == "verify_tool_unregistered"
    ):
        # Required verify tool missing — fail closed; do not remutate / claim success.
        return StepCycleOutcome(
            execution=execution,
            verification=verification,
            repair=RepairDecision(action=RepairAction.FAIL, reason="verify_tool_unregistered"),
            verified_success=False,
            diagnosis=diagnose_failure(
                tool_name=name,
                arguments=args,
                execution=execution,
                verification=verification,
                classification=classification,
            ),
            trace=trace,
        )

    from core.agent_repair import decide_execution_repair

    repairs_used = 0
    current = result
    current_exec = execution
    current_ver = verification
    last_repair: Optional[RepairDecision] = None
    last_diagnosis = diagnose_failure(
        tool_name=name,
        arguments=args,
        execution=current_exec,
        verification=current_ver,
        classification=classification,
    )
    while repairs_used < max_repair:
        decision = decide_execution_repair(
            tool_name=name,
            arguments=args,
            execution=current_exec,
            verification=current_ver,
            classification=classification,
            goal=goal,
            repair_used=repair_used + repairs_used,
            max_repair=max_repair,
            diagnosis=last_diagnosis,
            force_alternate=force_alternate,
            blocked_fingerprints=list(blocked_fps),
        )
        last_repair = decision
        cat = getattr(getattr(last_diagnosis, "category", None), "value", None) or str(
            getattr(last_diagnosis, "category", "") or ""
        )
        trace.append(
            _trace(
                CyclePhase.REPAIR.value,
                action=decision.action.value,
                reason=decision.reason,
                tool=decision.tool or name,
                diagnosis=cat or None,
            )
        )
        if decision.action == RepairAction.ASK:
            return StepCycleOutcome(
                execution=current_exec,
                verification=current_ver,
                repair=decision,
                repairs_used=repairs_used,
                ask=True,
                ask_question=decision.question or "Możesz sprecyzować, co mam zrobić?",
                diagnosis=last_diagnosis,
                trace=trace,
            )
        if decision.action in (RepairAction.FAIL, RepairAction.SKIP):
            break
        if decision.action in (RepairAction.RETRY, RepairAction.ALTERNATE):
            retry_tool = decision.tool or name
            retry_args = dict(decision.arguments or args)
            retry_fp = action_fingerprint(retry_tool, retry_args)
            if current_ver.status == VerifyStatus.PASSED:
                break
            if (
                retry_fp in blocked_fps
                and decision.action == RepairAction.RETRY
                and force_alternate
            ):
                alt = decide_execution_repair(
                    tool_name=name,
                    arguments=args,
                    execution=current_exec,
                    verification=current_ver,
                    classification=classification,
                    goal=goal,
                    repair_used=repair_used + repairs_used,
                    max_repair=max_repair,
                    diagnosis=last_diagnosis,
                    force_alternate=True,
                    blocked_fingerprints=list(blocked_fps) + [retry_fp],
                )
                if alt.action == RepairAction.ALTERNATE and action_fingerprint(
                    alt.tool or "", alt.arguments or {}
                ) != retry_fp:
                    decision = alt
                    retry_tool = decision.tool or name
                    retry_args = dict(decision.arguments or args)
                    retry_fp = action_fingerprint(retry_tool, retry_args)
                else:
                    last_repair = RepairDecision(action=RepairAction.FAIL, reason="duplicate_path_blocked")
                    break
            current = call(retry_tool, retry_args, confirmed)
            current_exec = ExecutionResult(
                success=bool(current.success),
                tool=retry_tool,
                arguments=retry_args,
                observation=current.to_observation(),
                retryable=bool(current.retryable or current.status == ToolStatus.RETRYABLE),
                result=current,
            )
            name = retry_tool
            args = retry_args
            blocked_fps.add(action_fingerprint(name, args))
            repairs_used += 1
            if current.status == ToolStatus.NEEDS_CONFIRMATION:
                break
            if current_exec.success:
                current_ver = verify_effect(
                    registry,
                    tool_name=retry_tool,
                    arguments=retry_args,
                    expected=expected_from_tool(retry_tool, retry_args),
                    invoke=call,
                    execution_observation=current_exec.observation,
                )
            else:
                current_ver = VerificationResult(
                    status=VerifyStatus.FAILED,
                    reason=str(current.error or "execute_failed"),
                )
            last_diagnosis = diagnose_failure(
                tool_name=retry_tool,
                arguments=retry_args,
                execution=current_exec,
                verification=current_ver,
                classification=classification,
            )
            trace.append(
                _trace(
                    CyclePhase.EXECUTE.value,
                    tool=retry_tool,
                    success=current_exec.success,
                    repair_attempt=repairs_used,
                )
            )
            trace.append(
                _trace(
                    CyclePhase.VERIFY.value,
                    tool=current_ver.tool or retry_tool,
                    status=current_ver.status.value,
                    reason=current_ver.reason,
                    repair_attempt=repairs_used,
                )
            )
            if current_ver.status == VerifyStatus.PASSED or (
                current_exec.success
                and current_ver.status in (VerifyStatus.SKIPPED, VerifyStatus.UNKNOWN)
                and verification_allows_success_claim(current_ver)
            ):
                return StepCycleOutcome(
                    execution=current_exec,
                    verification=current_ver,
                    repair=decision,
                    repairs_used=repairs_used,
                    verified_success=True,
                    diagnosis=last_diagnosis,
                    trace=trace,
                )
            if (
                current_exec.success
                and str(getattr(current_ver, "reason", "") or "") == "verify_tool_unregistered"
            ):
                return StepCycleOutcome(
                    execution=current_exec,
                    verification=current_ver,
                    repair=RepairDecision(
                        action=RepairAction.FAIL, reason="verify_tool_unregistered"
                    ),
                    repairs_used=repairs_used,
                    verified_success=False,
                    diagnosis=last_diagnosis,
                    trace=trace,
                )
            continue
        break

    if last_repair is None:
        last_repair = RepairDecision(action=RepairAction.FAIL, reason="unrepaired")
    return StepCycleOutcome(
        execution=current_exec,
        verification=current_ver,
        repair=last_repair,
        repairs_used=repairs_used,
        verified_success=False,
        diagnosis=last_diagnosis,
        trace=trace,
    )



def plan_status_from_rows(rows: List[Dict[str, Any]]) -> str:
    from core.execution_context import step_row_outcome

    outcomes = [
        step_row_outcome(item) if isinstance(item, dict) else ""
        for item in (rows or [])
    ]
    failed_any = any(o == STEP_FAILED for o in outcomes)
    skipped_any = any(o == STEP_SKIPPED for o in outcomes)
    succeeded_any = any(o == STEP_SUCCEEDED for o in outcomes)
    unknown_any = any(not o for o in outcomes)
    if failed_any and (succeeded_any or skipped_any):
        return PLAN_PARTIAL
    if failed_any:
        return PLAN_FAILED
    if unknown_any:
        # Bad-shape / incomplete rows: never claim PLAN_COMPLETED.
        return PLAN_PARTIAL if succeeded_any else PLAN_RUNNING
    if succeeded_any or (outcomes and all(o == STEP_SKIPPED for o in outcomes)):
        return PLAN_COMPLETED
    if rows:
        # Legacy fallback was PLAN_COMPLETED; fail closed for empty outcomes.
        return PLAN_RUNNING
    return PLAN_RUNNING


def deadline_exceeded(started_at: float, timeout_s: float) -> bool:
    return bool(timeout_s) and (time.monotonic() - float(started_at)) > float(timeout_s)
