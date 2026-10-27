# ==========================
# ELIAN — runtime system state (Phase 6)
# ==========================

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass
class SystemState:
    """Short-lived runtime metrics — not user Memory."""

    battery_percent: Optional[float] = None
    battery_plugged: Optional[bool] = None
    has_battery: bool = False
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    disk_free_gb: Optional[float] = None
    disk_percent_used: Optional[float] = None
    network_online: bool = True
    ha_available: Optional[bool] = None
    updated_at: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return data

    def summary_text(self) -> str:
        parts = []
        if self.has_battery and self.battery_percent is not None:
            plug = "ładowanie" if self.battery_plugged else "na baterii"
            parts.append(f"Bateria {int(self.battery_percent)}% ({plug})")
        parts.append(f"CPU {self.cpu_percent:.0f}%")
        parts.append(f"RAM {self.ram_percent:.0f}%")
        if self.disk_free_gb is not None:
            parts.append(f"Dysk wolny {self.disk_free_gb:.1f} GB")
        parts.append("Sieć OK" if self.network_online else "Brak sieci")
        if self.ha_available is True:
            parts.append("Home Assistant dostępny")
        elif self.ha_available is False:
            parts.append("Home Assistant niedostępny")
        return ". ".join(parts) + "."


class SystemStateStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._state = SystemState()

    @property
    def state(self) -> SystemState:
        with self._lock:
            return SystemState(**asdict(self._state))

    def update(self, **kwargs) -> SystemState:
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._state, key):
                    setattr(self._state, key, value)
            return self.state


_store: Optional[SystemStateStore] = None


def get_system_state_store() -> SystemStateStore:
    global _store
    if _store is None:
        _store = SystemStateStore()
    return _store


def reset_system_state_for_tests(store: Optional[SystemStateStore] = None) -> None:
    global _store
    _store = store if store is not None else SystemStateStore()
