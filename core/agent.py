"""Agent entrypoint: Planner + ToolRegistry (Brain.py stays conversational chat)."""

from __future__ import annotations

from Config import get_config_value
from Diagnostics import log_turn

from core.model_client import ModelClient, get_model_client
from core.planner import Planner, PlannerResult
from core.tools import ToolRegistry, get_registry


def agent_enabled():
    return bool(get_config_value("ai", "agent_enabled", default=True))



def _gate_agent_success_evidence(result: PlannerResult | None, context=None) -> PlannerResult | None:
    """Downgrade vacuous / no-tool success so ActiveGoal cannot COMPLETE falsely."""
    if result is None or not bool(getattr(result, "success", False)):
        return result
    if (
        getattr(result, "needs_clarification", False)
        or getattr(result, "needs_confirmation", False)
        or getattr(result, "needs_knowledge", False)
    ):
        return result
    try:
        from core.active_goal import (
            has_goal_completion_evidence,
            is_vacuous_goal_reply,
        )
    except Exception:
        return result

    obs = list(getattr(result, "observations", None) or [])
    steps = list(getattr(result, "steps", None) or [])
    last_tool = ""
    if obs:
        last_tool = str((obs[-1] or {}).get("tool") or "")
    payload = {
        "message": str(getattr(result, "message", "") or ""),
        "success": True,
        "plan_status": str(getattr(result, "plan_status", "") or ""),
        "steps": steps,
        "observations": [dict(o) for o in obs if isinstance(o, dict)],
    }
    if has_goal_completion_evidence(
        result=payload,
        observations=obs,
        steps=steps,
        action=last_tool or str(getattr(result, "plan_id", "") or ""),
    ):
        return result

    envelope = None
    if isinstance(context, dict):
        envelope = context.get("decision_envelope")
    needs_tools = bool(getattr(envelope, "needs_tools", False)) if envelope is not None else False
    mode = str(getattr(envelope, "mode", "") or "") if envelope is not None else ""
    vacuous = is_vacuous_goal_reply(str(getattr(result, "message", "") or ""))
    actionable = needs_tools or mode in ("ACT", "PLAN")
    # Pure talk/meta with no open action intent may keep success for speech path,
    # but actionable / vacuous finals without evidence are not successes.
    if actionable or vacuous or (needs_tools is False and mode in ("ACT", "PLAN")):
        result.success = False
        log_turn(
            "agent",
            event="false_success_blocked",
            mode=mode,
            needs_tools=needs_tools,
            vacuous=vacuous,
            observations=len(obs),
        )
    return result


def run_agent(
    user_text,
    *,
    registry: ToolRegistry | None = None,
    model: ModelClient | None = None,
    context=None,
    confirmed=False,
    max_steps=None,
    cancel_check=None,
) -> PlannerResult | None:
    """
    Run the multi-step agent. Returns None if agent is disabled or hard-fails
    so ConversationManager can fall back to legacy IntentRouter / Brain.
    """
    if not agent_enabled():
        return None

    registry = registry or get_registry()
    model = model or get_model_client()
    steps = max_steps
    if steps is None:
        steps = int(get_config_value("ai", "agent_max_steps", default=6))

    planner = Planner(registry, model=model, max_steps=steps)
    try:
        result = planner.run(
            user_text,
            context=context,
            confirmed=confirmed,
            cancel_check=cancel_check,
        )
    except Exception as error:
        print("Błąd agenta:", error)
        log_turn("agent", event="crash", error=str(error)[:160])
        return None

    return _gate_agent_success_evidence(result, context)
