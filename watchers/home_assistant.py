# ==========================
# ELIAN — Home Assistant health watcher (Phase 6)
# ==========================

from __future__ import annotations

from typing import Callable, Optional

from Config import config
from core.clock import Clock
from core.event_bus import EventBus
from core.events import HA_RESTORED, HA_UNAVAILABLE, Priority, Event
from core.system_state import SystemStateStore, get_system_state_store
from watchers.base import Watcher


HealthFn = Callable[[], bool]


class FakeHAHealth:
    def __init__(self, available: bool = True):
        self.available = available

    def __call__(self) -> bool:
        return bool(self.available)


def default_ha_health() -> bool:
    """Lightweight availability check — no aggressive polling of full registry."""
    try:
        from Config import get_config_value

        if not bool(get_config_value("smart_home", "enabled", default=False)):
            return True  # treat as N/A / healthy when disabled
        from HomeAssistant import get_registry

        registry = get_registry()
        # If registry exists and has loaded recently or has entities
        if registry is None:
            return False
        entities = getattr(registry, "entities", None)
        if entities is not None and len(entities) >= 0:
            return True
        return True
    except Exception:
        return False


class HomeAssistantWatcher(Watcher):
    name = "home_assistant"
    interval_seconds = 60.0

    def __init__(
        self,
        bus: EventBus,
        *,
        health_fn: Optional[HealthFn] = None,
        clock: Optional[Clock] = None,
        state_store: Optional[SystemStateStore] = None,
    ):
        super().__init__(bus, clock=clock)
        cfg = config.get("proactive", {})
        if not isinstance(cfg, dict):
            cfg = {}
        self.interval_seconds = float(cfg.get("ha_poll_seconds", 60))
        self.health_fn = health_fn or default_ha_health
        self.state_store = state_store or get_system_state_store()
        self._available: Optional[bool] = None

    def poll_once(self) -> None:
        # Skip spam when smart home disabled
        try:
            from Config import get_config_value

            if not bool(get_config_value("smart_home", "enabled", default=False)):
                # Still allow fake health_fn in tests
                if self.health_fn is default_ha_health:
                    return
        except Exception:
            pass

        now = self.clock.time()
        try:
            ok = bool(self.health_fn())
        except Exception:
            ok = False

        self.state_store.update(ha_available=ok, updated_at=now)

        if self._available is None:
            self._available = ok
            return

        if ok == self._available:
            return

        self._available = ok
        if ok:
            self.bus.publish(
                Event(
                    type=HA_RESTORED,
                    source="home_assistant",
                    data={},
                    priority=Priority.NORMAL,
                    timestamp=now,
                    dedup_key="ha:restored",
                    ttl_seconds=180,
                    invalidates=(HA_UNAVAILABLE,),
                )
            )
        else:
            self.bus.publish(
                Event(
                    type=HA_UNAVAILABLE,
                    source="home_assistant",
                    data={},
                    priority=Priority.NORMAL,
                    timestamp=now,
                    dedup_key="ha:unavailable",
                    ttl_seconds=900,
                )
            )
