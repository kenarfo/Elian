# ==========================
# ELIAN — ActionLoop (understand → plan → execute → verify → repair)
# ==========================
"""Session-facing loop around Planner / ToolRegistry / plan_cycle.

Does not replace Planner. Handles soft goal shortcuts, capability honesty,
journal replay, and natural failure explanations before/around Agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from Diagnostics import log_turn

from core.capability_gate import CapabilityDecision, capabilities_for_goal
from core.goal_context import GoalContext, build_goal_context
from core.soft_goals import detect_soft_goal
from core.tool_result import ToolResult, ToolStatus
from core.user_goal import FailureMode, UserGoal, formulate_user_goal, honest_failure_reply


@dataclass
class ActionLoopResult:
    handled: bool = False
    message: str = ""
    source: str = "action_loop"
    user_goal: Optional[UserGoal] = None
    capability: Optional[CapabilityDecision] = None
    observations: List[Dict[str, Any]] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    skip_fast_path: bool = False
    force_agent: bool = False
    agent_text: str = ""
    classification_override: Any = None
    retry_pending: bool = False


def _vacuous_tool_msg(msg: str) -> bool:
    low = str(msg or "").strip().lower().rstrip(".!")
    if not low:
        return True
    if low in ("ok", "gotowe", "zrobione", "wykonałem polecenie", "wykonalem polecenie"):
        return True
    if low.startswith("ok:") or low.startswith("ok "):
        return True
    return False


def _observation_verified(result: ToolResult) -> bool:
    data = dict(getattr(result, "data", None) or {})
    ha = data.get("ha_verify") if isinstance(data.get("ha_verify"), dict) else {}
    outcome = str(
        data.get("verification")
        or data.get("ha_outcome")
        or ha.get("verification")
        or ha.get("status")
        or ""
    ).upper()
    if outcome in ("SUCCESS", "PASSED", "VERIFIED_SUCCESS", "ALREADY_SATISFIED"):
        return True
    if outcome in ("UNKNOWN", "PARTIAL", "FAILED", "UNAVAILABLE"):
        return False
    # Local volume / media: treat successful tool run as confirmed for soft speech.
    tool = str(getattr(result, "tool_name", "") or "").lower()
    if tool in ("volume_down", "volume_up", "play_media", "get_now_playing", "open_website", "web_search"):
        return bool(getattr(result, "success", False))
    return False


def _natural_success(tool: str, *, goal: UserGoal, result: ToolResult) -> str:
    msg = str(getattr(result, "message", "") or "").strip()
    if _vacuous_tool_msg(msg):
        msg = ""
    soft = goal.soft_kind
    verified = _observation_verified(result)
    if soft == "volume_down":
        return msg or ("Ściszyłem." if verified else "Wysłałem ściszenie, ale nie potwierdziłem poziomu.")
    if soft == "volume_up":
        return msg or ("Podgłośniłem." if verified else "Wysłałem podgłośnienie, ale nie potwierdziłem poziomu.")
    if soft in ("brightness_up",):
        return msg or ("Rozjaśniłem." if verified else "Wysłałem rozjaśnienie, ale nie mogę potwierdzić.")
    if soft in ("brightness_down",):
        return msg or ("Przyciemniłem." if verified else "Wysłałem przyciemnienie, ale nie mogę potwierdzić.")
    if soft == "light_off":
        return msg or ("Wyłączam światło." if verified else "Wysłałem wyłączenie, ale nie mogę potwierdzić.")
    if soft == "light_on":
        return msg or ("Włączam światło." if verified else "Wysłałem włączenie, ale nie mogę potwierdzić.")
    if soft == "media_calm":
        return msg or "Puściłem coś spokojniejszego."
    if soft == "media_play":
        q = str((goal.slots or {}).get("media_query") or "")
        if msg:
            return msg
        return f"Puszczam {q}." if q and q != "muzyka" else "Puszczam."
    if soft == "media_now":
        return msg or "Sprawdzam, co leci."
    if soft == "open_result":
        title = str((goal.slots or {}).get("title") or "")
        idx = (goal.slots or {}).get("index")
        if msg:
            return msg
        if title:
            return f"Otwieram: {title}."
        if idx == 1:
            return "Otwieram drugi wynik."
        if idx == 2:
            return "Otwieram trzeci wynik."
        return "Otwieram wynik."
    if soft == "web_search":
        q = str((goal.slots or {}).get("query") or goal.target or "")
        return msg or (f"Szukam: {q}." if q else "Szukam.")
    if soft == "replay_last":
        return msg or "Powtórzyłem ostatnią czynność."
    if soft == "gaming_setup":
        return msg or "Ustawiam tryb do grania."
    if msg:
        return msg
    # Never claim bare Gotowe/Zrobione without verification evidence.
    if verified:
        return "Zrobione."
    return "Wysłałem polecenie, ale nie mogę tego potwierdzić."


def _summarize_prior_web(goal: UserGoal, ctx: GoalContext) -> str:
    """Short grounded summary from prior results / last answer — no new web_search."""
    slots = dict(goal.slots or {})
    results = list(slots.get("results") or getattr(ctx, "last_web_results", None) or [])
    query = str(slots.get("query") or getattr(ctx, "last_web_query", "") or "").strip()
    assistant = str(slots.get("last_assistant") or getattr(ctx, "last_assistant", "") or "").strip()

    try:
        from core.response_finalize import is_ack_like, looks_like_tool_status
        from core.web_synthesis import is_generic_failure, is_useful_web_answer
    except Exception:
        is_ack_like = lambda _m: False  # noqa: E731
        looks_like_tool_status = lambda _m: False  # noqa: E731
        is_generic_failure = lambda _m: False  # noqa: E731
        is_useful_web_answer = lambda _m, **_k: bool(_m and len(str(_m)) >= 8)

    _SEARCH_ACK = re.compile(
        r"(?:"
        r"znalaz[łl]em\s+wynik|"
        r"mam\s+wyniki|"
        r"found\s+(?:\d+\s+)?results?|"
        r"szukam\b|"
        r"chwila\s*[—\-].*wynik|"
        r"^(?:otwieram|uruchomiłem|gotowe|zrobione|ok)\b"
        r")",
        re.I,
    )

    usable_assistant = (
        bool(assistant)
        and not is_generic_failure(assistant)
        and not is_ack_like(assistant)
        and not looks_like_tool_status(assistant)
        and not _SEARCH_ACK.search(assistant)
        and is_useful_web_answer(assistant, question_type="OTHER", query=query)
    )
    if usable_assistant:
        parts = re.split(r"(?<=[.!?…])\s+", assistant.strip())
        brief = " ".join(p for p in parts[:2] if p).strip()
        if brief:
            return brief if brief.endswith((".", "!", "?", "…")) else f"{brief}."

    if results:
        try:
            from core.web_grounding import rank_hits
            from core.web_synthesis import synthesize_from_evidence

            ranked = rank_hits(results, query=query or goal.objective or "")
            synth, _origin = synthesize_from_evidence(
                query or "streść wyniki",
                ranked,
                lang="pl",
            )
            if synth and not is_generic_failure(synth) and not _SEARCH_ACK.search(synth):
                return synth
        except Exception:
            pass
        # Honest brief without inventing facts beyond titles/snippets.
        try:
            from core.web_synthesis import evidence_briefs

            briefs = evidence_briefs(results, max_items=2)
            if briefs:
                if len(briefs) == 1:
                    return f"W skrócie: {briefs[0]}"
                return f"W skrócie: {briefs[0]} Dodatkowo: {briefs[1]}"
        except Exception:
            pass
        titles = []
        for row in results[:3]:
            if isinstance(row, dict):
                title = str(row.get("title") or "").strip()
                if title:
                    titles.append(title)
        if titles:
            topic = query or "temat"
            joined = "; ".join(titles[:2])
            return f"W skrócie o {topic}: {joined}."

    return "Nie mam jeszcze treści do streszczenia."


def _brightness_args(goal: UserGoal, *, down: bool) -> Dict[str, Any]:
    slots = dict(goal.slots or {})
    entity = str(slots.get("entity_id") or slots.get("target") or goal.target or "")
    args: Dict[str, Any] = {
        "action": "adjust_brightness",
        "domain": "light",
        "value": -15 if down else 20,
    }
    if entity:
        args["entity_id"] = entity
        args["target"] = entity
    elif slots.get("implicit_light"):
        args["target"] = "światło"
    return args


def _explain_failure(goal: UserGoal, ctx: GoalContext) -> str:
    pending = dict(goal.slots.get("pending") or ctx.pending_executable or {})
    unresolved = dict(goal.slots.get("unresolved") or ctx.unresolved_task or {})
    prev = dict(goal.slots.get("previous_result") or ctx.previous_result or {})
    if pending or unresolved:
        failed = list(pending.get("failed_steps") or unresolved.get("failed_steps") or [])
        labels = []
        for step in failed:
            if isinstance(step, dict):
                labels.append(
                    str(step.get("semantic_action") or step.get("tool") or step.get("text") or "")
                )
        labels = [x for x in labels if x]
        if labels:
            return (
                f"Jeszcze tego nie domknąłem — padło na: {', '.join(labels[:3])}. "
                "Powiedz „spróbuj jeszcze raz”, to ponowię."
            )
        goal_txt = str(pending.get("goal") or unresolved.get("goal") or "").strip()
        if goal_txt:
            return (
                f"Jeszcze nie dokończyłem: {goal_txt[:80]}. "
                "Mogę spróbować ponownie."
            )
        return "Mam niedokończone zadanie. Powiedz „spróbuj jeszcze raz”."
    if prev and not prev.get("success"):
        tool = str(prev.get("tool") or "narzędzie")
        err = str(prev.get("message") or prev.get("error") or "").strip()
        if err:
            return f"Ostatnia próba ({tool}) nie wyszła: {err[:100]}"
        return f"Ostatnia próba ({tool}) nie wyszła. Mogę spróbować inaczej."
    if ctx.last_assistant and "nie" in ctx.last_assistant.lower():
        return "Bo poprzednia próba nie przeszła weryfikacji. Mogę spróbować jeszcze raz."
    return "Nie widzę niedokończonej czynności z poprzedniej tury. Co mam zrobić?"


def _invoke(
    registry: Any,
    tool: str,
    arguments: Dict[str, Any],
    *,
    confirmed: bool = False,
) -> ToolResult:
    return registry.invoke(tool, dict(arguments or {}), confirmed=confirmed)


def run_soft_action_loop(
    text: str,
    *,
    registry: Any,
    memory: Any = None,
    envelope: Any = None,
    assessment: Any = None,
    guidance: Any = None,
    classification: Any = None,
    pending_executable: Optional[Dict[str, Any]] = None,
    last_observations: Optional[List[Dict[str, Any]]] = None,
    last_assistant: str = "",
    last_user_goal: Optional[Dict[str, Any]] = None,
    confirmed: bool = False,
) -> ActionLoopResult:
    """Formulate UserGoal; handle soft shortcuts / honesty; else hand off to Agent."""
    ctx = build_goal_context(
        memory=memory,
        pending_executable=pending_executable,
        last_observations=last_observations,
        last_assistant=last_assistant,
        last_user_goal=last_user_goal,
        envelope=envelope,
    )
    soft = detect_soft_goal(text, goal_context=ctx, envelope=envelope)
    goal = formulate_user_goal(
        text,
        envelope=envelope,
        assessment=assessment,
        guidance=guidance,
        classification=classification,
        goal_context=ctx,
        soft=soft,
    )
    cap = capabilities_for_goal(goal, registry=registry)
    result = ActionLoopResult(user_goal=goal, capability=cap, agent_text=goal.planner_goal_text())

    log_turn(
        "action_loop",
        soft_kind=goal.soft_kind or "",
        requires=goal.primary_requirement,
        family=goal.intent_family or "",
        allowed=bool(cap.allowed),
        confidence=round(float(goal.confidence or 0.0), 3),
    )

    if not cap.allowed:
        goal.failure_mode = cap.failure_mode or FailureMode.NO_CAPABILITY.value
        result.handled = True
        result.message = cap.message or honest_failure_reply(goal.failure_mode)
        result.source = "capability_gate"
        result.data = {"user_goal": goal.to_dict(), "capability": cap.to_dict()}
        return result

    soft_kind = goal.soft_kind

    # Semantic talk/answer lock: never mutate when DecisionEnvelope forbids tools.
    _MUTATING_SOFT = frozenset(
        {
            "brightness_up",
            "brightness_down",
            "light_on",
            "light_off",
            "volume_up",
            "volume_down",
            "media_play",
            "media_calm",
            "media_now",
            "open_result",
            "web_search",
            "web_about",
            "gaming_setup",
            "open_app",
            "set_temperature",
        }
    )
    if soft_kind in _MUTATING_SOFT and envelope is not None:
        mode = str(getattr(envelope, "mode", "") or "")
        needs = bool(getattr(envelope, "needs_tools", False))
        intent = str(getattr(envelope, "intent", "") or "")
        # Live P3: web ordinal follow-ups ("a drugi") often arrive as TALK/REFERENCE
        # but soft_goals already resolved open_result from last_web_results.
        allow_open_result = soft_kind == "open_result" and bool(
            (goal.slots or {}).get("url") or goal.target
        )
        # Definitional "czym jest X" stays ANSWER in semantic (behavioral corpus),
        # but soft_define may still ground via read-only web_search.
        allow_define_web = soft_kind == "web_search" and str(goal.source or "").startswith(
            "soft_define"
        )
        allow_media_mood = soft_kind == "media_calm" and str(goal.source or "").startswith(
            "soft_media_mood"
        )
        allow_light_off_talk = soft_kind == "light_off" and str(goal.source or "").startswith(
            "soft_light_off"
        )
        allow_light_talk = soft_kind in ("brightness_up", "brightness_down") and str(
            goal.source or ""
        ).startswith("soft_light")
        allow_volume_talk = soft_kind in ("volume_up", "volume_down") and str(
            goal.source or ""
        ).startswith("soft_volume")
        if (
            not allow_open_result
            and not allow_define_web
            and not allow_media_mood
            and not allow_light_off_talk
            and not allow_light_talk
            and not allow_volume_talk
            and (
                (mode in ("TALK", "ANSWER", "WAIT") and not needs)
                or intent
                in (
                    "action_discussion",
                    "creative_humor",
                    "social_smalltalk",
                )
            )
        ):
            result.handled = False
            result.skip_fast_path = True
            result.data = {
                "user_goal": goal.to_dict(),
                "suppressed": "semantic_talk_lock",
            }
            return result

    if soft_kind == "explain_failure":
        result.handled = True
        result.message = _explain_failure(goal, ctx)
        result.source = "action_loop"
        result.data = {"user_goal": goal.to_dict()}
        return result

    if soft_kind == "unclear":
        result.handled = True
        result.skip_fast_path = True
        obj = str(goal.objective or "").lower()
        if "streszczenia" in obj:
            result.message = "Nie mam jeszcze treści do streszczenia."
        elif "wynik" in obj or str(goal.source or "") == "soft_open_first_missing":
            result.message = "Nie mam jeszcze wyników wyszukiwania do otwarcia."
        else:
            result.message = str(goal.objective or "Doprecyzuj, o co Ci chodzi.")
        result.source = "action_loop"
        result.data = {"user_goal": goal.to_dict(), "clarify": True}
        return result

    if soft_kind == "ask_goal":
        result.handled = True
        result.skip_fast_path = True
        if (goal.slots or {}).get("alternate_after_fail"):
            result.message = "Jak mam to zrobić inaczej?"
        else:
            result.message = "Co mam zrobić?"
        result.source = "action_loop"
        result.data = {"user_goal": goal.to_dict(), "ask": True}
        return result

    if soft_kind == "cancel_goal":
        result.handled = True
        result.message = "Dobra, odpuszczam."
        result.source = "action_loop"
        result.skip_fast_path = True
        result.data = {"user_goal": goal.to_dict(), "cancel": True}
        return result

    if soft_kind == "retry_pending":
        result.handled = False
        result.retry_pending = True
        result.skip_fast_path = True
        result.force_agent = True
        result.data = {"user_goal": goal.to_dict()}
        return result

    if soft_kind == "resume_previous":
        prev = dict((goal.slots or {}).get("previous_goal") or {})
        result.handled = False
        result.skip_fast_path = True
        result.force_agent = True
        result.agent_text = str(prev.get("objective") or goal.planner_goal_text())
        result.data = {"user_goal": goal.to_dict()}
        return result

    if soft_kind == "open_result":
        url = str((goal.slots or {}).get("url") or goal.target or "")
        if not url:
            result.handled = True
            result.message = honest_failure_reply(
                FailureMode.MISSING_INFO.value,
                detail="Nie mam wyniku wyszukiwania do otwarcia.",
            )
            return result
        tool_result = _invoke(registry, "open_website", {"target": url}, confirmed=confirmed)
        obs = tool_result.to_observation()
        result.observations.append(obs)
        result.handled = True
        result.message = (
            _natural_success("open_website", goal=goal, result=tool_result)
            if tool_result.success
            else honest_failure_reply(FailureMode.TOOL_ERROR.value, detail=tool_result.message)
        )
        result.data = {"user_goal": goal.to_dict(), "observation": obs}
        return result

    if soft_kind == "summarize_web":
        result.handled = True
        result.skip_fast_path = True
        result.message = _summarize_prior_web(goal, ctx)
        result.source = "action_loop"
        result.data = {
            "user_goal": goal.to_dict(),
            "summarize_web": True,
            "web_query": str((goal.slots or {}).get("query") or ""),
        }
        return result

    if soft_kind == "replay_last":
        tool = str((goal.slots or {}).get("tool") or "")
        args = dict((goal.slots or {}).get("arguments") or {})
        if not tool:
            result.handled = True
            result.message = "Nie mam ostatniej czynności do powtórzenia."
            return result
        tool_result = _invoke(registry, tool, args, confirmed=confirmed)
        obs = tool_result.to_observation()
        result.observations.append(obs)
        result.handled = True
        result.message = (
            _natural_success(tool, goal=goal, result=tool_result)
            if tool_result.success
            else honest_failure_reply(FailureMode.TOOL_ERROR.value, detail=tool_result.message)
        )
        result.data = {"user_goal": goal.to_dict(), "observation": obs}
        return result

    if soft_kind in ("volume_down", "volume_up"):
        tool = "volume_down" if soft_kind == "volume_down" else "volume_up"
        tool_result = _invoke(registry, tool, {}, confirmed=confirmed)
        obs = tool_result.to_observation()
        result.observations.append(obs)
        result.handled = True
        result.message = (
            _natural_success(tool, goal=goal, result=tool_result)
            if tool_result.success
            else honest_failure_reply(FailureMode.TOOL_ERROR.value, detail=tool_result.message)
        )
        result.data = {"user_goal": goal.to_dict(), "observation": obs}
        # Soft volume is still a deterministic shortcut; CM FastPath may also own bare volume.
        result.skip_fast_path = True
        return result

    if soft_kind == "media_now":
        if hasattr(registry, "has") and registry.has("get_now_playing"):
            tool_result = _invoke(registry, "get_now_playing", {}, confirmed=confirmed)
            obs = tool_result.to_observation()
            result.observations.append(obs)
            result.handled = True
            if tool_result.success:
                data = dict(tool_result.data or {})
                title = str(data.get("title") or "")
                artist = str(data.get("artist") or "")
                if title and artist:
                    result.message = f"Leci {artist} — {title}."
                elif title:
                    result.message = f"Leci: {title}."
                else:
                    result.message = _natural_success(
                        "get_now_playing", goal=goal, result=tool_result
                    )
            else:
                result.message = honest_failure_reply(
                    FailureMode.TOOL_ERROR.value, detail=tool_result.message
                )
            result.data = {"user_goal": goal.to_dict(), "observation": obs}
            result.skip_fast_path = True
            return result
        result.handled = False
        result.force_agent = True
        result.skip_fast_path = True
        result.agent_text = "co teraz leci"
        return result

    if soft_kind in ("media_calm", "media_play"):
        if soft_kind == "media_play" and goal.requires_clarification:
            # Explicit clarify from goal formulation — don't invent a play.
            result.handled = False
            result.force_agent = True
            result.skip_fast_path = True
            result.agent_text = goal.planner_goal_text()
            return result
        query = str((goal.slots or {}).get("media_query") or "muzyka")
        play_args: Dict[str, Any] = {"query": query}
        # Same READ-ONLY default as Planner — soft path must not bypass prefs.
        if soft_kind == "media_play" and memory is not None:
            try:
                default_app = memory.get_preference("music.default_app")
                if default_app:
                    play_args["app"] = str(default_app)
            except Exception:
                pass
        tool_result = _invoke(registry, "play_media", play_args, confirmed=confirmed)
        obs = tool_result.to_observation()
        result.observations.append(obs)
        result.handled = True
        result.message = (
            _natural_success("play_media", goal=goal, result=tool_result)
            if tool_result.success
            else honest_failure_reply(FailureMode.TOOL_ERROR.value, detail=tool_result.message)
        )
        result.data = {"user_goal": goal.to_dict(), "observation": obs}
        result.skip_fast_path = True
        return result

    if soft_kind == "web_search":
        query = str((goal.slots or {}).get("query") or goal.target or "").strip()
        if query:
            try:
                from core.web_grounding import clean_web_query

                query = clean_web_query(query) or query
            except Exception:
                pass
        if not query:
            result.handled = False
            result.force_agent = True
            result.skip_fast_path = True
            result.agent_text = goal.planner_goal_text()
            return result
        if hasattr(registry, "has") and registry.has("web_search"):
            tool_result = _invoke(registry, "web_search", {"query": query}, confirmed=confirmed)
            obs = tool_result.to_observation()
            result.observations.append(obs)
            result.handled = True
            result.skip_fast_path = True
            if tool_result.success:
                data = dict(tool_result.data or {})
                results = list(data.get("ranked_results") or data.get("results") or [])
                spoken = ""
                if results:
                    try:
                        from core.web_grounding import rank_hits
                        from core.web_synthesis import (
                            is_generic_failure,
                            synthesize_from_evidence,
                        )

                        ranked = rank_hits(results, query=query)
                        synth, _ = synthesize_from_evidence(query, ranked, lang="pl")
                        if synth and not is_generic_failure(synth):
                            spoken = synth
                    except Exception:
                        spoken = ""
                    if not spoken:
                        try:
                            from core.web_synthesis import evidence_briefs

                            briefs = evidence_briefs(results, max_items=2)
                            if briefs:
                                spoken = (
                                    f"W skrócie: {briefs[0]}"
                                    if len(briefs) == 1
                                    else f"W skrócie: {briefs[0]} Dodatkowo: {briefs[1]}"
                                )
                        except Exception:
                            spoken = ""
                    if not spoken:
                        titles = [
                            str(r.get("title") or "").strip()
                            for r in results[:2]
                            if isinstance(r, dict) and str(r.get("title") or "").strip()
                        ]
                        if titles:
                            spoken = f"W skrócie o {query}: {'; '.join(titles)}."
                if not spoken:
                    raw = str(tool_result.message or "").strip()
                    if raw and raw.lower().rstrip(".!") not in (
                        "ok",
                        "gotowe",
                        "zrobione",
                    ) and not raw.lower().startswith("ok:"):
                        # Prefer not to speak bare "Znalazłem wyniki." when we have no extract.
                        if re.search(r"znalaz[łl]em\s+wynik", raw, re.I) and results:
                            spoken = f"Mam wyniki o {query}. Mogę streścić albo otworzyć pierwszy."
                        else:
                            spoken = raw
                    else:
                        spoken = f"Szukam: {query}."
                result.message = spoken
            else:
                result.message = honest_failure_reply(
                    FailureMode.TOOL_ERROR.value, detail=tool_result.message
                )
            result.data = {"user_goal": goal.to_dict(), "observation": obs}
            return result

    if soft_kind == "gaming_setup":
        # Prefer procedure when present; else open Steam as safe gaming prep.
        if hasattr(registry, "has") and registry.has("procedure_run"):
            tool_result = _invoke(
                registry,
                "procedure_run",
                {"procedure_id": "gaming", "name": "gaming"},
                confirmed=confirmed,
            )
            if tool_result.success or tool_result.status == ToolStatus.NEEDS_CONFIRMATION:
                obs = tool_result.to_observation()
                result.observations.append(obs)
                result.handled = True
                result.message = _natural_success(
                    "procedure_run", goal=goal, result=tool_result
                )
                result.data = {"user_goal": goal.to_dict(), "observation": obs}
                return result
            # Unavailable / missing procedure → fall through to open_app.
        # Deterministic fallback: open Steam (common gaming hub) without fake success.
        if hasattr(registry, "has") and registry.has("open_app"):
            tool_result = _invoke(registry, "open_app", {"name": "Steam"}, confirmed=confirmed)
            obs = tool_result.to_observation()
            result.observations.append(obs)
            result.handled = True
            if tool_result.success:
                result.message = "Odpalam Steam pod granie."
            else:
                result.message = honest_failure_reply(
                    FailureMode.TOOL_ERROR.value,
                    detail=tool_result.message or "Nie udało się przygotować trybu grania.",
                )
            result.data = {"user_goal": goal.to_dict(), "observation": obs}
            return result
        result.handled = True
        result.message = honest_failure_reply(
            FailureMode.NO_CAPABILITY.value,
            detail="Nie mam procedury gaming ani aplikacji do grania pod ręką.",
        )
        return result

    # Soft lighting — execute when we can; else Agent with classification override.
    if soft_kind in ("brightness_up", "brightness_down", "light_on", "light_off"):
        slots = dict(goal.slots or {})
        entity = str(slots.get("entity_id") or slots.get("target") or goal.target or "")
        resolve_reason = str(slots.get("resolve_reason") or "")
        resolved = bool(slots.get("resolved_context") and entity)
        raw_light = str(goal.raw_text or text or "")
        low = (
            raw_light.casefold()
            .replace("ą", "a")
            .replace("ę", "e")
            .replace("ó", "o")
            .replace("ś", "s")
            .replace("ł", "l")
            .replace("ż", "z")
            .replace("ź", "z")
            .replace("ć", "c")
            .replace("ń", "n")
        )
        tokens = set(low.replace(".", " ").replace(",", " ").split())
        pronounish = bool(tokens & {"ja", "go", "niej", "nim", "to", "te"})
        # Pronoun without discourse/active referent must not inherit singleton alias/registry.
        if pronounish and resolve_reason not in ("active", "previous_slot"):
            resolved = False
            entity = ""
            resolve_reason = "ambiguous"
            slots = dict(slots)
            slots["resolve_reason"] = "ambiguous"
            slots.pop("entity_id", None)
            slots.pop("target", None)
            slots.pop("resolved_context", None)
        ambiguous = resolve_reason == "ambiguous" or bool(
            (goal.requires_clarification or resolve_reason == "none") and not entity
        )
        if ambiguous and not resolved:
            # ASK once — including pronoun without referent ("rozjaśnij ją").
            result.handled = True
            result.skip_fast_path = True
            result.message = (
                "Które światło mam wyłączyć?"
                if soft_kind == "light_off"
                else (
                    "Które światło mam przyciemnić?"
                    if soft_kind == "brightness_down"
                    else "Które światło mam rozjaśnić?"
                )
            )
            result.data = {
                "user_goal": goal.to_dict(),
                "needs_clarification": True,
                "question_type": "light_target",
                "ask_once": True,
            }
            return result
        # Pronoun follow-ups without a bound entity fall through to device-resolution / FastPath.
        # When soft already resolved the light, execute here (incl. "zgaś ją").
        if pronounish and not resolved and soft_kind != "light_off":
            result.handled = False
            result.skip_fast_path = False
            result.force_agent = False
            return result
        if soft_kind == "light_on" and hasattr(registry, "has") and registry.has("turn_on_device"):
            args = {
                "target": entity or "światło",
                "domain": "light",
                "utterance": goal.raw_text or goal.objective,
            }
            if entity:
                args["entity_id"] = entity
            tool_result = _invoke(registry, "turn_on_device", args, confirmed=confirmed)
            obs = tool_result.to_observation()
            result.observations.append(obs)
            result.handled = True
            result.skip_fast_path = True
            result.message = _natural_success("turn_on_device", goal=goal, result=tool_result)
            if not tool_result.success:
                result.message = honest_failure_reply(
                    FailureMode.TOOL_ERROR.value, detail=tool_result.message
                )
            result.data = {"user_goal": goal.to_dict(), "observation": obs}
            return result
        if soft_kind == "light_off" and hasattr(registry, "has") and registry.has("turn_off_device"):
            if not resolved and pronounish:
                # Let CM pronoun binder execute with active device.
                result.handled = False
                result.skip_fast_path = False
                result.force_agent = False
                return result
            args = {
                "target": entity or "światło",
                "domain": "light",
                "utterance": goal.raw_text or goal.objective,
            }
            if entity:
                args["entity_id"] = entity
            tool_result = _invoke(registry, "turn_off_device", args, confirmed=confirmed)
            obs = tool_result.to_observation()
            result.observations.append(obs)
            result.handled = True
            result.skip_fast_path = True
            if tool_result.success:
                try:
                    from core.ha_speech import speak_ha_result

                    spoken = speak_ha_result(tool_result, utterance=raw_light)
                except Exception:
                    spoken = ""
                result.message = spoken or _natural_success(
                    "turn_off_device", goal=goal, result=tool_result
                )
            else:
                result.message = honest_failure_reply(
                    FailureMode.TOOL_ERROR.value, detail=tool_result.message
                )
            result.data = {"user_goal": goal.to_dict(), "observation": obs}
            return result
        down = soft_kind == "brightness_down"
        if soft_kind in ("brightness_up", "brightness_down") and hasattr(registry, "has"):
            if resolved and registry.has("set_device_value"):
                tool_result = _invoke(
                    registry,
                    "set_device_value",
                    _brightness_args(goal, down=down),
                    confirmed=confirmed,
                )
                obs = tool_result.to_observation()
                result.observations.append(obs)
                result.handled = True
                result.skip_fast_path = True
                if tool_result.success:
                    result.message = _natural_success(
                        "set_device_value", goal=goal, result=tool_result
                    )
                else:
                    result.message = honest_failure_reply(
                        FailureMode.TOOL_ERROR.value,
                        detail=tool_result.message or "Nie udało się zmienić jasności.",
                    )
                result.data = {"user_goal": goal.to_dict(), "observation": obs}
                return result
            if (
                soft_kind == "brightness_up"
                and resolved
                and registry.has("turn_on_device")
            ):
                args = {
                    "target": entity,
                    "domain": "light",
                    "entity_id": entity,
                }
                tool_result = _invoke(registry, "turn_on_device", args, confirmed=confirmed)
                obs = tool_result.to_observation()
                result.observations.append(obs)
                result.handled = True
                result.skip_fast_path = True
                result.message = (
                    _natural_success("turn_on_device", goal=goal, result=tool_result)
                    if tool_result.success
                    else honest_failure_reply(
                        FailureMode.TOOL_ERROR.value, detail=tool_result.message
                    )
                )
                result.data = {"user_goal": goal.to_dict(), "observation": obs}
                return result
            # Unresolved brightness — ask once instead of agent clarify churn.
            if not resolved:
                result.handled = True
                result.skip_fast_path = True
                result.message = (
                    "Które światło mam przyciemnić?"
                    if down
                    else "Które światło mam rozjaśnić?"
                )
                result.data = {
                    "user_goal": goal.to_dict(),
                    "needs_clarification": True,
                    "question_type": "light_target",
                    "ask_once": True,
                }
                return result
        try:
            from core.intent_family import IntentClassification, IntentFamily

            result.classification_override = IntentClassification(
                family=IntentFamily.SMART_HOME,
                is_complete=bool(entity or slots.get("resolved_context")),
                slots=dict(slots),
                raw_text=goal.planner_goal_text(),
            )
        except Exception:
            pass
        result.handled = False
        # Prefer Agent deterministic brightness over FastPath phrase parsers.
        result.skip_fast_path = True
        result.force_agent = True
        result.agent_text = goal.planner_goal_text()
        result.data = {"user_goal": goal.to_dict()}
        return result

    # Default: pass structured goal to Agent (FastPath only if still obvious).
    if goal.requires_action and goal.confidence >= 0.75 and soft_kind:
        result.skip_fast_path = soft_kind not in ("",)
        result.force_agent = True
    if goal.continuation and goal.requires_action:
        result.skip_fast_path = True
        result.force_agent = True
    result.handled = False
    result.data = {"user_goal": goal.to_dict(), "goal_context": ctx.to_dict()}
    return result


def attach_goal_to_context(context: Dict[str, Any], goal: Optional[UserGoal]) -> Dict[str, Any]:
    ctx = dict(context or {})
    if goal is None:
        return ctx
    ctx["user_goal"] = goal.to_dict()
    ctx["user_goal_objective"] = goal.objective
    ctx["user_goal_expected"] = dict(goal.expected_result or {})
    if goal.soft_kind:
        ctx["soft_kind"] = goal.soft_kind
    return ctx
