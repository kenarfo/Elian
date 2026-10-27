# ==========================
# ELIAN — Capability Registry (RC tools/capabilities)
# ==========================
"""Runtime truth: which capabilities exist, their health, and associated tools.

Planner gets a compact capability shortlist — not a full tool-schema dump.
Wraps HealthRegistry / config / providers; does not invent a second health system.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from Config import get_config_value


class CapStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED = "BLOCKED"


# Canonical capability IDs (planner / Brain / diagnostics).
WEB_SEARCH = "WEB_SEARCH"
WEB_FETCH = "WEB_FETCH"
BROWSER_AUTOMATION = "BROWSER_AUTOMATION"
HOME_ASSISTANT = "HOME_ASSISTANT"
MEDIA = "MEDIA"
WINDOWS_APPS = "WINDOWS_APPS"
COMPUTER_CONTROL = "COMPUTER_CONTROL"
CALENDAR = "CALENDAR"
REMINDERS = "REMINDERS"
MEMORY = "MEMORY"
PHONE = "PHONE"
VISION = "VISION"
LOCAL_AI = "LOCAL_AI"
NETWORK = "NETWORK"

CANONICAL_IDS: Tuple[str, ...] = (
    WEB_SEARCH,
    WEB_FETCH,
    BROWSER_AUTOMATION,
    HOME_ASSISTANT,
    MEDIA,
    WINDOWS_APPS,
    COMPUTER_CONTROL,
    CALENDAR,
    REMINDERS,
    MEMORY,
    PHONE,
    VISION,
    LOCAL_AI,
    NETWORK,
)

# Tool → primary capability
TOOL_CAPABILITY: Dict[str, str] = {
    "web_search": WEB_SEARCH,
    "fetch_web_page": WEB_FETCH,
    "open_website": WEB_FETCH,
    "browser_launch": BROWSER_AUTOMATION,
    "browser_attach": BROWSER_AUTOMATION,
    "browser_open_url": BROWSER_AUTOMATION,
    "browser_read_title": BROWSER_AUTOMATION,
    "browser_read_page": BROWSER_AUTOMATION,
    "browser_find_element": BROWSER_AUTOMATION,
    "browser_click": BROWSER_AUTOMATION,
    "browser_type": BROWSER_AUTOMATION,
    "browser_select": BROWSER_AUTOMATION,
    "browser_check": BROWSER_AUTOMATION,
    "browser_scroll": BROWSER_AUTOMATION,
    "browser_back": BROWSER_AUTOMATION,
    "browser_forward": BROWSER_AUTOMATION,
    "browser_wait": BROWSER_AUTOMATION,
    "browser_screenshot": BROWSER_AUTOMATION,
    "browser_search_service": BROWSER_AUTOMATION,
    "browser_select_result": BROWSER_AUTOMATION,
    "browser_play_media": BROWSER_AUTOMATION,
    "close_browser_tab": BROWSER_AUTOMATION,
    "turn_on_device": HOME_ASSISTANT,
    "turn_off_device": HOME_ASSISTANT,
    "set_device_value": HOME_ASSISTANT,
    "get_device_state": HOME_ASSISTANT,
    "get_home_summary": HOME_ASSISTANT,
    "list_devices": HOME_ASSISTANT,
    "activate_scene": HOME_ASSISTANT,
    "play_media": MEDIA,
    "pause_media": MEDIA,
    "resume_media": MEDIA,
    "next_track": MEDIA,
    "previous_track": MEDIA,
    "get_now_playing": MEDIA,
    "get_volume": COMPUTER_CONTROL,
    "volume_up": COMPUTER_CONTROL,
    "volume_down": COMPUTER_CONTROL,
    "open_app": WINDOWS_APPS,
    "close_app": WINDOWS_APPS,
    "check_app_installed": WINDOWS_APPS,
    "list_windows": COMPUTER_CONTROL,
    "get_active_window": COMPUTER_CONTROL,
    "inspect_ui": COMPUTER_CONTROL,
    "type_text": COMPUTER_CONTROL,
    "click_ui_element": COMPUTER_CONTROL,
    "hotkey": COMPUTER_CONTROL,
    "focus_window": COMPUTER_CONTROL,
    "press_key": COMPUTER_CONTROL,
    "create_calendar_event": CALENDAR,
    "update_calendar_event": CALENDAR,
    "delete_calendar_event": CALENDAR,
    "get_next_event": CALENDAR,
    "get_today_schedule": CALENDAR,
    "get_calendar_events": CALENDAR,
    "create_reminder": REMINDERS,
    "update_reminder": REMINDERS,
    "cancel_reminder": REMINDERS,
    "list_reminders": REMINDERS,
    "remember_fact": MEMORY,
    "get_memory": MEMORY,
    "forget_fact": MEMORY,
    "set_preference": MEMORY,
    "get_preference": MEMORY,
    "get_phone_status": PHONE,
    "describe_screen": VISION,
    "find_ui_element": VISION,
    "ask_about_screen": VISION,
}

# When primary tool/capability is down, prefer these tools (still safety-gated).
TOOL_ALTERNATIVES: Dict[str, Tuple[str, ...]] = {
    "web_search": ("fetch_web_page", "open_website"),
    "fetch_web_page": ("web_search", "open_website"),
    "browser_open_url": ("open_website",),
    "browser_search_service": ("web_search", "open_website"),
    "play_media": ("open_app",),
}

# Intent family / keyword → capabilities to surface compactly
_FAMILY_CAPS: Dict[str, Tuple[str, ...]] = {
    "WEB": (WEB_SEARCH, WEB_FETCH, BROWSER_AUTOMATION, NETWORK),
    "SMART_HOME": (HOME_ASSISTANT,),
    "MEDIA": (MEDIA, WINDOWS_APPS),
    "APP": (WINDOWS_APPS,),
    "COMPUTER": (COMPUTER_CONTROL, WINDOWS_APPS, VISION),
    "VISION": (VISION, COMPUTER_CONTROL),
    "CALENDAR": (CALENDAR,),
    "REMINDER": (REMINDERS,),
    "KNOWLEDGE": (WEB_SEARCH, WEB_FETCH, MEMORY, LOCAL_AI),
    "SYSTEM": (COMPUTER_CONTROL, WINDOWS_APPS),
    "PREFERENCE": (MEMORY,),
    "UNKNOWN": (WEB_SEARCH, LOCAL_AI, MEMORY),
}

# IntentFamily / HUD names are NOT extra Cap ids. Lookup aliases fold them
# onto the canonical catalog so get("SYSTEM") never looks missing.
_LOOKUP_ALIASES: Dict[str, str] = {
    "SYSTEM": COMPUTER_CONTROL,
    "COMPUTER": COMPUTER_CONTROL,
    "INTERNET": WEB_SEARCH,
    "BROWSER": BROWSER_AUTOMATION,
    "HOMEASSISTANT": HOME_ASSISTANT,
}

_LABELS_PL: Dict[str, str] = {
    WEB_SEARCH: "wyszukiwanie w internecie",
    WEB_FETCH: "pobieranie stron",
    BROWSER_AUTOMATION: "automatyzacja przeglądarki",
    HOME_ASSISTANT: "Home Assistant",
    MEDIA: "odtwarzanie multimediów",
    WINDOWS_APPS: "aplikacje Windows",
    COMPUTER_CONTROL: "sterowanie komputerem",
    CALENDAR: "kalendarz",
    REMINDERS: "przypomnienia",
    MEMORY: "pamięć",
    PHONE: "telefon / Companion",
    VISION: "widzenie ekranu",
    LOCAL_AI: "lokalny model AI",
    NETWORK: "sieć",
}

# Genitive / "dostępu do …" forms for UNAVAILABLE copy.
_LABELS_ACCESS_PL: Dict[str, str] = {
    WEB_SEARCH: "wyszukiwania internetowego",
    WEB_FETCH: "pobierania stron",
    BROWSER_AUTOMATION: "automatyzacji przeglądarki",
    HOME_ASSISTANT: "Home Assistant",
    MEDIA: "odtwarzania multimediów",
    WINDOWS_APPS: "aplikacji Windows",
    COMPUTER_CONTROL: "sterowania komputerem",
    CALENDAR: "kalendarza",
    REMINDERS: "przypomnień",
    MEMORY: "pamięci",
    PHONE: "telefonu / Companion",
    VISION: "widzenia ekranu",
    LOCAL_AI: "lokalnego modelu AI",
    NETWORK: "sieci",
}

HEALTH_TTL_SEC = 20.0


@dataclass
class CapabilityRecord:
    id: str
    status: CapStatus = CapStatus.UNAVAILABLE
    supported_operations: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    last_health_check: float = 0.0
    failure_reason: str = ""
    detail: str = ""
    alternatives: List[str] = field(default_factory=list)

    @property
    def probed(self) -> bool:
        """False until a health probe wrote last_health_check — blank ≠ offline."""
        return float(self.last_health_check or 0.0) > 0.0

    @property
    def usable(self) -> bool:
        if not self.probed:
            return False
        return self.status in (CapStatus.AVAILABLE, CapStatus.DEGRADED)

    @property
    def label_pl(self) -> str:
        return _LABELS_PL.get(self.id, self.id)

    def spoken_failure(self) -> str:
        """Honest user-facing copy — never 'nie potrafię' when the tool exists."""
        if not self.probed:
            # Never claim dependency offline before the first probe finishes.
            return "Chwila — sprawdzam, czy mam do tego dostęp."
        label = self.label_pl
        access = _LABELS_ACCESS_PL.get(self.id, label)
        if self.status == CapStatus.DISABLED:
            return f"Funkcja {label} jest wyłączona."
        if self.status == CapStatus.BLOCKED:
            return f"Funkcja {label} jest zablokowana ze względów bezpieczeństwa."
        if self.status == CapStatus.DEGRADED:
            reason = self.failure_reason or self.detail
            if reason:
                return f"{label.capitalize()} działa w trybie ograniczonym: {reason}."
            return f"{label.capitalize()} działa w trybie ograniczonym."
        # UNAVAILABLE — prefer concrete reason, then named dependency language.
        reason = (self.failure_reason or "").strip()
        if reason:
            return f"Nie mam teraz dostępu do {access} ({reason})."
        if self.id in (
            HOME_ASSISTANT,
            PHONE,
            LOCAL_AI,
            CALENDAR,
            MEDIA,
            NETWORK,
        ):
            return f"Nie mogę teraz połączyć się z {label}."
        if self.id in (WEB_SEARCH, WEB_FETCH, BROWSER_AUTOMATION):
            return f"Nie mogę teraz połączyć się z internetem ({label})."
        return f"Nie mam teraz dostępu do {access}."

    def to_compact(self) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "id": self.id,
            "status": self.status.value if self.probed else CapStatus.DEGRADED.value,
            "ops": list(self.supported_operations[:8]),
            "tools": list(self.tools[:10]),
        }
        if not self.probed:
            row["reason"] = "health check in progress"
            row["say"] = self.spoken_failure()
            row["probed"] = False
        elif not self.usable:
            row["reason"] = (self.failure_reason or self.detail or self.status.value)[:120]
            row["say"] = self.spoken_failure()
        elif self.status == CapStatus.DEGRADED and (self.failure_reason or self.detail):
            row["reason"] = (self.failure_reason or self.detail)[:120]
        if self.alternatives:
            row["alt_tools"] = list(self.alternatives[:5])
        return row

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status.value,
            "supported_operations": list(self.supported_operations),
            "dependencies": list(self.dependencies),
            "tools": list(self.tools),
            "last_health_check": self.last_health_check,
            "failure_reason": self.failure_reason,
            "detail": self.detail,
            "alternatives": list(self.alternatives),
            "usable": self.usable,
        }


def _tools_for(cap_id: str) -> List[str]:
    return sorted(name for name, cid in TOOL_CAPABILITY.items() if cid == cap_id)


def _blank_catalog() -> Dict[str, CapabilityRecord]:
    specs: Dict[str, Tuple[List[str], List[str], List[str]]] = {
        # ops, deps, alt tools
        WEB_SEARCH: (["search"], ["network", "web_provider"], ["fetch_web_page", "open_website"]),
        WEB_FETCH: (["fetch", "open_url"], ["network"], ["web_search", "open_website"]),
        BROWSER_AUTOMATION: (
            ["launch", "navigate", "click", "type", "read"],
            ["playwright", "browser_opt_in"],
            ["open_website", "web_search"],
        ),
        HOME_ASSISTANT: (
            ["turn_on", "turn_off", "set_value", "get_state", "scene"],
            ["home_assistant", "network"],
            [],
        ),
        MEDIA: (["play", "pause", "resume", "skip", "now_playing"], ["media_backend"], ["open_app"]),
        WINDOWS_APPS: (["open", "close", "check_installed"], [], []),
        COMPUTER_CONTROL: (["focus", "type", "click", "hotkey", "inspect", "volume"], [], []),
        CALENDAR: (["create", "update", "delete", "list", "next"], ["calendar"], []),
        REMINDERS: (["create", "update", "cancel", "list"], ["reminders"], []),
        MEMORY: (["remember", "recall", "forget", "preference"], [], []),
        PHONE: (["status", "companion"], ["companion_or_twilio"], []),
        VISION: (["describe", "find", "ask"], ["vision"], ["inspect_ui"]),
        LOCAL_AI: (["chat", "plan"], ["ollama"], []),
        NETWORK: (["connectivity"], [], []),
    }
    rows: Dict[str, CapabilityRecord] = {}
    for cap_id, (ops, deps, alts) in specs.items():
        rows[cap_id] = CapabilityRecord(
            id=cap_id,
            supported_operations=list(ops),
            dependencies=list(deps),
            tools=_tools_for(cap_id),
            alternatives=list(alts),
        )
    return rows


class CapabilityRegistry:
    def __init__(self) -> None:
        self._caps: Dict[str, CapabilityRecord] = _blank_catalog()
        self._last_refresh: float = 0.0
        self._probing: bool = False
        self._staging: Optional[Dict[str, CapabilityRecord]] = None

    def get(self, cap_id: str) -> Optional[CapabilityRecord]:
        # Publish path: never force readers onto an in-flight blank staging catalog.
        if not self._last_refresh and not self._probing:
            self.refresh()
        key = str(cap_id or "").upper().replace("-", "_").replace(" ", "")
        key = _LOOKUP_ALIASES.get(key, key)
        return self._caps.get(key)

    def ids_for_family(self, intent_family: str) -> Tuple[str, ...]:
        family = str(intent_family or "").upper()
        if family in _FAMILY_CAPS:
            return _FAMILY_CAPS[family]
        alias = _LOOKUP_ALIASES.get(family)
        if alias:
            return (alias,)
        if family in self._caps:
            return (family,)
        return ()

    def all(self) -> Dict[str, CapabilityRecord]:
        if not self._last_refresh and not self._probing:
            self.refresh()
        return dict(self._caps)

    def refresh(self, *, force: bool = False) -> Dict[str, CapabilityRecord]:
        now = time.time()
        if not force and self._last_refresh and (now - self._last_refresh) < HEALTH_TTL_SEC:
            return {cid: rec for cid, rec in self._caps.items()}
        if self._probing:
            # Nested/mid-probe read keeps last published snapshot (may be unprobed on first run).
            return {cid: rec for cid, rec in self._caps.items()}
        self._probing = True
        try:
            staging = _blank_catalog()
            self._staging = staging
            self._probe_all()
            # Atomic publish — blank staging never becomes the public catalog mid-probe.
            self._caps = staging
            self._last_refresh = time.time()
            self._sync_health_registry()
        finally:
            self._probing = False
            self._staging = None
        return {cid: rec for cid, rec in self._caps.items()}

    def snapshot(self, *, force: bool = False) -> Dict[str, Dict[str, Any]]:
        self.refresh(force=force)
        return {cid: rec.to_dict() for cid, rec in self._caps.items()}

    def compact_for_goal(
        self,
        *,
        goal: str = "",
        intent_family: str = "",
        tool_names: Optional[Sequence[str]] = None,
        max_items: int = 8,
    ) -> List[Dict[str, Any]]:
        """Compact capability rows for the planner prompt (not full schemas)."""
        self.refresh()
        wanted: List[str] = []

        def _add(ids: Sequence[str]) -> None:
            for item in ids:
                key = str(item or "").upper()
                if key in self._caps and key not in wanted:
                    wanted.append(key)

        family = str(intent_family or "").upper()
        if family in _FAMILY_CAPS:
            _add(_FAMILY_CAPS[family])
        for name in tool_names or ():
            cap = TOOL_CAPABILITY.get(str(name or ""))
            if cap:
                _add((cap,))
        lowered = str(goal or "").lower()
        if any(tok in lowered for tok in ("szukaj", "wyszuk", "internet", "google", "aktualn")):
            _add((WEB_SEARCH, WEB_FETCH, NETWORK))
        if any(tok in lowered for tok in ("przeglądark", "przegladark", "playwright", "na stronie")):
            _add((BROWSER_AUTOMATION,))
        if any(tok in lowered for tok in ("światł", "swiatl", "lamp", "klima", "domu", "scen")):
            _add((HOME_ASSISTANT,))
        if any(tok in lowered for tok in ("muzyk", "spotify", "puść", "pusc")):
            _add((MEDIA,))
        if any(tok in lowered for tok in ("telefon", "companion", "numer")):
            _add((PHONE,))
        if not wanted:
            _add((LOCAL_AI, MEMORY, WINDOWS_APPS, WEB_SEARCH))
        # Always surface usable matches first, then blocked/disabled for honesty.
        usable = [cid for cid in wanted if self._caps[cid].usable]
        other = [cid for cid in wanted if cid not in usable]
        ordered = (usable + other)[:max_items]
        return [self._caps[cid].to_compact() for cid in ordered]

    def filter_tool_catalog(
        self,
        catalog: Sequence[Dict[str, Any]],
        *,
        include_alternatives: bool = True,
    ) -> List[Dict[str, Any]]:
        """Annotate tools with capability status.

        Drop only UNAVAILABLE/BLOCKED primaries (dependency offline / blocked).
        DISABLED stays in the shortlist so the planner can report “wyłączone”
        honestly; AVAILABLE/DEGRADED always remain.
        """
        self.refresh()
        by_name = {str(item.get("name") or ""): item for item in catalog}
        kept: List[Dict[str, Any]] = []
        kept_names: List[str] = []
        dropped_for_alt: List[str] = []

        for item in catalog:
            name = str(item.get("name") or "")
            if not name:
                continue
            cap_id = TOOL_CAPABILITY.get(name)
            if cap_id is None:
                if item.get("available", True):
                    kept.append(item)
                    kept_names.append(name)
                continue
            rec = self._caps.get(cap_id)
            row = dict(item)
            if rec is not None:
                row["capability"] = cap_id
                row["capability_status"] = rec.status.value
            # Unprobed ≠ offline — keep tools until health check publishes.
            if rec is None or (not rec.probed) or rec.status in (
                CapStatus.AVAILABLE,
                CapStatus.DEGRADED,
                CapStatus.DISABLED,
            ):
                kept.append(row)
                kept_names.append(name)
            else:
                dropped_for_alt.append(name)

        if include_alternatives:
            for name in dropped_for_alt:
                for alt in TOOL_ALTERNATIVES.get(name, ()):
                    if alt in by_name and alt not in kept_names:
                        alt_cap = TOOL_CAPABILITY.get(alt)
                        alt_rec = self._caps.get(alt_cap) if alt_cap else None
                        if alt_rec is None or alt_rec.status in (
                            CapStatus.AVAILABLE,
                            CapStatus.DEGRADED,
                            CapStatus.DISABLED,
                        ):
                            alt_row = dict(by_name[alt])
                            if alt_rec is not None:
                                alt_row["capability"] = alt_cap
                                alt_row["capability_status"] = alt_rec.status.value
                                alt_row["alternative_for"] = name
                            kept.append(alt_row)
                            kept_names.append(alt)
        return kept

    def gate_tool(self, tool_name: str):
        """Return ToolResult when capability forbids execution; else None.

        AVAILABLE/DEGRADED/DISABLED → None (handlers own DISABLED messaging).
        UNAVAILABLE/BLOCKED → honest dependency / safety message (not a tool failure).
        """
        from core.tool_result import ToolResult

        self.refresh()
        cap_id = TOOL_CAPABILITY.get(str(tool_name or ""))
        if not cap_id:
            return None
        rec = self._caps.get(cap_id)
        if rec is None or rec.status in (
            CapStatus.AVAILABLE,
            CapStatus.DEGRADED,
            CapStatus.DISABLED,
        ):
            return None
        error_code = {
            CapStatus.BLOCKED: "CAPABILITY_BLOCKED",
            CapStatus.UNAVAILABLE: "CAPABILITY_UNAVAILABLE",
        }.get(rec.status, "CAPABILITY_UNAVAILABLE")
        return ToolResult.unavailable(
            rec.spoken_failure(),
            tool_name=str(tool_name or ""),
            data={
                "capability": cap_id,
                "capability_status": rec.status.value,
                "error_code": error_code,
                "failure_reason": rec.failure_reason or rec.detail,
                "alternatives": list(rec.alternatives),
            },
            error_code=error_code,
            error_message=rec.spoken_failure(),
            retryable=rec.status == CapStatus.UNAVAILABLE,
        )

    def alternatives_for_tool(self, tool_name: str) -> List[str]:
        self.refresh()
        name = str(tool_name or "")
        out: List[str] = []
        for alt in TOOL_ALTERNATIVES.get(name, ()):
            cap_id = TOOL_CAPABILITY.get(alt)
            rec = self._caps.get(cap_id) if cap_id else None
            if rec is None or rec.usable:
                out.append(alt)
        return out

    def apply_tool_availability(self, registry) -> None:
        """Best-effort sync of Tool.available from capability status."""
        self.refresh()
        try:
            tools = registry.list_tools()
        except Exception:
            return
        for tool in tools:
            cap_id = TOOL_CAPABILITY.get(tool.name)
            if not cap_id:
                continue
            rec = self._caps.get(cap_id)
            if rec is None:
                continue
            # Unprobed blank must not mark tools unavailable (false offline).
            # DISABLED stays available so handlers own messaging (see gate_tool).
            if not rec.probed:
                tool.available = True
            elif rec.status == CapStatus.DISABLED:
                tool.available = True
            else:
                tool.available = bool(rec.usable)

    # --- probes ---

    def _set(
        self,
        cap_id: str,
        status: CapStatus,
        *,
        reason: str = "",
        detail: str = "",
    ) -> None:
        target = self._staging if self._staging is not None else self._caps
        rec = target.get(cap_id)
        if rec is None:
            return
        rec.status = status
        rec.failure_reason = reason
        rec.detail = detail
        rec.last_health_check = time.time()
        if self._staging is None:
            # Direct publish (tests / runtime override) is catalog truth.
            self._last_refresh = rec.last_health_check

    def _probe_all(self) -> None:
        self._probe_network()
        self._probe_local_ai()
        self._probe_web()
        self._probe_browser()
        self._probe_ha()
        self._probe_media()
        self._probe_calendar()
        self._probe_reminders()
        self._probe_phone()
        self._probe_vision()
        # Local always-on capabilities
        self._set(WINDOWS_APPS, CapStatus.AVAILABLE, detail="local")
        self._set(COMPUTER_CONTROL, CapStatus.AVAILABLE, detail="local")
        self._set(MEMORY, CapStatus.AVAILABLE, detail="local memory store")

    def _probe_network(self) -> None:
        try:
            from core.health import get_health_registry

            item = get_health_registry().get("network")
            if item is not None:
                raw = str(getattr(item.status, "value", item.status) or "")
                if raw == "READY":
                    self._set(NETWORK, CapStatus.AVAILABLE, detail=str(item.detail or ""))
                    return
                if raw == "DEGRADED":
                    self._set(NETWORK, CapStatus.DEGRADED, reason=str(item.detail or "degraded"))
                    return
        except Exception:
            pass
        # Soft default: assume network unless proven otherwise (web probe may refine).
        self._set(NETWORK, CapStatus.AVAILABLE, detail="assumed")

    def _probe_local_ai(self) -> None:
        if not get_config_value("ai", "agent_enabled", default=True):
            self._set(LOCAL_AI, CapStatus.DISABLED, reason="agent_enabled=false")
            return
        try:
            from core.health import get_health_registry

            for key in ("ollama", "ai"):
                item = get_health_registry().get(key)
                if item is None:
                    continue
                raw = str(getattr(item.status, "value", item.status) or "")
                detail = str(getattr(item, "detail", "") or "")
                if raw == "READY":
                    self._set(LOCAL_AI, CapStatus.AVAILABLE, detail=detail)
                    return
                if raw == "DEGRADED":
                    self._set(LOCAL_AI, CapStatus.DEGRADED, reason=detail or "degraded")
                    return
                if raw in ("NOT CONFIGURED", "NOT_CONFIGURED"):
                    self._set(LOCAL_AI, CapStatus.DISABLED, reason=detail or "not configured")
                    return
                if raw in ("UNAVAILABLE", "ERROR"):
                    self._set(
                        LOCAL_AI,
                        CapStatus.UNAVAILABLE,
                        reason=detail or "Ollama niedostępna",
                    )
                    return
        except Exception:
            pass
        # No live Ollama call here — doctor / runtime sets health. Assume usable.
        model = get_config_value("ai", "ollama_model", default="")
        self._set(LOCAL_AI, CapStatus.AVAILABLE, detail=str(model or "ollama assumed"))

    def _probe_web(self) -> None:
        web_cfg = {}
        try:
            from Config import config as app_config

            block = app_config.get("web")
            if isinstance(block, dict):
                web_cfg = block
        except Exception:
            pass
        if web_cfg.get("enabled") is False:
            self._set(WEB_SEARCH, CapStatus.DISABLED, reason="web.enabled=false")
            self._set(WEB_FETCH, CapStatus.DISABLED, reason="web.enabled=false")
            return
        try:
            from core.web_search_provider import provider_available

            if provider_available():
                self._set(WEB_SEARCH, CapStatus.AVAILABLE, detail="provider ready")
            else:
                self._set(
                    WEB_SEARCH,
                    CapStatus.UNAVAILABLE,
                    reason="brak dostępnego providera wyszukiwania",
                )
        except Exception as exc:
            self._set(WEB_SEARCH, CapStatus.UNAVAILABLE, reason=str(exc)[:80])

        fetch_on = web_cfg.get("fetch_enabled", True)
        if fetch_on is False:
            self._set(WEB_FETCH, CapStatus.DISABLED, reason="fetch_enabled=false")
        else:
            # open_website / fetch are local OS + HTTP — available unless web fully off
            self._set(WEB_FETCH, CapStatus.AVAILABLE, detail="fetch/open_website")

    def _probe_browser(self) -> None:
        try:
            from core.browser_agent import browser_opt_in, browser_runtime_available, playwright_installed

            if not browser_opt_in():
                self._set(
                    BROWSER_AUTOMATION,
                    CapStatus.DISABLED,
                    reason="integrations.browser.enabled=false",
                )
                return
            if browser_runtime_available():
                self._set(BROWSER_AUTOMATION, CapStatus.AVAILABLE, detail="Playwright ready")
                return
            if not playwright_installed():
                self._set(
                    BROWSER_AUTOMATION,
                    CapStatus.UNAVAILABLE,
                    reason="Playwright nie jest zainstalowany",
                )
                return
            self._set(
                BROWSER_AUTOMATION,
                CapStatus.UNAVAILABLE,
                reason="przeglądarka niedostępna",
            )
        except Exception as exc:
            self._set(BROWSER_AUTOMATION, CapStatus.UNAVAILABLE, reason=str(exc)[:80])

    def _probe_ha(self) -> None:
        enabled = bool(get_config_value("smart_home", "enabled", default=False))
        try:
            from integrations.base import integration_enabled

            enabled = enabled and integration_enabled("home_assistant")
        except Exception:
            pass
        if not enabled:
            self._set(HOME_ASSISTANT, CapStatus.DISABLED, reason="smart_home disabled")
            return
        url = str(get_config_value("smart_home", "url", default="") or "").strip()
        token = str(
            get_config_value("smart_home", "token", default="")
            or get_config_value("smart_home", "access_token", default="")
            or ""
        ).strip()
        if not url:
            self._set(HOME_ASSISTANT, CapStatus.UNAVAILABLE, reason="brak URL Home Assistant")
            return
        try:
            from core.health import get_health_registry

            for key in ("home_assistant", "ha"):
                item = get_health_registry().get(key)
                if item is None:
                    continue
                raw = str(getattr(item.status, "value", item.status) or "")
                detail = str(getattr(item, "detail", "") or "")
                if raw == "READY":
                    self._set(HOME_ASSISTANT, CapStatus.AVAILABLE, detail=detail or url[:40])
                    return
                if raw == "DEGRADED":
                    self._set(HOME_ASSISTANT, CapStatus.DEGRADED, reason=detail or "degraded")
                    return
                if raw in ("UNAVAILABLE", "ERROR"):
                    self._set(
                        HOME_ASSISTANT,
                        CapStatus.UNAVAILABLE,
                        reason=detail or "Home Assistant niedostępny",
                    )
                    return
        except Exception:
            pass
        # Configured — avoid live maybe_refresh on every planner turn.
        if not token:
            self._set(
                HOME_ASSISTANT,
                CapStatus.DEGRADED,
                reason="brak tokenu — możliwy brak autoryzacji",
                detail=url[:40],
            )
            return
        try:
            from HomeAssistant import get_registry

            registry = get_registry()
            entities = getattr(registry, "entities", None)
            if entities is None and hasattr(registry, "all"):
                try:
                    entities = registry.all()
                except Exception:
                    entities = None
            count = len(entities) if entities is not None else -1
            if count == 0:
                self._set(
                    HOME_ASSISTANT,
                    CapStatus.DEGRADED,
                    reason="brak załadowanych urządzeń",
                    detail=url[:40],
                )
            else:
                detail = url[:40] if count < 0 else f"{count} devices; {url[:40]}"
                self._set(HOME_ASSISTANT, CapStatus.AVAILABLE, detail=detail)
        except Exception as exc:
            self._set(
                HOME_ASSISTANT,
                CapStatus.UNAVAILABLE,
                reason=str(exc)[:80] or "Home Assistant niedostępny",
            )

    def _probe_media(self) -> None:
        try:
            from integrations.base import Availability, integration_enabled
            from integrations.media.provider import get_primary_media_provider

            if not integration_enabled("media"):
                self._set(MEDIA, CapStatus.DISABLED, reason="media disabled")
                return
            status = get_primary_media_provider().availability()
            if status is Availability.AVAILABLE:
                self._set(MEDIA, CapStatus.AVAILABLE, detail="backend ready")
            elif status is Availability.DISABLED:
                self._set(MEDIA, CapStatus.DISABLED, reason="media disabled")
            elif status in (Availability.NOT_CONFIGURED, Availability.AUTH_REQUIRED):
                self._set(MEDIA, CapStatus.DEGRADED, reason=status.value)
            else:
                self._set(MEDIA, CapStatus.UNAVAILABLE, reason=status.value)
        except Exception as exc:
            # Windows media often still works via open_app — degrade, don't hard-fail.
            self._set(MEDIA, CapStatus.DEGRADED, reason=str(exc)[:80] or "media probe failed")

    def _probe_calendar(self) -> None:
        try:
            from integrations.base import Availability
            from integrations.calendar.provider import get_calendar_provider

            status = get_calendar_provider().availability()
            if status is Availability.AVAILABLE:
                self._set(CALENDAR, CapStatus.AVAILABLE)
            elif status is Availability.DISABLED:
                self._set(CALENDAR, CapStatus.DISABLED, reason="calendar disabled")
            elif status in (Availability.NOT_CONFIGURED, Availability.AUTH_REQUIRED):
                self._set(CALENDAR, CapStatus.UNAVAILABLE, reason=status.value)
            else:
                self._set(CALENDAR, CapStatus.UNAVAILABLE, reason=status.value)
        except Exception as exc:
            self._set(CALENDAR, CapStatus.UNAVAILABLE, reason=str(exc)[:80])

    def _probe_reminders(self) -> None:
        try:
            from integrations.base import integration_enabled

            if not integration_enabled("reminders"):
                self._set(REMINDERS, CapStatus.DISABLED, reason="reminders disabled")
                return
            from integrations.reminders.store import get_reminder_store

            get_reminder_store()
            self._set(REMINDERS, CapStatus.AVAILABLE, detail="local store")
        except Exception as exc:
            self._set(REMINDERS, CapStatus.UNAVAILABLE, reason=str(exc)[:80])

    def _probe_phone(self) -> None:
        companion_on = bool(get_config_value("companion", "enabled", default=False))
        twilio_on = False
        try:
            from integrations.twilio_provider import twilio_enabled

            twilio_on = bool(twilio_enabled())
        except Exception:
            twilio_on = False

        if not companion_on and not twilio_on:
            self._set(
                PHONE,
                CapStatus.DISABLED,
                reason="companion i Twilio wyłączone",
            )
            return

        online = False
        if companion_on:
            try:
                from core.companion.service import get_companion_service

                service = get_companion_service()
                if service is not None and getattr(service, "registry", None) is not None:
                    for device in service.registry.list_active():
                        if service.registry.is_online(device.device_id):
                            online = True
                            break
                else:
                    # Service not started — enabled but no live connection yet.
                    online = False
            except Exception:
                online = False

        if companion_on and online:
            self._set(PHONE, CapStatus.AVAILABLE, detail="companion device online")
        elif companion_on and not online and twilio_on:
            self._set(
                PHONE,
                CapStatus.DEGRADED,
                reason="Companion włączony, brak połączonego urządzenia; Twilio dostępne",
            )
        elif companion_on and not online:
            self._set(
                PHONE,
                CapStatus.UNAVAILABLE,
                reason="brak połączonego urządzenia Companion",
            )
        elif twilio_on:
            self._set(PHONE, CapStatus.AVAILABLE, detail="Twilio")
        else:
            self._set(PHONE, CapStatus.UNAVAILABLE, reason="telefon niedostępny")

    def _probe_vision(self) -> None:
        if not get_config_value("ai", "vision_enabled", default=True):
            self._set(VISION, CapStatus.DISABLED, reason="vision_enabled=false")
            return
        model = get_config_value("ai", "vision_model", default="")
        local = self._caps.get(LOCAL_AI)
        if not model:
            self._set(VISION, CapStatus.DEGRADED, reason="UIA/OCR only (no vision_model)")
            return
        if local and local.status == CapStatus.AVAILABLE:
            self._set(VISION, CapStatus.AVAILABLE, detail=str(model))
        elif local and local.status == CapStatus.DEGRADED:
            self._set(VISION, CapStatus.DEGRADED, reason="Ollama degraded", detail=str(model))
        else:
            self._set(
                VISION,
                CapStatus.DEGRADED,
                reason="model vision skonfigurowany; Ollama może być offline",
                detail=str(model),
            )

    def _sync_health_registry(self) -> None:
        """Mirror key caps into HealthRegistry for HUD/doctor consumers."""
        try:
            from core.health import HealthStatus, get_health_registry

            reg = get_health_registry()
            mapping = {
                "ollama": LOCAL_AI,
                "home_assistant": HOME_ASSISTANT,
                "ha": HOME_ASSISTANT,
                "network": NETWORK,
                "vision": VISION,
                "media": MEDIA,
                "calendar": CALENDAR,
                "reminders": REMINDERS,
                "companion": PHONE,
            }
            status_map = {
                CapStatus.AVAILABLE: HealthStatus.READY,
                CapStatus.DEGRADED: HealthStatus.DEGRADED,
                CapStatus.DISABLED: HealthStatus.NOT_CONFIGURED,
                CapStatus.UNAVAILABLE: HealthStatus.UNAVAILABLE,
                CapStatus.BLOCKED: HealthStatus.UNAVAILABLE,
            }
            for health_name, cap_id in mapping.items():
                rec = self._caps.get(cap_id)
                if rec is None:
                    continue
                reg.set(
                    health_name,
                    status_map.get(rec.status, HealthStatus.UNAVAILABLE),
                    detail=rec.detail or rec.failure_reason,
                    error=rec.failure_reason if rec.status == CapStatus.UNAVAILABLE else "",
                )
        except Exception:
            pass


_REGISTRY: Optional[CapabilityRegistry] = None


def get_capability_registry() -> CapabilityRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = CapabilityRegistry()
    return _REGISTRY


def reset_capability_registry() -> CapabilityRegistry:
    global _REGISTRY
    _REGISTRY = CapabilityRegistry()
    return _REGISTRY


def snapshot_capability_registry(*, force: bool = False) -> Dict[str, Dict[str, Any]]:
    return get_capability_registry().snapshot(force=force)


def compact_capabilities_for_planner(
    *,
    goal: str = "",
    intent_family: str = "",
    tool_names: Optional[Sequence[str]] = None,
    max_items: int = 8,
) -> List[Dict[str, Any]]:
    return get_capability_registry().compact_for_goal(
        goal=goal,
        intent_family=intent_family,
        tool_names=tool_names,
        max_items=max_items,
    )


def filter_catalog_by_capabilities(
    catalog: Sequence[Dict[str, Any]],
    *,
    include_alternatives: bool = True,
) -> List[Dict[str, Any]]:
    return get_capability_registry().filter_tool_catalog(
        catalog, include_alternatives=include_alternatives
    )


def capability_gate_for_tool(tool_name: str):
    return get_capability_registry().gate_tool(tool_name)


def inject_capabilities_hint(
    envelope: Any,
    *,
    goal: str = "",
    intent_family: str = "",
) -> Any:
    """Minimal Semantic Executive injection interface — attaches compact caps.

    Does not change decide_semantics; callers may pass the envelope through.
    Stores a compact list on envelope.payload JSON side-channel when possible,
    else sets attribute ``capability_hint`` (non-schema, best-effort).
    """
    if envelope is None:
        return None
    caps = compact_capabilities_for_planner(
        goal=goal or str(getattr(envelope, "user_goal", "") or ""),
        intent_family=intent_family
        or (
            (getattr(envelope, "allowed_domains", None) or [None])[0]
            if getattr(envelope, "allowed_domains", None)
            else ""
        ),
    )
    try:
        setattr(envelope, "capability_hint", caps)
    except Exception:
        pass
    return envelope
