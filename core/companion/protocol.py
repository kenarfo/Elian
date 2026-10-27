# ==========================
# ELIAN — Phone Companion protocol (LAN terminal)
# ==========================
"""JSON envelopes for the LAN companion sidecar.

Phone is an extra terminal, not a second brain. Deferred (honest UNAVAILABLE):
camera, photos, location-with-consent, PC↔phone handoff, „jestem w drodze”.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


PROTOCOL_VERSION = 1

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
STATUS_UNAUTHORIZED = "unauthorized"
STATUS_FORBIDDEN = "forbidden"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_INVALID = "invalid"
STATUS_CONFLICT = "conflict"
STATUS_TIMEOUT = "timeout"

HTTP_STATUS = {
    STATUS_OK: 200,
    STATUS_UNAVAILABLE: 503,
    STATUS_UNAUTHORIZED: 401,
    STATUS_FORBIDDEN: 403,
    STATUS_RATE_LIMITED: 429,
    STATUS_INVALID: 400,
    STATUS_CONFLICT: 409,
    STATUS_TIMEOUT: 504,
}

# Future capabilities — not implemented this phase.
DEFERRED_CAPABILITIES = {
    "camera": "Kamera i zdjęcia nie są dostępne w tej wersji.",
    "photos": "Zdjęcia nie są dostępne w tej wersji.",
    "location": "Lokalizacja wymaga zgody i nie jest dostępna w tej wersji.",
    "handoff": "Przekazywanie sesji PC↔telefon nie jest jeszcze dostępne.",
    "in_transit": "Tryb „jestem w drodze” nie jest jeszcze dostępny.",
    "mic_stream": "Ciągły mikrofon z telefonu nie jest jeszcze dostępny.",
}

QUICK_ACTIONS: List[Dict[str, Any]] = [
    {
        "id": "notifications",
        "label": "Powiadomienia",
        "command": "Jakie mam powiadomienia?",
        "local": False,
    },
    {
        "id": "weather",
        "label": "Pogoda",
        "command": "Jaka jest pogoda?",
        "local": False,
    },
    {"id": "mute", "label": "Wycisz powiadomienia", "command": "", "local": True},
    {"id": "unmute", "label": "Włącz powiadomienia", "command": "", "local": True},
]

DEFAULT_PERMISSIONS: Dict[str, bool] = {
    "commands": True,
    "notifications": True,
    "audio": True,
    "quick_actions": True,
    "camera": False,
    "location": False,
    "handoff": False,
}


@dataclass
class CompanionEnvelope:
    ok: bool
    status: str = STATUS_OK
    error: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    http_status: int = 200
    content_type: str = "application/json; charset=utf-8"
    body_bytes: Optional[bytes] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "ok": self.ok,
            "status": self.status,
            "protocol": PROTOCOL_VERSION,
        }
        if self.error:
            payload["error"] = self.error
        if self.data:
            payload["data"] = self.data
        return payload


def ok(data: Optional[Dict[str, Any]] = None) -> CompanionEnvelope:
    return CompanionEnvelope(
        ok=True,
        status=STATUS_OK,
        data=dict(data or {}),
        http_status=200,
    )


def fail(status: str, error: str, *, data: Optional[Dict[str, Any]] = None) -> CompanionEnvelope:
    return CompanionEnvelope(
        ok=False,
        status=status,
        error=str(error or ""),
        data=dict(data or {}),
        http_status=HTTP_STATUS.get(status, 400),
    )


def unavailable(error: str, *, data: Optional[Dict[str, Any]] = None) -> CompanionEnvelope:
    return fail(STATUS_UNAVAILABLE, error, data=data)


def unauthorized(error: str = "Brak autoryzacji.") -> CompanionEnvelope:
    return fail(STATUS_UNAUTHORIZED, error)


def forbidden(error: str, *, data: Optional[Dict[str, Any]] = None) -> CompanionEnvelope:
    return fail(STATUS_FORBIDDEN, error, data=data)


def invalid(error: str, *, data: Optional[Dict[str, Any]] = None) -> CompanionEnvelope:
    return fail(STATUS_INVALID, error, data=data)


def rate_limited(error: str = "Za dużo zapytań. Spróbuj za chwilę.") -> CompanionEnvelope:
    return fail(STATUS_RATE_LIMITED, error)


def conflict(error: str, *, data: Optional[Dict[str, Any]] = None) -> CompanionEnvelope:
    return fail(STATUS_CONFLICT, error, data=data)


def timed_out(error: str = "Elian nie odpowiedział na czas.", *, data: Optional[Dict[str, Any]] = None) -> CompanionEnvelope:
    return fail(STATUS_TIMEOUT, error, data=data)


def capabilities(*, audio: bool = True, enabled: bool = True) -> Dict[str, Any]:
    """Honest availability map for the phone client."""
    if not enabled:
        return {
            "commands": STATUS_UNAVAILABLE,
            "notifications": STATUS_UNAVAILABLE,
            "audio": STATUS_UNAVAILABLE,
            "quick_actions": STATUS_UNAVAILABLE,
            "camera": STATUS_UNAVAILABLE,
            "location": STATUS_UNAVAILABLE,
            "handoff": STATUS_UNAVAILABLE,
            "in_transit": STATUS_UNAVAILABLE,
            "mic_stream": STATUS_UNAVAILABLE,
        }
    return {
        "commands": STATUS_OK,
        "notifications": STATUS_OK,
        "audio": STATUS_OK if audio else STATUS_UNAVAILABLE,
        "quick_actions": STATUS_OK,
        "camera": STATUS_UNAVAILABLE,
        "location": STATUS_UNAVAILABLE,
        "handoff": STATUS_UNAVAILABLE,
        "in_transit": STATUS_UNAVAILABLE,
        "mic_stream": STATUS_UNAVAILABLE,
        "deferred": dict(DEFERRED_CAPABILITIES),
        "quick_action_catalog": list(QUICK_ACTIONS),
    }
