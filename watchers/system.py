# ==========================
# ELIAN — system watcher (Phase 6)
# ==========================

from __future__ import annotations

from typing import List, Optional, Set

from Config import config
from core.clock import Clock
from core.event_bus import EventBus
from core.events import (
    BATTERY_CHARGING,
    BATTERY_CRITICAL,
    BATTERY_LOW,
    CPU_HIGH_SUSTAINED,
    DISK_LOW,
    NETWORK_LOST,
    NETWORK_RESTORED,
    Priority,
    RAM_HIGH_SUSTAINED,
    Event,
)
from core.system_state import SystemStateStore, get_system_state_store
from watchers.base import Watcher
from watchers.system_provider import (
    FakeSystemProvider,
    PsutilSystemProvider,
    SystemProvider,
)


def _cfg() -> dict:
    section = config.get("proactive", {})
    return section if isinstance(section, dict) else {}


class SystemWatcher(Watcher):
    name = "system"
    interval_seconds = 10.0

    def __init__(
        self,
        bus: EventBus,
        *,
        provider: Optional[SystemProvider] = None,
        clock: Optional[Clock] = None,
        state_store: Optional[SystemStateStore] = None,
    ):
        super().__init__(bus, clock=clock)
        cfg = _cfg()
        self.interval_seconds = float(cfg.get("system_poll_seconds", 10))
        self.provider = provider or PsutilSystemProvider()
        self.state_store = state_store or get_system_state_store()
        self.battery_thresholds: List[int] = list(
            cfg.get("battery_thresholds", [20, 10, 5])
        )
        self.battery_critical = int(cfg.get("battery_critical_percent", 5))
        self.cpu_threshold = float(cfg.get("cpu_threshold", 95))
        self.cpu_duration = float(cfg.get("cpu_duration_seconds", 120))
        self.ram_threshold = float(cfg.get("ram_threshold", 92))
        self.ram_duration = float(cfg.get("ram_duration_seconds", 120))
        self.disk_free_gb = float(cfg.get("disk_free_gb", 10))
        self.network_debounce = float(cfg.get("network_debounce_seconds", 15))

        self._fired_battery: Set[int] = set()
        self._was_plugged: Optional[bool] = None
        self._cpu_high_since: Optional[float] = None
        self._cpu_event_sent = False
        self._ram_high_since: Optional[float] = None
        self._ram_event_sent = False
        self._disk_event_sent = False
        self._network_online: Optional[bool] = None
        self._network_flip_since: Optional[float] = None
        self._pending_network: Optional[bool] = None

    def poll_once(self) -> None:
        snap = self.provider.snapshot()
        now = self.clock.time()
        self.state_store.update(
            battery_percent=snap.battery_percent,
            battery_plugged=snap.battery_plugged,
            has_battery=snap.has_battery,
            cpu_percent=snap.cpu_percent,
            ram_percent=snap.ram_percent,
            disk_free_gb=snap.disk_free_gb,
            disk_percent_used=snap.disk_percent_used,
            network_online=snap.network_online,
            updated_at=now,
        )
        try:
            from core.world_state import note_network

            note_network(online=bool(snap.network_online))
        except Exception:
            pass
        self._check_battery(snap, now)
        self._check_cpu(snap, now)
        self._check_ram(snap, now)
        self._check_disk(snap, now)
        self._check_network(snap, now)

    def _check_battery(self, snap, now: float) -> None:
        if not snap.has_battery or snap.battery_percent is None:
            return

        plugged = bool(snap.battery_plugged)
        if self._was_plugged is False and plugged:
            self._fired_battery.clear()
            self.bus.publish(
                Event(
                    type=BATTERY_CHARGING,
                    source="system",
                    data={"percent": snap.battery_percent},
                    priority=Priority.NORMAL,
                    timestamp=now,
                    dedup_key="system:battery_charging",
                    ttl_seconds=120,
                    invalidates=(BATTERY_LOW, BATTERY_CRITICAL),
                )
            )
        self._was_plugged = plugged

        if plugged:
            return

        percent = float(snap.battery_percent)
        # Reset thresholds if battery rose significantly (e.g. after charge then unplug)
        for thr in list(self._fired_battery):
            if percent > thr + 5:
                self._fired_battery.discard(thr)

        for thr in sorted(self.battery_thresholds, reverse=True):
            if percent <= thr and thr not in self._fired_battery:
                self._fired_battery.add(thr)
                etype = (
                    BATTERY_CRITICAL
                    if thr <= self.battery_critical
                    else BATTERY_LOW
                )
                priority = (
                    Priority.CRITICAL
                    if thr <= self.battery_critical
                    else Priority.HIGH
                )
                self.bus.publish(
                    Event(
                        type=etype,
                        source="system",
                        data={"percent": percent, "threshold": thr},
                        priority=priority,
                        timestamp=now,
                        dedup_key=f"system:battery_{thr}",
                        ttl_seconds=600,
                    )
                )

    def _check_cpu(self, snap, now: float) -> None:
        if snap.cpu_percent >= self.cpu_threshold:
            if self._cpu_high_since is None:
                self._cpu_high_since = now
            elif (
                not self._cpu_event_sent
                and (now - self._cpu_high_since) >= self.cpu_duration
            ):
                self._cpu_event_sent = True
                self.bus.publish(
                    Event(
                        type=CPU_HIGH_SUSTAINED,
                        source="system",
                        data={
                            "percent": snap.cpu_percent,
                            "duration": now - self._cpu_high_since,
                        },
                        priority=Priority.NORMAL,
                        timestamp=now,
                        dedup_key="system:cpu_high",
                        ttl_seconds=900,
                    )
                )
        else:
            self._cpu_high_since = None
            self._cpu_event_sent = False

    def _check_ram(self, snap, now: float) -> None:
        if snap.ram_percent >= self.ram_threshold:
            if self._ram_high_since is None:
                self._ram_high_since = now
            elif (
                not self._ram_event_sent
                and (now - self._ram_high_since) >= self.ram_duration
            ):
                self._ram_event_sent = True
                self.bus.publish(
                    Event(
                        type=RAM_HIGH_SUSTAINED,
                        source="system",
                        data={"percent": snap.ram_percent},
                        priority=Priority.NORMAL,
                        timestamp=now,
                        dedup_key="system:ram_high",
                        ttl_seconds=900,
                    )
                )
        else:
            self._ram_high_since = None
            self._ram_event_sent = False

    def _check_disk(self, snap, now: float) -> None:
        if snap.disk_free_gb is None:
            return
        if snap.disk_free_gb < self.disk_free_gb:
            if not self._disk_event_sent:
                self._disk_event_sent = True
                self.bus.publish(
                    Event(
                        type=DISK_LOW,
                        source="system",
                        data={"free_gb": snap.disk_free_gb},
                        priority=Priority.NORMAL,
                        timestamp=now,
                        dedup_key="system:disk_low",
                        ttl_seconds=3600,
                    )
                )
        else:
            # reset when free space recovers above threshold + margin
            if snap.disk_free_gb > self.disk_free_gb + 2:
                self._disk_event_sent = False

    def _check_network(self, snap, now: float) -> None:
        online = bool(snap.network_online)
        if self._network_online is None:
            self._network_online = online
            return

        if online == self._network_online:
            self._pending_network = None
            self._network_flip_since = None
            return

        # Debounce transition
        if self._pending_network != online:
            self._pending_network = online
            self._network_flip_since = now
            return

        if self._network_flip_since is None:
            return
        if (now - self._network_flip_since) < self.network_debounce:
            return

        self._network_online = online
        self._pending_network = None
        self._network_flip_since = None
        if online:
            self.bus.publish(
                Event(
                    type=NETWORK_RESTORED,
                    source="system",
                    data={},
                    priority=Priority.NORMAL,
                    timestamp=now,
                    dedup_key="system:network_restored",
                    ttl_seconds=120,
                    invalidates=(NETWORK_LOST,),
                )
            )
        else:
            self.bus.publish(
                Event(
                    type=NETWORK_LOST,
                    source="system",
                    data={},
                    priority=Priority.HIGH,
                    timestamp=now,
                    dedup_key="system:network_lost",
                    ttl_seconds=300,
                )
            )
