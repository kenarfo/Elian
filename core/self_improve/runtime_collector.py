# ==========================
# ELIAN — Runtime Experience Collector (Phase 1)
# ==========================
"""Fail-open turn telemetry → ExperienceLogStore.

Collects one sanitized ExperienceEntry per ConversationManager turn.
Never stores raw utterances, tool argument values, or secrets.
Must never raise into the conversation path.
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import threading
from contextvars import ContextVar
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.self_improve.experience_log import (
    PROVENANCE_RUNTIME,
    ExperienceEntry,
    ExperienceLogStore,
    FailureCategory,
    get_store,
    normalize_repeat_count,
)

# --- tunables (overridable via config) ---------------------------------------

DEFAULT_MAX_ENTRIES = 2000
DEFAULT_SUCCESS_SAMPLE_RATE = 0.10  # ~10% of eligible SUCCESS turns
DEDUP_WINDOW = 8  # look back N physical rows for equivalent runtime failures

# Injectable per-draw success sampler for tests (None → random.random).
SuccessSampler = Callable[[], float]
_success_sampler: Optional[SuccessSampler] = None

_SECRET_KEY_PARTS = (
    "token",
    "password",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "bearer",
)

_NOISE_SOURCES = frozenset(
    {
        "wake",
        "wake_ack",
        "ack",
        "wait",
        "ambient_continue",
        "ambient_bleed",
        "noise_unknown",
        "empty",
        "empty_after_prep",
        "idle_timeout",
        "standby",
        "forgot",
        "speech_guard",
        "companion_empty",
        "no_response",
        "collab",
        "meta",
    }
)

_ACTION_SOURCES = frozenset(
    {
        "fast_path",
        "agent",
        "action_loop",
        "planner",
        "confirm",
        "procedure",
        "skill",
        "command",
        "intent",
        "chat",
        "clarify",
        "correction",
        "why_failure",
        "teach",
        "memory",
        "proactive_pref",
    }
)

# Experience-domain vocabulary aligned with seed log + IntentFamily (lowercase).
# Single source of truth for runtime domain attribution — do not scatter copies.
_CANONICAL_DOMAINS = frozenset(
    {
        "scheduling",
        "calendar",
        "web",
        "smart_home",
        "media",
        "app",
        "computer",
        "vision",
        "knowledge",
        "messaging",
        "system",
        "self_improve",
    }
)

# DecisionEnvelope.allowed_domains / IntentFamily labels → experience domain.
_DOMAIN_ALIASES = {
    "smart_home": "smart_home",
    "smarthome": "smart_home",
    "ha": "smart_home",
    "home_assistant": "smart_home",
    "media": "media",
    "app": "app",
    "apps": "app",
    "windows_apps": "app",
    "system": "system",
    "computer": "computer",
    "computer_control": "computer",
    "scheduling": "scheduling",
    "schedule": "scheduling",
    "reminder": "scheduling",
    "reminders": "scheduling",
    "calendar": "calendar",
    "web": "web",
    "research": "web",
    "internet": "web",
    "knowledge": "knowledge",
    "vision": "vision",
    "read_only_screen": "vision",
    "messaging": "messaging",
    "climate": "smart_home",
    "preference": "knowledge",
    "procedure": "system",
}

# Vague envelope domains — defer to tool/intent instead of winning early.
_WEAK_ALLOWED_DOMAINS = frozenset({"system", "unknown", ""})

# Exact ToolRegistry names → experience domain (capability involved).
TOOL_DOMAINS: Dict[str, str] = {
    # messaging / contacts
    "find_contact": "messaging",
    "send_message": "messaging",
    # apps
    "open_app": "app",
    "close_app": "app",
    "check_app_installed": "app",
    "focus_or_open": "app",
    "focus_window": "app",
    # media
    "play_media": "media",
    "pause_media": "media",
    "resume_media": "media",
    "browser_play_media": "media",
    # smart home
    "turn_on_device": "smart_home",
    "turn_off_device": "smart_home",
    "set_device_value": "smart_home",
    "get_device_state": "smart_home",
    "list_devices": "smart_home",
    # web
    "web_search": "web",
    "fetch_web_page": "web",
    "open_website": "web",
    # scheduling
    "create_reminder": "scheduling",
    "list_reminders": "scheduling",
    "cancel_reminder": "scheduling",
    "update_reminder": "scheduling",
    "get_today_schedule": "scheduling",
    "recurring_schedule": "scheduling",
    # system volume / status
    "volume_up": "system",
    "volume_down": "system",
    "get_volume": "system",
    "get_system_status": "system",
    "list_os_notifications": "system",
    "get_phone_status": "system",
    "get_situation_briefing": "system",
    "undo_last_action": "system",
    # vision
    "describe_screen": "vision",
    "ask_about_screen": "vision",
    "get_screen": "vision",
    "find_ui_element": "vision",
    # computer control (UIA)
    "type_text": "computer",
    "click_ui_element": "computer",
    "hotkey": "computer",
    "press_key": "computer",
    "inspect_ui": "computer",
    "list_windows": "computer",
    "get_active_window": "computer",
    # memory / prefs → knowledge-adjacent
    "remember_fact": "knowledge",
    "set_preference": "knowledge",
    "get_preference": "knowledge",
    "get_memory": "knowledge",
    "forget_fact": "knowledge",
    "get_weather": "knowledge",
}

# Exact Semantic Executive intent → experience domain.
INTENT_DOMAINS: Dict[str, str] = {
    "smart_home_control": "smart_home",
    "smart_home_status": "smart_home",
    "home_overview": "smart_home",
    "home_arrival": "smart_home",
    "media_playback": "media",
    "open_app": "app",
    "system_volume": "system",
    "knowledge_explanation": "knowledge",
    "weather_status": "knowledge",
    "today_schedule": "scheduling",
    "list_reminders": "scheduling",
    "current_screen_observation": "vision",
    "phone_status": "system",
    "list_notifications": "system",
    "situation_briefing": "system",
    "undo_action": "system",
    # deliberately omit general_conversation / unclassified_request —
    # those are not capability domains.
}

_SUMMARY_MAX = 120

# Per-turn tool buffer (ContextVar + lock for cross-thread tool workers).
_turn_tools: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar(
    "elian_runtime_exp_tools", default=None
)
_buffer_lock = threading.Lock()


def begin_turn_tool_buffer() -> None:
    """Start collecting lightweight tool events for the current turn."""
    try:
        _turn_tools.set([])
    except Exception:
        pass


def clear_turn_tool_buffer() -> None:
    try:
        _turn_tools.set(None)
    except Exception:
        pass


def note_tool_event(
    *,
    tool_name: str = "",
    status: str = "",
    success: bool = False,
    error_code: str = "",
) -> None:
    """Buffer a compact tool outcome. Fail-open; never writes the experience log."""
    try:
        buf = _turn_tools.get()
        if buf is None:
            return
        name = str(tool_name or "").strip()[:64]
        if not name:
            return
        event = {
            "tool": name,
            "status": str(status or "").strip()[:32],
            "success": bool(success),
            "error_code": str(error_code or "").strip()[:64],
        }
        with _buffer_lock:
            if len(buf) >= 24:
                return
            buf.append(event)
    except Exception:
        pass


def take_turn_tool_events() -> List[Dict[str, Any]]:
    try:
        buf = _turn_tools.get()
        if not buf:
            return []
        with _buffer_lock:
            return [dict(item) for item in buf]
    except Exception:
        return []


def _is_secret_key(key: Any) -> bool:
    lowered = str(key).lower()
    return any(part in lowered for part in _SECRET_KEY_PARTS)


def _runtime_logging_config() -> Dict[str, Any]:
    try:
        from Config import get_config_value

        section = get_config_value("self_improve", "runtime_logging", default=None)
        if isinstance(section, dict):
            return section
    except Exception:
        pass
    return {}


def is_runtime_logging_enabled() -> bool:
    cfg = _runtime_logging_config()
    if "enabled" not in cfg:
        return True  # safe default: on, but fail-open
    return bool(cfg.get("enabled"))


def _max_entries() -> int:
    cfg = _runtime_logging_config()
    try:
        value = int(cfg.get("max_entries", DEFAULT_MAX_ENTRIES))
        return max(1, value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_ENTRIES


def _success_sample_rate() -> float:
    cfg = _runtime_logging_config()
    try:
        rate = float(cfg.get("success_sample_rate", DEFAULT_SUCCESS_SAMPLE_RATE))
        return max(0.0, min(1.0, rate))
    except (TypeError, ValueError):
        return DEFAULT_SUCCESS_SAMPLE_RATE


def store_utterance_hashes_only() -> bool:
    """Config flag retained for clarity; raw utterances are never stored."""
    cfg = _runtime_logging_config()
    if "store_utterance_hashes_only" not in cfg:
        return True
    return bool(cfg.get("store_utterance_hashes_only"))


def sanitize_metadata(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Compact metadata: drop secrets, truncate strings, no nested dumps."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for key, value in raw.items():
        if _is_secret_key(key):
            continue
        key_s = str(key)[:40]
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, bool):
            out[key_s] = value
        elif isinstance(value, (int, float)):
            out[key_s] = value
        elif isinstance(value, str):
            text = value.strip()
            if len(text) > 80:
                text = text[:77] + "..."
            out[key_s] = text
        elif isinstance(value, list):
            # Only allow short lists of small dicts/strings (tool events).
            cleaned: List[Any] = []
            for item in value[:12]:
                if isinstance(item, dict):
                    cleaned.append(sanitize_metadata(item))
                elif isinstance(item, str):
                    cleaned.append(item[:64])
                elif isinstance(item, (int, float, bool)):
                    cleaned.append(item)
            if cleaned:
                out[key_s] = cleaned
        # skip arbitrary nested dicts / objects
    return out


def _truncate_summary(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "").strip())
    if len(text) > _SUMMARY_MAX:
        return text[: _SUMMARY_MAX - 3] + "..."
    return text


def normalize_fingerprint_error_code(error_code: Any = "") -> str:
    """Canonical error_code for failure fingerprints (deterministic, non-sensitive)."""
    try:
        return str(error_code or "").strip().upper()[:64]
    except Exception:
        return ""


def fingerprint_for(
    *,
    intent: str,
    tool: str,
    category: str,
    error_code: str = "",
) -> str:
    """Stable identity: intent|tool|category|error_code → sha256[:16].

    Success sampling stores this too (typically with empty error_code); keep/drop
    decisions remain per-occurrence RNG, not fingerprint permanence.
    On unexpected construction failure, return "" so callers skip dedup merge.
    """
    try:
        base = "|".join(
            [
                str(intent or "").strip().lower(),
                str(tool or "").strip().lower(),
                str(category or "").strip().upper(),
                normalize_fingerprint_error_code(error_code),
            ]
        )
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return ""


def _normalize_domain_label(raw: Any) -> str:
    """Map envelope/family labels onto the experience-domain vocabulary."""
    key = str(raw or "").strip().lower().replace("-", "_")
    if not key:
        return ""
    if key in _DOMAIN_ALIASES:
        return _DOMAIN_ALIASES[key]
    if key in _CANONICAL_DOMAINS:
        return key
    return ""


def _domain_from_tool(tool: str) -> str:
    tool_l = str(tool or "").strip().lower()
    if not tool_l:
        return ""
    if tool_l in TOOL_DOMAINS:
        return TOOL_DOMAINS[tool_l]
    # Prefix families (browser_* workers, etc.)
    if tool_l.startswith("browser_"):
        return "web"
    if tool_l.startswith("ha_"):
        return "smart_home"
    return ""


def _domain_from_intent(intent: str) -> str:
    intent_l = str(intent or "").strip().lower()
    if not intent_l:
        return ""
    if intent_l in INTENT_DOMAINS:
        return INTENT_DOMAINS[intent_l]
    # Conservative substrings for compound / legacy intent strings only.
    for needle, domain in (
        ("smart_home", "smart_home"),
        ("climate", "smart_home"),
        ("schedule", "scheduling"),
        ("remind", "scheduling"),
        ("media_play", "media"),
        ("play_media", "media"),
        ("open_app", "app"),
        ("web_search", "web"),
        ("research", "web"),
        ("vision", "vision"),
        ("screen", "vision"),
        ("computer", "computer"),
        ("message", "messaging"),
        ("contact", "messaging"),
    ):
        if needle in intent_l:
            return domain
    return ""


def _map_domain(
    *,
    intent: str = "",
    domains: Sequence[str] | None = None,
    tool: str = "",
    source: str = "",
) -> str:
    """Resolve experience domain from structured signals (fail → system).

    Priority:
      1. specific allowed_domains / capability labels
      2. tool → domain (ToolRegistry name)
      3. intent → domain (Semantic Executive)
      4. source/handler weak fallback
      5. weak allowed_domains (e.g. SYSTEM) or \"system\"
    """
    try:
        weak_allowed = ""
        for item in domains or ():
            mapped = _normalize_domain_label(item)
            if not mapped:
                continue
            if mapped in _WEAK_ALLOWED_DOMAINS:
                weak_allowed = mapped or weak_allowed
                continue
            return mapped

        from_tool = _domain_from_tool(tool)
        if from_tool:
            return from_tool

        from_intent = _domain_from_intent(intent)
        if from_intent:
            return from_intent

        source_l = str(source or "").strip().lower()
        if source_l == "chat":
            return "knowledge"
        # Preference / teach / memory maintenance are not knowledge Q&A.
        if source_l in ("memory", "teach"):
            return "system"
        if source_l in ("proactive_pref", "proactive_offer", "future_intent_cancel"):
            return "system"

        if weak_allowed:
            return weak_allowed
        return "system"
    except Exception:
        return "system"


_CASUAL_INTENTS = frozenset(
    {
        "general_conversation",
        "unclear_utterance",
        "meta_ack",
        "acknowledgement",
        "smalltalk",
    }
)
_SUBSTANTIVE_UTTERANCE_CLASSES = frozenset(
    {
        "INFORMATION_REQUEST",
        "ACTION_REQUEST",
        "GOAL",
    }
)
_SUBSTANTIVE_MODES = frozenset({"ACT", "PLAN", "ANSWER", "OBSERVE"})


def _truthy_flag(data: Dict[str, Any], *keys: str) -> bool:
    for key in keys:
        if key in data and data.get(key) is not None:
            return bool(data.get(key))
    return False


def _normalize_utterance_class(value: Any) -> str:
    """Stable utterance-class label for experience metadata (never a dataclass repr)."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        # Reject leftover truncated object reprs already in the wild.
        if text.startswith("UtteranceClassification("):
            return ""
        return text
    # UtteranceClassification / similar objects expose .utterance_class
    inner = getattr(value, "utterance_class", None)
    if inner is not None:
        return str(getattr(inner, "value", inner) or "").strip()
    # Enum-like
    if hasattr(value, "value") and not callable(getattr(value, "value", None)):
        try:
            return str(getattr(value, "value") or "").strip()
        except Exception:
            pass
    text = str(value).strip()
    # Never persist truncated object reprs into metadata.
    if text.startswith("UtteranceClassification("):
        return ""
    return text


