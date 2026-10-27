# ==========================
# ELIAN — voice pipeline (Phase 4)
# ==========================

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from core.latency import LatencyTracker, new_turn, set_current_tracker
from voice.settings import VoiceSettings
from voice.state import SpeechState, SpeechStateMachine
from voice.wake import (
    FakeWakeListener,
    WakeListenerBase,
    WakeResult,
    create_wake_listener,
)


@dataclass
class PipelineTurn:
    text: str
    wake: bool = False
    bare_wake: bool = False
    latency: Optional[LatencyTracker] = None


_TOOL_SOURCES = frozenset(
    {
        "fast_path",
        "agent",
        "tool",
        "deterministic",
        "intent",
        "skills",
        "command",
    }
)


def _normalize_echo(text: str) -> str:
    raw = str(text or "").lower().strip()
    out = []
    for ch in raw:
        if ch.isalnum() or ch.isspace():
            out.append(ch)
    return " ".join("".join(out).split())


def looks_like_self_echo(transcript: str, last_tts: str) -> bool:
    """True when STT is our own TTS (or a near-copy), not a new user turn."""
    heard = _normalize_echo(transcript)
    spoken = _normalize_echo(last_tts)
    if not heard or not spoken:
        return False
    if len(heard) < 4:
        return heard == spoken
    if heard == spoken or heard in spoken or spoken in heard:
        return True
    ta, tb = set(heard.split()), set(spoken.split())
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / float(min(len(ta), len(tb)))
    return overlap >= 0.85 and abs(len(heard) - len(spoken)) <= 16


