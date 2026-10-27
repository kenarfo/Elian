# ==========================
# ELIAN — pending proactive question
# ==========================
"""One short-lived offer after Elian asks (e.g. Odczytać?)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

_offer: Optional["ProactiveOffer"] = None


@dataclass
class ProactiveOffer:
    kind: str
    item_id: str = ""
    spoken_body: str = ""
    created_at: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)


def set_proactive_offer(offer: Optional[ProactiveOffer]) -> None:
    global _offer
    _offer = offer


def get_proactive_offer() -> Optional[ProactiveOffer]:
    return _offer


def clear_proactive_offer() -> None:
    global _offer
    _offer = None


def reset_offer_for_tests() -> None:
    clear_proactive_offer()