def _is_substantive_request(
    *,
    intent: str = "",
    mode: str = "",
    needs_tools: bool = False,
    utterance_class: str = "",
) -> bool:
    utt = str(utterance_class or "").strip().upper()
    intent_l = str(intent or "").strip().lower()
    mode_u = str(mode or "").strip().upper()
    if intent_l in _CASUAL_INTENTS:
        return False
    if utt in ("CASUAL_TALK", "CONFIRMATION"):
        return False
    if needs_tools:
        return True
    if mode_u in _SUBSTANTIVE_MODES:
        return True
    if utt in _SUBSTANTIVE_UTTERANCE_CLASSES:
        return True
    return False


def _vague_answer_error_code(data: Dict[str, Any]) -> str:
    """Stable fingerprint code from structured answer-quality signals only."""
    if _truthy_flag(data, "vacuous_reply", "response_empty"):
        return "VACUOUS_REPLY"
    if _truthy_flag(data, "false_success_blocked"):
        return "NO_COMPLETION_EVIDENCE"
    gstatus = str(data.get("grounding_status") or "").strip().upper()
    if gstatus in ("NONE", "INSUFFICIENT", "CONFLICT") and not bool(
        data.get("grounded_in_web")
    ):
        if data.get("agent_success") is False:
            return "UNGROUNDED"
    return ""


