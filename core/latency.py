# ==========================
# ELIAN — latency metrics (Phase 4 / Faza 2.2 honesty)
# ==========================

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Optional


SANITY_LIMITS_MS = {
    "wake_detection_ms": 10_000.0,
    "speech_capture_ms": 120_000.0,
    "stt_ms": 120_000.0,
    "understanding_ms": 60_000.0,
    "cognitive_ms": 30_000.0,
    "decision_ms": 60_000.0,
    "tts_generation_ms": 120_000.0,
    "tts_playback_start_ms": 180_000.0,
    "total_response_ms": 300_000.0,
    "routing_ms": 60_000.0,
    "planner_ms": 180_000.0,
    "tool_ms": 120_000.0,
    "llm_ms": 180_000.0,
    "gpu_lease_wait_ms": 60_000.0,
    "fish_worker_startup_ms": 120_000.0,
    "fish_synthesis_ms": 120_000.0,
    "tts_playback_ms": 120_000.0,
}


def sanity_reason(metric: str, value_ms: float) -> Optional[str]:
    if value_ms < 0:
        return "negative_timer"
    limit = SANITY_LIMITS_MS.get(metric)
    if limit is not None and value_ms > limit:
        return "idle_or_stale_timer"
    return None


@dataclass
class LatencyTracker:
    """Collects per-turn voice/agent timing. Never spoken aloud."""

    marks: Dict[str, float] = field(default_factory=dict)
    started_at: float = field(default_factory=time.perf_counter)
    invalid: Dict[str, str] = field(default_factory=dict)

    def reset(self) -> None:
        self.marks.clear()
        self.invalid.clear()
        self.started_at = time.perf_counter()

    def mark(self, name: str) -> None:
        self.marks[name] = time.perf_counter()

    def duration_ms(self, start: str, end: str) -> Optional[float]:
        if start not in self.marks or end not in self.marks:
            return None
        return (self.marks[end] - self.marks[start]) * 1000.0

    def since_start_ms(self, end: str) -> Optional[float]:
        if end not in self.marks:
            return None
        return (self.marks[end] - self.started_at) * 1000.0

    @contextmanager
    def measure(self, name: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.marks[f"{name}_ms"] = (time.perf_counter() - t0) * 1000.0

    def record_ms(self, name: str, milliseconds: float) -> None:
        """Record an explicit duration (or accumulate under the same key)."""
        key = name if str(name).endswith("_ms") else f"{name}_ms"
        prev = self.marks.get(key)
        value = float(milliseconds)
        if isinstance(prev, (int, float)) and key in (
            "gpu_lease_wait_ms",
            "fish_worker_startup_ms",
            "fish_synthesis_ms",
            "tool_ms",
            "llm_ms",
        ):
            # Multiple tools / LLM steps in one turn — accumulate.
            self.marks[key] = float(prev) + value
        else:
            self.marks[key] = value

    def snapshot(self) -> Dict[str, float]:
        """Named metrics for logging (only present keys). Prefer explicit recorded *_ms."""
        out: Dict[str, float] = {}
        recorded = {
            key: float(value)
            for key, value in self.marks.items()
            if key.endswith("_ms")
        }
        pairs = (
            ("wake_detection_ms", "wake_start", "wake_detected"),
            ("speech_capture_ms", "capture_start", "capture_end"),
            ("stt_ms", "stt_start", "stt_end"),
            ("understanding_ms", "understanding_start", "understanding_end"),
            ("cognitive_ms", "cognitive_start", "cognitive_end"),
            ("decision_ms", "decision_start", "decision_end"),
            ("routing_ms", "route_start", "route_end"),
            ("planner_ms", "planner_start", "planner_end"),
            ("tool_ms", "tool_start", "tool_end"),
            ("llm_ms", "llm_start", "llm_end"),
            ("tts_queue_ms", "tts_queue_start", "tts_gen_start"),
            ("tts_generation_ms", "tts_gen_start", "tts_gen_end"),
            ("tts_playback_start_ms", "turn_start", "tts_first_audio"),
            ("tts_playback_ms", "tts_playback_start", "tts_playback_end"),
            ("total_response_ms", "turn_start", "tts_first_audio"),
        )
        for key, a, b in pairs:
            if key in recorded:
                out[key] = round(recorded[key], 1)
                continue
            ms = self.duration_ms(a, b)
            if ms is not None:
                out[key] = round(ms, 1)
        # Prefer Fish wall breakdown when present: generation = lease + cold + synth.
        fish_parts = []
        for part in (
            "gpu_lease_wait_ms",
            "fish_worker_startup_ms",
            "fish_synthesis_ms",
        ):
            if part in recorded:
                fish_parts.append(recorded[part])
                out[part] = round(recorded[part], 1)
        if fish_parts and "tts_generation_ms" not in recorded:
            # Prefer Fish wall (lease + cold + synth) over thin wrapper marks.
            wrapper = out.get("tts_generation_ms")
            fish_total = sum(fish_parts)
            if wrapper is None or wrapper < 5.0 or fish_total > float(wrapper):
                out["tts_generation_ms"] = round(fish_total, 1)
        elif "fish_synthesis_ms" in recorded and "tts_generation_ms" not in out:
            out["tts_generation_ms"] = round(recorded["fish_synthesis_ms"], 1)
        for key, value in recorded.items():
            if key not in out:
                out[key] = round(float(value), 1)
        # time-to-first-audio from gen start when available
        if "tts_playback_start_ms" not in out:
            ms = self.duration_ms("tts_gen_start", "tts_first_audio")
            if ms is not None:
                out["tts_playback_start_ms"] = round(ms, 1)
        self.invalid = {}
        for key, value in list(out.items()):
            reason = sanity_reason(key, value)
            if reason:
                self.invalid[key] = reason
        return out

    def log(self, prefix: str = "[LATENCY]") -> None:
        snap = self.snapshot()
        if not snap:
            return
        labels = {
            "wake_detection_ms": "wake",
            "speech_capture_ms": "capture",
            "stt_ms": "stt",
            "understanding_ms": "understanding",
            "cognitive_ms": "cognitive",
            "decision_ms": "decision",
            "routing_ms": "route",
            "planner_ms": "planner",
            "tool_ms": "tool",
            "llm_ms": "llm",
            "gpu_lease_wait_ms": "gpu_lease_wait",
            "fish_worker_startup_ms": "fish_worker_startup",
            "fish_synthesis_ms": "fish_synthesis",
            "tts_queue_ms": "tts_queue",
            "tts_generation_ms": "tts_generate",
            "tts_playback_start_ms": "time_to_first_audio",
            "tts_playback_ms": "tts_playback",
            "total_response_ms": "total",
            "screen_capture_ms": "screen_capture",
            "uia_ms": "uia",
            "ocr_ms": "ocr",
            "vision_model_ms": "vision_model",
            "element_match_ms": "element_match",
            "ui_action_ms": "ui_action",
            "screen_verify_ms": "screen_verify",
        }
        for key, reason in self.invalid.items():
            print(
                f"[LATENCY WARN] metric={key} value_ms={snap.get(key, 0):.0f} reason={reason}"
            )
        print(prefix)
        for key, label in labels.items():
            if key not in snap:
                continue
            if key in self.invalid:
                print(f"  {label}: invalid")
            else:
                print(f"  {label}: {snap[key]:.0f} ms")
        for key, value in snap.items():
            if key in labels:
                continue
            if key in self.invalid:
                print(f"  {key}: invalid")
            else:
                print(f"  {key}: {value:.0f} ms")


# Thread-local style shared tracker for the current turn (optional).
_current: Optional[LatencyTracker] = None


def current_tracker() -> Optional[LatencyTracker]:
    return _current


def set_current_tracker(tracker: Optional[LatencyTracker]) -> None:
    global _current
    _current = tracker


def new_turn() -> LatencyTracker:
    tracker = LatencyTracker()
    tracker.mark("turn_start")
    set_current_tracker(tracker)
    return tracker


def record_current_ms(name: str, milliseconds: float) -> None:
    tracker = current_tracker()
    if tracker is not None:
        tracker.record_ms(name, milliseconds)
