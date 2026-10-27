# ==========================
# ELIAN — RC validation metrics collector (Phase 9.5)
# ==========================

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.metrics import get_metrics


ROUTABLE_SOURCES = frozenset({
    "fast_path",
    "action_loop",
    "agent",
    "clarify",
    "confirm",
    "chat",
    "skill",
    "command",
    "intent",
})

LEGACY_SOURCES = frozenset({"skill", "command", "intent"})

AGENT_SOURCES = frozenset({"agent", "clarify", "confirm"})


def _route_bucket(source: str) -> str:
    src = str(source or "")
    # Soft ActionLoop is a deterministic shortcut (same RC bucket as FastPath).
    if src in ("fast_path", "action_loop"):
        return "fast_path"
    if src in AGENT_SOURCES:
        return "agent"
    if src == "chat":
        return "brain"
    if src in LEGACY_SOURCES:
        return "legacy"
    return "other"


def _category_from_text(text: str, source: str) -> str:
    t = str(text or "").lower().strip()
    if not t:
        return source or "empty"
    if any(w in t for w in ("głośniej", "glośniej", "ciszej", "głośność")):
        return "volume"
    if any(w in t for w in ("spotify", "muzyk", "puść", "pusz")):
        return "media"
    if any(w in t for w in ("lamp", "świat", "swiat", "salon")):
        return "smart_home"
    if "przypomnij" in t or "przypomn" in t:
        return "reminder"
    if "zapamiętaj" in t or "zapamietaj" in t:
        return "memory"
    if "ekran" in t or "notatnik" in t:
        return "vision"
    if t.startswith("nie "):
        return "negation"
    if "co by" in t or "gdybym" in t:
        return "hypothetical"
    if "czekaj" in t or "moment" in t:
        return "wait"
    return source or "general"


