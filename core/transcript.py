# ==========================
# ELIAN — dual transcript envelope (C.5.1)
# ==========================
"""Immutable Whisper raw vs normalized. Never assign .lower() into raw_*. """

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

_LAST: Optional["TranscriptEnvelope"] = None


@dataclass(frozen=True)
class TranscriptEnvelope:
    raw_transcript: str
    normalized_transcript: str


def envelope_from_whisper(whisper_text: str, *, corrected: str | None = None) -> TranscriptEnvelope:
    """Keep Whisper casing in raw_*. Command path may use corrected separately.

    normalized_transcript is lowercase of the corrected (or raw) form for matching.
    """
    raw = str(whisper_text or "").strip()
    fixed = str(corrected if corrected is not None else raw).strip() or raw
    return TranscriptEnvelope(
        raw_transcript=raw,
        normalized_transcript=fixed.lower(),
    )

def set_last_transcript(envelope: Optional[TranscriptEnvelope]) -> None:
    global _LAST
    _LAST = envelope


def get_last_transcript() -> Optional[TranscriptEnvelope]:
    return _LAST
