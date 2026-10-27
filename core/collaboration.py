# ==========================
# ELIAN — proactive collaboration (C.5.4 Part X)
# ==========================
"""Observable task/decision state for the existing Planner — not a second planner.

Elian may start a conversation only with user value. Task context and
conversation topic are different salience tracks.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.events import (
    ACTIVE_WINDOW_CHANGED,
    SCREEN_FRAME_UPDATED,
    SCREEN_MAJOR_CHANGE,
    VISION_COMPLETED,
    VISION_FAILED,
    VISION_STARTED,
    VISUAL_REFERENT_UPDATED,
    Priority,
)
from core.speech_guard import looks_like_internal_identifier


# ----- schemas (X3, X6) -----

@dataclass
class ActiveTaskContext:
    task_id: str = ""
    goal: str = ""
    semantic_goal: str = ""
    plan_id: str = ""
    status: str = "idle"
    current_step: str = ""
    completed_steps: List[str] = field(default_factory=list)
    failed_steps: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)
    pending_decision: str = ""
    possible_next_actions: List[str] = field(default_factory=list)
    expected_completion_condition: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    started_at: float = 0.0
    resumable: bool = True
    awaiting_user_decision: bool = False
    notify_on_complete: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionOption:
    id: str
    label: str
    consequences: str = ""
    action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OptionContext:
    """Pending user decision after Elian presented options (X6 DecisionContext)."""

    decision_id: str = ""
    question: str = ""
    options: List[DecisionOption] = field(default_factory=list)
    recommended_option: str = ""
    recommended_why: str = ""
    expires_at: float = 0.0
    problem: str = ""
    evidence: str = ""
    recommendation_strength: str = "moderate"  # strong | moderate | weak

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "question": self.question,
            "options": [opt.to_dict() for opt in self.options],
            "recommended_option": self.recommended_option,
            "recommended_why": self.recommended_why,
            "expires_at": self.expires_at,
            "problem": self.problem,
            "evidence": self.evidence,
            "recommendation_strength": self.recommendation_strength,
        }

    def option_by_id(self, oid: str) -> Optional[DecisionOption]:
        for opt in self.options:
            if opt.id == oid:
                return opt
        return None

    def expired(self, now: Optional[float] = None) -> bool:
        if not self.expires_at:
            return False
        return float(now if now is not None else time.time()) > float(self.expires_at)


@dataclass
class ProblemBriefing:
    problem: str
    cause_or_evidence: str
    possible_options: List[DecisionOption]
    recommended_option: str
    why: str
    whether_user_decision_required: bool


@dataclass
class ContextSalience:
    conversation_topic: str = ""
    active_task: str = ""
    execution_referent: str = ""
    pending_decision: str = ""
    visual_referent: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CollabReply:
    message: str
    source: str = "collab"
    silent: bool = False
    execute_option_id: str = ""
    resolved_kind: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


# ----- policy constants -----

LEGITIMATE_INITIATE_REASONS = frozenset(
    {
        "task_completed_requested",
        "task_blocked",
        "analysis_complete",
        "procedure_partial_fail",
        "user_decision_needed",
        "monitored_meaningful_change",
        "better_option_for_task",
        "reminder",
        "user_asked_notify",
        "critical_failure",
        "incoming_message",
    }
)

SILENT_LOW_VALUE_EVENTS = frozenset(
    {
        ACTIVE_WINDOW_CHANGED,
        SCREEN_FRAME_UPDATED,
        SCREEN_MAJOR_CHANGE,
        VISUAL_REFERENT_UPDATED,
        VISION_STARTED,
        VISION_COMPLETED,
        VISION_FAILED,
        "internal",
        "INTERNAL_EVENT",
        "PROCEDURE_COMPLETE",
    }
)

BANNED_TTS_FRAGMENTS = (
    "PROCEDURE_COMPLETE",
    "ACTIVE_WINDOW_CHANGED",
    "VISION_COMPLETED",
    "SCREEN_MAJOR_CHANGE",
    "plan_id=",
    "step_id=",
    "status=RUNNING",
    "status=FAILED",
    "TASK_BLOCKED",
    "INTERNAL_EVENT",
)

_TASK_STATUS = re.compile(
    r"(?:"
    r"^jak\s+idzie\??|"
    r"^co\s+si[eę]\s+dzieje\??|"
    r"^na\s+czym\s+stan[eę]li[sś]my\??|"
    r"^jaki\s+jest\s+problem\??|"
    r"^jak\s+tam\s+z\s+(?:tym|tamtym|tymi)\b|"
    r"^a\s+jak\s+tam\s+z\s+(?:tym|tamtym)\b|"
    r"^co\s+z\s+(?:tym|tamtym)\s+problemem\??|"
    r"^dobra,?\s+a\s+jak\s+tam"
    r")",
    re.I,
)
_RETURN_TO_TASK = re.compile(
    r"(?:"
    r"jak\s+tam\s+z\s+(?:tym|tamtym)|"
    r"a\s+jak\s+tam\s+z\s+(?:tym\s+)?problemem|"
    r"wr[oó][cć]my\s+do\s+(?:tego|zadania|tematu)|"
    r"a\s+co\s+z\s+tym\s+zadaniem"
    r")",
    re.I,
)
_MORE_JOKE = re.compile(
    r"^(?:jeszcze\s+jeden|jeszcze\s+jaki[sś]|kolejny\s+[zż]art|"
    r"jeszcze\s+suchar|opowiedz\s+jeszcze)[.!?…]*$",
    re.I,
)
_DEFER = re.compile(
    r"^(?:nie\s+teraz|powiedz\s+p[oó][zź]niej|potem|"
    r"nie\s+teraz,?\s+dzi[eę]ki)[.!?…]*$",
    re.I,
)
_APPROVE = re.compile(
    r"^(?:tak|dobra|zrób\s+to|zrob\s+to|okej|ok|jasne|dawaj|"
    r"zgoda|niech\s+b[eę]dzie|kontynuuj)[.!?…]*$",
    re.I,
)
_FIRST = re.compile(
    r"(?:^|\b)(?:ten\s+)?(?:pierwsz[ay]|opcj[ae]\s+pierwsz|"
    r"numer\s+jeden|jedynk[ae]|1\b)(?:\b|$)|"
    r"jednak\s+pierwsz",
    re.I,
)
_SECOND = re.compile(
    r"(?:^|\b)(?:ten\s+)?(?:drug[ai]|opcj[ae]\s+drug|"
    r"numer\s+dwa|2\b)(?:\b|$)|"
    r"zr[oó]b\s+drug",
    re.I,
)
_WHY = re.compile(
    r"^(?:a\s+)?(?:czemu|dlaczego|a\s+dlaczego|czemu\s+tak)[?.!…]*$",
    re.I,
)
_HOW = re.compile(
    r"^(?:jak\??|a\s+jak\??|jak\s+to\??)[.!?…]*$",
    re.I,
)
_ALT = re.compile(
    r"(?:"
    r"a\s+inaczej|"
    r"da\s+si[eę]\s+(?:co[sś]\s+)?inaczej|"
    r"a\s+nie\s+da\s+si[eę]\s+inaczej|"
    r"da\s+si[eę]\s+(?:co[sś]\s+)?(?:z\s+tym\s+)?zrobi[cć]|"
    r"innej\s+opcji|"
    r"inna\s+opcja|"
    r"co\s+innego"
    r")",
    re.I,
)
_RECOMMEND_Q = re.compile(
    r"(?:"
    r"co\s+proponujesz|"
    r"co\s+by[sś]\s+zrobi[lł]|"
    r"a\s+kt[oó]ra\s+jest\s+lepsza|"
    r"kt[oó]r[aą]\s+wybierasz|"
    r"co\s+polecasz"
    r")",
    re.I,
)
_REJECT = re.compile(
    r"^(?:nie|zostaw|niewa[zż]ne|odpu[sś][cć]|nie\s+r[oó]b)[.!?…]*$",
    re.I,
)
_WAIT_NOW = re.compile(
    r"^(?:czekaj|poczekaj|moment|nie\s+teraz)[.!?…]*$",
    re.I,
)
_LATER = re.compile(r"powiedz\s+p[oó][zź]niej", re.I)
_NO_TASK_STATUS = re.compile(
    r"^(?:jak\s+idzie|co\s+si[eę]\s+dzieje|na\s+czym\s+stan[eę]li[sś]my)[?.!…]*$",
    re.I,
)


def _now() -> float:
    return time.time()


def looks_internal_tts(text: str) -> bool:
    raw = str(text or "")
    if looks_like_internal_identifier(raw):
        return True
    upper = raw.upper()
    return any(frag in raw or frag in upper for frag in BANNED_TTS_FRAGMENTS)


def collaboration_overrides_ack(store: Optional["CollaborationStore"], text: str) -> bool:
    if store is None:
        return False
    raw = str(text or "").strip()
    if not raw:
        return False
    if store.decision and not store.decision.expired():
        if (
            _APPROVE.match(raw)
            or _FIRST.search(raw)
            or _SECOND.search(raw)
            or _WHY.match(raw)
            or _HOW.match(raw)
            or _ALT.search(raw)
            or _RECOMMEND_Q.search(raw)
            or _REJECT.match(raw)
        ):
            return True
    topic = store.salience.conversation_topic
    if topic in ("joke", "humor", "creative_humor") and _MORE_JOKE.match(raw):
        return True
    if _DEFER.match(raw) or _LATER.search(raw):
        return True
    if store.task and store.task.status not in ("idle", "") and _TASK_STATUS.search(raw):
        return True
    return False


def classify_followup_kind(text: str, conv: Optional[Dict[str, Any]] = None) -> str:
    raw = str(text or "").strip()
    ctx = dict(conv or {})
    topic = str(ctx.get("conversation_topic") or "")
    has_task = bool(ctx.get("has_active_task") or ctx.get("task_id"))
    pending = bool(ctx.get("pending_decision"))
    if _DEFER.match(raw) or _LATER.search(raw):
        return "defer_proactive"
    if _WAIT_NOW.match(raw) and raw.lower().startswith("czek"):
        return "wait"
    if topic in ("joke", "humor", "creative_humor") and _MORE_JOKE.match(raw):
        return "continue_conversation"
    if pending and _WHY.match(raw):
        return "ask_why"
    if pending and _HOW.match(raw):
        return "ask_how"
    if pending and _ALT.search(raw):
        return "ask_alternative"
    if has_task and _ALT.search(raw):
        return "ask_alternative"
    if pending and _RECOMMEND_Q.search(raw):
        return "ask_recommendation"
    if pending and (_FIRST.search(raw) or _SECOND.search(raw)):
        return "pick_option"
    if pending and _APPROVE.match(raw):
        return "approve_recommended"
    if pending and _REJECT.match(raw):
        return "reject_suggestion"
    if has_task and _RETURN_TO_TASK.search(raw):
        return "return_to_task"
    if has_task and _TASK_STATUS.search(raw):
        return "task_status"
    if (not has_task) and _NO_TASK_STATUS.match(raw):
        return "no_task_status"
    if _MORE_JOKE.match(raw):
        return "ambiguous_continue"
    return ""


def apply_collaboration_semantics(text: str, conv: Optional[Dict[str, Any]] = None):
    """Return a DecisionEnvelope for collaboration follow-ups, or None."""
    from core.semantic_executive import DecisionEnvelope, DecisionMode

    raw = str(text or "").strip()
    ctx = dict(conv or {})
    kind = classify_followup_kind(raw, ctx)
    if not kind:
        return None

    topic = str(ctx.get("conversation_topic") or "")
    task_id = str(ctx.get("task_id") or "")
    decision_id = ""
    pending = ctx.get("pending_decision")
    if isinstance(pending, dict):
        decision_id = str(pending.get("decision_id") or "")
    elif pending:
        decision_id = str(ctx.get("decision_id") or "pending")

    base = dict(
        confidence=0.94,
        needs_tools=False,
        continuation=True,
        user_is_responding_to_elian=True,
        followup_kind=kind,
        conversation_topic=topic,
        task_reference=task_id,
        decision_reference=decision_id,
        allowed_domains=[],
    )

    if kind == "continue_conversation":
        return DecisionEnvelope(
            mode=DecisionMode.TALK.value,
            intent="creative_humor",
            user_goal="Continue the current conversational topic",
            conversation_topic="joke",
            followup_kind=kind,
            user_is_responding_to_elian=True,
            confidence=0.96,
            needs_tools=False,
            response_style="conversational",
        )
    if kind == "defer_proactive":
        return DecisionEnvelope(
            mode=DecisionMode.WAIT.value,
            intent="defer_proactive",
            user_goal="Defer non-urgent proactive dialogue",
            **base,
            response_style="concise",
        )
    if kind == "wait":
        return DecisionEnvelope(
            mode=DecisionMode.WAIT.value,
            intent="wait",
            user_goal="Pause",
            confidence=0.97,
            needs_tools=False,
            followup_kind="wait",
            user_is_responding_to_elian=True,
        )
    if kind in ("ask_why", "ask_how", "ask_alternative", "ask_recommendation"):
        return DecisionEnvelope(
            mode=DecisionMode.ANSWER.value,
            intent="decision_followup",
            user_goal="Discuss the pending recommendation",
            context_requested=True,
            **base,
            response_style="explanatory",
        )
    if kind in ("pick_option", "approve_recommended", "reject_suggestion"):
        return DecisionEnvelope(
            mode=DecisionMode.ANSWER.value,
            intent="decision_followup",
            user_goal="Resolve the pending decision",
            context_requested=True,
            recommended_next_action=kind,
            **base,
        )
    if kind in ("task_status", "return_to_task"):
        payload = dict(base)
        payload["followup_kind"] = kind
        payload["conversation_topic"] = "task"
        return DecisionEnvelope(
            mode=DecisionMode.ANSWER.value,
            intent="task_status" if kind == "task_status" else "return_to_task",
            user_goal="Report verified task state",
            context_requested=True,
            response_style="concise",
            **payload,
        )
    if kind == "no_task_status":
        return DecisionEnvelope(
            mode=DecisionMode.ASK.value,
            intent="no_task_status",
            user_goal="Clarify that there is no active task",
            confidence=0.9,
            needs_tools=False,
            needs_clarification=True,
            followup_kind=kind,
            draft_reply="Nie mam teraz otwartego zadania. O czym chcesz usłyszeć?",
            response_style="concise",
        )
    if kind == "ambiguous_continue":
        return DecisionEnvelope(
            mode=DecisionMode.ASK.value,
            intent="ambiguous_continue",
            user_goal="Clarify what to continue",
            confidence=0.8,
            needs_tools=False,
            needs_clarification=True,
            followup_kind=kind,
            draft_reply="Jeszcze jeden żart, czy wracamy do zadania?",
        )
    return None


def should_initiate(
    *,
    reason: str = "",
    event_type: str = "",
    user_value: bool = False,
    notify_requested: bool = False,
    priority: str = "normal",
    conversation_active: bool = False,
    suppress_non_urgent: bool = False,
    has_background_work: bool = False,
) -> Tuple[bool, str]:
    """Proactivity requires USER VALUE. Never initiate to sound clever."""
    et = str(event_type or "")
    if et in ("INTERNAL_EVENT", "PROCEDURE_COMPLETE"):
        return False, "internal_event"
    if et in SILENT_LOW_VALUE_EVENTS and not user_value:
        return False, "silent_low_value"
    if not user_value and not notify_requested:
        if reason not in LEGITIMATE_INITIATE_REASONS:
            return False, "no_user_value"
    if reason == "task_completed_requested" and not notify_requested and not has_background_work:
        return False, "no_fake_background"
    if reason in LEGITIMATE_INITIATE_REASONS or notify_requested or user_value:
        pr = str(priority or "normal").lower()
        if suppress_non_urgent and pr in ("low", "normal"):
            return False, "suppressed_non_urgent"
        if conversation_active and pr == "low":
            return False, "low_no_interrupt"
        return True, reason or "user_value"
    return False, "no_user_value"


def natural_task_status(task: Optional[ActiveTaskContext]) -> str:
    if task is None or not task.goal:
        return "Nie mam teraz otwartego zadania. O czym chcesz usłyszeć?"
    done = len(task.completed_steps)
    current = (task.current_step or "").strip()
    goal = task.goal.strip()
    if task.status == "blocked" or task.blockers:
        blocker = (task.blockers[0] if task.blockers else "coś zablokowało dalszy krok")
        return (
            f"Stoimy. Przy „{goal}” pojawił się problem: {blocker}."
            if goal
            else f"Stoimy. Problem: {blocker}."
        )
    if task.status == "completed":
        obs = task.observations[-1] if task.observations else ""
        if obs:
            return f"Skończyłem. {obs}"
        return f"Skończyłem sprawdzać: {goal}." if goal else "Skończyłem."
    if task.status == "failed":
        ev = task.observations[-1] if task.observations else ""
        if ev:
            return f"Nie udało się dokończyć. {ev}"
        return "Nie udało się dokończyć tego kroku."
    parts = []
    if done:
        if done == 1:
            parts.append("Pierwszy krok przeszedł")
        else:
            parts.append(f"Pierwsze {done} kroki przeszły")
    if current:
        parts.append(f"teraz {current}")
    if not parts:
        return f"Pracuję nad: {goal}." if goal else "Jestem w trakcie."
    return ". ".join(parts) + "."


def recommendation_speech(
    option_label: str,
    why: str = "",
    strength: str = "moderate",
) -> str:
    label = option_label.strip() or "tę opcję"
    extra = (" " + why.strip()) if why.strip() else ""
    if strength == "strong":
        return f"Najrozsądniej będzie: {label}.{extra}".strip()
    if strength == "weak":
        return (
            f"Na ten moment skłaniam się ku: {label}, ale dowody są słabe.{extra}"
        ).strip()
    return f"Wybrałbym: {label}.{extra}".strip()


def problem_speech(briefing: ProblemBriefing) -> str:
    opts = briefing.possible_options
    if not opts:
        ev = briefing.cause_or_evidence.strip()
        if ev:
            return f"{briefing.problem} {ev}".strip()
        return briefing.problem
    if len(opts) == 1:
        body = f"{briefing.problem} Mogę {opts[0].label}."
    else:
        labels = " albo ".join(opt.label for opt in opts[:3])
        body = f"{briefing.problem} Widzę możliwości: {labels}."
    rec = briefing.recommended_option
    rec_opt = next((o for o in opts if o.id == rec or o.label == rec), None)
    if rec_opt:
        why = briefing.why.strip()
        body += " " + recommendation_speech(rec_opt.label, why, "moderate")
    return body.strip()


def completion_speech(task: Optional[ActiveTaskContext], *, requested: bool) -> str:
    if not requested:
        return ""
    if task is None:
        return "Skończyłem to, o co prosiłeś."
    obs = [o for o in task.observations if o and not looks_internal_tts(o)]
    if obs:
        return f"Skończyłem. {obs[-1]}"
    if task.goal:
        return f"Skończyłem sprawdzać: {task.goal}."
    return "Skończyłem. Mogę powiedzieć, co z tego wynika."


def compose_proactive_speech(
    *,
    reason: str,
    task: Optional[ActiveTaskContext] = None,
    briefing: Optional[ProblemBriefing] = None,
    extra: str = "",
) -> str:
    extra = str(extra or "").strip()
    if extra and looks_internal_tts(extra):
        extra = ""
    if reason == "critical_failure":
        if briefing:
            return problem_speech(briefing)
        if extra:
            return extra
        return "Wykryłem poważny problem w aktualnym zadaniu. Potrzebuję Twojej decyzji, żeby iść dalej."
    if reason in ("task_blocked", "user_decision_needed"):
        if briefing:
            return problem_speech(briefing)
        status = natural_task_status(task)
        return status
    if reason in ("task_completed_requested", "analysis_complete", "user_asked_notify"):
        spoken = completion_speech(task, requested=True)
        return extra or spoken
    if reason == "procedure_partial_fail":
        if briefing:
            return problem_speech(briefing)
        return extra or "Procedura przeszła tylko częściowo. Mogę spróbować inną drogą."
    if reason == "better_option_for_task":
        return extra or "Znalazłem lepszą drogę dla tego zadania. Chcesz, żebym ją omówił?"
    if reason == "reminder":
        return extra or "Przypomnienie."
    if extra:
        return extra
    return ""


class CollaborationStore:
    """Per-session salience: conversation vs task vs pending decision."""

    def __init__(self) -> None:
        self.task: Optional[ActiveTaskContext] = None
        self.decision: Optional[OptionContext] = None
        self.salience = ContextSalience()
        self.proactive_speaking = False
        self.suppress_non_urgent = False
        self.defer_pending: List[str] = []
        self.last_recommendation: str = ""
        self.last_proactive_reason: str = ""
        self.metrics: Dict[str, int] = {
            "irrelevant_proactive_speech": 0,
            "internal_event_proactive": 0,
            "wrong_pending_decision_resolution": 0,
            "task_context_hijack": 0,
            "conversation_context_hijack": 0,
            "pending_decision_resolutions": 0,
            "useful_followup_offer": 0,
        }

    def reset_session(self) -> None:
        self.task = None
        self.decision = None
        self.salience = ContextSalience()
        self.proactive_speaking = False
        self.suppress_non_urgent = False
        self.defer_pending = []
        self.last_recommendation = ""
        self.last_proactive_reason = ""

    def semantic_conversation(self) -> Dict[str, Any]:
        pending = None
        if self.decision:
            pending = self.decision.to_dict()
            pending["expired"] = self.decision.expired()
        return {
            "conversation_topic": self.salience.conversation_topic,
            "has_active_task": bool(self.task and self.task.goal),
            "task_id": (self.task.task_id if self.task else ""),
            "task_goal": (self.task.goal if self.task else ""),
            "pending_decision": pending,
            "decision_id": (self.decision.decision_id if self.decision else ""),
            "awaiting_user_decision": bool(
                self.task and self.task.awaiting_user_decision
            ),
            "last_recommendation": self.last_recommendation,
        }

    def snapshot(self) -> Dict[str, Any]:
        return {
            "task": self.task.to_dict() if self.task else None,
            "decision": self.decision.to_dict() if self.decision else None,
            "salience": self.salience.to_dict(),
            "suppress_non_urgent": self.suppress_non_urgent,
            "proactive_speaking": self.proactive_speaking,
            "last_recommendation": self.last_recommendation,
        }

    def seed_task(
        self,
        *,
        task_id: str = "task-1",
        goal: str = "",
        status: str = "running",
        current_step: str = "",
        completed_steps: Optional[List[str]] = None,
        blockers: Optional[List[str]] = None,
        observations: Optional[List[str]] = None,
        possible_next_actions: Optional[List[str]] = None,
        awaiting_user_decision: bool = False,
        notify_on_complete: bool = False,
        plan_id: str = "",
    ) -> ActiveTaskContext:
        now = _now()
        self.task = ActiveTaskContext(
            task_id=task_id,
            goal=goal,
            status=status,
            current_step=current_step,
            completed_steps=list(completed_steps or []),
            blockers=list(blockers or []),
            observations=list(observations or []),
            possible_next_actions=list(possible_next_actions or []),
            awaiting_user_decision=awaiting_user_decision,
            started_at=now,
            updated_at=now,
            notify_on_complete=notify_on_complete,
            plan_id=plan_id or task_id,
        )
        self.salience.active_task = goal or task_id
        return self.task

    def seed_decision(
        self,
        *,
        decision_id: str = "dec-1",
        question: str = "",
        options: Optional[List[DecisionOption]] = None,
        recommended_option: str = "",
        recommended_why: str = "",
        problem: str = "",
        evidence: str = "",
        ttl_seconds: float = 600.0,
        strength: str = "moderate",
    ) -> OptionContext:
        opts = list(options or [])
        rec = recommended_option or (opts[0].id if opts else "")
        self.decision = OptionContext(
            decision_id=decision_id,
            question=question,
            options=opts,
            recommended_option=rec,
            recommended_why=recommended_why,
            expires_at=_now() + float(ttl_seconds or 0) if ttl_seconds else 0.0,
            problem=problem,
            evidence=evidence,
            recommendation_strength=strength,
        )
        self.salience.pending_decision = decision_id
        rec_opt = self.decision.option_by_id(rec)
        self.last_recommendation = rec_opt.label if rec_opt else rec
        if self.task:
            self.task.awaiting_user_decision = True
            self.task.updated_at = _now()
        return self.decision

    def clear_decision(self) -> None:
        self.decision = None
        self.salience.pending_decision = ""
        if self.task:
            self.task.awaiting_user_decision = False

    def note_user_speech(self) -> None:
        """Barge-in: user speech immediately wins over proactive TTS."""
        self.proactive_speaking = False

    def observe_envelope(self, envelope: Any, text: str = "") -> None:
        intent = str(getattr(envelope, "intent", "") or "")
        mode = str(getattr(envelope, "mode", "") or "")
        kind = str(getattr(envelope, "followup_kind", "") or "")
        if intent == "creative_humor" or kind == "continue_conversation":
            if self.task and kind not in ("continue_conversation",) and intent == "creative_humor":
                # Joke during task: conversation track, do not hijack task.
                pass
            self.salience.conversation_topic = "joke"
            return
        if kind in ("task_status", "return_to_task"):
            self.salience.conversation_topic = "task"
            return
        if kind in (
            "pick_option",
            "approve_recommended",
            "ask_why",
            "ask_alternative",
            "ask_recommendation",
        ):
            self.salience.conversation_topic = "decision"
            return
        if mode in ("ACT", "PLAN") and intent not in ("creative_humor",):
            self.salience.execution_referent = str(text or "")[:80]
        if intent == "social_smalltalk":
            self.salience.conversation_topic = "smalltalk"

    def initiate(
        self,
        reason: str,
        *,
        message: str = "",
        briefing: Optional[ProblemBriefing] = None,
        priority: str = "normal",
        conversation_active: bool = False,
        user_value: bool = True,
        notify_requested: bool = False,
        event_type: str = "",
        has_background_work: bool = True,
        seed_decision_from_briefing: bool = True,
    ) -> Optional[str]:
        ok, why = should_initiate(
            reason=reason,
            event_type=event_type,
            user_value=user_value,
            notify_requested=notify_requested,
            priority=priority,
            conversation_active=conversation_active,
            suppress_non_urgent=self.suppress_non_urgent,
            has_background_work=has_background_work,
        )
        if not ok:
            if why == "silent_low_value":
                self.metrics["irrelevant_proactive_speech"] += 0  # stayed silent
            if why == "internal_event":
                self.metrics["internal_event_proactive"] += 0
            return None
        spoken = compose_proactive_speech(
            reason=reason,
            task=self.task,
            briefing=briefing,
            extra=message,
        )
        if not spoken or looks_internal_tts(spoken):
            self.metrics["internal_event_proactive"] += 1
            return None
        self.last_proactive_reason = reason
        self.proactive_speaking = True
        self.salience.conversation_topic = "task" if self.task else "proactive"
        if briefing and seed_decision_from_briefing and briefing.possible_options:
            self.seed_decision(
                decision_id=f"dec-{reason}",
                question=briefing.problem,
                options=briefing.possible_options,
                recommended_option=briefing.recommended_option,
                recommended_why=briefing.why,
                problem=briefing.problem,
                evidence=briefing.cause_or_evidence,
            )
        return spoken

    def try_handle(self, text: str, envelope: Any = None) -> Optional[CollabReply]:
        raw = str(text or "").strip()
        kind = str(getattr(envelope, "followup_kind", "") or "") or classify_followup_kind(
            raw, self.semantic_conversation()
        )
        if not kind:
            return None
        if kind == "continue_conversation":
            # Planner/talk_reply tells the joke; do not run the task.
            if self.task and self.salience.execution_referent:
                # Would-be hijack avoided.
                pass
            self.salience.conversation_topic = "joke"
            return None
        if kind == "defer_proactive":
            self.suppress_non_urgent = True
            self.proactive_speaking = False
            later = bool(_LATER.search(raw))
            if later and self.last_proactive_reason:
                self.defer_pending.append(self.last_proactive_reason)
            msg = "Dobrze, wrócę do tego później." if later else "Jasne, nie teraz."
            return CollabReply(message=msg, resolved_kind=kind, source="collab")
        if kind == "wait":
            self.proactive_speaking = False
            return CollabReply(message="Dobrze, czekam.", resolved_kind=kind, source="collab")
        if kind == "no_task_status":
            return CollabReply(
                message="Nie mam teraz otwartego zadania. O czym chcesz usłyszeć?",
                resolved_kind=kind,
                source="collab",
            )
        if kind in ("task_status", "return_to_task"):
            self.salience.conversation_topic = "task"
            return CollabReply(
                message=natural_task_status(self.task),
                resolved_kind=kind,
                source="collab",
                data={"task_id": self.task.task_id if self.task else ""},
            )
        if kind == "ambiguous_continue":
            return CollabReply(
                message="Jeszcze jeden żart, czy wracamy do zadania?",
                resolved_kind=kind,
                source="collab",
            )
        if kind in (
            "ask_why",
            "ask_how",
            "ask_alternative",
            "ask_recommendation",
            "pick_option",
            "approve_recommended",
            "reject_suggestion",
        ):
            return self._handle_decision(raw, kind)
        return None

    def _handle_decision(self, raw: str, kind: str) -> CollabReply:
        dec = self.decision
        if dec is None or dec.expired():
            if kind == "reject_suggestion":
                return CollabReply(
                    message="Dobra, odpuszczam.",
                    resolved_kind="cancel_idle",
                    source="collab",
                )
            return CollabReply(
                message="Nie mam już tej decyzji na stole. Powiedz, o którą opcję chodzi.",
                resolved_kind="expired",
                source="collab",
            )
        if kind == "ask_why":
            rec = dec.option_by_id(dec.recommended_option)
            label = rec.label if rec else (self.last_recommendation or "tę opcję")
            why = dec.recommended_why or dec.evidence
            if not why:
                why = "łatwiej to sprawdzić i łatwo cofnąć."
            return CollabReply(
                message=f"Bo {why[0].lower() + why[1:]}" if why else recommendation_speech(label, why, dec.recommendation_strength),
                resolved_kind=kind,
                source="collab",
            )
        if kind == "ask_how":
            rec = dec.option_by_id(dec.recommended_option)
            label = rec.label if rec else "pierwszą opcję"
            cons = rec.consequences if rec and rec.consequences else "krok po kroku, bez zgadywania."
            return CollabReply(
                message=f"Zacząłbym od: {label}. {cons}",
                resolved_kind=kind,
                source="collab",
            )
        if kind == "ask_alternative":
            alts = [o for o in dec.options if o.id != dec.recommended_option]
            if not alts and len(dec.options) >= 2:
                alts = dec.options[1:]
            if not alts:
                return CollabReply(
                    message="Na ten moment nie mam innej sprawdzonej drogi dla tego samego problemu.",
                    resolved_kind=kind,
                    source="collab",
                )
            labels = "; ".join(f"{o.label}" + (f" — {o.consequences}" if o.consequences else "") for o in alts)
            return CollabReply(
                message=f"Dla tego samego problemu zostaje: {labels}.",
                resolved_kind=kind,
                source="collab",
            )
        if kind == "ask_recommendation":
            rec = dec.option_by_id(dec.recommended_option)
            label = rec.label if rec else "pierwszą opcję"
            return CollabReply(
                message=recommendation_speech(
                    label, dec.recommended_why, dec.recommendation_strength
                ),
                resolved_kind=kind,
                source="collab",
            )
        if kind == "reject_suggestion":
            self.clear_decision()
            return CollabReply(
                message="Zostawiam to. Daj znać, jeśli wracamy.",
                resolved_kind=kind,
                source="collab",
            )
        chosen: Optional[DecisionOption] = None
        if kind == "pick_option":
            if _SECOND.search(raw) and not _FIRST.search(raw):
                chosen = dec.options[1] if len(dec.options) > 1 else None
            elif _FIRST.search(raw):
                chosen = dec.options[0] if dec.options else None
            if chosen is None:
                self.metrics["wrong_pending_decision_resolution"] += 1
                return CollabReply(
                    message="Którą dokładnie: pierwszą czy drugą?",
                    resolved_kind="ambiguous",
                    source="collab",
                )
        elif kind == "approve_recommended":
            chosen = dec.option_by_id(dec.recommended_option) or (
                dec.options[0] if dec.options else None
            )
        if chosen is None:
            self.metrics["wrong_pending_decision_resolution"] += 1
            return CollabReply(
                message="Nie złapałem, którą opcję wybierasz.",
                resolved_kind="ambiguous",
                source="collab",
            )
        self.metrics["pending_decision_resolutions"] += 1
        self.clear_decision()
        return CollabReply(
            message=f"Dobrze. {chosen.label[0].upper() + chosen.label[1:]}.",
            resolved_kind=kind,
            execute_option_id=chosen.id,
            source="collab",
            data={"option_id": chosen.id, "action": chosen.action},
        )


def default_sensor_briefing() -> ProblemBriefing:
    return ProblemBriefing(
        problem="Nie udało się połączyć z urządzeniem.",
        cause_or_evidence="Trzy próby już nie przeszły.",
        possible_options=[
            DecisionOption(
                id="retry",
                label="spróbować ponownie",
                consequences="szybkie, ale może znów paść",
            ),
            DecisionOption(
                id="config",
                label="sprawdzić konfigurację połączenia",
                consequences="wolniejsze, ale trafia w przyczynę",
            ),
        ],
        recommended_option="config",
        why="trzy próby już nie przeszły",
        whether_user_decision_required=True,
    )
