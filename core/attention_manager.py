# ==========================
# ELIAN — AttentionManager (speak NOW vs later)
# ==========================
"""Decide INTERRUPT_NOW / SPEAK_AFTER_TURN / DEFER / DIGEST / SILENT.

SituationEngine finds meaning; AttentionManager finds timing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.situation_types import (
    AttentionContext,
    AttentionDecision,
    Situation,
)


@dataclass
class AttentionVerdict:
    decision: AttentionDecision
    reason: str = ""
    situation: Optional[Situation] = None


class AttentionManager:
    """Know whether Elian should speak now — not what to say."""

    def decide(
        self,
        situation: Situation,
        ctx: AttentionContext,
    ) -> AttentionVerdict:
        if situation is None:
            return AttentionVerdict(AttentionDecision.SILENT, "no_situation")
        if situation.resolved or situation.already_presented:
            return AttentionVerdict(AttentionDecision.SILENT, "resolved_or_presented", situation)
        if situation.is_expired(ctx.now):
            return AttentionVerdict(AttentionDecision.SILENT, "expired", situation)

        mute_key = situation.mute_key or situation.type
        muted_until = (ctx.muted_types or {}).get(mute_key)
        if muted_until is not None and float(muted_until) > float(ctx.now):
            return AttentionVerdict(AttentionDecision.SILENT, "user_muted_type", situation)

        if not ctx.proactive_enabled and not situation.critical:
            return AttentionVerdict(AttentionDecision.SILENT, "proactive_disabled", situation)

        # Very low relevance / CPU blip class
        if (
            situation.relevance < 0.35
            and situation.importance < 0.45
            and not situation.critical
        ):
            return AttentionVerdict(AttentionDecision.SILENT, "low_value", situation)

        if situation.critical or (
            situation.urgency >= 0.9 and situation.importance >= 0.85
        ):
            if ctx.user_speaking or ctx.tts_active:
                # Safety still interrupts
                return AttentionVerdict(
                    AttentionDecision.INTERRUPT_NOW, "critical_interrupt", situation
                )
            return AttentionVerdict(
                AttentionDecision.INTERRUPT_NOW, "critical", situation
            )

        if ctx.suppress_non_urgent and situation.urgency < 0.75:
            return AttentionVerdict(AttentionDecision.DEFER, "suppress_non_urgent", situation)

        if ctx.quiet_hours and not situation.critical and situation.urgency < 0.85:
            return AttentionVerdict(AttentionDecision.DEFER, "quiet_hours", situation)

        if ctx.gaming or ctx.fullscreen:
            if situation.deferrable and situation.urgency < 0.85:
                return AttentionVerdict(AttentionDecision.DEFER, "fullscreen_game", situation)

        if ctx.user_speaking:
            if situation.deferrable:
                return AttentionVerdict(
                    AttentionDecision.SPEAK_AFTER_TURN, "user_speaking", situation
                )
            return AttentionVerdict(AttentionDecision.INTERRUPT_NOW, "non_deferrable", situation)

        if ctx.tts_active or ctx.important_tts:
            if situation.deferrable:
                return AttentionVerdict(AttentionDecision.DEFER, "tts_active", situation)
            return AttentionVerdict(AttentionDecision.INTERRUPT_NOW, "tts_interrupt", situation)

        if ctx.conversation_active and situation.deferrable and situation.urgency < 0.7:
            if situation.importance < 0.7:
                return AttentionVerdict(AttentionDecision.DIGEST, "conversation_digest", situation)
            return AttentionVerdict(
                AttentionDecision.SPEAK_AFTER_TURN, "conversation_active", situation
            )

        if ctx.rate_limited and situation.urgency < 0.8:
            return AttentionVerdict(AttentionDecision.DIGEST, "rate_limit", situation)

        # Idle / clear to speak
        if situation.importance < 0.45 and situation.urgency < 0.4:
            return AttentionVerdict(AttentionDecision.DIGEST, "low_priority_digest", situation)

        return AttentionVerdict(AttentionDecision.INTERRUPT_NOW, "clear", situation)


def build_attention_context(host: Any, *, now: float | None = None) -> AttentionContext:
    """Build AttentionContext from ProactiveManager-like host + WorldState."""
    stamp = float(now if now is not None else host.clock.time())
    world: Dict[str, Any] = {}
    try:
        from core.world_state import get_world_state

        world = dict(get_world_state() or {})
    except Exception:
        world = {}

    tts = world.get("tts") or {}
    stt = world.get("stt") or {}
    session = world.get("session") or {}
    computer = world.get("computer") or world.get("pc") or {}
    active_app = ""
    fullscreen = False
    gaming = False
    if isinstance(computer, dict):
        active_app = str(
            computer.get("process")
            or computer.get("active_app")
            or computer.get("title")
            or ""
        )
        fullscreen = bool(computer.get("fullscreen") or computer.get("exclusive_fullscreen"))
        gaming = bool(computer.get("gaming") or computer.get("is_game"))
        app_l = active_app.lower()
        if any(g in app_l for g in ("steam", "game", ".exe") ) and computer.get("fullscreen"):
            gaming = True

    user_speaking = False
    try:
        user_speaking = bool(host.busy_check())
    except Exception:
        user_speaking = False
    if isinstance(stt, dict) and (
        stt.get("listening") is True or str(stt.get("state") or "").lower() == "listening"
    ):
        # listening alone is not "user speaking" for interrupt — barge uses busy_check
        pass
    if isinstance(stt, dict) and stt.get("user_speaking") is True:
        user_speaking = True

    tts_active = bool(getattr(host, "important_tts", False))
    if isinstance(tts, dict) and (
        tts.get("speaking") is True or str(tts.get("state") or "").lower() == "speaking"
    ):
        tts_active = True

    quiet = False
    try:
        quiet = bool(host.quiet_hours_active())
    except Exception:
        quiet = False

    muted: Dict[str, float] = {}
    try:
        muted = dict(getattr(host, "_muted_situation_types", {}) or {})
    except Exception:
        muted = {}

    last_activity = stamp
    if isinstance(session, dict) and session.get("last_activity"):
        try:
            last_activity = float(session["last_activity"])
        except (TypeError, ValueError):
            pass

    rate_limited = False
    try:
        rate_limited = not bool(host._rate_allows(stamp))
    except Exception:
        rate_limited = False

    enabled = True
    try:
        enabled = bool(host.enabled())
    except Exception:
        enabled = True

    return AttentionContext(
        now=stamp,
        user_speaking=user_speaking,
        tts_active=tts_active,
        conversation_active=bool(getattr(host, "conversation_active", False)),
        important_tts=bool(getattr(host, "important_tts", False)),
        quiet_hours=quiet,
        last_user_activity=last_activity,
        active_app=active_app,
        fullscreen=fullscreen,
        gaming=gaming,
        muted_types=muted,
        suppress_non_urgent=bool(getattr(host, "suppress_non_urgent", False)),
        rate_limited=rate_limited,
        proactive_enabled=enabled,
    )


def attention_to_policy_action(decision: AttentionDecision) -> str:
    """Map attention → ProactiveManager action verb."""
    if decision is AttentionDecision.INTERRUPT_NOW:
        return "notify"
    if decision is AttentionDecision.SPEAK_AFTER_TURN:
        return "queue"
    if decision is AttentionDecision.DEFER:
        return "queue"
    if decision is AttentionDecision.DIGEST:
        return "queue"
    return "silent"
