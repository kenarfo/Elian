# ==========================
# ELIAN — ActiveGoal / ConversationGoal (2.0.0-rc1)
# ==========================
"""Persistent conversational goal — meaning beyond intent_family.

Tracks what the user wants across turns. Does not execute tools.
ConversationManager and Semantic Executive consume this for unresolved
awareness, parameter follow-ups, reference picks, and honest failure dialogue.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from core.utterance_class import UtteranceClass, UtteranceClassification, classify_utterance


class GoalStatus(str, Enum):
    DISCUSSED = "DISCUSSED"  # mentioned / planned verbally, not started
    ACTIVE = "ACTIVE"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    UNRESOLVED = "UNRESOLVED"  # attempted, did not complete
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


@dataclass
class ActiveGoal:
    """Canonical conversation goal structure."""

    id: str = ""
    objective: str = ""
    target: str = ""
    constraints: List[str] = field(default_factory=list)
    status: str = GoalStatus.ACTIVE.value
    confidence: float = 0.0
    active_references: List[str] = field(default_factory=list)
    previous_plan: List[Dict[str, Any]] = field(default_factory=list)
    last_action: str = ""
    last_result: Dict[str, Any] = field(default_factory=dict)
    unresolved_reason: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    # Additive tracking (still part of the goal contract for consumers)
    domain: str = ""
    parameter: str = ""  # brightness | volume | temperature | …
    parameter_direction: str = ""  # more | less | brighter | …
    presented_options: List[str] = field(default_factory=list)
    utterance_class: str = ""
    intent_family_signal: str = ""
    discussed_action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any] | None) -> Optional["ActiveGoal"]:
        if not isinstance(raw, dict):
            return None
        try:
            return cls(
                id=str(raw.get("id") or ""),
                objective=str(raw.get("objective") or ""),
                target=str(raw.get("target") or ""),
                constraints=[str(x) for x in (raw.get("constraints") or []) if str(x)],
                status=str(raw.get("status") or GoalStatus.ACTIVE.value),
                confidence=float(raw.get("confidence") or 0.0),
                active_references=[
                    str(x) for x in (raw.get("active_references") or []) if str(x)
                ],
                previous_plan=[
                    dict(x) for x in (raw.get("previous_plan") or []) if isinstance(x, dict)
                ],
                last_action=str(raw.get("last_action") or ""),
                last_result=dict(raw.get("last_result") or {})
                if isinstance(raw.get("last_result"), dict)
                else {},
                unresolved_reason=str(raw.get("unresolved_reason") or ""),
                created_at=float(raw.get("created_at") or 0.0),
                updated_at=float(raw.get("updated_at") or 0.0),
                domain=str(raw.get("domain") or ""),
                parameter=str(raw.get("parameter") or ""),
                parameter_direction=str(raw.get("parameter_direction") or ""),
                presented_options=[
                    str(x) for x in (raw.get("presented_options") or []) if str(x)
                ],
                utterance_class=str(raw.get("utterance_class") or ""),
                intent_family_signal=str(raw.get("intent_family_signal") or ""),
                discussed_action=str(raw.get("discussed_action") or ""),
            )
        except Exception:
            return None

    @property
    def is_unresolved(self) -> bool:
        return self.status in (
            GoalStatus.UNRESOLVED.value,
            GoalStatus.BLOCKED.value,
        )

    @property
    def is_open(self) -> bool:
        return self.status in (
            GoalStatus.DISCUSSED.value,
            GoalStatus.ACTIVE.value,
            GoalStatus.IN_PROGRESS.value,
            GoalStatus.UNRESOLVED.value,
            GoalStatus.BLOCKED.value,
        )


# Alias for docs / callers that prefer ConversationGoal naming.
ConversationGoal = ActiveGoal


_NON_TOOL_ACTIONS = frozenset(
    {
        "",
        "agent",
        "final",
        "talk",
        "meta",
        "ack",
        "knowledge",
        "semantic",
        "social",
        "correction",
        "false_premise",
        "perception",
        "chat",
        "planner",
        "clarify",
        "action_loop",
        "fast_path",
    }
)


def is_vacuous_goal_reply(message: str) -> bool:
    """True for empty / ACK-theater / finalize stall replies (not real completion)."""
    text = str(message or "").strip()
    if not text:
        return True
    lowered = text.lower()
    if "sprawdzam to dokładniej" in lowered or "let me check that properly" in lowered:
        return True
    try:
        from core.response_finalize import is_ack_like

        if is_ack_like(text):
            return True
    except Exception:
        pass
    return False


def has_goal_completion_evidence(
    *,
    result: Optional[Dict[str, Any]] = None,
    observations: Optional[Sequence[Any]] = None,
    steps: Optional[Sequence[Any]] = None,
    action: str = "",
) -> bool:
    """COMPLETED requires successful tool observations and/or verified steps.

    Caller aspiration (success=True) alone is never enough.
    """
    payload = dict(result) if isinstance(result, dict) else {}

    obs_list: List[Any] = []
    if observations:
        obs_list.extend(list(observations))
    nested_obs = payload.get("observations")
    if isinstance(nested_obs, list):
        obs_list.extend(nested_obs)
    for item in obs_list:
        if isinstance(item, dict) and bool(item.get("success")):
            return True

    step_list: List[Any] = []
    if steps:
        step_list.extend(list(steps))
    nested_steps = payload.get("steps")
    if isinstance(nested_steps, list):
        step_list.extend(nested_steps)
    for item in step_list:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").upper()
        if status in (
            "SUCCEEDED",
            "COMPLETED",
            "VERIFIED",
            "SUCCESS",
            "STEP_SUCCEEDED",
            "SKIPPED",
        ):
            return True
        if item.get("verified_success"):
            return True
        obs = item.get("observation")
        if isinstance(obs, dict) and bool(obs.get("success")):
            return True

    for key in ("completed_step_ids", "successful_step_ids", "successful_steps"):
        ids = payload.get(key)
        if isinstance(ids, list) and any(str(x).strip() for x in ids):
            return True

    action_name = str(
        action or payload.get("tool") or payload.get("tool_name") or ""
    ).strip()
    if action_name.lower() not in _NON_TOOL_ACTIONS:
        if payload.get("success") is True:
            return True
        status = str(payload.get("status") or "").lower()
        if status in ("success", "ok"):
            return True

    return False




def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _domain_from_envelope(envelope: Any) -> str:
    domains = list(getattr(envelope, "allowed_domains", None) or [])
    if domains:
        return str(domains[0])
    intent = str(getattr(envelope, "intent", "") or "").lower()
    if "smart_home" in intent or "light" in intent:
        return "SMART_HOME"
    if "media" in intent:
        return "MEDIA"
    if "web" in intent:
        return "WEB"
    if "app" in intent or "open" in intent or "close" in intent:
        return "APP"
    return ""


def _parameter_from_hint(hint: str, domain: str) -> tuple[str, str]:
    if hint in ("brighter", "darker"):
        return "brightness", hint
    if hint in ("more", "less", "louder", "quieter"):
        if domain == "SMART_HOME":
            return "brightness", "brighter" if hint in ("more", "louder") else "darker"
        return "volume", hint if hint in ("louder", "quieter") else (
            "louder" if hint == "more" else "quieter"
        )
    return "", hint


def _direction_from_last_action(goal: Optional[ActiveGoal]) -> str:
    if goal is None:
        return ""
    action = str(getattr(goal, "last_action", "") or "").casefold()
    mapping = {
        "volume_up": "louder",
        "volume_down": "quieter",
        "brightness_up": "brighter",
        "brightness_down": "darker",
        "set_device_value": "",  # ambiguous without soft_kind
    }
    return mapping.get(action, "")


def _parameter_from_last_action(goal: Optional[ActiveGoal]) -> str:
    if goal is None:
        return ""
    action = str(getattr(goal, "last_action", "") or "").casefold()
    if action.startswith("volume_"):
        return "volume"
    if action.startswith("brightness_") or action in (
        "set_device_value",
        "turn_on_device",
        "turn_off_device",
    ):
        if str(getattr(goal, "domain", "") or "").upper() in ("SMART_HOME", "LIGHT", ""):
            if action.startswith("brightness_") or action == "set_device_value":
                return "brightness"
    return ""


def build_goal_from_semantics(
    text: str,
    *,
    envelope: Any = None,
    classification: Optional[UtteranceClassification] = None,
    conversation: Optional[Dict[str, Any]] = None,
) -> ActiveGoal:
    """Create / sketch a goal from the current utterance + envelope."""
    cls = classification or classify_utterance(text, conversation=conversation)
    now = _now()
    objective = str(getattr(envelope, "user_goal", "") or text or "").strip()
    domain = _domain_from_envelope(envelope)
    param, direction = _parameter_from_hint(cls.parameter_hint, domain)
    if cls.parameter_hint in ("brighter", "darker") or "ciemno" in str(text).lower():
        domain = domain or "SMART_HOME"
        param = param or "brightness"
        direction = direction or cls.parameter_hint or "brighter"
    refs = list(getattr(envelope, "referents", None) or [])
    if cls.needs_referent and "current" not in refs:
        refs.append("current_referent")
    status = GoalStatus.ACTIVE.value
    if cls.utterance_class in (
        UtteranceClass.CASUAL_TALK.value,
        UtteranceClass.INFORMATION_REQUEST.value,
    ):
        status = GoalStatus.DISCUSSED.value
    conf = float(getattr(envelope, "confidence", 0.0) or cls.confidence)
    return ActiveGoal(
        id=_new_id(),
        objective=objective[:240],
        target=str(getattr(envelope, "payload", "") or "")[:120],
        constraints=[],
        status=status,
        confidence=conf,
        active_references=refs[:8],
        created_at=now,
        updated_at=now,
        domain=domain,
        parameter=param,
        parameter_direction=direction,
        utterance_class=cls.utterance_class,
        intent_family_signal=str(getattr(envelope, "intent", "") or ""),
        discussed_action=objective[:160]
        if cls.utterance_class
        in (
            UtteranceClass.GOAL.value,
            UtteranceClass.ACTION_REQUEST.value,
            UtteranceClass.CONFIRMATION.value,
        )
        else "",
    )


class ConversationGoalStore:
    """Session store for the current ActiveGoal (+ light history).

    PRIMARY goal = ``current`` (real user executable intent).
    DISCOURSE = clarification / ordinal choice refinements — must NOT replace
    the primary objective with boilerplate like \"Correct or resolve...\".
    """

    _CHOICE_BOILERPLATE = (
        "correct or resolve the previous choice",
        "correct the previous choice",
        "refine the previous choice",
        "wybierz:",
    )

    def __init__(self) -> None:
        self.current: Optional[ActiveGoal] = None
        self.history: List[ActiveGoal] = []
        self.presented_options: List[str] = []
        self.last_referent: str = ""
        # Discourse refinement for the primary goal (choice / clarify), not a sibling goal.
        self.discourse: Dict[str, Any] = {}

    def clear(self) -> None:
        if self.current is not None:
            self.history.append(self.current)
        self.current = None
        self.presented_options = []
        self.last_referent = ""
        self.discourse = {}

    def reset_session(self) -> None:
        self.current = None
        self.history.clear()
        self.presented_options = []
        self.last_referent = ""
        self.discourse = {}

    def goal_trace(self, pending_executable: Any = None, execution_context: Any = None) -> Dict[str, Any]:
        """Debug snapshot: PRIMARY_GOAL / DISCOURSE / PENDING / EXECUTION."""
        pending = pending_executable if isinstance(pending_executable, dict) else {}
        exec_ctx = {}
        if execution_context is not None and hasattr(execution_context, "to_dict"):
            try:
                exec_ctx = dict(execution_context.to_dict() or {})
            except Exception:
                exec_ctx = {}
        elif isinstance(execution_context, dict):
            exec_ctx = dict(execution_context)
        return {
            "PRIMARY_GOAL": self.current.to_dict() if self.current else None,
            "DISCOURSE_STATE": dict(self.discourse or {}),
            "PENDING_EXECUTABLE": {
                "goal": str(pending.get("goal") or ""),
                "status": str(pending.get("status") or ""),
                "last_error": str(pending.get("last_error") or ""),
            }
            if pending
            else None,
            "EXECUTION_CONTEXT": exec_ctx or None,
        }

    def log_trace(self, event: str, **extra: Any) -> None:
        try:
            from Diagnostics import log_turn

            payload = self.goal_trace(
                pending_executable=extra.pop("pending_executable", None),
                execution_context=extra.pop("execution_context", None),
            )
            payload["event"] = str(event or "")
            payload.update(extra)
            log_turn("goal_trace", **payload)
        except Exception:
            pass

    @classmethod
    def _is_choice_boilerplate(cls, text: str) -> bool:
        low = str(text or "").strip().casefold()
        if not low:
            return False
        return any(token in low for token in cls._CHOICE_BOILERPLATE)

    def set_discourse(self, *, kind: str, **fields: Any) -> None:
        self.discourse = {
            "kind": str(kind or ""),
            "updated_at": _now(),
            **{k: v for k, v in fields.items() if v is not None},
        }
        self.log_trace("discourse_set", kind=kind)

    def clear_discourse(self) -> None:
        if self.discourse:
            self.discourse = {}
            self.log_trace("discourse_clear")

    def semantic_conversation(self) -> Dict[str, Any]:
        goal = self.current
        recent = goal
        if recent is None and self.history:
            recent = self.history[-1]
        return {
            "has_active_goal": bool(goal and goal.is_open),
            # Completed/cancelled current slot still anchors follow-ups ("nie aż tak", "zgaś ją").
            "has_recent_goal": bool(goal or self.history),
            "unresolved_goal": bool(goal and goal.is_unresolved),
            "active_parameter": (goal.parameter if goal else "")
            or _parameter_from_last_action(goal),
            "last_parameter": (
                (goal.parameter if goal and goal.parameter else "")
                or (recent.parameter if recent and recent.parameter else "")
                or _parameter_from_last_action(goal or recent)
            ),
            # Last brightness/volume nudge — needed to reverse "nie aż tak".
            "parameter_direction": (
                (goal.parameter_direction if goal and goal.parameter_direction else "")
                or (
                    recent.parameter_direction
                    if recent and recent.parameter_direction
                    else ""
                )
                or _direction_from_last_action(goal or recent)
            ),
            "last_parameter_direction": (
                (goal.parameter_direction if goal and goal.parameter_direction else "")
                or (
                    recent.parameter_direction
                    if recent and recent.parameter_direction
                    else ""
                )
                or _direction_from_last_action(goal or recent)
            ),
            "recent_domain": (
                (goal.domain if goal and goal.domain else "")
                or (recent.domain if recent and recent.domain else "")
            ),
            "presented_options": list(self.presented_options or (goal.presented_options if goal else [])),
            "discussed_action": bool(
                goal and (goal.discussed_action or goal.objective)
            ) or bool(recent and (recent.discussed_action or recent.objective)),
            "goal_cancelled": bool(
                goal and str(getattr(goal, "status", "") or "").upper() == "CANCELLED"
            ),
            "active_goal": goal.to_dict() if goal else None,
            "recent_goal": recent.to_dict() if recent else None,
            "discourse": dict(self.discourse or {}),
            "discourse_kind": str((self.discourse or {}).get("kind") or ""),
        }

    def set_presented_options(self, options: Sequence[str]) -> None:
        self.presented_options = [str(x).strip() for x in options if str(x).strip()][:8]
        if self.current is not None:
            self.current.presented_options = list(self.presented_options)
            self.current.updated_at = _now()
        self.set_discourse(
            kind="choice",
            options=list(self.presented_options),
            primary_objective=(self.current.objective if self.current else ""),
        )

    def observe_utterance(
        self,
        text: str,
        *,
        envelope: Any = None,
        classification: Optional[UtteranceClassification] = None,
    ) -> ActiveGoal:
        conv = self.semantic_conversation()
        cls = classification or classify_utterance(text, conversation=conv)
        uclass = cls.utterance_class
        env_intent = str(getattr(envelope, "intent", "") or "") if envelope is not None else ""
        env_goal = str(getattr(envelope, "user_goal", "") or "") if envelope is not None else ""

        # Choice / visual refinement is DISCOURSE — never spawn a new primary goal.
        is_choice_turn = uclass in (
            UtteranceClass.REFERENCE.value,
            UtteranceClass.CORRECTION.value,
        ) or env_intent in (
            "resolve_pending_visual_choice",
            "pick_presented_option",
            "correction",
        ) or self._is_choice_boilerplate(env_goal)

        # Follow-ups mutate the open goal instead of spawning a sibling.
        # Parameter/device follow-ups may also revive a just-COMPLETED goal
        # ("jeszcze jaśniej" / "nie aż tak" / "zgaś ją" after a successful act).
        followup_classes = (
            UtteranceClass.CHANGE_PARAMETER.value,
            UtteranceClass.CONTINUATION.value,
            UtteranceClass.CONFIRMATION.value,
            UtteranceClass.RETRY.value,
            UtteranceClass.WHY_FAILURE.value,
            UtteranceClass.REFERENCE.value,
            UtteranceClass.CORRECTION.value,
        )
        can_revive = bool(
            self.current
            and self.current.status == GoalStatus.COMPLETED.value
            and uclass in (
                UtteranceClass.CHANGE_PARAMETER.value,
                UtteranceClass.CONTINUATION.value,
                UtteranceClass.CORRECTION.value,
                UtteranceClass.ACTION_REQUEST.value,
                UtteranceClass.REFERENCE.value,
                UtteranceClass.CONFIRMATION.value,
                UtteranceClass.RETRY.value,
            )
            and (
                self.current.parameter
                or self.current.domain
                or self.current.target
                or self.presented_options
                or self.current.presented_options
            )
        )
        if is_choice_turn and self.current is not None:
            goal = self.current
            if can_revive and goal.status == GoalStatus.COMPLETED.value:
                goal.status = GoalStatus.ACTIVE.value
            goal.updated_at = _now()
            goal.utterance_class = uclass
            # Keep primary objective; only refresh discourse.
            ordinal = getattr(cls, "ordinal", None)
            picked = ""
            if ordinal:
                picked = self.resolve_ordinal(ordinal) or ""
            self.set_discourse(
                kind="choice",
                utterance=str(text or "")[:160],
                ordinal=ordinal,
                picked=picked,
                options=list(self.presented_options or goal.presented_options or []),
                primary_objective=goal.objective,
                envelope_intent=env_intent,
            )
            if envelope is not None and not self._is_choice_boilerplate(goal.objective):
                try:
                    # Keep envelope goal aligned with PRIMARY, not choice boilerplate.
                    envelope.user_goal = goal.objective or envelope.user_goal
                    envelope.goal_objective = goal.objective or getattr(
                        envelope, "goal_objective", ""
                    )
                except Exception:
                    pass
            self.log_trace("choice_refines_primary")
            return goal

        if self.current and (self.current.is_open or can_revive) and uclass in followup_classes:
            goal = self.current
            goal.updated_at = _now()
            goal.utterance_class = uclass
            goal.confidence = max(goal.confidence, cls.confidence)
            if can_revive and goal.status == GoalStatus.COMPLETED.value:
                goal.status = GoalStatus.ACTIVE.value
            if cls.parameter_hint:
                param, direction = _parameter_from_hint(
                    cls.parameter_hint, goal.domain or _domain_from_envelope(envelope)
                )
                if param:
                    goal.parameter = param
                if direction:
                    goal.parameter_direction = direction
            if uclass == UtteranceClass.CONFIRMATION.value and goal.status == GoalStatus.DISCUSSED.value:
                goal.status = GoalStatus.ACTIVE.value
            if uclass == UtteranceClass.RETRY.value and goal.is_unresolved:
                goal.status = GoalStatus.ACTIVE.value
                goal.unresolved_reason = goal.unresolved_reason  # keep prior reason
            if uclass == UtteranceClass.CORRECTION.value:
                try:
                    from core.utterance import extract_correction_target

                    retarget = extract_correction_target(text)
                except Exception:
                    retarget = ""
                if retarget and not self._is_choice_boilerplate(retarget):
                    # Explicit "chodziło mi o X" rewrites the open goal, not a sibling.
                    goal.objective = retarget[:240]
                    goal.discussed_action = retarget[:160]
                    goal.target = retarget[:120]
                    goal.status = GoalStatus.ACTIVE.value
                    goal.unresolved_reason = ""
                    if envelope is not None:
                        try:
                            envelope.user_goal = retarget[:240]
                            envelope.goal_objective = retarget[:240]
                        except Exception:
                            pass
            if envelope is not None:
                intent_sig = str(getattr(envelope, "intent", "") or goal.intent_family_signal)
                if intent_sig not in (
                    "resolve_pending_visual_choice",
                    "pick_presented_option",
                ):
                    goal.intent_family_signal = intent_sig
            self.log_trace("followup_mutates_primary", utterance_class=uclass)
            return goal

        # New goal-bearing utterances replace completed/cancelled or empty slots.
        # Never adopt choice boilerplate as a new primary goal.
        if self._is_choice_boilerplate(env_goal) or is_choice_turn:
            if self.current is not None:
                self.set_discourse(
                    kind="choice",
                    utterance=str(text or "")[:160],
                    primary_objective=self.current.objective,
                    envelope_intent=env_intent,
                )
                self.log_trace("choice_without_primary_replace")
                return self.current
            # No primary yet — ignore boilerplate; fall through to build from text.
            if envelope is not None:
                try:
                    envelope.user_goal = str(text or "")[:240]
                except Exception:
                    pass

        if uclass in (
            UtteranceClass.GOAL.value,
            UtteranceClass.ACTION_REQUEST.value,
            UtteranceClass.CHANGE_PARAMETER.value,
        ) or (
            envelope is not None
            and bool(getattr(envelope, "needs_tools", False))
            and str(getattr(envelope, "mode", "")) in ("ACT", "PLAN", "ASK")
            and env_intent
            not in (
                "resolve_pending_visual_choice",
                "pick_presented_option",
                "correction",
            )
        ):
            if self.current and self.current.is_open and not self.current.is_unresolved:
                # Soft replace when a fresh complete action arrives.
                if uclass in (UtteranceClass.ACTION_REQUEST.value, UtteranceClass.GOAL.value):
                    self.history.append(self.current)
                    self.clear_discourse()
                    self.current = build_goal_from_semantics(
                        text, envelope=envelope, classification=cls, conversation=conv
                    )
                    self.log_trace("primary_replaced", utterance_class=uclass)
                    return self.current
            self.clear_discourse()
            self.current = build_goal_from_semantics(
                text, envelope=envelope, classification=cls, conversation=conv
            )
            self.log_trace("primary_set", utterance_class=uclass)
            return self.current

        if self.current is None and uclass not in (
            UtteranceClass.CASUAL_TALK.value,
            UtteranceClass.UNKNOWN.value,
        ):
            self.current = build_goal_from_semantics(
                text, envelope=envelope, classification=cls, conversation=conv
            )
            self.log_trace("primary_set_fallback", utterance_class=uclass)
        return self.current or build_goal_from_semantics(
            text, envelope=envelope, classification=cls, conversation=conv
        )

    def mark_in_progress(self, *, action: str = "", plan: Optional[List[Dict[str, Any]]] = None) -> None:
        if self.current is None:
            return
        self.current.status = GoalStatus.IN_PROGRESS.value
        self.current.updated_at = _now()
        if action:
            self.current.last_action = action[:120]
        if plan:
            self.current.previous_plan = [dict(x) for x in plan if isinstance(x, dict)]

    def mark_result(
        self,
        *,
        success: bool,
        action: str = "",
        result: Optional[Dict[str, Any]] = None,
        reason: str = "",
        plan: Optional[List[Dict[str, Any]]] = None,
        observations: Optional[Sequence[Any]] = None,
        steps: Optional[Sequence[Any]] = None,
    ) -> None:
        if self.current is None:
            return
        now = _now()
        self.current.updated_at = now
        if action:
            self.current.last_action = action[:120]
        payload = dict(result or {}) if isinstance(result, dict) else {}
        step_list = steps if steps is not None else payload.get("steps")
        evidenced = has_goal_completion_evidence(
            result=payload,
            observations=observations,
            steps=step_list,
            action=action,
        )
        claimed_success = bool(success)
        effective_success = claimed_success and evidenced
        # Mixed / failed / incomplete step or observation outcomes cannot COMPLETE.
        try:
            from core.execution_context import rows_indicate_partial_or_incomplete

            if effective_success and rows_indicate_partial_or_incomplete(
                steps=list(step_list) if isinstance(step_list, (list, tuple)) else None,
                observations=list(observations)
                if observations is not None
                else (
                    list(payload.get("observations"))
                    if isinstance(payload.get("observations"), list)
                    else None
                ),
            ):
                effective_success = False
                payload["partial_blocked_completion"] = True
                payload.setdefault("reason", reason or "partial_or_incomplete_outcome")
                if not reason:
                    reason = "partial_or_incomplete_outcome"
        except Exception:
            pass
        if reason and "reason" not in payload:
            payload["reason"] = reason
        if claimed_success and not evidenced:
            payload["false_success_blocked"] = True
            payload.setdefault("reason", reason or "no_completion_evidence")
            if not reason:
                reason = "no_completion_evidence"
        payload["success"] = effective_success
        self.current.last_result = payload
        if plan:
            self.current.previous_plan = [dict(x) for x in plan if isinstance(x, dict)]
        if effective_success:
            self.current.status = GoalStatus.COMPLETED.value
            self.current.unresolved_reason = ""
            return
        # Vacuous agent reply / no tool evidence → IN_PROGRESS (not COMPLETED).
        # Failed attempt or non-vacuous empty evidence → UNRESOLVED.
        vacuous = is_vacuous_goal_reply(str(payload.get("message") or ""))
        action_key = str(
            action or payload.get("tool") or payload.get("tool_name") or ""
        ).strip().lower()
        no_tool = action_key in _NON_TOOL_ACTIONS
        if claimed_success and not evidenced and (vacuous or no_tool):
            self.current.status = GoalStatus.IN_PROGRESS.value
            if not vacuous:
                self.current.unresolved_reason = _honest_reason(
                    payload, reason or "no_completion_evidence"
                )
            return
        self.current.status = GoalStatus.UNRESOLVED.value
        honest = _honest_reason(payload, reason)
        self.current.unresolved_reason = honest

    def mark_cancelled(self) -> None:
        if self.current is None:
            return
        self.current.status = GoalStatus.CANCELLED.value
        self.current.updated_at = _now()
        self.clear_discourse()
        self.log_trace("primary_cancelled")

    def resolve_ordinal(self, ordinal: str) -> str:
        opts = list(self.presented_options or (self.current.presented_options if self.current else []))
        if not opts:
            return ""
        key = str(ordinal or "").lower()
        if key == "first":
            return opts[0]
        if key == "second" and len(opts) > 1:
            return opts[1]
        if key == "third" and len(opts) > 2:
            return opts[2]
        if key == "last":
            return opts[-1]
        return ""


def _honest_reason(result: Dict[str, Any], fallback: str = "") -> str:
    """Prefer verified tool/result fields — never invent a cause."""
    for key in (
        "reason",
        "error",
        "message",
        "verification_message",
        "detail",
        "unresolved_reason",
    ):
        val = str(result.get(key) or "").strip()
        if val and not _looks_invented(val):
            return val[:240]
    steps = result.get("failed_steps") or result.get("steps")
    if isinstance(steps, list):
        for item in steps:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "").upper() == "FAILED":
                msg = str(
                    item.get("error")
                    or item.get("message")
                    or item.get("reason")
                    or ""
                ).strip()
                if msg:
                    return msg[:240]
    fb = str(fallback or "").strip()
    if fb:
        return fb[:240]
    return ""


def _looks_invented(text: str) -> bool:
    lowered = text.lower()
    return any(
        frag in lowered
        for frag in (
            "prawdopodobnie",
            "może dlatego",
            "pewnie",
            "wydaje mi się",
            "zgaduję",
        )
    )


def explain_failure(
    goal: Optional[ActiveGoal],
    *,
    journal_entry: Optional[Dict[str, Any]] = None,
    pending: Optional[Dict[str, Any]] = None,
) -> str:
    """Natural Polish answer for WHY_FAILURE from real evidence only."""
    evidence: List[str] = []
    if goal is not None:
        if goal.unresolved_reason:
            evidence.append(goal.unresolved_reason)
        lr = goal.last_result or {}
        for key in ("reason", "error", "message", "verification_message"):
            val = str(lr.get(key) or "").strip()
            if val and val not in evidence:
                evidence.append(val)
    if isinstance(journal_entry, dict):
        for key in ("error", "message", "reason", "verification_message"):
            val = str(journal_entry.get(key) or "").strip()
            if val and val not in evidence:
                evidence.append(val)
    if isinstance(pending, dict):
        steps = pending.get("steps")
        if isinstance(steps, list):
            try:
                from core.pending_goal import failed_step_labels, honest_failed_message

                labels = failed_step_labels(steps)
                if labels:
                    evidence.append(honest_failed_message(steps, offer_retry=False))
            except Exception:
                for item in steps:
                    if isinstance(item, dict) and str(item.get("status") or "") == "FAILED":
                        msg = str(item.get("error") or item.get("message") or "").strip()
                        if msg:
                            evidence.append(msg)
    evidence = [e for e in evidence if e and not _looks_invented(e)]
    if not evidence:
        if goal is not None and str(goal.status or "") == GoalStatus.COMPLETED.value:
            action = str(goal.last_action or "").strip()
            obj = str(goal.objective or "").strip()[:80]
            if action or obj:
                detail = obj or action
                return f"Zrobiłem to przed chwilą ({detail}). O co dokładnie Ci chodzi?"
            return "Według mnie to już jest zrobione. Doprecyzuj, jeśli czegoś brakuje."
        if goal and goal.is_unresolved:
            return (
                "Próbowałem, ale nie dokończyłem tego kroku. "
                "Nie mam pewnego powodu w logu działania."
            )
        return "Nie mam zapisanej nieudanej czynności, o którą pytasz."
    primary = evidence[0].strip()
    if primary and primary[-1] not in ".!?…":
        primary += "."
    objective = (goal.objective if goal else "")[:80]
    if objective:
        return f"Nie udało się dokończyć „{objective}”: {primary}"
    return f"Nie udało się, bo {primary[0].lower() + primary[1:]}" if primary else primary


def enrich_followup_goal_text(
    text: str,
    goal: Optional[ActiveGoal],
    *,
    classification: Optional[UtteranceClassification] = None,
) -> str:
    """Expand short follow-ups into an executable goal string for Planner/FastPath."""
    raw = str(text or "").strip()
    if goal is None:
        return raw
    cls = classification or classify_utterance(
        raw,
        conversation={
            "has_active_goal": True,
            "unresolved_goal": goal.is_unresolved,
            "active_parameter": goal.parameter,
            "presented_options": goal.presented_options,
        },
    )
    uclass = cls.utterance_class
    if uclass == UtteranceClass.CHANGE_PARAMETER.value:
        direction = cls.parameter_hint or goal.parameter_direction or "more"
        param = goal.parameter or "brightness"
        if param == "brightness" or direction in ("brighter", "darker"):
            # Pronoun without a bound light referent must stay pronoun-form
            # (never invent "światło" → singleton alias false resolve).
            try:
                from core.device_pronoun import has_device_pronoun

                if has_device_pronoun(raw) and not (goal.target or "").strip():
                    return raw
            except Exception:
                pass
            if direction in ("brighter", "more", "louder"):
                return "Rozjaśnij światło trochę bardziej."
            return "Przyciemnij światło trochę."
        if param == "volume":
            if direction in ("more", "louder", "brighter"):
                return "Zwiększ głośność."
            return "Zmniejsz głośność."
        return f"{goal.objective} — {raw}".strip(" —")
    if uclass == UtteranceClass.CONFIRMATION.value:
        return (
            goal.discussed_action
            or goal.objective
            or raw
        )
    if uclass == UtteranceClass.CONTINUATION.value:
        return goal.discussed_action or goal.objective or raw
    if uclass == UtteranceClass.RETRY.value:
        # Vague "spróbuj inaczej" must not be rewritten into the same completed
        # objective — that silently re-fires the identical soft/tool path.
        if re.search(
            r"\b(?:inaczej|innym\s+sposobem|inna\s+(?:droga|metoda|opcja))\b",
            raw,
            re.I,
        ) and not re.search(
            r"\b(?:ponownie|jeszcze\s+raz|powt[oó]rz|pon[oó]w)\b",
            raw,
            re.I,
        ):
            return raw
        return goal.objective or raw
    if uclass == UtteranceClass.REFERENCE.value and cls.ordinal:
        picked = ""
        # Caller may resolve via store; here use goal.presented_options.
        opts = goal.presented_options
        if cls.ordinal == "first" and opts:
            picked = opts[0]
        elif cls.ordinal == "second" and len(opts) > 1:
            picked = opts[1]
        elif cls.ordinal == "last" and opts:
            picked = opts[-1]
        if picked:
            return f"Wybierz: {picked}"
    if uclass == UtteranceClass.INFORMATION_REQUEST.value and cls.signals:
        if "more_about_referent" in cls.signals:
            ref = goal.target or goal.last_action or goal.objective or "tym"
            return f"Wyszukaj coś więcej o: {ref}"
    if "unresolved_awareness" in cls.signals:
        return goal.objective or raw
    return raw


def journal_latest_failure() -> Optional[Dict[str, Any]]:
    """Best-effort last failed ActionJournal entry."""
    try:
        from core.action_journal import get_journal

        journal = get_journal()
        recent = list(getattr(journal, "entries", None) or [])[-12:]
        for entry in reversed(recent):
            data = entry.to_dict() if hasattr(entry, "to_dict") else dict(entry or {})
            ok = data.get("success")
            if ok is False or str(data.get("status") or "").upper() in (
                "FAILED",
                "ERROR",
            ):
                return data
    except Exception:
        return None
    return None
