# ==========================
# ELIAN — semantic META / ACK (Phase 11.4)
# ==========================
"""Comprehension ACK detection — fillers + understanding, not exact string lists."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple


META_ACK = "META_ACK"
META_COMPOUND = "META_COMPOUND"
META_NONE = "NONE"
META_REPAIR = "REPAIR"  # negated comprehension


# Negated understanding — never ACK (Phase 11.1 polarity).
_NEGATED = re.compile(
    r"(?:"
    r"\bnie\s+rozumiem\b|"
    r"\bwcale\s+nie\s+rozumiem\b|"
    r"\bdalej\s+nie\s+rozumiem\b|"
    r"\bnadal\s+nie\s+rozumiem\b|"
    r"\bjeszcze\s+nie\s+rozumiem\b|"
    r"\bwci[aą][zż]\s+nie\s+rozumiem\b|"
    r"\bnie\s+kapuj[eę]\b|"
    r"\bwci[aą][zż]\s+nie\s+wiem\b|"
    r"\bjeszcze\s+nie\s+wiem\b|"
    r"\bnadal\s+nie\s+wiem\b|"
    r"\bnie\s+wiem\s+(?:o\s+co|o\s+czym|co\s+masz)\b"
    r")",
    re.I,
)

_FILLER = frozenset(
    {
        "no", "okej", "ok", "okay", "dobra", "dobrze", "spoko", "jasne",
        "aha", "tak", "hmm", "mhm", "to", "już", "juz", "teraz", "wszystko",
        "nope", "yhm", "uhm", "ee", "eee",
    }
)

_UNDERSTAND = frozenset(
    {
        "wiem", "rozumiem", "kumam", "czaje", "czaję", "czaje",
        "załapałem", "zalapalem", "załapalam", "zalapalam",
        "załapałem", "załapałem",
        "skapowałem", "skapowalem", "skapowałam", "skapowalam",
        "jasne",  # also filler; counts as understand when alone after strip
    }
)

_UNDERSTAND_PHRASE = re.compile(
    r"(?:"
    r"teraz\s+wiem|ju[zż]\s+wiem|to\s+ju[zż]\s+wiem|"
    r"teraz\s+rozumiem|ju[zż]\s+rozumiem|"
    r"wszystko\s+jasne|teraz\s+wszystko\s+jasne|"
    r"to\s+ma\s+sens|teraz\s+ma\s+to\s+sens|"
    r"ju[zż]\s+kumam|teraz\s+kumam|"
    r"dobra[,\s]+czaj[eę]|czaj[eę]|"
    r"załapa[łl][ae]m|zalapa[łl][ae]m|"
    r"wiem[,\s]+wiem"
    r")",
    re.I,
)

# Separators introducing a new request after meta filler.
_SEP = re.compile(
    r"(?:"
    r"[.!…]+\s*|"
    r",\s*|"
    r"\s+"
    r")"
    r"(?:"
    r"ale|"
    r"tylko|"
    r"natomiast|"
    r"w\s+takim\s+razie|"
    r"a"  # "… . A kto…" / "…, a kto…"
    r")\s+",
    re.I,
)

_NEW_REQUEST = re.compile(
    r"(?:"
    r"[?？]|"
    r"^\s*(?:kto|co|ile|jaki|jaka|jakie|gdzie|kiedy|czy|dlaczego|czemu)\b|"
    r"\b(?:powiedz|sprawd[zź]|szukaj|znajd[zź]|otw[oó]rz|pu[sś][cć])\b|"
    r"\b(?:prezydent|premier|kanclerz|kr[oó]l|ceo|cena|kosztuje)\w*\b"
    r")",
    re.I,
)


@dataclass
class MetaAnalysis:
    kind: str = META_NONE
    negated: bool = False
    meta_prefix: str = ""
    remainder: str = ""
    suppress_previous_answer_replay: bool = False

    @property
    def is_meta_ack(self) -> bool:
        return self.kind == META_ACK

    @property
    def is_compound(self) -> bool:
        return self.kind == META_COMPOUND

    @property
    def is_repair(self) -> bool:
        return self.kind == META_REPAIR


def is_negated_comprehension(text: str) -> bool:
    return bool(_NEGATED.search(str(text or "")))


def _normalize_tokens(text: str) -> list[str]:
    lowered = str(text or "").lower().replace("…", " ")
    lowered = re.sub(r"[^\wąćęłńóśźż\s]+", " ", lowered, flags=re.I)
    return [t for t in lowered.split() if t]


def _tokens_are_pure_meta(tokens: list[str]) -> bool:
    if not tokens:
        return False
    has_understand = False
    for tok in tokens:
        if tok in _FILLER:
            continue
        if tok in _UNDERSTAND:
            has_understand = True
            continue
        # Unknown content token → not pure meta.
        return False
    # Require at least one understand signal (not just "okej okej").
    if has_understand:
        return True
    # "jasne" alone / "okej jasne" — jasne is in both sets; treat filler-only
    # short acks like "okej" as CONVERSATION_CONTROL elsewhere, not META_ACK.
    joined = " ".join(tokens)
    return bool(_UNDERSTAND_PHRASE.search(joined))


def is_pure_meta_ack(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw or is_negated_comprehension(raw):
        return False
    if _UNDERSTAND_PHRASE.fullmatch(raw.strip(" !.…")):
        return True
    # Strip leading filler phrases then check remainder phrase.
    stripped = re.sub(
        r"^(?:(?:no|okej|ok|okay|dobra|dobrze|spoko|jasne|aha|tak|to)"
        r"[,\s!.]*)+",
        "",
        raw.lower().strip(),
        flags=re.I,
    ).strip(" !.…")
    if _UNDERSTAND_PHRASE.fullmatch(stripped) or _UNDERSTAND_PHRASE.search(
        f"^{stripped}$"
    ):
        # fullmatch on search result — use fullmatch on stripped
        pass
    if _UNDERSTAND_PHRASE.fullmatch(stripped):
        return True
    tokens = _normalize_tokens(raw)
    return _tokens_are_pure_meta(tokens)


def _looks_like_new_request(text: str) -> bool:
    rest = str(text or "").strip()
    if not rest:
        return False
    if _NEW_REQUEST.search(rest):
        return True
    # Multi-word content that is not pure meta.
    tokens = _normalize_tokens(rest)
    if len(tokens) >= 3 and not _tokens_are_pure_meta(tokens):
        return True
    return False


def _looks_like_meta_prefix(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if is_pure_meta_ack(raw):
        return True
    # Allow slightly loose prefixes: "okej, wiem, wiem" / "dobra już"
    tokens = _normalize_tokens(raw)
    if not tokens:
        return False
    # Majority filler+understand, at least one understand.
    content = [t for t in tokens if t not in _FILLER]
    if not content:
        return False
    return all(t in _UNDERSTAND or _UNDERSTAND_PHRASE.search(t) for t in content)


def split_meta_compound(text: str) -> Tuple[str, str]:
    """
    Returns (kind, remainder):
      meta | compound | none
    Remainder is empty for pure meta; for compound it is the new request only.
    """
    raw = str(text or "").strip()
    if not raw:
        return "none", ""
    if is_negated_comprehension(raw):
        return "none", raw

    # Prefer longest valid split: walk separators left-to-right.
    for match in _SEP.finditer(raw):
        prefix = raw[: match.start()].strip(" .,!")
        rest = raw[match.end() :].strip(" .,!")
        if not prefix or not rest:
            continue
        # Avoid splitting inside "aha" etc. — separator must follow meta-ish prefix.
        if _looks_like_meta_prefix(prefix) and _looks_like_new_request(rest):
            return "compound", rest

    if is_pure_meta_ack(raw):
        return "meta", ""
    return "none", raw


def analyze_meta(text: str) -> MetaAnalysis:
    raw = str(text or "").strip()
    if not raw:
        return MetaAnalysis()
    if is_negated_comprehension(raw):
        return MetaAnalysis(kind=META_REPAIR, negated=True, remainder=raw)

    kind, rest = split_meta_compound(raw)
    if kind == "meta":
        return MetaAnalysis(
            kind=META_ACK,
            meta_prefix=raw,
            suppress_previous_answer_replay=True,
        )
    if kind == "compound":
        # Recover prefix for diagnostics.
        prefix = raw
        for match in _SEP.finditer(raw):
            candidate = raw[: match.start()].strip(" .,!")
            tail = raw[match.end() :].strip(" .,!")
            if tail.lower().rstrip("?.!") == rest.lower().rstrip("?.!"):
                prefix = candidate
                break
        return MetaAnalysis(
            kind=META_COMPOUND,
            meta_prefix=prefix,
            remainder=rest,
            suppress_previous_answer_replay=True,
        )
    return MetaAnalysis(kind=META_NONE, remainder=raw)


def is_meta_utterance(text: str) -> bool:
    return analyze_meta(text).is_meta_ack


def meta_ack_phrase() -> str:
    import random

    return random.choice(("Okej.", "Jasne.", "Super."))


def strip_meta_noise_from_query(text: str) -> str:
    """Remove conversational meta / fillers from a search query string."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    kind, rest = split_meta_compound(raw)
    if kind == "compound" and rest:
        raw = rest
    elif kind == "meta":
        return ""
    # Only strip leading conversational / understand noise — keep "teraz" inside facts.
    raw = re.sub(
        r"^(?:(?:okej|ok|okay|no|dobra|dobrze|spoko|jasne|aha|tak|wiem|rozumiem|"
        r"kumam|ale|to|inaczej)[,\s!.]*)+",
        "",
        raw,
        flags=re.I,
    ).strip(" .,!?")
    # Drop duplicated understand tokens if still leading.
    raw = re.sub(
        r"^(?:wiem|rozumiem|kumam)(?:\s*,\s*(?:wiem|rozumiem|kumam))*[.\s]*",
        "",
        raw,
        flags=re.I,
    ).strip(" .,!?")
    return raw or str(text or "").strip()
