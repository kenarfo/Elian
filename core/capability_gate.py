# ==========================
# ELIAN — Capability honesty gate
# ==========================
"""Before planning/speaking: check ToolRegistry + CapabilityMap.

Never claim "nie umiem web_search" when the tool is available.
Never claim done without going through tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from core.capability_map import DISABLED, NOT_CONFIGURED, READY, UNAVAILABLE, snapshot_capabilities
from core.user_goal import FailureMode, UserGoal, honest_failure_reply

# Soft kind / family → tools that must be available for action.
_GOAL_TOOLS: Dict[str, List[str]] = {
    "volume_down": ["volume_down"],
    "volume_up": ["volume_up"],
    "brightness_up": ["set_device_value", "turn_on_device"],
    "brightness_down": ["set_device_value"],
    "light_on": ["turn_on_device"],
    "light_off": ["turn_off_device"],
    "media_calm": ["play_media"],
    "media_play": ["play_media"],
    "media_now": ["get_now_playing"],
    "web_search": ["web_search"],
    "open_result": ["open_website"],
    "replay_last": [],  # tool taken from slots
    "resume_previous": [],
    "gaming_setup": ["open_app", "procedure_run"],
    "cancel_goal": [],
}

_FAMILY_TOOLS: Dict[str, List[str]] = {
    "WEB": ["web_search"],
    "MEDIA": ["play_media"],
    "APP": ["open_app"],
    "SYSTEM": ["volume_up", "volume_down"],
    "SMART_HOME": ["turn_on_device", "turn_off_device", "set_device_value"],
}

_CAPABILITY_FOR_TOOL: Dict[str, str] = {
    "web_search": "Internet",
    "fetch_web_page": "Internet",
    "open_website": "Internet",
    "play_media": "LocalAI",  # media often local; Internet optional
    "browser_navigate": "Browser",
    "browser_click": "Browser",
}


@dataclass
class CapabilityDecision:
    allowed: bool = True
    failure_mode: str = ""
    message: str = ""
    required_tools: List[str] = field(default_factory=list)
    missing_tools: List[str] = field(default_factory=list)
    capability_status: Dict[str, str] = field(default_factory=dict)
    available_tools: Set[str] = field(default_factory=set)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "failure_mode": self.failure_mode,
            "message": self.message,
            "required_tools": list(self.required_tools),
            "missing_tools": list(self.missing_tools),
            "capability_status": dict(self.capability_status),
            "available_tools": sorted(self.available_tools),
        }


def _registry_tool_state(registry: Any) -> Dict[str, bool]:
    state: Dict[str, bool] = {}
    if registry is None:
        return state
    try:
        for item in registry.descriptions_for_planner() or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if not name:
                continue
            state[name] = bool(item.get("available", True))
    except Exception:
        pass
    # Also probe known tools via has()
    for name in (
        "web_search",
        "open_website",
        "play_media",
        "volume_up",
        "volume_down",
        "turn_on_device",
        "turn_off_device",
        "set_device_value",
        "open_app",
        "get_now_playing",
        "procedure_run",
    ):
        if name in state:
            continue
        try:
            if hasattr(registry, "has") and registry.has(name):
                tool = registry.get(name) if hasattr(registry, "get") else None
                state[name] = bool(getattr(tool, "available", True)) if tool else True
        except Exception:
            pass
    return state


def required_tools_for_goal(goal: UserGoal) -> List[str]:
    soft = str(goal.soft_kind or "")
    if soft == "replay_last":
        tool = str((goal.slots or {}).get("tool") or "")
        return [tool] if tool else []
    if soft in _GOAL_TOOLS:
        return list(_GOAL_TOOLS[soft])
    family = str(goal.intent_family or "").upper()
    if family in _FAMILY_TOOLS:
        return list(_FAMILY_TOOLS[family])
    return []


def capabilities_for_goal(
    goal: UserGoal,
    *,
    registry: Any = None,
) -> CapabilityDecision:
    """Honesty gate. Talk/answer goals always allowed (no tool claim)."""
    if not goal.requires_action:
        return CapabilityDecision(allowed=True)

    soft = str(goal.soft_kind or "")
    if soft in (
        "explain_failure",
        "unclear",
        "retry_pending",
        "cancel_goal",
        "resume_previous",
        "summarize_web",
        "ask_goal",
    ):
        return CapabilityDecision(allowed=True)

    caps = snapshot_capabilities()
    cap_status = {name: item.status for name, item in caps.items()}
    try:
        from core.capability_registry import get_capability_registry

        crec = get_capability_registry()
        for rec in crec.all().values():
            if rec is None:
                continue
            cap_status[rec.id] = rec.status.value
        # IntentFamily SYSTEM is not a Cap id — same record as COMPUTER_CONTROL.
        if "COMPUTER_CONTROL" in cap_status:
            cap_status["SYSTEM"] = cap_status["COMPUTER_CONTROL"]
    except Exception:
        pass
    tool_state = _registry_tool_state(registry)
    available = {name for name, ok in tool_state.items() if ok}

    required = required_tools_for_goal(goal)
    # Prefer any-of groups: brightness needs set_device_value OR turn_on
    missing: List[str] = []
    if soft in ("brightness_up", "brightness_down", "light_on", "light_off"):
        # Honesty: if HA write tools are registered and available, try them.
        # Global HA snapshot UNAVAILABLE must not block a dry/local tool path —
        # real failure still surfaces from the tool result.
        tools_ready = any(tool_state.get(t, t in available) for t in required) if required else False
        ha_rec = None
        try:
            from core.capability_registry import (
                HOME_ASSISTANT,
                CapStatus as _CapStatus,
                get_capability_registry,
            )

            ha_rec = get_capability_registry().get(HOME_ASSISTANT)
        except Exception:
            ha_rec = None
        if not tools_ready and ha_rec is not None and ha_rec.probed and not ha_rec.usable:
            mode = FailureMode.DISABLED.value
            if ha_rec.status not in (_CapStatus.DISABLED, _CapStatus.BLOCKED):
                mode = FailureMode.OFFLINE.value
            return CapabilityDecision(
                allowed=False,
                failure_mode=mode,
                message=ha_rec.spoken_failure(),
                required_tools=required,
                capability_status=cap_status,
                available_tools=available,
            )
        if required and not tools_ready:
            missing = [t for t in required if not tool_state.get(t, False)]
    elif soft == "web_search" or str(goal.intent_family or "").upper() == "WEB":
        # Honesty: if web_search is registered and available, NEVER claim can't search.
        if tool_state.get("web_search", False) or ("web_search" in available):
            return CapabilityDecision(
                allowed=True,
                required_tools=["web_search"],
                capability_status=cap_status,
                available_tools=available,
            )
        # CapRegistry WEB_SEARCH is provider truth; Phase-1 map Internet is network-only.
        try:
            from core.capability_registry import WEB_SEARCH, CapStatus, get_capability_registry

            crec = get_capability_registry()
            crec.refresh()
            web = crec.get(WEB_SEARCH)
            if web is not None:
                cap_status = dict(cap_status)
                cap_status["WEB_SEARCH"] = web.status.value
                if web.usable:
                    return CapabilityDecision(
                        allowed=True,
                        required_tools=["web_search"],
                        capability_status=cap_status,
                        available_tools=available,
                    )
                mode = FailureMode.OFFLINE.value
                if web.status == CapStatus.DISABLED:
                    mode = FailureMode.DISABLED.value
                elif web.status == CapStatus.BLOCKED:
                    mode = FailureMode.DISABLED.value
                return CapabilityDecision(
                    allowed=False,
                    failure_mode=mode,
                    message=web.spoken_failure(),
                    required_tools=["web_search"],
                    missing_tools=["web_search"] if not web.usable else [],
                    capability_status=cap_status,
                    available_tools=available,
                )
        except Exception:
            pass
        if "web_search" not in tool_state and "web_search" not in available:
            # Tool missing from registry entirely
            return CapabilityDecision(
                allowed=False,
                failure_mode=FailureMode.NO_CAPABILITY.value,
                message=honest_failure_reply(
                    FailureMode.NO_CAPABILITY.value,
                    detail="Nie mam narzędzia wyszukiwania w tej konfiguracji.",
                ),
                required_tools=["web_search"],
                capability_status=cap_status,
                available_tools=available,
            )
        if tool_state.get("web_search") is False:
            return CapabilityDecision(
                allowed=False,
                failure_mode=FailureMode.DISABLED.value,
                message=honest_failure_reply(
                    FailureMode.DISABLED.value,
                    detail="Wyszukiwanie w internecie jest teraz niedostępne.",
                ),
                required_tools=["web_search"],
                missing_tools=["web_search"],
                capability_status=cap_status,
                available_tools=available,
            )
    else:
        for tool in required:
            if tool and not tool_state.get(tool, tool in available):
                # If tool unknown to registry map, don't block — Planner may still work.
                if tool in tool_state and tool_state[tool] is False:
                    missing.append(tool)
                elif tool in tool_state and not tool_state[tool]:
                    missing.append(tool)

    if missing:
        return CapabilityDecision(
            allowed=False,
            failure_mode=FailureMode.DISABLED.value,
            message=honest_failure_reply(
                FailureMode.DISABLED.value,
                detail=f"Narzędzie {missing[0]} jest obecnie niedostępne.",
            ),
            required_tools=required,
            missing_tools=missing,
            capability_status=cap_status,
            available_tools=available,
        )

    return CapabilityDecision(
        allowed=True,
        required_tools=required,
        capability_status=cap_status,
        available_tools=available,
    )


def never_claim_missing_web_if_available(registry: Any, spoken: str) -> str:
    """Sanitize false 'can't search' claims when web_search is available."""
    text = str(spoken or "")
    if not text:
        return text
    state = _registry_tool_state(registry)
    if not state.get("web_search", False):
        return text
    lowered = text.lower()
    false_cant = (
        "nie mogę wyszukać",
        "nie umiem wyszukać",
        "nie mogę wyszukiwać",
        "nie umiem wyszukiwać",
        "nie mam dostępu do internetu",
        "nie mam dostępu do wyszukiwania",
        "nie potrafię szukać w internecie",
        "nie mogę przeszukać sieci",
        "nie mogę szukać w internecie",
        "nie wyszukuję w internecie",
    )
    if any(phrase in lowered for phrase in false_cant):
        return "Mogę to wyszukać — powiedz czego szukamy."
    return text
