# ==========================
# ELIAN — proactive turn ingress
# ==========================
"""Controlled path into ConversationManager — same session, not a second chat.

Not every Elian utterance is a reply to a user turn.
"""

from __future__ import annotations

from typing import Any, Optional

from core.situation_types import Situation


def handle_proactive_turn(
    conversation_manager: Any,
    message: str,
    *,
    situation: Optional[Situation] = None,
    source: str = "proactive",
    data: Optional[dict] = None,
) -> Any:
    """Speak a proactive line through CM without inventing a user utterance.

    Keeps session/context; does not run FastPath/Agent on synthetic user text.
    """
    text = str(message or "").strip()
    if not text:
        from core.conversation_manager import HandleResult

        return HandleResult.silent(
            active=bool(getattr(conversation_manager, "active", False)),
            source="proactive_empty",
        )

    # Ensure session is live so follow-ups (offers) share context.
    if hasattr(conversation_manager, "active") and not conversation_manager.active:
        try:
            conversation_manager.activate()
        except Exception:
            pass

    payload = dict(data or {})
    payload["proactive"] = True
    if situation is not None:
        payload["situation_id"] = situation.id
        payload["situation_type"] = situation.type
        if situation.offer_kind:
            payload["offer_kind"] = situation.offer_kind

    # Prefer CM._speak so TTS / digest / HUD hooks stay unified.
    speak = getattr(conversation_manager, "_speak", None)
    if callable(speak):
        return speak(text, source=source, data=payload)

    from core.conversation_manager import HandleResult

    return HandleResult.speak(
        text,
        active=bool(getattr(conversation_manager, "active", True)),
        source=source,
        data=payload,
    )