def is_vague_answer(
    *,
    source: str = "",
    utterance_class: str = "",
    intent: str = "",
    mode: str = "",
    needs_tools: bool = False,
    result_data: Optional[Dict[str, Any]] = None,
) -> bool:
    """Conservative answer-quality failure from structured signals only."""
    try:
        src = str(source or "").strip().lower()
        utt = str(utterance_class or "").strip().upper()
        if src in ("clarify", "confirm", "correction", "ack", "meta", "wait"):
            return False
        if utt == "CORRECTION":
            return False
        if not _is_substantive_request(
            intent=intent,
            mode=mode,
            needs_tools=needs_tools,
            utterance_class=utt,
        ):
            return False
        data = dict(result_data or {}) if isinstance(result_data, dict) else {}
        if not data:
            return False

        # Explicit success without blocked/vacuous markers → not vague.
        if data.get("agent_success") is True and not (
            _truthy_flag(data, "vacuous_reply", "response_empty", "false_success_blocked")
        ):
            return False

        vacuous = _truthy_flag(data, "vacuous_reply", "response_empty")
        false_blocked = _truthy_flag(data, "false_success_blocked")
        agent_failed = data.get("agent_success") is False
        grounded = bool(data.get("grounded_in_web"))
        gstatus = str(data.get("grounding_status") or "").strip().upper()
        ungrounded_answer = (
            str(mode or "").strip().upper() == "ANSWER"
            and utt == "INFORMATION_REQUEST"
            and agent_failed
            and not grounded
            and gstatus in ("NONE", "INSUFFICIENT", "CONFLICT", "")
        )

        # Require at least one strong insufficient-resolution signal.
        if vacuous and (agent_failed or false_blocked or needs_tools):
            return True
        if false_blocked and agent_failed:
            return True
        if ungrounded_answer:
            return True
        # Actionable turn: agent failed with no completion evidence flag set
        # (false_success_blocked already covers the gated path).
        if agent_failed and needs_tools and vacuous:
            return True
        return False
    except Exception:
        return False


