# ==========================
# ELIAN — Runtime hardware profile (truthful detection)
# ==========================
from __future__ import annotations

import platform
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass
class HardwareProfile:
    gpu_name: str = ""
    cuda_available: bool = False
    cuda_version: str = ""
    vram_total_gb: float = 0.0
    vram_allocated_gb: float = 0.0
    vram_reserved_gb: float = 0.0
    vram_free_gb: float = 0.0
    cpu_cores: int = 0
    ram_total_gb: float = 0.0
    ram_used_gb: float = 0.0
    ram_available_gb: float = 0.0
    os_name: str = ""
    python_version: str = ""
    torch_version: str = ""
    updated_at: float = 0.0
    notes: list = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HardwareProbe:
    """Detect real GPU/CPU/RAM — never invent 32 GB VRAM from system RAM."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._profile = HardwareProfile()
        self.refresh()

    def refresh(self) -> HardwareProfile:
        profile = HardwareProfile(
            os_name=platform.platform(),
            python_version=platform.python_version(),
            cpu_cores=0,
            updated_at=time.time(),
        )
        notes = []
        try:
            import psutil

            profile.cpu_cores = int(psutil.cpu_count(logical=True) or 0)
            vm = psutil.virtual_memory()
            profile.ram_total_gb = round(vm.total / (1024**3), 2)
            profile.ram_used_gb = round(vm.used / (1024**3), 2)
            profile.ram_available_gb = round(vm.available / (1024**3), 2)
            # Explicit: system RAM is NOT VRAM.
            notes.append(
                f"system_ram_gb={profile.ram_total_gb} (not counted as GPU VRAM)"
            )
        except Exception as exc:
            notes.append(f"psutil_unavailable:{exc}")

        try:
            import torch

            profile.torch_version = str(getattr(torch, "__version__", "") or "")
            profile.cuda_available = bool(torch.cuda.is_available())
            if profile.cuda_available:
                profile.cuda_version = str(getattr(torch.version, "cuda", "") or "")
                profile.gpu_name = str(torch.cuda.get_device_name(0) or "")
                props = torch.cuda.get_device_properties(0)
                total = float(props.total_memory) / (1024**3)
                allocated = float(torch.cuda.memory_allocated(0)) / (1024**3)
                reserved = float(torch.cuda.memory_reserved(0)) / (1024**3)
                profile.vram_total_gb = round(total, 3)
                profile.vram_allocated_gb = round(allocated, 3)
                profile.vram_reserved_gb = round(reserved, 3)
                profile.vram_free_gb = round(max(0.0, total - reserved), 3)
            else:
                notes.append("cuda_unavailable")
        except Exception as exc:
            notes.append(f"torch_probe_failed:{exc}")

        profile.notes = notes
        with self._lock:
            self._profile = profile
        return HardwareProfile(**asdict(profile))

    def snapshot(self) -> Dict[str, Any]:
        return self.refresh().to_dict()

    def vram_pressure(self, *, warn_ratio: float = 0.75, critical_ratio: float = 0.90) -> str:
        """Return OK | WARNING | CRITICAL based on reserved/total VRAM."""
        p = self.refresh()
        if not p.cuda_available or p.vram_total_gb <= 0:
            return "OK"
        used = p.vram_reserved_gb / p.vram_total_gb
        if used >= critical_ratio:
            return "CRITICAL"
        if used >= warn_ratio:
            return "WARNING"
        return "OK"

    def recommended_limits(self) -> Dict[str, int]:
        """Derive safe concurrency from actual VRAM."""
        p = self.refresh()
        vram = p.vram_total_gb
        if not p.cuda_available or vram <= 0:
            return {
                "max_gpu_slots": 1,
                "max_cpu_slots": max(2, (p.cpu_cores or 4) // 2),
                "max_concurrent_agents": 2,
                "max_background_workers": 1,
            }
        # ~12 GB class: keep interactive LLM + limited background.
        if vram < 10:
            gpu_slots, concurrent, bg = 1, 2, 1
        elif vram < 16:
            gpu_slots, concurrent, bg = 1, 3, 1
        elif vram < 24:
            gpu_slots, concurrent, bg = 2, 5, 2
        else:
            gpu_slots, concurrent, bg = 2, 8, 3
        return {
            "max_gpu_slots": gpu_slots,
            "max_cpu_slots": max(2, min(8, (p.cpu_cores or 4) // 2)),
            "max_concurrent_agents": concurrent,
            "max_background_workers": bg,
        }


_PROBE: Optional[HardwareProbe] = None


def get_hardware_probe() -> HardwareProbe:
    global _PROBE
    if _PROBE is None:
        _PROBE = HardwareProbe()
    return _PROBE
