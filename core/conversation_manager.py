"""Conversation session orchestration for Elian."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from Brain import ask_ai
from Commands import run_command
from Config import config, get_config_value
from ConversationContext import clear as clear_context
from ConversationFlow import (
    get_effective_idle_timeout,
    handle_forgot_phrase,
    handle_wait_phrase,
    is_forgot_phrase,
    is_wait_phrase,
    on_user_utterance,
)
from Diagnostics import log_turn
from IntentRouter import clear_pending_action, run_intent
from Memory import record_action
from SkillsLoader import run_skills
from TextCorrector import correct_text, should_llm_correct
from WakeWord import is_wake_word, remove_wake_word
from WebActions import normalize_name
from memory.manager import MemoryManager, get_memory_manager

from core.agent import run_agent
from core.agent_safety import is_acknowledgement, is_correction
from core.clarification_policy import (
    FollowUpKind,
    clarification_expired,
    clarification_limit_reached,
    classify_followup,
)
from core.clarification_slots import (
    apply_pending_update,
    build_pending_clarification,
    enrichment_goal,
    is_light_target_pending,
    looks_like_light_slot_answer,
    resolve_light_target_answer,
)
from core.fast_path import run_fast_path
from core.intent_family import (
    IntentClassification,
    IntentFamily,
    classify_intent_family,
    is_clarification_answer,
    is_complete_intent,
)
from core.model_client import ModelClient
from core.text_pipeline import PreparedText, prepare_user_text
from core.tool_result import ToolResult, ToolStatus
from core.tools import ToolRegistry, get_registry

# PHASE8_CM
from core.metrics import get_metrics
from core.response_builder import from_tool_result, never_fake_success
from core.utterance import analyze_utterance, negation_was_lost, strip_trailing_wait_marker


END_PHRASES = [
    "koniec",
    "nieważne",
    "nie dzięki",
    "nie dziekuje",
    "nie dziękuję",
    "to wszystko",
    "dzięki to wszystko",
    "dzieki to wszystko",
    "możesz przestać",
    "mozesz przestac",
    "przestań",
    "przestan",
    "żegnam",
    "zegnam",
    "do widzenia",
]

YES_PHRASES = {
    "tak",
    "tak proszę",
    "tak prosze",
    "potwierdzam",
    "zgadza się",
    "zgadza sie",
    "wykonaj",
}

NO_PHRASES = {
    "nie",
    "anuluj",
    "nie rób tego",
    "nie rob tego",
    "jednak nie",
    "nieważne",
    "niewazne",
}


@dataclass
class HandleResult:
    text: str = ""
    should_speak: bool = False
    active: bool = False
    ended: bool = False
    source: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    paused: bool = False
    interrupted: bool = False
    # Phase 0–2: Brain prose stream session (AiStreamSession); planner never sets this.
    prose_stream: Any = None

    @classmethod
    def silent(cls, *, active=False, ended=False, source=""):
        return cls(active=active, ended=ended, source=source)

    @classmethod
    def speak(cls, text, *, active=True, source="", ended=False, data=None):
        return cls(
            text=str(text or ""),
            should_speak=bool(text),
            active=active,
            ended=ended,
            source=source,
            data=dict(data or {}),
            paused=source == "wait",
        )


def should_end_conversation(text):
    from core.compound_action import split_dismiss_and_action

    text = str(text or "").lower().strip()
    _dismiss, action = split_dismiss_and_action(text)
    if action:
        # Compound: dismiss + new command → do not end session.
        return False
    for phrase in END_PHRASES:
        if text == phrase:
            return True
    first = text.split(" ", 1)[0] if text else ""
    if first in ("koniec", "nieważne", "niewazne") and len(text) < 40:
        return True
    if text.startswith("to wszystko") and len(text) < 30:
        return True
    return False


class ConversationManager:
    """Owns session state and routes utterances; does not implement HA/apps itself."""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        memory=None,
        model: ModelClient | None = None,
        memory_manager: MemoryManager | None = None,
    ):
        self.registry = registry or get_registry()
        self.memory = list(memory or [])
        self.model = model
        self.mem = memory_manager or get_memory_manager()
        self.active = False
        self.pending_tool: dict[str, Any] | None = None
        self.pending_clarification: dict[str, Any] | None = None
        self.pending_executable_goal: dict[str, Any] | None = None
        self.pending_wait_goal: str = ""
        self._session_briefing_done = False
        self._retry_failed_only = False
        self._force_alternate_retry = False
        self.clarification_rounds = 0
        self._pending_slot_classification: IntentClassification | None = None
        self.recent_user: list[str] = []
        self.last_assistant: str = ""
        self.last_observations: list[dict[str, Any]] = []
        self._last_raw_text = ""
        self._last_raw_transcript = ""
        self._last_normalized_transcript = ""
        self._last_normalized_command = ""
        voice_cfg = config.get("voice", {}) if isinstance(config.get("voice"), dict) else {}
        idle_default = voice_cfg.get(
            "session_idle_timeout",
            config.get("settings", {}).get("conversation_idle_timeout", 30),
        )
        self.idle_timeout = float(idle_default)
        self.cancel_requested = False
        try:
            _lang = str(config.get("language") or "en")
        except Exception:
            _lang = "en"
        # Normalize en-US → en for internal session codes.
        if _lang.lower().startswith("en"):
            _lang = "en"
        elif _lang.lower().startswith("pl"):
            _lang = "pl"
        self.session_language: str = _lang
        self._language_locked: bool = False
        self._turn_side_effects: list[dict[str, Any]] = []
        self._turn_device_resolution = None
        self._turn_pronoun_executed = False
        self._confirm_risky = bool(
            get_config_value(
                "computer_control",
                "confirm_risky_actions",
                default=True,
            )
        )
        from core.cognitive_core import CognitiveState
        from core.collaboration import CollaborationStore
        from core.active_goal import ConversationGoalStore

        self.cognitive = CognitiveState()
        self._cognitive_assessment = None
        self._cognitive_guidance = None
        self._user_goal = None
        self._goal_context = None
        self._soft_goal_cancelled = False
        self.collab = CollaborationStore()
        self.goals = ConversationGoalStore()
        self._last_utterance_class = None
        self._experience_session_id = ""

    def listen_timeout(self):
        if not self.active:
            return None
        return get_effective_idle_timeout(self.idle_timeout)

    def deactivate(self, *, reason=""):
        self.active = False
        self.pending_tool = None
        self.pending_clarification = None
        try:
            from core.proactive_policy import note_session_active

            note_session_active(False)
        except Exception:
            pass
        from core.pending_goal import clear_persisted_pending, persist_pending

        if reason == "idle_timeout":
            if self.pending_executable_goal:
                persist_pending(self.pending_executable_goal)
        else:
            clear_persisted_pending()
        self.pending_executable_goal = None
        self._retry_failed_only = False
        self.clarification_rounds = 0
        self._pending_slot_classification = None
        try:
            _lang = str(config.get("language") or "en")
        except Exception:
            _lang = "en"
        if str(_lang).lower().startswith("en"):
            _lang = "en"
        elif str(_lang).lower().startswith("pl"):
            _lang = "pl"
        self.session_language = _lang
        self._language_locked = False
        self.mem.clear_session()
        try:
            self.cognitive.clear()
        except Exception:
            pass
        self._cognitive_assessment = None
        self._cognitive_guidance = None
        self._user_goal = None
        self._goal_context = None
        try:
            self.collab.reset_session()
        except Exception:
            pass
        try:
            self.goals.reset_session()
        except Exception:
            pass
        self._last_utterance_class = None
        self._experience_session_id = ""
        clear_pending_action()
        clear_context()
        if reason != "idle_timeout":
            try:
                from core.active_service import reset_active_service
                from core.world_state import reset_world_state

                reset_active_service()
                reset_world_state()
            except Exception:
                pass
        if reason:
            log_turn("session", event="deactivate", reason=reason)

    def clear_pending_interaction(self, *, reason=""):
        """Clear clarification / confirmation without ending session."""
        self.pending_tool = None
        if isinstance(self.pending_clarification, dict):
            # Wipe response candidates, not only intent metadata.
            self.pending_clarification.clear()
        self.pending_clarification = None
        self.clarification_rounds = 0
        self._pending_slot_classification = None
        self.cancel_requested = False
        self._turn_candidate_final = ""
        self._turn_meta_response = ""
        self._turn_clarification_response = ""
        self._turn_repair_response = ""
        if reason:
            log_turn("session", event="clear_pending", reason=reason)

    def _clear_sticky_conversation_topic(self, *, reason: str = "") -> None:
        """Cancel / new knowledge must not keep prior Osoby/Temat (MJ poison)."""
        try:
            from core.conversation_focus import clear_focus_topic

            focus = getattr(self.mem.short, "focus", None)
            self.mem.short.focus = clear_focus_topic(focus)
            self.mem.short.topic = ""
            if reason:
                log_turn("session", event="clear_topic", reason=reason)
        except Exception:
            pass

    def _reset_turn_response_state(self):
        """Turn-local spoken candidates — never carry META ACK into next web final."""
        self._turn_candidate_final = ""
        self._turn_meta_response = ""
        self._turn_clarification_response = ""
        self._turn_repair_response = ""
        self._turn_fresh_web = False
        self._turn_response_origin = ""
        self._turn_suppress_ack_reuse = True
        self._turn_side_effects: list = []
        self._turn_device_resolution = None
        self._turn_pronoun_executed = False

    def activate(self):
        self.active = True
        self.cancel_requested = False
        self._restore_pending_from_ttl()
        # Cold start / post-deactivate: rehydrate ReminderRule from durable ROUTINE.
        try:
            from core.cognitive_core import rehydrate_reminder_rules_from_memory

            cog = getattr(self, "cognitive", None)
            mem = getattr(self, "mem", None)
            if cog is not None and mem is not None and not cog.enabled_rules():
                rehydrate_reminder_rules_from_memory(cog, mem)
        except Exception:
            pass
        try:
            from core.world_state import note_session

            note_session({"active": True, "language": self.session_language})
        except Exception:
            pass
        try:
            from core.proactive_policy import note_session_active

            note_session_active(True)
        except Exception:
            pass
        try:
            import uuid

            self._experience_session_id = f"sess_{uuid.uuid4().hex[:12]}"
        except Exception:
            self._experience_session_id = ""
        log_turn("session", event="activate")
        # Cheap fail-open due-reminder check (does not block; store I/O only).
        try:
            self.poll_proactive_reminders()
        except Exception:
            pass

    def poll_proactive_reminders(self):
        """Deliver due reminders through the proactive speech path (fail-open)."""
        try:
            from core.proactive_reminders import poll_and_deliver

            return poll_and_deliver(self)
        except Exception:
            return []

    def session_start_briefing(self):
        """Optional first-wake briefing. Off unless proactive.briefing.on_wake."""
        if self._session_briefing_done:
            return None
        try:
            from Config import get_config_value

            briefing = get_config_value("proactive", "briefing", default={}) or {}
            if not isinstance(briefing, dict) or not briefing.get("on_wake"):
                return None
            from core.proactive import ProactiveManager

            probe = ProactiveManager()
            if probe.quiet_hours_active():
                return None
        except Exception:
            return None
        self._session_briefing_done = True
        return self.handle("Podsumuj sytuację.")

    def _set_pending_executable(self, goal):
        from core.pending_goal import (
            clear_persisted_pending,
            normalize_pending_goal_fields,
            persist_pending,
        )

        if goal and isinstance(goal, dict):
            self.pending_executable_goal = normalize_pending_goal_fields(dict(goal)) or dict(goal)
            persist_pending(self.pending_executable_goal)
            try:
                from core.execution_context import (
                    ActiveExecutionContext,
                    set_active_execution,
                )

                ctx = ActiveExecutionContext.from_pending(self.pending_executable_goal)
                set_active_execution(ctx)
            except Exception:
                pass
            return
        self.pending_executable_goal = None
        clear_persisted_pending()
        try:
            from core.execution_context import clear_active_execution

            clear_active_execution()
        except Exception:
            pass

    def _restore_pending_from_ttl(self):
        if self.pending_executable_goal:
            return
        from core.pending_goal import load_persisted_pending, normalize_pending_goal_fields

        loaded = load_persisted_pending()
        if loaded:
            self.pending_executable_goal = normalize_pending_goal_fields(loaded) or loaded

    def request_cancel(self):
        self.cancel_requested = True
        try:
            from core.procedure_engine import cancel_active_procedure

            cancel_active_procedure()
        except Exception:
            pass

    def handle_from_companion(
        self,
        text,
        *,
        device_id: str = "",
        correlation_id: str = "",
    ) -> HandleResult:
        """Phone/LAN terminal ingress — same pipeline as voice, no ToolRegistry bypass.

        correlation_id is optional companion-layer tracing only (phone ↔ turn result).
        """
        raw = str(text or "").strip()
        if not raw:
            data = {}
            if correlation_id:
                data["correlation_id"] = str(correlation_id)
            return HandleResult.speak(
                "Nie usłyszałem komendy.",
                source="companion_empty",
                active=self.active,
                data=data,
            )
        if not self.active:
            self.activate()
        result = self.handle(raw)
        result.data["channel"] = "companion"
        if device_id:
            result.data["companion_device_id"] = str(device_id)
        if correlation_id:
            result.data["correlation_id"] = str(correlation_id)
        return result

    def handle_proactive_turn(
        self,
        message: str,
        *,
        situation=None,
        source: str = "proactive",
        data: dict | None = None,
    ) -> HandleResult:
        """Controlled proactive ingress — same session, not a second conversation."""
        from core.proactive_turn import handle_proactive_turn as _ingress

        return _ingress(
            self,
            message,
            situation=situation,
            source=source,
            data=data,
        )

    def handle(self, text) -> HandleResult:
        """Public turn entry: routes utterance, then fail-open runtime experience log."""
        import time as _time

        started = _time.monotonic()
        try:
            from core.self_improve.runtime_collector import begin_turn_tool_buffer

            begin_turn_tool_buffer()
        except Exception:
            pass
        result = self._handle_turn(text)
        try:
            from core.self_improve.runtime_collector import record_handle_result

            record_handle_result(self, result, started_monotonic=started)
        except Exception:
            pass
        return result

    def _handle_turn(self, text) -> HandleResult:
        raw = text
        if not raw:
            if self.active:
                self.deactivate(reason="idle_timeout")
                print("🔵 Brak odpowiedzi. Powrót do czuwania.")
                return HandleResult.silent(active=False, ended=True, source="idle_timeout")
            return HandleResult.silent(active=False, source="empty")

        self._reset_turn_response_state()
        original_raw = str(raw).strip()
        try:
            from core.hud_events import emit_transcript

            emit_transcript(original_raw)
        except Exception:
            pass
        from WakeWord import extract_wake

        wake = extract_wake(original_raw)
        self._last_raw_transcript = wake.raw_transcript or original_raw
        self._last_normalized_transcript = wake.normalized_transcript or original_raw.lower()
        self._last_raw_text = wake.raw_command or original_raw
        self._last_normalized_command = wake.normalized_command or original_raw.lower()
        text = (wake.normalized_command or original_raw).lower().strip()
        on_user_utterance(text)
        try:
            self.collab.note_user_speech()
        except Exception:
            pass

        if not self.active:
            if wake.wake_detected or is_wake_word(original_raw.lower()):
                get_metrics().record_legacy("wake_text")
                self.activate()
                text = wake.normalized_command or wake.raw_command
                self._last_raw_text = wake.raw_command or original_raw
                if text:
                    print("Komenda po wake word:", text)
                    print(
                        f"[TEXT PIPELINE] raw_command={self._last_raw_text!r} "
                        f"normalized_command={wake.normalized_command or text.lower()!r}"
                    )
                else:
                    return self._speak("Słucham.", source="wake")
            else:
                return HandleResult.silent(active=False, source="standby")

        # Ambient session: bare "Hej Elian" is not a new wake — soft ACK only.
        if wake.wake_detected and not str(wake.raw_command or "").strip():
            try:
                from Config import get_config_value

                ack_on = bool(
                    get_config_value("voice", "listening_ack_on_bare_wake", default=True)
                )
                ack = str(
                    get_config_value("voice", "listening_ack", default="Słucham.") or "Słucham."
                ).strip()
            except Exception:
                ack_on, ack = True, "Słucham."
            if ack_on and ack:
                return self._speak(ack, source="wake_ack")
            return HandleResult.silent(active=True, source="ambient_continue")

        # Deterministic short control utterances — before Agent / clarification merge.
        control = self._handle_control_utterance(text)
        if control is not None:
            return control

        if getattr(self, "pending_wait_goal", "") and not is_wait_phrase(text):
            held = str(self.pending_wait_goal or "").strip()
            if held and text and text.lower() not in held.lower():
                text = f"{held} {text}".strip()
                self._last_raw_text = text
            self.pending_wait_goal = ""

        if is_wait_phrase(text):
            log_turn("handler", handler="wait", result="extended")
            return self._speak(handle_wait_phrase(), source="wait")

        if is_forgot_phrase(text):
            log_turn("handler", handler="forgot", result="extended")
            return self._speak(handle_forgot_phrase(), source="forgot")

        # Phase 12.1 — dismiss/filler + actionable remainder before end-phrase.
        from core.compound_action import actionable_remainder, split_dismiss_and_action

        dismiss_prefix, action_rest = split_dismiss_and_action(text)
        if action_rest and dismiss_prefix:
            log_turn(
                "handler",
                handler="compound_dismiss",
                remainder=action_rest[:80],
            )
            text = action_rest
            orig = str(self._last_raw_text or original_raw)
            low = orig.lower()
            mapped = orig
            if action_rest and action_rest in low:
                idx = low.find(action_rest)
                mapped = orig[idx : idx + len(action_rest)]
            self._last_raw_text = mapped

        # Cognitive Core: "nieważne" with an active intention/rule is invalidation,
        # not session end — resolve after Semantic Executive.
        _end_blocked_by_cognitive = False
        try:
            from core.cognitive_core import has_active_referent, looks_invalidation

            if looks_invalidation(text) and has_active_referent(self.cognitive):
                _end_blocked_by_cognitive = True
        except Exception:
            _end_blocked_by_cognitive = False

        # Active goal cancel ("nieważne") must beat session-end phrases.
        try:
            from core.pending_goal import looks_cancel_pending as _looks_cancel_goal

            self._restore_pending_from_ttl()
            if self.pending_executable_goal and _looks_cancel_goal(text):
                self._set_pending_executable(None)
                try:
                    from core.execution_context import clear_active_execution

                    clear_active_execution()
                except Exception:
                    pass
                self.clear_pending_interaction(reason="cancel_pending_retry")
                log_turn("handler", handler="pending_cancel", result="ok")
                return self._speak("Dobrze, nie próbuję.", source="cancel")
        except Exception:
            pass

        if should_end_conversation(text) and not _end_blocked_by_cognitive:
            if self.pending_tool or self.pending_clarification:
                self.clear_pending_interaction(reason="end_phrase_cancel")
                self._set_pending_executable(None)
                get_metrics().corrections += 1
                return self._speak("Anulowano.", source="cancel")
            self.deactivate(reason="end_phrase")
            print("Koniec rozmowy.")
            return HandleResult.silent(active=False, ended=True, source="end")

        pending = self._handle_pending_confirmation(text)
        if pending is not None:
            return pending

        offer_reply = self._handle_proactive_offer(text)
        if offer_reply is not None:
            return offer_reply

        from core.pending_goal import (
            looks_alternate_utterance,
            looks_cancel_pending,
            looks_incomplete_reminder,
            looks_resume_utterance,
            looks_retry_utterance,
            status_summary_for_pending,
        )

        self._restore_pending_from_ttl()
        if self.pending_executable_goal and looks_cancel_pending(text):
            self._set_pending_executable(None)
            try:
                from core.execution_context import clear_active_execution

                clear_active_execution()
            except Exception:
                pass
            self.clear_pending_interaction(reason="cancel_pending_retry")
            log_turn("handler", handler="pending_cancel", result="ok")
            return self._speak("Dobrze, nie próbuję.", source="cancel")

        if self.pending_executable_goal and looks_incomplete_reminder(text):
            summary = status_summary_for_pending(self.pending_executable_goal)
            log_turn("handler", handler="pending_status", result="ok")
            return self._speak(summary, source="pending_status")

        if self.pending_executable_goal and looks_alternate_utterance(text):
            from core.pending_goal import normalize_pending_goal_fields

            pending = normalize_pending_goal_fields(dict(self.pending_executable_goal)) or dict(
                self.pending_executable_goal
            )
            pending["force_alternate"] = True
            self.pending_executable_goal = pending
            self._force_alternate_retry = True
            log_turn("handler", handler="pending_alternate", result="ok")
            return self._retry_pending_executable_goal()

        if self.pending_executable_goal and (
            looks_resume_utterance(text)
            or looks_retry_utterance(
                text, allow_weak=not (self.pending_tool or self.pending_clarification)
            )
        ):
            return self._retry_pending_executable_goal()

        # Explicit cancel planner/clarification (after confirmation handling)
        if text in ("anuluj", "anuluj to", "przestań planować", "przestan planowac"):
            self.clear_pending_interaction(reason="explicit_cancel")
            self._clear_sticky_conversation_topic(reason="explicit_cancel")
            self._set_pending_executable(None)
            self._soft_goal_cancelled = True
            try:
                self.goals.mark_cancelled()
            except Exception:
                pass
            try:
                from core.execution_context import clear_active_execution

                clear_active_execution()
            except Exception:
                pass
            try:
                from core.procedure_engine import cancel_active_procedure

                cancel_active_procedure()
            except Exception:
                pass
            log_turn("handler", handler="cancel", result="ok")
            return self._speak("Anulowano.", source="cancel")

        # Explicit preference must not be saved as a generic remember_fact.
        teach_reply = self._handle_teach_phrase(text)
        if teach_reply is not None:
            return teach_reply

        procedure_reply = self._handle_procedure_command(text)
        if procedure_reply is not None:
            return procedure_reply

        memory_reply = self._handle_memory_command(text)
        if memory_reply is not None:
            return memory_reply

        proactive_reply = self._handle_proactive_preference(text)
        if proactive_reply is not None:
            return proactive_reply

        prepared = prepare_user_text(self._last_raw_text)
        if not prepared.semantic_text and not prepared.normalized_text:
            return HandleResult.silent(active=True, source="empty_after_prep")

        from core.text_pipeline import looks_truncated_command, recover_full_command

        semantic = prepared.semantic_text
        if looks_truncated_command(semantic):
            recovered = recover_full_command(self._last_raw_text, semantic)
            if recovered and not looks_truncated_command(recovered):
                print(f"[WAKE] recovered truncated command: {recovered[:80]}")
                prepared = PreparedText(
                    raw_text=recovered,
                    normalized_text=recovered,
                    semantic_text=recovered,
                )
                self._last_raw_text = recovered
            else:
                log_turn("handler", handler="truncated_command", result="clarify")
                return self._speak(
                    "Nie złapałem początku. Powtórz całe polecenie.",
                    source="clarify",
                )
        elif prepared.raw_text:
            self._last_raw_text = prepared.raw_text

        wait_stripped, had_wait = strip_trailing_wait_marker(
            prepared.semantic_text.lower()
        )
        from core.utterance_noise import looks_ambient_bleed, looks_noise_like

        noise_probe = wait_stripped if had_wait else prepared.semantic_text
        if looks_ambient_bleed(noise_probe):
            log_turn("handler", handler="ambient_bleed", result="silent")
            return HandleResult.silent(active=True, source="ambient_bleed")
        if (
            looks_noise_like(noise_probe)
            and classify_intent_family(noise_probe).family == IntentFamily.UNKNOWN
            and not self.pending_clarification
            and not self.pending_tool
        ):
            log_turn("handler", handler="noise_unknown", result="silent")
            return HandleResult.silent(active=True, source="noise_unknown")
        analysis = analyze_utterance(wait_stripped if had_wait else prepared.semantic_text)
        if had_wait and wait_stripped:
            wait_family = classify_intent_family(wait_stripped)
            if wait_family.family in (IntentFamily.REMINDER, IntentFamily.CALENDAR) or (
                wait_family.family != IntentFamily.UNKNOWN
                and len((wait_stripped or "").split()) >= 2
            ):
                self.pending_wait_goal = wait_stripped
                log_turn("handler", handler="wait", result="held_goal")
                return self._speak("Dobrze, czekam.", source="wait")
        if analysis.filler_only or (had_wait and len((analysis.normalized or '').split()) <= 3):
            return self._speak("Dobrze, czekam.", source="wait")
        # Phase 11.1 — never let mid-utterance "correction" drop meaning-changing "nie".
        candidate = (
            analysis.normalized
            if (analysis.corrected or analysis.normalized)
            else prepared.semantic_text
        )
        if negation_was_lost(prepared.raw_text or prepared.semantic_text, candidate):
            text = (prepared.semantic_text or prepared.raw_text).lower()
            analysis.corrected = False
        else:
            text = str(candidate or prepared.semantic_text).lower()
        self._intent_hint = analysis.intent_hint
        if analysis.negated_action:
            get_metrics().corrections += 1
            return self._speak("Dobrze, nie robię tego.", source="negation")
        if analysis.explain_only:
            self._intent_hint = "explain"

        self._note_user(text)

        # Pronoun / reference expansion for FastPath + Agent.
        routed_text = self.mem.expand_utterance(text)

        # Phase 12.2 — bind device anaphora to canonical entity BEFORE intent/slots.
        self._bind_turn_device_pronoun(routed_text)

        # Phase 10.2/10.3 — pure acknowledgements / corrections never reach Planner / tools.
        # Phase 11.1 — "Nie rozumiem" is comprehension repair, not ACK.
        from core.conversation_focus import (
            is_comprehension_repair,
            knowledge_reexplain_prompt,
        )
        from core.meta_ack import analyze_meta, meta_ack_phrase
        from core.intent_family import NEGATED_COMPREHENSION
        from core.response_language import (
            ack_for_language,
            detect_utterance_language,
            is_language_only_request,
            parse_language_request,
        )

        # Session language: detect / explicit request (session-only, not set_preference).
        lang_req = parse_language_request(routed_text)
        if lang_req:
            self.session_language = lang_req
            if is_language_only_request(routed_text):
                log_turn("handler", handler="session_language", lang=lang_req)
                return self._speak(ack_for_language(lang_req), source="chat")
        else:
            detected = detect_utterance_language(routed_text)
            if detected in ("pl", "en"):
                # Soft update from user turns unless already explicitly set this session
                # Keep last explicit preference; only bootstrap from speech.
                if not getattr(self, "_language_locked", False):
                    self.session_language = detected

        if lang_req:
            self._language_locked = True

        # Self-model introspection — answers from real orchestrator state, not invented text.
        _self_q = self._try_self_model_answer(routed_text)
        if _self_q is not None:
            log_turn("handler", handler="self_model")
            return _self_q

        _active_domain = ""
        try:
            from core.device_context import get_active_device as _get_active_now

            _dev = _get_active_now(self.mem)
            _active_domain = str(getattr(_dev, "domain", "") or "")
        except Exception:
            _active_domain = ""
        ack_class = classify_intent_family(routed_text, active_domain=_active_domain)
        from core.collaboration import collaboration_overrides_ack

        if is_acknowledgement(ack_class) and not collaboration_overrides_ack(
            getattr(self, "collab", None), routed_text
        ):
            if self.pending_tool or self.pending_clarification:
                self.clear_pending_interaction(reason="acknowledgement")
            log_turn("handler", handler="ack", result="noop")
            return HandleResult.silent(active=True, source="ack")
        if is_correction(ack_class) or ack_class.family == IntentFamily.CORRECTION:
            from core.utterance import extract_correction_target

            remainder = extract_correction_target(routed_text)
            if self.pending_tool or self.pending_clarification:
                self.clear_pending_interaction(reason="correction_reject")
            self._set_pending_executable(None)
            get_metrics().corrections += 1
            if not remainder:
                log_turn("handler", handler="correction", result="rejected_assumption")
                return self._speak(
                    "Jasne, powiedz co miałeś na myśli.",
                    source="correction",
                )
            log_turn(
                "handler",
                handler="correction",
                result="retarget",
                remainder=remainder[:80],
            )
            routed_text = remainder
            self._last_raw_text = remainder
            if self.recent_user:
                self.recent_user[-1] = remainder
            ack_class = classify_intent_family(
                routed_text, active_domain=_active_domain
            )

        from core.semantic_executive import (
            decide_semantics,
            fast_path_permitted,
            log_semantic_trace,
        )

        goal_conv = {}
        try:
            goal_conv = self.goals.semantic_conversation() if getattr(self, "goals", None) else {}
        except Exception:
            goal_conv = {}
        try:
            from core.latency import current_tracker

            _lat = current_tracker()
        except Exception:
            _lat = None
        if _lat:
            _lat.mark("understanding_start")
            _lat.mark("decision_start")
        envelope = decide_semantics(
            routed_text,
            runtime_context=None,
            conversation={
                "last_mode": str(getattr(self, "_last_semantic_mode", "") or ""),
                "last_intent": str(getattr(self, "_last_semantic_intent", "") or ""),
                "pending_executable": bool(self.pending_executable_goal),
                "unresolved_goal": bool(
                    goal_conv.get("unresolved_goal") or self.pending_executable_goal
                ),
                **goal_conv,
                **(self.collab.semantic_conversation() if getattr(self, "collab", None) else {}),
            },
            model=self.model,
            allow_llm=True,
        )
        if _lat:
            _lat.mark("understanding_end")
            _lat.mark("decision_end")
        self._decision_envelope = envelope
        try:
            self._last_semantic_mode = str(getattr(envelope, "mode", "") or "")
            self._last_semantic_intent = str(getattr(envelope, "intent", "") or "")
        except Exception:
            pass
        log_semantic_trace(envelope)
        try:
            from core.utterance_class import classify_utterance

            uclass = classify_utterance(
                routed_text,
                conversation={
                    "pending_executable": bool(self.pending_executable_goal),
                    **goal_conv,
                },
            )
            self._last_utterance_class = uclass
            self.goals.observe_utterance(
                routed_text, envelope=envelope, classification=uclass
            )
        except Exception:
            pass

        # Natural failure dialogue — answer from verified results, not smalltalk.
        if str(getattr(envelope, "intent", "") or "") == "why_failure" or (
            getattr(self, "_last_utterance_class", None)
            and getattr(self._last_utterance_class, "utterance_class", "") == "WHY_FAILURE"
        ):
            why = self._explain_why_failure()
            if why:
                return self._speak(why, source="why_failure")

        # Unresolved awareness: expand goal text before FastPath/Agent.
        try:
            from core.active_goal import enrich_followup_goal_text
            from core.utterance_class import UtteranceClass

            uclass = getattr(self, "_last_utterance_class", None)
            skip_enrich = bool(
                uclass is not None
                and (
                    "state_goal" in (uclass.signals or [])
                    or uclass.utterance_class == UtteranceClass.GOAL.value
                )
            )
            if (
                not skip_enrich
                and uclass is not None
                and uclass.utterance_class
                in (
                    UtteranceClass.CHANGE_PARAMETER.value,
                    UtteranceClass.CONFIRMATION.value,
                    UtteranceClass.RETRY.value,
                    UtteranceClass.REFERENCE.value,
                    UtteranceClass.INFORMATION_REQUEST.value,
                    UtteranceClass.CONTINUATION.value,
                )
            ):
                enriched = enrich_followup_goal_text(
                    routed_text,
                    self.goals.current,
                    classification=uclass,
                )
                if enriched and enriched != routed_text:
                    if uclass.utterance_class == UtteranceClass.REFERENCE.value and uclass.ordinal:
                        picked = self.goals.resolve_ordinal(uclass.ordinal)
                        if picked:
                            enriched = f"Wybierz: {picked}"
                    # Do not rewrite complete FastPath-friendly commands (e.g. "głośniej")
                    # into paraphrases that CommandParser misses — keep original for FastPath.
                    keep_original = False
                    try:
                        info = classify_intent_family(routed_text)
                        if info.is_complete and info.family in (
                            IntentFamily.SYSTEM,
                            IntentFamily.SMART_HOME,
                            IntentFamily.MEDIA,
                            IntentFamily.APP,
                            IntentFamily.WEB,
                        ):
                            keep_original = True
                    except Exception:
                        keep_original = False
                    if not keep_original:
                        routed_text = enriched
                        if self.recent_user:
                            self.recent_user[-1] = enriched
        except Exception:
            pass
        try:
            from core.proactive_policy import note_session_from_envelope

            note_session_from_envelope(envelope)
        except Exception:
            pass
        try:
            vis = classify_intent_family(routed_text)
            if envelope.intent == "current_screen_observation" or (
                vis.family == IntentFamily.VISION and vis.is_complete
            ):
                if self.pending_clarification:
                    self.clear_pending_interaction(reason="vision_new_intent")
        except Exception:
            pass
        try:
            from core.rc_trace import log_rc_trace

            log_rc_trace(
                semantic_started="yes",
                semantic_decision=f"{envelope.mode}/{envelope.intent}",
                salience="deferred",
            )
        except Exception:
            pass
        if envelope.intent == "unclear_utterance" and not envelope.needs_tools:
            return self._speak(
                "Nie jestem pewien, czy dobrze Cię zrozumiałem.",
                source="clarify",
            )
        try:
            self.collab.observe_envelope(envelope, routed_text)
            # P1.4: soft "nieważne" / CHANGE_OF_MIND with an active TemporaryIntention
            # or ReminderRule must reach Cognitive Core (session invalidation) instead
            # of collab cancel_idle — otherwise durable-safe memory invalidation never runs.
            _defer_collab_for_cognitive = False
            try:
                from core.cognitive_core import has_active_referent, looks_invalidation

                if looks_invalidation(routed_text) and has_active_referent(self.cognitive):
                    _defer_collab_for_cognitive = True
            except Exception:
                _defer_collab_for_cognitive = False
            collab_reply = None
            if not _defer_collab_for_cognitive:
                collab_reply = self.collab.try_handle(routed_text, envelope)
            if collab_reply is not None:
                if collab_reply.silent:
                    return HandleResult.silent(active=True, source="collab")
                # Cancel / dismiss must clear PRIMARY + discourse, not leave stale goal.
                try:
                    kind = str(collab_reply.resolved_kind or "")
                    if kind in ("cancel_idle", "cancel_goal", "reject") or str(
                        getattr(envelope, "intent", "") or ""
                    ) == "cancel_goal":
                        self.goals.mark_cancelled()
                        self.clear_pending_interaction(reason="collab_cancel")
                        self._clear_sticky_conversation_topic(reason="collab_cancel")
                        self.pending_executable_goal = None
                        self._user_goal = None
                        self._soft_goal_cancelled = True
                        self.goals.log_trace(
                            "collab_cancelled",
                            pending_executable=self.pending_executable_goal,
                        )
                except Exception:
                    pass
                log_turn(
                    "handler",
                    handler="collab",
                    kind=collab_reply.resolved_kind,
                    result=(collab_reply.message or "")[:120],
                )
                return self._speak(
                    collab_reply.message,
                    source=collab_reply.source,
                    data=collab_reply.data,
                )
        except Exception:
            pass

        # Cognitive Core — after DecisionEnvelope, before FastPath/Agent action choice.
        try:
            from core.latency import current_tracker

            _clat = current_tracker()
        except Exception:
            _clat = None
        if _clat:
            _clat.mark("cognitive_start")
        cognitive_reply = self._run_cognitive_core(routed_text, envelope)
        if _clat:
            _clat.mark("cognitive_end")
        if cognitive_reply is not None:
            return cognitive_reply

        from core.pending_goal import looks_retry_utterance

        talk_turn = envelope.mode in ("TALK", "ANSWER", "WAIT") and not envelope.needs_tools
        if self.pending_executable_goal and not talk_turn:
            allow_weak = not (self.pending_tool or self.pending_clarification)
            retry_sources = (text, routed_text, self._last_raw_text)
            if any(
                looks_retry_utterance(item, allow_weak=allow_weak)
                for item in retry_sources
            ):
                return self._retry_pending_executable_goal()
            if ack_class.is_complete and ack_class.family not in (
                IntentFamily.KNOWLEDGE,
                IntentFamily.CONVERSATION_CONTROL,
                IntentFamily.CORRECTION,
            ):
                self._set_pending_executable(None)

        if not self.pending_executable_goal and looks_retry_utterance(
            routed_text, allow_weak=False
        ):
            return self._speak(
                "Nie mam teraz czynności do ponowienia. Co mam zrobić?",
                source="clarify",
            )

        from core.utterance_noise import looks_ambient_bleed, looks_noise_like

        if looks_ambient_bleed(routed_text):
            log_turn("handler", handler="ambient_bleed", result="silent")
            return HandleResult.silent(active=True, source="ambient_bleed")
        # Clarification answers (e.g. app name "battlefront") must not be
        # discarded as STT noise while a pending question is open.
        if (
            looks_noise_like(routed_text)
            and ack_class.family == IntentFamily.UNKNOWN
            and not self.pending_clarification
            and not self.pending_tool
        ):
            log_turn("handler", handler="noise_unknown", result="silent")
            return HandleResult.silent(active=True, source="noise_unknown")

        meta = analyze_meta(routed_text)
        if meta.is_meta_ack:
            log_turn("handler", handler="meta_talk", result="ack_no_replay")
            return self._speak(meta_ack_phrase(), source="ack", data={"kind": "ack"})
        if meta.is_compound and meta.remainder:
            routed_text = meta.remainder
            agent_text = meta.remainder
            if self.recent_user:
                self.recent_user[-1] = meta.remainder

        # Phase 11.4.2 — "Czy to prawda?" resolves previous factual claim.
        from core.factual_followup import (
            is_claim_verification_followup,
            resolve_claim_referent,
            verification_reply_from_referent,
            verification_search_query,
        )

        if is_claim_verification_followup(routed_text):
            focus = dict(getattr(self.mem.short, "focus", {}) or {})
            referent = resolve_claim_referent(focus)
            if referent and referent.claim_text:
                log_turn(
                    "handler",
                    handler="claim_verify_followup",
                    referent=(referent.claim_text or "")[:120],
                    has_web=bool(referent.has_web_evidence),
                )
                if referent.has_web_evidence:
                    reply = verification_reply_from_referent(
                        referent, lang=self.session_language
                    )
                    return self._speak(
                        reply,
                        source="chat",
                        data={
                            "response_origin": "WEB_SYNTHESIS",
                            "followup_referent": referent.claim_text[:160],
                        },
                    )
                # No prior web evidence — re-check the prior topic via agent/web.
                routed_text = verification_search_query(referent) or referent.topic
                agent_text = routed_text
                if self.recent_user:
                    self.recent_user[-1] = routed_text
            else:
                return self._speak(
                    "Nie mam wcześniejszego twierdzenia do sprawdzenia.",
                    source="chat",
                )

        if (
            meta.is_repair
            or is_comprehension_repair(routed_text)
            or NEGATED_COMPREHENSION.search(routed_text)
        ):
            focus = dict(getattr(self.mem.short, "focus", {}) or {})
            topic = str(focus.get("current_topic") or "").strip()
            if topic:
                prompt = knowledge_reexplain_prompt(focus, routed_text)
                streamed = self._brain_reply(
                    prompt, mode="knowledge", log_handler="knowledge_repair"
                )
                if streamed is not None:
                    return streamed
            # No topic yet — still conversational repair, never silent ACK.
            log_turn("handler", handler="knowledge_repair", result="no_topic")
            return self._speak(
                "Powiedz, o czym mam wytłumaczyć dokładniej.",
                source="chat",
            )

        # Goal-first ActionLoop: formulate UserGoal, soft shortcuts, capability honesty.
        skip_fast_path = False
        agent_text = routed_text
        try:
            from core.action_loop import run_soft_action_loop
            from core.intent_family import classify_intent_family as _classify_for_goal

            loop_class = None
            try:
                from core.device_context import get_active_device as _gad

                _dev = _gad(self.mem)
                loop_class = _classify_for_goal(
                    routed_text,
                    active_domain=str(getattr(_dev, "domain", "") or ""),
                )
            except Exception:
                loop_class = _classify_for_goal(routed_text)

            last_goal = None
            try:
                cancelled = bool(getattr(self, "_soft_goal_cancelled", False))
                try:
                    if str(getattr(self.goals.current, "status", "") or "") == "CANCELLED":
                        cancelled = True
                except Exception:
                    pass
                prev = getattr(self, "_user_goal", None)
                if cancelled:
                    # Soft expectant continue after cancel must ASK, not invent resume.
                    last_goal = {
                        "status": "CANCELLED",
                        "soft_kind": "cancel_goal",
                        "objective": "",
                    }
                elif (
                    prev is not None
                    and hasattr(prev, "to_dict")
                    and str(getattr(prev, "soft_kind", "") or "") != "cancel_goal"
                ):
                    last_goal = prev.to_dict()
            except Exception:
                last_goal = None

            loop = run_soft_action_loop(
                routed_text,
                registry=self.registry,
                memory=self.mem,
                envelope=getattr(self, "_decision_envelope", None),
                assessment=getattr(self, "_cognitive_assessment", None),
                guidance=getattr(self, "_cognitive_guidance", None),
                classification=loop_class,
                pending_executable=(
                    dict(self.pending_executable_goal)
                    if isinstance(self.pending_executable_goal, dict)
                    else None
                ),
                last_observations=list(self.last_observations or []),
                last_assistant=str(self.last_assistant or ""),
                last_user_goal=last_goal,
                confirmed=False,
            )
            if loop.user_goal is not None:
                soft_kind_now = str(getattr(loop.user_goal, "soft_kind", "") or "")
                if soft_kind_now == "cancel_goal" or (loop.data and loop.data.get("cancel")):
                    self._user_goal = None
                    self._soft_goal_cancelled = True
                else:
                    self._user_goal = loop.user_goal
                    self._soft_goal_cancelled = False
                # Domain switch away from light: drop stale brightness clarify.
                try:
                    slots = dict(getattr(loop.user_goal, "slots", None) or {})
                    soft = str(getattr(loop.user_goal, "soft_kind", "") or "")
                    if (
                        slots.get("clear_light_context")
                        or soft in ("volume_down", "volume_up", "media_play", "media_calm")
                    ) and self.pending_clarification:
                        if is_light_target_pending(self.pending_clarification):
                            self.clear_pending_interaction(reason="domain_switch_clear_light")
                except Exception:
                    pass
            if loop.data and loop.data.get("cancel"):
                try:
                    self.goals.mark_cancelled()
                except Exception:
                    try:
                        self.goals.clear()
                    except Exception:
                        pass
                self.clear_pending_interaction(reason="soft_cancel")
                self._clear_sticky_conversation_topic(reason="soft_cancel")
                self.pending_executable_goal = None
                self._user_goal = None
                self._soft_goal_cancelled = True
                try:
                    self.goals.log_trace(
                        "soft_cancelled",
                        pending_executable=self.pending_executable_goal,
                    )
                except Exception:
                    pass
            if loop.data and loop.data.get("ask_once") and loop.data.get("needs_clarification"):
                # Soft light ASK once — seed pending clarification without agent churn.
                try:
                    from core.clarification_slots import build_pending_clarification

                    ug = loop.user_goal
                    self.pending_clarification = build_pending_clarification(
                        goal=str(getattr(ug, "objective", "") or routed_text),
                        question=str(loop.message or "Które światło?"),
                        intent_family="SMART_HOME",
                        action=str(getattr(ug, "soft_kind", "") or "brightness_up"),
                        question_type="light_target",
                        missing_slots=["target"],
                        known_slots=dict(getattr(ug, "slots", None) or {}),
                    )
                    self.clarification_rounds = max(int(self.clarification_rounds or 0), 1)
                except Exception:
                    pass
                log_turn(
                    "handler",
                    handler="action_loop_clarify",
                    soft=str(getattr(loop.user_goal, "soft_kind", "") or ""),
                    result=(loop.message or "")[:120],
                )
                try:
                    self.goals.log_trace(
                        "light_ask_once",
                        pending_executable=self.pending_executable_goal,
                    )
                except Exception:
                    pass
                return self._speak(loop.message, source="clarify", data=dict(loop.data or {}))
            if loop.retry_pending and self.pending_executable_goal:
                return self._retry_pending_executable_goal()
            if loop.classification_override is not None:
                self._pending_slot_classification = loop.classification_override
            if loop.skip_fast_path or loop.force_agent:
                skip_fast_path = True
            if loop.agent_text:
                agent_text = loop.agent_text
            if loop.handled:
                for obs in list(loop.observations or []):
                    if isinstance(obs, dict):
                        self.last_observations.append(obs)
                        self._record_turn_observation(obs)
                        try:
                            tool = str(obs.get("tool") or "")
                            args = dict(obs.get("arguments") or obs.get("args") or {})
                            if not args and isinstance(obs.get("data"), dict):
                                args = dict(obs.get("data") or {})
                            if tool and obs.get("success"):
                                self.mem.note_tool_success(tool, args, obs)
                        except Exception:
                            pass
                if loop.observations:
                    self.last_observations = self.last_observations[-12:]
                try:
                    soft = str(getattr(loop.user_goal, "soft_kind", "") or "")
                    ok = any(
                        isinstance(o, dict) and o.get("success")
                        for o in (loop.observations or [])
                    )
                    if soft and not loop.data.get("cancel"):
                        self._note_goal_tool_result(
                            success=bool(ok),
                            action=soft,
                            result={
                                "observations": [
                                    dict(o)
                                    for o in (loop.observations or [])
                                    if isinstance(o, dict)
                                ],
                                "success": bool(ok),
                            },
                            observations=[
                                dict(o)
                                for o in (loop.observations or [])
                                if isinstance(o, dict)
                            ],
                        )
                except Exception:
                    pass
                try:
                    self.goals.log_trace(
                        "soft_executed",
                        pending_executable=self.pending_executable_goal,
                    )
                except Exception:
                    pass
                log_turn(
                    "handler",
                    handler="action_loop",
                    soft=str(getattr(loop.user_goal, "soft_kind", "") or ""),
                    result=(loop.message or "")[:120],
                )
                return self._speak(
                    loop.message,
                    source=loop.source or "action_loop",
                    data=dict(loop.data or {}),
                )
        except Exception:
            skip_fast_path = False
            agent_text = routed_text

        slot_entities = None
        try:
            guidance = getattr(self, "_cognitive_guidance", None)
            if guidance is not None:
                if getattr(guidance, "enriched_goal", "") and not (
                    getattr(self, "_user_goal", None)
                    and getattr(self._user_goal, "soft_kind", "")
                ):
                    agent_text = str(guidance.enriched_goal)
                if getattr(guidance, "skip_fast_path", False):
                    skip_fast_path = True
        except Exception:
            pass
        try:
            env = getattr(self, "_decision_envelope", None)
            if env is not None:
                mode = str(getattr(env, "mode", "") or "")
                intent = str(getattr(env, "intent", "") or "")
                if mode == "PLAN":
                    skip_fast_path = True
                elif mode == "OBSERVE":
                    skip_fast_path = True
                elif mode == "WAIT":
                    skip_fast_path = True
                elif mode in ("TALK", "ANSWER") and intent in (
                    "creative_humor",
                    "social_smalltalk",
                    "action_discussion",
                    "conversational_followup",
                    "knowledge_explanation",
                    "say_text",
                ):
                    skip_fast_path = True
        except Exception:
            pass

        if self.pending_clarification:
            if clarification_expired(self.pending_clarification):
                self.clear_pending_interaction(reason="clarification_ttl")
            else:
                # Knowledge / new INFORMATION_REQUEST must beat stale SMART_HOME clarify.
                # Never burn clarify_limit on an unrelated question.
                try:
                    from core.conversation_focus import is_new_topic_knowledge
                    from core.utterance_class import UtteranceClass

                    uclass = getattr(self, "_last_utterance_class", None)
                    info_now = classify_intent_family(routed_text)
                    knowledge_switch = (
                        info_now.family == IntentFamily.KNOWLEDGE
                        or (
                            uclass is not None
                            and uclass.utterance_class
                            == UtteranceClass.INFORMATION_REQUEST.value
                        )
                        or is_new_topic_knowledge(routed_text)
                    )
                    pending_fam = str(
                        (self.pending_clarification or {}).get("intent_family") or ""
                    ).upper()
                    if knowledge_switch and pending_fam in (
                        "SMART_HOME",
                        "APP",
                        "MEDIA",
                        "SYSTEM",
                        "",
                    ):
                        self.clear_pending_interaction(reason="knowledge_domain_switch")
                        self._clear_sticky_conversation_topic(
                            reason="knowledge_domain_switch"
                        )
                except Exception:
                    pass
            if self.pending_clarification and clarification_limit_reached(
                self.clarification_rounds
            ):
                # Still only fail the clarify path — never speak limit on knowledge Q.
                try:
                    from core.utterance_class import UtteranceClass

                    uclass = getattr(self, "_last_utterance_class", None)
                    if uclass is not None and uclass.utterance_class in (
                        UtteranceClass.INFORMATION_REQUEST.value,
                        UtteranceClass.GOAL.value,
                    ):
                        self.clear_pending_interaction(reason="clarify_limit_drop_for_new")
                    elif classify_intent_family(routed_text).family in (
                        IntentFamily.KNOWLEDGE,
                        IntentFamily.WEB,
                        IntentFamily.MEDIA,
                        IntentFamily.APP,
                        IntentFamily.SYSTEM,
                    ):
                        self.clear_pending_interaction(reason="clarify_limit_drop_for_new")
                    else:
                        self.clear_pending_interaction(reason="clarification_limit")
                        return self._speak(
                            "Nie udało mi się ustalić. Spróbuj od nowa.",
                            source="clarify_limit",
                        )
                except Exception:
                    self.clear_pending_interaction(reason="clarification_limit")
                    return self._speak(
                        "Nie udało mi się ustalić. Spróbuj od nowa.",
                        source="clarify_limit",
                    )
            if self.pending_clarification:
                pending_q = self.pending_clarification.get("question") or ""
                if is_complete_intent(routed_text):
                    info = classify_intent_family(routed_text)
                    if info.family == IntentFamily.CORRECTION:
                        self.clear_pending_interaction(reason="correction_reject")
                        return self._speak(
                            "Jasne, powiedz co miałeś na myśli.",
                            source="correction",
                        )
                    self.clear_pending_interaction(reason="new_complete_intent")
                    agent_text = routed_text
                elif (
                    is_light_target_pending(self.pending_clarification)
                    and (
                        is_clarification_answer(routed_text, pending_question=pending_q)
                        or looks_like_light_slot_answer(routed_text)
                    )
                ):
                    slot_result = resolve_light_target_answer(
                        self.pending_clarification,
                        routed_text,
                        entities=slot_entities,
                    )
                    if slot_result.status == "resolved" and slot_result.target:
                        known = dict(slot_result.known_slots)
                        self.pending_clarification = apply_pending_update(
                            self.pending_clarification,
                            slot_result,
                            answer=routed_text,
                        )
                        classification = IntentClassification(
                            family=IntentFamily.SMART_HOME,
                            is_complete=True,
                            slots={
                                "implicit_light": True,
                                "desired": "brighter",
                                "domain": "light",
                                **known,
                            },
                            raw_text=str(
                                self.pending_clarification.get("original_goal")
                                or self.pending_clarification.get("goal")
                                or ""
                            ),
                        )
                        agent_text = enrichment_goal(
                            self.pending_clarification, routed_text
                        )
                        skip_fast_path = True
                        # Inject resolved slots so Planner skips identical clarify.
                        self._pending_slot_classification = classification
                    else:
                        previous_q = pending_q
                        self.pending_clarification = apply_pending_update(
                            self.pending_clarification,
                            slot_result,
                            answer=routed_text,
                        )
                        self.clarification_rounds += 1
                        new_q = (
                            slot_result.question
                            or self.pending_clarification.get("question")
                            or previous_q
                        )
                        # No-progress guard: never repeat identical question after an answer.
                        if (
                            new_q.strip().lower() == previous_q.strip().lower()
                            and routed_text.strip()
                        ):
                            new_q = "Podaj nazwę lub pokój lampy."
                            self.pending_clarification["question"] = new_q
                        log_turn(
                            "handler",
                            handler="slot_clarify",
                            result=new_q[:120],
                            progress=slot_result.progress,
                        )
                        return self._speak(new_q, source="clarify")
                elif is_clarification_answer(routed_text, pending_question=pending_q):
                    original = (
                        self.pending_clarification.get("original_goal")
                        or self.pending_clarification.get("goal")
                        or ""
                    )
                    agent_text = (
                        f"{original}\nUżytkownik uzupełnił: {routed_text}"
                        if original
                        else routed_text
                    )
                    skip_fast_path = True
                    self.pending_clarification["started_at"] = time.monotonic()
                else:
                    followup = classify_followup(
                        routed_text,
                        self.pending_clarification,
                        last_assistant=self.last_assistant,
                    )
                    if followup.kind == FollowUpKind.CANCEL:
                        self.clear_pending_interaction(reason="followup_cancel")
                        return self._speak("Anulowano.", source="cancel")
                    if followup.kind == FollowUpKind.STOP:
                        self.clear_pending_interaction(reason="followup_stop")
                        return self._speak("Przerwano.", source="stop")
                    if followup.kind == FollowUpKind.WAIT:
                        return self._speak("Dobrze, czekam.", source="wait")
                    if followup.kind in (FollowUpKind.NEW_INTENT, FollowUpKind.CORRECTION):
                        self.clear_pending_interaction(reason=f"followup_{followup.kind.value}")
                        agent_text = routed_text
                    elif followup.kind == FollowUpKind.NO:
                        self.clear_pending_interaction(reason="followup_no")
                        return self._speak("Anulowano.", source="cancel")
                    elif (
                        is_light_target_pending(self.pending_clarification)
                        and looks_like_light_slot_answer(routed_text)
                    ):
                        slot_result = resolve_light_target_answer(
                            self.pending_clarification,
                            routed_text,
                            entities=slot_entities,
                        )
                        if slot_result.status == "resolved" and slot_result.target:
                            known = dict(slot_result.known_slots)
                            self.pending_clarification = apply_pending_update(
                                self.pending_clarification,
                                slot_result,
                                answer=routed_text,
                            )
                            self._pending_slot_classification = IntentClassification(
                                family=IntentFamily.SMART_HOME,
                                is_complete=True,
                                slots={
                                    "implicit_light": True,
                                    "desired": "brighter",
                                    "domain": "light",
                                    **known,
                                },
                                raw_text=str(
                                    self.pending_clarification.get("original_goal")
                                    or ""
                                ),
                            )
                            agent_text = enrichment_goal(
                                self.pending_clarification, routed_text
                            )
                            skip_fast_path = True
                        else:
                            previous_q = pending_q
                            self.pending_clarification = apply_pending_update(
                                self.pending_clarification,
                                slot_result,
                                answer=routed_text,
                            )
                            self.clarification_rounds += 1
                            new_q = slot_result.question or previous_q
                            if new_q.strip().lower() == previous_q.strip().lower():
                                new_q = "Podaj nazwę lub pokój lampy."
                                self.pending_clarification["question"] = new_q
                            return self._speak(new_q, source="clarify")
                    else:
                        original = (
                            self.pending_clarification.get("original_goal")
                            or self.pending_clarification.get("goal")
                            or ""
                        )
                        agent_text = (
                            f"{original}\nUżytkownik uzupełnił: {routed_text}"
                            if original
                            else routed_text
                        )
                        skip_fast_path = True
                        self.pending_clarification["started_at"] = time.monotonic()

        if not skip_fast_path:
            try:
                from core.context_selection import looks_small_talk

                if looks_small_talk(routed_text) or looks_small_talk(
                    getattr(self, "_last_raw_text", "") or ""
                ):
                    skip_fast_path = True
            except Exception:
                pass

        # Universal HA devices / climate / pronouns — always before Agent, not owned by FastPath.
        executed = self._execute_turn_device_resolution()
        if executed is not None:
            self.clarification_rounds = 0
            return executed

        climate = self._execute_climate_plan(routed_text)
        if climate is not None:
            self.clarification_rounds = 0
            return climate

        try:
            from core.device_context import (
                domain_locked_percent_action,
                followup_intent_slots,
                get_active_device,
                get_last_device_operation,
                sync_active_device_to_conversation_context,
            )

            active = get_active_device(self.mem)
            if active:
                sync_active_device_to_conversation_context(active)
            last_op = get_last_device_operation(self.mem)
            slots = (
                followup_intent_slots(routed_text, active, last_op)
                if active
                else None
            )
            if slots and not slots.get("unsupported"):
                args, err = domain_locked_percent_action(
                    active,
                    int(slots.get("percent") or slots.get("value") or 0),
                    action=str(slots.get("action") or ""),
                )
                if args and not err:
                    result = self.registry.invoke(
                        "set_device_value", args, confirmed=False
                    )
                    observation = result.to_observation()
                    self.last_observations.append(observation)
                    self._record_turn_observation(observation)
                    self.mem.note_tool_success(
                        "set_device_value", args, observation
                    )
                    log_turn(
                        "handler",
                        handler="device_followup",
                        result=result.message[:120],
                        entity_id=active.entity_id,
                        domain=active.domain,
                    )
                    return self._speak(
                        result.message,
                        source="agent",
                        data=observation,
                    )
            if slots and slots.get("unsupported"):
                msg = slots.get("unsupported_message") or (
                    "Tej czynności nie obsługuję dla tego urządzenia."
                )
                return self._speak(msg, source="agent")
        except Exception:
            pass

        if not skip_fast_path:
            # Agent-first: FastPath only when semantics allow AND intent is obvious.
            try:
                from core.semantic_executive import (
                    fast_path_permitted,
                    is_obvious_fast_path_intent,
                )

                env = getattr(self, "_decision_envelope", None)
                if env is None or not fast_path_permitted(routed_text, env):
                    skip_fast_path = True
                elif not is_obvious_fast_path_intent(env, routed_text):
                    skip_fast_path = True
            except Exception:
                skip_fast_path = True

        if not skip_fast_path:
            fast = self._run_fast_path(routed_text)
            if fast is not None:
                self.clarification_rounds = 0
                return fast

        agent_result = self._run_agent(agent_text, raw_text=self._last_raw_text)
        if agent_result is not None:
            return agent_result

        # --- Legacy routes (metrics / last-resort only — not the intelligence) ---
        skill_result = run_skills(routed_text)
        if skill_result:
            record_action("direct_skill", argument=routed_text, result=skill_result)
            log_turn("handler", handler="skill", result=skill_result)
            get_metrics().record_legacy("skills")
            return self._speak(skill_result, source="skill")

        command_result = run_command(routed_text)
        if command_result:
            record_action("direct_command", argument=routed_text, result=command_result)
            log_turn("handler", handler="command", result=command_result)
            get_metrics().record_legacy("commands")
            return self._speak(command_result, source="command")

        intent_result = run_intent(routed_text)
        if intent_result:
            log_turn("handler", handler="intent", result=intent_result)
            get_metrics().record_legacy("intent_router")
            return self._speak(intent_result, source="intent")

        if should_llm_correct(routed_text):
            corrected = correct_text(routed_text)
            if corrected:
                log_turn("correction", original=routed_text, corrected=corrected)
                routed_text = corrected

        streamed = self._brain_reply(routed_text, mode="chat", log_handler="chat")
        if streamed is not None:
            return streamed

        return HandleResult.silent(active=True, source="no_response")

    def _brain_stream_enabled(self) -> bool:
        """True when Brain prose may stream to chunked TTS (planner stays blocking)."""
        import os

        env = str(os.environ.get("ELIAN_STREAM_LLM", "") or "").strip().lower()
        if env in ("0", "false", "no", "off"):
            return False
        if env in ("1", "true", "yes", "on"):
            return True
        try:
            from voice.settings import VoiceSettings

            vs = VoiceSettings.from_config()
            return bool(vs.stream_llm_responses and vs.stream_tts)
        except Exception:
            return False

    def _brain_reply(self, text, *, mode="chat", log_handler="chat"):
        """Chat/knowledge via streaming Brain when enabled; else blocking ask_ai."""
        text = str(text or "").strip()
        if not text:
            return None
        if self._brain_stream_enabled():
            from Brain import begin_ai_stream

            session = begin_ai_stream(
                text,
                memory=self.memory,
                memory_manager=self.mem,
                mode=mode,
                response_language=self.session_language,
            )
            if session is None:
                return None
            log_turn("handler", handler=log_handler, result="stream")
            return HandleResult(
                text="",
                should_speak=True,
                active=True,
                source="chat",
                prose_stream=session,
            )
        answer = ask_ai(
            text,
            memory=self.memory,
            memory_manager=self.mem,
            mode=mode,
            response_language=self.session_language,
        )
        if answer:
            log_turn("handler", handler=log_handler, result=answer[:120])
            return self._speak(answer, source="chat")
        return None

    def _try_self_model_answer(self, text: str):
        """Answer self-awareness questions from SelfModel / orchestrator state."""
        raw = str(text or "").strip().lower()
        if not raw:
            return None
        triggers = (
            "what are you doing",
            "which agents",
            "what agents",
            "why did you create",
            "what are you currently working",
            "what are your limitations",
            "what did you improve",
            "which tools are",
            "tools unavailable",
            "self-development",
            "self development",
            "describe yourself",
            "system status",
            "what are you unable",
            "why did you start another agent",
            "why are you using",
            "what did you roll",
            "co robisz",
            "jakie agenty",
            "jakie masz ograniczenia",
        )
        if not any(t in raw for t in triggers):
            return None
        try:
            from core.multi_agent import get_orchestrator

            orch = get_orchestrator()
            if orch is None:
                return None
            answer = orch.describe_self()
            if "limitation" in raw or "unable" in raw or "ograniczenia" in raw:
                lims = orch.self_model.snapshot().get("known_limitations") or []
                if lims:
                    answer = "Known limitations: " + "; ".join(lims[:8]) + "."
            if "improve" in raw:
                dev = orch.self_model.snapshot().get("development") or {}
                answer = (
                    f"Last retained improvement: {dev.get('last_improvement') or 'none'}. "
                    f"Last rollback: {dev.get('last_rollback') or 'none'}."
                )
            if "self-development" in raw or "self development" in raw:
                dev = orch.self_model.snapshot().get("development") or {}
                answer = (
                    f"Self-development active={dev.get('active')} "
                    f"cycle={dev.get('cycle')} phase={dev.get('phase')} "
                    f"candidate={dev.get('candidate') or '-'}."
                )
            return self._speak(answer, source="chat")
        except Exception:
            return None

    def _speak(self, text, *, source="", data=None):
        from core.response_finalize import finalize_response, is_ack_like

        kind = "answer"
        origin_hint = "MODEL"
        if isinstance(data, dict) and data.get("kind") == "ack":
            kind = "ack"
            origin_hint = "META_ACK"
        elif source in ("ack", "meta"):
            kind = "ack"
            origin_hint = "META_ACK"
        elif source in ("clarify", "clarify_limit"):
            kind = "clarify"
        elif source == "chat":
            origin_hint = "KNOWLEDGE"
        elif source in ("agent", "fast_path", "action_loop", "planner"):
            # Tool execution speech: bare ok/gotowe must not become „Chwila — sprawdzam…”.
            if is_ack_like(text):
                kind = "ack"
                origin_hint = "TOOL_SUCCESS"
        text, origin = finalize_response(
            text,
            lang=getattr(self, "session_language", None) or "en",
            kind=kind,
            origin=origin_hint,
        )
        from core.speech_guard import guard_user_facing_speech

        text = guard_user_facing_speech(text)
        try:
            from core.capability_gate import never_claim_missing_web_if_available

            text = never_claim_missing_web_if_available(self.registry, text)
        except Exception:
            pass
        try:
            from core.followup_policy import apply_followup_policy, has_generic_closer
            from core.metrics import get_metrics as _gm

            text, offered = apply_followup_policy(
                text,
                getattr(self, "_decision_envelope", None),
                success=True,
                goal=getattr(self, "_last_raw_text", "") or "",
            )
            metrics = _gm()
            metrics.followup_offer_total = int(getattr(metrics, "followup_offer_total", 0) or 0) + 1
            if offered:
                metrics.followup_offer_count = int(getattr(metrics, "followup_offer_count", 0) or 0) + 1
            if has_generic_closer(text):
                metrics.generic_callcenter_closer_count = (
                    int(getattr(metrics, "generic_callcenter_closer_count", 0) or 0) + 1
                )
            env = getattr(self, "_decision_envelope", None)
            if env is not None:
                self._last_semantic_mode = str(getattr(env, "mode", "") or "")
        except Exception:
            pass
        if not str(text or "").strip() and kind != "ack":
            return HandleResult.silent(active=self.active, source="speech_guard")
        # Hard action-claim guard:
        # - CLAIM_CHECK / send / create always (promise without ToolResult is forbidden)
        # - other side-effect claims for agent/chat (existing Phase 12 contract)
        # Procedure/memory/fast_path success speech may say "wyłączam" after real work
        # without registry observations on _turn_side_effects — do not strip those.
        if kind == "answer":
            from core.action_claim import (
                CLAIM_CHECK,
                CLAIM_CREATE,
                CLAIM_SEND,
                detect_all_action_claims,
                guard_action_claim,
                honest_no_tool_message,
            )

            claims = detect_all_action_claims(text)
            hard_kinds = {CLAIM_CHECK, CLAIM_SEND, CLAIM_CREATE}
            must_guard = bool(claims) and (
                source in ("agent", "chat")
                or bool(hard_kinds.intersection(claims))
            )
            if must_guard:
                guarded = guard_action_claim(
                    text, self._turn_side_effects, fallback=honest_no_tool_message()
                )
                if guarded.blocked:
                    if not self._turn_pronoun_executed:
                        retry = self._execute_turn_device_resolution()
                        if retry is not None:
                            return retry
                    text = guarded.text
                    data = dict(data or {})
                    data["fake_action_claim_blocked"] = True
                    origin = "ACTION_CLAIM_GUARD"
                    from core.pending_goal import (
                        honest_failed_message,
                        pending_from_unexecuted_goal,
                    )

                    if self.pending_executable_goal:
                        text = honest_failed_message(
                            self.pending_executable_goal.get("steps")
                        )
                    else:
                        built = pending_from_unexecuted_goal(
                            getattr(self, "_last_raw_text", "") or ""
                        )
                        if built:
                            self._set_pending_executable(built)
                            text = honest_failed_message(built.get("steps"))
        lowered_spoken = str(text or "").lower()
        if self.pending_executable_goal is None and "mogę spróbować" in lowered_spoken:
            text = re.sub(
                r"\s*Mogę spróbować[^.]*\.",
                "",
                str(text or ""),
                flags=re.I,
            ).strip()
            if not text:
                from core.action_claim import HONEST_NO_TOOL as _honest

                text = _honest
        # Never reuse previous META ACK as this turn's spoken content.
        if (
            kind == "answer"
            and getattr(self, "_turn_suppress_ack_reuse", False)
            and str(text or "").strip().lower().rstrip(".!")
            in ("okej", "ok", "jasne", "super")
            and source in ("agent", "chat")
        ):
            # Prefer agent-provided synthesis already finalized; if still ACK, soft note.
            if getattr(self, "_turn_fresh_web", False):
                text = "Chwila — streścię świeże wyniki."
                origin = "FALLBACK"
        self._turn_candidate_final = text
        self._turn_response_origin = origin
        if kind == "ack":
            self._turn_meta_response = text
        self.last_assistant = str(text or "")
        self.mem.add_turn("assistant", text)
        try:
            last_user = self.recent_user[-1] if self.recent_user else ""
            web_consensus = ""
            web_query = ""
            if isinstance(data, dict):
                web_consensus = str(data.get("web_consensus") or "")
                web_query = str(data.get("web_query") or "")
            # Persist grounding consensus from last agent observations if present.
            if not web_consensus and self.last_observations:
                from core.web_grounding import authoritative_consensus, rank_hits

                for obs in reversed(self.last_observations):
                    if str(obs.get("tool") or "") != "web_search" or not obs.get("success"):
                        continue
                    payload = obs.get("data") if isinstance(obs.get("data"), dict) else {}
                    ranked = rank_hits(
                        payload.get("ranked_results") or payload.get("results") or [],
                        query=str(payload.get("query") or ""),
                    )
                    web_consensus = authoritative_consensus(ranked) or ""
                    web_query = str(payload.get("query") or "")
                    break
            self.mem.short.update_focus(
                user_text=last_user,
                assistant_text=text,
                entities={
                    key: entity.to_dict()
                    for key, entity in self.mem.short.entities.items()
                },
                web_consensus=web_consensus,
                web_query=web_query,
            )
        except Exception:
            pass
        payload = dict(data or {})
        payload.setdefault("response_origin", origin)
        payload.setdefault("fresh_web", bool(getattr(self, "_turn_fresh_web", False)))
        try:
            from core.hud_events import emit_response

            emit_response(text)
        except Exception:
            pass
        return HandleResult.speak(
            text,
            active=True,
            source=source,
            data=payload,
        )

    def _note_user(self, text):
        self.recent_user.append(text)
        self.recent_user = self.recent_user[-6:]
        self.mem.add_turn("user", text)

    def _record_turn_observation(self, observation):
        if isinstance(observation, dict):
            self._turn_side_effects.append(observation)

    def _bind_turn_device_pronoun(self, text):
        from core.device_context import get_active_device, get_last_device_operation
        from core.device_pronoun import resolve_pronoun_device_action

        try:
            device = get_active_device(self.mem)
            if device is None or not getattr(device, "is_valid", False):
                from core.world_state import active_device_from_world

                device = active_device_from_world() or device
            self._turn_device_resolution = resolve_pronoun_device_action(
                text,
                device,
                last_op=get_last_device_operation(self.mem),
            )
        except Exception:
            self._turn_device_resolution = None
        res = self._turn_device_resolution
        if res and res.pronoun_detected:
            log_turn(
                "handler",
                handler="pronoun_bind",
                entity_id=res.entity_id,
                domain=res.domain,
                action=res.action,
                complete=res.complete,
            )

    def _execute_turn_device_resolution(self):
        res = self._turn_device_resolution
        if res is None or self._turn_pronoun_executed:
            return None
        if not res.pronoun_detected:
            return None
        if res.slots.get("unsupported"):
            self._turn_pronoun_executed = True
            return self._speak(
                res.slots.get("unsupported_message")
                or "Tej czynności nie obsługuję dla tego urządzenia.",
                source="fast_path",
            )
        if not res.complete or not res.tool:
            return None
        from core.device_pronoun import tool_args_from_resolution

        self._turn_pronoun_executed = True
        args = tool_args_from_resolution(res)
        result = self.registry.invoke(res.tool, args, confirmed=False)
        observation = result.to_observation()
        self.last_observations.append(observation)
        self._record_turn_observation(observation)
        self.mem.note_tool_success(res.tool, args, observation)
        log_turn(
            "handler",
            handler="device_pronoun",
            result=(result.message or "")[:120],
            entity_id=res.entity_id,
            domain=res.domain,
            tool=res.tool,
        )
        spoken = ""
        try:
            from core.ha_speech import speak_ha_result

            spoken = speak_ha_result(result, utterance=str(getattr(self, "_last_raw_text", "") or ""))
        except Exception:
            spoken = ""
        if not spoken:
            from core.response_builder import from_tool_result

            spoken = from_tool_result(result, allow_silent=False)
        if not spoken or str(spoken).strip().lower().rstrip(".!") in ("ok", "gotowe", "zrobione"):
            spoken = "Wyłączone." if res.tool == "turn_off_device" else "Zrobione."
            # Prefer honest unverified only when HA did not confirm.
            data = dict(getattr(result, "data", None) or {})
            ha = data.get("ha_verify") if isinstance(data.get("ha_verify"), dict) else {}
            outcome = str(
                data.get("verification") or data.get("ha_outcome") or ha.get("verification") or ""
            ).upper()
            if outcome in ("UNKNOWN", "PARTIAL", "") and res.tool in (
                "turn_off_device",
                "turn_on_device",
                "set_device_value",
            ):
                spoken = (
                    "Wysłałem polecenie, ale nie mogę potwierdzić stanu światła."
                    if not outcome
                    else spoken
                )
                if outcome in ("UNKNOWN", "PARTIAL"):
                    spoken = (
                        "Wysłałem polecenie, ale nie mogę potwierdzić stanu światła."
                    )
        return self._speak(spoken, source="fast_path", data=observation)

    def _execute_climate_plan(self, text):
        from core.climate_control import plan_climate_utterance
        from core.device_context import get_active_device, get_last_device_operation

        try:
            plan = plan_climate_utterance(
                text,
                get_active_device(self.mem),
                get_last_device_operation(self.mem),
            )
        except Exception:
            return None
        if plan is None or not plan.handled:
            return None
        if plan.status_text:
            try:
                from core.local_device_status import classify_local_request, print_source_diagnostic

                src = classify_local_request(text, active_domain="climate")
                if src.kind:
                    print_source_diagnostic(src)
                print(f"[CONTEXT]\ndevice={plan.entity_id or None}\nwebsite=None")
            except Exception:
                pass
            log_turn("handler", handler="climate_status", result=plan.status_text[:120])
            return self._speak(plan.status_text, source="fast_path")
        if plan.clarification and not plan.steps:
            log_turn("handler", handler="climate_clarify", result=plan.clarification[:120])
            return self._speak(plan.clarification, source="clarify")
        if plan.unsupported_message and not plan.steps:
            log_turn(
                "handler",
                handler="climate_unsupported",
                result=plan.unsupported_message[:120],
            )
            return self._speak(plan.unsupported_message, source="fast_path")
        if not plan.steps:
            return None
        messages = []
        last_obs = None
        any_success = False
        for step in plan.steps:
            result = self.registry.invoke(step.tool, step.arguments, confirmed=False)
            observation = result.to_observation()
            data = dict(observation.get("data") or {})
            data["semantic_action"] = step.semantic_action
            if plan.axis:
                data["axis"] = plan.axis
            if plan.target_mode:
                data["target_mode"] = plan.target_mode
            observation["data"] = data
            self.last_observations.append(observation)
            last_obs = observation
            if result.success:
                any_success = True
                self._record_turn_observation(observation)
                note = observation
                # Related Gree switches must not replace canonical climate identity.
                if str(plan.entity_id).startswith("climate.") and str(
                    data.get("resolved_domain") or step.arguments.get("domain") or ""
                ) != "climate":
                    note = dict(observation)
                    note_data = dict(note.get("data") or {})
                    note_data["resolved_entity_id"] = plan.entity_id
                    note_data["resolved_target"] = plan.entity_id
                    note_data["resolved_domain"] = "climate"
                    note["data"] = note_data
                self.mem.note_tool_success(step.tool, step.arguments, note)
            if result.message:
                messages.append(result.message)
        if plan.unsupported_message:
            messages.append(plan.unsupported_message)
        spoken = " ".join(messages).strip()
        log_turn(
            "handler",
            handler="climate_plan",
            result=spoken[:120],
            entity_id=plan.entity_id,
            steps=len(plan.steps),
            success=any_success,
        )
        return self._speak(spoken, source="fast_path", data=last_obs)

    def _handle_control_utterance(self, text):
        """Stop / cancel before clarification merge or Agent."""
        from core.clarification_policy import STOP_PHRASES, classify_followup

        normalized = str(text or "").lower().strip()
        if normalized in STOP_PHRASES:
            self.clear_pending_interaction(reason="stop")
            try:
                from core.procedure_engine import cancel_active_procedure

                cancel_active_procedure()
            except Exception:
                pass
            log_turn("handler", handler="stop", result="pending_cleared")
            return HandleResult.speak(
                "",
                active=True,
                source="stop",
            )

        if self.pending_clarification:
            followup = classify_followup(
                normalized,
                self.pending_clarification,
                last_assistant=self.last_assistant,
            )
            if followup.kind == FollowUpKind.CANCEL:
                self.clear_pending_interaction(reason="niewazne")
                return self._speak("Anulowano.", source="cancel")
        return None

    def _agent_context(self, *, raw_text="", goal_text=""):
        semantic_probe = str(raw_text or goal_text or self._last_raw_text or "")
        knowledge_only = False
        try:
            from core.context_selection import looks_small_talk

            env = getattr(self, "_decision_envelope", None)
            knowledge_only = looks_small_talk(semantic_probe) or (
                env is not None
                and str(getattr(env, "mode", "")) in ("TALK", "ANSWER", "WAIT")
                and not bool(getattr(env, "needs_tools", False))
            )
        except Exception:
            knowledge_only = False
        built = self.mem.build_context(
            self.recent_user[-1] if self.recent_user else "",
            knowledge_only=knowledge_only,
        )
        pending_q = ""
        if self.pending_clarification:
            pending_q = self.pending_clarification.get("question") or ""
        semantic = raw_text or self._last_raw_text or ""
        # Prefer slot-resolved classification (clarification follow-up) over re-parse.
        if self._pending_slot_classification is not None:
            classification = self._pending_slot_classification
            self._pending_slot_classification = None
        else:
            # Classify the planned goal (includes clarification merges), not only
            # a short follow-up like "battlefront" which would look UNKNOWN.
            classify_source = goal_text or semantic
            from core.device_context import get_active_device as _get_active_for_family

            _active = None
            try:
                _active = _get_active_for_family(self.mem)
            except Exception:
                _active = None
            classification = classify_intent_family(
                classify_source,
                active_domain=str(getattr(_active, "domain", "") or ""),
            )
            try:
                from core.computer_semantics import overlay_raw_type_payload

                classification = overlay_raw_type_payload(
                    classification, str(semantic or self._last_raw_text or "")
                )
            except Exception:
                pass
            from core.device_context import (
                followup_intent_slots,
                get_active_device,
                get_last_device_operation,
            )
            from core.device_pronoun import resolve_pronoun_device_action
            from core.intent_family import IntentClassification, IntentFamily

            device = get_active_device(self.mem)
            last_op = get_last_device_operation(self.mem)
            env_now = getattr(self, "_decision_envelope", None)
            talk_locked = (
                env_now is not None
                and str(getattr(env_now, "mode", "")) in ("TALK", "ANSWER", "WAIT")
                and not bool(getattr(env_now, "needs_tools", False))
            )
            res = self._turn_device_resolution
            if talk_locked or (classification.slots or {}).get("compound"):
                res = None
            if res is None and not talk_locked and not (classification.slots or {}).get("compound"):
                res = resolve_pronoun_device_action(
                    semantic or classify_source, device, last_op=last_op
                )
            if res and res.complete and not talk_locked and not (classification.slots or {}).get("compound"):
                classification = IntentClassification(
                    family=IntentFamily.SMART_HOME,
                    is_complete=True,
                    slots=res.to_slots(),
                    raw_text=str(semantic or classify_source),
                )
            elif (
                device
                and device.is_valid
                and not talk_locked
                and not (classification.slots or {}).get("compound")
            ):
                follow_slots = followup_intent_slots(
                    semantic or classify_source,
                    device,
                    last_op,
                )
                if follow_slots:
                    classification = IntentClassification(
                        family=IntentFamily.SMART_HOME,
                        is_complete=not follow_slots.get("unsupported"),
                        slots=follow_slots,
                        raw_text=str(semantic or classify_source),
                    )
                elif (
                    classification.family == IntentFamily.SMART_HOME
                    and (classification.slots or {}).get("implicit_light")
                    and not (classification.slots or {}).get("entity_id")
                    and not (classification.slots or {}).get("target")
                    and device.domain == "light"
                ):
                    slots = dict(classification.slots or {})
                    room = str(
                        slots.get("room_hint")
                        or slots.get("room")
                        or ""
                    ).strip()
                    if not room:
                        try:
                            from core.clarification_slots import extract_room_hint

                            room = extract_room_hint(
                                classification.raw_text or str(semantic or classify_source)
                            )
                            if room:
                                slots["room"] = room
                                slots["room_hint"] = room
                        except Exception:
                            room = ""
                    room_ok = True
                    if room:
                        try:
                            from core.clarification_slots import room_matches_device

                            room_ok = room_matches_device(room, device)
                        except Exception:
                            # Fail closed: named room must not bind a mismatched ActiveDevice.
                            blob = " ".join(
                                [
                                    str(getattr(device, "name", "") or ""),
                                    str(getattr(device, "entity_id", "") or ""),
                                    str(getattr(device, "alias", "") or ""),
                                ]
                            ).lower()
                            room_ok = room.lower() in blob
                    if room_ok:
                        slots.update(
                            {
                                "target": device.entity_id,
                                "entity_id": device.entity_id,
                                "domain": "light",
                                "device_name": device.name,
                                "resolved_context": True,
                            }
                        )
                        classification = IntentClassification(
                            family=IntentFamily.SMART_HOME,
                            is_complete=True,
                            slots=slots,
                            raw_text=classification.raw_text
                            or str(semantic or classify_source),
                        )
                    else:
                        # Keep room slot; leave target unresolved for resolver/clarify.
                        classification = IntentClassification(
                            family=IntentFamily.SMART_HOME,
                            is_complete=False,
                            slots=slots,
                            raw_text=classification.raw_text
                            or str(semantic or classify_source),
                        )
            # Preserve original SMART_HOME goal slots when merge text loses them.
            if (
                self.pending_clarification
                and self.pending_clarification.get("intent_family") == "SMART_HOME"
            ):
                known = dict(self.pending_clarification.get("known_slots") or {})
                if known.get("target") or known.get("entity_id"):
                    classification = IntentClassification(
                        family=IntentFamily.SMART_HOME,
                        is_complete=True,
                        slots={
                            "implicit_light": True,
                            "desired": "brighter",
                            "domain": "light",
                            **known,
                        },
                        raw_text=str(
                            self.pending_clarification.get("original_goal")
                            or classification.raw_text
                        ),
                    )
        lang = getattr(self, "session_language", None) or "pl"
        active = None
        try:
            from core.device_context import get_active_device

            active = get_active_device(self.mem)
        except Exception:
            active = None
        ctx = {
            "intent_hint": getattr(self, "_intent_hint", "") or "",
            "raw_text": semantic,
            "raw_transcript": getattr(self, "_last_raw_transcript", "") or semantic,
            "normalized_transcript": getattr(self, "_last_normalized_transcript", "") or "",
            "raw_command": getattr(self, "_last_raw_text", "") or semantic,
            "normalized_command": getattr(self, "_last_normalized_command", "") or "",
            "intent_classification": classification,
            "intent_family": classification.family.value,
            "intent_family_complete": classification.is_complete,
            "intent_slots": classification.slots,
            "recent_user": list(self.recent_user),
            "last_assistant": self.last_assistant,
            "conversation_focus": dict(getattr(self.mem.short, "focus", {}) or {}),
            "pending_clarification": pending_q,
            "observations": list(self.last_observations[-6:]),
            "memory_context": built.get("text") or "",
            "compact_memory": built.get("compact") or [],
            "preferences": built.get("preferences") or {},
            "entities": built.get("entities") or {},
            "memory_manager": self.mem,
            "clarification_rounds": self.clarification_rounds,
            "session_language": lang,
            "response_language": lang,
            "suppress_previous_answer_replay": False,
            "active_device": active.to_dict() if active else {},
            "last_device_operation": dict(
                getattr(self.mem.short, "last_device_operation", None) or {}
            ),
            "retry_failed_only": bool(getattr(self, "_retry_failed_only", False)),
            "force_alternate": bool(getattr(self, "_force_alternate_retry", False)),
            "failed_fingerprints": list(
                (self.pending_executable_goal or {}).get("failed_fingerprints") or []
            )
            if isinstance(self.pending_executable_goal, dict)
            else [],
            "pending_executable_goal": (
                dict(self.pending_executable_goal)
                if getattr(self, "_retry_failed_only", False)
                and isinstance(self.pending_executable_goal, dict)
                else None
            ),
            "execution_context": (
                dict((self.pending_executable_goal or {}).get("execution_context") or {})
                if getattr(self, "_retry_failed_only", False)
                and isinstance(self.pending_executable_goal, dict)
                else None
            ),
        }
        try:
            from core.action_loop import attach_goal_to_context

            ctx = attach_goal_to_context(ctx, getattr(self, "_user_goal", None))
        except Exception:
            pass
        try:
            from core.salience import select_salient
            from core.context_selection import (
                close_candidates_from_runtime,
                should_enrich_goal,
            )

            candidates = close_candidates_from_runtime()
            envelope = getattr(self, "_decision_envelope", None)
            if envelope is not None and bool(getattr(envelope, "context_requested", False)):
                from core.semantic_executive import refine_with_context

                service = None
                try:
                    from core.active_service import get_active_service

                    service = get_active_service()
                except Exception:
                    service = None
                envelope = refine_with_context(
                    envelope,
                    {
                        "active_service": service.to_dict() if service is not None else {},
                        "active_service_name": getattr(service, "service_name", "") if service else "",
                        "pending_visual": bool(self.pending_clarification),
                        "verified_editor": str(
                            ((ctx.get("entities") or {}).get("application") or "")
                        ),
                        "utterance": semantic_probe or semantic,
                    },
                )
                self._decision_envelope = envelope
            selection = select_salient(
                semantic_probe or semantic,
                close_candidates=candidates,
                envelope=envelope,
            )
            ctx["decision_envelope"] = envelope
            ctx["last_semantic_mode"] = str(getattr(self, "_last_semantic_mode", "") or "")
            ctx["context_selection"] = selection.to_dict()
            try:
                assessment = getattr(self, "_cognitive_assessment", None)
                if assessment is not None:
                    ctx["cognitive_assessment"] = assessment.to_dict()
                    ctx["cognitive_should_act"] = bool(assessment.should_act)
                    ctx["cognitive_importance"] = str(assessment.importance)
            except Exception:
                pass
            try:
                from core.world_state import compact_world_for_llm, refresh_voice_status_readonly

                refresh_voice_status_readonly()
                ctx["world_brief"] = compact_world_for_llm()
            except Exception:
                ctx["world_brief"] = ""
            try:
                ctx["collaboration"] = self.collab.snapshot()
                ctx["collab_store"] = self.collab
            except Exception:
                pass
            if selection.needs_clarification:
                ctx["force_clarification"] = selection.clarification
            if not should_enrich_goal(semantic_probe or semantic, envelope):
                ctx["entities"] = {}
                ctx["active_device"] = {}
                ctx["conversation_focus"] = {}
                ctx.pop("active_service", None)
            elif not selection.include_device:
                ctx["active_device"] = {}
            if selection.include_service and selection.fragments.get("active_service"):
                ctx["active_service"] = selection.fragments.get("active_service")
        except Exception:
            pass
        return ctx

    def _run_cognitive_core(self, text, envelope):
        """Assess understanding after Semantic Executive; guide without tools."""
        try:
            from core.cognitive_core import assess_and_apply, cognitive_blocks_auto_reminder
        except Exception:
            return None
        try:
            guidance = assess_and_apply(
                text,
                envelope=envelope,
                state=self.cognitive,
                memory=self.mem,
                recent_user=list(self.recent_user),
            )
        except Exception:
            return None
        self._cognitive_guidance = guidance
        self._cognitive_assessment = guidance.assessment
        assessment = guidance.assessment
        try:
            from core.rc_trace import log_rc_trace

            log_rc_trace(
                cognitive_intent=str(assessment.intent),
                cognitive_importance=str(assessment.importance),
                cognitive_rationale=str(assessment.rationale_code),
            )
        except Exception:
            pass

        # Block accidental reminder tooling for uncertain future thoughts.
        if cognitive_blocks_auto_reminder(assessment):
            try:
                self._intent_hint = "no_auto_reminder"
            except Exception:
                pass

        if guidance.short_circuit_reply:
            log_turn(
                "handler",
                handler="cognitive",
                rationale=assessment.rationale_code,
                result=guidance.short_circuit_reply[:120],
            )
            return self._speak(
                guidance.short_circuit_reply,
                source="cognitive",
                data={"cognitive": assessment.to_dict()},
            )
        return None

    def _handle_proactive_preference(self, text):
        """Deterministic proactive notification preferences (no LLM)."""
        t = str(text or "").lower().strip()

        try:
            from core.future_intent import (
                get_future_intent_engine,
                looks_like_cancel_future_intent,
            )

            if looks_like_cancel_future_intent(t):
                engine = get_future_intent_engine()
                cancelled = engine.cancel()
                if cancelled:
                    log_turn(
                        "handler",
                        handler="future_intent_cancel",
                        ids=",".join(cancelled),
                    )
                    return self._speak(
                        "Dobrze, już nie muszę o tym pamiętać.",
                        source="future_intent_cancel",
                    )
        except Exception:
            pass

        def set_pref(key, value, reply):
            self.mem.set_preference(key, value, explicit=True, source="user")
            log_turn("handler", handler="proactive_pref", key=key, value=value)
            return self._speak(reply, source="proactive_pref")

        if t in (
            "wyłącz powiadomienia",
            "wylacz powiadomienia",
            "wyłącz powiadomienia eliana",
            "wylacz powiadomienia eliana",
            "nie przeszkadzaj",
            "cichy tryb",
        ):
            return set_pref("proactive.enabled", "false", "Wyłączam powiadomienia.")

        if t in (
            "włącz powiadomienia",
            "wlacz powiadomienia",
            "włącz znowu powiadomienia",
            "wlacz znowu powiadomienia",
        ):
            return set_pref("proactive.enabled", "true", "Włączam powiadomienia.")

        if t in (
            "czytaj powiadomienia windows",
            "włącz czytanie powiadomień",
            "wlacz czytanie powiadomien",
            "czytaj powiadomienia",
        ):
            return set_pref(
                "proactive.os_notifications.enabled",
                "true",
                "Będę czytał powiadomienia Windows, gdy system mi na to pozwoli.",
            )

        if t in (
            "nie czytaj powiadomień windows",
            "nie czytaj powiadomien windows",
            "wyłącz czytanie powiadomień",
            "wylacz czytanie powiadomien",
        ):
            return set_pref(
                "proactive.os_notifications.enabled",
                "false",
                "Nie będę czytał tostów Windows.",
            )

        if t in (
            "nie informuj mnie o procesorze",
            "nie mów o procesorze",
            "nie mow o procesorze",
            "wyłącz alerty cpu",
            "wylacz alerty cpu",
        ):
            return set_pref(
                "proactive.system.cpu",
                "false",
                "Nie będę informował o wysokim CPU.",
            )

        if t in (
            "włącz alerty baterii",
            "wlacz alerty baterii",
            "informuj o baterii",
        ):
            return set_pref(
                "proactive.battery.enabled",
                "true",
                "Włączam alerty baterii.",
            )

        if t in (
            "wyłącz alerty baterii",
            "wylacz alerty baterii",
            "nie informuj o baterii",
        ):
            return set_pref(
                "proactive.battery.enabled",
                "false",
                "Wyłączam alerty baterii.",
            )

        return None

    def _handle_teach_phrase(self, text):
        from core.learned_routines import (
            lookup_learned_routine,
            looks_teach_phrase,
            note_learned_routine,
            parse_teach_phrase,
            teach_spoken,
        )

        if not looks_teach_phrase(text):
            return None
        parsed = parse_teach_phrase(text)
        if parsed is None:
            return None
        src, dest = parsed
        try:
            from core.procedure_engine import define_from_teach, looks_procedure_definition

            if looks_procedure_definition(text):
                result = define_from_teach(
                    src,
                    dest,
                    registry=self.registry,
                )
                if result.status == ToolStatus.NEEDS_CONFIRMATION:
                    self.pending_tool = {
                        "name": "procedure_save",
                        "arguments": dict((result.data or {}).get("arguments") or {}),
                    }
                    log_turn("handler", handler="procedure_conflict", src=src[:80])
                    return self._speak(result.message, source="confirm")
                log_turn(
                    "handler",
                    handler="procedure_define",
                    src=src[:80],
                    success=bool(result.success),
                )
                return self._speak(result.message, source="procedure")
        except Exception:
            pass
        try:
            from memory.vocabulary import remember_phrase

            remember_phrase(src, dest)
        except Exception:
            pass
        try:
            self.mem.remember(
                type="VOCABULARY",
                key=src,
                value=dest,
                provenance={"via": "teach_phrase", "store": "personal_vocabulary.json"},
                ask_on_conflict=False,
            )
        except Exception:
            pass
        existing = lookup_learned_routine(dest)
        if existing is not None:
            note_learned_routine(
                src,
                existing.tool,
                existing.arguments,
                intent=existing.intent,
                force=True,
            )
        log_turn("handler", handler="teach_phrase", src=src[:80], dest=dest[:80])
        return self._speak(teach_spoken(src, dest), source="teach")

    def _handle_procedure_command(self, text):
        try:
            from core.procedure_engine import handle_manage, parse_manage_utterance
        except Exception:
            return None
        managed = parse_manage_utterance(text)
        if managed is None:
            return None
        action, payload = managed
        result = handle_manage(
            action,
            payload,
            registry=self.registry,
            cancel_check=lambda: self.cancel_requested,
        )
        if result.status == ToolStatus.NEEDS_CONFIRMATION:
            self.pending_tool = {
                "name": "procedure_run" if action == "run" else "procedure_save",
                "arguments": dict((result.data or {}).get("arguments") or {}),
            }
            return self._speak(result.message, source="confirm")
        log_turn("handler", handler="procedure_manage", action=action)
        return self._speak(result.message, source="procedure")

    def _note_learned_from_success(self, utterance, tool, data=None, *, intent=""):
        try:
            from core.learned_routines import (
                arguments_for_learn,
                note_learned_routine,
                should_auto_learn,
            )

            if not should_auto_learn(utterance, intent):
                return
            args = arguments_for_learn(tool, data)
            note_learned_routine(utterance, tool, args, intent=intent)
        except Exception:
            pass

    def _handle_memory_command(self, text):
        kind, payload = self.mem.parse_explicit_memory_command(text)
        if kind is None:
            return None

        # "Zapamiętaj, że wolę Spotify…" is PREFERENCE → Agent set_preference, not a free-form fact.
        if kind == "remember" and classify_intent_family(text).family == IntentFamily.PREFERENCE:
            return None

        if kind == "refuse_sensitive":
            return self._speak(
                "Nie zapamiętam haseł, tokenów ani innych tajnych danych.",
                source="memory",
            )

        if kind == "remember":
            ok, message = self.mem.remember_fact(payload.get("text") or "")
            if ok and payload.get("text"):
                self.memory.append(payload["text"])
            return self._speak(message, source="memory")

        if kind == "prefer":
            ok, message = self.mem.set_preference(
                payload.get("key") or "",
                payload.get("value") or "",
            )
            return self._speak(message if ok else message, source="memory")

        if kind == "forget":
            removed = self.mem.forget(payload.get("query") or "")
            total = (
                len(removed["facts"])
                + len(removed["preferences"])
                + len(removed.get("entries") or [])
            )
            if total == 0:
                return self._speak(
                    "Nie znalazłem takiej informacji do usunięcia.",
                    source="memory",
                )
            if total > 3:
                return self._speak(
                    "Znalazłem kilka pasujących wpisów. Sprecyzuj, co mam zapomnieć.",
                    source="memory",
                )
            return self._speak("Zapomniałem.", source="memory")

        if kind == "recall":
            query = payload.get("query") or text
            prefs = self.mem.preferences.as_dict()
            facts = self.mem.search_facts(query, limit=5)
            if facts:
                fact_text = facts[0].get("text") if isinstance(facts[0], dict) else str(facts[0])
                return self._speak(
                    self._answer_from_fact(fact_text, query),
                    source="memory",
                )
            if "muzyk" in query.lower() and prefs.get("music.default_app"):
                return self._speak(
                    f"Preferujesz {prefs['music.default_app']}.",
                    source="memory",
                )
            return self._speak(
                "Nie mówiłeś mi jeszcze o tym.",
                source="memory",
            )

        return None

    def _handle_proactive_offer(self, text):
        from core.proactive_offer import clear_proactive_offer, get_proactive_offer

        offer = get_proactive_offer()
        if offer is None:
            return None
        normalized = normalize_name(text)
        read_yes = normalized in YES_PHRASES or normalized in (
            "odczytaj",
            "przeczytaj",
            "czytaj",
            "daj treść",
            "daj tresc",
            "no to czytaj",
        )
        light_yes = normalized in (
            "zgas",
            "zgasic",
            "wylacz",
            "wylacz ja",
            "wylacz ja",
            "zgas ja",
            "zgasic ja",
        )
        if offer.kind == "read_inbox":
            if read_yes:
                from core.notification_inbox import get_inbox_item
                from core.os_notifications import compose_read_spoken

                item = get_inbox_item(offer.item_id) if offer.item_id else None
                spoken = ""
                if item is not None:
                    spoken = compose_read_spoken(item)
                elif offer.spoken_body:
                    spoken = offer.spoken_body if offer.spoken_body.endswith(".") else offer.spoken_body + "."
                clear_proactive_offer()
                return self._speak(spoken or "Nie mam już treści.", source="proactive_offer")
            if normalized in NO_PHRASES or normalized in ("pomiń", "pomin", "nie teraz"):
                clear_proactive_offer()
                return self._speak("Dobrze, pomijam.", source="proactive_offer")
            return None
        if offer.kind in ("turn_off_light", "tool_confirm"):
            yes = normalized in YES_PHRASES or (
                offer.kind == "turn_off_light" and (read_yes or light_yes)
            )
            if yes:
                tool = str((offer.extra or {}).get("tool") or "turn_off_device")
                args = dict((offer.extra or {}).get("arguments") or {})
                clear_proactive_offer()
                result = self.registry.invoke(tool, args, confirmed=True)
                # Prefer honest tool TTS when present; avoid inventing over "Zgaszona.".
                spoken = str(getattr(result, "message", "") or "").strip()
                if not spoken or spoken.lower().rstrip(".!") in ("ok", "gotowe", "zrobione"):
                    try:
                        from core.ha_speech import speak_ha_result

                        spoken = speak_ha_result(result, utterance=text) or spoken
                    except Exception:
                        pass
                if not spoken or spoken.lower().rstrip(".!") in ("ok", "gotowe", "zrobione"):
                    spoken = (
                        "Wyłączone."
                        if getattr(result, "success", False)
                        else "Wysłałem polecenie, ale nie mogę potwierdzić wyniku."
                    )
                return self._speak(spoken, source="proactive_offer")
            if normalized in NO_PHRASES or normalized in ("pomiń", "pomin", "nie teraz"):
                clear_proactive_offer()
                return self._speak("Dobrze, pomijam.", source="proactive_offer")
            return None
        if read_yes:
            clear_proactive_offer()
            extra_reply = str((offer.extra or {}).get("yes_reply") or "").strip()
            return self._speak(extra_reply or "Dobrze.", source="proactive_offer")
        if normalized in NO_PHRASES or normalized in ("pomiń", "pomin", "nie teraz"):
            clear_proactive_offer()
            return self._speak("Dobrze, pomijam.", source="proactive_offer")
        return None

    def _handle_pending_confirmation(self, text):
        if self.pending_tool is None:
            return None

        normalized = normalize_name(text)
        if normalized in YES_PHRASES:
            pending = self.pending_tool
            self.pending_tool = None
            arguments = dict(pending.get("arguments") or {})
            name = str(pending.get("name") or "")
            if name == "procedure_run":
                arguments["confirmed"] = True
            if name == "procedure_save":
                arguments["replace"] = True
            result = self.registry.invoke(
                name,
                arguments,
                confirmed=True,
            )
            observation = result.to_observation()
            self.last_observations.append(observation)
            self.mem.note_tool_success(
                pending["name"],
                pending.get("arguments") or {},
                observation,
            )
            log_turn("handler", handler="tool_confirmed", result=result.message)
            return self._speak(
                result.message,
                source="tool",
                data=observation,
            )

        if normalized in NO_PHRASES:
            self.pending_tool = None
            return self._speak("Anulowałem.", source="cancel")

        self.pending_tool = None
        return None

    def _retry_pending_executable_goal(self):
        from core.pending_goal import (
            goal_text_from_pending,
            normalize_pending_goal_fields,
            persist_pending,
            status_summary_for_pending,
        )

        pending = dict(self.pending_executable_goal or {})
        if not pending:
            return self._speak(
                "Nie mam już tej czynności do ponowienia.",
                source="agent",
            )

        goal = goal_text_from_pending(pending)
        if not goal:
            try:
                cur = getattr(self.goals, "current", None)
                if cur is not None:
                    goal = str(getattr(cur, "objective", "") or "").strip()
            except Exception:
                goal = ""
        if not goal:
            # Retain pending; speak honest status instead of claiming no action.
            summary = status_summary_for_pending(pending)
            log_turn(
                "handler",
                handler="pending_retry",
                result="status_fallback",
                plan_id=str(pending.get("plan_id") or ""),
            )
            return self._speak(summary, source="pending_status")

        pending = normalize_pending_goal_fields(pending) or pending
        pending["original_goal"] = goal
        pending["original_user_text"] = str(
            pending.get("original_user_text") or goal
        ).strip() or goal
        pending["goal"] = str(pending.get("goal") or goal).strip() or goal
        pending["retry_count"] = int(pending.get("retry_count") or 0) + 1
        if getattr(self, "_force_alternate_retry", False):
            pending["force_alternate"] = True
            pending["replan_count"] = int(pending.get("replan_count") or 0) + 1
        # Keep pending until a successful re-execute clears it via _run_agent.
        self.pending_executable_goal = pending
        persist_pending(pending)
        log_turn(
            "handler",
            handler="pending_retry",
            plan_id=str(pending.get("plan_id") or ""),
            alternate=bool(pending.get("force_alternate")),
        )
        self._retry_failed_only = True
        try:
            return self._run_agent(goal, raw_text=goal)
        finally:
            self._retry_failed_only = False
            self._force_alternate_retry = False

    def _explain_why_failure(self) -> str:
        """Honest WHY_FAILURE reply from ActiveGoal / pending / ActionJournal."""
        try:
            from core.active_goal import explain_failure, journal_latest_failure

            pending = (
                dict(self.pending_executable_goal)
                if isinstance(self.pending_executable_goal, dict)
                else None
            )
            return explain_failure(
                getattr(self.goals, "current", None),
                journal_entry=journal_latest_failure(),
                pending=pending,
            )
        except Exception:
            return "Nie mam zapisanej nieudanej czynności, o którą pytasz."

    def _note_goal_tool_result(
        self,
        *,
        success: bool,
        action: str = "",
        result=None,
        reason: str = "",
        observations=None,
        steps=None,
    ):
        try:
            payload = {}
            if isinstance(result, dict):
                payload = dict(result)
            elif result is not None and hasattr(result, "to_observation"):
                payload = dict(result.to_observation() or {})
            elif result is not None and hasattr(result, "to_dict"):
                payload = dict(result.to_dict() or {})
            elif result is not None:
                payload = {
                    "message": str(getattr(result, "message", "") or ""),
                    "error": str(getattr(result, "error", "") or ""),
                    "success": bool(getattr(result, "success", success)),
                    "tool": str(getattr(result, "tool_name", "") or ""),
                    "tool_name": str(getattr(result, "tool_name", "") or ""),
                    "status": str(
                        getattr(getattr(result, "status", None), "value", "")
                        or getattr(result, "status", "")
                        or ""
                    ),
                }
            obs = observations
            if obs is None and isinstance(payload.get("observations"), list):
                obs = payload.get("observations")
            step_rows = steps
            if step_rows is None and isinstance(payload.get("steps"), list):
                step_rows = payload.get("steps")
            # mark_result enforces evidence; pass the caller's claim through.
            self.goals.mark_result(
                success=bool(success),
                action=str(action or ""),
                result=payload,
                reason=reason or str(payload.get("error") or payload.get("message") or ""),
                observations=obs,
                steps=step_rows,
            )
        except Exception:
            pass

    def _answer_from_fact(self, fact_text, query):
        text = str(fact_text or "")
        # "ulubiona gra to Minecraft" → "Minecraft."
        match = re.search(
            r"ulubion\w+\s+gra\s+to\s+(.+)$",
            text,
            flags=re.I,
        )
        if match:
            return match.group(1).strip().rstrip(".") + "."
        return text if text.endswith(".") else text + "."

    def _run_fast_path(self, text):
        envelope = getattr(self, "_decision_envelope", None)
        try:
            from core.semantic_executive import (
                fast_path_permitted,
                is_obvious_fast_path_intent,
            )

            if envelope is not None and not fast_path_permitted(text, envelope):
                return None
            # Agent-first: FastPath only for obvious deterministic intents.
            if envelope is not None and not is_obvious_fast_path_intent(
                envelope, text
            ):
                return None
        except Exception:
            pass
        # Resume / retry / execute discussed → prefer agent when goal is open.
        try:
            intent = str(getattr(envelope, "intent", "") or "")
            if intent in (
                "resume_unresolved_goal",
                "retry_goal",
                "execute_discussed_goal",
            ) and (
                self.pending_executable_goal
                or (getattr(self, "goals", None) and self.goals.current is not None)
            ):
                return None
            utt = str(getattr(envelope, "utterance_class", "") or "")
            intent = str(getattr(envelope, "intent", "") or "")
            # Discourse follow-ups → Agent.
            if utt in (
                "CONFIRMATION",
                "RETRY",
                "CONTINUATION",
                "REJECTION",
                "WHY_FAILURE",
                "CORRECTION",
                "REFERENCE",
            ):
                return None
            # Soft SMART_HOME without a complete CommandParser hit → Agent.
            domains = {
                str(d).upper()
                for d in (getattr(envelope, "allowed_domains", None) or [])
            }
            if (
                utt in ("GOAL", "CHANGE_PARAMETER", "ACTION_REQUEST")
                and (
                    "SMART_HOME" in domains
                    or intent in ("smart_home_control", "climate_control")
                )
            ):
                try:
                    from CommandParser import parse_commands

                    cmds = parse_commands(text) or []
                    if len(cmds) != 1:
                        return None
                    cmd = cmds[0]
                    tool = (
                        cmd.get("tool")
                        if isinstance(cmd, dict)
                        else getattr(cmd, "action", None) or getattr(cmd, "tool", None)
                    )
                    if not tool:
                        return None
                except Exception:
                    return None
        except Exception:
            pass
        outcome = run_fast_path(
            text,
            self.registry,
            confirmed=False,
            memory=self.mem,
            envelope=envelope,
        )
        if outcome is None:
            return None

        if isinstance(outcome, ToolResult):
            # Soft fall-through: capability/unavailable / schema mismatch must not
            # trap the user in a FastPath failure loop — let Agent/ActionLoop retry.
            err = str(getattr(outcome, "error_code", "") or "")
            err_msg = str(getattr(outcome, "error", "") or getattr(outcome, "error_message", "") or "")
            if err in ("CAPABILITY_UNAVAILABLE", "CAPABILITY_BLOCKED", "TOOL_UNAVAILABLE"):
                return None
            if not outcome.success and (
                err_msg == "invalid_arguments"
                or "niedozwolone argumenty" in str(outcome.message or "").lower()
            ):
                return None
            if outcome.status == ToolStatus.NEEDS_CONFIRMATION:
                self.pending_tool = {
                    "name": outcome.tool_name,
                    "arguments": dict((outcome.data or {}).get("arguments") or {}),
                }
                log_turn("handler", handler="tool_confirm", result=outcome.message)
                return self._speak(outcome.message, source="confirm")
            observation = outcome.to_observation()
            self.last_observations.append(observation)
            self._record_turn_observation(observation)
            self._note_goal_tool_result(
                success=bool(outcome.success),
                action=str(outcome.tool_name or ""),
                result=outcome,
            )
            if outcome.success:
                self._note_learned_from_success(
                    text,
                    str(outcome.tool_name or ""),
                    dict(outcome.data or {}),
                    intent=str(getattr(envelope, "intent", "") or ""),
                )
            log_turn("handler", handler="fast_path", result=outcome.message)
            spoken = self._jarvis_fast_path_speech(outcome, text)
            if spoken == "":
                get_metrics().silent_success += 1
                self.last_assistant = ""
                return HandleResult.speak("", active=True, source="fast_path", data=observation)
            return self._speak(
                spoken,
                source="fast_path",
                data=observation,
            )

        log_turn("handler", handler="fast_path", result=str(outcome)[:120])
        return self._speak(str(outcome), source="fast_path")

    def _jarvis_fast_path_speech(self, outcome, text):
        from core.jarvis_response import polish_spoken
        from core.response_builder import from_tool_result

        tool = str(getattr(outcome, "tool_name", "") or "")
        data = dict(getattr(outcome, "data", None) or {})
        if tool in (
            "turn_on_device",
            "turn_off_device",
            "set_device_value",
            "get_device_state",
        ) or data.get("ha_outcome") or data.get("verification"):
            from core.ha_speech import speak_ha_result

            spoken = speak_ha_result(outcome, utterance=text)
            if spoken:
                return polish_spoken(spoken)
        if tool in ("get_situation_briefing", "list_os_notifications", "get_phone_status"):
            return polish_spoken(str(outcome.message or "").strip())
        spoken = from_tool_result(outcome, allow_silent=False)
        if spoken == "":
            spoken = str(outcome.message or "").strip()
        return polish_spoken(spoken)

    def _run_agent(self, text, *, raw_text=""):
        from core.conversation_focus import (
            enrich_goal_with_focus,
            is_new_topic_knowledge,
        )

        if is_new_topic_knowledge(text) or is_new_topic_knowledge(raw_text or ""):
            self._clear_sticky_conversation_topic(reason="new_knowledge")
        agent_goal = enrich_goal_with_focus(
            text,
            getattr(self.mem.short, "focus", None),
        )
        try:
            from core.latency import current_tracker

            _plat = current_tracker()
        except Exception:
            _plat = None
        if _plat:
            _plat.mark("planner_start")
            _plat.mark("tool_start")
        result = run_agent(
            agent_goal,
            registry=self.registry,
            model=self.model,
            context=self._agent_context(raw_text=raw_text, goal_text=agent_goal),
            confirmed=False,
            cancel_check=lambda: self.cancel_requested,
        )
        if _plat:
            _plat.mark("planner_end")
            _plat.mark("tool_end")
        if result is None:
            return None

        pending_goal = getattr(result, "pending_executable_goal", None)
        if pending_goal:
            self._set_pending_executable(dict(pending_goal))
        elif getattr(result, "plan_status", "") == "COMPLETED" or (
            result.success and getattr(result, "plan_id", "")
        ):
            self._set_pending_executable(None)
        elif not result.success and not self.pending_executable_goal:
            from core.pending_goal import pending_from_unexecuted_goal

            built = pending_from_unexecuted_goal(raw_text or text)
            if built:
                self._set_pending_executable(built)

        skip_goal_note = bool(
            getattr(result, "needs_clarification", False)
            or getattr(result, "needs_confirmation", False)
            or getattr(result, "needs_knowledge", False)
        )
        if skip_goal_note:
            try:
                if self.goals.current is not None:
                    self.goals.mark_in_progress(
                        action=(
                            "clarify"
                            if getattr(result, "needs_clarification", False)
                            else "pending"
                        )
                    )
            except Exception:
                pass
        else:
            try:
                from core.active_goal import has_goal_completion_evidence

                last_tool = ""
                obs = list(result.observations or [])
                if obs:
                    last_tool = str((obs[-1] or {}).get("tool") or "")
                step_rows = (
                    list(getattr(result, "steps", None) or [])
                    if isinstance(getattr(result, "steps", None), list)
                    else []
                )
                reason = ""
                if not result.success:
                    reason = str(result.message or getattr(result, "error", "") or "")
                payload = {
                    "message": str(result.message or ""),
                    "success": bool(result.success),
                    "plan_status": str(getattr(result, "plan_status", "") or ""),
                    "steps": step_rows,
                    "observations": [dict(o) for o in obs if isinstance(o, dict)],
                    "completed_step_ids": list(
                        getattr(result, "completed_step_ids", None) or []
                    )
                    if isinstance(getattr(result, "completed_step_ids", None), list)
                    else [],
                }
                action = last_tool or str(getattr(result, "plan_id", "") or "agent")
                evidenced = has_goal_completion_evidence(
                    result=payload,
                    observations=obs,
                    steps=step_rows,
                    action=action,
                )
                if bool(result.success) and not evidenced:
                    reason = reason or "no_completion_evidence"
                self._note_goal_tool_result(
                    success=bool(result.success),
                    action=action,
                    result=payload,
                    reason=reason,
                    observations=obs,
                    steps=step_rows,
                )
                if self.pending_executable_goal and not result.success:
                    # Keep goal UNRESOLVED while retry is available.
                    if self.goals.current is not None:
                        self.goals.current.status = "UNRESOLVED"
            except Exception:
                pass

        if result.observations:
            self.last_observations.extend(result.observations)
            self.last_observations = self.last_observations[-12:]
            for item in result.observations:
                self._record_turn_observation(item)
            if result.success:
                last = dict(result.observations[-1] or {})
                if last.get("success"):
                    self._note_learned_from_success(
                        raw_text or text,
                        str(last.get("tool") or ""),
                        dict(last.get("data") or {}),
                    )

        if result.needs_confirmation and result.pending_tool:
            self.pending_tool = dict(result.pending_tool)
            log_turn("handler", handler="agent_confirm", result=result.message)
            return self._speak(result.message, source="confirm")

        if result.needs_knowledge:
            topic = text
            focus = dict(getattr(self.mem.short, "focus", {}) or {})
            if focus.get("current_topic"):
                from core.conversation_focus import enrich_goal_with_focus

                topic = enrich_goal_with_focus(text, focus)
            streamed = self._brain_reply(
                topic, mode="knowledge", log_handler="knowledge"
            )
            if streamed is not None:
                return streamed
            return self._speak(
                "Sztuczna inteligencja to dziedzina informatyki badająca maszyny uczące się.",
                source="chat",
            )

        if result.needs_clarification:
            self.clarification_rounds += 1
            question = result.clarification_question or result.message
            original_goal = str(text or "").split("\nUżytkownik uzupełnił:")[0].strip()
            raw_meta = {}
            # PlannerResult may expose decision raw via steps / message only —
            # recover SMART_HOME defaults from current classification.
            classification = classify_intent_family(original_goal)
            if classification.family == IntentFamily.SMART_HOME or "świat" in (question or "").lower():
                raw_meta = {
                    "implicit_light": True,
                    "intent_family": "SMART_HOME",
                    "action": "brightness_increase",
                    "question_type": "light_target",
                    "missing_slots": ["target"],
                }
            # Preserve prior known slots / candidates when re-clarifying.
            prior = self.pending_clarification or {}
            self.pending_clarification = build_pending_clarification(
                goal=original_goal or str(prior.get("original_goal") or ""),
                question=question,
                intent_family=str(
                    prior.get("intent_family")
                    or raw_meta.get("intent_family")
                    or classification.family.value
                ),
                action=str(prior.get("action") or raw_meta.get("action") or ""),
                question_type=str(
                    prior.get("question_type") or raw_meta.get("question_type") or ""
                ),
                missing_slots=list(
                    prior.get("missing_slots") or raw_meta.get("missing_slots") or []
                ),
                known_slots=dict(prior.get("known_slots") or {}),
                candidate_entities=list(prior.get("candidate_entities") or []),
                attempts=int(prior.get("attempts") or 0) + 1,
                round_num=self.clarification_rounds,
                raw=raw_meta,
            )
            log_turn("handler", handler="agent_clarify", result=result.message)
            get_metrics().record_agent(success=False, clarification=True)
            return self._speak(result.message, source="clarify")

        self.pending_clarification = None
        self.clarification_rounds = 0

        # Phase 10.5 — dead-end unknown_guard / empty talk → Brain conversation.
        if (
            getattr(result, "source", "") == "unknown_guard"
            and not (result.observations or [])
            and not (result.steps or [])
        ):
            from core.utterance_noise import looks_noise_like

            if looks_noise_like(raw_text or text):
                log_turn("handler", handler="noise_unknown", result="unknown_guard")
                return self._speak(
                "Nie jestem pewien, czy dobrze Cię zrozumiałem.",
                source="chat",
            )
            log_turn("handler", handler="ai_first_fallthrough", result="ask_ai")
            streamed = self._brain_reply(
                raw_text or text, mode="chat", log_handler="ai_first_fallthrough"
            )
            if streamed is not None:
                return streamed
            # Soft conversational fallback — never "Nie zrozumiałem."
            return self._speak(
                result.message
                or "Okej — o czym chcesz pogadać albo co mam zrobić?",
                source="chat",
            )

        if not result.message:
            return None

        spoken = never_fake_success(result.message, result.observations or [])
        try:
            from core.jarvis_response import polish_spoken

            spoken = polish_spoken(spoken)
        except Exception:
            pass
        # Strip legacy dead-end phrasing if it somehow appears.
        if "nie zrozumiałem" in spoken.lower() and not (result.observations or []):
            streamed = self._brain_reply(
                raw_text or text, mode="chat", log_handler="chat"
            )
            if streamed is not None:
                return streamed
            spoken = "Okej — o czym chcesz pogadać albo co mam zrobić?"

        get_metrics().record_agent(
            success=bool(getattr(result, "success", True)),
            steps=len(result.steps),
        )
        log_turn("handler", handler="agent", result=spoken[:120])
        if getattr(result, "fresh_web_observation_this_turn", False) or getattr(
            result, "grounded_in_web", False
        ):
            self._turn_fresh_web = True
        origin = getattr(result, "response_origin", "") or ""
        self._turn_response_origin = origin
        # Compact answer-quality signals for runtime experience logging (no raw text).
        vacuous_reply = False
        false_success_blocked = False
        try:
            from core.active_goal import is_vacuous_goal_reply

            vacuous_reply = bool(is_vacuous_goal_reply(result.message))
        except Exception:
            vacuous_reply = not bool(str(result.message or "").strip())
        try:
            lr = {}
            if self.goals.current is not None:
                lr = dict(getattr(self.goals.current, "last_result", None) or {})
            false_success_blocked = bool(lr.get("false_success_blocked"))
        except Exception:
            false_success_blocked = False
        return self._speak(
            spoken,
            source="agent",
            data={
                "steps": len(result.steps),
                "plan_id": getattr(result, "plan_id", "") or "",
                "plan_status": getattr(result, "plan_status", "") or "",
                "response_origin": origin,
                "fresh_web": bool(getattr(result, "fresh_web_observation_this_turn", False)),
                "agent_success": bool(getattr(result, "success", True)),
                "grounded_in_web": bool(getattr(result, "grounded_in_web", False)),
                "grounding_status": str(getattr(result, "grounding_status", "") or "NONE")[:32],
                "vacuous_reply": vacuous_reply,
                "false_success_blocked": false_success_blocked,
                "response_empty": not bool(str(result.message or "").strip()),
            },
        )
