# ==========================
# ELIAN — utterance helpers (Phase 8 / 11.1)
# ==========================
from __future__ import annotations

import re
from dataclasses import dataclass


FILLER_ONLY = re.compile(
    # Vocalized hesitation: eh/ehh/ahh/aaa/mm… — not a command.
    # Keep y+/e+; require h+ or multi-vowel for other letters so "aha" stays out.
    r"^(?:(?:[aeiouy]+h+|[aeiouy]{2,}|y+|e+|mm+|uhm+|hmm+|no+|tego+|wiesz+|jakby+)[\s.]*)+$",
    re.I,
)

WAIT_THINKING = re.compile(
    r"(?:^|\b)(?:czekaj|moment|chwila|chwileczkę|chwileczke|daj sekund[eę]|zapomniałem|zapomnialem|"
    r"zaraz|sekund[eę]|daj mi pomyśleć)(?:\b|$)",
    re.I,
)

NEGATED_ACTION = re.compile(
    r"^\s*nie\s+(?:"
    r"otwieraj|otwiera[cć]|włączaj|wlaczaj|w[lł][aą]cza[cć]|"
    r"zamykaj|zamyka[cć]|usuwaj|usuwa[cć]|wyłączaj|wylaczaj|wy[lł][aą]cza[cć]|"
    r"puszczaj|puszcza[cć]|wysyłaj|wysylaj|"
    r"uruchamiaj|uruchamia[cć]|kasuj|kasa[cć]|wykonuj|wykona[cć]|"
    r"m[oó]w|powiedz|powiedzie[cć]|"
    r"chc[eę]\s+otwiera[cć]"
    r")\b",
    re.I,
)

# "nie" + predicate that CHANGES meaning — never treat as mid-utterance swap.
MEANING_NEGATION_AFTER = re.compile(
    r"^(?:"
    r"jest|s[aą]|by[lł]|by[lł]a|by[lł]o|b[eę]d[eę]|"
    r"rozumiem|wiem|chc[eę]|mog[eę]|potrafi[eę]?|"
    r"otwieraj|w[lł][aą]czaj|wy[lł][aą]czaj|zamykaj|r[oó]b|rob|"
    r"puszczaj|uruchamiaj|kasuj|usuwaj|wykonuj|"
    r"dzia[lł]a|mam|masz|mo[zż]na"
    r")\b",
    re.I,
)

HYPOTHETICAL = re.compile(
    r"(?:co\s+by\s+się\s+stało|co\s+by\s+sie\s+stalo|gdybym|gdybyś|gdybys|"
    r"co\s+oznacza\s+komenda|co\s+by\s+było|co\s+by\s+bylo|"
    r"czy\s+mógłbyś\s+wyjaśnić|czy\s+moglbys\s+wyjasnic)",
    re.I,
)

CAPABILITY_QUESTION = re.compile(
    r"^\s*czy\s+(?:możesz|mozesz|umiesz|potrafisz|dasz\s+radę|dasz\s+rade)\b",
    re.I,
)

FRUSTRATION = re.compile(
    r"\b(?:nie\s+działa|nie\s+dziala|źle|zle|nie\s+o\s+to\s+mi\s+chodzi|"
    r"nie\s+o\s+to\s+chodziło|nie\s+o\s+to\s+chodzilo|"
    r"to\s+nie\s+to|popraw\s+to)\b",
    re.I,
)

# Full-utterance rejection / correction — must NEVER lose leading "nie".
REJECTION_CORRECTION = re.compile(
    r"^\s*nie\s*,\s*nie\b|"
    r"^\s*nie[,.]?\s+nie\s+o\s+to\b|"
    r"^\s*nie[,.]?\s+o\s+to\s+(?:mi\s+)?chodzi|"
    r"^\s*to\s+nie\s+to\b|"
    r"^\s*nie\s+o\s+to\b|"
    r"^\s*chodziło\s+mi\s+o\s+coś\s+innego\b|"
    r"^\s*miałem\s+na\s+myśli\s+coś\s+innego\b",
    re.I,
)

QUOTED_COMMAND = re.compile(
    r"(?:co\s+oznacza|co\s+znaczy|wyjaśnij|wyjasnij).{0,40}['\"„«].+['\"”»]",
    re.I,
)

OPEN_VERB = re.compile(
    r"^((?:otwórz|otworz|włącz|wlacz|puść|pusc|uruchom|zamknij|kliknij|wpisz)\s+)",
    re.I,
)

