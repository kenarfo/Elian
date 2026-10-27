# ==========================
# ELIAN — Utterance classes (2.0.0-rc1)
# ==========================
"""Discourse-level utterance typing. Not a tool router.

intent_family remains a binding signal for action domains; utterance class
captures how the user is speaking relative to ActiveGoal / conversation state.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class UtteranceClass(str, Enum):
    ACTION_REQUEST = "ACTION_REQUEST"
    INFORMATION_REQUEST = "INFORMATION_REQUEST"
    GOAL = "GOAL"
    CORRECTION = "CORRECTION"
    CONTINUATION = "CONTINUATION"
    CONFIRMATION = "CONFIRMATION"
    REJECTION = "REJECTION"
    WHY_FAILURE = "WHY_FAILURE"
    RETRY = "RETRY"
    CHANGE_PARAMETER = "CHANGE_PARAMETER"
    REFERENCE = "REFERENCE"
    CASUAL_TALK = "CASUAL_TALK"
    MEMORY_UPDATE = "MEMORY_UPDATE"
    FUTURE_INTENTION = "FUTURE_INTENTION"
    UNKNOWN = "UNKNOWN"


# Compact discourse detectors — class shapes, not phrase→tool maps.
_WHY_FAILURE = re.compile(
    r"(?:"
    r"^\s*(?:a\s+)?(?:czemu|dlaczego)(?:\s+nie)?(?:\s+(?:zrobi[lł]e[sś]|uda[lł]o|"
    r"dzia[lł]a|posz[lł]o))?\s*\??\s*$|"
    r"\b(?:czemu|dlaczego)\s+(?:tego\s+)?nie\s+(?:zrobi[lł]e[sś]|wykona[lł]e[sś]|"
    r"uda[lł]o|posz[lł]o)\b|"
    r"\bczemu\s+nie\s*\??\s*$|"
    r"\bdlaczego\s+nie\s*\??\s*$|"
    r"^\s*co\s+si[eę]\s+sta[lł]o\s*\??\s*$|"
    r"^\s*co\s+posz[lł]o\s+(?:nie\s+)?tak\s*\??\s*$|"
    r"^\s*co\s+nie\s+tak\s*\??\s*$"
    r")",
    re.I,
)

_RETRY = re.compile(
    r"(?:"
    r"\bspr[oó]buj(?:\s+inaczej|\s+ponownie|\s+jeszcze\s+raz)?\b|"
    r"\bpr[oó]buj\s+inaczej\b|"
    r"\bpon[oó]w\b|"
    r"\bpowt[oó]rz\b|"
    r"\bjeszcze\s+raz\b|"
    r"\bno\s+to\s+spr[oó]buj\b|"
    r"\binna\s+(?:droga|metoda|opcja)\b"
    r")",
    re.I,
)
_CHANGE_PARAM = re.compile(
    r"(?:"
    r"^\s*(?:troch[eę]\s+)?(?:wi[eę]cej|mniej|bardziej|mocniej|ciszej|"
    r"g[lł]o[sś]niej|ja[sś]niej|ciemniej)\s*[.!?…]*$|"
    r"^\s*(?:jeszcze\s+)?(?:troch[eę]\s+)?(?:wi[eę]cej|mniej)\s*[.!?…]*$|"
    r"^\s*jeszcze\s+troch[eę]\s*[.!?…]*$|"
    r"^\s*(?:podnie[sś]|obni[zż]|zwi[eę]ksz|zmniejsz)\s*(?:troch[eę])?\s*[.!?…]*$|"
    r"\bza\s+(?:ciemno|jasno|g[lł]o[sś]no|cicho|zimno|ciep[lł]o)\b|"
    r"\bjest\s+(?:tu\s+)?za\s+(?:ciemno|jasno|g[lł]o[sś]no|cicho|zimno|ciep[lł]o)\b|"
    r"\bnic\s+nie\s+widz[eę]\b|"
    r"\bs[lł]abo\s+wida[cć]\b"
    r")",
    re.I,
)
# Relative dampen / "too much" — discourse shape, not a tool map.
_RELATIVE_DAMPEN = re.compile(
    r"(?:"
    r"^\s*nie\s+a[zż]\s+tak\s*[.!?…]*$|"
    r"^\s*nie\s+tyle\s*[.!?…]*$|"
    r"^\s*nie\s+tak\s+(?:bardzo|mocno|du[zż]o)\s*[.!?…]*$|"
    r"^\s*(?:troch[eę]\s+)?mniej\s*[.!?…]*$|"
    r"^\s*za\s+mocno\s*[.!?…]*$|"
    r"^\s*za\s+du[zż]o\s*[.!?…]*$|"
    r"^\s*cofnij\s+(?:troch[eę])?\s*[.!?…]*$|"
    r"^\s*delikatniej\s*[.!?…]*$"
    r")",
    re.I,
)
# Repeat / "do it like earlier" — resume prior goal, not a new unclassified ask.
_REPEAT_PRIOR = re.compile(
    r"(?:"
    r"\bzr[oó]b\s+(?:tak\s+)?jak\s+(?:wcze[sś]niej|ostatnio|przedtem|wtedy)\b|"
    r"\btak\s+jak\s+(?:wcze[sś]niej|ostatnio|przedtem)\b|"
    r"\bpowt[oó]rz\s+(?:to\s+)?(?:co\s+)?(?:wcze[sś]niej|ostatnio)\b|"
    r"\bjak\s+(?:ostatnio|wcze[sś]niej)\b"
    r")",
    re.I,
)

# Vague "do something about that" while a goal is open.
_ACT_ON_OPEN_GOAL = re.compile(
    r"(?:"
    r"\bwe[zź]\s+(?:co[sś]\s+)?(?:z\s+tym\s+)?zr[oó]b\b|"
    r"^\s*(?:no\s+)?we[zź]\s+no\s*[.!?…]*$|"
    r"^\s*no\s+we[zź]\s*[.!?…]*$|"
    r"\bzr[oó]b\s+(?:co[sś]\s+)?(?:z\s+tym|z\s+t[aą])\b|"
    r"\bco[sś]\s+z\s+tym\s+(?:zr[oó]b|zrobi[cć])\b|"
    r"\bogarnij\s+(?:to|to\s+prosz[eę]|ten\s+temat)\b|"
    r"\bza[lł]atw\s+(?:to|to\s+prosz[eę])\b|"
    r"^\s*(?:no\s+)?to\s+(?:zr[oó]b|dawaj|jedziemy)\b"
    r")",
    re.I,
)
_MEDIA_STATUS = re.compile(
    r"(?:"
    r"^\s*co\s+(?:teraz\s+)?leci\s*\??\s*$|"
    r"^\s*co\s+gra\s*\??\s*$|"
    r"^\s*jaki\s+(?:utw[oó]r|numer)\s*\??\s*$|"
    r"^\s*co\s+jest\s+odtwarzane\s*\??\s*$"
    r")",
    re.I,
)
_VAGUE_MEDIA = re.compile(
    r"(?:"
    r"^\s*(?:odpal|pu[sś][cć]|odtw[oó]rz)\s+(?:mi\s+)?co[sś]\s*[.!?…]*$|"
    r"^\s*(?:odpal|pu[sś][cć])\s+(?:mi\s+)?co[sś]\s+\w+\s*[.!?…]*$"
    r")",
    re.I,
)
_COMPUTER_PREP = re.compile(
    r"(?:"
    r"\b(?:ogarnij|przygotuj|ustaw|przygotowanie)\b.{0,48}\b(?:komputer|pc|pecet)\b.{0,24}\b(?:grania|gaming|gry)\b|"
    r"\b(?:komputer|pc)\b.{0,24}\bdo\s+grania\b|"
    r"\btryb\s+(?:do\s+)?grania\b|"
    r"\bprocedur\w*\s+gaming\b"
    r")",
    re.I,
)
_GOALISH = re.compile(
    r"(?:"
    r"\bchcia[lł](?:bym|abym|aby[sś])\b|"
    r"\bchc[eę]\s+(?:[zż]eby|[zż]ebym)\b|"
    r"\btrzeba\b|"
    r"\bpowinno\s+by[cć]\b|"
    r"\bniech\s+(?:b[eę]dzie|si[eę])\b|"
    r"\bzrobi[lł]oby\s+mi\s+si[eę]\b|"
    r"\bprzyda[lł]oby\s+si[eę]\b"
    r")",
    re.I,
)
_CONFIRM = re.compile(
    r"(?:"
    r"^\s*(?:tak|no|dobra|no\s+dobra|okej|ok|jasne|dawaj|zr[oó]b|"
    r"no\s+to\s+zr[oó]b|to\s+zr[oó]b|zr[oó]b\s+to|"
    r"(?:ok|okej|prosz[eę])\s+zr[oó]b|"
    r"wykonaj|działaj|dzialaj)\s*[.!?…]*$|"
    r"^\s*(?:ej\s+)?jednak\s+zr[oó]b\s*[.!?…]*$|"
    r"^\s*no\s+to\s+(?:zr[oó]b|dawaj|jedziemy)\s*[.!?…]*$|"
    # English confirm / resume prior discussed action
    r"^\s*(?:okay|ok|alright|sure),?\s*(?:so\s+)?(?:do\s+it|go\s+ahead|please)\s*[.!?…]*$|"
    r"^\s*(?:do\s+it|go\s+ahead|please\s+do)\s*[.!?…]*$|"
    r"^\s*(?:yes|yeah|yep),?\s*(?:do\s+it|please)?\s*[.!?…]*$"
    r")",
    re.I,
)
_EXPECTANT_CONTINUE = re.compile(
    r"(?:"
    r"^\s*no\s+i\s*(?:co)?\s*\?*\s*$|"
    r"^\s*i\s*\?*\s*$|"
    r"^\s*no\s*\?*\s*$|"
    r"^\s*to\s+co\s*\?*\s*$|"
    r"^\s*i\s+co\s*(?:dalej)?\s*\?*\s*$|"
    r"^\s*co\s+dalej\s*\?*\s*$|"
    r"^\s*no\s+to\s+co\s*\?*\s*$|"
    r"^\s*(?:a\s+|no\s+|i\s+)?dalej\s*\?*\s*$|"
    r"^\s*i\s+dalej\s*\?*\s*$|"
    r"^\s*no\s+dalej\s*\?*\s*$|"
    r"^\s*a\s+dalej\s*\?*\s*$|"
    r"^\s*no\s+i\s+co\s*\?*\s*$|"
    r"^\s*kontynuuj(?:\s+prosz[eę])?\s*\?*\s*$|"
    r"^\s*wzn[oó]w\s*\?*\s*$|"
    r"^\s*wr[oó][cć]\s+do\s+tego\s*\?*\s*$"
    r")",
    re.I,
)

_REJECT = re.compile(
    r"(?:"
    r"^\s*(?:nie|niewa[zż]ne|anuluj(?:\s+to)?|odpu[sś][cć]|nie\s+teraz|"
    r"jednak\s+(?:nie|niewa[zż]ne)|zostaw)\s*[.!?…]*$|"
    r"^\s*(?:dobra|okej|ok|no)\s*,?\s*(?:niewa[zż]ne|anuluj(?:\s+to)?|odpu[sś][cć]|zostaw)\s*[.!?…]*$|"
    r"^\s*(?:to\s+)?niewa[zż]ne\s*[.!?…]*$"
    r")",
    re.I,
)
_CORRECTION = re.compile(
    r"(?:"
    r"^\s*nie\s*,\s*chodzi[lł]o\s+mi\s+o\b|"
    r"^\s*chodzi[lł]o\s+mi\s+o\b|"
    r"^\s*nie\s+ten\b|"
    r"^\s*nie\s+to\s*[.!?…]*$|"
    r"^\s*nie\s*,\s*nie\s+to\b|"
    r"^\s*nie\s+o\s+to\s+(?:mi\s+)?chodzi"
    r"(?:[lł]o)?\b|"
    r"\bmyli[lł]em\s+si[eę]\b|"
    r"\bpoprawka\b"
    r")",
    re.I,
)
# "nie, Discord" / "nie Spotify" — retarget, not a tool whitelist.
_NIE_RETARGET = re.compile(
    r"^\s*nie\s*,\s*(?P<target>\S.+?)\s*[.!?…]*$|"
    r"^\s*nie\s+(?P<target2>(?!o\s+to\b|ten\b|to\b|wiem\b|mog[eę]\b|"
    r"chc[eę]\b|teraz\b|dzi[sś]\b|jutro\b|niewa[zż]ne\b|anuluj\b)"
    r"[^\s,]{2,40})\s*[.!?…]*$",
    re.I,
)
_SOFT_CORRECTION = re.compile(
    r"(?:"
    r"^\s*jednak\s+(?!niewa[zż]ne\b|anuluj\b|odpu[sś][cć]\b|zostaw\b|nie\b)\w+|"
    r"^\s*nie\s+\w+,\s*\w+"
    r")",
    re.I,
)
_REFERENCE = re.compile(
    r"(?:"
    r"\b(?:tego|to|t[eę]|tamto|tamten|tamt[aą]|go|j[aą]|niego|ni[aą]|je)\b|"
    r"\b(?:pierwsz(?:ego|y|ą|a)|drug(?:iego|i|ą|a)|trzec(?:iego|i)|"
    r"ostatni(?:ego|ą)?|tam)\b|"
    r"\bo\s+tym\b|"
    r"\btak\s+samo\b|"
    r"\bjeszcze\s+(?:tego|to|raz)?\b"
    r")",
    re.I,
)
_UNRESOLVED_REF = re.compile(
    r"(?:"
    r"\bjeszcze\s+(?:tego\s+)?nie\s+(?:zrobi[lł]e[sś]|wykona[lł]e[sś]|uda[lł]o)\b|"
    r"\bnie\s+zrobi[lł]e[sś]\s+(?:tego|jeszcze)\b|"
    r"\bwci[aą][zż]\s+nie\b|"
    r"\bnadal\s+nie(?:\s+\w+)?\b|"
    r"\ba\s+to\s+zadanie\b|"
    r"\bto\s+co\s+mia[lł]e[sś]\s+zrobi[cć]\b"
    r")",
    re.I,
)
_BARE_CONTINUATION = re.compile(
    r"^(?:"
    r"jeszcze|dalej|kontynuuj|tak\s+samo|to\s+samo|"
    r"a\s+teraz\s+to|i\s+jeszcze\s+raz"
    r")[.!?…]*$",
    re.I,
)
_INFO = re.compile(
    r"(?:"
    r"\b(?:wyja[sś]nij|wyt[lł]umacz|powiedz\s+mi|opowiedz|"
    r"co\s+to|czym\s+jest|jak\s+dzia[lł]a|ile\s+jest|"
    r"sprawd[zź]|poszukaj|wyszukaj|szukaj|znajd[zź])\b|"
    r"^\s*(?:co|jak|kiedy|gdzie|czy|ile)\b"
    r")",
    re.I,
)
_ACTION = re.compile(
    r"\b(?:"
    r"otw[oó]rz|zamknij|w[lł][aą]cz|wy[lł][aą]cz|zapal|z?ga[sś]|wyga[sś]|"
    r"pu[sś][cć]|odtw[oó]rz|uruchom|wpisz|kliknij|ustaw|"
    r"zwi[eę]ksz|zmniejsz|wy[sś]lij|przypomnij|dodaj|"
    r"wyszukaj|poszukaj|szukaj|znajd[zź]"
    r")\b",
    re.I,
)
_CASUAL = re.compile(
    r"(?:"
    r"^\s*(?:hej|cze[sś][cć]|siema|jak\s+tam|co\s+tam|"
    r"jak\s+si[eę]\s+masz|co\s+s[lł]ycha[cć])\b|"
    r"\b[zż]art\b|\bsuchar\b|\bpogadajmy\b"
    r")",
    re.I,
)
_MEMORY = re.compile(
    r"\b(?:zapami[eę]taj|pami[eę]taj(?:\s+[zż]e)?|zapomnij|"
    r"nie\s+pami[eę]taj|od\s+teraz\s+wol[eę])\b",
    re.I,
)
_FUTURE = re.compile(
    r"(?:"
    r"\bjutro\b|\bp[oó][zź]niej\b|\bza\s+(?:godzin[eę]|tydzie[nń])\b|"
    r"\bmusz[eę]\s+(?:jutro|p[oó][zź]niej)\b|"
    r"\bprzypomnij\s+mi\b"
    r")",
    re.I,
)
_INCOMPLETE = re.compile(
    r"(?:"
    r"^\s*(?:no|to|a|i|ale|bo|jak|żeby|zeby|mo[zż]e)\s*$|"
    r"^\s*(?:otw[oó]rz|w[lł][aą]cz|pu[sś][cć]|ustaw|zapal)\s*$|"
    r"^\s*(?:tego|to|tam|go|j[aą])\s*$"
    r")",
    re.I,
)
_ORDINAL = re.compile(
    r"\b(?:pierwsz(?:ego|y|ą|a)|drug(?:iego|i|ą|a)|trzec(?:iego|i)|"
    r"ostatni(?:ego|ą)?)\b",
    re.I,
)
_MORE_ABOUT = re.compile(
    r"(?:"
    r"\b(?:wi[eę]cej|dalej|dok[lł]adniej)\s+o\s+(?:tym|tym\s+temacie|nim|niej)\b|"
    r"\bwyszukaj\s+(?:co[sś]\s+)?(?:wi[eę]cej\s+)?o\s+tym\b|"
    r"\bposzukaj\s+(?:co[sś]\s+)?(?:wi[eę]cej\s+)?o\s+(?:tym|nim)\b|"
    r"\bco[sś]\s+wi[eę]cej\s+o\s+tym\b"
    r")",
    re.I,
)


@dataclass
class UtteranceClassification:
    utterance_class: str = UtteranceClass.UNKNOWN.value
    confidence: float = 0.0
    needs_active_goal: bool = False
    needs_referent: bool = False
    incomplete: bool = False
    parameter_hint: str = ""  # brighter | darker | louder | quieter | more | less
    ordinal: str = ""  # first | second | last
    signals: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _param_hint(text: str) -> str:
    raw = str(text or "").lower()
    if re.search(
        r"za\s+ciemno|troch[eę]\s+tu\s+ciemn|jest\s+(?:tu\s+)?(?:za\s+)?ciemn|"
        r"(?:^|\b)ciemno\s+tu\b|\btu\s+ciemno\b|"
        r"nic\s+nie\s+widz|s[lł]abo\s+wida|\bmrok\b|"
        r"\btoo\s+dark\b|\ba\s+bit\s+dark\b|\bdark(?:er)?\s+(?:in\s+here|here)\b|"
        r"\bit'?s\s+(?:a\s+bit\s+|too\s+)?dark\b|\bcan'?t\s+see\b|\bhard\s+to\s+see\b",
        raw,
    ):
        return "brighter"
    if re.search(r"za\s+jasno|za\s+mocno\s+[sś]wieci", raw):
        return "darker"
    if re.search(r"\bciemn(?:iej|o)\b|przyciemn|mniej\s+[sś]wiat", raw):
        # "ciemniej" alone = make darker; state complaints already handled above.
        if re.search(r"\bciemn(?:o)\b", raw) and not re.search(r"\bciemn(?:iej)\b", raw):
            return "brighter"
        return "darker"
    if re.search(r"ja[sś]niej|wi[eę]cej\s+[sś]wiat|rozja[sś]ni", raw):
        return "brighter"
    if re.search(r"g[lł]o[sś]niej|za\s+cicho", raw):
        return "louder"
    if re.search(r"ciszej|za\s+g[lł]o[sś]no", raw):
        return "quieter"
    if re.search(r"^\s*(?:troch[eę]\s+)?wi[eę]cej", raw) or re.search(r"\bbardziej\b|\bmocniej\b", raw):
        return "more"
    if re.search(r"^\s*(?:troch[eę]\s+)?mniej", raw):
        return "less"
    return ""


def _ordinal(text: str) -> str:
    raw = str(text or "").lower()
    if re.search(r"pierwsz", raw):
        return "first"
    if re.search(r"drug", raw):
        return "second"
    if re.search(r"trzec", raw):
        return "third"
    if re.search(r"ostatni", raw):
        return "last"
    return ""


def classify_utterance(
    text: str,
    *,
    conversation: Optional[Dict[str, Any]] = None,
) -> UtteranceClassification:
    """Deterministic discourse class. Context only gates continuation/failure classes."""
    raw = str(text or "").strip()
    conv = dict(conversation or {})
    has_goal = bool(
        conv.get("has_active_goal")
        or conv.get("pending_executable")
        or conv.get("has_active_task")
        or conv.get("unresolved_goal")
    )
    has_options = bool(conv.get("presented_options") or conv.get("pending_decision"))
    has_param = bool(conv.get("active_parameter") or conv.get("last_parameter"))
    unresolved = bool(conv.get("unresolved_goal") or conv.get("pending_executable"))

    if not raw:
        return UtteranceClassification(
            utterance_class=UtteranceClass.UNKNOWN.value,
            confidence=0.3,
            incomplete=True,
            signals=["empty"],
        )

    incomplete = bool(_INCOMPLETE.match(raw))
    hint = _param_hint(raw)
    ordinal = _ordinal(raw)
    signals: List[str] = []

    # Single-token incomplete stems before broad info/action matchers.
    if incomplete and len(raw.split()) <= 2:
        return UtteranceClassification(
            utterance_class=UtteranceClass.CONTINUATION.value
            if has_goal
            else UtteranceClass.UNKNOWN.value,
            confidence=0.6 if has_goal else 0.5,
            incomplete=True,
            needs_active_goal=has_goal,
            signals=["incomplete"],
        )

    if _WHY_FAILURE.search(raw):
        signals.append("why_failure")
        return UtteranceClassification(
            utterance_class=UtteranceClass.WHY_FAILURE.value,
            confidence=0.94 if unresolved or has_goal else 0.82,
            needs_active_goal=True,
            signals=signals,
        )

    if _UNRESOLVED_REF.search(raw):
        signals.append("unresolved_ref")
        return UtteranceClassification(
            utterance_class=UtteranceClass.REFERENCE.value,
            confidence=0.93,
            needs_active_goal=True,
            needs_referent=True,
            signals=signals + ["unresolved_awareness"],
        )

    if _RETRY.search(raw) and (has_goal or unresolved or len(raw.split()) <= 5):
        signals.append("retry")
        return UtteranceClassification(
            utterance_class=UtteranceClass.RETRY.value,
            confidence=0.93 if has_goal or unresolved else 0.8,
            needs_active_goal=True,
            signals=signals,
        )

    if _BARE_CONTINUATION.match(raw) and has_goal:
        return UtteranceClassification(
            utterance_class=UtteranceClass.CONTINUATION.value,
            confidence=0.88,
            needs_active_goal=True,
            signals=["bare_continuation"],
        )

    # Dismissals ("jednak nieważne") must beat soft-correction ("jednak …").
    if _REJECT.match(raw):
        return UtteranceClassification(
            utterance_class=UtteranceClass.REJECTION.value,
            confidence=0.94,
            needs_active_goal=has_goal,
            signals=["reject"],
        )

    if _CORRECTION.search(raw) or _SOFT_CORRECTION.search(raw):
        return UtteranceClassification(
            utterance_class=UtteranceClass.CORRECTION.value,
            confidence=0.92 if _CORRECTION.search(raw) else 0.8,
            needs_referent=True,
            signals=["correction"],
        )

    # Context-gated "nie, X" / "nie X" retarget after an open goal.
    if (
        has_goal
        or conv.get("has_recent_goal")
        or conv.get("discussed_action")
    ) and _NIE_RETARGET.search(raw):
        return UtteranceClassification(
            utterance_class=UtteranceClass.CORRECTION.value,
            confidence=0.9,
            needs_active_goal=True,
            needs_referent=True,
            signals=["correction", "nie_retarget"],
        )

    if _MEDIA_STATUS.search(raw):
        return UtteranceClassification(
            utterance_class=UtteranceClass.INFORMATION_REQUEST.value,
            confidence=0.92,
            signals=["media_status"],
        )

    if _COMPUTER_PREP.search(raw):
        return UtteranceClassification(
            utterance_class=UtteranceClass.GOAL.value,
            confidence=0.9,
            signals=["computer_prep", "gaming"],
        )

    if _VAGUE_MEDIA.search(raw):
        return UtteranceClassification(
            utterance_class=UtteranceClass.ACTION_REQUEST.value,
            confidence=0.88,
            signals=["vague_media"],
        )

    if _REPEAT_PRIOR.search(raw) and (has_goal or conv.get("discussed_action") or conv.get("has_recent_goal")):
        return UtteranceClassification(
            utterance_class=UtteranceClass.CONTINUATION.value,
            confidence=0.9,
            needs_active_goal=True,
            needs_referent=True,
            signals=["repeat_prior"],
        )

    if has_goal and _ACT_ON_OPEN_GOAL.search(raw):
        return UtteranceClassification(
            utterance_class=UtteranceClass.CONFIRMATION.value,
            confidence=0.9,
            needs_active_goal=True,
            needs_referent=True,
            signals=["act_on_open_goal"],
        )

    if (has_goal or has_param or conv.get("has_recent_goal")) and _RELATIVE_DAMPEN.search(raw):
        damp_hint = hint or "less"
        param = str(conv.get("active_parameter") or conv.get("last_parameter") or "")
        last_dir = str(
            conv.get("parameter_direction")
            or conv.get("last_parameter_direction")
            or ""
        ).casefold()
        # "nie aż tak" / "za mocno" / "cofnij" = reverse last nudge, not always darker.
        reverse_cue = bool(
            re.search(
                r"nie\s+a[zż]\s+tak|nie\s+tyle|nie\s+tak\s+|"
                r"za\s+mocno|za\s+du[zż]o|cofnij|delikatniej",
                raw,
                re.I,
            )
        )
        _INVERT = {
            "brighter": "darker",
            "darker": "brighter",
            "louder": "quieter",
            "quieter": "louder",
            "more": "less",
            "less": "more",
        }
        if reverse_cue and last_dir in _INVERT:
            damp_hint = _INVERT[last_dir]
            if damp_hint in ("more", "less"):
                if param == "brightness" or last_dir in ("brighter", "darker"):
                    damp_hint = "brighter" if damp_hint == "more" else "darker"
                    param = param or "brightness"
                elif param == "volume" or last_dir in ("louder", "quieter"):
                    damp_hint = "louder" if damp_hint == "more" else "quieter"
                    param = param or "volume"
        elif damp_hint in ("less", "more", ""):
            # Map relative more/less onto the open parameter dimension.
            if param == "brightness":
                damp_hint = "darker" if damp_hint != "more" else "brighter"
            elif param == "volume":
                damp_hint = "quieter" if damp_hint != "more" else "louder"
            elif not damp_hint:
                damp_hint = "less"
        elif damp_hint not in ("darker", "quieter", "brighter", "louder", "less", "more"):
            damp_hint = "darker" if param == "brightness" else (
                "quieter" if param == "volume" else "less"
            )
        return UtteranceClassification(
            utterance_class=UtteranceClass.CHANGE_PARAMETER.value,
            confidence=0.92,
            needs_active_goal=True,
            needs_referent=True,
            parameter_hint=damp_hint,
            signals=["relative_dampen", "reverse_last"] if reverse_cue else ["relative_dampen"],
        )

    if (has_goal or conv.get("discussed_action") or has_options) and _EXPECTANT_CONTINUE.match(raw):
        return UtteranceClassification(
            utterance_class=UtteranceClass.CONTINUATION.value,
            confidence=0.82,
            needs_active_goal=True,
            signals=["expectant_continue"],
        )
    if _CONFIRM.match(raw) and (has_goal or conv.get("discussed_action") or has_options):
        return UtteranceClassification(
            utterance_class=UtteranceClass.CONFIRMATION.value,
            confidence=0.93,
            needs_active_goal=True,
            signals=["confirm_execute"],
        )
    if _CONFIRM.match(raw):
        # Bare "zrób" / "no to zrób" without goal — still mark confirmation-shaped.
        return UtteranceClassification(
            utterance_class=UtteranceClass.CONFIRMATION.value,
            confidence=0.78,
            needs_active_goal=True,
            signals=["confirm_shaped"],
        )

    if _CHANGE_PARAM.search(raw) or hint:
        cls = UtteranceClass.CHANGE_PARAMETER
        conf = 0.9
        if hint in ("brighter", "darker") and re.search(
            r"za\s+ciemno|za\s+jasno|troch[eę]\s+tu\s+ciemn|"
            r"jest\s+(?:tu\s+)?(?:za\s+)?ciemn|"
            r"(?:^|\b)ciemno\s+tu\b|\btu\s+ciemno\b|"
            r"(?:^|\b)jasno\s+tu\b|\btu\s+jasno\b|"
            r"nic\s+nie\s+widz|s[lł]abo\s+wida|\bmrok\b",
            raw,
            re.I,
        ):
            cls = UtteranceClass.GOAL
            conf = 0.92
            signals.append("state_goal")
        elif has_param or has_goal:
            signals.append("param_followup")
            conf = 0.93
        else:
            signals.append("param_bare")
            conf = 0.84
        return UtteranceClassification(
            utterance_class=cls.value,
            confidence=conf,
            needs_active_goal=cls == UtteranceClass.CHANGE_PARAMETER,
            needs_referent=cls == UtteranceClass.CHANGE_PARAMETER,
            parameter_hint=hint,
            signals=signals,
        )

    if (_ORDINAL.search(raw) or (has_options and _REFERENCE.search(raw))) and (
        has_options or ordinal or len(raw.split()) <= 4
    ):
        return UtteranceClassification(
            utterance_class=UtteranceClass.REFERENCE.value,
            confidence=0.91 if has_options else 0.8,
            needs_referent=True,
            needs_active_goal=True,
            ordinal=ordinal,
            signals=["ordinal_or_option"],
        )

    if _MORE_ABOUT.search(raw):
        return UtteranceClassification(
            utterance_class=UtteranceClass.INFORMATION_REQUEST.value,
            confidence=0.9,
            needs_referent=True,
            signals=["more_about_referent"],
        )

    if _MEMORY.search(raw):
        return UtteranceClassification(
            utterance_class=UtteranceClass.MEMORY_UPDATE.value,
            confidence=0.9,
            signals=["memory"],
        )

    if _FUTURE.search(raw) and not _ACTION.search(raw):
        return UtteranceClassification(
            utterance_class=UtteranceClass.FUTURE_INTENTION.value,
            confidence=0.86,
            signals=["future"],
        )

    if _CASUAL.search(raw) and not _ACTION.search(raw):
        return UtteranceClassification(
            utterance_class=UtteranceClass.CASUAL_TALK.value,
            confidence=0.92,
            signals=["casual"],
        )

    if _GOALISH.search(raw) and not _ACTION.search(raw):
        return UtteranceClassification(
            utterance_class=UtteranceClass.GOAL.value,
            confidence=0.86,
            signals=["goalish"],
        )

    if _INFO.search(raw) and not re.search(
        r"\b(?:otw[oó]rz|w[lł][aą]cz|zapal|pu[sś][cć])\b", raw, re.I
    ):
        # Search verbs count as action+info; prefer ACTION when explicit toolish verb.
        if re.search(r"\b(?:wyszukaj|poszukaj|szukaj|znajd[zź]|sprawd[zź])\b", raw, re.I):
            # "nie szukaj … zgadnij żartowo" is talk/humor, not web search.
            if re.search(
                r"\bnie\s+(?:wyszukuj|poszukuj|szukaj|szukać)\b|"
                r"\bzgadnij\b|"
                r"\b[zż]artowo\b",
                raw,
                re.I,
            ):
                return UtteranceClassification(
                    utterance_class=UtteranceClass.CASUAL_TALK.value,
                    confidence=0.9,
                    signals=["humor_not_search"],
                )
            return UtteranceClassification(
                utterance_class=UtteranceClass.ACTION_REQUEST.value,
                confidence=0.9,
                needs_referent=bool(_REFERENCE.search(raw)),
                signals=["search_action"],
            )
        return UtteranceClassification(
            utterance_class=UtteranceClass.INFORMATION_REQUEST.value,
            confidence=0.88,
            needs_referent=bool(_REFERENCE.search(raw)),
            signals=["info"],
        )

    if _ACTION.search(raw):
        return UtteranceClassification(
            utterance_class=UtteranceClass.ACTION_REQUEST.value,
            confidence=0.9,
            needs_referent=bool(_REFERENCE.search(raw)),
            incomplete=incomplete,
            signals=["action_verb"],
        )

    if _REFERENCE.search(raw) and (has_goal or has_options or len(raw.split()) <= 5):
        return UtteranceClassification(
            utterance_class=UtteranceClass.CONTINUATION.value
            if has_goal
            else UtteranceClass.REFERENCE.value,
            confidence=0.82 if has_goal else 0.72,
            needs_referent=True,
            needs_active_goal=has_goal,
            ordinal=ordinal,
            incomplete=incomplete,
            signals=["referential"],
        )

    if incomplete:
        return UtteranceClassification(
            utterance_class=UtteranceClass.CONTINUATION.value
            if has_goal
            else UtteranceClass.UNKNOWN.value,
            confidence=0.55,
            incomplete=True,
            needs_active_goal=has_goal,
            signals=["incomplete"],
        )

    return UtteranceClassification(
        utterance_class=UtteranceClass.UNKNOWN.value,
        confidence=0.45,
        signals=["fallback"],
    )
