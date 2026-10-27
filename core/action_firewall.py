# ==========================
# ELIAN — Intent / Action firewall (C.5.5)
# ==========================
"""FastPath still needs permission. Semantics stay the authority."""

from __future__ import annotations

from typing import Any, Optional, Tuple

from Diagnostics import log_turn


HA_MUTATION_TOOLS = frozenset(
    {
        "turn_on_device",
        "turn_off_device",
        "set_device_value",
        "activate_scene",
    }
)

MUTATION_TOOLS = frozenset(
    {
        *HA_MUTATION_TOOLS,
        "open_app",
        "close_app",
        "play_media",
        "pause_media",
        "resume_media",
        "next_track",
        "previous_track",
        "type_text",
        "click_ui_element",
        "hotkey",
        "press_key",
        "close_window",
        "volume_up",
        "volume_down",
        "create_reminder",
        "create_calendar_event",
        "open_website",
        "procedure_run",
        "procedure_save",
        "procedure_delete",
        "browser_click",
        "browser_type",
        "browser_select",
        "browser_check",
        "browser_open_url",
        "browser_search_service",
        "browser_select_result",
        "browser_play_media",
        "close_browser_tab",
        "undo_last_action",
    }
)

_NO_MUTATION_INTENTS = frozenset(
    {
        "creative_humor",
        "social_smalltalk",
        "action_discussion",
        "knowledge_explanation",
        "smart_home_status",
        "situation_briefing",
        "home_overview",
        "today_schedule",
        "list_reminders",
        "weather_status",
        "missed_digest",
        "home_arrival",
        "list_notifications",
        "phone_status",
        "general_conversation",
        "conversational_followup",
        "unclear_utterance",
        "current_screen_observation",
        "say_text",
    }
)

_ACT_MODES = frozenset({"ACT", "PLAN"})


def allow_tool(
    envelope: Any,
    tool_name: str,
    text: str = "",
) -> Tuple[bool, str]:
    """Return (allowed, reason). Never logs chain-of-thought."""
    name = str(tool_name or "")
    mode = str(getattr(envelope, "mode", "") or "")
    intent = str(getattr(envelope, "intent", "") or "")
    domains = [str(d) for d in (getattr(envelope, "allowed_domains", None) or [])]
    needs_tools = bool(getattr(envelope, "needs_tools", False))
    confidence = float(getattr(envelope, "confidence", 0.0) or 0.0)

    try:
        from core.semantic_executive import (
            looks_creative_humor,
            looks_discussion_not_action,
            looks_garbled_stt,
            looks_hypothetical,
            looks_quoted_command,
        )

        if looks_garbled_stt(text) and name in MUTATION_TOOLS:
            return False, "garbled_unknown"
        if looks_creative_humor(text) or looks_discussion_not_action(text):
            if name in MUTATION_TOOLS:
                return False, "conversational_semantics"
        if looks_hypothetical(text) and name in MUTATION_TOOLS:
            return False, "hypothetical"
        if getattr(envelope, "hypothetical", False) and name in MUTATION_TOOLS:
            return False, "hypothetical"
        if looks_quoted_command(text) and name in MUTATION_TOOLS:
            return False, "quoted_command"
        if getattr(envelope, "quoted", False) and name in MUTATION_TOOLS:
            return False, "quoted_command"
        if re_negation(text) and name in MUTATION_TOOLS:
            return False, "negated"
    except Exception:
        pass

    if intent in _NO_MUTATION_INTENTS and name in MUTATION_TOOLS:
        return False, "non_mutation_intent"
    if mode in ("TALK", "WAIT", "ASK", "SIMULATE", "CANCEL", "ANSWER") and name in MUTATION_TOOLS:
        return False, "non_act_mode"
    if mode == "OBSERVE" and name in MUTATION_TOOLS:
        return False, "observe_read_only"
    if not needs_tools and name in MUTATION_TOOLS:
        return False, "needs_tools_false"
    if confidence < 0.75 and name in MUTATION_TOOLS:
        return False, "low_confidence"

    if name == "undo_last_action":
        if mode in ("TALK", "WAIT", "ASK", "SIMULATE", "CANCEL", "ANSWER"):
            return False, "non_act_mode"
        return True, "explicit_undo"

    if name in HA_MUTATION_TOOLS:
        if "SMART_HOME" not in domains and mode in _ACT_MODES:
            if intent not in ("smart_home_control", "smart_home", "procedure_run"):
                return False, "domain_not_smart_home"
        if mode not in _ACT_MODES and intent != "smart_home_control":
            return False, "mode_not_act"
        return True, "explicit_current_utterance_action"

    if name == "get_device_state":
        if mode in ("TALK", "WAIT"):
            return False, "non_read_mode"
        return True, "read_only_state"

    if name == "get_situation_briefing":
        if mode in ("TALK", "WAIT"):
            return False, "non_read_mode"
        return True, "read_only_briefing"

    if name in (
        "get_home_summary",
        "list_devices",
        "list_reminders",
        "get_next_event",
        "get_today_schedule",
        "get_calendar_events",
        "get_now_playing",
        "get_volume",
        "get_weather",
        "ask_about_screen",
        "list_os_notifications",
        "get_phone_status",
    ):
        if mode in ("TALK", "WAIT"):
            return False, "non_read_mode"
        return True, "read_only_state"

    if mode in ("TALK", "ANSWER") and not needs_tools:
        return False, "talk_no_tools"
    return True, "explicit_current_utterance_action"


def allow_side_effect(envelope: Any, tool_name: str, text: str = "") -> bool:
    """Single mutation gate for FastPath and Planner."""
    allowed, _reason = allow_tool(envelope, tool_name, text)
    return bool(allowed)


def re_negation(text: str) -> bool:
    import re

    return bool(
        re.search(
            r"\bnie\s+(?:w[lł][aą]czaj|wy[lł][aą]czaj|otwieraj|zamykaj|wpisuj|pu[sś][cć])",
            str(text or ""),
            re.I,
        )
    )


def log_firewall(
    tool_name: str,
    allowed: bool,
    reason: str,
    *,
    base_intent: str = "",
) -> None:
    flag = 1 if allowed else 0
    print(
        f"[INTENT FIREWALL] base_intent={base_intent or '-'} "
        f"candidate_tool={tool_name} allowed={flag} reason={reason}"
    )
    log_turn(
        "intent_firewall",
        base_intent=base_intent or "",
        tool=tool_name,
        allowed=flag,
        reason=reason,
    )