def classify_turn(
    *,
    source: str = "",
    utterance_class: str = "",
    tool_events: Sequence[Dict[str, Any]] | None = None,
    observations: Sequence[Dict[str, Any]] | None = None,
    result_data: Optional[Dict[str, Any]] = None,
    intent: str = "",
    mode: str = "",
    needs_tools: bool = False,
) -> FailureCategory:
    """Conservative category mapping from structured runtime signals.

    Precedence: correction → clarify/confirm → timeout → no-match → tool error
    → vague answer → success → unknown.
    """
    utt = str(utterance_class or "").strip().upper()
    src = str(source or "").strip().lower()
    if utt == "CORRECTION" or src == "correction":
        return FailureCategory.USER_CORRECTION

    # Final turn outcome is clarification: not a capability/reliability failure,
    # even if an internal tool returned a failure-like ToolResult (e.g. AMBIGUOUS).
    if src == "clarify":
        return FailureCategory.SUCCESS

    # Legitimate confirmation prompts are discourse, not answer-quality failures.
    if src == "confirm":
        return FailureCategory.SUCCESS

    events = list(tool_events or ())
    if not events and isinstance(result_data, dict):
        # Fall back to compacted observation fields on HandleResult.data
        obs_tool = result_data.get("tool") or result_data.get("name")
        if obs_tool:
            events = [
                {
                    "tool": str(obs_tool),
                    "status": str(result_data.get("status") or ""),
                    "success": bool(
                        result_data.get("success")
                        if "success" in result_data
                        else result_data.get("agent_success")
                    ),
                    "error_code": str(
                        result_data.get("error_code")
                        or result_data.get("error")
                        or ""
                    ),
                }
            ]
    if not events:
        for obs in observations or ():
            if not isinstance(obs, dict):
                continue
            events.append(
                {
                    "tool": str(obs.get("tool") or ""),
                    "status": str(obs.get("status") or ""),
                    "success": bool(obs.get("success")),
                    "error_code": str(obs.get("error_code") or obs.get("error") or ""),
                }
            )

    # Prefer the last non-empty tool event (most recent outcome).
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        status = str(event.get("status") or "").strip().lower()
        code = str(event.get("error_code") or "").strip().upper()
        success = bool(event.get("success"))
        if status == "timeout" or code == "TIMEOUT" or "TIMEOUT" in code:
            return FailureCategory.TIMEOUT
        if code in ("UNKNOWN_TOOL",) or status == "unknown_tool":
            return FailureCategory.NO_TOOL_MATCH
        # Ambiguity / confirmation: tool asked for user input — not TOOL_ERROR.
        if code in ("AMBIGUOUS", "NEEDS_CONFIRMATION"):
            continue
        if code in (
            "CAPABILITY_UNAVAILABLE",
            "CAPABILITY_BLOCKED",
            "TOOL_UNAVAILABLE",
        ):
            # Environment / policy — not "no tool match".
            return FailureCategory.TOOL_ERROR
        if not success and (status or code or event.get("tool")):
            return FailureCategory.TOOL_ERROR
        if success and event.get("tool"):
            return FailureCategory.SUCCESS

    # No genuine execution failure: optional answer-quality failure.
    if is_vague_answer(
        source=src,
        utterance_class=utt,
        intent=intent,
        mode=mode,
        needs_tools=needs_tools,
        result_data=result_data if isinstance(result_data, dict) else None,
    ):
        return FailureCategory.VAGUE_ANSWER

    if utt in ("WHY_FAILURE",) or src == "why_failure":
        return FailureCategory.UNKNOWN
    if src == "chat":
        # Do not mark every chat reply SUCCESS; skip unless sampled as vague later.
        return FailureCategory.SUCCESS
    # Deterministic preference / session helpers completed without tool failure.
    # Misclassifying these as UNKNOWN fabricated a false knowledge capability gap.
    if src in (
        "proactive_pref",
        "proactive_offer",
        "future_intent_cancel",
        "memory",
        "teach",
        "cognitive",
    ):
        return FailureCategory.SUCCESS
    if src in ("fast_path", "agent", "action_loop", "planner", "confirm", "procedure"):
        return FailureCategory.SUCCESS
    return FailureCategory.UNKNOWN


