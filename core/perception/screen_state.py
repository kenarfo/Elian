# ==========================
# ELIAN — ScreenState (C.5)
# ==========================
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ScreenState:
    frame_id: int = 0
    timestamp: float = 0.0
    monitor: int = 0
    resolution: Dict[str, int] = field(default_factory=lambda: {"width": 0, "height": 0})
    active_window: Dict[str, Any] = field(default_factory=dict)
    ui_snapshot: Dict[str, Any] = field(default_factory=dict)
    visual: Dict[str, Any] = field(default_factory=dict)
    cursor: Dict[str, Any] = field(default_factory=dict)
    modal: Dict[str, Any] = field(default_factory=dict)
    privacy: Dict[str, Any] = field(
        default_factory=lambda: {
            "capture_active": False,
            "vision_active": False,
            "frame_storage_enabled": False,
        }
    )
    sensitive_region: bool = False
    perception_health: str = "idle"
    ui_snapshot_stale: bool = False
    window_history: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def observation_age_ms(self) -> float:
        if not self.timestamp:
            return 1_000_000.0
        return max(0.0, (time.time() - float(self.timestamp)) * 1000.0)

    def to_public_dict(self) -> Dict[str, Any]:
        """WorldState-safe: never include pixels or password values."""
        ui = dict(self.ui_snapshot or {})
        elements = []
        for el in list(ui.get("elements") or []):
            if not isinstance(el, dict):
                continue
            row = {
                "element_id": el.get("element_id") or el.get("id"),
                "role": el.get("role"),
                "name": el.get("name") or el.get("text"),
            }
            if el.get("is_password") or str(el.get("role") or "").lower() == "password":
                row["sensitive"] = True
            else:
                row["value"] = el.get("value") if not el.get("is_password") else ""
            elements.append(row)
        return {
            "frame_id": self.frame_id,
            "timestamp": self.timestamp,
            "monitor": self.monitor,
            "resolution": dict(self.resolution),
            "active_window": dict(self.active_window),
            "focused_element": (ui.get("focused_element") or {}),
            "changed_regions": list((self.visual or {}).get("change_regions") or []),
            "visual_referent": dict((self.visual or {}).get("referent") or {}),
            "perception_health": self.perception_health,
            "sensitive_region": bool(self.sensitive_region),
            "element_count": len(elements),
            "observation_age_ms": int(self.observation_age_ms),
            "ui_snapshot_stale": bool(self.ui_snapshot_stale),
        }
