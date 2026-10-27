# ==========================
# ELIAN — TTS engine abstraction (Fish Audio + English Piper fallback)
# ==========================
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


@dataclass
class TTSStatus:
    engine: str
    healthy: bool
    detail: str = ""
    last_latency_ms: float = 0.0
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "engine": self.engine,
            "healthy": self.healthy,
            "detail": self.detail,
            "last_latency_ms": self.last_latency_ms,
            "extras": dict(self.extras),
        }


class TTSEngine(ABC):
    name = "base"

    @abstractmethod
    def speak(self, text: str, **kwargs: Any) -> bool:
        raise NotImplementedError

    def stream(self, text: str, **kwargs: Any) -> bool:
        return self.speak(text, **kwargs)

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> TTSStatus:
        raise NotImplementedError

    def status(self) -> TTSStatus:
        return self.health_check()

    def synthesize_to_file(self, text: str, path: str) -> Tuple[bool, float]:
        """Generate audio file without requiring mic/playback. Returns (ok, latency_ms)."""
        raise NotImplementedError


def _voice_paths() -> Tuple[str, str, str]:
    piper = ""
    model = ""
    language = "en-GB"
    try:
        from Config import config, get_config_value

        vc = config.get("voice") if isinstance(config.get("voice"), dict) else {}
        piper = str(os.environ.get("ELIAN_PIPER_PATH") or vc.get("piper_path") or "")
        model = str(os.environ.get("ELIAN_PIPER_MODEL_PATH") or vc.get("model_path") or "")
        language = str(get_config_value("voice", "tts_language", default="en-GB") or "en-GB")
    except Exception:
        pass
    return piper, model, language


