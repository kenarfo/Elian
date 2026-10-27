# ==========================
# ELIAN — pending executable goal / retry continuation (Phase 13 Stage A)
# ==========================
"""Retry only failed_steps. Successful steps stay skip-locked on that plan_id."""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from core.action_claim import HONEST_NO_TOOL

PENDING_TTL_SEC = 15 * 60

_RETRY_HINT = re.compile(
    r"\b(?:"
    r"spr[oó]buj|pr[oó]buj|pon[oó]w|"
    r"jeszcze\s+raz|"
    r"to\s+spr[oó]buj|no\s+to\s+spr[oó]buj|"
    r"tak\s*,?\s*spr[oó]buj|"
    r"no\s+dobra(?:\s*,?\s*spr[oó]buj)?"
    r")\b",
    re.I,
)
_RETRY_WEAK = re.compile(
    r"^(?:tak|no|teraz|dobra|no\s+dobra)[.!…]*$",
    re.I,
)
_CANCEL_PENDING = re.compile(
    r"^(?:"
    r"nieważne|niewazne|anuluj(?:\s+to)?|"
    r"odpuść|odpusc|nie\s+pr[oó]buj|"
    r"nie\s+teraz"
    r")[.!…]*$",
    re.I,
)
_RESUME_HINT = re.compile(
    r"^(?:"
    r"zr[oó]b\s+to\s+teraz|"
    r"zr[oó]b\s+to|"
    r"kontynuuj(?:\s+to)?|"
    r"doko[nń]cz(?:\s+to)?|"
    r"wr[oó][cć]\s+do\s+tego|"
    r"no\s+to\s+zr[oó]b"
    r")[.!…]*$",
    re.I,
)
_ALTERNATE_HINT = re.compile(
    r"\b(?:"
    r"spr[oó]buj\s+inaczej|"
    r"(?:ale\s+)?inaczej|"
    r"zr[oó]b\s+(?:to\s+)?inaczej|"
    r"inn[aą]\s+drog[aą]|"
    r"innym\s+sposobem|"
    r"nie\s+tak\s+samo"
    r")\b",
    re.I,
)
_INCOMPLETE_HINT = re.compile(
    r"\b(?:"
    r"jeszcze\s+tego\s+nie\s+zrobi[łl]e[sś]|"
    r"tego\s+nie\s+zrobi[łl]e[sś]|"
    r"nie\s+zrobi[łl]e[sś]\s+tego|"
    r"brakuje\s+jeszcze|"
    r"co\s+z\s+reszt[aą]|"
    r"a\s+reszta"
    r")\b",
    re.I,
)

_PENDING_STORE: Dict[str, Any] = {"goal": None, "created": 0.0}


def _retry_core(text: str) -> str:
    raw = str(text or "").strip()
    raw = raw.split("[kontekst:", 1)[0].split("\n(", 1)[0].strip()
    return raw


_RETRY_PREFIX = re.compile(
    r"^(?:no\s+to\s+|to\s+|tak,?\s+|no\s+dobra,?\s+)*"
    r"(?:spr[oó]buj|pr[oó]buj|pon[oó]w|jeszcze\s+raz|teraz)\s*[,.!]?\s*",
    re.I,
)


def _strip_retry_prefix(raw: str) -> str:
    return _RETRY_PREFIX.sub("", raw, count=1).strip(" .,!?")


def looks_retry_utterance(text: str, *, allow_weak: bool = False) -> bool:
    raw = _retry_core(text)
    if not raw:
        return False
    remainder = _strip_retry_prefix(raw)
    if remainder and remainder.casefold() != raw.casefold():
        try:
            from core.intent_family import IntentFamily, classify_intent_family

            info = classify_intent_family(remainder)
            if info.is_complete and info.family not in (
                IntentFamily.CONVERSATION_CONTROL,
                IntentFamily.CORRECTION,
            ):
                return False
        except Exception:
            pass
        if len(remainder.split()) >= 3:
            return False
    if _RETRY_HINT.search(raw) and len(raw.split()) <= 8:
        return True
    if allow_weak and _RETRY_WEAK.match(raw):
        return True
    return False


def looks_cancel_pending(text: str) -> bool:
    return bool(_CANCEL_PENDING.match(_retry_core(text)))


def looks_resume_utterance(text: str) -> bool:
    raw = _retry_core(text)
    if not raw:
        return False
    if looks_retry_utterance(raw) or looks_alternate_utterance(raw):
        return False
    return bool(_RESUME_HINT.match(raw)) or bool(
        re.match(r"^(?:kontynuuj|doko[nń]cz)[.!…]*$", raw, re.I)
    )


def looks_alternate_utterance(text: str) -> bool:
    raw = _retry_core(text)
    if not raw:
        return False
    if _ALTERNATE_HINT.search(raw) and len(raw.split()) <= 10:
        return True
    return False


def looks_incomplete_reminder(text: str) -> bool:
    raw = _retry_core(text)
    if not raw:
        return False
    return bool(_INCOMPLETE_HINT.search(raw)) and len(raw.split()) <= 12



