# ==========================
# ELIAN — voice settings (Phase 4)
# ==========================

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from Config import config, get_config_value


def _clamp(value: float, low: float, high: float, name: str) -> float:
    v = float(value)
    if v < low or v > high:
        raise ValueError(f"{name}={v} out of range [{low}, {high}]")
    return v


def _voice_section() -> Dict[str, Any]:
    section = config.get("voice", {})
    return section if isinstance(section, dict) else {}


@dataclass(frozen=True)
class VoiceSettings:
    wake_enabled: bool = True
    wake_model: str = ""  # path to custom elian.onnx (optional)
    wake_threshold: float = 0.5
    wake_cooldown: float = 1.5
    wake_legacy_stt_fallback: bool = True
    session_idle_timeout: float = 30.0
    vad_threshold: float = 0.5
    vad_silence_ms: float = 700.0
    vad_min_speech_ms: float = 250.0
    max_recording_seconds: float = 20.0
    pre_roll_ms: float = 400.0
    energy_threshold: float = 0.008
    barge_in_enabled: bool = True
    barge_in_energy: float = 0.02
    barge_in_grace_ms: float = 250.0
    echo_guard_ms: float = 600.0
    ambient_session: bool = True
    tts_enabled: bool = True
    tts_cache_size: int = 16
    tts_cooldown: float = 0.35
    # Phase 0–2: Brain prose streaming + chunked Piper (planner stays blocking).
    stream_llm_responses: bool = True
    stream_tts: bool = True
    tts_clause_flush_chars: int = 50
    listening_ack: str = "Słucham."
    listening_ack_on_bare_wake: bool = True
    input_device: Optional[Any] = None
    input_device_name: str = ""
    output_device: Optional[Any] = None
    output_device_name: str = ""
    sample_rate: int = 16000
    block_size: int = 512
    wake_chunk: int = 1280

    @classmethod
    def from_config(cls) -> "VoiceSettings":
        voice = _voice_section()
        settings = config.get("settings", {})
        if not isinstance(settings, dict):
            settings = {}

        idle = voice.get(
            "session_idle_timeout",
            settings.get("conversation_idle_timeout", 30),
        )
        threshold = float(voice.get("wake_threshold", 0.5))
        try:
            threshold = _clamp(threshold, 0.0, 1.0, "wake_threshold")
        except ValueError as exc:
            print(f"[CONFIG] {exc} — using 0.5")
            threshold = 0.5

        silence_ms = float(voice.get("vad_silence_ms", 700))
        if silence_ms < 200 or silence_ms > 5000:
            print(f"[CONFIG] vad_silence_ms={silence_ms} invalid — using 700")
            silence_ms = 700.0

        return cls(
            wake_enabled=bool(voice.get("wake_enabled", True)),
            wake_model=str(voice.get("wake_model", "") or ""),
            wake_threshold=threshold,
            wake_cooldown=max(0.0, float(voice.get("wake_cooldown", 1.5))),
            wake_legacy_stt_fallback=bool(
                voice.get("wake_legacy_stt_fallback", True)
            ),
            session_idle_timeout=max(5.0, float(idle)),
            vad_threshold=_safe_unit(
                float(voice.get("vad_threshold", 0.5)), "vad_threshold", 0.5
            ),
            vad_silence_ms=silence_ms,
            vad_min_speech_ms=max(
                50.0, float(voice.get("vad_min_speech_ms", 250))
            ),
            max_recording_seconds=max(
                2.0, float(voice.get("max_recording_seconds", 20))
            ),
            pre_roll_ms=max(0.0, float(voice.get("pre_roll_ms", 400))),
            energy_threshold=float(voice.get("energy_threshold", 0.008)),
            barge_in_enabled=bool(voice.get("barge_in_enabled", True)),
            barge_in_energy=float(voice.get("barge_in_energy", 0.02)),
            barge_in_grace_ms=max(
                0.0, float(voice.get("barge_in_grace_ms", 250))
            ),
            echo_guard_ms=max(0.0, float(voice.get("echo_guard_ms", 600))),
            ambient_session=bool(voice.get("ambient_session", True)),
            tts_enabled=bool(voice.get("tts_enabled", True)),
            tts_cache_size=max(0, int(voice.get("tts_cache_size", 16))),
            tts_cooldown=float(
                voice.get(
                    "tts_cooldown",
                    get_config_value(
                        "settings", "tts_cooldown", default=0.35
                    ),
                )
            ),
            stream_llm_responses=bool(voice.get("stream_llm_responses", True)),
            stream_tts=bool(voice.get("stream_tts", True)),
            tts_clause_flush_chars=max(
                0, int(voice.get("tts_clause_flush_chars", 50))
            ),
            listening_ack=str(voice.get("listening_ack", "Słucham.")),
            listening_ack_on_bare_wake=bool(
                voice.get("listening_ack_on_bare_wake", True)
            ),
            input_device=voice.get("input_device"),
            input_device_name=str(voice.get("input_device_name", "") or ""),
            output_device=voice.get("output_device"),
            output_device_name=str(voice.get("output_device_name", "") or ""),
            sample_rate=int(voice.get("sample_rate", 16000)),
            block_size=int(voice.get("block_size", 512)),
            wake_chunk=int(voice.get("wake_chunk", 1280)),
        )


def _safe_unit(value: float, name: str, default: float) -> float:
    try:
        return _clamp(value, 0.0, 1.0, name)
    except ValueError as exc:
        print(f"[CONFIG] {exc} — using {default}")
        return default


def validate_voice_config(raw: Optional[Dict[str, Any]] = None) -> List[str]:
    """Return list of validation errors (empty = ok)."""
    errors: List[str] = []
    voice = raw if raw is not None else _voice_section()
    if "wake_threshold" in voice:
        try:
            _clamp(float(voice["wake_threshold"]), 0.0, 1.0, "wake_threshold")
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
    if "vad_threshold" in voice:
        try:
            _clamp(float(voice["vad_threshold"]), 0.0, 1.0, "vad_threshold")
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
    return errors
