# ==========================
# ELIAN — ActiveServiceContext (Phase 13 Stage B)
# ==========================
"""APP vs WEBSITE identity. Never claim active_app when only a website opened."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

KIND_APP = "APP"
KIND_WEBSITE = "WEBSITE"
KIND_NONE = "NONE"

CAP_OPEN = "OPEN"
CAP_CLOSE = "CLOSE"
CAP_MEDIA_PLAY = "MEDIA_PLAY"
CAP_MEDIA_PAUSE = "MEDIA_PAUSE"
CAP_MEDIA_NEXT = "MEDIA_NEXT"
CAP_MEDIA_PREVIOUS = "MEDIA_PREVIOUS"
CAP_IN_PAGE_SEARCH = "IN_PAGE_SEARCH"
CAP_CLICK = "CLICK"
CAP_PLAY_TRACK = "PLAY_TRACK"
CAP_READ_PAGE = "READ_PAGE"

STAGE_D_BROWSER = False


def _browser_stage_ready() -> bool:
    try:
        from core.browser_agent import browser_runtime_available

        return bool(browser_runtime_available())
    except Exception:
        return bool(STAGE_D_BROWSER)


@dataclass
class ActiveServiceContext:
    service_name: str = ""
    resolved_kind: str = KIND_NONE
    app_target: str = ""
    app_process: str = ""
    app_window: str = ""
    official_url: str = ""
    browser_backend: str = ""
    browser_tab_id: str = ""
    current_url: str = ""
    capabilities: List[str] = field(default_factory=list)
    opened_by_elian: bool = False
    last_action: str = ""
    updated_at: float = 0.0
    referent_confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "ActiveServiceContext":
        raw = dict(data or {})
        caps = raw.get("capabilities") or []
        return cls(
            service_name=str(raw.get("service_name") or ""),
            resolved_kind=str(raw.get("resolved_kind") or KIND_NONE),
            app_target=str(raw.get("app_target") or ""),
            app_process=str(raw.get("app_process") or ""),
            app_window=str(raw.get("app_window") or ""),
            official_url=str(raw.get("official_url") or ""),
            browser_backend=str(raw.get("browser_backend") or ""),
            browser_tab_id=str(raw.get("browser_tab_id") or ""),
            current_url=str(raw.get("current_url") or ""),
            capabilities=[str(item) for item in caps],
            opened_by_elian=bool(raw.get("opened_by_elian")),
            last_action=str(raw.get("last_action") or ""),
            updated_at=float(raw.get("updated_at") or 0.0),
            referent_confidence=float(raw.get("referent_confidence") or 1.0),
        )


_STORE: Dict[str, Any] = {"current": None, "history": []}


def reset_active_service() -> None:
    _STORE["current"] = None
    _STORE["history"] = []


def get_active_service() -> Optional[ActiveServiceContext]:
    current = _STORE.get("current")
    if isinstance(current, ActiveServiceContext):
        return current
    if isinstance(current, dict):
        return ActiveServiceContext.from_dict(current)
    return None


def set_active_service(service: ActiveServiceContext | None) -> None:
    _STORE["current"] = service
    if service is not None:
        history = list(_STORE.get("history") or [])
        history.append(service.to_dict())
        _STORE["history"] = history[-8:]


def service_history() -> List[Dict[str, Any]]:
    return list(_STORE.get("history") or [])


def capabilities_for(kind: str, *, service_name: str = "") -> List[str]:
    """Declare only capabilities the current runtime actually has."""
    resolved = str(kind or KIND_NONE)
    name = str(service_name or "").strip().lower()
    if resolved == KIND_APP:
        caps = [CAP_OPEN, CAP_CLOSE]
        if name in ("spotify", "youtube music", "youtube"):
            caps.extend(
                [CAP_MEDIA_PLAY, CAP_MEDIA_PAUSE, CAP_MEDIA_NEXT, CAP_MEDIA_PREVIOUS]
            )
        return caps
    if resolved == KIND_WEBSITE:
        caps = [CAP_OPEN]
        if _browser_stage_ready():
            caps.extend(
                [
                    CAP_CLOSE,
                    CAP_IN_PAGE_SEARCH,
                    CAP_CLICK,
                    CAP_PLAY_TRACK,
                    CAP_MEDIA_PLAY,
                    CAP_MEDIA_PAUSE,
                    CAP_MEDIA_NEXT,
                    CAP_MEDIA_PREVIOUS,
                    CAP_READ_PAGE,
                ]
            )
        return caps
    return []


def has_capability(kind: str, capability: str, *, service_name: str = "") -> bool:
    return str(capability or "") in capabilities_for(kind, service_name=service_name)


def note_service_from_observation(
    *,
    tool_name: str,
    arguments: Dict[str, Any] | None,
    observation: Dict[str, Any] | None,
) -> Optional[ActiveServiceContext]:
    args = dict(arguments or {})
    obs = dict(observation or {})
    data = dict(obs.get("data") or {})
    if not obs.get("success"):
        return get_active_service()
    kind = str(data.get("resolved_kind") or "").strip() or KIND_NONE
    name = str(
        data.get("canonical_name")
        or data.get("app")
        or args.get("name")
        or ""
    ).strip()
    if str(tool_name or "") == "open_app" and kind in (KIND_APP, KIND_WEBSITE) and name:
        process = ""
        if kind == KIND_APP:
            process = f"{name}.exe"
        service = ActiveServiceContext(
            service_name=name,
            resolved_kind=kind,
            app_target=name if kind == KIND_APP else "",
            app_process=process,
            official_url=str(data.get("official_url") or ""),
            current_url=str(data.get("official_url") or data.get("target") or ""),
            capabilities=capabilities_for(kind, service_name=name),
            opened_by_elian=True,
            last_action="open_service",
            updated_at=time.monotonic(),
            referent_confidence=1.0 if kind == KIND_APP else 0.9,
        )
        set_active_service(service)
        try:
            from core.world_state import note_service

            note_service(service)
        except Exception:
            pass
        return service
    if str(tool_name or "") in ("close_app", "close_browser_tab") and name:
        current = get_active_service()
        if current and current.service_name.lower() == name.lower():
            closed = ActiveServiceContext(
                service_name=name,
                resolved_kind=KIND_NONE,
                last_action="close_service",
                updated_at=time.monotonic(),
                capabilities=[],
            )
            set_active_service(closed)
        return get_active_service()
    return get_active_service()
