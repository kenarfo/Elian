# ==========================
# ELIAN — Resource scheduler for multi-agent LLM/tool work
# ==========================
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from .hardware import HardwareProbe, get_hardware_probe
from .types import AgentPriority


_PRIORITY_RANK = {
    AgentPriority.CRITICAL.value: 100,
    AgentPriority.HIGH.value: 80,
    AgentPriority.NORMAL.value: 50,
    AgentPriority.BACKGROUND.value: 20,
    AgentPriority.LOW.value: 10,
}


@dataclass
class ResourceLease:
    holder_id: str
    priority: str
    kind: str  # gpu | cpu | tts | stt | network
    acquired_at: float = field(default_factory=time.time)


class ResourceManager:
    """
    Coordinates scarce local resources using measured hardware.

    Interactive (CRITICAL) work always preempts BACKGROUND self-development.
    VRAM pressure pauses/queues background and reduces NORMAL concurrency.
    """

    def __init__(
        self,
        *,
        max_gpu_slots: int = 0,
        max_cpu_slots: int = 0,
        max_tts_slots: int = 1,
        background_budget: float = 0.35,
        hardware: Optional[HardwareProbe] = None,
        vram_warn_ratio: float = 0.75,
        vram_critical_ratio: float = 0.90,
    ) -> None:
        self.hardware = hardware or get_hardware_probe()
        limits = self.hardware.recommended_limits()
        self.max_gpu_slots = max(1, int(max_gpu_slots or limits["max_gpu_slots"]))
        self.max_cpu_slots = max(1, int(max_cpu_slots or limits["max_cpu_slots"]))
        self.max_tts_slots = max(1, int(max_tts_slots))
        self.max_concurrent_normal = int(limits["max_concurrent_agents"])
        self.max_background_workers = int(limits["max_background_workers"])
        self.background_budget = float(background_budget)
        self.vram_warn_ratio = float(vram_warn_ratio)
        self.vram_critical_ratio = float(vram_critical_ratio)
        self._lock = threading.RLock()
        self._leases: Dict[str, ResourceLease] = {}
        self._interactive_active = False
        self._metrics: Dict[str, float] = {
            "gpu_util": 0.0,
            "vram_gb": 0.0,
            "vram_total_gb": 0.0,
            "vram_free_gb": 0.0,
            "cpu_pct": 0.0,
            "ram_gb": 0.0,
            "ram_total_gb": 0.0,
        }
        self._pressure = "OK"

    def apply_hardware_limits(self) -> Dict[str, int]:
        limits = self.hardware.recommended_limits()
        with self._lock:
            self.max_gpu_slots = int(limits["max_gpu_slots"])
            self.max_cpu_slots = int(limits["max_cpu_slots"])
            self.max_concurrent_normal = int(limits["max_concurrent_agents"])
            self.max_background_workers = int(limits["max_background_workers"])
        return dict(limits)

    def note_interactive(self, active: bool) -> None:
        with self._lock:
            self._interactive_active = bool(active)

    def interactive_active(self) -> bool:
        with self._lock:
            return self._interactive_active

    def refresh_metrics(self) -> Dict[str, float]:
        hw = self.hardware.refresh()
        metrics = {
            "gpu_util": round(
                min(1.0, hw.vram_reserved_gb / max(hw.vram_total_gb, 0.001)), 3
            )
            if hw.cuda_available
            else 0.0,
            "vram_gb": hw.vram_reserved_gb,
            "vram_allocated_gb": hw.vram_allocated_gb,
            "vram_total_gb": hw.vram_total_gb,
            "vram_free_gb": hw.vram_free_gb,
            "cpu_pct": 0.0,
            "ram_gb": hw.ram_used_gb,
            "ram_total_gb": hw.ram_total_gb,
            "cpu_cores": float(hw.cpu_cores),
        }
        try:
            import psutil

            metrics["cpu_pct"] = float(psutil.cpu_percent(interval=None))
        except Exception:
            pass
        pressure = self.hardware.vram_pressure(
            warn_ratio=self.vram_warn_ratio,
            critical_ratio=self.vram_critical_ratio,
        )
        with self._lock:
            self._metrics = metrics
            self._pressure = pressure
        return dict(metrics)

    def pressure(self) -> str:
        self.refresh_metrics()
        with self._lock:
            return self._pressure

    def can_acquire(self, *, kind: str, priority: str) -> bool:
        with self._lock:
            if self._interactive_active and priority in (
                AgentPriority.BACKGROUND.value,
                AgentPriority.LOW.value,
            ):
                return False

            # VRAM pressure: pause background / constrain normal workers.
            if self._pressure == "CRITICAL" and priority != AgentPriority.CRITICAL.value:
                return False
            if self._pressure == "WARNING" and priority in (
                AgentPriority.BACKGROUND.value,
                AgentPriority.LOW.value,
            ):
                return False

            if kind == "gpu" and priority in (
                AgentPriority.NORMAL.value,
                AgentPriority.HIGH.value,
            ):
                normal_busy = sum(
                    1
                    for lease in self._leases.values()
                    if lease.kind == "gpu"
                    and lease.priority
                    in (AgentPriority.NORMAL.value, AgentPriority.HIGH.value)
                )
                if normal_busy >= self.max_concurrent_normal:
                    if priority != AgentPriority.CRITICAL.value:
                        return False

            if kind in ("gpu", "cpu") and priority == AgentPriority.BACKGROUND.value:
                bg_busy = sum(
                    1
                    for lease in self._leases.values()
                    if lease.priority == AgentPriority.BACKGROUND.value
                )
                if bg_busy >= self.max_background_workers:
                    return False

            limit = self._limit_for(kind)
            held = sum(1 for lease in self._leases.values() if lease.kind == kind)
            if held >= limit:
                if priority == AgentPriority.CRITICAL.value:
                    return True
                return False
            return True

    def acquire(
        self,
        holder_id: str,
        *,
        kind: str = "gpu",
        priority: str = AgentPriority.NORMAL.value,
        timeout_s: float = 0.0,
    ) -> bool:
        deadline = time.time() + max(0.0, float(timeout_s))
        while True:
            self.refresh_metrics()
            with self._lock:
                if self.can_acquire(kind=kind, priority=priority):
                    if priority == AgentPriority.CRITICAL.value:
                        self._preempt_background(kind)
                    key = f"{kind}:{holder_id}"
                    self._leases[key] = ResourceLease(
                        holder_id=holder_id, priority=priority, kind=kind
                    )
                    return True
            if time.time() >= deadline:
                return False
            time.sleep(0.05)

    def release(self, holder_id: str, *, kind: str = "gpu") -> None:
        with self._lock:
            self._leases.pop(f"{kind}:{holder_id}", None)

    def release_all(self, holder_id: str) -> None:
        with self._lock:
            drop = [k for k, v in self._leases.items() if v.holder_id == holder_id]
            for key in drop:
                self._leases.pop(key, None)

    def snapshot(self) -> Dict:
        self.refresh_metrics()
        with self._lock:
            return {
                "interactive_active": self._interactive_active,
                "pressure": self._pressure,
                "leases": [
                    {
                        "holder_id": lease.holder_id,
                        "priority": lease.priority,
                        "kind": lease.kind,
                        "acquired_at": lease.acquired_at,
                    }
                    for lease in self._leases.values()
                ],
                "metrics": dict(self._metrics),
                "hardware": self.hardware.snapshot(),
                "limits": {
                    "gpu": self.max_gpu_slots,
                    "cpu": self.max_cpu_slots,
                    "tts": self.max_tts_slots,
                    "max_concurrent_normal": self.max_concurrent_normal,
                    "max_background_workers": self.max_background_workers,
                },
                "background_budget": self.background_budget,
            }

    def _limit_for(self, kind: str) -> int:
        if kind == "gpu":
            return self.max_gpu_slots
        if kind == "tts":
            return self.max_tts_slots
        if kind == "stt":
            return 1
        return self.max_cpu_slots

    def _preempt_background(self, kind: str) -> None:
        drop: Set[str] = set()
        for key, lease in self._leases.items():
            if lease.kind != kind:
                continue
            if lease.priority in (
                AgentPriority.BACKGROUND.value,
                AgentPriority.LOW.value,
            ):
                drop.add(key)
        for key in drop:
            self._leases.pop(key, None)
