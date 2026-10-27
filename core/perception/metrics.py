# ==========================
# ELIAN — perception metrics (C.5)
# ==========================
from __future__ import annotations

from typing import Dict

_METRICS = {
    "stale_visual_action": 0,
    "low_confidence_visual_action": 0,
    "ambiguous_visual_action": 0,
    "sensitive_screen_persistence": 0,
    "unnecessary_vision_call": 0,
    "direct_vision_side_effect": 0,
    "vision_requests": 0,
    "stale_screen_claim_rate": 0,
}


def perception_metrics() -> Dict[str, int]:
    return dict(_METRICS)


def reset_perception_metrics() -> None:
    for key in list(_METRICS):
        _METRICS[key] = 0


def note_perception(name: str, amount: int = 1) -> None:
    if name in _METRICS:
        _METRICS[name] = int(_METRICS[name] or 0) + int(amount)