def _should_skip_source(source: str) -> bool:
    return str(source or "").strip().lower() in _NOISE_SOURCES


def set_success_sampler(sampler: Optional[SuccessSampler]) -> None:
    """Install a per-draw success sampler (tests). Pass None to restore random."""
    global _success_sampler
    _success_sampler = sampler


def get_success_sampler() -> Optional[SuccessSampler]:
    return _success_sampler


def _should_sample_success(
    rate: float,
    *,
    sampler: Optional[SuccessSampler] = None,
) -> bool:
    """Per-occurrence success sampling. Failures never call this."""
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    try:
        draw_fn = sampler if sampler is not None else _success_sampler
        if draw_fn is None:
            draw = random.random()
        else:
            draw = float(draw_fn())
        return draw < float(rate)
    except Exception:
        # Sampler broken → keep the success (do not invent a failure).
        return True


def _try_increment_runtime_repeat(
    store: ExperienceLogStore,
    fingerprint: str,
) -> Optional[ExperienceEntry]:
    """Increment repeat_count on a matching *runtime* failure in the dedup window.

    Returns the updated entry, or None if no match / on error (caller must append).
    Never mutates seed/manual rows.
    """
    if not fingerprint:
        return None
    try:
        entries = store.load()
    except Exception:
        return None
    start = max(0, len(entries) - DEDUP_WINDOW)
    try:
        for index in range(len(entries) - 1, start - 1, -1):
            entry = entries[index]
            if not getattr(entry, "is_runtime", False):
                continue
            if not getattr(entry, "is_failure", False):
                continue
            meta = getattr(entry, "metadata", None) or {}
            if str(meta.get("fingerprint") or "") != fingerprint:
                continue
            entry.repeat_count = normalize_repeat_count(entry.repeat_count) + 1
            entries[index] = entry
            store.save(entries)
            return entry
    except Exception:
        return None
    return None


