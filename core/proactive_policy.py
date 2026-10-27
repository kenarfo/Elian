# ==========================
# ELIAN — ProactivePolicy (multi-signal conclusions)
# ==========================
"""Evaluate event + WorldState + session into IGNORE/STORE/DIGEST/NOTIFY/ASK.

Extends ProactiveManager — not a second alarm system, not a third safety policy.
Internal telemetry stays silent. ASK only when the user has a real decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.events import (
    BATTERY_CRITICAL,
    BATTERY_LOW,
    CALENDAR_UPCOMING,
    HA_UNAVAILABLE,
    INCOMING_MESSAGE,
    OS_NOTIFICATION,
    Priority,
    REMINDER_DUE,
    Event,
)
from core.speech_guard import (
    SILENT_STATE_EVENTS,
    guard_user_facing_speech,
    looks_like_internal_identifier,
)


# ----- vocabulary -----

INFO = "INFO"
USEFUL = "USEFUL"
IMPORTANT = "IMPORTANT"
URGENT = "URGENT"

IGNORE = "IGNORE"
STORE = "STORE"
DEFER = "DEFER"
DIGEST = "DIGEST"
NOTIFY = "NOTIFY"
SUGGEST = "SUGGEST"
ASK = "ASK"

HOME_ARRIVAL = "home_arrival"
LEAVING_HOME = "leaving_home"
WINDOWS_UPDATE = "windows_update_starting"
HEARTBEAT = "heartbeat"
WORLD_STATE_REFRESH = "world_state_refresh"
CPU_SAMPLE = "cpu_sample"

SILENT_TELEMETRY = frozenset(
    {
        HEARTBEAT,
        WORLD_STATE_REFRESH,
        "WORLD_STATE_REFRESH",
        CPU_SAMPLE,
        "cpu_update",
        "cpu_percent",
        "cpu_tick",
    }
)

_HA_INTENTS = frozenset(
    {
        "turn_on_device",
        "turn_off_device",
        "set_device_value",
        "get_device_state",
        "get_home_summary",
        "home_overview",
        "home_arrival",
        "activate_scene",
        "light_control",
    }
)

_BANNED_ASK = (
    "czy chcesz coś jeszcze",
    "czy chcesz cos jeszcze",
    "czy mogę pomóc",
    "czy moge pomoc",
    "czy mogę jeszcze pomóc",
    "czy moge jeszcze pomoc",
    "w czym mogę pomóc",
    "w czym moge pomoc",
)

_CONSUMED: Dict[str, float] = {}
_CONSUMED_TTL = 600.0


@dataclass
class PolicyVerdict:
    """event → context → policy → decision → spoken/deferred/suppressed."""

    decision: str
    action: str
    reason: str
    message: str = ""
    priority: str = USEFUL
    outcome: str = "suppressed"
    rule: str = ""
    offer_kind: str = ""
    offer_extra: Dict[str, Any] = field(default_factory=dict)
    context: str = ""

    def to_log(self, event: Event, now: float) -> dict:
        return {
            "event": event.type if event else "",
            "context": self.context,
            "policy": self.decision,
            "decision": self.action,
            "reason": self.reason,
            "priority": self.priority,
            "outcome": self.outcome,
            "rule": self.rule,
            "ts": now,
        }


def mark_consumed(topic: str, *, now: float | None = None) -> None:
    import time

    key = str(topic or "").strip()
    if not key:
        return
    _CONSUMED[key] = float(now if now is not None else time.time())


def was_consumed(topic: str, *, now: float | None = None) -> bool:
    import time

    key = str(topic or "").strip()
    if not key:
        return False
    stamp = _CONSUMED.get(key)
    if stamp is None:
        return False
    current = float(now if now is not None else time.time())
    if (current - float(stamp)) > _CONSUMED_TTL:
        _CONSUMED.pop(key, None)
        return False
    return True


def reset_policy_for_tests() -> None:
    _CONSUMED.clear()


def note_session_from_envelope(envelope: Any) -> None:
    """WorldState hook — last intent / HA topic. Does not rewrite WorldState."""
    try:
        from core.world_state import note_conversation

        intent = str(getattr(envelope, "intent", "") or "").strip()
        if intent:
            note_conversation("last_intent", intent)
            if intent in _HA_INTENTS:
                note_conversation("ha_topic", True)
    except Exception:
        pass


def note_session_active(active: bool) -> None:
    try:
        from core.world_state import note_conversation

        note_conversation("session_active", bool(active))
    except Exception:
        pass


def group_digest_spoken(lines: List[str], *, max_items: int = 3) -> str:
    bits = [str(item).strip().rstrip(".") for item in lines if str(item).strip()]
    bits = bits[: max(1, int(max_items or 3))]
    if not bits:
        return ""
    if len(bits) == 1:
        return bits[0] + "."
    return "Kilka rzeczy: " + "; ".join(bits) + "."


def _norm_msg(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _priority_band(event: Event) -> str:
    if event.priority is Priority.CRITICAL:
        return URGENT
    if event.priority is Priority.HIGH:
        return IMPORTANT
    if event.priority is Priority.LOW:
        return INFO
    return USEFUL


def _snapshot_world() -> Dict[str, Any]:
    try:
        from core.world_state import get_world_state, take_snapshot

        world = dict(get_world_state() or {})
        try:
            snap = take_snapshot()
        except Exception:
            snap = None
        if snap is not None:
            for slot in (
                "session",
                "tts",
                "stt",
                "calendar",
                "reminders",
                "pc",
                "integrations",
                "network",
                "media",
            ):
                fact = snap.fact(slot)
                if fact is None:
                    continue
                world[slot] = copy_value(fact.value)
                world[f"_{slot}_freshness"] = fact.freshness
                world[f"_{slot}_status"] = fact.status
            smart = snap.fact("smart_home")
            if smart is not None:
                world["_smart_home_freshness"] = smart.freshness
                world["_smart_home_status"] = smart.status
                if smart.value and not world.get("smart_home"):
                    world["smart_home"] = copy_value(smart.value)
        return world
    except Exception:
        return {}


def copy_value(value: Any) -> Any:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value


def _conversation(world: Dict[str, Any]) -> Dict[str, Any]:
    conv = world.get("conversation") or {}
    return conv if isinstance(conv, dict) else {}


def _user_sleeping(event: Event, world: Dict[str, Any]) -> bool:
    data = dict(event.data or {})
    if data.get("sleeping") is True:
        return True
    conv = _conversation(world)
    if conv.get("sleeping") is True:
        return True
    session = world.get("session") or {}
    if isinstance(session, dict) and session.get("sleeping") is True:
        return True
    presence = str(
        data.get("presence") or conv.get("presence") or (
            session.get("presence") if isinstance(session, dict) else ""
        )
        or ""
    ).strip().lower()
    return presence in ("sleeping", "asleep", "sleep")


def _presence(event: Event, world: Dict[str, Any]) -> str:
    data = dict(event.data or {})
    conv = _conversation(world)
    raw = str(data.get("presence") or conv.get("presence") or "").strip().lower()
    if event.type == LEAVING_HOME or data.get("leaving"):
        return "leaving"
    if event.type == HOME_ARRIVAL or data.get("home_arrival"):
        return "arriving"
    return raw


def _lights_on(world: Dict[str, Any]) -> List[Dict[str, Any]]:
    freshness = str(world.get("_smart_home_freshness") or "")
    if freshness in ("UNKNOWN", "UNAVAILABLE"):
        return []
    smart = world.get("smart_home") or {}
    if not isinstance(smart, dict):
        return []
    on: List[Dict[str, Any]] = []
    for entity_id, device in smart.items():
        if not isinstance(device, dict):
            continue
        state = str(device.get("state") or device.get("status") or "").strip().lower()
        if state not in ("on", "true", "1"):
            continue
        domain = str(device.get("domain") or entity_id or "").lower()
        name = str(device.get("name") or device.get("friendly_name") or "").lower()
        if (
            "light" in domain
            or "lamp" in name
            or "lampa" in name
            or str(entity_id).startswith("light.")
            or device.get("is_light")
        ):
            row = dict(device)
            row.setdefault("entity_id", entity_id)
            on.append(row)
    return on


def _windows_update(event: Event, world: Dict[str, Any]) -> bool:
    data = dict(event.data or {})
    if event.type == WINDOWS_UPDATE:
        return True
    if data.get("windows_update") or data.get("update_starting"):
        return True
    for key in ("computer", "pc"):
        blob = world.get(key) or {}
        if isinstance(blob, dict) and (
            blob.get("windows_update") or blob.get("update_starting")
        ):
            return True
    return False


def _ha_topic(event: Event, world: Dict[str, Any]) -> bool:
    data = dict(event.data or {})
    if data.get("ha_topic") or data.get("light_conversation"):
        return True
    conv = _conversation(world)
    if conv.get("ha_topic") is True:
        return True
    intent = str(conv.get("last_intent") or data.get("last_intent") or "").strip()
    return intent in _HA_INTENTS


def _lamp_label(lights: List[Dict[str, Any]]) -> str:
    if not lights:
        return "Lampa"
    first = lights[0]
    name = str(first.get("name") or first.get("friendly_name") or "").strip()
    if name:
        return name[0].upper() + name[1:] if name else "Lampa"
    return "Lampa"


def _banned_ask(message: str) -> bool:
    low = _norm_msg(message)
    return any(frag in low for frag in _BANNED_ASK)


def _freshness_ok_for_lights(world: Dict[str, Any]) -> bool:
    freshness = str(world.get("_smart_home_freshness") or "CURRENT")
    status = str(world.get("_smart_home_status") or "")
    if status == "UNAVAILABLE" or freshness == "UNAVAILABLE":
        return False
    return freshness in ("CURRENT", "STALE", "KNOWN", "")


def _compose_arrival_message() -> str:
    try:
        from core.situation_briefing import compose_situation_briefing

        result = compose_situation_briefing({"mode": "arrival"})
        text = str(getattr(result, "message", "") or "").strip()
        return guard_user_facing_speech(text)
    except Exception:
        return "Witaj w domu."


class ProactivePolicy:
    """Multi-signal policy used by ProactiveManager._decide."""

    def evaluate(self, event: Event, host: Any) -> PolicyVerdict:
        now = float(host.clock.time())
        world = _snapshot_world()
        band = _priority_band(event)
        ctx_bits = self._context_label(event, host, world)
        sleeping = _user_sleeping(event, world)

        if event.is_expired(now):
            return PolicyVerdict(
                IGNORE, "ignore", "expired", priority=band, context=ctx_bits
            )

        spoken, relevant, state_reason = host._semantic_state_speech(event)
        if event.type in SILENT_STATE_EVENTS and not relevant:
            return PolicyVerdict(
                IGNORE, "silent", "state_only", priority=INFO, context=ctx_bits
            )
        if event.type in SILENT_TELEMETRY:
            return PolicyVerdict(
                IGNORE, "silent", "telemetry", priority=INFO, context=ctx_bits
            )
        if event.type in ("INTERNAL_EVENT", "PROCEDURE_COMPLETE"):
            try:
                from core.proactive import note_internal_event_tts_leak

                note_internal_event_tts_leak()
            except Exception:
                pass
            return PolicyVerdict(
                IGNORE, "silent", "internal_event", priority=INFO, context=ctx_bits
            )

        data = dict(event.data or {})
        reason = str(data.get("reason") or data.get("initiate_reason") or "")
        taskish = event.type in (
            "task_completed",
            "task_blocked",
            "analysis_complete",
            "procedure_partial_fail",
            "critical_task_failure",
        )
        if taskish or reason:
            try:
                from core.collaboration import should_initiate

                ok, why = should_initiate(
                    reason=reason or event.type,
                    event_type=event.type,
                    user_value=bool(
                        data.get("user_value")
                        or data.get("user_relevance")
                        or data.get("goal_blocked")
                    ),
                    notify_requested=bool(
                        data.get("notify_requested") or data.get("user_asked_notify")
                    ),
                    priority=event.priority.value,
                    conversation_active=bool(host.conversation_active),
                    suppress_non_urgent=bool(host.suppress_non_urgent),
                    has_background_work=bool(data.get("has_background_work", True)),
                )
                if not ok:
                    return PolicyVerdict(
                        IGNORE, "silent", why, priority=band, context=ctx_bits
                    )
            except Exception:
                pass

        if host.suppress_non_urgent and event.priority.rank < Priority.HIGH.rank:
            return PolicyVerdict(
                IGNORE,
                "suppress",
                "suppressed_non_urgent",
                priority=band,
                context=ctx_bits,
            )

        session_active = bool(host.conversation_active)
        if session_active and event.priority is Priority.LOW:
            message = guard_user_facing_speech(host.message_for(event))
            return PolicyVerdict(
                DIGEST,
                "queue",
                "low_no_interrupt",
                message=message,
                priority=INFO,
                outcome="deferred",
                context=ctx_bits,
            )
        if (
            session_active
            and event.type in (INCOMING_MESSAGE, OS_NOTIFICATION)
            and event.priority.rank < Priority.HIGH.rank
            and not _ha_topic(event, world)
        ):
            message = guard_user_facing_speech(host.message_for(event))
            return PolicyVerdict(
                DIGEST,
                "queue",
                "conversation_active",
                message=message,
                priority=band,
                outcome="deferred",
                context=ctx_bits,
            )
        if host._barge_in and event.priority.rank < Priority.HIGH.rank:
            message = guard_user_facing_speech(host.message_for(event))
            return PolicyVerdict(
                DIGEST,
                "queue",
                "barge_in",
                message=message,
                priority=band,
                outcome="deferred",
                context=ctx_bits,
            )

        if not host.enabled():
            if event.priority is Priority.CRITICAL:
                return PolicyVerdict(
                    STORE,
                    "suppress",
                    "proactive_disabled_critical_log_only",
                    priority=URGENT,
                    context=ctx_bits,
                )
            return PolicyVerdict(
                IGNORE,
                "suppress",
                "proactive_disabled",
                priority=band,
                context=ctx_bits,
            )

        cat = host.category_for(event)
        if not host.category_enabled(cat):
            return PolicyVerdict(
                IGNORE,
                "suppress",
                f"category_disabled:{cat}",
                priority=band,
                context=ctx_bits,
            )

        if event.type == "cpu_high_sustained":
            cpu_pref = host.preference_get("proactive.system.cpu", None)
            if cpu_pref is not None and str(cpu_pref).lower() in (
                "0",
                "false",
                "no",
                "off",
                "nie",
            ):
                return PolicyVerdict(
                    IGNORE, "suppress", "pref_cpu_off", priority=band, context=ctx_bits
                )

        if event.dedup_key in host._seen_dedup and event.type not in (
            "network_restored",
            "ha_restored",
            "battery_charging",
        ):
            return PolicyVerdict(
                IGNORE, "suppress", "dedup_latch", priority=band, context=ctx_bits
            )

        cool = host.cooldown_seconds(event.type)
        last = host._last_notified.get(event.dedup_key)
        if last is not None and cool > 0 and (now - last) < cool:
            return PolicyVerdict(
                IGNORE, "suppress", "cooldown", priority=band, context=ctx_bits
            )

        message = spoken if relevant else host.message_for(event)
        signal = self._multi_signal(event, host, world, message, band)
        if signal is not None:
            message, band, extra_rule, extra_kind, extra_offer = signal
            if extra_rule in ("consumed", "leaving_no_lights"):
                return PolicyVerdict(
                    IGNORE,
                    "silent",
                    extra_rule,
                    priority=band,
                    context=ctx_bits,
                )
        else:
            extra_rule, extra_kind, extra_offer = "", "", {}

        message = guard_user_facing_speech(message)
        if _banned_ask(message):
            return PolicyVerdict(
                IGNORE, "silent", "banned_ask", priority=band, context=ctx_bits
            )
        if not message:
            return PolicyVerdict(
                IGNORE, "silent", "no_user_message", priority=band, context=ctx_bits
            )
        if looks_like_internal_identifier(message):
            return PolicyVerdict(
                IGNORE, "silent", "internal_identifier", priority=band, context=ctx_bits
            )

        similar = _norm_msg(message)
        last_sim = getattr(host, "_last_spoken_norm", "") or ""
        last_sim_at = float(getattr(host, "_last_spoken_at", 0.0) or 0.0)
        similar_cool = max(cool, 30.0)
        ask = bool(extra_kind) or event.type in (INCOMING_MESSAGE, OS_NOTIFICATION)
        if (
            similar
            and similar == last_sim
            and (now - last_sim_at) < similar_cool
            and not ask
        ):
            return PolicyVerdict(
                IGNORE,
                "suppress",
                "duplicate_message",
                priority=band,
                context=ctx_bits,
            )

        if extra_rule == "home_arrival" and was_consumed("home_arrival", now=now):
            return PolicyVerdict(
                IGNORE, "silent", "consumed", priority=band, context=ctx_bits
            )

        if ask and not extra_kind and event.type in (INCOMING_MESSAGE, OS_NOTIFICATION):
            extra_kind = "read_inbox"
            extra_offer = {
                "item_id": str(data.get("item_id") or ""),
                "body": str(data.get("body") or data.get("title") or "").strip(),
            }

        if sleeping and band != URGENT:
            return PolicyVerdict(
                DIGEST,
                "queue",
                "sleeping",
                message=message,
                priority=band,
                outcome="deferred",
                rule=extra_rule or "sleeping",
                context=ctx_bits,
            )

        speaking = bool(host.busy_check())
        tts = world.get("tts") or {}
        if isinstance(tts, dict) and (
            tts.get("speaking") is True or str(tts.get("state") or "").lower() == "speaking"
        ):
            speaking = True
        important_tts = bool(getattr(host, "important_tts", False))
        interrupt_ok = band == URGENT or (
            extra_rule in ("ha_during_light",) and not important_tts and not speaking
        )

        if host.quiet_hours_active() and event.priority is not Priority.CRITICAL:
            if event.type != REMINDER_DUE:
                return PolicyVerdict(
                    DIGEST,
                    "queue",
                    "quiet_hours",
                    message=message,
                    priority=band,
                    outcome="deferred",
                    rule=extra_rule,
                    offer_kind=extra_kind,
                    offer_extra=extra_offer,
                    context=ctx_bits,
                )

        if (
            event.type != REMINDER_DUE
            and not host._rate_allows(now)
            and event.priority.rank < Priority.CRITICAL.rank
        ):
            return PolicyVerdict(
                DIGEST,
                "queue",
                "rate_limit",
                message=message,
                priority=band,
                outcome="deferred",
                rule=extra_rule,
                context=ctx_bits,
            )

        if speaking and not interrupt_ok:
            if event.type in SILENT_STATE_EVENTS:
                return PolicyVerdict(
                    IGNORE, "silent", "state_only", priority=band, context=ctx_bits
                )
            return PolicyVerdict(
                DIGEST,
                "queue",
                "user_busy",
                message=message,
                priority=band,
                outcome="deferred",
                rule=extra_rule,
                offer_kind=extra_kind,
                offer_extra=extra_offer,
                context=ctx_bits,
            )

        if important_tts and band != URGENT and not interrupt_ok:
            return PolicyVerdict(
                DIGEST,
                "queue",
                "important_tts",
                message=message,
                priority=band,
                outcome="deferred",
                rule=extra_rule,
                context=ctx_bits,
            )

        if (
            session_active
            and band in (INFO, USEFUL)
            and extra_rule not in ("ha_during_light", "leaving_lights")
            and event.type not in (REMINDER_DUE,)
            and event.priority.rank < Priority.HIGH.rank
        ):
            return PolicyVerdict(
                DIGEST,
                "queue",
                "conversation_active",
                message=message,
                priority=band,
                outcome="deferred",
                rule=extra_rule,
                context=ctx_bits,
            )

        reason_out = extra_rule or (state_reason if relevant else "ok")
        decision = ASK if ask else NOTIFY
        if extra_kind:
            decision = ASK
        return PolicyVerdict(
            decision,
            "notify",
            reason_out,
            message=message,
            priority=band,
            outcome="spoken",
            rule=extra_rule,
            offer_kind=extra_kind,
            offer_extra=extra_offer,
            context=ctx_bits,
        )

    def _context_label(self, event: Event, host: Any, world: Dict[str, Any]) -> str:
        bits = [event.type]
        presence = _presence(event, world)
        if presence:
            bits.append(f"presence={presence}")
        if _user_sleeping(event, world):
            bits.append("sleeping")
        if host.conversation_active or _conversation(world).get("session_active"):
            bits.append("session")
        if host.busy_check():
            bits.append("speaking")
        if _ha_topic(event, world):
            bits.append("ha_topic")
        lights = _lights_on(world)
        if lights:
            bits.append(f"lights_on={len(lights)}")
        return ",".join(bits)

    def _multi_signal(
        self,
        event: Event,
        host: Any,
        world: Dict[str, Any],
        message: str,
        band: str,
    ) -> Optional[Tuple[str, str, str, str, Dict[str, Any]]]:
        """Return (message, band, rule, offer_kind, offer_extra) or None."""
        presence = _presence(event, world)
        lights = _lights_on(world) if _freshness_ok_for_lights(world) else []
        now = float(host.clock.time())

        if presence == "arriving" or event.type == HOME_ARRIVAL:
            if was_consumed("home_arrival", now=now):
                return message, band, "consumed", "", {}
            spoken = _compose_arrival_message() or "Witaj w domu."
            return spoken, IMPORTANT, "home_arrival", "", {}

        if presence == "leaving" or event.type == LEAVING_HOME:
            if not lights:
                return "", band, "leaving_no_lights", "", {}
            label = _lamp_label(lights)
            spoken = f"{label} nadal świeci. Zgasić?"
            first = lights[0]
            extra = {
                "tool": "turn_off_device",
                "arguments": {
                    "target": str(first.get("name") or first.get("friendly_name") or "lampa"),
                    "entity_id": str(first.get("entity_id") or ""),
                    "domain": str(first.get("domain") or "light"),
                },
                "yes_phrases": ["zgas", "zgasic", "wylacz"],
            }
            return spoken, IMPORTANT, "leaving_lights", "turn_off_light", extra

        if event.type == HA_UNAVAILABLE:
            if _ha_topic(event, world):
                return (
                    "Sterowanie domu jest chwilowo niedostępne.",
                    IMPORTANT,
                    "ha_during_light",
                    "",
                    {},
                )
            return (
                message or "Home Assistant jest niedostępny.",
                band,
                "ha_unavailable",
                "",
                {},
            )

        if event.type == CALENDAR_UPCOMING and _windows_update(event, world):
            data = dict(event.data or {})
            title = str(data.get("title") or "wydarzenie").strip() or "wydarzenie"
            minutes = data.get("minutes")
            when = f"Za {int(minutes)} minut" if minutes is not None else "Wkrótce"
            spoken = (
                f"{when} masz {title}, a właśnie zaczyna się aktualizacja Windows."
            )
            return spoken, IMPORTANT, "calendar_windows_update", "", {}

        if event.type == WINDOWS_UPDATE:
            data = dict(event.data or {})
            minutes = data.get("minutes")
            title = str(data.get("title") or "").strip()
            if minutes is not None and title:
                spoken = (
                    f"Za {int(minutes)} minut masz {title}, "
                    "a właśnie zaczyna się aktualizacja Windows."
                )
                return spoken, IMPORTANT, "calendar_windows_update", "", {}
            if minutes is not None or title:
                return message, band, "", "", {}

        if event.type in (BATTERY_LOW, BATTERY_CRITICAL):
            return message, band, "battery", "", {}

        if event.type == REMINDER_DUE:
            return message, IMPORTANT, "reminder_due", "", {}

        return None


def evaluate_event(event: Event, host: Any) -> PolicyVerdict:
    policy = getattr(host, "policy", None) or ProactivePolicy()
    return policy.evaluate(event, host)


def verdict_from_situation(
    situation: Any,
    *,
    attention_decision: str,
    attention_reason: str = "",
) -> PolicyVerdict:
    """Map Situation + AttentionManager decision → PolicyVerdict.

    Does not replace evaluate(); used when SituationEngine owns the meaning.
    """
    from core.situation_types import AttentionDecision

    message = str(getattr(situation, "message", "") or "").strip()
    message = guard_user_facing_speech(message)
    hint = str(getattr(situation, "policy_hint", "") or NOTIFY).upper()
    offer_kind = str(getattr(situation, "offer_kind", "") or "")
    offer_extra = dict(getattr(situation, "offer_extra", None) or {})
    critical = bool(getattr(situation, "critical", False))
    band = URGENT if critical else (
        IMPORTANT
        if float(getattr(situation, "importance", 0.5) or 0.5) >= 0.75
        else USEFUL
    )
    rule = str(getattr(situation, "type", "") or "situation")
    decision_name = str(attention_decision or "")
    try:
        attn = AttentionDecision(decision_name)
    except ValueError:
        attn = AttentionDecision.SILENT

    if attn is AttentionDecision.SILENT or not message:
        return PolicyVerdict(
            IGNORE,
            "silent",
            attention_reason or "attention_silent",
            priority=band,
            rule=rule,
            context=rule,
        )
    if attn is AttentionDecision.DIGEST:
        return PolicyVerdict(
            DIGEST,
            "queue",
            attention_reason or "digest",
            message=message,
            priority=INFO if band == USEFUL else band,
            outcome="deferred",
            rule=rule,
            offer_kind=offer_kind,
            offer_extra=offer_extra,
            context=rule,
        )
    if attn in (AttentionDecision.DEFER, AttentionDecision.SPEAK_AFTER_TURN):
        return PolicyVerdict(
            DEFER,
            "queue",
            attention_reason or "defer",
            message=message,
            priority=band,
            outcome="deferred",
            rule=rule,
            offer_kind=offer_kind,
            offer_extra=offer_extra,
            context=rule,
        )

    # INTERRUPT_NOW
    policy = hint if hint in (NOTIFY, SUGGEST, ASK, STORE) else NOTIFY
    if offer_kind and policy == NOTIFY:
        policy = SUGGEST if hint == SUGGEST else ASK
    return PolicyVerdict(
        policy,
        "notify",
        attention_reason or "situation",
        message=message,
        priority=band,
        outcome="spoken",
        rule=rule,
        offer_kind=offer_kind,
        offer_extra=offer_extra,
        context=rule,
    )
