# ==========================
# ELIAN AI ASSISTANT
# Version 0.6
# WakeWord.py
# ==========================
"""Wake / address detection. Strip ONLY a leading address prefix — never in-content 'Elian'."""

import re
from dataclasses import dataclass

from Config import config


LOCAL_WAKE_PREFIXES = ("hi", "hej", "hey", "ej", "ok", "okay")

_WAKE_NAMES = r"(?:elian|eljan|ilian|eliasz|eliam|eliom|eleon|adrian)"
# Include English "hi" — product wake is "Hi Elian" (en-US config).
_WAKE_GREET = r"(?:hi|hej|hey|ej|ok|okay|cze[sś][cć]|siema)"
# Leading address only: "Hej Elian,", "Elian,", "Hej, Elian."
LEADING_WAKE = re.compile(
    rf"^\s*(?:{_WAKE_GREET}\s*[,.]?\s*)?{_WAKE_NAMES}\b\s*[,.!:;]?\s*",
    re.I,
)
# Whisper often hears "Hi Elian" as "higher lane" / "High LN" / "Hey, Alien".
LEADING_WAKE_ASR_SLIP = re.compile(
    r"^\s*(?:"
    r"higher\s+lane|higher\s+lain|higher\s+lean|"
    r"high\s+ln|hi\s+ln|high\s+el\s*n|"
    r"hi\s*[,.]?\s*alien|high\s*[,.]?\s*elian|"
    r"hey\s*[,.]?\s*alien|hey\s*[,.]?\s*leone|he\s*[,.]?\s*elian"
    r")\b\s*[,.!:;]?\s*",
    re.I,
)

# Phrase repairs before single-token name map (order matters).
_NORMALIZE_PHRASES = (
    ("higher lane", "hi elian"),
    ("higher lain", "hi elian"),
    ("higher lean", "hi elian"),
    ("high ln", "hi elian"),
    ("hi ln", "hi elian"),
    ("hey, alien", "hey elian"),
    ("hi, alien", "hi elian"),
    ("hey alien", "hey elian"),
    ("hi alien", "hi elian"),
    ("high elian", "hi elian"),
    ("hey leone", "hey elian"),
)


def normalize(text):

    replacements = {

        "eljan": "elian",
        "ilian": "elian",
        "eliasz": "elian",
        "eliam": "elian",
        "eliom": "elian",
        "eleon": "elian",
        "adrian": "adrian"

    }


    text = text.lower()

    for wrong, correct in _NORMALIZE_PHRASES:
        text = text.replace(wrong, correct)

    for wrong, correct in replacements.items():

        text = text.replace(
            wrong,
            correct
        )


    return text



def is_wake_word(text):

    if not text:

        return False


    return find_wake_word(text) is not None



def find_wake_word(text):
    """Span of a LEADING wake/address prefix, or None. Never matches in-content names."""

    if not text:

        return None

    normalized = normalize(text)
    match = LEADING_WAKE.match(normalized)
    if match and match.end() > 0:
        return match.span()
    slip = LEADING_WAKE_ASR_SLIP.match(str(text or "").lower())
    if slip and slip.end() > 0:
        return slip.span()

    # Configured multi-word phrases still only count at the start.
    wake_words = sorted(
        config["wake_words"],
        key=len,
        reverse=True
    )
    for word in wake_words:
        normalized_word = normalize(word)
        if not normalized_word:
            continue
        pattern = rf"^{re.escape(normalized_word)}\b"
        found = re.search(pattern, normalized)
        if found:
            return found.span()

    return None


def _clean_remainder(text: str) -> str:
    cleaned = str(text or "")
    cleaned = re.sub(r"^[\s,.!?;:\-]+", "", cleaned)
    cleaned = re.sub(r"[\s,.!?;:\-]+$", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def remove_wake_word(text):
    """Remove the first addressing phrase only. Later 'Elian' is command content."""
    return extract_wake(text).raw_command


def extract_command_from_transcript(text: str) -> str:
    """Public helper: wake prefix off, in-content Elian kept."""
    return extract_wake(text).raw_command


@dataclass
class WakeResult:
    raw_transcript: str = ""
    normalized_transcript: str = ""
    raw_command: str = ""
    normalized_command: str = ""
    wake_detected: bool = False
    wake_span: tuple | None = None


def _length_safe_lower(text: str) -> str:
    """Lowercase without the name-rewrite map (keeps offsets aligned with raw)."""
    return str(text or "").lower()


def _leading_wake_span(lowered: str) -> tuple | None:
    """Wake span on length-preserving lowercase — never rewrite names before slicing."""
    if not lowered:
        return None
    match = LEADING_WAKE.match(lowered)
    if match and match.end() > 0:
        return match.span()
    # STT slip spans must be measured on the raw lowered string (same length).
    slip = LEADING_WAKE_ASR_SLIP.match(lowered)
    if slip and slip.end() > 0:
        return slip.span()
    wake_words = sorted(config.get("wake_words") or [], key=len, reverse=True)
    for word in wake_words:
        token = str(word or "").strip().lower()
        if not token:
            continue
        found = re.search(rf"^{re.escape(token)}\b", lowered)
        if found:
            return found.span()
    return None


def extract_wake(text: str) -> WakeResult:
    """Detect wake on normalized text; slice the command from the raw transcript."""
    raw_transcript = str(text or "")
    lowered = _length_safe_lower(raw_transcript)
    normalized_transcript = normalize(raw_transcript)
    # Slice from length-safe lowercase so a later "Elian" keeps raw casing.
    span = _leading_wake_span(lowered)
    if span is None:
        command = _clean_remainder(raw_transcript)
        return WakeResult(
            raw_transcript=raw_transcript,
            normalized_transcript=normalized_transcript,
            raw_command=command,
            normalized_command=_length_safe_lower(command),
            wake_detected=False,
            wake_span=None,
        )
    start, end = span
    if start > 0:
        command = _clean_remainder(raw_transcript)
        return WakeResult(
            raw_transcript=raw_transcript,
            normalized_transcript=normalized_transcript,
            raw_command=command,
            normalized_command=_length_safe_lower(command),
            wake_detected=False,
            wake_span=span,
        )
    raw_command = _clean_remainder(raw_transcript[end:])
    return WakeResult(
        raw_transcript=raw_transcript,
        normalized_transcript=normalized_transcript,
        raw_command=raw_command,
        normalized_command=_length_safe_lower(raw_command),
        wake_detected=True,
        wake_span=(start, end),
    )
