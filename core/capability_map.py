# ==========================
# ELIAN — CapabilityMap (Phase 1)
# ==========================
"""Named capability snapshot for HUD / doctor.

Planner-facing runtime truth lives in ``core.capability_registry``
(WEB_SEARCH, HOME_ASSISTANT, …). This module keeps the Phase-1 names
(LocalAI, Browser, …) and wraps HealthRegistry — not a second health system.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

READY = "READY"
DEGRADED = "DEGRADED"
UNAVAILABLE = "UNAVAILABLE"
DISABLED = "DISABLED"
NOT_CONFIGURED = "NOT_CONFIGURED"
EXPERIMENTAL = "EXPERIMENTAL"

CAPABILITY_IDS = (
    "LocalAI",
    "CloudAI",
    "Internet",
    "ComputerControl",
    "Browser",
    "ScreenPerception",
    "HomeAssistant",
    "Calendar",
    "Camera",
    "MR",
)


@dataclass
class CapabilityStatus:
    name: str
    status: str = UNAVAILABLE
    health: str = ""
    local: bool = True
    privacy: str = "local"
    last_success: str = ""
    latency_estimate_ms: float = 0.0
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "health": self.health or self.status,
            "local": self.local,
            "privacy": self.privacy,
            "last_success": self.last_success,
            "latency_estimate_ms": self.latency_estimate_ms,
            "detail": self.detail,
        }


def _from_health(name: str, health_name: str, *, local: bool, privacy: str) -> CapabilityStatus:
    status = UNAVAILABLE
    detail = ""
    try:
        from core.health import get_health_registry

        item = get_health_registry().get(health_name)
        if item is not None:
            raw = str(getattr(item.status, "value", item.status) or "")
            detail = str(getattr(item, "detail", "") or "")
            if raw in ("READY",):
                status = READY
            elif raw in ("DEGRADED",):
                status = DEGRADED
            elif raw in ("NOT CONFIGURED", "NOT_CONFIGURED"):
                status = NOT_CONFIGURED
            elif raw in ("UNAVAILABLE", "ERROR"):
                status = UNAVAILABLE
    except Exception:
        status = UNAVAILABLE
    return CapabilityStatus(
        name=name,
        status=status,
        health=status,
        local=local,
        privacy=privacy,
        detail=detail,
    )


def snapshot_capabilities() -> Dict[str, CapabilityStatus]:
    """Best-effort. Missing adapters stay UNAVAILABLE/DISABLED — never fake READY."""
    rows: Dict[str, CapabilityStatus] = {}
    rows["LocalAI"] = _from_health("LocalAI", "ollama", local=True, privacy="local")
    if rows["LocalAI"].status == UNAVAILABLE:
        rows["LocalAI"] = _from_health("LocalAI", "ai", local=True, privacy="local")
    rows["CloudAI"] = CapabilityStatus(
        name="CloudAI",
        status=DISABLED,
        health=DISABLED,
        local=False,
        privacy="cloud",
        detail="Cloud models are opt-in. Not enabled in this runtime.",
    )
    rows["Internet"] = _from_health("Internet", "network", local=False, privacy="network")
    rows["ComputerControl"] = CapabilityStatus(
        name="ComputerControl",
        status=READY,
        health=READY,
        local=True,
        privacy="local",
        detail="ComputerAgent verified UI worker.",
    )
    browser_status = UNAVAILABLE
    browser_detail = "BrowserAgent off until integrations.browser.enabled and Playwright."
    try:
        from core.browser_agent import browser_opt_in, browser_runtime_available, playwright_installed

        if browser_runtime_available():
            # Opt-in + Playwright: experimental until full hardware RC.
            browser_status = EXPERIMENTAL
            browser_detail = "Playwright BrowserAgent EXPERIMENTAL (opt-in)."
        elif browser_opt_in() and not playwright_installed():
            browser_detail = "Browser enabled, but Playwright is not installed."
        elif playwright_installed() and not browser_opt_in():
            browser_detail = (
                "Playwright installed; BrowserAgent stays UNAVAILABLE "
                "until integrations.browser.enabled=true."
            )
    except Exception:
        pass
    rows["Browser"] = CapabilityStatus(
        name="Browser",
        status=browser_status,
        health=browser_status,
        local=True,
        privacy="local",
        detail=browser_detail,
    )
    rows["ScreenPerception"] = CapabilityStatus(
        name="ScreenPerception",
        status=READY,
        health=READY,
        local=True,
        privacy="local",
        detail="Foreground + UIA + optional vision.",
    )
    rows["HomeAssistant"] = _from_health(
        "HomeAssistant", "home_assistant", local=True, privacy="local"
    )
    if rows["HomeAssistant"].status == UNAVAILABLE:
        rows["HomeAssistant"] = _from_health(
            "HomeAssistant", "ha", local=True, privacy="local"
        )
    rows["Calendar"] = CapabilityStatus(
        name="Calendar",
        status=NOT_CONFIGURED,
        health=NOT_CONFIGURED,
        local=True,
        privacy="local",
        detail="Local calendar adapter exists; external calendar may be unconfigured.",
    )
    try:
        from integrations.calendar.provider import get_calendar_provider

        avail = str(get_calendar_provider().availability().value or "")
        if avail == "available":
            rows["Calendar"].status = READY
            rows["Calendar"].health = READY
            rows["Calendar"].detail = ""
        elif avail in ("disabled",):
            rows["Calendar"].status = DISABLED
        elif avail in ("not_configured", "auth_required"):
            rows["Calendar"].status = NOT_CONFIGURED
        else:
            rows["Calendar"].status = UNAVAILABLE
        rows["Calendar"].health = rows["Calendar"].status
        rows["Calendar"].detail = avail
    except Exception:
        pass
    rows["Camera"] = CapabilityStatus(
        name="Camera",
        status=DISABLED,
        health=DISABLED,
        local=True,
        privacy="local",
        detail="Camera/presence is opt-in and off by default.",
    )
    rows["MR"] = CapabilityStatus(
        name="MR",
        status=DISABLED,
        health=DISABLED,
        local=True,
        privacy="local",
        detail="Spatial/MR stage is not in this runtime.",
    )
    return _overlay_registry_truth(rows)


# Phase-1 HUD names <- CapStatus. Map is a read-only mirror, not a second truth.
_REGISTRY_TO_MAP = {
    "WEB_SEARCH": "Internet",
    "HOME_ASSISTANT": "HomeAssistant",
    "BROWSER_AUTOMATION": "Browser",
    "LOCAL_AI": "LocalAI",
    "COMPUTER_CONTROL": "ComputerControl",
    "VISION": "ScreenPerception",
    "CALENDAR": "Calendar",
}

_CAP_TO_MAP_STATUS = {
    "AVAILABLE": READY,
    "DEGRADED": DEGRADED,
    "DISABLED": DISABLED,
    "UNAVAILABLE": UNAVAILABLE,
    "BLOCKED": UNAVAILABLE,
}


def _overlay_registry_truth(rows: Dict[str, CapabilityStatus]) -> Dict[str, CapabilityStatus]:
    """HUD/ActionLoop snapshot follows CapabilityRegistry. Never invent a second health."""
    try:
        from core.capability_registry import get_capability_registry

        crec = get_capability_registry()
        crec.refresh()
    except Exception:
        return rows
    for cap_id, map_name in _REGISTRY_TO_MAP.items():
        rec = crec.get(cap_id)
        item = rows.get(map_name)
        if rec is None or item is None or not rec.probed:
            continue
        mapped = _CAP_TO_MAP_STATUS.get(rec.status.value)
        if not mapped:
            continue
        item.status = mapped
        item.health = mapped
        item.detail = rec.detail or rec.failure_reason or item.detail
    return rows


def capability_snapshot_dict() -> Dict[str, Dict[str, Any]]:
    return {name: item.to_dict() for name, item in snapshot_capabilities().items()}
