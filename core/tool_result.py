"""Unified tool execution results for agent / planner observation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ToolStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    NEEDS_CONFIRMATION = "needs_confirmation"
    TIMEOUT = "timeout"
    RETRYABLE = "retryable"
    PARTIAL = "partial"


@dataclass
class ToolResult:
    success: bool
    message: str
    status: ToolStatus = ToolStatus.SUCCESS
    data: dict[str, Any] = field(default_factory=dict)
    tool_name: str = ""
    retryable: bool = False
    error: str = ""
    # Standardized contract fields (capability failures ≠ execution failures).
    error_code: str = ""
    error_message: str = ""
    side_effect: Optional[bool] = None
    verification_hint: str = ""

    @classmethod
    def ok(
        cls,
        message,
        *,
        data=None,
        tool_name="",
        side_effect: Optional[bool] = None,
        verification_hint: str = "",
    ):
        return cls(
            success=True,
            message=str(message or ""),
            status=ToolStatus.SUCCESS,
            data=dict(data or {}),
            tool_name=tool_name,
            side_effect=side_effect,
            verification_hint=str(verification_hint or ""),
        )

    @classmethod
    def fail(
        cls,
        message,
        *,
        error="",
        data=None,
        tool_name="",
        retryable=False,
        error_code: str = "",
        error_message: str = "",
        side_effect: Optional[bool] = None,
        verification_hint: str = "",
    ):
        status = ToolStatus.RETRYABLE if retryable else ToolStatus.FAILURE
        err = str(error or message or "")
        code = str(error_code or ("TEMPORARY_FAILURE" if retryable else "FAILURE"))
        return cls(
            success=False,
            message=str(message or "Operacja nie powiodła się."),
            status=status,
            data=dict(data or {}),
            tool_name=tool_name,
            retryable=retryable,
            error=err,
            error_code=code,
            error_message=str(error_message or err),
            side_effect=side_effect,
            verification_hint=str(verification_hint or ""),
        )

    @classmethod
    def unsupported(cls, message, *, tool_name="", data=None, error="unsupported"):
        return cls(
            success=False,
            message=str(message or "Tej czynności nie obsługuję."),
            status=ToolStatus.UNSUPPORTED,
            data=dict(data or {}),
            tool_name=tool_name,
            error=str(error or "unsupported"),
            error_code="UNSUPPORTED",
            error_message=str(message or "unsupported"),
        )

    @classmethod
    def unavailable(
        cls,
        message,
        *,
        tool_name="",
        data=None,
        error_code: str = "CAPABILITY_UNAVAILABLE",
        error_message: str = "",
        retryable: bool = False,
    ):
        """Capability / dependency offline — not an execution failure."""
        msg = str(message or "Narzędzie jest niedostępne.")
        return cls(
            success=False,
            message=msg,
            status=ToolStatus.UNAVAILABLE,
            data=dict(data or {}),
            tool_name=tool_name,
            error="unavailable",
            error_code=str(error_code or "CAPABILITY_UNAVAILABLE"),
            error_message=str(error_message or msg),
            retryable=bool(retryable),
            side_effect=False,
        )

    @classmethod
    def needs_confirmation(cls, message, *, tool_name="", data=None):
        return cls(
            success=False,
            message=str(message or "Potrzebuję potwierdzenia."),
            status=ToolStatus.NEEDS_CONFIRMATION,
            data=dict(data or {}),
            tool_name=tool_name,
            error_code="NEEDS_CONFIRMATION",
            error_message=str(message or ""),
            side_effect=False,
        )

    @classmethod
    def timed_out(cls, message, *, tool_name="", data=None):
        return cls(
            success=False,
            message=str(message or "Przekroczono limit czasu."),
            status=ToolStatus.TIMEOUT,
            data=dict(data or {}),
            tool_name=tool_name,
            retryable=True,
            error="timeout",
            error_code="TIMEOUT",
            error_message=str(message or "timeout"),
            side_effect=None,
            verification_hint="Sprawdź, czy skutek jednak wystąpił.",
        )

    @classmethod
    def unknown_side_effect(cls, message, *, tool_name="", data=None):
        payload = dict(data or {})
        payload["side_effect_may_have_occurred"] = True
        return cls(
            success=False,
            message=str(message or "Nie potwierdziłem skutku tej czynności."),
            status=ToolStatus.PARTIAL,
            data=payload,
            tool_name=tool_name,
            retryable=False,
            error="unknown_side_effect",
            error_code="UNKNOWN_SIDE_EFFECT",
            error_message=str(message or "unknown_side_effect"),
            side_effect=True,
            verification_hint="Zweryfikuj stan systemu przed ponowieniem.",
        )

    def to_observation(self):
        """Shape future Planner can consume without another rewrite."""
        obs = {
            "tool": self.tool_name,
            "success": self.success,
            "status": self.status.value,
            "message": self.message,
            "data": dict(self.data),
            "retryable": self.retryable,
            "error": self.error,
            "error_code": self.error_code,
            "error_message": self.error_message or self.error,
            "verification_hint": self.verification_hint,
        }
        if self.side_effect is not None:
            obs["side_effect"] = self.side_effect
        return obs
