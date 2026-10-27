# ==========================
# ELIAN — Phone Companion (LAN terminal sidecar)
# ==========================
"""Extra terminal for Elian: pairing, commands, notifications. One brain."""

from core.companion.protocol import capabilities
from core.companion.service import (
    CompanionService,
    CompanionSettings,
    get_companion_service,
    reset_companion_for_tests,
    set_companion_service,
)
from core.companion.server import CompanionServer, maybe_start_companion

__all__ = [
    "CompanionServer",
    "CompanionService",
    "CompanionSettings",
    "capabilities",
    "get_companion_service",
    "maybe_start_companion",
    "reset_companion_for_tests",
    "set_companion_service",
]
