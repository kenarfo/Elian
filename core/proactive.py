# ==========================
# ELIAN — ProactiveManager (Phase 6)
# ==========================

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from Config import config, get_config_value
from Diagnostics import log_turn
from core.clock import Clock, SystemClock
from core.events import (
    ACTIVE_WINDOW_CHANGED,
    Notification,
    Priority,
    Event,
)
from core.proactive_policy import ProactivePolicy, evaluate_event, group_digest_spoken
from core.speech_guard import (
    SILENT_STATE_EVENTS,
    guard_user_facing_speech,
    looks_like_internal_identifier,
)


BusyCheck = Callable[[], bool]
SpeakFn = Callable[[str], None]
PrefGet = Callable[[str, Optional[str]], Optional[str]]

_METRICS = {
    "internal_event_tts_leak_rate": 0,
    "irrelevant_proactive_speech_rate": 0,
    "internal_event_proactive_rate": 0,
}


def proactive_metrics() -> Dict[str, int]:
    return dict(_METRICS)


def reset_proactive_metrics() -> None:
    for key in list(_METRICS):
        _METRICS[key] = 0


def note_internal_event_tts_leak() -> None:
    _METRICS["internal_event_tts_leak_rate"] = int(
        _METRICS.get("internal_event_tts_leak_rate") or 0
    ) + 1
    _METRICS["internal_event_proactive_rate"] = int(
        _METRICS.get("internal_event_proactive_rate") or 0
    ) + 1


def note_irrelevant_proactive_speech() -> None:
    _METRICS["irrelevant_proactive_speech_rate"] = int(
        _METRICS.get("irrelevant_proactive_speech_rate") or 0
    ) + 1


def _proactive_config() -> dict:
    section = config.get("proactive", {})
    return section if isinstance(section, dict) else {}


@dataclass
class ProactiveCandidate:
    """Queued proactive item. `message` is user-facing speech or None — never event.type."""

    event: Event
    message: Optional[str] = None
    user_relevance: bool = False
    reason: str = ""
    event_type: str = ""
    source: str = ""
    payload: Dict = field(default_factory=dict)
    created_at: float = 0.0
    expires_at: Optional[float] = None
    topic: str = ""
    enqueued_at: float = 0.0

    def __post_init__(self):
        evt = self.event
        self.event_type = self.event_type or (evt.type if evt else "")
        self.source = self.source or (evt.source if evt else "")
        if not self.payload and evt is not None:
            self.payload = dict(evt.data or {})
        stamp = float(self.created_at or (evt.timestamp if evt else 0.0) or 0.0)
        self.created_at = stamp
        self.enqueued_at = float(self.enqueued_at or stamp)
        if self.expires_at is None and evt is not None and evt.ttl_seconds is not None:
            self.expires_at = stamp + float(evt.ttl_seconds)


QueuedItem = ProactiveCandidate


@dataclass
class ProactiveDecision:
    action: str  # ignore | queue | notify | suppress | silent
    reason: str
    message: str = ""
    policy: str = ""
    priority: str = ""
    outcome: str = ""
    rule: str = ""
    offer_kind: str = ""
    offer_extra: Dict = field(default_factory=dict)


