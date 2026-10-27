# ==========================
# ELIAN — acoustic wake (Phase 4)
# ==========================

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, List, Optional, Tuple

import numpy as np

from voice.settings import VoiceSettings
from voice.state import SpeechState, SpeechStateMachine


@dataclass
class WakeResult:
    detected: bool
    score: float = 0.0
    model_name: str = ""
    mode: str = "acoustic"  # acoustic | legacy_stt | fake
    # Optional PCM float32 @ 16k after wake (pre-roll + post-wake speech)
    audio_after_wake: Optional[np.ndarray] = None
    latency_ms: float = 0.0


class WakeListenerBase:
    """Public wake abstraction."""

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def wait_for_wake(self, *, timeout: Optional[float] = None) -> WakeResult:
        raise NotImplementedError

    def close(self) -> None:
        self.stop()


class FakeWakeListener(WakeListenerBase):
    """Offline / tests: inject wake events without microphone."""

    def __init__(self):
        self._event = threading.Event()
        self._result = WakeResult(detected=False)
        self._lock = threading.Lock()
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False
        self._event.set()

    def trigger(
        self,
        *,
        score: float = 0.9,
        audio_after_wake: Optional[np.ndarray] = None,
        latency_ms: float = 40.0,
    ) -> None:
        with self._lock:
            self._result = WakeResult(
                detected=True,
                score=score,
                model_name="fake",
                mode="fake",
                audio_after_wake=audio_after_wake,
                latency_ms=latency_ms,
            )
        self._event.set()

    def wait_for_wake(self, *, timeout: Optional[float] = None) -> WakeResult:
        ok = self._event.wait(timeout=timeout)
        if not ok:
            return WakeResult(detected=False, mode="fake")
        with self._lock:
            result = self._result
            self._event.clear()
            self._result = WakeResult(detected=False, mode="fake")
        if not self.started and not result.detected:
            return WakeResult(detected=False, mode="fake")
        return result


def has_custom_elian_wake_model(wake_model_path: str = "") -> bool:
    """True only when a custom elian*.onnx file exists on disk."""
    path = str(wake_model_path or "").strip()
    if not path:
        return False
    lower = path.lower().replace("\\", "/")
    name = os.path.basename(lower)
    if not name.endswith(".onnx"):
        return False
    if "elian" not in name and "elian" not in lower:
        return False
    return os.path.isfile(path)


