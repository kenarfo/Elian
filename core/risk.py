"""Risk levels for tool execution."""

from enum import Enum


class RiskLevel(str, Enum):
    SAFE = "safe"
    MEDIUM = "medium"
    DANGEROUS = "dangerous"

    def requires_confirmation(self, confirm_medium=True):
        if self is RiskLevel.DANGEROUS:
            return True
        if self is RiskLevel.MEDIUM:
            return bool(confirm_medium)
        return False