class ProactiveManager:
    """
    EVENT → filter → dedupe → cooldown → policy → IGNORE / QUEUE / NOTIFY
    Does not monitor hardware itself.
    """

    def __init__(
        self,
        *,
        clock: Optional[Clock] = None,
        speak_fn: Optional[SpeakFn] = None,
        busy_check: Optional[BusyCheck] = None,
        preference_get: Optional[PrefGet] = None,
        queue_limit: int = 8,
        max_notifications_per_window: int = 3,
        rate_window_seconds: float = 600.0,
    ):
        self.clock = clock or SystemClock()
        self.speak_fn = speak_fn
        self.busy_check = busy_check or (lambda: False)
        self.preference_get = preference_get or (lambda key, default=None: default)
        cfg = _proactive_config()
        self.queue_limit = int(cfg.get("queue_limit", queue_limit))
        self.max_notifications_per_window = int(
            cfg.get("max_per_window", max_notifications_per_window)
        )
        self.rate_window_seconds = float(
            cfg.get("rate_window_seconds", rate_window_seconds)
        )
        self._lock = threading.RLock()
        self._queue: List[ProactiveCandidate] = []
        self._last_notified: Dict[str, float] = {}
        self._seen_dedup: Set[str] = set()  # active threshold latches
        self._notify_times: List[float] = []
        self.delivered: List[Notification] = []
        self.decisions_log: List[dict] = []
        self.conversation_active = False
        self.suppress_non_urgent = False
        self.important_tts = False
        self._barge_in = False
        self.collab = None
        self.policy = ProactivePolicy()
        self._last_spoken_norm = ""
        self._last_spoken_at = 0.0
        self._last_verdict = None
        self._muted_situation_types: Dict[str, float] = {}
        self.conversation_manager = None
        try:
            from core.situation_engine import SituationEngine

            self.situation_engine = SituationEngine()
            self.situation_engine.set_clock(self.clock.time)
        except Exception:
            self.situation_engine = None
        try:
            from core.attention_manager import AttentionManager

            self.attention_manager = AttentionManager()
        except Exception:
            self.attention_manager = None

    def mute_situation_type(self, mute_key: str, *, until: float) -> None:
        key = str(mute_key or "").strip()
        if key:
            self._muted_situation_types[key] = float(until)

    # ----- config / prefs -----

    def enabled(self) -> bool:
        pref = self.preference_get("proactive.enabled", None)
        if pref is not None:
            return str(pref).lower() in ("1", "true", "yes", "on", "tak")
        return bool(_proactive_config().get("enabled", True))

    def category_enabled(self, category: str) -> bool:
        key = f"proactive.{category}.enabled"
        pref = self.preference_get(key, None)
        if pref is not None:
            return str(pref).lower() in ("1", "true", "yes", "on", "tak")
        # Also accept proactive.system.cpu style handled elsewhere
        cfg = _proactive_config()
        cfg_key = {
            "battery": "battery_enabled",
            "system": "system_enabled",
            "home_assistant": "home_assistant_enabled",
            "network": "network_enabled",
        }.get(category)
        if category == "os_notifications":
            block = cfg.get("os_notifications", {})
            if isinstance(block, dict):
                return bool(block.get("enabled", False))
            return bool(block)
        if cfg_key and cfg_key in cfg:
            return bool(cfg.get(cfg_key, True))
        cats = cfg.get("categories", {})
        if isinstance(cats, dict) and category in cats:
            return bool(cats.get(category, True))
        return True

    def quiet_hours_active(self) -> bool:
        cfg = _proactive_config().get("quiet_hours", {})
        if not isinstance(cfg, dict):
            return False
        pref_on = self.preference_get("proactive.quiet_hours.enabled", None)
        enabled = (
            str(pref_on).lower() in ("1", "true", "yes", "on", "tak")
            if pref_on is not None
            else bool(cfg.get("enabled", False))
        )
        if not enabled:
            return False
        start = str(
            self.preference_get("proactive.quiet_hours.start", None)
            or cfg.get("start", "22:00")
        )
        end = str(
            self.preference_get("proactive.quiet_hours.end", None)
            or cfg.get("end", "07:00")
        )
        now = self.clock.now_local()
        return _in_quiet_hours(now.hour, now.minute, start, end)

    def cooldown_seconds(self, event_type: str) -> float:
        cool = _proactive_config().get("cooldowns", {})
        if isinstance(cool, dict) and event_type in cool:
            return float(cool[event_type])
        defaults = {
            "battery_low": 0,  # latch until charge
            "battery_critical": 0,
            "cpu_high_sustained": 1800,
            "ram_high_sustained": 1800,
            "disk_low": 86400,
            "network_lost": 0,
            "network_restored": 0,
            "ha_unavailable": 1800,
            "ha_restored": 0,
            "reminder_due": 0,
            "calendar_upcoming": 0,
            "incoming_message": 20,
            "os_notification": 20,
        }
        return float(defaults.get(event_type, 600))

    # ----- messaging -----

    def message_for(self, event: Event) -> str:
        """User-facing copy only. Never returns event.type."""
        t = event.type
        d = event.data or {}
        if t in SILENT_STATE_EVENTS:
            spoken, relevant, _reason = self._semantic_state_speech(event)
            return spoken if relevant else ""
        if t == "battery_low":
            pct = int(d.get("percent", 0))
            return f"Masz {pct}% baterii."
        if t == "battery_critical":
            pct = int(d.get("percent", 0))
            return f"Krytycznie niski poziom baterii: {pct}%."
        if t == "battery_charging":
            return "Ładowanie podłączone."
        if t == "cpu_high_sustained":
            return "Procesor jest mocno obciążony od dłuższego czasu."
        if t == "ram_high_sustained":
            return "Pamięć RAM jest mocno zajęta."
        if t == "disk_low":
            free = d.get("free_gb")
            if free is not None:
                return (
                    f"Zostało mało miejsca na dysku ({float(free):.0f} GB). "
                    "Chcesz, żebym sprawdził, co zajmuje najwięcej?"
                )
            return (
                "Zostało mało miejsca na dysku. "
                "Chcesz, żebym sprawdził, co zajmuje najwięcej?"
            )
        if t == "network_lost":
            return "Wygląda na to, że nie ma internetu."
        if t == "network_restored":
            return "Internet wrócił."
        if t == "ha_unavailable":
            return "Home Assistant jest niedostępny."
        if t == "ha_restored":
            return "Home Assistant znów działa."
        if t == "home_arrival":
            return "Witaj w domu."
        if t == "leaving_home":
            return ""
        if t == "windows_update_starting":
            return "Zaczyna się aktualizacja Windows."
        if t == "reminder_due":
            text = d.get("text") or "przypomnienie"
            return f"Przypomnienie: {text}."
        if t == "calendar_upcoming":
            title = d.get("title") or "wydarzenie"
            minutes = d.get("minutes")
            if minutes is not None:
                return f"Za {int(minutes)} minut: {title}."
            return f"Wkrótce: {title}."
        if t in ("incoming_message", "os_notification"):
            try:
                from core.notification_inbox import InboxItem, get_inbox_item
                from core.os_notifications import compose_ask_spoken

                item = get_inbox_item(str(d.get("item_id") or ""))
                if item is None:
                    item = InboxItem(
                        id=str(d.get("item_id") or "incoming"),
                        source="twilio" if str(d.get("channel") or "") == "twilio" else "windows",
                        app=str(d.get("app") or ""),
                        title=str(d.get("title") or ""),
                        body=str(d.get("body") or ""),
                        sender=str(d.get("sender") or ""),
                    )
                return compose_ask_spoken(item)
            except Exception:
                return ""
        if t in (
            "task_completed",
            "task_blocked",
            "analysis_complete",
            "procedure_partial_fail",
            "critical_task_failure",
        ):
            extra = str(d.get("message") or "").strip()
            if extra and looks_like_internal_identifier(extra):
                return ""
            if extra:
                return extra
            try:
                from core.collaboration import compose_proactive_speech

                reason = str(d.get("reason") or t)
                return compose_proactive_speech(reason=reason, extra="")
            except Exception:
                return ""
        extra = str(d.get("message") or "").strip()
        if extra and not looks_like_internal_identifier(extra):
            return extra
        return ""

    def note_barge_in(self) -> None:
        self._barge_in = True

    def suppress_or_defer(self, *, later: bool = False) -> None:
        self.suppress_non_urgent = True
        if not later:
            with self._lock:
                self._queue = [
                    q
                    for q in self._queue
                    if q.event.priority.rank >= Priority.HIGH.rank
                ]

    def clear_barge_in(self) -> None:
        self._barge_in = False

    def _semantic_state_speech(self, event: Event) -> tuple:
        """Optional human speech for otherwise-silent state events."""
        data = dict(event.data or {})
        if event.type != ACTIVE_WINDOW_CHANGED:
            return "", False, "state_only"
        if not (data.get("goal_blocked") or data.get("user_relevance")):
            return "", False, "state_only"
        if data.get("observation_confirmed") is False:
            return "", False, "unconfirmed"
        extra = str(data.get("message") or "").strip()
        if extra and looks_like_internal_identifier(extra):
            extra = ""
        if extra:
            return extra, True, "goal_blocked"
        blocker = str(
            data.get("blocker")
            or data.get("title")
            or data.get("process")
            or "Inne okno"
        ).strip()
        action = str(data.get("blocked_action") or "wpisywanie").strip() or "wpisywanie"
        return (
            f"{blocker} przejął fokus, więc zatrzymałem {action}.",
            True,
            "goal_blocked",
        )

    def category_for(self, event: Event) -> str:
        if event.type.startswith("battery"):
            return "battery"
        if event.type.startswith("ha_"):
            return "home_assistant"
        if event.type.startswith("network"):
            return "network"
        if event.type in ("cpu_high_sustained", "ram_high_sustained", "disk_low"):
            return "system"
        if event.type == "reminder_due":
            return "reminders"
        if event.type in ("calendar_upcoming", "home_arrival"):
            return "calendar"
        if event.type in ("leaving_home", "windows_update_starting"):
            return "home_assistant" if event.type == "leaving_home" else "system"
        if event.type in ("incoming_message", "os_notification"):
            channel = str((event.data or {}).get("channel") or event.source or "")
            if channel in ("twilio", "sms"):
                return "messaging"
            return "os_notifications"
        if event.type in (
            "task_completed",
            "task_blocked",
            "analysis_complete",
            "procedure_partial_fail",
            "critical_task_failure",
        ):
            return "task"
        return event.source or "system"

    # ----- core policy -----

    def handle_event(self, event: Event) -> ProactiveDecision:
        with self._lock:
            self._invalidate_from(event)
            decision = self._decide(event)
            self.decisions_log.append(
                {
                    "type": event.type,
                    "event": event.type,
                    "action": decision.action,
                    "reason": decision.reason,
                    "policy": decision.policy,
                    "priority": decision.priority,
                    "outcome": decision.outcome or (
                        "spoken"
                        if decision.action == "notify"
                        else "deferred"
                        if decision.action == "queue"
                        else "suppressed"
                    ),
                    "rule": decision.rule,
                    "ts": self.clock.time(),
                }
            )
            if len(self.decisions_log) > 200:
                self.decisions_log = self.decisions_log[-200:]
            print(
                f"[PROACTIVE] event={event.type} decision={decision.action} "
                f"policy={decision.policy or '-'} reason={decision.reason}"
                + (
                    f" message={decision.message!r}"
                    if decision.action == "notify" and decision.message
                    else ""
                )
            )
            log_turn(
                "proactive",
                event=event.type,
                decision=decision.action,
                policy=decision.policy,
                reason=decision.reason,
                outcome=decision.outcome,
            )

            if decision.action == "notify":
                self._do_notify(event, decision.message, decision=decision)
            elif decision.action == "queue":
                self._enqueue(event, decision.message, decision=decision)
            return decision

    def _decide(self, event: Event) -> ProactiveDecision:
        now = self.clock.time()
        if event.is_expired(now):
            return ProactiveDecision(
                "ignore",
                "expired",
                policy="IGNORE",
                outcome="suppressed",
                rule="expired",
            )
        situation_decision = self._decide_situation(event)
        if situation_decision is not None:
            return situation_decision
        verdict = evaluate_event(event, self)
        self._last_verdict = verdict
        outcome = verdict.outcome or (
            "spoken"
            if verdict.action == "notify"
            else "deferred"
            if verdict.action == "queue"
            else "suppressed"
        )
        return ProactiveDecision(
            verdict.action,
            verdict.reason,
            message=verdict.message,
            policy=verdict.decision,
            priority=verdict.priority,
            outcome=outcome,
            rule=verdict.rule,
            offer_kind=verdict.offer_kind,
            offer_extra=dict(verdict.offer_extra or {}),
        )

    def _decide_situation(self, event: Event) -> Optional[ProactiveDecision]:
        """SituationEngine → AttentionManager → PolicyVerdict (optional path)."""
        engine = getattr(self, "situation_engine", None)
        attention = getattr(self, "attention_manager", None)
        if engine is None or attention is None:
            return None
        try:
            from core.attention_manager import build_attention_context
            from core.proactive_policy import verdict_from_situation
            from core.situation_types import AttentionDecision

            considered = engine.consider(event, host=self)
            if considered.route == "silent":
                return ProactiveDecision(
                    "silent",
                    considered.reason or "situation_silent",
                    policy="IGNORE",
                    outcome="suppressed",
                    rule=considered.reason,
                )
            if considered.route == "absorb":
                return ProactiveDecision(
                    "silent",
                    considered.reason or "absorbed",
                    policy="IGNORE",
                    outcome="suppressed",
                    rule=considered.reason,
                )
            if considered.route != "situation" or considered.situation is None:
                return None

            situation = considered.situation
            ctx = build_attention_context(self, now=self.clock.time())
            attn = attention.decide(situation, ctx)
            verdict = verdict_from_situation(
                situation,
                attention_decision=attn.decision.value,
                attention_reason=attn.reason,
            )
            self._last_verdict = verdict
            if verdict.action == "notify" and situation is not None:
                engine.mark_presented(situation, now=self.clock.time())
            outcome = verdict.outcome or (
                "spoken"
                if verdict.action == "notify"
                else "deferred"
                if verdict.action == "queue"
                else "suppressed"
            )
            # Preserve AttentionDecision name in reason for tests / diagnostics
            reason = verdict.reason
            if attn.decision is AttentionDecision.INTERRUPT_NOW and verdict.action == "notify":
                reason = attn.reason or reason
            return ProactiveDecision(
                verdict.action,
                reason,
                message=verdict.message,
                policy=verdict.decision,
                priority=verdict.priority,
                outcome=outcome,
                rule=verdict.rule,
                offer_kind=verdict.offer_kind,
                offer_extra=dict(verdict.offer_extra or {}),
            )
        except Exception as exc:
            print(f"[PROACTIVE] situation path failed: {exc}")
            return None

    def tick_future_intents(self) -> List[ProactiveDecision]:
        """Evaluate Future Intent Engine; emit as synthetic high-value events."""
        out: List[ProactiveDecision] = []
        try:
            from core.future_intent import get_future_intent_engine, spoken_for_intent
            from core.world_state import get_world_state

            engine = get_future_intent_engine()
            engine.set_clock(self.clock.time)
            world = dict(get_world_state() or {})
            triggered = engine.evaluate(world=world, idle_seconds=0.0)
            for intent in triggered:
                msg = spoken_for_intent(intent)
                evt = Event(
                    type="future_intent",
                    source="future_intent",
                    data={
                        "message": msg,
                        "intent_id": intent.id,
                        "condition": intent.condition,
                        "user_relevance": True,
                        "high_value": True,
                    },
                    priority=Priority.HIGH,
                    timestamp=self.clock.time(),
                    dedup_key=f"future_intent:{intent.id}",
                    ttl_seconds=600,
                )
                out.append(self.handle_event(evt))
        except Exception as exc:
            print(f"[PROACTIVE] future intent tick failed: {exc}")
        return out

    def _do_notify(self, event: Event, message: str, decision: Optional[ProactiveDecision] = None) -> None:
        safe = guard_user_facing_speech(message)
        try:
            from core.collaboration import looks_internal_tts

            if looks_internal_tts(safe):
                note_internal_event_tts_leak()
                print(
                    f"[PROACTIVE] event={event.type} decision=silent "
                    f"reason=blocked_internal_message"
                )
                return
        except Exception:
            pass
        if not safe:
            print(
                f"[PROACTIVE] event={event.type} decision=silent "
                f"reason=blocked_internal_message"
            )
            return
        now = self.clock.time()
        self._last_notified[event.dedup_key] = now
        self._seen_dedup.add(event.dedup_key)
        self._notify_times.append(now)
        self._last_spoken_norm = " ".join(safe.strip().lower().split())
        self._last_spoken_at = now
        try:
            from core.proactive_policy import mark_consumed

            if event.type == "home_arrival" or (
                decision and decision.rule == "home_arrival"
            ):
                mark_consumed("home_arrival", now=now)
        except Exception:
            pass
        # Clear latch on restore/charge events of related keys handled in invalidate
        if event.type in ("battery_charging", "network_restored", "ha_restored"):
            self._clear_related_latches(event)

        note = Notification(
            message=safe,
            priority=event.priority,
            source=event.source,
            event_id=event.id,
            event_type=event.type,
            topic=event.type,
        )
        self.delivered.append(note)
        offer_kind = str((decision.offer_kind if decision else "") or "")
        offer_extra = dict((decision.offer_extra if decision else None) or {})
        if not offer_kind and event.type in ("incoming_message", "os_notification"):
            offer_kind = "read_inbox"
        if offer_kind:
            try:
                from core.proactive_offer import ProactiveOffer, set_proactive_offer

                data = dict(event.data or {})
                set_proactive_offer(
                    ProactiveOffer(
                        kind=offer_kind,
                        item_id=str(
                            offer_extra.get("item_id") or data.get("item_id") or ""
                        ),
                        spoken_body=str(
                            offer_extra.get("body")
                            or data.get("body")
                            or data.get("title")
                            or ""
                        ).strip(),
                        created_at=now,
                        extra=offer_extra,
                    )
                )
            except Exception:
                pass
        # Prefer ConversationManager proactive ingress when wired by Runtime.
        cm = getattr(self, "conversation_manager", None)
        spoken_via_cm = False
        if cm is not None and hasattr(cm, "handle_proactive_turn"):
            try:
                cm.handle_proactive_turn(
                    safe,
                    source="proactive",
                    data=dict(event.data or {}),
                )
                spoken_via_cm = True
            except Exception as exc:
                print(f"[PROACTIVE] cm speak failed: {exc}")
        if not spoken_via_cm and self.speak_fn:
            try:
                self.speak_fn(safe)
            except Exception as exc:
                print(f"[PROACTIVE] speak failed: {exc}")
                try:
                    from core.session_digest import note_digest_line

                    note_digest_line(safe)
                except Exception:
                    pass
        elif not spoken_via_cm:
            try:
                from core.session_digest import note_digest_line

                note_digest_line(safe)
            except Exception:
                pass

        # Persist reminder delivery only after user-facing speech succeeded.
        if str(getattr(event, "type", "") or "") == "reminder_due":
            rid = str((event.data or {}).get("reminder_id") or "").strip()
            if rid:
                try:
                    from integrations.reminders.store import get_reminder_store

                    get_reminder_store().mark_delivered(rid)
                except Exception:
                    pass

    def _enqueue(
        self,
        event: Event,
        message: str,
        decision: Optional[ProactiveDecision] = None,
    ) -> None:
        safe = guard_user_facing_speech(message)
        if not safe or looks_like_internal_identifier(safe):
            print(
                f"[PROACTIVE] event={event.type} decision=silent "
                f"reason=refuse_queue_without_speech"
            )
            return
        if event.type in SILENT_STATE_EVENTS:
            spoken, relevant, _reason = self._semantic_state_speech(event)
            if not relevant or not spoken:
                print(
                    f"[PROACTIVE] event={event.type} decision=silent reason=state_only"
                )
                return
        # Replace same dedup_key
        self._queue = [q for q in self._queue if q.event.dedup_key != event.dedup_key]
        item = ProactiveCandidate(
            event=event,
            message=safe,
            user_relevance=True,
            reason=(decision.rule if decision and decision.rule else "queued"),
            topic=event.type,
            created_at=self.clock.time(),
        )
        if decision is not None:
            item.payload["_offer_kind"] = decision.offer_kind
            item.payload["_offer_extra"] = dict(decision.offer_extra or {})
            item.payload["_policy"] = decision.policy
        self._queue.append(item)
        try:
            from core.session_digest import note_digest_line

            note_digest_line(safe)
        except Exception:
            pass
        self._queue.sort(key=lambda q: (-q.event.priority.rank, q.enqueued_at))
        while len(self._queue) > self.queue_limit:
            dropped = self._queue.pop()
            print(f"[PROACTIVE] queue overflow drop={dropped.event_type}")

    def _candidate_speakable(self, item: ProactiveCandidate) -> bool:
        if item is None:
            return False
        if not item.user_relevance:
            return False
        msg = guard_user_facing_speech(item.message or "")
        if not msg:
            return False
        if looks_like_internal_identifier(msg):
            return False
        if item.event_type in SILENT_STATE_EVENTS:
            _spoken, relevant, _reason = self._semantic_state_speech(item.event)
            return bool(relevant and _spoken)
        return True

    def _session_defers(self, item: ProactiveCandidate) -> bool:
        if not (self.conversation_active or bool(getattr(self, "important_tts", False))):
            return False
        if item.event.priority.rank >= Priority.HIGH.rank:
            return False
        if item.reason in ("ha_during_light", "leaving_lights"):
            return False
        return True

    def _batch_queue_items(self) -> List[ProactiveCandidate]:
        items = []
        for item in self._queue:
            if item.event.priority.rank >= Priority.HIGH.rank:
                continue
            if item.reason in ("ha_during_light", "leaving_lights"):
                continue
            if item.event_type in ("incoming_message", "os_notification"):
                continue
            items.append(item)
        return items

    def try_deliver(self) -> Optional[Notification]:
        """Deliver highest-priority queued event if allowed. Never speak event.type."""
        with self._lock:
            now = self.clock.time()
            self._purge_expired(now)
            if not self.enabled():
                return None
            if self.busy_check() or bool(getattr(self, "important_tts", False)):
                return None
            if self.conversation_active:
                urgent = [
                    q
                    for q in self._queue
                    if q.event.priority.rank >= Priority.HIGH.rank
                    or q.reason in ("ha_during_light", "leaving_lights")
                ]
                if not urgent:
                    return None
            batch = self._batch_queue_items()
            if (
                not self.conversation_active
                and not self.quiet_hours_active()
                and len(batch) >= 2
                and (not self._queue or self._queue[0] in batch)
            ):
                spoken = group_digest_spoken([q.message or "" for q in batch])
                spoken = guard_user_facing_speech(spoken)
                if spoken:
                    keep = [q for q in self._queue if q not in batch]
                    self._queue = keep
                    fake = Event(
                        type="digest_batch",
                        source="proactive",
                        data={"message": spoken},
                        priority=Priority.NORMAL,
                        timestamp=now,
                        dedup_key=f"digest:{now}",
                        ttl_seconds=60,
                    )
                    self._do_notify(fake, spoken)
                    return self.delivered[-1] if self.delivered else None
            while self._queue:
                if self.quiet_hours_active():
                    critical = [
                        q
                        for q in self._queue
                        if q.event.priority is Priority.CRITICAL
                    ]
                    if not critical:
                        return None
                    item = critical[0]
                else:
                    if not self._rate_allows(now):
                        return None
                    item = self._queue[0]
                    if self._session_defers(item):
                        return None

                self._queue = [q for q in self._queue if q is not item]
                if not self._candidate_speakable(item):
                    print(
                        f"[PROACTIVE] event={item.event_type} decision=silent "
                        f"reason=drop_invalid_queue"
                    )
                    continue
                decision = self._decide(item.event)
                if decision.action in ("suppress", "ignore", "silent"):
                    print(
                        f"[PROACTIVE] event={item.event_type} decision={decision.action} "
                        f"reason={decision.reason}"
                    )
                    continue
                if decision.action == "queue":
                    self._queue.insert(0, item)
                    return None
                spoken = guard_user_facing_speech(
                    item.message or decision.message or ""
                )
                if not spoken:
                    continue
                self._do_notify(item.event, spoken, decision=decision)
                return self.delivered[-1] if self.delivered else None
            return None

    def _purge_expired(self, now: float) -> None:
        kept = []
        for item in self._queue:
            if item.event.is_expired(now):
                print(f"[PROACTIVE] ttl expired type={item.event.type}")
                continue
            kept.append(item)
        self._queue = kept

    def _invalidate_from(self, event: Event) -> None:
        targets = set(event.invalidates or ())
        if event.type == "network_restored":
            targets.add("network_lost")
        if event.type == "ha_restored":
            targets.add("ha_unavailable")
        if event.type == "battery_charging":
            targets.update({"battery_low", "battery_critical"})
            # clear battery latches
            self._seen_dedup = {
                k for k in self._seen_dedup if not k.startswith("system:battery")
            }

        if not targets:
            return
        before = len(self._queue)
        self._queue = [
            q
            for q in self._queue
            if q.event.type not in targets
            and not any(q.event.dedup_key.endswith(t) or t in q.event.dedup_key for t in targets)
        ]
        # Also clear dedup latches for restored conditions
        if event.type in ("network_restored", "ha_restored", "battery_charging"):
            for t in targets:
                self._seen_dedup = {k for k in self._seen_dedup if t not in k}
        if len(self._queue) < before:
            print(f"[PROACTIVE] invalidated queued related to {event.type}")

    def _clear_related_latches(self, event: Event) -> None:
        if event.type == "battery_charging":
            self._seen_dedup = {
                k for k in self._seen_dedup if "battery" not in k
            }

    def _rate_allows(self, now: float) -> bool:
        window = self.rate_window_seconds
        self._notify_times = [t for t in self._notify_times if now - t <= window]
        return len(self._notify_times) < self.max_notifications_per_window

    def queue_size(self) -> int:
        with self._lock:
            return len(self._queue)

    def clear_queue(self) -> None:
        with self._lock:
            self._queue.clear()


def _in_quiet_hours(hour: int, minute: int, start: str, end: str) -> bool:
    def parse(value: str) -> int:
        parts = str(value).strip().split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return h * 60 + m

    now = hour * 60 + minute
    a = parse(start)
    b = parse(end)
    if a == b:
        return False
    if a < b:
        return a <= now < b
    # wraps midnight
    return now >= a or now < b
