# ==========================
# ELIAN — system metrics providers (Phase 6)
# ==========================

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SystemSnapshot:
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    disk_free_gb: Optional[float] = None
    disk_percent_used: Optional[float] = None
    battery_percent: Optional[float] = None
    battery_plugged: Optional[bool] = None
    has_battery: bool = False
    network_online: bool = True


class SystemProvider:
    def snapshot(self) -> SystemSnapshot:
        raise NotImplementedError


class FakeSystemProvider(SystemProvider):
    def __init__(self, **kwargs):
        self.data = SystemSnapshot(**kwargs)

    def snapshot(self) -> SystemSnapshot:
        return SystemSnapshot(
            cpu_percent=self.data.cpu_percent,
            ram_percent=self.data.ram_percent,
            disk_free_gb=self.data.disk_free_gb,
            disk_percent_used=self.data.disk_percent_used,
            battery_percent=self.data.battery_percent,
            battery_plugged=self.data.battery_plugged,
            has_battery=self.data.has_battery,
            network_online=self.data.network_online,
        )

    def set(self, **kwargs) -> None:
        for key, value in kwargs.items():
            if hasattr(self.data, key):
                setattr(self.data, key, value)


class PsutilSystemProvider(SystemProvider):
    def snapshot(self) -> SystemSnapshot:
        import psutil
        import shutil

        cpu = float(psutil.cpu_percent(interval=0.0))
        ram = float(psutil.virtual_memory().percent)
        disk_free = None
        disk_used = None
        try:
            usage = shutil.disk_usage("C:\\" if __import__("os").name == "nt" else "/")
            disk_free = usage.free / (1024 ** 3)
            disk_used = (usage.used / usage.total) * 100.0
        except Exception:
            pass

        has_battery = False
        battery_percent = None
        battery_plugged = None
        try:
            bat = psutil.sensors_battery()
            if bat is not None:
                has_battery = True
                battery_percent = float(bat.percent)
                battery_plugged = bool(bat.power_plugged)
        except Exception:
            pass

        online = True
        try:
            import socket

            socket.create_connection(("1.1.1.1", 53), timeout=1.5).close()
        except Exception:
            online = False

        return SystemSnapshot(
            cpu_percent=cpu,
            ram_percent=ram,
            disk_free_gb=disk_free,
            disk_percent_used=disk_used,
            battery_percent=battery_percent,
            battery_plugged=battery_plugged,
            has_battery=has_battery,
            network_online=online,
        )