class AcousticWakeListener(WakeListenerBase):
    """
    OpenWakeWord-based acoustic wake.

    Custom model path (elian.onnx) via voice.wake_model is required for
    production detection of "Elian". OpenWakeWord stock models do not hear
    that name — create_wake_listener falls back to legacy STT instead.
    """

    def __init__(
        self,
        settings: VoiceSettings,
        *,
        state_machine: Optional[SpeechStateMachine] = None,
        is_speaking: Optional[Callable[[], bool]] = None,
        on_score: Optional[Callable[[str, float], None]] = None,
        sounddevice=None,
    ):
        self.settings = settings
        self.state_machine = state_machine or SpeechStateMachine()
        self.is_speaking = is_speaking or (lambda: False)
        self.on_score = on_score
        self._sd = sounddevice
        self._model = None
        self._mode = "acoustic"
        self._last_wake_at = 0.0
        self._stop = threading.Event()
        self._started = False
        self._last_log_bucket = None
        self._pre_roll: Deque[np.ndarray] = deque()
        self._pre_roll_samples = int(
            settings.sample_rate * (settings.pre_roll_ms / 1000.0)
        )
        self.available = False
        self.unavailable_reason = ""

    def _ensure_sd(self):
        if self._sd is None:
            import sounddevice as sd

            self._sd = sd
        return self._sd

    def load(self) -> bool:
        try:
            from openwakeword.model import Model
        except Exception as exc:
            self.unavailable_reason = f"openwakeword import failed: {exc}"
            self.available = False
            return False

        try:
            kwargs = {"inference_framework": "onnx"}
            if self.settings.wake_model and os.path.isfile(
                self.settings.wake_model
            ):
                kwargs["wakeword_models"] = [self.settings.wake_model]
                print(f"[WAKE] Custom model: {self.settings.wake_model}")
            else:
                # Diagnostics / explicit AcousticWakeListener only — production
                # create_wake_listener must not rely on stock OWW for "Elian".
                print(
                    "[WAKE] No custom elian.onnx — OpenWakeWord stock models "
                    "(not tuned for Elian; prefer legacy STT in production)."
                )
            self._model = Model(**kwargs)
            self.available = True
            self.unavailable_reason = ""
            print("[WAKE] Acoustic OpenWakeWord READY")
            return True
        except Exception as exc:
            self.unavailable_reason = str(exc)
            self.available = False
            self._model = None
            print(f"[WAKE] Acoustic unavailable: {exc}")
            return False

    def start(self) -> None:
        if not self.available and self._model is None:
            self.load()
        self._stop.clear()
        self._started = True
        self.state_machine.set(SpeechState.WAKE_LISTENING)

    def stop(self) -> None:
        self._stop.set()
        self._started = False

    def _log_score(self, name: str, score: float, *, detected: bool) -> None:
        # Rate-limit: log only when bucket changes or on detect
        bucket = int(score * 10)
        key = (name, bucket, detected)
        if not detected and key == self._last_log_bucket:
            return
        self._last_log_bucket = key
        if detected:
            print(f"[WAKE] score={score:.2f} detected ({name})")
        elif score >= self.settings.wake_threshold * 0.6:
            print(f"[WAKE] score={score:.2f} ignored ({name})")
        if self.on_score:
            self.on_score(name, score)

    def wait_for_wake(self, *, timeout: Optional[float] = None) -> WakeResult:
        if not self.available or self._model is None:
            return WakeResult(
                detected=False,
                mode="acoustic",
                score=0.0,
            )

        sd = self._ensure_sd()
        chunk = self.settings.wake_chunk
        rate = self.settings.sample_rate
        t0 = time.perf_counter()
        deadline = None if timeout is None else (time.time() + timeout)
        self.state_machine.set(SpeechState.WAKE_LISTENING)
        self._pre_roll.clear()
        samples_kept = 0

        device = self.settings.input_device
        stream_kwargs = {
            "samplerate": rate,
            "channels": 1,
            "dtype": "int16",
            "blocksize": chunk,
        }
        if device is not None:
            stream_kwargs["device"] = device

        try:
            from voice.audio_gate import get_audio_gate

            gate = get_audio_gate()
            # Share PortAudio serialization with listen/barge (avoids -9985).
            with gate.input_stream_slot(wait_for_tts=True):
                with sd.InputStream(**stream_kwargs) as stream:
                    while not self._stop.is_set():
                        if deadline is not None and time.time() >= deadline:
                            return WakeResult(detected=False, mode="acoustic")

                        audio, _ = stream.read(chunk)
                        audio = np.squeeze(audio).astype(np.int16)
                        chunk_t0 = time.perf_counter()

                        # Pre-roll (float for later STT)
                        float_chunk = (audio.astype(np.float32) / 32768.0).reshape(
                            -1, 1
                        )
                        self._pre_roll.append(float_chunk)
                        samples_kept += len(float_chunk)
                        while samples_kept > self._pre_roll_samples and self._pre_roll:
                            dropped = self._pre_roll.popleft()
                            samples_kept -= len(dropped)

                        # Self-echo: ignore wake while we are speaking
                        if self.is_speaking():
                            continue

                        prediction = self._model.predict(audio)
                        best_name = ""
                        best_score = 0.0
                        for word, score in prediction.items():
                            score_f = float(score)
                            if score_f > best_score:
                                best_score = score_f
                                best_name = str(word)

                        if best_score < self.settings.wake_threshold:
                            if best_score > 0.15:
                                self._log_score(
                                    best_name, best_score, detected=False
                                )
                            continue

                        now = time.time()
                        if now - self._last_wake_at < self.settings.wake_cooldown:
                            self._log_score(
                                best_name, best_score, detected=False
                            )
                            print("[WAKE] cooldown — ignored")
                            continue

                        self._log_score(best_name, best_score, detected=True)
                        self._last_wake_at = now
                        latency_ms = (time.perf_counter() - chunk_t0) * 1000.0

                        # Collect short post-wake speech for wake+command
                        post = self._capture_post_wake(stream, float_chunk)
                        pre = (
                            np.concatenate(list(self._pre_roll), axis=0)
                            if self._pre_roll
                            else np.zeros((0, 1), dtype=np.float32)
                        )
                        combined = (
                            np.concatenate([pre, post], axis=0)
                            if post is not None and len(post)
                            else pre
                        )

                        return WakeResult(
                            detected=True,
                            score=best_score,
                            model_name=best_name,
                            mode="acoustic",
                            audio_after_wake=combined.flatten()
                            if combined is not None and len(combined)
                            else None,
                            latency_ms=latency_ms,
                        )
        except Exception as exc:
            print(f"[WAKE] Mic/stream error: {exc}")
            # Stale index: re-bind by name for the next wake cycle.
            try:
                import Voice as voice_mod

                if voice_mod._is_portaudio_device_error(exc):
                    new_idx = voice_mod.reresolve_input_device(
                        exclude={int(device)} if device is not None else set()
                    )
                    if new_idx is not None:
                        from dataclasses import replace

                        self.settings = replace(self.settings, input_device=new_idx)
            except Exception:
                pass
            # Avoid tight spin when PortAudio cannot open the mic.
            time.sleep(1.0)
            return WakeResult(detected=False, mode="acoustic")

        return WakeResult(detected=False, mode="acoustic")

    def _capture_post_wake(self, stream, first_chunk: np.ndarray) -> np.ndarray:
        """Continue a short VAD-ish capture after wake for same-utterance commands."""
        silence_limit = self.settings.vad_silence_ms / 1000.0
        max_sec = min(8.0, self.settings.max_recording_seconds)
        energy = self.settings.energy_threshold
        chunks: List[np.ndarray] = [first_chunk]
        speaking = True
        silence_start = None
        started = time.time()
        chunk = self.settings.wake_chunk

        while time.time() - started < max_sec:
            if self._stop.is_set():
                break
            try:
                audio, _ = stream.read(chunk)
            except Exception:
                break
            audio = np.squeeze(audio).astype(np.int16)
            float_chunk = (audio.astype(np.float32) / 32768.0).reshape(-1, 1)
            chunks.append(float_chunk)
            vol = float(np.sqrt(np.mean(float_chunk ** 2)))
            if vol > energy:
                speaking = True
                silence_start = None
            elif speaking:
                if silence_start is None:
                    silence_start = time.time()
                elif time.time() - silence_start >= silence_limit:
                    break
        return np.concatenate(chunks, axis=0)


