# ==========================
# ELIAN — companion STT ingest (optional WAV upload)
# ==========================
"""Decode uploaded WAV and call Voice.transcribe_audio — no voice-loop rewrite.

Does not touch WakeWord / voice pipeline. Audio other than WAV is UNAVAILABLE.
"""

from __future__ import annotations

import io
import wave
from typing import Callable, Optional, Tuple

import numpy as np

TARGET_RATE = 16000
TranscribeFn = Callable[[np.ndarray], str]


def decode_wav_pcm(data: bytes, *, target_rate: int = TARGET_RATE) -> np.ndarray:
    if not data or len(data) < 12:
        raise ValueError("empty")
    with wave.open(io.BytesIO(data), "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        rate = int(wf.getframerate() or 0)
        frames = wf.readframes(wf.getnframes())
    if sampwidth != 2:
        raise ValueError("unsupported_width")
    if rate <= 0:
        raise ValueError("bad_rate")
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != target_rate and audio.size:
        duration = audio.size / float(rate)
        target_n = max(1, int(round(duration * target_rate)))
        x_old = np.linspace(0.0, 1.0, audio.size, endpoint=False)
        x_new = np.linspace(0.0, 1.0, target_n, endpoint=False)
        audio = np.interp(x_new, x_old, audio).astype(np.float32)
    return audio


def _default_transcribe(audio: np.ndarray) -> str:
    # Lazy import: Voice.py pulls the STT stack. Do not rewrite the mic loop.
    from Voice import transcribe_audio

    return str(transcribe_audio(audio, llm_correct=False) or "").strip()


def transcribe_wav_bytes(
    data: bytes,
    *,
    transcribe_fn: Optional[TranscribeFn] = None,
) -> Tuple[str, str]:
    """Returns (text, status) where status is ok | invalid | unavailable."""
    try:
        audio = decode_wav_pcm(data)
    except Exception:
        return "", "invalid"
    fn = transcribe_fn
    if fn is None:
        try:
            fn = _default_transcribe
        except Exception:
            return "", "unavailable"
    try:
        text = str(fn(audio) or "").strip()
    except Exception:
        return "", "unavailable"
    return text, "ok"