def _percentiles(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"p50": None, "p95": None, "count": 0, "reliable_p95": False}
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    p50 = sorted_vals[n // 2]
    p95 = sorted_vals[min(n - 1, int(n * 0.95))]
    return {
        "p50": round(p50, 1),
        "p95": round(p95, 1),
        "count": n,
        "reliable_p95": n >= 5,
    }


def _text_fingerprint(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:12]


@dataclass
class ManualTestResult:
    test_id: str
    result: str
    note: str = ""
    timestamp: str = ""


@dataclass
class RCValidationCollector:
    store_transcripts: bool = False
    active: bool = False
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None

    interactions: int = 0
    routable_interactions: int = 0
    route_counts: Dict[str, int] = field(default_factory=lambda: {
        "fast_path": 0,
        "agent": 0,
        "brain": 0,
        "legacy": 0,
        "other": 0,
    })

    wake_count: int = 0
    wake_latencies_ms: List[float] = field(default_factory=list)
    wake_false_positive_manual: int = 0
    wake_missed_manual: int = 0

    stt_failures: int = 0
    tts_failures: int = 0
    stt_latencies_ms: List[float] = field(default_factory=list)
    agent_latencies_ms: List[float] = field(default_factory=list)
    tool_latencies_ms: List[float] = field(default_factory=list)
    tts_latencies_ms: List[float] = field(default_factory=list)
    fastpath_latencies_ms: List[float] = field(default_factory=list)
    e2e_latencies_ms: List[float] = field(default_factory=list)

    vision_failures: int = 0
    ha_calls: int = 0
    ha_failures: int = 0

    crashes: int = 0
    idle_wake_activations: int = 0

    interaction_traces: List[Dict[str, Any]] = field(default_factory=list)
    legacy_events: List[Dict[str, Any]] = field(default_factory=list)
    manual_results: List[ManualTestResult] = field(default_factory=list)
    issue_log: List[str] = field(default_factory=list)
    _interaction_seq: int = 0

    custom_elian_wake_installed: bool = False
    wake_model_path: str = ""
    wake_mode_used: str = ""
    acoustic_wake_status: str = ""

    def activate(self) -> None:
        self.active = True
        self.started_at = time.time()
        self._detect_wake_model()

    def deactivate(self) -> None:
        self.active = False
        self.ended_at = time.time()

    def _detect_wake_model(self) -> None:
        try:
            from Config import get_config_value
            from core.rc_validation.wake_policy import (
                ACOUSTIC_STATUS_MISSING,
                has_custom_elian_wake_model,
            )

            model_path = str(get_config_value("voice", "wake_model", default="") or "")
            self.wake_model_path = model_path
            self.custom_elian_wake_installed = has_custom_elian_wake_model(model_path)
            if not self.custom_elian_wake_installed:
                self.acoustic_wake_status = ACOUSTIC_STATUS_MISSING
        except Exception:
            self.custom_elian_wake_installed = False
            self.acoustic_wake_status = "MANUAL TEST REQUIRED / CUSTOM MODEL MISSING"

    def set_wake_policy_meta(self, meta: dict, *, mode: str = "") -> None:
        self.wake_model_path = str(meta.get("wake_model_path") or self.wake_model_path)
        self.custom_elian_wake_installed = bool(meta.get("custom_elian_onnx_installed"))
        self.wake_mode_used = mode or str(meta.get("wake_mode_label") or "")
        self.acoustic_wake_status = str(
            meta.get("acoustic_wake_status")
            or (
                "READY"
                if self.custom_elian_wake_installed
                else "MANUAL TEST REQUIRED / CUSTOM MODEL MISSING"
            )
        )

    def record_wake(
        self,
        *,
        detected: bool,
        latency_ms: Optional[float] = None,
        mode: str = "",
        score: float = 0.0,
    ) -> None:
        if not self.active:
            return
        if detected:
            self.wake_count += 1
            if latency_ms is not None:
                self.wake_latencies_ms.append(float(latency_ms))

    def record_wake_false_positive(self) -> None:
        if self.active:
            self.wake_false_positive_manual += 1

    def record_wake_missed(self) -> None:
        if self.active:
            self.wake_missed_manual += 1

    def record_stt_failure(self) -> None:
        if self.active:
            self.stt_failures += 1

    def record_tts_failure(self) -> None:
        if self.active:
            self.tts_failures += 1

    def record_crash(self, component: str, error: str) -> None:
        if self.active:
            self.crashes += 1
            self.issue_log.append(f"CRASH [{component}]: {error[:200]}")

    def add_issue(self, text: str) -> None:
        if text.strip():
            self.issue_log.append(text.strip())

    def record_manual_test(self, test_id: str, result: str, note: str = "") -> None:
        result = result.upper()
        if result not in ("PASS", "FAIL", "PARTIAL", "SKIP"):
            return
        self.manual_results.append(
            ManualTestResult(
                test_id=test_id,
                result=result,
                note=note,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            )
        )

    def record_legacy_event(
        self,
        component: str,
        *,
        reason: str = "",
        intent_hint: str = "",
    ) -> None:
        if not self.active:
            return
        self.legacy_events.append({
            "component": component,
            "reason": reason or "fallback_after_fastpath_agent_miss",
            "intent_hint": intent_hint,
            "ts": time.time(),
        })

    def record_interaction(
        self,
        text: str,
        result,
        *,
        latency: Optional[Dict[str, float]] = None,
        tool_names: Optional[List[str]] = None,
        planner_steps: int = 0,
        legacy_component: str = "",
    ) -> None:
        if not self.active:
            return

        self.interactions += 1
        source = str(getattr(result, "source", "") or "")
        bucket = _route_bucket(source)
        self.route_counts[bucket] = self.route_counts.get(bucket, 0) + 1
        if source in ROUTABLE_SOURCES:
            self.routable_interactions += 1

        lat = dict(latency or {})
        if lat.get("stt_ms") is not None:
            self.stt_latencies_ms.append(float(lat["stt_ms"]))
        if lat.get("planner_ms") is not None:
            self.agent_latencies_ms.append(float(lat["planner_ms"]))
        if lat.get("tool_ms") is not None:
            self.tool_latencies_ms.append(float(lat["tool_ms"]))
        if lat.get("tts_generation_ms") is not None:
            self.tts_latencies_ms.append(float(lat["tts_generation_ms"]))
        if lat.get("total_response_ms") is not None:
            self.e2e_latencies_ms.append(float(lat["total_response_ms"]))
        if bucket == "fast_path" and lat.get("total_response_ms") is not None:
            self.fastpath_latencies_ms.append(float(lat["total_response_ms"]))

        for tool in tool_names or []:
            name = str(tool).lower()
            if "turn_" in name or "device" in name or name.startswith("ha_"):
                self.ha_calls += 1

        try:
            from core.tools import get_registry

            last = get_registry().last_result
            if last and not last.success:
                tn = str(last.tool_name or "").lower()
                if "turn_" in tn or "device" in tn:
                    self.ha_failures += 1
                if "vision" in tn or "screen" in tn or "describe" in tn:
                    self.vision_failures += 1
        except Exception:
            pass

        success = source not in ("no_response",)
        self._interaction_seq += 1
        trace: Dict[str, Any] = {
            "interaction_id": self._interaction_seq,
            "route": source,
            "route_bucket": bucket,
            "category": _category_from_text(text, source),
            "tool_names": list(tool_names or []),
            "success": success,
            "planner_steps": planner_steps,
            "latency_ms": lat,
            "legacy_component": legacy_component or "",
        }
        if self.store_transcripts:
            trace["transcript"] = str(text or "")[:500]
        else:
            trace["text_fingerprint"] = _text_fingerprint(text)

        self.interaction_traces.append(trace)
        if len(self.interaction_traces) > 50:
            self.interaction_traces = self.interaction_traces[-50:]

        get_metrics().record_interaction_trace(trace, keep_last=50)

    def snapshot_metrics(self) -> Dict[str, Any]:
        m = get_metrics()
        snap = m.snapshot()
        legacy = snap.get("legacy") or {}
        routable = max(1, self.routable_interactions)
        legacy_count = self.route_counts.get("legacy", 0)
        legacy_rate = legacy_count / routable if self.routable_interactions else 0.0

        routing_pct = {
            k: round(100.0 * self.route_counts.get(k, 0) / routable, 1)
            for k in ("fast_path", "agent", "brain", "legacy")
        }

        duration = (self.ended_at or time.time()) - self.started_at
        tool_calls = int(snap.get("tool_calls") or 0)
        tool_failures = int(snap.get("tool_failures") or 0)

        return {
            "version": "2.0.0-rc1",
            "session_duration_s": round(duration, 1),
            "interactions": self.interactions,
            "routable_interactions": self.routable_interactions,
            "routing_counts": dict(self.route_counts),
            "routing_distribution_pct": routing_pct,
            "legacy_fallback_rate_pct": round(legacy_rate * 100, 2),
            "legacy_breakdown": legacy,
            "legacy_fallback_used": snap.get("legacy_fallback_used", 0),
            "fast_path_count": self.route_counts.get("fast_path", 0),
            "agent_count": self.route_counts.get("agent", 0),
            "brain_count": self.route_counts.get("brain", 0),
            "legacy_route_count": legacy_count,
            "agent_success": snap.get("agent_success", 0),
            "agent_failure": snap.get("agent_failure", 0),
            "clarifications": snap.get("clarifications", 0),
            "recoveries": snap.get("recovery_count", 0),
            "no_progress": snap.get("no_progress_stops", 0),
            "tool_success": max(0, tool_calls - tool_failures),
            "tool_failure": tool_failures,
            "tool_calls": tool_calls,
            "model_calls": snap.get("model_calls", {}),
            "wake_count": self.wake_count,
            "wake_false_positive_manual": self.wake_false_positive_manual,
            "wake_missed_manual": self.wake_missed_manual,
            "wake_latency": _percentiles(self.wake_latencies_ms),
            "stt_failures": self.stt_failures,
            "tts_failures": self.tts_failures,
            "latency": {
                "stt": _percentiles(self.stt_latencies_ms),
                "agent": _percentiles(self.agent_latencies_ms),
                "tool": _percentiles(self.tool_latencies_ms),
                "tts": _percentiles(self.tts_latencies_ms),
                "fast_path": _percentiles(self.fastpath_latencies_ms),
                "end_to_end": _percentiles(self.e2e_latencies_ms),
            },
            "vision_calls": snap.get("model_calls", {}).get("vision", 0),
            "vision_failures": self.vision_failures,
            "ha_calls": self.ha_calls,
            "ha_failures": self.ha_failures,
            "wake_model": {
                "path": self.wake_model_path,
                "custom_elian_onnx_installed": self.custom_elian_wake_installed,
                "wake_mode_used": self.wake_mode_used or (
                    "legacy_stt" if not self.custom_elian_wake_installed else "acoustic"
                ),
                "acoustic_wake_status": self.acoustic_wake_status
                or (
                    "READY"
                    if self.custom_elian_wake_installed
                    else "MANUAL TEST REQUIRED / CUSTOM MODEL MISSING"
                ),
                "warning": (
                    None
                    if self.custom_elian_wake_installed
                    else "CUSTOM ELIAN WAKE MODEL NOT INSTALLED"
                ),
            },
            "store_transcripts": self.store_transcripts,
            "crashes": self.crashes,
            "interaction_traces": list(self.interaction_traces),
            "legacy_events": list(self.legacy_events),
            "manual_results": [
                {"test_id": r.test_id, "result": r.result, "note": r.note, "ts": r.timestamp}
                for r in self.manual_results
            ],
            "issue_log": list(self.issue_log),
        }


_COLLECTOR: Optional[RCValidationCollector] = None


def get_rc_collector() -> Optional[RCValidationCollector]:
    return _COLLECTOR


def activate_collector(*, store_transcripts: bool = False) -> RCValidationCollector:
    global _COLLECTOR
    _COLLECTOR = RCValidationCollector(store_transcripts=store_transcripts)
    _COLLECTOR.activate()

    metrics = get_metrics()
    if not getattr(metrics, "_rc_legacy_hook_installed", False):
        original = metrics.record_legacy

        def wrapped(kind: str = "unknown"):
            original(kind)
            if _COLLECTOR and _COLLECTOR.active:
                _COLLECTOR.record_legacy_event(component=str(kind))

        metrics.record_legacy = wrapped  # type: ignore[method-assign]
        metrics._rc_legacy_hook_installed = True  # type: ignore[attr-defined]

    return _COLLECTOR


def deactivate_collector() -> Optional[RCValidationCollector]:
    global _COLLECTOR
    if _COLLECTOR:
        _COLLECTOR.deactivate()
    return _COLLECTOR


def record_interaction_if_active(text: str, result, *, latency=None) -> None:
    col = get_rc_collector()
    if not col or not col.active:
        return

    tool_names: List[str] = []
    planner_steps = 0
    legacy_component = ""

    data = getattr(result, "data", None) or {}
    if isinstance(data, dict):
        planner_steps = int(data.get("steps") or 0)

    try:
        from core.tools import get_registry

        history = get_registry().history[-3:]
        tool_names = [
            str(h.get("tool") or h.get("name") or "")
            for h in history
            if h
        ]
        tool_names = [t for t in tool_names if t]
    except Exception:
        pass

    source = str(getattr(result, "source", "") or "")
    if source in LEGACY_SOURCES:
        legacy_map = {"skill": "skills", "command": "commands", "intent": "intent_router"}
        legacy_component = legacy_map.get(source, source)

    col.record_interaction(
        text,
        result,
        latency=latency,
        tool_names=tool_names,
        planner_steps=planner_steps,
        legacy_component=legacy_component,
    )


def record_wake_if_active(**kwargs) -> None:
    col = get_rc_collector()
    if col and col.active:
        col.record_wake(**kwargs)
