# ==========================
# ELIAN — UserGoal (goal-first intelligence)
# ==========================
"""Structured user goal between Semantic Executive and Planner/FastPath.

Not a second IntentRouter. Normalizes DecisionEnvelope + Cognitive + WorldState
into objective/target/constraints/expected_result for capability-aware planning.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class GoalRequirement(str, Enum):
    ACTION = "action"
    ANSWER = "answer"
    CLARIFICATION = "clarification"
    NONE = "none"


class FailureMode(str, Enum):
    NONE = ""
    NO_CAPABILITY = "no_capability"
    DISABLED = "disabled"
    OFFLINE = "offline"
    TOOL_ERROR = "tool_error"
    UNCLEAR_GOAL = "unclear_goal"
    MISSING_INFO = "missing_info"


@dataclass
class UserGoal:
    objective: str = ""
    target: str = ""
    constraints: List[str] = field(default_factory=list)
    references: List[str] = field(default_factory=list)
    expected_result: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    requires_action: bool = False
    requires_answer: bool = False
    requires_clarification: bool = False
    soft_kind: str = ""
    intent_family: str = ""
    failure_mode: str = ""
    source: str = "formulated"
    raw_text: str = ""
    enriched_text: str = ""
    slots: Dict[str, Any] = field(default_factory=dict)
    continuation: bool = False
    mode: str = ""

    @property
    def primary_requirement(self) -> str:
        if self.requires_clarification:
            return GoalRequirement.CLARIFICATION.value
        if self.requires_action:
            return GoalRequirement.ACTION.value
        if self.requires_answer:
            return GoalRequirement.ANSWER.value
        return GoalRequirement.NONE.value

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["confidence"] = round(float(self.confidence or 0.0), 3)
        data["primary_requirement"] = self.primary_requirement
        return data

    def planner_goal_text(self) -> str:
        text = str(self.enriched_text or self.objective or self.raw_text or "").strip()
        return text


def _envelope_mode(envelope: Any) -> str:
    return str(getattr(envelope, "mode", "") or "").upper()


def _envelope_confidence(envelope: Any) -> float:
    try:
        return float(getattr(envelope, "confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def formulate_user_goal(
    text: str,
    *,
    envelope: Any = None,
    assessment: Any = None,
    guidance: Any = None,
    classification: Any = None,
    goal_context: Any = None,
    soft: Optional[Dict[str, Any]] = None,
) -> UserGoal:
    """Build UserGoal from turn signals. Soft desires fill expected_result/slots."""
    raw = str(text or "").strip()
    mode = _envelope_mode(envelope) if envelope is not None else ""
    intent = str(getattr(envelope, "intent", "") or "") if envelope is not None else ""
    conf = _envelope_confidence(envelope) if envelope is not None else 0.0

    enriched = raw
    if guidance is not None and getattr(guidance, "enriched_goal", ""):
        enriched = str(guidance.enriched_goal).strip() or raw

    objective = enriched
    if assessment is not None and getattr(assessment, "meaning", ""):
        meaning = str(assessment.meaning or "").strip()
        if meaning:
            objective = meaning

    family = ""
    slots: Dict[str, Any] = {}
    if classification is not None:
        family = str(getattr(getattr(classification, "family", None), "value", "") or "")
        slots = dict(getattr(classification, "slots", None) or {})

    soft = dict(soft or {})
    soft_kind = str(soft.get("kind") or "")
    if soft_kind:
        family = family or str(soft.get("intent_family") or "")
        slots.update(dict(soft.get("slots") or {}))
        if soft.get("objective"):
            objective = str(soft["objective"])
        conf = max(conf, float(soft.get("confidence") or 0.82))

    references: List[str] = []
    if envelope is not None:
        for item in list(getattr(envelope, "referents", None) or []):
            token = str(item or "").strip()
            if token and token not in references:
                references.append(token)
    target = str(slots.get("target") or slots.get("entity_id") or soft.get("target") or "")
    if goal_context is not None:
        active = getattr(goal_context, "active_entities", None) or {}
        if isinstance(active, dict):
            if not target:
                target = str(
                    active.get("device_id")
                    or active.get("entity_id")
                    or active.get("app")
                    or active.get("service")
                    or ""
                )
            for key in ("device_name", "app", "service", "media_query", "topic"):
                val = str(active.get(key) or "").strip()
                if val and val not in references:
                    references.append(val)
        for item in list(getattr(goal_context, "reference_tokens", None) or []):
            token = str(item or "").strip()
            if token and token not in references:
                references.append(token)

    constraints: List[str] = []
    if envelope is not None:
        for note in list(getattr(envelope, "safety_notes", None) or []):
            note_s = str(note or "").strip()
            if note_s:
                constraints.append(note_s)
        if getattr(envelope, "hypothetical", False):
            constraints.append("hypothetical")
        if getattr(envelope, "quoted", False):
            constraints.append("quoted")
        if getattr(envelope, "negated", False):
            constraints.append("negated")
    if soft.get("mood"):
        constraints.append(f"mood:{soft['mood']}")

    expected = dict(soft.get("expected_result") or {})
    if not expected and soft_kind:
        expected = {"kind": soft_kind}
    if not expected and slots.get("implicit_light"):
        desired = str(slots.get("desired") or "brighter")
        expected = {"kind": "brightness_up" if desired != "darker" else "brightness_down"}
    if not expected and family == "SYSTEM" and slots.get("volume_direction"):
        expected = {"kind": f"volume_{slots['volume_direction']}"}

    needs_clarify = bool(getattr(envelope, "needs_clarification", False)) if envelope else False
    if soft.get("needs_clarification"):
        needs_clarify = True
    missing = list(getattr(envelope, "missing_information", None) or []) if envelope else []
    if soft.get("missing"):
        missing = list(soft.get("missing") or missing)

    needs_tools = bool(getattr(envelope, "needs_tools", False)) if envelope else False
    if soft_kind and soft_kind not in ("unclear", "talk", "summarize_web", "ask_goal"):
        needs_tools = True

    requires_action = False
    requires_answer = False
    requires_clarification = False

    if needs_clarify or missing:
        requires_clarification = True
    elif soft_kind == "summarize_web":
        requires_answer = True
    elif mode in ("ACT", "PLAN", "OBSERVE") or needs_tools or soft_kind in (
        "volume_down",
        "volume_up",
        "brightness_up",
        "brightness_down",
        "light_on",
        "light_off",
        "media_calm",
        "media_play",
        "media_now",
        "web_search",
        "open_result",
        "replay_last",
        "retry_pending",
        "resume_previous",
        "gaming_setup",
        "explain_failure",
        "cancel_goal",
        "ask_goal",
        "unclear",
    ):
        requires_action = True
    elif mode in ("TALK", "ANSWER"):
        requires_answer = True
    elif mode == "ASK":
        requires_clarification = True
    elif assessment is not None and getattr(assessment, "should_act", False):
        requires_action = True
    elif assessment is not None and getattr(assessment, "should_ask", False):
        requires_clarification = True
    elif assessment is not None and getattr(assessment, "should_reply", False):
        requires_answer = True

    if soft_kind in ("unclear", "ask_goal"):
        requires_action = False
        requires_answer = False
        requires_clarification = True

    failure_mode = ""
    if requires_clarification and (missing or soft_kind in ("unclear", "ask_goal")):
        failure_mode = FailureMode.MISSING_INFO.value if missing else FailureMode.UNCLEAR_GOAL.value

    continuation = bool(getattr(envelope, "continuation", False)) if envelope else False
    if soft.get("continuation") or (goal_context and getattr(goal_context, "is_continuation", False)):
        continuation = True

    if assessment is not None:
        try:
            conf = max(conf, float(getattr(assessment, "confidence", 0.0) or 0.0))
        except (TypeError, ValueError):
            pass

    return UserGoal(
        objective=objective or raw,
        target=target,
        constraints=constraints,
        references=references,
        expected_result=expected,
        confidence=conf,
        requires_action=requires_action,
        requires_answer=requires_answer,
        requires_clarification=requires_clarification,
        soft_kind=soft_kind,
        intent_family=family,
        failure_mode=failure_mode,
        source=str(soft.get("source") or "formulated"),
        raw_text=raw,
        enriched_text=enriched,
        slots=slots,
        continuation=continuation,
        mode=mode or intent,
    )


def honest_failure_reply(mode: str, *, detail: str = "") -> str:
    """Short Polish failure speech — never claim success."""
    key = str(mode or "").strip().lower()
    extra = str(detail or "").strip()
    if key == FailureMode.NO_CAPABILITY.value:
        return extra or "Nie mam do tego możliwości."
    if key == FailureMode.DISABLED.value:
        return extra or "Ta funkcja jest wyłączona."
    if key == FailureMode.OFFLINE.value:
        return extra or "Nie mam teraz połączenia potrzebnego do tego."
    if key == FailureMode.TOOL_ERROR.value:
        return extra or "Narzędzie się wyłożyło. Spróbuję inaczej, jeśli dasz znać."
    if key == FailureMode.UNCLEAR_GOAL.value:
        return extra or "Nie jestem pewien, o co chodzi. Doprecyzuj proszę."
    if key == FailureMode.MISSING_INFO.value:
        return extra or "Brakuje mi informacji, żeby to zrobić."
    return extra or "Nie udało mi się tego domknąć."