# Explicit retarget markers (nie jednak / znaczy / właściwie / …).
# Bare ``nie`` is handled separately: retarget for click/type, exclusion for open/play.
CORRECTION_MARKER = re.compile(
    r"(?:nie[,.]?\s+jednak\s+|znaczy\s+|właściwie\s+|wlasciwie\s+|"
    r"jednak\s+|to znaczy\s+)",
    re.I,
)

BARE_NIE_MARKER = re.compile(r"(?<=\S)\s+nie\s+(?=\S)", re.I)
APP_OPEN_VERB = re.compile(
    r"^(?:otwórz|otworz|włącz|wlacz|puść|pusc|uruchom|zamknij)\s+",
    re.I,
)
CLICK_TYPE_VERB = re.compile(r"^(?:kliknij|wpisz)\s+", re.I)


@dataclass
class UtteranceAnalysis:
    original: str
    normalized: str
    corrected: bool = False
    filler_only: bool = False
    wait_like: bool = False
    negated_action: bool = False
    hypothetical: bool = False
    capability_question: bool = False
    frustration: bool = False
    explain_only: bool = False
    rejection_correction: bool = False
    intent_hint: str = ""  # act | explain | wait | correct


def is_rejection_correction(text: str) -> bool:
    """True when the utterance rejects prior assumption / asks to correct."""
    return bool(REJECTION_CORRECTION.search(str(text or "").strip()))


def negation_was_lost(raw: str, candidate: str) -> bool:
    """True when meaning-changing 'nie' present in raw but missing in candidate."""
    raw_l = str(raw or "").lower()
    cand_l = str(candidate or "").lower()
    if not re.search(r"\bnie\b", raw_l):
        return False
    if re.search(r"\bnie\b", cand_l):
        return False
    # Raw had nie; candidate dropped it — always meaning risk.
    return True


def _marker_is_meaning_negation(raw: str, match: re.Match) -> bool:
    after = raw[match.end() :].lstrip()
    if MEANING_NEGATION_AFTER.match(after):
        return True
    # Leading "Nie …" cognitive / refusal without comma-correction shape.
    if match.start() == 0 and re.match(r"^\s*nie\b", raw, re.I):
        if not re.match(r"^\s*nie\s*,", raw, re.I):
            return True
    return False


def apply_mid_utterance_correction(text: str) -> tuple[str, bool]:
    raw = str(text or "").strip()
    if not raw:
        return raw, False

    # Phase 10.3 — never strip negation from rejection phrases.
    if is_rejection_correction(raw):
        return raw, False

    if NEGATED_ACTION.match(raw):
        rest = raw[3:]
        if not CORRECTION_MARKER.search(rest):
            return raw, False

    markers = list(CORRECTION_MARKER.finditer(raw))
    # Phase 11.1 — drop markers that are semantic negations (nie jest / nie rozumiem).
    markers = [m for m in markers if not _marker_is_meaning_negation(raw, m)]
    used_bare_nie = False
    if not markers and CLICK_TYPE_VERB.match(raw):
        # "Kliknij Ustawienia nie Dźwięk" → retarget. APP open "X nie Y" stays exclusion.
        markers = [
            m
            for m in BARE_NIE_MARKER.finditer(raw)
            if not _marker_is_meaning_negation(raw, m)
        ]
        used_bare_nie = bool(markers)
    if not markers:
        return raw, False

    last = markers[-1]
    if last.start() == 0 and NEGATED_ACTION.match(raw) and len(markers) == 1:
        return raw, False

    # Leading "Nie," used as rejection opener — do not treat as mid-utterance swap.
    if last.start() == 0 and re.match(r"^\s*nie\s*,", raw, re.I):
        return raw, False

    before = raw[: last.start()].strip(" .,…")
    after = raw[last.end() :].strip(" .,…")
    if not after:
        return raw, False

    # Bare nie after open/play verbs is exclusion, not retarget.
    if used_bare_nie and APP_OPEN_VERB.match(before):
        return raw, False

    verb = OPEN_VERB.match(before)
    if verb and not OPEN_VERB.match(after):
        candidate = (verb.group(1) + after).strip()
    else:
        # Only allow bare replacement when an action verb preceded the marker
        # (true mid-utterance retarget). Otherwise keep original.
        if not verb:
            return raw, False
        candidate = after

    # Losing "nie" is OK when it was the retarget marker after an action verb
    # ("Kliknij A nie B" → "Kliknij B").
    if negation_was_lost(raw, candidate) and not verb:
        return raw, False
    return candidate, True


