# ==========================
# ELIAN — ContextSelector contract (Phase 13 Stage B)
# ==========================
"""Pick WorldState fragments. Do not dump the whole store into the prompt."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.active_service import get_active_service, service_history
from core.world_state import get_world_state

_SMALL_TALK = re.compile(
    r"^(?:"
    r"jak\s+si[eę]\s+masz|"
    r"jak\s+tam|"
    r"co\s+s[lł]ycha[cć]|"
    r"co\s+tam|"
    r"co\s+u\s+ciebie|"
    r"jak\s+leci|"
    r"co\s+u\s+nas|"
    r"cze[sś][cć](?:\s+elian)?|"
    r"hej(?:\s+elian)?|"
    r"siema|"
    r"dzie[nń]\s+dobry|"
    r"co\s+potrafisz|"
    r"no\s+cze[sś][cć]"
    r")[.!?…]*$",
    re.I,
)
_COMPUTER_REFERENT = re.compile(
    r"\b(?:to\s+okno|ten\s+przycisk|to\s+pole|wpisz|dopisz|zaznacz|cofnij|"
    r"drugie|poprzedni|kliknij\s+(?:to|ten|drug))\b",
    re.I,
)
_CLOSE_REFERENT = re.compile(
    r"\b(?:zamknij|wyłącz|wylacz)\s+(?:to|t[eę]|tamto|go|j[aą])\b",
    re.I,
)


@dataclass
class ContextSelection:
    include_service: bool = False
    include_device: bool = False
    include_browser: bool = False
    include_plan: bool = False
    include_computer: bool = False
    fragments: Dict[str, Any] = field(default_factory=dict)
    needs_clarification: bool = False
    clarification: str = ""
    excluded: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "include_service": self.include_service,
            "include_device": self.include_device,
            "include_browser": self.include_browser,
            "include_plan": self.include_plan,
            "include_computer": self.include_computer,
            "fragments": dict(self.fragments),
            "needs_clarification": self.needs_clarification,
            "clarification": self.clarification,
            "excluded": list(self.excluded),
        }


def looks_small_talk(text: str) -> bool:
    return bool(_SMALL_TALK.match(str(text or "").strip()))


def looks_close_referent(text: str) -> bool:
    return bool(_CLOSE_REFERENT.search(str(text or "")))


def _attach_world_referent(text: str, fragments: Dict[str, Any]) -> None:
    raw = str(text or "")
    low = raw.lower()
    if not (
        looks_close_referent(raw)
        or re.search(r"\b(?:ją|ja|nią|nia|go|niego|je|to|ten|tę|te|tą|ta|tamto)\b", raw, re.I)
        or "ciszej" in low
        or "głośniej" in low
        or "glosniej" in low
        or "zgaś" in low
        or "zgas" in low
    ):
        return
    try:
        from core.world_state import KNOWN, resolve_pronoun_referent, take_snapshot

        ref = resolve_pronoun_referent(text)
        # STALE / UNKNOWN must not populate driving active_referent fragments.
        if (
            ref is not None
            and not ref.ambiguous
            and str(getattr(ref, "status", "") or "") == KNOWN
        ):
            fragments["active_referent"] = ref.to_dict()
        raw = str(text or "").lower()
        if "ciszej" in raw or "głośniej" in raw or "glosniej" in raw:
            snap = take_snapshot()
            media = snap.fact("media")
            volume = snap.fact("volume")
            if media is not None and media.status == KNOWN:
                value = media.value if isinstance(media.value, dict) else {}
                fragments["media"] = {
                    "title": value.get("title") or value.get("query") or "",
                    "status": media.status,
                }
            if volume is not None and volume.status == KNOWN:
                value = volume.value if isinstance(volume.value, dict) else {}
                fragments["volume"] = {
                    "direction": value.get("direction") or "",
                    "status": volume.status,
                }
    except Exception:
        pass


def close_candidates_from_runtime() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    current = get_active_service()
    if current is not None and current.service_name:
        rows.append(
            {
                "name": current.service_name,
                "service_name": current.service_name,
                "resolved_kind": current.resolved_kind,
                "confidence": float(current.referent_confidence or 0.0),
            }
        )
    for item in service_history():
        name = str(item.get("service_name") or "")
        if not name:
            continue
        if any(str(row.get("name") or "").lower() == name.lower() for row in rows):
            continue
        rows.append(
            {
                "name": name,
                "service_name": name,
                "resolved_kind": item.get("resolved_kind") or "",
                "confidence": float(item.get("referent_confidence") or 0.0),
            }
        )
    return rows


def ambiguous_close_candidates(
    candidates: List[Dict[str, Any]] | None = None,
) -> Optional[ContextSelection]:
    rows = list(candidates or [])
    if len(rows) < 2:
        return None
    scored = sorted(
        rows,
        key=lambda item: float(item.get("confidence") or 0.0),
        reverse=True,
    )
    top = float(scored[0].get("confidence") or 0.0)
    second = float(scored[1].get("confidence") or 0.0)
    if top < 0.8 and abs(top - second) < 0.1:
        names = " czy ".join(
            str(item.get("name") or item.get("service_name") or "?")
            for item in scored[:2]
        )
        return ContextSelection(
            needs_clarification=True,
            clarification=f"Mam zamknąć {names}?",
            fragments={"candidates": scored[:2]},
        )
    return None


def select_context(
    text: str,
    *,
    world: Dict[str, Any] | None = None,
    close_candidates: List[Dict[str, Any]] | None = None,
    envelope: Any = None,
) -> ContextSelection:
    raw = str(text or "").strip()
    state = world if isinstance(world, dict) else get_world_state()
    mode = str(getattr(envelope, "mode", "") or "")
    continuation = bool(getattr(envelope, "continuation", False))
    context_requested = bool(getattr(envelope, "context_requested", False))
    needs_tools = bool(getattr(envelope, "needs_tools", False))
    # Context resolves referents. It does not invent intent for unrelated talk.
    if mode in ("TALK", "ANSWER", "WAIT") and not continuation and not context_requested:
        return ContextSelection(
            excluded=["service", "device", "browser", "execution_plan", "computer"],
        )
    if looks_small_talk(raw) and not (continuation and needs_tools):
        return ContextSelection(
            excluded=["service", "device", "browser", "execution_plan"],
        )
    followup_kind = str(getattr(envelope, "followup_kind", "") or "")
    if followup_kind in (
        "task_status",
        "return_to_task",
        "ask_why",
        "ask_alternative",
        "ask_recommendation",
        "pick_option",
        "approve_recommended",
    ):
        selected = ContextSelection(include_plan=True)
        if state.get("active_plan"):
            selected.fragments["active_plan"] = state.get("active_plan")
        selected.excluded = ["visual_referent"] if followup_kind != "pick_option" else []
        return selected
    if looks_close_referent(raw):
        ambiguous = ambiguous_close_candidates(close_candidates)
        if ambiguous is not None:
            return ambiguous
        service = get_active_service()
        fragments: Dict[str, Any] = {}
        if service is not None:
            fragments["active_service"] = service.to_dict()
        history = service_history()
        if history:
            fragments["recent_services"] = history[-3:]
        _attach_world_referent(raw, fragments)
        return ContextSelection(
            include_service=True,
            include_plan=True,
            include_device=bool(fragments.get("active_referent", {}).get("kind") == "device"),
            fragments=fragments,
        )
    selected = ContextSelection()
    conversation = dict(state.get("conversation") or {})
    if conversation.get("active_service"):
        selected.include_service = True
        selected.fragments["active_service"] = conversation.get("active_service")
    if state.get("smart_home"):
        selected.include_device = True
        selected.fragments["smart_home"] = state.get("smart_home")
    if state.get("active_plan"):
        selected.include_plan = True
        selected.fragments["active_plan"] = state.get("active_plan")
    computer = dict(state.get("computer") or {})
    screen = dict(state.get("screen") or {})
    if (computer or screen) and (
        _COMPUTER_REFERENT.search(raw)
        or looks_close_referent(raw)
        or "okno" in raw.lower()
        or "ekran" in raw.lower()
    ):
        selected.include_computer = True
        selected.fragments["computer"] = {
            "active_window": (computer.get("active_window") or {}).get("active_window")
            or computer.get("active_window"),
            "snapshot_id": computer.get("snapshot_id") or "",
        }
        if screen:
            selected.fragments["screen"] = {
                "active_window": screen.get("active_window"),
                "frame_id": screen.get("frame_id"),
                "visual_referent": screen.get("visual_referent"),
            }
    _attach_world_referent(raw, selected.fragments)
    if selected.fragments.get("active_referent", {}).get("kind") == "device":
        selected.include_device = True
    return selected


def should_enrich_goal(text: str, envelope: Any = None) -> bool:
    mode = str(getattr(envelope, "mode", "") or "")
    if mode in ("TALK", "ANSWER", "WAIT") and not bool(getattr(envelope, "continuation", False)):
        return False
    if bool(getattr(envelope, "needs_tools", False)) and bool(
        getattr(envelope, "context_requested", False) or getattr(envelope, "continuation", False)
    ):
        return True
    return not looks_small_talk(text)