class VoicePipeline:
    """
    IDLE → acoustic wake → ACTIVE → VAD+STT → (caller handles CM) → TTS → ACTIVE
    ACTIVE → idle timeout → IDLE

    ConversationManager remains the conversation brain; this class only
    delivers audio/transcripts and manages TTS interruptibility.
    """

    def __init__(
        self,
        *,
        settings: Optional[VoiceSettings] = None,
        listen_fn: Optional[Callable[..., str]] = None,
        transcribe_fn: Optional[Callable[..., str]] = None,
        speak_fn: Optional[Callable[..., None]] = None,
        stop_speak_fn: Optional[Callable[[], None]] = None,
        is_speaking_fn: Optional[Callable[[], bool]] = None,
        wake_listener: Optional[WakeListenerBase] = None,
        remove_wake_fn: Optional[Callable[[str], str]] = None,
        is_wake_fn: Optional[Callable[[str], bool]] = None,
    ):
        self.settings = settings or VoiceSettings.from_config()
        self.state = SpeechStateMachine(SpeechState.IDLE)
        self._listen = listen_fn
        self._transcribe = transcribe_fn
        self._speak = speak_fn
        self._stop_speak = stop_speak_fn or (lambda: None)
        self._is_speaking = is_speaking_fn or (lambda: False)
        self._remove_wake = remove_wake_fn
        self._is_wake = is_wake_fn
        self._wake: Optional[WakeListenerBase] = wake_listener
        self._wake_mode = "unset"
        self._closed = False
        self._barge_stop = threading.Event()
        self.last_latency: Optional[LatencyTracker] = None
        self.cancellation_requested = False
        self.was_interrupted = False
        self.last_listen_echo = False
        self._last_tts_text = ""
        self._echo_guard_until = 0.0
        self._barge_stream_closed = threading.Event()
        self._barge_stream_closed.set()
        self._active_prose_stream = None

    def start(self) -> None:
        if self._listen is None:
            import Voice as voice_mod

            self._listen = voice_mod.listen
            self._transcribe = getattr(
                voice_mod, "transcribe_audio", None
            )
            self._is_speaking = getattr(
                voice_mod, "is_speaking", lambda: False
            )

        # RoutedTTS (Fish → Piper) unless caller injected speak/stop (tests).
        if self._speak is None:
            try:
                from voice.tts_engine import get_tts_engine

                engine = get_tts_engine()
                self._speak = engine.speak
                self._stop_speak = engine.stop
            except Exception:
                import Voice as voice_mod

                self._speak = voice_mod.speak
                self._stop_speak = getattr(
                    voice_mod, "stop_speaking", lambda: None
                )
        if self._is_speaking is None:
            import Voice as voice_mod

            self._is_speaking = getattr(
                voice_mod, "is_speaking", lambda: False
            )

        if self._remove_wake is None or self._is_wake is None:
            from WakeWord import is_wake_word, remove_wake_word

            self._remove_wake = remove_wake_word
            self._is_wake = is_wake_word

        if self._wake is None:
            self._wake, self._wake_mode = create_wake_listener(
                self.settings,
                state_machine=self.state,
                is_speaking=lambda: self.should_ignore_wake(),
                listen_fn=self._listen,
            )
        else:
            self._wake_mode = getattr(self._wake, "mode", "custom")
            if isinstance(self._wake, FakeWakeListener):
                self._wake_mode = "fake"

        self._wake.start()
        self.state.set(SpeechState.IDLE)
        print(f"[WAKE] mode={self._wake_mode}")

    def close(self) -> None:
        self._closed = True
        self._barge_stop.set()
        try:
            self._stop_speak()
        except Exception:
            pass
        if self._wake:
            self._wake.stop()
            self._wake.close()
        set_current_tracker(None)

    def await_wake(self, *, timeout: Optional[float] = None) -> WakeResult:
        self.state.set(SpeechState.WAKE_LISTENING)
        assert self._wake is not None
        # Do not start the turn timer here — idle listening is not wake latency.
        result = self._wake.wait_for_wake(timeout=timeout)
        if result.detected:
            tracker = new_turn()
            tracker.mark("wake_start")
            tracker.mark("wake_detected")
            tracker.record_ms("wake_detection_ms", float(result.latency_ms or 0.0))
            self.last_latency = tracker
            self.state.transition(
                SpeechState.ACTIVE_LISTENING, reason="wake_detected"
            )
            try:
                from core.hud_events import emit_confidence

                if result.score:
                    emit_confidence(float(result.score))
            except Exception:
                pass
        return result

    def transcript_from_wake(self, wake: WakeResult) -> str:
        """STT residual audio from wake+command utterance."""
        if wake.audio_after_wake is None or self._transcribe is None:
            # Prefer the full Whisper transcript so a second in-content "Elian"
            # is not stripped. last_remainder may already be truncated.
            full = getattr(self._wake, "last_transcript", None)
            remainder = getattr(self._wake, "last_remainder", None)
            text = full or remainder
            if text:
                from WakeWord import extract_wake, is_wake_word

                if is_wake_word(text):
                    return extract_wake(text).raw_command
                return str(text).strip()
            return ""

        tracker = self.last_latency or new_turn()
        tracker.mark("stt_start")
        text = self._transcribe(
            wake.audio_after_wake, llm_correct=False
        )
        tracker.mark("stt_end")
        if text and self._is_wake and self._is_wake(text):
            from WakeWord import extract_wake

            sliced = extract_wake(text).raw_command
            if sliced:
                return sliced
            if self._remove_wake:
                return str(self._remove_wake(text) or "").strip()
        return (text or "").strip()

    def listen_active(
        self, *, wait_timeout: Optional[float] = None
    ) -> str:
        self.last_listen_echo = False
        self.state.transition(
            SpeechState.ACTIVE_LISTENING, reason="listen_active"
        )
        tracker = new_turn()
        self.last_latency = tracker
        assert self._listen is not None
        text = self._listen(
            wait_timeout=wait_timeout, llm_correct=False
        )
        if "capture_end" not in tracker.marks:
            tracker.mark("capture_end")
        tracker.mark("stt_end")
        self.last_latency = tracker
        heard = (text or "").strip()
        if heard and not self.was_interrupted and self.is_self_echo(heard):
            self.last_listen_echo = True
            try:
                from core.metrics import get_metrics

                get_metrics().echo_suppressed = int(
                    getattr(get_metrics(), "echo_suppressed", 0) or 0
                ) + 1
            except Exception:
                pass
            print("[ECHO] own TTS ignored — session stays active")
            return ""
        return heard

    def _begin_speak_session(self, *, allow_barge: bool = True):
        """Shared SPEAKING transition + optional energy barge monitor (existing path)."""
        self.was_interrupted = False
        self.state.transition(SpeechState.SPEAKING, reason="tts_start")
        self.cancellation_requested = False
        self._barge_stop.clear()
        self._echo_guard_until = 0.0
        self._barge_stream_closed = threading.Event()
        self._barge_stream_closed.set()

        barge_thread = None
        if allow_barge and self.settings.barge_in_enabled:
            self._barge_stream_closed.clear()
            barge_thread = threading.Thread(
                target=self._barge_monitor,
                name="elian-barge",
                daemon=True,
            )
            barge_thread.start()
        return barge_thread

    def _end_speak_session(self, barge_thread) -> None:
        self._barge_stop.set()
        if barge_thread is not None:
            barge_thread.join(timeout=2.0)
            try:
                from voice.audio_gate import get_audio_gate

                get_audio_gate().wait_inputs_idle(timeout=2.0)
            except Exception:
                pass
            self._barge_stream_closed.wait(timeout=2.0)
        if self.was_interrupted:
            if self.state.state is SpeechState.INTERRUPTED:
                self.state.transition(
                    SpeechState.ACTIVE_LISTENING,
                    reason="resume_after_barge",
                )
            elif self.state.state is SpeechState.SPEAKING:
                self.state.transition(
                    SpeechState.INTERRUPTED, reason="barge_invariant"
                )
                self.state.transition(
                    SpeechState.ACTIVE_LISTENING,
                    reason="resume_after_barge",
                )
            self._echo_guard_until = 0.0
        elif self.state.state is SpeechState.SPEAKING:
            self.state.transition(
                SpeechState.ACTIVE_LISTENING, reason="tts_done"
            )
            self._arm_echo_guard()

    def speak(self, text: str, *, allow_barge: bool = True) -> None:
        if not text:
            return
        self._last_tts_text = str(text)
        if not self.settings.tts_enabled:
            return
        barge_thread = self._begin_speak_session(allow_barge=allow_barge)

        try:
            try:
                from core.crash_log import write_runtime_snapshot

                write_runtime_snapshot(
                    reason="before_tts",
                    pipeline=self,
                    barge_in=False,
                    persist=False,
                )
            except Exception:
                pass
            assert self._speak is not None
            self._speak(text)
        finally:
            self._end_speak_session(barge_thread)

    def speak_prose_stream(self, session, *, allow_barge: bool = True) -> None:
        """Stream Brain tokens → chunked Piper. Commits only spoken text."""
        if session is None:
            return
        # Fish s2.exe is full-utterance + exclusive GPU — drain then RoutedTTS.
        fish_files = False
        try:
            from voice.fish_s2_backend import files_ready

            fish_files, _ = files_ready()
        except Exception:
            fish_files = False
        if (
            not self.settings.tts_enabled
            or not getattr(self.settings, "stream_tts", True)
            or fish_files
        ):
            # Fallback: drain stream to full text, then blocking speak.
            parts = []
            try:
                for piece in session.iter_tokens():
                    parts.append(str(piece or ""))
            except Exception:
                pass
            full = "".join(parts).strip()
            session.commit_spoken(full)
            if full:
                self.speak(full, allow_barge=allow_barge)
            return

        self._active_prose_stream = session
        barge_thread = self._begin_speak_session(allow_barge=allow_barge)
        speak_result = None
        try:
            try:
                from core.crash_log import write_runtime_snapshot

                write_runtime_snapshot(
                    reason="before_tts_stream",
                    pipeline=self,
                    barge_in=False,
                    persist=False,
                )
            except Exception:
                pass

            import Voice as voice_mod

            speak_stream = getattr(voice_mod, "speak_stream", None)
            if speak_stream is None:
                parts = list(session.iter_tokens())
                full = "".join(str(p or "") for p in parts).strip()
                session.commit_spoken(full)
                if full and self._speak is not None:
                    self._last_tts_text = full
                    self._speak(full)
                return

            speak_result = speak_stream(
                session.iter_tokens(),
                clause_flush_chars=int(
                    getattr(self.settings, "tts_clause_flush_chars", 50) or 0
                ),
                cancel_check=lambda: bool(self.cancellation_requested),
            )
            spoken = str(getattr(speak_result, "spoken_text", "") or "").strip()
            interrupted = bool(getattr(speak_result, "interrupted", False))
            if interrupted:
                self.was_interrupted = True
            self._last_tts_text = spoken
            session.commit_spoken(spoken)
            if not spoken and not interrupted:
                session.abandon()
        except Exception:
            try:
                session.cancel_generation()
            except Exception:
                pass
            try:
                spoken = str(getattr(session, "spoken_text", "") or "").strip()
                if spoken:
                    session.commit_spoken(spoken)
                else:
                    session.abandon()
            except Exception:
                pass
            raise
        finally:
            self._active_prose_stream = None
            self._end_speak_session(barge_thread)

    def stop_speaking(self) -> None:
        """Cancel chain: playback → Piper → Ollama stream → drop unspoken."""
        self.cancellation_requested = True
        self.was_interrupted = True
        self._barge_stop.set()
        self._echo_guard_until = 0.0
        try:
            from core.crash_log import write_runtime_snapshot

            write_runtime_snapshot(
                reason="barge_or_stop",
                pipeline=self,
                barge_in=True,
            )
        except Exception:
            pass

        # 1) Stop / abort current playback
        try:
            import Voice as voice_mod

            if hasattr(voice_mod, "request_playback_interrupt"):
                voice_mod.request_playback_interrupt()
            if hasattr(voice_mod, "cancel_stream_tts"):
                voice_mod.cancel_stream_tts()
            elif hasattr(voice_mod, "cancel_piper_synthesis"):
                voice_mod.cancel_piper_synthesis()
        except Exception:
            pass
        try:
            if self._stop_speak is not None:
                try:
                    self._stop_speak(force_portaudio_stop=False)
                except TypeError:
                    self._stop_speak()
        except Exception as exc:
            print(f"[BARGE] stop_speaking error (ignored): {exc}")

        # 2–3) Cancel pending Piper (via cancel_stream_tts) + active Ollama stream
        session = self._active_prose_stream
        if session is not None:
            try:
                session.cancel_generation()
            except Exception:
                pass
        else:
            try:
                from core.model_client import get_model_client

                client = get_model_client()
                cancel = getattr(client, "cancel_active_stream", None)
                if callable(cancel):
                    cancel()
            except Exception:
                pass

        if self.state.is_speaking() or self.state.state is SpeechState.ACTING:
            self.state.transition(SpeechState.INTERRUPTED, reason="barge_in")

    def clear_cancellation(self) -> None:
        self.cancellation_requested = False

    def _barge_monitor(self) -> None:
        """Half-duplex barge-in: energy spike while speaking → stop TTS.

        Critical: never call sd.stop() while this InputStream is open.
        Only signal playback interrupt; TTS owner thread performs PortAudio stop.

        Prefer callback InputStream on the primary STT mic (WDM-KS supports
        callbacks; blocking read fails with PaError -9999). Fall back to a
        Blocking-capable MME/DirectSound sibling via resolve_barge_input_device.
        """
        try:
            import sounddevice as sd
            from queue import Empty, Queue
            from voice.audio_gate import get_audio_gate
        except Exception:
            self._barge_stream_closed.set()
            return

        rate = self.settings.sample_rate
        block = self.settings.block_size
        threshold = self.settings.barge_in_energy
        grace = max(0.0, float(getattr(self.settings, "barge_in_grace_ms", 250) or 0) / 1000.0)
        time.sleep(grace)
        gate = get_audio_gate()
        primary = self.settings.input_device
        fallback = primary
        try:
            import Voice as voice_mod

            fallback = voice_mod.resolve_barge_input_device(
                preferred_index=self.settings.input_device,
                name=str(getattr(self.settings, "input_device_name", "") or ""),
            )
        except Exception:
            fallback = self.settings.input_device

        audio_q: Queue = Queue(maxsize=16)

        def _on_audio(indata, frames, time_info, status):  # noqa: ARG001
            try:
                audio_q.put_nowait(np.asarray(indata, dtype=np.float32).copy())
            except Exception:
                pass

        def _fire_barge() -> None:
            print("[BARGE] user interruption — stop TTS")
            try:
                from core.crash_log import write_runtime_snapshot

                write_runtime_snapshot(
                    reason="before_barge_signal",
                    pipeline=self,
                    barge_in=True,
                    gpu_op="",
                )
            except Exception:
                pass
            self.cancellation_requested = True
            self.was_interrupted = True
            try:
                import Voice as voice_mod

                if hasattr(voice_mod, "cancel_stream_tts"):
                    voice_mod.cancel_stream_tts()
                else:
                    voice_mod.request_playback_interrupt()
            except Exception:
                pass
            session = self._active_prose_stream
            if session is not None:
                try:
                    session.cancel_generation()
                except Exception:
                    pass
            if self.state.is_speaking() or self.state.state is SpeechState.ACTING:
                self.state.transition(SpeechState.INTERRUPTED, reason="barge_in")

        def _energy_loop(*, blocking_stream=None) -> None:
            while not self._barge_stop.is_set() and self.state.is_speaking():
                if blocking_stream is not None:
                    audio, _ = blocking_stream.read(block)
                else:
                    try:
                        audio = audio_q.get(timeout=0.05)
                    except Empty:
                        continue
                vol = float(np.sqrt(np.mean(np.square(audio))))
                if vol >= threshold:
                    _fire_barge()
                    break

        last_open: BaseException = RuntimeError("barge InputStream open failed")
        try:
            with gate.input_stream_slot():
                opened = False
                # Callback on primary STT device first (WDM-KS-safe).
                for device, mode in (
                    (primary, "callback"),
                    (fallback, "callback"),
                    (fallback, "blocking"),
                ):
                    try:
                        if mode == "callback":
                            kwargs = {
                                "samplerate": rate,
                                "channels": 1,
                                "dtype": "float32",
                                "blocksize": block,
                                "callback": _on_audio,
                            }
                            if device is not None:
                                kwargs["device"] = device
                            with sd.InputStream(**kwargs):
                                if device is not None and device != primary:
                                    print(f"[BARGE] callback monitor on device #{device}")
                                opened = True
                                _energy_loop(blocking_stream=None)
                            break
                        kwargs = {
                            "samplerate": rate,
                            "channels": 1,
                            "dtype": "float32",
                            "blocksize": block,
                        }
                        if device is not None:
                            kwargs["device"] = device
                        with sd.InputStream(**kwargs) as stream:
                            print(f"[BARGE] blocking monitor on device #{device}")
                            opened = True
                            _energy_loop(blocking_stream=stream)
                        break
                    except Exception as open_exc:
                        last_open = open_exc
                        continue
                if not opened:
                    raise last_open
        except Exception as exc:
            # Rate-limit RUNTIME spam: same host error every TTS is noise.
            now = time.monotonic()
            last = float(getattr(self, "_barge_fail_log_at", 0.0) or 0.0)
            if now - last >= 30.0:
                self._barge_fail_log_at = now
                print(f"[BARGE] monitor unavailable (degraded): {exc}")
                try:
                    from core.crash_log import classify_and_recover

                    classify_and_recover(
                        exc,
                        subsystem="voice",
                        classification="BARGE_MONITOR_FAILURE",
                    )
                except Exception:
                    pass
            else:
                print("[BARGE] monitor still unavailable — TTS continues without barge")
        finally:
            self._barge_stream_closed.set()

    def request_cancel(self) -> None:
        """Signal planner/tools that user said anuluj (ConversationManager owns semantics)."""
        self.cancellation_requested = True

    def go_idle(self) -> None:
        self.state.transition(SpeechState.IDLE, reason="session_end")
        if self._wake:
            self._wake.start()

    def begin_processing(self) -> None:
        self.state.transition(SpeechState.PROCESSING, reason="handle")

    def begin_acting(self) -> None:
        self.state.transition(SpeechState.ACTING, reason="tools")

    def pause_session(self) -> None:
        self.state.transition(SpeechState.PAUSED, reason="wait")

    def resume_listening(self, *, reason: str = "resume") -> None:
        if self.state.state is SpeechState.IDLE:
            return
        self.state.transition(SpeechState.ACTIVE_LISTENING, reason=reason)

    def _arm_echo_guard(self) -> None:
        guard_ms = float(getattr(self.settings, "echo_guard_ms", 600) or 0)
        cooldown = float(getattr(self.settings, "tts_cooldown", 0) or 0)
        hold = max(guard_ms / 1000.0, cooldown)
        self._echo_guard_until = time.monotonic() + hold

    def echo_guard_active(self) -> bool:
        if self.was_interrupted:
            return False
        return time.monotonic() < self._echo_guard_until

    def should_ignore_wake(self) -> bool:
        """Own TTS must not re-trigger wake-word or STT."""
        if self.state.is_speaking() or bool(self._is_speaking()):
            return True
        if self.state.state is SpeechState.ACTING:
            return True
        return self.echo_guard_active()

    def is_self_echo(self, transcript: str) -> bool:
        if not looks_like_self_echo(transcript, self._last_tts_text):
            return False
        heard = _normalize_echo(transcript)
        spoken = _normalize_echo(self._last_tts_text)
        if heard and heard == spoken:
            return True
        return self.echo_guard_active()

    def execute_turn(self, manager, text: str):
        """
        PROCESSING → (ACTING) → SPEAKING → ACTIVE_LISTENING.
        Always leaves PROCESSING. ConversationManager stays the only brain.
        """
        self.begin_processing()
        self.was_interrupted = False
        self.clear_cancellation()
        if hasattr(manager, "cancel_requested"):
            manager.cancel_requested = False
        result = None
        try:
            result = manager.handle(text)
            if getattr(result, "source", "") in _TOOL_SOURCES:
                self.begin_acting()
            prose_stream = getattr(result, "prose_stream", None)
            if prose_stream is not None:
                self.speak_prose_stream(prose_stream)
                # Surface spoken text on the result for diagnostics / inject.
                spoken = str(getattr(prose_stream, "spoken_text", "") or "")
                if spoken:
                    result.text = spoken
                    result.should_speak = True
            elif getattr(result, "should_speak", False) and getattr(result, "text", ""):
                self.speak(result.text)
            if getattr(result, "ended", False) or not getattr(result, "active", True):
                self.go_idle()
            elif getattr(result, "paused", False) or getattr(result, "source", "") == "wait":
                self.pause_session()
                self.resume_listening(reason="wait_resume")
            else:
                self.resume_listening(reason="turn_done")
            if self.was_interrupted and result is not None:
                result.interrupted = True
            return result
        except Exception:
            self.resume_listening(reason="processing_error")
            try:
                from core.metrics import get_metrics

                get_metrics().processing_recovered = int(
                    getattr(get_metrics(), "processing_recovered", 0) or 0
                ) + 1
            except Exception:
                pass
            raise
        finally:
            if self.state.is_processing():
                self.resume_listening(reason="processing_deadlock_guard")
                try:
                    from core.metrics import get_metrics

                    get_metrics().processing_recovered = int(
                        getattr(get_metrics(), "processing_recovered", 0) or 0
                    ) + 1
                except Exception:
                    pass
