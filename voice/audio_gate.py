# ==========================
# ELIAN — PortAudio serialization (Phase 14)
# ==========================
"""Single process-wide gate for sounddevice streams.

Barge-in must never call sd.stop() while an InputStream is open on the
same barge thread, and listen must not open InputStream until barge closes.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Optional


@dataclass
class AudioGateSnapshot:
    active_input_streams: int = 0
    active_tts_playback: int = 0
    barge_signal_pending: bool = False
    last_op: str = ""
    last_op_ts: float = 0.0
    last_thread: str = ""


class AudioGate:
    """Serialize PortAudio open/close/play/stop and track lifecycle invariants."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._input_streams = 0
        self._tts_playback = 0
        self._barge_signal = threading.Event()
        self._last_op = ""
        self._last_op_ts = 0.0
        self._last_thread = ""
        # Tests / diagnostics may observe violations without aborting.
        self.violations: list[str] = []

    def snapshot(self) -> AudioGateSnapshot:
        with self._lock:
            return AudioGateSnapshot(
                active_input_streams=self._input_streams,
                active_tts_playback=self._tts_playback,
                barge_signal_pending=self._barge_signal.is_set(),
                last_op=self._last_op,
                last_op_ts=self._last_op_ts,
                last_thread=self._last_thread,
            )

    def _note(self, op: str) -> None:
        self._last_op = op
        self._last_op_ts = time.time()
        self._last_thread = threading.current_thread().name

    def signal_playback_interrupt(self) -> None:
        """Barge thread: request TTS stop only — never touch PortAudio here."""
        self._barge_signal.set()
        with self._lock:
            self._note("barge_signal")

    def clear_playback_interrupt(self) -> None:
        self._barge_signal.clear()

    def playback_interrupt_requested(self) -> bool:
        return self._barge_signal.is_set()

    def begin_tts(self) -> None:
        with self._lock:
            if self._tts_playback >= 1:
                self.violations.append("duplicate_tts_playback")
            self._tts_playback += 1
            self._note("tts_begin")

    def end_tts(self) -> None:
        idle = False
        with self._lock:
            self._tts_playback = max(0, self._tts_playback - 1)
            self._note("tts_end")
            idle = self._tts_playback <= 0
        # Fish persistent worker: release GPU shortly after TTS playback ends.
        if idle:
            try:
                from voice.fish_s2_backend import schedule_fish_session_end

                schedule_fish_session_end()
            except Exception:
                pass

    def wait_inputs_idle(self, *, timeout: float = 2.0) -> bool:
        """Block until no InputStream is open (or timeout)."""
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            with self._lock:
                if self._input_streams <= 0:
                    return True
            time.sleep(0.02)
        with self._lock:
            if self._input_streams > 0:
                self.violations.append("input_stream_linger")
            return self._input_streams <= 0

    @contextmanager
    def input_stream_slot(self, *, wait_for_tts: bool = False) -> Iterator[None]:
        """Acquire exclusive input-stream accounting around sd.InputStream.

        At most one InputStream at a time (listen vs barge). Barge may overlap
        TTS playback (wait_for_tts=False); listen waits for TTS first.
        """
        deadline = time.monotonic() + 2.0
        acquired = False
        while time.monotonic() < deadline:
            if wait_for_tts:
                with self._lock:
                    tts_busy = self._tts_playback > 0
                if tts_busy:
                    self._note("input_wait_tts")
                    time.sleep(0.02)
                    continue
            with self._lock:
                if self._input_streams >= 1:
                    self._note("input_wait")
                else:
                    self._input_streams += 1
                    self._note("input_open")
                    acquired = True
                    break
            time.sleep(0.02)
        if not acquired:
            # Same-thread nested acquire or a stuck peer — record and force so
            # callers do not hang forever (tests + degraded recovery).
            with self._lock:
                if self._input_streams >= 1:
                    self.violations.append("duplicate_input_stream")
                self._input_streams += 1
                self._note("input_open_forced")
        try:
            yield
        finally:
            with self._lock:
                self._input_streams = max(0, self._input_streams - 1)
                self._note("input_close")

    def wait_tts_idle(self, *, timeout: float = 1.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            with self._lock:
                if self._tts_playback <= 0:
                    return True
            time.sleep(0.02)
        with self._lock:
            return self._tts_playback <= 0

    def with_portaudio(self, op: str = "portaudio"):
        """Context manager for serialized sd.play / sd.stop / stream open."""
        return _PortAudioSection(self, op)

    def invariants_ok(self) -> bool:
        snap = self.snapshot()
        if snap.active_input_streams > 1:
            return False
        if snap.active_tts_playback > 1:
            return False
        return not self.violations


class _PortAudioSection:
    def __init__(self, gate: AudioGate, op: str) -> None:
        self._gate = gate
        self._op = op

    def __enter__(self) -> AudioGate:
        self._gate._lock.acquire()
        self._gate._note(self._op)
        return self._gate

    def __exit__(self, exc_type, exc, tb) -> None:
        self._gate._lock.release()


_GATE: Optional[AudioGate] = None
_GATE_LOCK = threading.Lock()


def get_audio_gate() -> AudioGate:
    global _GATE
    with _GATE_LOCK:
        if _GATE is None:
            _GATE = AudioGate()
        return _GATE


def reset_audio_gate_for_tests() -> AudioGate:
    global _GATE
    with _GATE_LOCK:
        _GATE = AudioGate()
        return _GATE
