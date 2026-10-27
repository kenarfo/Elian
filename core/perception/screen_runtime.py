# ==========================
# ELIAN — ScreenPerceptionRuntime (C.5)
# ==========================
"""Persistent visual sensor. Not a second planner or chat loop."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from Config import get_config_value
from core.perception.change_detector import ChangeDetector, ScreenChange
from core.perception.foreground import (
    FakeForegroundProvider,
    ForegroundInfo,
    ForegroundProvider,
)
from core.perception.frame_provider import Frame, FrameProvider, MemoryFrameProvider
from core.perception.metrics import note_perception, perception_metrics
from core.perception.perception_fusion import fuse_elements
from core.perception.screen_state import ScreenState
from core.perception.visual_referent import VisualReferent, VisualReferentContext
from core.perception.vision_router import LocalVisionProvider, VisionResourcePolicy, VisionRouter

SCREEN_FRAME_UPDATED = "SCREEN_FRAME_UPDATED"
SCREEN_MAJOR_CHANGE = "SCREEN_MAJOR_CHANGE"
ACTIVE_WINDOW_CHANGED = "ACTIVE_WINDOW_CHANGED"
VISUAL_REFERENT_UPDATED = "VISUAL_REFERENT_UPDATED"
VISION_STARTED = "VISION_STARTED"
VISION_COMPLETED = "VISION_COMPLETED"
VISION_FAILED = "VISION_FAILED"

_RUNTIME: Optional["ScreenPerceptionRuntime"] = None


class ScreenPerceptionRuntime:
    def __init__(
        self,
        *,
        provider: Optional[FrameProvider] = None,
        vision: Optional[VisionRouter] = None,
        foreground_provider: Optional[ForegroundProvider] = None,
    ):
        self.mode = str(get_config_value("screen_perception", "mode", default="ACTIVE_LIGHT") or "ACTIVE_LIGHT")
        self.store_frames = bool(get_config_value("screen_perception", "store_frames", default=False))
        self.store_video = bool(get_config_value("screen_perception", "store_video", default=False))
        self.capture_fps = float(get_config_value("screen_perception", "capture_fps", default=20) or 20)
        self.provider = provider or MemoryFrameProvider()
        self.detector = ChangeDetector()
        self.vision = vision or VisionRouter(provider=LocalVisionProvider(), policy=VisionResourcePolicy())
        self.referents = VisualReferentContext()
        self.state = ScreenState()
        self.foreground = foreground_provider or FakeForegroundProvider()
        self.uia_probe = None
        self._started = False
        self._lock = threading.Lock()
        self._last_event_ts = 0.0
        self._prev_hwnd = 0
        self._previous_frame: Optional[Frame] = None
        self.disk_writes = 0
        # Default: never persist frames unless an explicit debug flag is on.
        if not bool(get_config_value("screen_perception", "debug_save_frames", default=False)):
            self.store_frames = False
            self.store_video = False

    def start(self) -> None:
        self.provider.start()
        self._started = True
        self.state.privacy["capture_active"] = True
        self.state.privacy["frame_storage_enabled"] = False
        self.state.perception_health = "active"
        threading.Thread(target=self._probe_vision, daemon=True).start()
        try:
            self.foreground.start(self._on_foreground)
        except Exception:
            pass
        print(
            f"[PERCEPTION] provider={self.provider.health().get('provider')} "
            f"mode={self.mode} capture_active=1 frame_id={self.state.frame_id} "
            f"major_change=0 active_window=-"
        )

    def stop(self) -> None:
        self.provider.stop()
        self._started = False
        self._previous_frame = None
        self.state.privacy["capture_active"] = False
        self.state.perception_health = "stopped"
        try:
            self.foreground.stop()
        except Exception:
            pass

    def _probe_vision(self) -> None:
        try:
            self.vision.provider.probe(timeout=1.5)
        except Exception:
            pass

    def ingest_frame(self, frame: Frame, *, hwnd: int = 0, title: str = "", process: str = "") -> ScreenChange:
        if self.store_frames or self.store_video:
            self.store_frames = False
            self.store_video = False
        self._previous_frame = self.provider.latest_frame() if self.provider.latest_frame() is not frame else self._previous_frame
        change = self.detector.observe(frame, hwnd=hwnd or frame.hwnd)
        new_hwnd = int(hwnd or frame.hwnd or 0)
        window_event = None
        with self._lock:
            self.state.frame_id = int(frame.frame_id)
            self.state.timestamp = float(frame.timestamp or time.time())
            self.state.resolution = {"width": frame.width, "height": frame.height}
            prev_hwnd = int((self.state.active_window or {}).get("hwnd") or self._prev_hwnd or 0)
            if new_hwnd or title or process:
                if prev_hwnd and new_hwnd and prev_hwnd != new_hwnd:
                    change.window_changed = True
                    change.major = True
                    self._invalidate_window_state_locked(prev_hwnd)
                    window_event = {"hwnd": new_hwnd, "title": title, "process": process}
                self.state.active_window = {
                    "hwnd": new_hwnd,
                    "title": title,
                    "process": process,
                    "bounds": dict(frame.window_bounds or {}),
                }
                self.state.ui_snapshot_stale = False
                self._prev_hwnd = new_hwnd
            self.state.visual["change_regions"] = list(change.changed_regions)
            self.state.visual["scene_hash"] = change.scene_hash
            self.state.privacy["frame_storage_enabled"] = False
        if window_event:
            self._publish(ACTIVE_WINDOW_CHANGED, window_event)
        if change.major:
            self._publish(SCREEN_MAJOR_CHANGE, {"frame_id": frame.frame_id, "ratio": change.changed_ratio})
            print(
                f"[PERCEPTION] provider={self.provider.health().get('provider')} "
                f"mode={self.mode} capture_active={1 if self._started else 0} "
                f"frame_id={frame.frame_id} major_change=1 "
                f"active_window={title or '-'}"
            )
        else:
            now = time.time()
            if now - self._last_event_ts >= 1.0:
                self._last_event_ts = now
                self._publish(SCREEN_FRAME_UPDATED, {"frame_id": frame.frame_id}, throttle=True)
        try:
            from core.world_state import note_screen

            note_screen(self.state.to_public_dict())
        except Exception:
            pass
        return change

    def observe_window_meta(
        self,
        *,
        hwnd: int = 0,
        title: str = "",
        process: str = "",
        bounds: Optional[Dict[str, Any]] = None,
        focused: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Cheap ScreenState update without pixels / vision."""
        with self._lock:
            prev = int((self.state.active_window or {}).get("hwnd") or 0)
            changed = bool(prev and hwnd and int(prev) != int(hwnd))
            if changed:
                self._invalidate_window_state_locked(prev)
            self.state.active_window = {
                "hwnd": int(hwnd or 0),
                "title": title,
                "process": process,
                "bounds": dict(bounds or {}),
            }
            self.state.timestamp = time.time()
            if focused:
                self.state.ui_snapshot["focused_element"] = {
                    "element_id": focused.get("element_id"),
                    "role": focused.get("role"),
                    "name": focused.get("name") or focused.get("text"),
                }
            self.state.privacy["frame_storage_enabled"] = False
            if not changed:
                self.state.ui_snapshot_stale = False
        if prev and hwnd and int(prev) != int(hwnd):
            self._publish(ACTIVE_WINDOW_CHANGED, {"hwnd": hwnd, "title": title, "process": process})
        try:
            from core.world_state import note_screen

            note_screen(self.state.to_public_dict())
        except Exception:
            pass

    def get_state(self) -> ScreenState:
        return self.state

    def _on_foreground(self, info: ForegroundInfo) -> None:
        if info is None or not int(getattr(info, "hwnd", 0) or 0):
            return
        self.apply_foreground(info, publish=True)

    def apply_foreground(self, info: ForegroundInfo, *, publish: bool = True) -> bool:
        hwnd = int(getattr(info, "hwnd", 0) or 0)
        if not hwnd:
            return False
        title = str(getattr(info, "title", "") or "")
        process = str(getattr(info, "process", "") or "")
        bounds = dict(getattr(info, "bounds", None) or {})
        changed = False
        with self._lock:
            prev = int((self.state.active_window or {}).get("hwnd") or 0)
            changed = bool(prev and prev != hwnd)
            if changed:
                self._invalidate_window_state_locked(prev)
            self.state.active_window = {
                "hwnd": hwnd,
                "title": title,
                "process": process,
                "bounds": bounds,
            }
            self.state.timestamp = float(getattr(info, "timestamp", 0) or time.time())
            self.state.ui_snapshot_stale = bool(changed)
            self._prev_hwnd = hwnd
        if changed and publish:
            self._publish(ACTIVE_WINDOW_CHANGED, {"hwnd": hwnd, "title": title, "process": process})
        try:
            from core.world_state import note_screen

            note_screen(self.state.to_public_dict())
        except Exception:
            pass
        return True

    def _invalidate_window_state_locked(self, old_hwnd: int) -> None:
        previous = dict(self.state.active_window or {})
        if previous:
            history = list(self.state.window_history or [])
            history.append(previous)
            self.state.window_history = history[-8:]
        self.state.ui_snapshot = {}
        self.state.ui_snapshot_stale = True
        self.state.visual["referent"] = {}
        self.state.visual["scene_summary"] = ""
        last = getattr(self.referents, "last", None)
        if last is not None:
            self.referents.invalidate_hwnd(int(old_hwnd or 0))

    def invalidate_window_state(self, old_hwnd: int = 0) -> None:
        with self._lock:
            self._invalidate_window_state_locked(int(old_hwnd or 0))

    def refresh_foreground(self, *, force: bool = True) -> ForegroundInfo:
        info = self.foreground.current() if self.foreground is not None else ForegroundInfo()
        if info and int(info.hwnd or 0):
            cached = int((self.state.active_window or {}).get("hwnd") or 0)
            if force or cached != int(info.hwnd):
                self.apply_foreground(info, publish=cached != int(info.hwnd))
        return info

    def _cheap_uia(self, hwnd: int) -> list:
        if not hwnd:
            return []
        probe = getattr(self, "uia_probe", None)
        if callable(probe):
            try:
                rows = probe(hwnd) or []
                return [dict(item) if isinstance(item, dict) else item for item in rows]
            except Exception:
                return []
        return []

    def fresh_screen_observation(self, *, force: bool = True, max_age_ms: float = 500):
        from core.perception.screen_truth import ScreenObservation, app_label

        cached = dict(self.state.active_window or {})
        cached_hwnd = int(cached.get("hwnd") or 0)
        cached_app = app_label(str(cached.get("process") or ""), str(cached.get("title") or ""))
        age = float(self.state.observation_age_ms)
        need = bool(force or age > float(max_age_ms) or not cached_hwnd)
        refreshed = False
        source = "cached"
        info = None
        if need:
            info = self.refresh_foreground(force=True)
            refreshed = True
            source = "foreground"
        window = dict(self.state.active_window or {})
        hwnd = int(window.get("hwnd") or 0)
        if refreshed and (not info or not int(getattr(info, "hwnd", 0) or 0)):
            return ScreenObservation(
                ok=False,
                hwnd=0,
                title="",
                process="",
                age_ms=age,
                source="foreground",
                refreshed=True,
                certainty="UNCERTAIN",
                cached_hwnd=cached_hwnd,
                cached_app=cached_app,
                fresh_app="",
                used_stale=False,
            )
        if hwnd and (info is None or int(info.hwnd or 0) == hwnd):
            uia = self._cheap_uia(hwnd)
            if uia:
                source = "UIA"
                self.fuse_ui(uia)
                self.state.ui_snapshot_stale = False
        title = str(window.get("title") or "")
        process = str(window.get("process") or "")
        fresh_app = app_label(process, title)
        certainty = "UNCERTAIN"
        if hwnd and (title or process):
            certainty = "STRONG" if title else "BASIC"
        if cached_hwnd and hwnd and cached_hwnd != hwnd and not refreshed:
            # Would have answered cached window as current — refuse.
            from core.perception.metrics import note_perception

            note_perception("stale_screen_claim_rate")
            return ScreenObservation(
                ok=False,
                used_stale=True,
                certainty="UNCERTAIN",
                cached_hwnd=cached_hwnd,
                cached_app=cached_app,
                fresh_app=fresh_app,
                hwnd=hwnd,
                title=title,
                process=process,
                refreshed=refreshed,
                source=source,
            )
        return ScreenObservation(
            ok=bool(hwnd and certainty != "UNCERTAIN"),
            hwnd=hwnd,
            title=title,
            process=process,
            bounds=dict(window.get("bounds") or {}),
            age_ms=float(self.state.observation_age_ms),
            source=source,
            refreshed=refreshed,
            certainty=certainty,
            cached_hwnd=cached_hwnd,
            cached_app=cached_app,
            fresh_app=fresh_app,
            used_stale=False,
        )

    def get_latest_frame(self) -> Optional[Frame]:
        return self.provider.latest_frame()

    def observe_active_window(self) -> Dict[str, Any]:
        return dict(self.state.active_window or {})

    def observe_region(self, bounds) -> Optional[Frame]:
        return self.provider.latest_frame()

    def detect_changes(self) -> ScreenChange:
        return self.detector.observe(self.provider.latest_frame(), hwnd=int((self.state.active_window or {}).get("hwnd") or 0))

    def request_visual_understanding(
        self,
        question: str,
        region=None,
        require_localization: bool = False,
        *,
        reason: str = "user_screen_question",
    ) -> Dict[str, Any]:
        frame = self.provider.latest_frame()
        self._publish(VISION_STARTED, {"reason": reason})
        payload = self.vision.maybe_request(reason, question, frame=frame, region=region, force=True) or {}
        if payload.get("error"):
            self._publish(VISION_FAILED, {"error": payload.get("error")})
        else:
            self._publish(VISION_COMPLETED, {"confidence": 0})
        self.state.visual["scene_summary"] = payload.get("description") or ""
        self.state.visual["vision_timestamp"] = time.time()
        return payload

    def resolve_visual_referent(self, phrase: str, cursor_position=None, candidates=None) -> Dict[str, Any]:
        cursor_el = None
        rows = list(candidates or [])
        if cursor_position and rows:
            try:
                cx, cy = cursor_position
            except Exception:
                cx, cy = 0, 0
            for item in rows:
                bbox = list(getattr(item, "bbox", None) or [])
                if len(bbox) >= 4 and bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]:
                    cursor_el = item
                    break
        result = self.referents.resolve(phrase, candidates=rows, cursor_element=cursor_el)
        if result.get("ok") and result.get("referent") is not None:
            ref = result["referent"]
            self.state.visual["referent"] = ref.to_dict() if hasattr(ref, "to_dict") else dict(ref)
            self._publish(VISUAL_REFERENT_UPDATED, self.state.visual["referent"])
        return result

    def verify_visual_postcondition(self, expected: str) -> Dict[str, Any]:
        return {"ok": True, "expected": expected, "used_vision": False}

    def assert_frame_fresh(self, frame_id: int, hwnd: int = 0) -> bool:
        current = self.provider.latest_frame()
        if current is None:
            return False
        if int(current.frame_id) != int(frame_id):
            note_perception("stale_visual_action")
            return False
        if hwnd and int(current.hwnd or 0) and int(current.hwnd) != int(hwnd):
            note_perception("stale_visual_action")
            return False
        return True

    def fuse_ui(self, uia_elements, vision_candidates=None):
        fused = fuse_elements(uia_elements, vision_candidates)
        sensitive = False
        for el in list(uia_elements or []):
            if not isinstance(el, dict):
                continue
            role = str(el.get("role") or "").lower()
            if el.get("is_password") or role in ("password",) or "hasł" in role:
                sensitive = True
        self.state.sensitive_region = bool(sensitive)
        public_elements = []
        for el in fused:
            row = dict(el)
            if row.get("is_password") or str(row.get("role") or "").lower() == "password":
                row["value"] = ""
                row["text"] = ""
                row["sensitive"] = True
            public_elements.append(row)
        self.state.ui_snapshot = {
            "elements": public_elements,
            "generation": self.state.frame_id,
            "focused_element": dict((self.state.ui_snapshot or {}).get("focused_element") or {}),
        }
        return public_elements

    def health(self) -> Dict[str, Any]:
        return {
            "started": self._started,
            "mode": self.mode,
            "provider": self.provider.health(),
            "vision_available": bool(self.vision.provider.available),
            "store_frames": False,
            "disk_writes": self.disk_writes,
            "metrics": perception_metrics(),
        }

    def _publish(self, event_type: str, data: Dict[str, Any], throttle: bool = False) -> None:
        try:
            from core.event_bus import get_event_bus
            from core.events import Event, Priority

            bus = get_event_bus()
            bus.publish(
                Event(
                    type=event_type,
                    source="perception",
                    data=dict(data or {}),
                    priority=Priority.LOW if throttle else Priority.NORMAL,
                )
            )
        except Exception:
            pass


def get_screen_runtime() -> ScreenPerceptionRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = ScreenPerceptionRuntime()
    return _RUNTIME


def set_screen_runtime(runtime: Optional[ScreenPerceptionRuntime]) -> None:
    global _RUNTIME
    _RUNTIME = runtime


def reset_screen_runtime_for_tests(runtime: Optional[ScreenPerceptionRuntime] = None) -> ScreenPerceptionRuntime:
    created = runtime if runtime is not None else ScreenPerceptionRuntime(provider=MemoryFrameProvider())
    set_screen_runtime(created)
    return created