class LegacySttWakeListener(WakeListenerBase):
    """
    LEGACY: wake via continuous STT (Whisper always listening).
    Used only when acoustic wake cannot start.
    """

    def __init__(self, listen_fn: Callable[..., str], settings: VoiceSettings):
        self.listen_fn = listen_fn
        self.settings = settings
        self._stop = threading.Event()
        self.mode = "legacy_stt"

    def start(self) -> None:
        self._stop.clear()
        print(
            "[WAKE] Acoustic unavailable — legacy STT wake fallback"
        )

    def stop(self) -> None:
        self._stop.set()

    def wait_for_wake(self, *, timeout: Optional[float] = None) -> WakeResult:
        from WakeWord import is_wake_word, remove_wake_word

        while not self._stop.is_set():
            text = self.listen_fn(
                wait_timeout=timeout,
                llm_correct=False,
            )
            detect_t0 = time.perf_counter()
            if not text:
                if timeout is not None:
                    return WakeResult(detected=False, mode="legacy_stt")
                continue
            text = str(text).strip()
            try:
                from core.utterance_noise import looks_ambient_bleed, looks_noise_like

                if looks_ambient_bleed(text) or looks_noise_like(text):
                    print(f"[WAKE] ignored ambient/noise STT: {text[:80]}")
                    continue
            except Exception:
                pass
            if is_wake_word(text):
                remainder = remove_wake_word(text)
                self.last_remainder = remainder
                self.last_transcript = text
                return WakeResult(
                    detected=True,
                    score=1.0,
                    model_name="legacy_stt",
                    mode="legacy_stt",
                    audio_after_wake=None,
                    latency_ms=(time.perf_counter() - detect_t0) * 1000.0,
                )
            # Without custom wake model: clear action commands may wake the session.
            try:
                from core.utterance_noise import looks_idle_action_command

                if looks_idle_action_command(text):
                    print(f"[WAKE] idle action accepted (no wake phrase): {text[:80]}")
                    self.last_remainder = text
                    self.last_transcript = text
                    return WakeResult(
                        detected=True,
                        score=0.85,
                        model_name="legacy_stt_idle_action",
                        mode="legacy_stt",
                        audio_after_wake=None,
                        latency_ms=(time.perf_counter() - detect_t0) * 1000.0,
                    )
            except Exception:
                pass
            self.last_transcript = text
        return WakeResult(detected=False, mode="legacy_stt")


def create_wake_listener(
    settings: VoiceSettings,
    *,
    state_machine: Optional[SpeechStateMachine] = None,
    is_speaking: Optional[Callable[[], bool]] = None,
    listen_fn: Optional[Callable[..., str]] = None,
    force_fake: bool = False,
) -> Tuple[WakeListenerBase, str]:
    """
    Returns (listener, mode_label).

    Acoustic only when custom elian*.onnx exists. Stock OpenWakeWord models do
    not detect "hej elian" — without a custom model, use legacy STT wake.
    """
    if force_fake:
        return FakeWakeListener(), "fake"

    if not settings.wake_enabled:
        print("[WAKE] Disabled in config — legacy STT wake")
        if listen_fn is None:
            raise RuntimeError("listen_fn required for legacy wake")
        return LegacySttWakeListener(listen_fn, settings), "legacy_stt"

    custom = has_custom_elian_wake_model(settings.wake_model)
    if not custom:
        print(
            "[WAKE] No custom elian.onnx — legacy STT wake "
            "(stock OpenWakeWord cannot hear Elian)"
        )
        if settings.wake_legacy_stt_fallback and listen_fn is not None:
            return LegacySttWakeListener(listen_fn, settings), "legacy_stt"
        print("[WAKE] Legacy STT fallback disabled — no wake backend")
        acoustic = AcousticWakeListener(
            settings,
            state_machine=state_machine,
            is_speaking=is_speaking,
        )
        return acoustic, "acoustic_failed"

    acoustic = AcousticWakeListener(
        settings,
        state_machine=state_machine,
        is_speaking=is_speaking,
    )
    if acoustic.load():
        return acoustic, "acoustic"

    if settings.wake_legacy_stt_fallback and listen_fn is not None:
        print(
            "[WAKE] Acoustic unavailable — legacy STT wake fallback"
        )
        return LegacySttWakeListener(listen_fn, settings), "legacy_stt"

    print("[WAKE] No wake backend available")
    return acoustic, "acoustic_failed"