def analyze_utterance(text: str) -> UtteranceAnalysis:
    original = str(text or "").strip()
    lowered = original.lower().strip()

    if not lowered:
        return UtteranceAnalysis(original=original, normalized="", intent_hint="wait")

    if FILLER_ONLY.match(lowered):
        return UtteranceAnalysis(
            original=original,
            normalized="",
            filler_only=True,
            wait_like=True,
            intent_hint="wait",
        )

    if is_rejection_correction(original):
        return UtteranceAnalysis(
            original=original,
            normalized=original,
            corrected=False,
            frustration=True,
            rejection_correction=True,
            intent_hint="correct",
        )

    corrected_text, corrected = apply_mid_utterance_correction(original)
    if negation_was_lost(original, corrected_text):
        corrected_text, corrected = original, False
    normalized = corrected_text.strip()
    low = normalized.lower()

    negated = bool(NEGATED_ACTION.match(low))
    hypo = bool(HYPOTHETICAL.search(low))
    capability = bool(CAPABILITY_QUESTION.match(low))
    quoted = bool(QUOTED_COMMAND.search(low))
    frustration = bool(FRUSTRATION.search(low))
    wait_like = bool(WAIT_THINKING.search(low)) and len(low.split()) <= 4

    explain_only = hypo or quoted or capability
    intent_hint = "act"
    if wait_like and not corrected:
        intent_hint = "wait"
    elif explain_only or negated:
        intent_hint = "explain"
    elif frustration:
        intent_hint = "correct"

    return UtteranceAnalysis(
        original=original,
        normalized=normalized,
        corrected=corrected,
        wait_like=wait_like,
        negated_action=negated,
        hypothetical=hypo,
        capability_question=capability,
        frustration=frustration,
        explain_only=explain_only,
        rejection_correction=False,
        intent_hint=intent_hint,
    )


_PURE_REJECTION = re.compile(
    r"^\s*(?:"
    r"niee*"
    r"|nie[,.]?\s+nie\s+o\s+to\b.*"
    r"|nie[,.]?\s+o\s+to\s+(?:mi\s+)?chodzi\w*"
    r"|to\s+nie\s+to"
    r"|chodzi(?:ło|lo)\s+mi\s+o\s+co[sś]\s+innego"
    r"|mia(?:łem|lem|łam|lam)\s+na\s+my[sś]li\s+co[sś]\s+innego"
    r")[.!?…]*\s*$",
    re.I,
)
_CORRECTION_TARGET = re.compile(
    r"(?:"
    r"(?:chodzi(?:ło|lo)\s+mi\s+o|mia(?:łem|lem|łam|lam)\s+na\s+my[sś]li)\s+(.+)$|"
    r"^\s*nie\s*,\s*(.+)$|"
    r"^\s*nie\s+(?!o\s+to\b|ten\b|to\b|wiem\b|mog[eę]\b|chc[eę]\b|"
    r"teraz\b|dzi[sś]\b|jutro\b|niewa[zż]ne\b|anuluj\b)(.+)$"
    r")",
    re.I,
)
_GENERIC_TARGET = re.compile(
    r"^(?:co[sś]\s+innego|inne|to)$",
    re.I,
)


def is_bare_rejection(text: str) -> bool:
    """Standalone 'nie' / 'nie o to chodziło' without a new target."""
    return bool(_PURE_REJECTION.match(str(text or "").strip()))


def extract_correction_target(text: str) -> str:
    """Remainder after a correction marker, or empty for pure rejection."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if is_bare_rejection(raw):
        return ""
    match = _CORRECTION_TARGET.search(raw)
    if not match:
        return ""
    target = ""
    for group in match.groups():
        if group:
            target = str(group).strip(" .,!?…")
            break
    if not target or _GENERIC_TARGET.match(target):
        return ""
    return target


def strip_trailing_wait_marker(text: str) -> tuple[str, bool]:
    raw = str(text or "").strip()
    if not raw:
        return raw, False
    match = re.search(
        r"^(.*?)(?:\.\.\.|…|,)?\s*(?:czekaj|moment|chwila)\s*$",
        raw,
        re.I,
    )
    if not match:
        return raw, False
    head = match.group(1).strip(" .,…")
    return head, True
