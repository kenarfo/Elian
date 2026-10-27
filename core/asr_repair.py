# ==========================
# ELIAN — safe ASR near-match for action verbs (C.4)
# ==========================
"""Repair STT slips on whitelisted verbs only. Never rewrite user payloads."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

SAFE_CANONICAL = (
    "otwórz",
    "zamknij",
    "kliknij",
    "wpisz",
    "zaznacz",
    "skopiuj",
    "wklej",
    "przewiń",
    "zwiększ",
    "zmniejsz",
    "włącz",
    "wyłącz",
    "dopisz",
    "cofnij",
)

_SAFE_FOLD = {
    "otworz": "otwórz",
    "otwierz": "otwórz",
    "otwóz": "otwórz",
    "zamkni": "zamknij",
    "klikni": "kliknij",
    "zaznasz": "zaznacz",
    "zasnacz": "zaznacz",
    "zaznaczć": "zaznacz",
    "skopjuj": "skopiuj",
    "przewin": "przewiń",
    "zwieksz": "zwiększ",
    "zmnejsz": "zmniejsz",
    "wlacz": "włącz",
    "wylacz": "wyłącz",
}

DESTRUCTIVE_CANONICAL = (
    "usuń",
    "usun",
    "wyślij",
    "wyslij",
    "kup",
    "zapłać",
    "zaplac",
    "restart",
    "restartuj",
    "sformatuj",
    "publikuj",
    "opublikuj",
)

_DESTRUCTIVE_CONTEXT = re.compile(
    r"\b(?:system|windows|komputer|konto|uprawnie|bezpiecze[nń]stw|"
    r"has[lł]o|bank|p[lł]atno[sś]|przelew|publikacj)\b",
    re.I,
)

_PAYLOAD_GUARD = re.compile(r"\b(?:wpisz|dopisz|napisz)\b", re.I)
_CONNECTOR = re.compile(r"\s+(?:i|oraz|potem|a\s+potem)\s+", re.I)


@dataclass
class AsrRepair:
    heard: str
    canonical: str
    confidence: float = 0.0
    reason: str = ""
    applied: bool = False
    needs_confirmation: bool = False
    repairs: List[Tuple[str, str, float]] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.repairs is None:
            self.repairs = []


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def _fold(token: str) -> str:
    raw = str(token or "").lower().strip(" .,!?;:")
    return _SAFE_FOLD.get(raw, raw)


def _best_safe_verb(token: str) -> Tuple[str, float]:
    folded = _fold(token)
    if folded in {c.lower() for c in SAFE_CANONICAL} or folded in _SAFE_FOLD.values():
        for canon in SAFE_CANONICAL:
            if canon.lower() == folded or _SAFE_FOLD.get(folded) == canon:
                return canon, 1.0
    best = ""
    best_score = 0.0
    for canon in SAFE_CANONICAL:
        dist = _levenshtein(folded, canon.lower())
        limit = 1 if len(folded) <= 5 else 2
        if dist <= limit and dist > 0:
            score = 1.0 - (dist / max(len(canon), 1))
            if score > best_score:
                best, best_score = canon, score
    return best, best_score


def _is_exact_destructive(token: str) -> bool:
    folded = str(token or "").lower().strip(" .,!?;:")
    return folded in {item.lower() for item in DESTRUCTIVE_CANONICAL}


def _near_destructive_typo(token: str) -> bool:
    folded = str(token or "").lower().strip(" .,!?;:")
    if _is_exact_destructive(folded):
        return False
    for canon in DESTRUCTIVE_CANONICAL:
        dist = _levenshtein(folded, canon.lower())
        if 0 < dist <= 1 and len(folded) >= 4:
            return True
    return False


def repair_action_verbs(text: str) -> AsrRepair:
    """Rewrite only safe action verbs on the normalized transcript.

    Raw user payload after wpisz/dopisz is left untouched.
    Near-destructive typos are NOT auto-applied.
    """
    heard = str(text or "")
    if not heard.strip():
        return AsrRepair(heard=heard, canonical=heard)

    repairs: List[Tuple[str, str, float]] = []
    needs_confirm = False
    chunks: List[str] = []
    last = 0
    for match in _CONNECTOR.finditer(heard):
        chunks.append(heard[last:match.start()])
        chunks.append(match.group(0))
        last = match.end()
    chunks.append(heard[last:])

    in_payload = False
    out_chunks: List[str] = []
    for chunk in chunks:
        if _CONNECTOR.fullmatch(chunk or ""):
            out_chunks.append(chunk)
            in_payload = False
            continue
        tokens = re.split(r"(\s+)", chunk)
        new_tokens: List[str] = []
        for tok in tokens:
            if not tok.strip() or re.fullmatch(r"\s+", tok):
                new_tokens.append(tok)
                continue
            bare = tok.strip(" .,!?;:")
            if in_payload:
                new_tokens.append(tok)
                continue
            if _near_destructive_typo(bare):
                needs_confirm = True
                new_tokens.append(tok)
                print(
                    f"[SEMANTIC ASR REPAIR] heard={bare!r} canonical=- "
                    f"confidence=0.0 reason=destructive_uncertain"
                )
                continue
            canon, score = _best_safe_verb(bare)
            if canon and score >= 0.72 and canon.lower() != bare.lower():
                if _DESTRUCTIVE_CONTEXT.search(chunk) and canon in ("zamknij", "wyłącz", "restart"):
                    needs_confirm = True
                    new_tokens.append(tok)
                    print(
                        f"[SEMANTIC ASR REPAIR] heard={bare!r} canonical=- "
                        f"confidence={score:.2f} reason=destructive_context"
                    )
                    continue
                suffix = tok[len(bare) :] if len(tok) >= len(bare) else ""
                new_tokens.append(canon + suffix)
                repairs.append((bare, canon, score))
                print(
                    f"[SEMANTIC ASR REPAIR] heard={bare!r} canonical={canon!r} "
                    f"confidence={score:.2f} reason=safe_verb_near_match"
                )
                if canon in ("wpisz", "dopisz"):
                    in_payload = True
                continue
            if _PAYLOAD_GUARD.match(bare):
                in_payload = True
            new_tokens.append(tok)
        out_chunks.append("".join(new_tokens))

    canonical = "".join(out_chunks)
    applied = bool(repairs) and not needs_confirm
    if not applied:
        canonical = heard
    return AsrRepair(
        heard=heard,
        canonical=canonical,
        confidence=max((item[2] for item in repairs), default=0.0),
        reason=(
            "destructive_uncertain"
            if needs_confirm
            else ("safe_verb_near_match" if repairs else "")
        ),
        applied=applied,
        needs_confirmation=needs_confirm,
        repairs=repairs,
    )


def semantic_verb_surface(text: str) -> str:
    """Normalized text with safe verb repairs applied. Payload casing is not a concern here."""
    result = repair_action_verbs(str(text or "").lower())
    if result.applied and result.canonical:
        return result.canonical
    return str(text or "").lower()
