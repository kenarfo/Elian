import re

from ConversationContext import (
    clear_wait,
    extend_wait,
    touch,
    waiting_seconds_left,
)
from DeviceResolver import normalize_pl


WAIT_PHRASES = (
    "daj mi chwile",
    "daj mi chwilke",
    "poczekaj",
    "czekaj",
    "moment",
    "zaraz ci powiem",
    "sekunde",
    "sekundke",
    "chwile",
    "daj chwile",
    "daj mi moment",
    "zaraz powiem",
    "poczekaj chwile",
)

FORGOT_PHRASES = (
    "zapomnialem co chcialem powiedziec",
    "zapomnialem co chcialem powiedzie",
    "zapomnialem o co chodzilo",
    "zapomnialem co mialem powiedziec",
    "nie pamietam co chcialem powiedziec",
    "nie pamietam co chcialem powiedzie",
)

WAIT_RESPONSE = "Dobrze, czekam."
FORGOT_RESPONSE = "Nic nie szkodzi. Jak sobie przypomnisz, po prostu powiedz."


def _normalize(text):
    return normalize_pl(str(text or "").lower().strip())


def is_wait_phrase(text):
    normalized = _normalize(text)
    if not normalized:
        return False
    if normalized in WAIT_PHRASES:
        return True
    return any(
        normalized == phrase or normalized.startswith(phrase + " ")
        for phrase in WAIT_PHRASES
    )


def is_forgot_phrase(text):
    normalized = _normalize(text)
    if not normalized:
        return False
    if normalized in FORGOT_PHRASES:
        return True
    return any(phrase in normalized for phrase in FORGOT_PHRASES)


def handle_wait_phrase():
    extend_wait()
    touch()
    return WAIT_RESPONSE


def handle_forgot_phrase():
    extend_wait()
    touch()
    return FORGOT_RESPONSE


def get_effective_idle_timeout(base_timeout):
    """Return extended timeout while the user asked for a pause."""
    base = float(base_timeout or 12)
    remaining = waiting_seconds_left()
    if remaining <= 0:
        return base
    return max(base, remaining)


def on_user_utterance(text):
    """Refresh context; keep extended wait unless this is itself a wait phrase."""
    touch()
    if not is_wait_phrase(text) and not is_forgot_phrase(text):
        clear_wait()
