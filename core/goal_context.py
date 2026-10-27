# ==========================
# ELIAN — GoalContext (multi-turn situation)
# ==========================
"""Compact turn context for pronouns / continuations / repair.

Reuses WorldState, ActionJournal, pending goals, focus — does not invent intent.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class GoalContext:
    previous_goal: Dict[str, Any] = field(default_factory=dict)
    previous_plan: Dict[str, Any] = field(default_factory=dict)
    previous_tool: str = ""
    previous_result: Dict[str, Any] = field(default_factory=dict)
    previous_verification: Dict[str, Any] = field(default_factory=dict)
    active_entities: Dict[str, Any] = field(default_factory=dict)
    unresolved_task: Dict[str, Any] = field(default_factory=dict)
    last_journal_action: Dict[str, Any] = field(default_factory=dict)
    last_web_results: List[Dict[str, Any]] = field(default_factory=list)
    last_web_query: str = ""
    last_assistant: str = ""
    reference_tokens: List[str] = field(default_factory=list)
    is_continuation: bool = False
    pending_executable: Dict[str, Any] = field(default_factory=dict)
    media_active: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _last_web_from_observations(observations: List[Dict[str, Any]] | None) -> tuple[List[Dict[str, Any]], str]:
    for item in reversed(list(observations or [])):
        if not isinstance(item, dict) or not item.get("success"):
            continue
        if str(item.get("tool") or "").lower() != "web_search":
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        results = list(data.get("ranked_results") or data.get("results") or [])
        query = str(data.get("query") or "")
        cleaned: List[Dict[str, Any]] = []
        for row in results:
            if isinstance(row, dict):
                cleaned.append(dict(row))
            elif hasattr(row, "to_dict"):
                try:
                    cleaned.append(dict(row.to_dict()))
                except Exception:
                    pass
        return cleaned, query
    return [], ""


def build_goal_context(
    *,
    memory: Any = None,
    pending_executable: Optional[Dict[str, Any]] = None,
    last_observations: Optional[List[Dict[str, Any]]] = None,
    last_assistant: str = "",
    last_user_goal: Optional[Dict[str, Any]] = None,
    envelope: Any = None,
) -> GoalContext:
    active: Dict[str, Any] = {}
    device = None
    try:
        from core.device_context import get_active_device, get_last_device_operation

        if memory is not None:
            device = get_active_device(memory)
            if device is not None and getattr(device, "is_valid", False):
                active["device_id"] = str(getattr(device, "entity_id", "") or "")
                active["entity_id"] = active["device_id"]
                active["device_name"] = str(getattr(device, "name", "") or "")
                active["domain"] = str(getattr(device, "domain", "") or "")
            last_op = get_last_device_operation(memory)
            if isinstance(last_op, dict) and last_op:
                active["last_device_op"] = dict(last_op)
    except Exception:
        pass

    try:
        from core.active_service import get_active_service

        service = get_active_service()
        if service is not None:
            active["service"] = str(getattr(service, "service_name", "") or "")
            active["service_kind"] = str(getattr(service, "resolved_kind", "") or "")
            active["app"] = active.get("app") or active["service"]
    except Exception:
        pass

    media_active = False
    try:
        from core import world_state as ws

        snap = None
        for name in ("compact_planner_view", "planner_snapshot", "snapshot"):
            fn = getattr(ws, name, None)
            if callable(fn):
                snap = fn()
                if isinstance(snap, dict) and snap:
                    break
        if isinstance(snap, dict):
            media = snap.get("media") if isinstance(snap.get("media"), dict) else {}
            if media.get("playing") or media.get("title") or media.get("artist"):
                media_active = True
                active["media_title"] = str(media.get("title") or "")
                active["media_artist"] = str(media.get("artist") or "")
            computer = snap.get("computer") if isinstance(snap.get("computer"), dict) else {}
            if computer.get("active_window"):
                active["app"] = active.get("app") or str(computer.get("active_window") or "")
    except Exception:
        pass

    focus: Dict[str, Any] = {}
    if memory is not None:
        try:
            focus = dict(getattr(getattr(memory, "short", None), "focus", {}) or {})
        except Exception:
            focus = {}
    if focus.get("current_topic"):
        active["topic"] = str(focus.get("current_topic") or "")
    if focus.get("last_web_query"):
        active["web_query"] = str(focus.get("last_web_query") or "")

    journal: Dict[str, Any] = {}
    try:
        from core.action_journal import last_action

        row = last_action()
        if isinstance(row, dict):
            journal = dict(row)
    except Exception:
        pass

    web_results, web_query = _last_web_from_observations(last_observations)
    if not web_query:
        web_query = str(focus.get("last_web_query") or "")

    previous_tool = ""
    previous_result: Dict[str, Any] = {}
    previous_verification: Dict[str, Any] = {}
    if last_observations:
        last = last_observations[-1] if isinstance(last_observations[-1], dict) else {}
        previous_tool = str(last.get("tool") or "")
        previous_result = dict(last)
        ver = last.get("verification") if isinstance(last.get("verification"), dict) else {}
        previous_verification = dict(ver)

    pending = dict(pending_executable or {})
    unresolved: Dict[str, Any] = {}
    if pending:
        unresolved = {
            "kind": "pending_executable",
            "plan_id": str(pending.get("plan_id") or ""),
            "goal": str(pending.get("goal") or pending.get("user_goal") or ""),
            "failed_steps": list(pending.get("failed_steps") or []),
        }

    refs: List[str] = []
    if envelope is not None:
        for item in list(getattr(envelope, "referents", None) or []):
            token = str(item or "").strip()
            if token:
                refs.append(token)
    for key in ("device_name", "app", "service", "topic", "media_title"):
        val = str(active.get(key) or "").strip()
        if val and val not in refs:
            refs.append(val)

    continuation = bool(getattr(envelope, "continuation", False)) if envelope else False
    if pending or refs or journal:
        # Soft signal — soft_goals / formulate decide if utterance is continuation.
        pass

    previous_plan: Dict[str, Any] = {}
    if pending:
        previous_plan = {
            "plan_id": pending.get("plan_id"),
            "status": pending.get("status") or "PENDING",
            "failed_steps": list(pending.get("failed_steps") or []),
            "successful_steps": list(pending.get("successful_steps") or []),
        }

    return GoalContext(
        previous_goal=dict(last_user_goal or {}),
        previous_plan=previous_plan,
        previous_tool=previous_tool,
        previous_result=previous_result,
        previous_verification=previous_verification,
        active_entities=active,
        unresolved_task=unresolved,
        last_journal_action=journal,
        last_web_results=web_results,
        last_web_query=web_query,
        last_assistant=str(last_assistant or ""),
        reference_tokens=refs,
        is_continuation=continuation,
        pending_executable=pending,
        media_active=media_active,
    )