def _primary_tool(tool_events: Sequence[Dict[str, Any]]) -> str:
    for event in reversed(list(tool_events or ())):
        if isinstance(event, dict) and event.get("tool"):
            return str(event["tool"])
    return ""


def _build_summary(
    *,
    source: str,
    category: FailureCategory,
    tool: str,
    error_code: str = "",
) -> str:
    parts = [str(source or "turn").strip() or "turn", category.value]
    if tool:
        parts.append(tool)
    if error_code:
        parts.append(error_code)
    return _truncate_summary(":".join(parts))


def record_handle_result(
    manager: Any,
    result: Any,
    *,
    started_monotonic: float,
    store: Optional[ExperienceLogStore] = None,
    success_sampler: Optional[SuccessSampler] = None,
) -> Optional[ExperienceEntry]:
    """Record one turn. Returns the entry when persisted, else None. Fail-open."""
    try:
        return _record_handle_result_impl(
            manager,
            result,
            started_monotonic=started_monotonic,
            store=store,
            success_sampler=success_sampler,
        )
    except Exception:
        return None
    finally:
        clear_turn_tool_buffer()


def _record_handle_result_impl(
    manager: Any,
    result: Any,
    *,
    started_monotonic: float,
    store: Optional[ExperienceLogStore] = None,
    success_sampler: Optional[SuccessSampler] = None,
) -> Optional[ExperienceEntry]:
    if not is_runtime_logging_enabled():
        return None
    if result is None:
        return None

    source = str(getattr(result, "source", "") or "").strip().lower()
    if _should_skip_source(source):
        return None
    # Clarification is a successful discourse outcome, not a reliability failure.
    # Skip persistence so ReflectionEngine cannot invent TOOL_ERROR gaps from it.
    if source == "clarify":
        return None
    if source and source not in _ACTION_SOURCES and source not in (
        "cancel",
        "end",
        "stop",
        "negation",
    ):
        # Unknown sources: only keep if they look actionable.
        if source not in ("fast_path", "agent", "chat"):
            if source in _NOISE_SOURCES:
                return None

    # cancel/end/negation are session control — skip Phase 1 noise
    if source in ("cancel", "end", "stop", "negation"):
        return None

    envelope = getattr(manager, "_decision_envelope", None)
    intent = str(getattr(envelope, "intent", "") or "")
    utterance_class = _normalize_utterance_class(
        getattr(envelope, "utterance_class", None)
    ) or _normalize_utterance_class(
        getattr(manager, "_last_utterance_class", None)
    )
    confidence = getattr(envelope, "confidence", None)
    domains = list(getattr(envelope, "allowed_domains", None) or [])
    mode = str(getattr(envelope, "mode", "") or "")
    needs_tools = bool(getattr(envelope, "needs_tools", False))

    tool_events = take_turn_tool_events()
    observations = list(getattr(manager, "last_observations", None) or [])[-6:]
    # Prefer events from this turn's buffer; observations are a fallback.
    result_data = getattr(result, "data", None)
    if not isinstance(result_data, dict):
        result_data = {}
    else:
        result_data = dict(result_data)

    # Merge goal-level false-success markers when HandleResult omitted them.
    try:
        goals = getattr(manager, "goals", None)
        current = getattr(goals, "current", None) if goals is not None else None
        lr = dict(getattr(current, "last_result", None) or {}) if current else {}
        if lr.get("false_success_blocked") and "false_success_blocked" not in result_data:
            result_data["false_success_blocked"] = True
        if current is not None and "goal_status" not in result_data:
            result_data["goal_status"] = str(getattr(current, "status", "") or "")[:32]
    except Exception:
        pass

    # Transient empty-response signal (never persisted as raw text).
    try:
        if "response_empty" not in result_data:
            result_data["response_empty"] = not bool(
                str(getattr(result, "text", "") or "").strip()
            )
    except Exception:
        pass

    category = classify_turn(
        source=source,
        utterance_class=utterance_class,
        tool_events=tool_events,
        observations=observations if not tool_events else [],
        result_data=result_data,
        intent=intent,
        mode=mode,
        needs_tools=needs_tools,
    )

    tool = _primary_tool(tool_events)
    if not tool and isinstance(result_data, dict):
        # Only accept real tool names — never fabricate answer helpers.
        maybe_tool = str(result_data.get("tool") or result_data.get("name") or "").strip()
        if maybe_tool and maybe_tool.lower() not in (
            "agent",
            "chat",
            "planner",
            "answer",
            "final_answer",
        ):
            tool = maybe_tool[:64]
    if not tool:
        for obs in reversed(observations):
            if isinstance(obs, dict) and obs.get("tool"):
                tool = str(obs.get("tool"))[:64]
                break
    # VAGUE_ANSWER with no concrete tool must keep tool empty.
    if category is FailureCategory.VAGUE_ANSWER and not tool_events:
        if not any(
            isinstance(obs, dict) and obs.get("tool") for obs in observations
        ):
            tool = ""

    error_code = ""
    tool_status = ""
    for event in reversed(tool_events):
        if event.get("error_code"):
            error_code = str(event["error_code"])
            tool_status = str(event.get("status") or "")
            break
        if event.get("status"):
            tool_status = str(event["status"])
    if category is FailureCategory.VAGUE_ANSWER and not error_code:
        error_code = _vague_answer_error_code(result_data)

    # SUCCESS only when a tool participated; sample those. Pure talk/clarify = noise.
    # VAGUE_ANSWER is a failure and always eligible for persistence (no tool required).
    if category is FailureCategory.SUCCESS:
        if not tool or source in ("chat", "clarify", "confirm"):
            return None

    try:
        domain = _map_domain(
            intent=intent, domains=domains, tool=tool, source=source
        )
    except Exception:
        domain = "system"
    if domain not in _CANONICAL_DOMAINS:
        domain = "system"
    # Failure identity: intent|tool|category|error_code (normalized).
    # Old rows without error_code in the hash will not match — safe non-merge.
    try:
        fp = fingerprint_for(
            intent=intent,
            tool=tool,
            category=category.value,
            error_code=error_code,
        )
    except Exception:
        fp = ""

    if category is FailureCategory.SUCCESS:
        if not _should_sample_success(
            _success_sample_rate(), sampler=success_sampler
        ):
            return None

    target_store = store or get_store()

    # Count-preserving failure dedup within the recent physical-row window.
    # Empty fp → no match → new row (safer than overly broad merge).
    if category is not FailureCategory.SUCCESS and fp:
        try:
            bumped = _try_increment_runtime_repeat(target_store, fp)
            if bumped is not None:
                return bumped
        except Exception:
            # Fall through and persist a new row — never silently lose a failure.
            pass

    import time as _time

    latency_ms = 0.0
    try:
        if started_monotonic:
            latency_ms = max(0.0, (_time.monotonic() - float(started_monotonic)) * 1000.0)
    except Exception:
        latency_ms = 0.0

    metadata: Dict[str, Any] = {
        "source": source,
        "fingerprint": fp,
    }
    if intent:
        metadata["intent"] = intent[:80]
    if utterance_class:
        metadata["utterance_class"] = utterance_class[:40]
    if confidence is not None:
        try:
            metadata["confidence"] = round(float(confidence), 3)
        except (TypeError, ValueError):
            pass
    if error_code:
        metadata["error_code"] = error_code[:64]
    if tool_status:
        metadata["tool_status"] = tool_status[:32]
    if tool_events:
        metadata["tools"] = [
            {
                "tool": e.get("tool"),
                "status": e.get("status"),
                "success": e.get("success"),
                "error_code": e.get("error_code") or "",
            }
            for e in tool_events[:12]
        ]
        metadata["steps"] = len(tool_events)
    if category is FailureCategory.VAGUE_ANSWER:
        metadata["answer_quality"] = "insufficient"
        metadata["grounded"] = bool(result_data.get("grounded_in_web"))
        if result_data.get("grounding_status"):
            metadata["grounding_status"] = str(result_data.get("grounding_status"))[:32]
        if "vacuous_reply" in result_data:
            metadata["vacuous_reply"] = bool(result_data.get("vacuous_reply"))
        if "false_success_blocked" in result_data:
            metadata["false_success_blocked"] = bool(
                result_data.get("false_success_blocked")
            )
        if mode:
            metadata["mode"] = mode[:24]
    # Optional RC session tag (set by self_improve.cli rc-session when launching voice).
    try:
        rc_sid = str(os.environ.get("ELIAN_RC_SESSION_ID") or "").strip()
        if rc_sid:
            metadata["rc_session_id"] = rc_sid[:64]
    except Exception:
        pass
    # Config flag documented; we never hash raw utterance text.
    metadata["utterance_hash_mode"] = bool(store_utterance_hashes_only())

    summary = _build_summary(
        source=source,
        category=category,
        tool=tool,
        error_code=error_code,
    )

    session_id = str(getattr(manager, "_experience_session_id", "") or "")

    entry = ExperienceEntry(
        domain=domain,
        category=category,
        summary=summary,
        session_id=session_id,
        tool=tool,
        user_feedback="",  # never store correction verbatim in Phase 1
        latency_ms=round(float(latency_ms), 1),
        metadata=sanitize_metadata(metadata),
        provenance=PROVENANCE_RUNTIME,
        repeat_count=1,
    )

    # Cap growth on write.
    try:
        entries = target_store.load()
        entries.append(entry)
        max_n = _max_entries()
        if len(entries) > max_n:
            entries = entries[-max_n:]
        target_store.save(entries)
    except Exception:
        return None
    return entry


class RuntimeExperienceCollector:
    """Thin façade used by ConversationManager (optional explicit instance)."""

    def __init__(
        self,
        store: Optional[ExperienceLogStore] = None,
        *,
        success_sampler: Optional[SuccessSampler] = None,
    ):
        self.store = store
        self.success_sampler = success_sampler

    def record_turn(
        self,
        manager: Any,
        result: Any,
        *,
        started_monotonic: float,
    ) -> Optional[ExperienceEntry]:
        return record_handle_result(
            manager,
            result,
            started_monotonic=started_monotonic,
            store=self.store,
            success_sampler=self.success_sampler,
        )
