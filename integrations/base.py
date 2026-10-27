# ==========================
# ELIAN — integration base (Phase 7)
# ==========================

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class Availability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"
    AUTH_REQUIRED = "auth_required"
    ERROR = "error"
    DISABLED = "disabled"


# Structured error codes for ToolResult.data["error_code"]
AUTH_REQUIRED = "AUTH_REQUIRED"
UNAVAILABLE = "UNAVAILABLE"
NOT_FOUND = "NOT_FOUND"
AMBIGUOUS = "AMBIGUOUS"
CONFLICT = "CONFLICT"
RATE_LIMITED = "RATE_LIMITED"
TEMPORARY_FAILURE = "TEMPORARY_FAILURE"
NOT_CONFIGURED = "NOT_CONFIGURED"
DISABLED = "DISABLED"


@dataclass
class IntegrationResult:
    success: bool
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    availability: Availability = Availability.AVAILABLE

    def to_tool_kwargs(self) -> Dict[str, Any]:
        payload = dict(self.data)
        if self.error_code:
            payload["error_code"] = self.error_code
        payload["availability"] = self.availability.value
        return payload


class IntegrationProvider:
    name = "base"
    label = "integracja"

    def availability(self) -> Availability:
        return Availability.NOT_CONFIGURED

    def health(self) -> IntegrationResult:
        status = self.availability()
        ok = status is Availability.AVAILABLE
        return IntegrationResult(
            success=ok,
            message=f"{self.label}: {status.value}",
            availability=status,
            error_code="" if ok else status.value.upper(),
        )

    def capabilities(self) -> list:
        return []


def integration_enabled(section: str) -> bool:
    from Config import config

    root = config.get("integrations", {})
    if not isinstance(root, dict):
        return True
    block = root.get(section, {})
    if isinstance(block, bool):
        return block
    if isinstance(block, dict):
        return bool(block.get("enabled", True))
    return True
