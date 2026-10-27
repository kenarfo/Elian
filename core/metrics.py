# ==========================
# ELIAN — local metrics (Phase 8 + Phase 9)
# ==========================
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


LEGACY_KEYS = (
    "skills",
    "commands",
    "intent_router",
    "goal_router",
    "memory",
    "wake_text",
    "screenshot",
)


@dataclass
class ConversationMetrics:
    agent_success: int = 0
    agent_failure: int = 0
    clarifications: int = 0
    tool_calls: int = 0
    planner_steps: int = 0
    recovery_count: int = 0
    failed_goals: int = 0
    legacy_fallback_used: int = 0
    legacy_breakdown: Dict[str, int] = field(default_factory=dict)
    tool_failures: int = 0
    recovery_success: int = 0
    no_progress_stops: int = 0
    corrections: int = 0
    silent_success: int = 0
    model_invalid_json: int = 0
    model_repair_attempt: int = 0
    agent_calls: int = 0
    brain_calls: int = 0
    vision_calls: int = 0
    research_calls: int = 0
    text_corrector_calls: int = 0
    generic_callcenter_closer_count: int = 0
    followup_offer_count: int = 0
    followup_offer_total: int = 0
    irrelevant_proactive_speech_count: int = 0
    internal_event_proactive_count: int = 0
    wrong_pending_decision_resolution_count: int = 0
    task_context_hijack_count: int = 0
    conversation_context_hijack_count: int = 0
    useful_followup_offer_count: int = 0
    irrelevant_context_injection_count: int = 0
    speech_transitions: int = 0
    barge_in_count: int = 0
    echo_suppressed: int = 0
    processing_recovered: int = 0
    illegal_state_hops: int = 0
    interaction_traces: List[Dict[str, Any]] = field(default_factory=list)

    def record_agent(self, *, success: bool = True, steps: int = 0, clarification: bool = False):
        if clarification:
            self.clarifications += 1
            return
        if success:
            self.agent_success += 1
        else:
            self.agent_failure += 1
            self.failed_goals += 1
        self.planner_steps += int(steps or 0)

    def record_tool(self, *, success: bool):
        self.tool_calls += 1
        if not success:
            self.tool_failures += 1

    def record_legacy(self, kind: str = "unknown"):
        self.legacy_fallback_used += 1
        key = str(kind or "unknown")
        self.legacy_breakdown[key] = self.legacy_breakdown.get(key, 0) + 1

    def record_recovery(self, *, success: bool):
        self.recovery_count += 1
        if success:
            self.recovery_success += 1

    def record_model_call(self, kind: str):
        key = str(kind or "").lower()
        if key == "agent":
            self.agent_calls += 1
        elif key == "brain":
            self.brain_calls += 1
        elif key == "vision":
            self.vision_calls += 1
        elif key == "research":
            self.research_calls += 1
        elif key == "text_corrector":
            self.text_corrector_calls += 1

    def record_interaction_trace(self, trace: Dict[str, Any], *, keep_last: int = 20):
        self.interaction_traces.append(dict(trace))
        if len(self.interaction_traces) > keep_last:
            self.interaction_traces = self.interaction_traces[-keep_last:]

    def snapshot(self) -> Dict[str, Any]:
        total_agent = max(1, self.agent_success + self.agent_failure)
        total_tools = max(1, self.tool_calls)
        legacy_den = max(1, self.agent_success + self.agent_failure + self.legacy_fallback_used)
        legacy_detail = {k: self.legacy_breakdown.get(k, 0) for k in LEGACY_KEYS}
        return {
            "agent_success": self.agent_success,
            "agent_failure": self.agent_failure,
            "clarification_rate": round(self.clarifications / total_agent, 3),
            "legacy_fallback_rate": round(self.legacy_fallback_used / legacy_den, 3),
            "legacy": legacy_detail,
            "average_planner_steps": round(
                self.planner_steps / max(1, self.agent_success + self.agent_failure), 3
            ),
            "tool_failure_rate": round(self.tool_failures / total_tools, 3),
            "recovery_success_rate": (
                round(self.recovery_success / max(1, self.recovery_count), 3)
                if self.recovery_count
                else 0.0
            ),
            "clarifications": self.clarifications,
            "tool_calls": self.tool_calls,
            "planner_steps": self.planner_steps,
            "recovery_count": self.recovery_count,
            "failed_goals": self.failed_goals,
            "legacy_fallback_used": self.legacy_fallback_used,
            "no_progress_stops": self.no_progress_stops,
            "corrections": self.corrections,
            "silent_success": self.silent_success,
            "generic_callcenter_closer_rate": round(
                self.generic_callcenter_closer_count / max(1, self.followup_offer_total),
                3,
            ),
            "forced_followup_offer_rate": round(
                self.followup_offer_count / max(1, self.followup_offer_total),
                3,
            ),
            "useful_followup_offer_rate": round(
                (self.useful_followup_offer_count or self.followup_offer_count)
                / max(1, self.followup_offer_total),
                3,
            ),
            "irrelevant_proactive_speech_rate": float(self.irrelevant_proactive_speech_count),
            "internal_event_proactive_rate": float(self.internal_event_proactive_count),
            "wrong_pending_decision_resolution_rate": float(
                self.wrong_pending_decision_resolution_count
            ),
            "task_context_hijack_rate": float(self.task_context_hijack_count),
            "conversation_context_hijack_rate": float(self.conversation_context_hijack_count),
            "irrelevant_context_injection_rate": float(self.irrelevant_context_injection_count),
            "model_invalid_json": self.model_invalid_json,
            "model_repair_attempt": self.model_repair_attempt,
            "model_calls": {
                "agent": self.agent_calls,
                "brain": self.brain_calls,
                "vision": self.vision_calls,
                "research": self.research_calls,
                "text_corrector": self.text_corrector_calls,
            },
            "speech_transitions": self.speech_transitions,
            "barge_in_count": self.barge_in_count,
            "echo_suppressed": self.echo_suppressed,
            "processing_recovered": self.processing_recovered,
            "illegal_state_hops": self.illegal_state_hops,
            "last_interaction": (
                self.interaction_traces[-1] if self.interaction_traces else None
            ),
        }


_METRICS = ConversationMetrics()


def get_metrics() -> ConversationMetrics:
    return _METRICS


def reset_metrics() -> ConversationMetrics:
    global _METRICS
    _METRICS = ConversationMetrics()
    return _METRICS