def goal_text_from_pending(goal: Optional[Dict[str, Any]]) -> str:
    """Resolve executable goal text from pending aliases (no phrase hardcoding)."""
    if not isinstance(goal, dict):
        return ""
    for key in ("original_goal", "original_user_text", "goal"):
        text = str(goal.get(key) or "").strip()
        if text:
            return text
    return ""


def normalize_pending_goal_fields(goal: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Ensure original_goal / original_user_text / goal stay aligned when any is set."""
    if not isinstance(goal, dict):
        return goal
    out = dict(goal)
    text = goal_text_from_pending(out)
    if text:
        out.setdefault("original_goal", text)
        out.setdefault("original_user_text", text)
        out.setdefault("goal", text)
        if not str(out.get("normalized_goal") or "").strip():
            out["normalized_goal"] = text.lower()
    return out


def status_summary_for_pending(goal: Optional[Dict[str, Any]]) -> str:
    """Short Polish status when user says the goal is incomplete."""
    if not isinstance(goal, dict):
        return "Nie mam aktywnego celu."
    steps = list(goal.get("steps") or [])
    # Pending executable goals also store failed_steps / successful_steps.
    if not steps:
        steps = list(goal.get("failed_steps") or []) + list(
            goal.get("successful_steps") or []
        )
    failed = [
        item for item in steps if isinstance(item, dict) and str(item.get("status") or "") == "FAILED"
    ]
    if not failed:
        failed = [
            item
            for item in list(goal.get("failed_steps") or [])
            if isinstance(item, dict)
        ]
    ok = [
        item
        for item in steps
        if isinstance(item, dict) and str(item.get("status") or "") in ("SUCCEEDED", "SKIPPED")
    ]
    if not ok:
        ok = [
            item
            for item in list(goal.get("successful_steps") or [])
            if isinstance(item, dict)
        ]
    remaining = failed or [
        item
        for item in steps
        if isinstance(item, dict)
        and str(item.get("status") or "") not in ("SUCCEEDED", "SKIPPED")
    ]
    goal_text = goal_text_from_pending(goal) or "ten cel"
    if len(goal_text) > 60:
        goal_text = goal_text[:57] + "…"
    if ok and remaining:
        labels = failed_step_labels(remaining) or ["dokończyć resztę"]
        return (
            f"Cel: {goal_text}. Zrobione: {len(ok)}. "
            f"Zostało: {', '.join(labels)}. Mogę dokończyć teraz."
        )
    if remaining:
        labels = failed_step_labels(remaining) or ["dokończyć"]
        return f"Cel: {goal_text}. Jeszcze nie: {', '.join(labels)}. Mogę spróbować teraz."
    return f"Cel: {goal_text}. Wygląda na domknięty."


def persist_pending(goal: Dict[str, Any] | None) -> None:
    if not goal:
        _PENDING_STORE["goal"] = None
        _PENDING_STORE["created"] = 0.0
        return
    _PENDING_STORE["goal"] = normalize_pending_goal_fields(dict(goal)) or dict(goal)
    _PENDING_STORE["created"] = time.monotonic()


def load_persisted_pending() -> Optional[Dict[str, Any]]:
    goal = _PENDING_STORE.get("goal")
    created = float(_PENDING_STORE.get("created") or 0.0)
    if not isinstance(goal, dict):
        return None
    if created <= 0 or (time.monotonic() - created) > PENDING_TTL_SEC:
        persist_pending(None)
        return None
    return dict(goal)


def clear_persisted_pending() -> None:
    persist_pending(None)


def failed_step_labels(steps: List[Dict[str, Any]] | None) -> List[str]:
    labels: List[str] = []
    for item in steps or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "") != "FAILED":
            continue
        action = str(item.get("semantic_action") or "")
        args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        target = str(
            args.get("name")
            or item.get("target")
            or ""
        ).strip() or "tego"
        if action == "close_service" or str(item.get("tool") or "") == "close_app":
            labels.append(f"zamknąć {target}")
        elif action == "open_service" or str(item.get("tool") or "") == "open_app":
            labels.append(f"otworzyć {target}")
        elif str(item.get("tool") or "") == "volume_down" or (
            action == "adjust_volume"
            and str(item.get("direction") or args.get("direction") or "") == "decrease"
        ):
            labels.append("zmniejszyć głośności")
        elif str(item.get("tool") or "") == "volume_up" or action == "adjust_volume":
            labels.append("zwiększyć głośności")
        else:
            labels.append("wykonać ten krok")
    return labels


def honest_failed_message(
    steps: List[Dict[str, Any]] | None = None,
    *,
    offer_retry: bool = True,
) -> str:
    labels = failed_step_labels(steps)
    succeeded = [
        item for item in (steps or [])
        if isinstance(item, dict) and str(item.get("status") or "") == "SUCCEEDED"
    ]
    if not labels:
        return HONEST_NO_TOOL
    retry = " Mogę spróbować jeszcze raz." if offer_retry else ""
    if len(labels) == 1 and not succeeded:
        return f"Nie udało mi się {labels[0]}.{retry}"
    if len(labels) == 1 and succeeded:
        extra = ""
        for item in succeeded:
            if str(item.get("tool") or "") in ("volume_down", "volume_up") or str(
                item.get("semantic_action") or ""
            ) == "adjust_volume":
                extra = " ale głośność zmieniłem."
                if str(item.get("tool") or "") == "volume_down" or str(
                    item.get("direction") or ""
                ) == "decrease":
                    extra = " ale głośność zmniejszyłem."
                elif str(item.get("tool") or "") == "volume_up":
                    extra = " ale głośność zwiększyłem."
                break
            if str(item.get("tool") or "") == "close_app":
                extra = f" ale {item.get('target') or 'aplikację'} zamknąłem."
                break
        specific = ""
        if offer_retry:
            specific = f" Mogę spróbować {labels[0]} jeszcze raz."
        return f"Nie udało mi się {labels[0]},{extra.rstrip('.')}{specific or retry}"
    joined = ", ".join(labels)
    return f"Nie udało mi się: {joined}.{retry}"


def pending_from_plan(
    *,
    plan_id: str,
    original_goal: str,
    status: str,
    steps: List[Dict[str, Any]],
    last_failure_reason: str = "",
    retry_count: int = 0,
) -> Optional[Dict[str, Any]]:
    failed = [item for item in steps if str(item.get("status") or "") == "FAILED"]
    succeeded = [item for item in steps if str(item.get("status") or "") == "SUCCEEDED"]
    if not failed:
        return None
    now = time.monotonic()
    return {
        "plan_id": plan_id,
        "original_goal": original_goal,
        "original_user_text": original_goal,
        "normalized_goal": str(original_goal or "").strip().lower(),
        "status": status,
        "semantic_steps": list(steps),
        "failed_steps": list(failed),
        "successful_steps": list(succeeded),
        "successful_step_ids": [str(item.get("step_id") or "") for item in succeeded],
        "failed_step_ids": [str(item.get("step_id") or "") for item in failed],
        "successful_idempotency_keys": [
            str(item.get("idempotency_key") or "")
            for item in succeeded
            if item.get("idempotency_key")
        ],
        "steps": list(steps),
        "created_at": now,
        "retry_count": int(retry_count or 0),
        "last_failure_reason": last_failure_reason or "step_failed",
    }


def pending_from_unexecuted_goal(text: str, *, reason: str = "honest_guard") -> Optional[Dict[str, Any]]:
    """Build a pending plan when the agent offered retry without executing."""
    from core.compound_plan import parse_compound_goal
    from core.intent_family import IntentFamily, classify_intent_family
    from core.plan_state import (
        STEP_FAILED,
        make_idempotency_key,
        make_step_id,
        new_plan_id,
    )

    raw = str(text or "").strip()
    if not raw:
        return None
    plan = parse_compound_goal(raw)
    plan_id = new_plan_id()
    rows: List[Dict[str, Any]] = []
    if plan.detected and plan.complete:
        for index, step in enumerate(plan.steps, start=1):
            step_id = make_step_id(plan_id, index)
            target = str(step.slots.get("target") or step.slots.get("app_name") or "").strip()
            direction = str(step.slots.get("direction") or "")
            tool = ""
            if step.semantic_action == "close_service":
                tool = "close_app"
            elif step.semantic_action == "open_service":
                tool = "open_app"
            elif step.semantic_action == "adjust_volume":
                tool = "volume_down" if direction == "decrease" else "volume_up"
            key = make_idempotency_key(
                plan_id, step_id, step.semantic_action, target, direction
            )
            rows.append(
                {
                    "step": index,
                    "plan_id": plan_id,
                    "step_id": step_id,
                    "attempt_id": 0,
                    "idempotency_key": key,
                    "semantic_action": step.semantic_action,
                    "target": target,
                    "direction": direction,
                    "normalized_value": direction,
                    "status": STEP_FAILED,
                    "tool": tool,
                    "arguments": {"name": target} if target else {},
                }
            )
    else:
        info = classify_intent_family(raw)
        if info.family == IntentFamily.APP and str(
            (info.slots or {}).get("semantic_action") or ""
        ) in ("close_app", "close_service"):
            target = str((info.slots or {}).get("app_name") or "").strip()
            step_id = make_step_id(plan_id, 1)
            rows.append(
                {
                    "step": 1,
                    "plan_id": plan_id,
                    "step_id": step_id,
                    "attempt_id": 0,
                    "idempotency_key": make_idempotency_key(
                        plan_id, step_id, "close_service", target, ""
                    ),
                    "semantic_action": "close_service",
                    "target": target,
                    "status": STEP_FAILED,
                    "tool": "close_app",
                    "arguments": {"name": target},
                }
            )
    if not rows:
        return None
    pending = pending_from_plan(
        plan_id=plan_id,
        original_goal=raw,
        status="FAILED",
        steps=rows,
        last_failure_reason=reason,
    )
    return pending
