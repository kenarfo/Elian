# ==========================
# ELIAN — component health (Phase 9)
# ==========================

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


class HealthStatus(str, Enum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"
    NOT_CONFIGURED = "NOT CONFIGURED"


@dataclass
class ComponentHealth:
    name: str
    status: HealthStatus = HealthStatus.UNAVAILABLE
    detail: str = ""
    last_error: str = ""

    def snapshot(self) -> Dict[str, str]:
        return {
            "name": self.name,
            "status": self.status.value,
            "detail": self.detail,
            "last_error": self.last_error[:200] if self.last_error else "",
        }


@dataclass
class HealthRegistry:
    components: Dict[str, ComponentHealth] = field(default_factory=dict)

    def set(
        self,
        name: str,
        status: HealthStatus,
        *,
        detail: str = "",
        error: str = "",
    ) -> None:
        item = self.components.get(name)
        if item is None:
            item = ComponentHealth(name=name)
            self.components[name] = item
        item.status = status
        item.detail = detail
        if error:
            item.last_error = error

    def get(self, name: str) -> Optional[ComponentHealth]:
        return self.components.get(name)

    def snapshot(self) -> Dict[str, Dict[str, str]]:
        return {name: comp.snapshot() for name, comp in self.components.items()}

    def overall_label(self) -> str:
        statuses = [c.status for c in self.components.values()]
        if not statuses:
            return "UNKNOWN"
        if any(s == HealthStatus.ERROR for s in statuses):
            return "ERROR"
        core = {"voice", "stt", "tts", "wake"}
        core_statuses = [
            self.components[n].status
            for n in core
            if n in self.components
        ]
        if core_statuses and all(s == HealthStatus.UNAVAILABLE for s in core_statuses):
            return "UNAVAILABLE"
        if any(s in (HealthStatus.DEGRADED, HealthStatus.UNAVAILABLE) for s in statuses):
            optional_bad = any(
                self.components[n].status
                in (HealthStatus.UNAVAILABLE, HealthStatus.NOT_CONFIGURED, HealthStatus.DEGRADED)
                for n in self.components
                if n not in core
            )
            if optional_bad:
                return "READY WITH OPTIONAL FEATURES UNAVAILABLE"
            return "DEGRADED"
        return "READY"


_REGISTRY = HealthRegistry()


def get_health_registry() -> HealthRegistry:
    return _REGISTRY


def reset_health_registry() -> HealthRegistry:
    global _REGISTRY
    _REGISTRY = HealthRegistry()
    return _REGISTRY