def _piper_synthesize(text: str, wav_path: str) -> Tuple[bool, float, str]:
    """Direct Piper CLI synthesis — no playback. Returns ok, latency_ms, detail."""
    piper, model, language = _voice_paths()
    t0 = time.perf_counter()
    if not piper or not (os.path.isfile(piper) or shutil.which(piper)):
        return False, 0.0, "piper_exe_missing"
    if not model or not os.path.isfile(model):
        return False, 0.0, "piper_model_missing"
    # Refuse Polish-only models when English-first is configured.
    model_name = Path(model).name.lower()
    if language.lower().startswith("en") and model_name.startswith("pl_"):
        return False, 0.0, "polish_model_not_allowed_for_english"
    try:
        proc = subprocess.run(
            [piper, "--model", model, "--output_file", wav_path],
            input=str(text or "").encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        latency = (time.perf_counter() - t0) * 1000.0
        if proc.returncode != 0 or not os.path.isfile(wav_path) or os.path.getsize(wav_path) < 44:
            err = (proc.stderr or b"")[:200].decode("utf-8", errors="replace")
            return False, latency, f"piper_failed:{err}"
        return True, latency, f"piper:{Path(model).name}:{language}"
    except Exception as exc:
        latency = (time.perf_counter() - t0) * 1000.0
        return False, latency, f"piper_error:{exc}"


class FallbackTTS(TTSEngine):
    """English-capable Piper fallback (not Polish-only)."""

    name = "english_piper"

    def __init__(self) -> None:
        self._last_latency_ms = 0.0
        self._init_ms = 0.0

    def speak(self, text: str, **kwargs: Any) -> bool:
        t0 = time.perf_counter()
        try:
            import Voice

            # Keep Voice module model path aligned with config (English).
            piper, model, _ = _voice_paths()
            if piper:
                Voice.PIPER = piper
            if model:
                Voice.VOICE_MODEL = model
            Voice.speak(str(text or ""), cooldown=kwargs.get("cooldown"))
            self._last_latency_ms = (time.perf_counter() - t0) * 1000.0
            return True
        except Exception as exc:
            self._last_latency_ms = (time.perf_counter() - t0) * 1000.0
            print(f"[TTS] english piper speak failed: {exc}")
            return False

    def stream(self, text: str, **kwargs: Any) -> bool:
        t0 = time.perf_counter()
        try:
            import Voice

            piper, model, _ = _voice_paths()
            if piper:
                Voice.PIPER = piper
            if model:
                Voice.VOICE_MODEL = model
            if hasattr(Voice, "speak_stream"):
                Voice.speak_stream(str(text or ""))
            else:
                Voice.speak(str(text or ""))
            self._last_latency_ms = (time.perf_counter() - t0) * 1000.0
            return True
        except Exception:
            return self.speak(text, **kwargs)

    def stop(self, **kwargs: Any) -> None:
        try:
            import Voice

            if hasattr(Voice, "stop_speaking"):
                Voice.stop_speaking(**kwargs)
            elif hasattr(Voice, "cancel_piper_synthesis"):
                Voice.cancel_piper_synthesis()
        except Exception:
            pass

    def synthesize_to_file(self, text: str, path: str) -> Tuple[bool, float]:
        ok, latency, detail = _piper_synthesize(text, path)
        self._last_latency_ms = latency
        self._init_ms = latency  # first-call includes model load for cold path
        if not ok:
            print(f"[TTS] synthesize_to_file failed: {detail}")
        return ok, latency

    def health_check(self) -> TTSStatus:
        piper, model, language = _voice_paths()
        exe_ok = bool(piper) and (os.path.isfile(piper) or shutil.which(piper) is not None)
        model_ok = bool(model) and os.path.isfile(model)
        english_ok = True
        if language.lower().startswith("en") and Path(model or "").name.lower().startswith("pl_"):
            english_ok = False
        ok = exe_ok and model_ok and english_ok
        detail = "ready" if ok else (
            "polish_model_not_allowed_for_english"
            if not english_ok
            else ("piper_model_missing" if not model_ok else "piper_exe_missing")
        )
        return TTSStatus(
            engine=self.name,
            healthy=ok,
            detail=detail,
            last_latency_ms=self._last_latency_ms,
            extras={
                "piper_path": piper,
                "model_path": model,
                "tts_language": language,
                "model_name": Path(model).name if model else "",
                "readiness": "READY" if ok else "UNAVAILABLE",
            },
        )


class FishAudioTTS(TTSEngine):
    """
    Fish Audio S2 Pro adapter via local s2.exe (GGUF/CUDA).

    READY only when s2.exe + model + tokenizer exist and CUDA synth has been verified.
    Never reports false readiness. Python Fish SDKs are optional legacy detectors only.
    """

    name = "fish_audio_s2_pro"

    def __init__(self, *, voice_id: str = "", api_key: str = "") -> None:
        self.voice_id = voice_id
        self.api_key = api_key
        self._last_latency_ms = 0.0
        self._init_error = "not_checked"
        self._probe_on_health = False
        try:
            from Config import get_config_value

            self._probe_on_health = bool(
                get_config_value("voice", "fish_probe_on_health", default=False)
            )
        except Exception:
            self._probe_on_health = False

    @staticmethod
    def sdk_available() -> bool:
        """True when local s2.cpp path is present (not the cloud Python SDK)."""
        try:
            from voice.fish_s2_backend import files_ready

            ok, _ = files_ready()
            return ok
        except Exception:
            return False

    @staticmethod
    def python_sdk_available() -> bool:
        for mod in ("fish_audio_sdk", "fishaudio", "fish_speech"):
            if importlib.util.find_spec(mod) is not None:
                return True
        return False

    def synthesize_to_file(self, text: str, path: str) -> Tuple[bool, float]:
        from voice.fish_s2_backend import synthesize_s2_wav

        ok, latency, detail = synthesize_s2_wav(str(text or ""), path, acquire_gpu=True)
        self._last_latency_ms = latency
        self._init_error = detail
        if not ok:
            print(f"[TTS] Fish s2 synthesize failed: {detail}")
        return ok, latency

    def speak(self, text: str, **kwargs: Any) -> bool:
        t0 = time.perf_counter()
        fd, path = tempfile.mkstemp(prefix="elian_fish_", suffix=".wav")
        os.close(fd)
        try:
            try:
                from core.latency import current_tracker

                tracker = current_tracker()
            except Exception:
                tracker = None
            if tracker:
                tracker.mark("tts_queue_start")
                tracker.mark("tts_gen_start")
            ok, latency = self.synthesize_to_file(str(text or ""), path)
            if tracker:
                tracker.mark("tts_gen_end")
                # Prefer Fish-reported synthesis; never leave generate as 0 when wall > 0.
                if latency and latency > 0:
                    existing = tracker.marks.get("fish_synthesis_ms")
                    if not isinstance(existing, (int, float)) or float(existing) < 1.0:
                        tracker.record_ms("fish_synthesis_ms", float(latency))
                    tracker.record_ms("tts_generation_ms", float(latency))
            if not ok:
                self._last_latency_ms = (time.perf_counter() - t0) * 1000.0
                return False
            import Voice

            played = Voice.speak_from_wav(
                str(text or ""), path, cooldown=kwargs.get("cooldown")
            )
            self._last_latency_ms = (time.perf_counter() - t0) * 1000.0
            return bool(played)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def stop(self, **kwargs: Any) -> None:
        try:
            import Voice

            if hasattr(Voice, "stop_speaking"):
                Voice.stop_speaking(**kwargs)
        except Exception:
            pass
        try:
            from voice.fish_s2_backend import cancel_s2_synthesis

            cancel_s2_synthesis()
        except Exception:
            pass

    def health_check(self) -> TTSStatus:
        from voice.fish_s2_backend import health_snapshot, probe_cuda_once

        snap = health_snapshot()
        if snap.get("ready"):
            usable = True
            detail = snap.get("detail") or "ready"
        elif snap.get("forced_unavailable"):
            usable = False
            detail = "forced_unavailable"
        elif not snap.get("cuda_verified") and self._probe_on_health and not snap.get(
            "forced_unavailable"
        ):
            ok, detail = probe_cuda_once()
            usable = bool(ok)
            snap = health_snapshot()
        else:
            usable = bool(snap.get("ready"))
            detail = snap.get("detail") or "unavailable"
        self._init_error = str(detail)
        return TTSStatus(
            engine=self.name,
            healthy=usable,
            detail=self._init_error,
            last_latency_ms=self._last_latency_ms,
            extras={
                # Local .s2voice profile only — never treat fish_voice_id / cloud IDs as voice.
                "voice_profile": snap.get("voice_profile") or "",
                "voice_dir": snap.get("voice_dir") or "",
                "target": "Fish Audio S2 Pro (s2.cpp)",
                "clone_policy": "local_s2voice_only",
                "backend": (
                    "s2_server"
                    if snap.get("persistent_worker")
                    else "s2_exe"
                ),
                "sdk_available": self.sdk_available(),
                "python_sdk_available": self.python_sdk_available(),
                "quant": snap.get("quant"),
                "gpu_policy": snap.get("gpu_policy"),
                "gpu_device": snap.get("gpu_device"),
                "model_path": snap.get("model_path"),
                "exe": snap.get("exe"),
                "cuda_verified": snap.get("cuda_verified"),
                "readiness": "READY" if usable else "UNAVAILABLE",
                "persistent_worker": snap.get("persistent_worker"),
                "session": snap.get("session") or {},
            },
        )


class RoutedTTS(TTSEngine):
    """Prefer Fish when actually usable; otherwise English Piper fallback."""

    name = "routed"

    def __init__(
        self,
        *,
        prefer_fish: bool = True,
        fish_voice_id: str = "",
        fish_api_key: str = "",
    ) -> None:
        self.fish = FishAudioTTS(voice_id=fish_voice_id, api_key=fish_api_key)
        self.fallback = FallbackTTS()
        self.prefer_fish = prefer_fish
        self.active: TTSEngine = self.fallback
        self._select()

    def _fish_files_ready(self) -> bool:
        try:
            return bool(self.fish.sdk_available())
        except Exception:
            return False

    def _select(self) -> None:
        # READY reporting is strict (CUDA verified). Routing may TRY Fish when files exist.
        fish_st = self.fish.health_check()
        if self.prefer_fish and fish_st.healthy:
            self.active = self.fish
            return
        if self.prefer_fish and self._fish_files_ready():
            self.active = self.fish
            return
        self.active = self.fallback

    def speak(self, text: str, **kwargs: Any) -> bool:
        if self.prefer_fish and self._fish_files_ready():
            ok = self.fish.speak(text, **kwargs)
            if ok:
                self.active = self.fish
                try:
                    from core.crash_log import clear_degraded_subsystem

                    clear_degraded_subsystem("tts")
                except Exception:
                    pass
                return True
            print("[TTS] Fish UNAVAILABLE — using English Piper fallback")
        self.active = self.fallback
        ok = bool(self.fallback.speak(text, **kwargs))
        if ok:
            try:
                from core.crash_log import clear_degraded_subsystem

                clear_degraded_subsystem("tts")
            except Exception:
                pass
        return ok

    def stream(self, text: str, **kwargs: Any) -> bool:
        # Fish s2.cpp path is full-utterance; no clause streaming yet.
        if self.prefer_fish and self._fish_files_ready():
            ok = self.fish.speak(text, **kwargs)
            if ok:
                self.active = self.fish
                return True
        self.active = self.fallback
        return self.fallback.stream(text, **kwargs)

    def stop(self, **kwargs: Any) -> None:
        # Fish cancel + barge-safe PortAudio stop (force_portaudio_stop=False).
        self.fish.stop(**kwargs)
        self.fallback.stop(**kwargs)

    def synthesize_to_file(self, text: str, path: str) -> Tuple[bool, float]:
        if self.prefer_fish and self._fish_files_ready():
            ok, latency = self.fish.synthesize_to_file(text, path)
            if ok:
                self.active = self.fish
                return ok, latency
            print("[TTS] Fish UNAVAILABLE — using English Piper fallback")
        self.active = self.fallback
        return self.fallback.synthesize_to_file(text, path)

    def health_check(self) -> TTSStatus:
        self._select()
        fish_st = self.fish.health_check()
        fb_st = self.fallback.health_check()
        # Active engine for status: verified Fish if READY, else current try/fallback.
        if fish_st.healthy:
            active_name = self.fish.name
        elif self.prefer_fish and self._fish_files_ready():
            active_name = f"{self.fish.name}_unverified"
        else:
            active_name = self.fallback.name
        st = fb_st if not fish_st.healthy else fish_st
        st.extras = {
            **st.extras,
            "preferred": "fish" if self.prefer_fish else "fallback",
            "fish": fish_st.to_dict(),
            "fallback": fb_st.to_dict(),
            "active": active_name,
            "fish_readiness": fish_st.extras.get("readiness") or "UNAVAILABLE",
            "fallback_readiness": fb_st.extras.get("readiness") or "UNAVAILABLE",
        }
        return st


_ENGINE: Optional[RoutedTTS] = None


def get_tts_engine() -> RoutedTTS:
    global _ENGINE
    if _ENGINE is None:
        prefer = True
        voice_id = ""
        api_key = ""
        try:
            from Config import get_config_value

            prefer = bool(get_config_value("voice", "prefer_fish_tts", default=True))
            voice_id = str(get_config_value("voice", "fish_voice_id", default="") or "")
            api_key = str(os.environ.get("FISH_AUDIO_API_KEY") or "")
        except Exception:
            pass
        _ENGINE = RoutedTTS(
            prefer_fish=prefer, fish_voice_id=voice_id, fish_api_key=api_key
        )
    return _ENGINE


def reset_tts_engine_for_tests() -> None:
    global _ENGINE
    _ENGINE = None


def measure_english_synthesis(text: str = "Elian speaking English.") -> Dict[str, Any]:
    """Deterministic offline TTS measurement (no mic, no telemetry audio storage)."""
    engine = get_tts_engine()
    t_init0 = time.perf_counter()
    health = engine.health_check()
    init_ms = (time.perf_counter() - t_init0) * 1000.0
    fd, path = tempfile.mkstemp(prefix="elian_tts_", suffix=".wav")
    os.close(fd)
    try:
        ok, gen_ms = engine.synthesize_to_file(text, path)
        size = os.path.getsize(path) if ok and os.path.isfile(path) else 0
        return {
            "ok": ok,
            "init_ms": round(init_ms, 2),
            "generation_ms": round(gen_ms, 2),
            "first_audio_latency_ms": round(gen_ms, 2),
            "bytes": size,
            "active_engine": health.extras.get("active") or health.engine,
            "fish_readiness": health.extras.get("fish_readiness"),
            "fallback_readiness": health.extras.get("fallback_readiness"),
            "health": health.to_dict(),
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
