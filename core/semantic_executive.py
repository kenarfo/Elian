# ==========================
# ELIAN — AI Semantic Executive (C.5.4)
# ==========================
"""Understand first, act second. One Elian — not a second assistant.

Pass 1 classifies the utterance alone. Context is retrieved only when the
decision needs a referent or continuation. Context resolves; it does not invent
intent. Side effects never leave this layer.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from Diagnostics import log_turn

from Config import get_config_value


# Confidence contract (documented in BEHAVIOR_FIX_ACCEPTANCE.md).
CONFIDENCE_STRONG = 0.90
CONFIDENCE_USABLE = 0.75
# < CONFIDENCE_USABLE: no side effect without additional deterministic evidence.

DECISION_MODES = (
    "TALK",
    "ANSWER",
    "OBSERVE",
    "ACT",
    "PLAN",
    "ASK",
    "WAIT",
    "RESUME",
    "CANCEL",
    "SIMULATE",
    "PROACTIVE_DIALOGUE",
)
ALLOWED_DOMAINS = (
    "APP",
    "MEDIA",
    "SYSTEM",
    "SMART_HOME",
    "COMPUTER",
    "VISION",
    "WEB",
    "READ_ONLY_SCREEN",
)

SEMANTIC_PROVIDER = "ollama"
SEMANTIC_REUSES_PLANNER_CLIENT = True


class DecisionMode(str, Enum):
    TALK = "TALK"
    ANSWER = "ANSWER"
    OBSERVE = "OBSERVE"
    ACT = "ACT"
    PLAN = "PLAN"
    ASK = "ASK"
    WAIT = "WAIT"
    RESUME = "RESUME"
    CANCEL = "CANCEL"
    SIMULATE = "SIMULATE"
    PROACTIVE_DIALOGUE = "PROACTIVE_DIALOGUE"


@dataclass
class DecisionEnvelope:
    mode: str = DecisionMode.TALK.value
    intent: str = "conversation"
    user_goal: str = ""
    confidence: float = 0.0
    needs_tools: bool = False
    allowed_domains: List[str] = field(default_factory=list)
    requires_fresh_observation: bool = False
    referents: List[str] = field(default_factory=list)
    continuation: bool = False
    needs_clarification: bool = False
    missing_information: List[str] = field(default_factory=list)
    response_style: str = ""
    safety_notes: List[str] = field(default_factory=list)
    context_requested: bool = False
    draft_reply: str = ""
    source: str = "deterministic"
    extra_llm_call: bool = False
    payload: str = ""
    conversation_topic: str = ""
    task_reference: str = ""
    decision_reference: str = ""
    user_is_responding_to_elian: bool = False
    followup_kind: str = ""
    recommended_next_action: str = ""
    negated: bool = False
    hypothetical: bool = False
    quoted: bool = False
    correction: bool = False
    wait_requested: bool = False
    cancel_requested: bool = False
    # Discourse layer (ActiveGoal) — intent_family remains a signal, not full meaning.
    utterance_class: str = ""
    goal_objective: str = ""
    goal_status: str = ""
    parameter_hint: str = ""
    unresolved_awareness: bool = False

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["confidence"] = round(float(self.confidence), 3)
        return data


def envelope_from_dict(raw: Dict[str, Any] | None) -> Optional[DecisionEnvelope]:
    if not isinstance(raw, dict):
        return None
    try:
        return validate_envelope(raw)
    except Exception:
        return None


_HUMOR = re.compile(
    r"(?:"
    r"\b(?:[zż]art|suchar|dowcip)\w*\b|"
    r"roz[sś]miesz|"
            r"co[sś]\s+[sś]mieszn|"
            r"co[sś]\s+zabawn|"
    r"co[sś]\s+g[lł]upiego|"
    r"rzu[cć]\s+(?:jakim[sś]\s+)?[zż]art|"
    r"daj\s+(?:jakiego[sś]\s+)?suchar|"
    r"masz\s+(?:jaki[sś]\s+)?(?:[zż]art|suchar|dowcip)"
    r")",
    re.I,
)
_SMALL_TALK = re.compile(
    r"^(?:"
    r"jak\s+si[eę]\s+masz|"
    r"jak\s+tam|"
    r"co\s+s[lł]ycha[cć]|"
    r"co\s+tam|"
    r"co\s+u\s+ciebie|"
    r"jak\s+leci|"
    r"cze[sś][cć](?:\s+elian)?|"
    r"hej(?:\s+elian)?|"
    r"siema|"
    r"dzie[nń]\s+dobry|"
    r"pogadajmy|"
    r"co\s+potrafisz|"
    r"no\s+cze[sś][cć]"
    r")[.!?…]*$",
    re.I,
)
_WAIT = re.compile(
    r"^(?:"
    r"poczekaj|czekaj|moment|"
    r"daj\s+mi\s+chwil[eę]|"
    r"daj\s+chwil[eę]"
    r")[.!?…]*$",
    re.I,
)
_CANCEL = re.compile(
    r"^(?:"
    r"niewa[zż]ne|anuluj(?:\s+to)?|jednak\s+niewa[zż]ne|"
    r"(?:dobra|okej|ok|no)\s*,?\s*(?:niewa[zż]ne|anuluj|odpu[sś][cć])|"
    r"to\s+niewa[zż]ne"
    r")[.!?…]*$",
    re.I,
)
# Mid-goal abort family (clarification STOP_PHRASES) — cancel, never smalltalk.
_STOP = re.compile(
    r"^(?:stop|st[oó]j|cisza|wystarczy)[.!?…]*$",
    re.I,
)
_CORRECTION = re.compile(
    r"(?:"
    r"^\s*nie\s*,\s*chodzi[lł]o\s+mi\s+o\b|"
    r"^\s*nie\s+ten\b|"
    r"^\s*nie\s*,\s*ten\s+po\s+prawej|"
    r"^\s*nie\s+wpisuj\s+tego|"
    r"^\s*nie\s*,\s*nie\s+to\b|"
    r"^\s*chodzi[lł]o\s+mi\s+o\b"
    r")",
    re.I,
)
_VISUAL_CHOICE = re.compile(
    r"(?:ten\s+po\s+prawej|ten\s+po\s+lewej|nie\s+ten|drugi|g[oó]rny|dolny)",
    re.I,
)
_FOLLOWUP_TALK = re.compile(
    r"^(?:"
    r"pro[sś]ciej|"
    r"podaj\s+przyk[lł]ad|"
    r"a\s+czemu\??|"
    r"a\s+dlaczego\??|"
    r"dlaczego\??|"
    r"czemu\??|"
    r"jeszcze\s+raz|"
    r"powiedz\s+to\s+inaczej|"
    r"nie\s+rozumiem\s+tego|"
    r"teraz\s+rozumiem|"
    r"opowiedz\s+o\s+czym[sś]\s+innym|"
    r"a\s+mo[zż]esz\s+pro[sś]ciej"
    r")[.!?…]*$",
    re.I,
)
_KNOWLEDGE_Q = re.compile(
    r"(?:"
    r"\b(?:wyja[sś]nij|wyt[lł]umacz|wytlumacz)\b|"
    r"\bjak\s+dzia[lł]a\b|"
    r"\bco\s+s[aą]dzisz\b|"
    r"\bco\s+o\s+tym\s+my[sś]lisz\b|"
    r"\bczym\s+jest\b|"
    r"\bco\s+to\s+jest\b|"
    r"\bco\s+oznacza\b|"
    r"\bczemu\s+tak\s+jest\b|"
    r"\ba\s+co\s+je[sś]li\b|"
    r"\bmam\s+pomys[lł]\b|"
    r"\bwymy[sś]l\b|"
    r"\bile\s+(?:to|jest|wynosi)\b|"
    r"\bjak\s+powiedzie[cć]\b|"
    r"\bda\s+si[eę]\s+to\s+zrobi[cć]\s+lepiej"
    r")",
    re.I,
)
_DISCUSSION = re.compile(
    r"(?:"
    r"^\s*jak\s+(?:otworzy[cć]|w[lł][aą]czy[cć]|zapali[cć]|zgasi[cć]|uruchomi[cć]|zamkn[aą][cć])\b|"
    r"\bczy\s+umiesz\b|"
    r"\bczy\s+potrafisz\b|"
    r"\bczy\s+mo[zż]esz\s+(?:powiedzie[cć]|wyja[sś]ni[cć])\b|"
    r"\bco\s+si[eę]\s+stanie\s*,?\s*(?:jak|je[sś]li)\b|"
    r"\bco\s+by\s+si[eę]\s+sta[lł]o\b|"
    r"\bco\s+je[sś]li\b|"
    r"\bje[sś]li\s+w[lł][aą]czysz\b|"
    r"\bco\s+znaczy\b|"
    r"\bco\s+oznacza\s+(?:komenda|polecenie|komend[eę]|instrukcj)\b|"
    r"\bgdybym\b|"
    r"\bgdyby\s+m\b|"
    r"\bhipotetycznie\b|"
    r"\bpowiedzia[lł]em\s+(?:mu|jej|kole)\w*\b|"
    r"\bpowiedzia[lł]em\s*:|"
    r"\bnapisa[lł]em\b|"
    r"\bnie\s+otwieraj\b|"
    r"\bnie\s+w[lł][aą]czaj\b|"
    r"\bnie\s+zapalaj\b|"
    r"\bnie\s+zgaszaj\b|"
    r"\bnie\s+zwi[eę]kszaj\b|"
    r"\bnie\s+zamykaj\b|"
    r"\bnie\s+wy[lł][aą]czaj\b|"
    r"\bnie\s+pu[sś][cć]\b|"
    r"\bnie\s+wykonuj\b|"
    r"\bnie\s+uruchamiaj\b|"
    r"\bnie\s+traktuj\b|"
    r"\bnie\s+r[oó]b\s+tego\b|"
    r"\bnie\s+r[oó]b\s*:|"
    r"\bto\s+pytanie\b|"
    r"\bnie\s+komenda\b|"
    r"\btylko\s+rozmowa\b|"
    r"\btylko\s+dyskusja\b|"
    r"\bjest poleceniem\b|"
    r"\bzdanie\b.+\bpolecen|"
    r"\bzostaw\s+\w+\s+w\s+spokoju|"
    r"\bna\s+stronie\s+(?:pisze|jest)\b|"
    r"\bw\s+(?:artykule|mailu|filmie|tutorialu|notatce)\b|"
    r"\bwynik\s+narz[eę]dzia\b|"
    r"\btool\s+log\b|"
    r"\boutput\s*:|"
    r"\bcytat\s+z\b|"
    r"\bpami[eę][cć]\s+kandydat\b|"
    r"\bzapami[eę]taj\s+instrukcj|"
    r"\bom[oó]w\s+komend|"
    r"\bwyja[sś]nij\s+co\s+robi\s+komend|"
    r"\bzacytuj\b|"
    r"\bto\s+tylko\s+(?:przyk[lł]ad|[zż]art)\b"
    r")",
    re.I,
)
_STATUS_Q = re.compile(
    r"(?:"
    r"czy\s+(?:to\s+)?(?:[sś]wiat[lł]o|lampa).{0,48}(?:w[lł][aą]czon|wy[lł][aą]czon|[sś]wieci)|"
    r"jaki\s+jest\s+(?:teraz\s+)?stan|"
    r"na\s+ile\s+(?:procent|jest)|"
    r"stan\s+(?:lamp|[sś]wiat)"
    r")",
    re.I,
)
_QUESTION_SHAPE = re.compile(
    r"(?:\?|^\s*(?:czy|jak|dlaczego|czemu|co\s+znaczy|co\s+oznacza)\b)",
    re.I,
)
_VOLUME_BARE = re.compile(
    r"^(?:(?:troch[eę]\s+)?(?:g[lł]o[sś]niej|ciszej|t[iy]szej)|"
    r"zwi[eę]ksz\s+g[lł]o[sś]no[sś][cć]|"
    r"zmniejsz\s+g[lł]o[sś]no[sś][cć]|podg[lł]o[sś]nij)[.!?…]*$",
    re.I,
)
_OPEN_APP = re.compile(
    r"\b(?:otw[oó]rz|uruchom|odpal)\s+(?!mi\b)",
    re.I,
)
_PLAY_MEDIA = re.compile(
    r"\b(?:pu[sś][cć]\w*|odtw[oó]rz\w*|pos[lł]uch\w*|graj(?:my|cie)?)\b",
    re.I,
)
_TYPE_CONT = re.compile(
    r"\b(?:wpisz|dopisz)\s+(?:tam|to|tu)\b",
    re.I,
)
_CLOSE_REF = re.compile(
    r"\b(?:zamknij|wy[lł][aą]cz)\s+(?:go|j[aą]|to|t[eę]|tamto)\b",
    re.I,
)
_MASC_REF = re.compile(r"\b(?:go|niego|ten|tamten)\b", re.I)
_THERE_REF = re.compile(r"\b(?:tam|tu|to)\b", re.I)
_COMPOUND_AND = re.compile(
    r"\s+(?:i|oraz|potem|a\s+potem|a\s+p[oó][zź]niej|nast[eę]pnie)\s+",
    re.I,
)
_ACTION_VERB = re.compile(
    r"\b(?:"
    r"otw[oó]rz|zamknij|zwi[eę]ksz|zmniejsz|g[lł]o[sś]niej|ciszej|"
    r"w[lł][aą]cz|wy[lł][aą]cz|zapal|z?ga[sś]|wyga[sś]|przyciemnij|rozja[sś]nij|"
    r"pu[sś][cć]|wpisz|kliknij|uruchom"
    r")\b",
    re.I,
)


def looks_creative_humor(text: str) -> bool:
    return bool(_HUMOR.search(str(text or "")))


_SAY_PREFIX = re.compile(
    r"^\s*(?:"
    r"chc[eę]\s+[zż]eby[sś]\s+powiedzia[lł]\s+|"
    r"mo[zż]esz\s+powiedzie[cć]\s+|"
    r"powiedzie[cć]\s+|"
    r"powiesz\s+|"
    r"powiedz\s+"
    r")",
    re.I,
)
_SAY_FILLERS = re.compile(
    r"^(?:mi|prosz[eę]|tylko|teraz|g[lł]o[sś]no|cicho)\s+",
    re.I,
)
_SAY_TOPICISH = re.compile(
    r"^(?:"
    r"to\s+inaczej|"
    r"p[oó][zź]niej|"
    r"ciekawostk|"
    r"zagadk|"
    r"histori|"
    r"haiku|"
    r"co[sś]\s+(?:m[aą]drego|[sś]mieszn|[zż]abawn)|"
    r"o\s+\w+|"
    r"(?:co|jak|czy|dlaczego|czemu|ile)\b"
    r")",
    re.I,
)
_SAY_NEGATED = re.compile(
    r"^\s*nie\s+(?:m[oó]w|powiedz|powiedzie[cć])\b",
    re.I,
)


def format_say_speech(payload: str) -> str:
    text = str(payload or "").strip().strip(" \"'„”«»")
    if not text:
        return ""
    if text[0].isalpha():
        text = text[0].upper() + text[1:]
    if text[-1] not in ".!?…":
        text += "."
    return text


def extract_say_payload(text: str) -> str:
    """Literal speech request: 'powiedz X' → X. Empty if not a say-text utterance."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if looks_hypothetical(raw) or looks_quoted_command(raw) or looks_creative_humor(raw):
        return ""
    if _SAY_NEGATED.search(raw):
        return ""
    if re.search(r"\bjak\s+powiedzie[cć]\b", raw, re.I):
        return ""
    if _ACTION_VERB.search(raw):
        return ""
    if re.search(r"\b(?:wpisz|dopisz)\b", raw, re.I):
        return ""
    if not _SAY_PREFIX.search(raw):
        return ""
    rest = _SAY_PREFIX.sub("", raw, count=1).strip()
    for _ in range(4):
        nxt = _SAY_FILLERS.sub("", rest)
        if nxt == rest:
            break
        rest = nxt.strip()
    rest = rest.strip(" .!?…\"'„”«»")
    if not rest or _SAY_TOPICISH.search(rest):
        return ""
    if looks_creative_humor(rest):
        return ""
    return rest


def looks_say_text(text: str) -> bool:
    return bool(extract_say_payload(text))


def _looks_talk_clause(text: str) -> bool:
    raw = str(text or "")
    if looks_creative_humor(raw) or _KNOWLEDGE_Q.search(raw):
        return True
    return bool(
        re.search(
            r"\b(?:powiedz|opowiedz|pogadaj|czy\s+ten\s+pomys[lł]|ma\s+sens)\b",
            raw,
            re.I,
        )
    )


def looks_garbled_stt(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    if looks_creative_humor(raw) or _SMALL_TALK.match(lowered) or _KNOWLEDGE_Q.search(raw):
        return False
    if looks_situation_briefing(raw):
        return False
    try:
        from core.read_intents import (
            looks_home_arrival,
            looks_home_overview,
            looks_list_notifications,
            looks_list_reminders,
            looks_missed_digest,
            looks_phone_status,
            looks_today_schedule,
            looks_undo,
            looks_weather,
        )

        if (
            looks_home_arrival(raw)
            or looks_home_overview(raw)
            or looks_list_reminders(raw)
            or looks_today_schedule(raw)
            or looks_weather(raw)
            or looks_missed_digest(raw)
            or looks_list_notifications(raw)
            or looks_phone_status(raw)
            or looks_undo(raw)
        ):
            return False
    except Exception:
        pass
    if _ACTION_VERB.search(raw) or _WAIT.match(lowered) or _CANCEL.match(lowered) or _STOP.match(lowered):
        return False
    if re.search(
        r"\b(?:zr[oó]bce|nowotni|widzisz\s+nowot|to\s+zr[oó]bce)\b",
        raw,
        re.I,
    ):
        return True
    return False


def looks_hypothetical(text: str) -> bool:
    raw = str(text or "")
    return bool(
        re.search(
            r"(?:"
            r"\bco\s+si[eę]\s+stanie\s*,?\s*(?:jak|je[sś]li)\b|"
            r"\bco\s+by\s+si[eę]\s+sta[lł]o\b|"
            r"\bco\s+je[sś]li\b|"
            r"\bje[sś]li\s+w[lł][aą]czysz\b|"
            r"\bgdybym\b|"
            r"\bgdyby\s+|"
            r"\bhipotetycznie\b|"
            r"\bco\s+je[sś]li\s+powiedzia"
            r")",
            raw,
            re.I,
        )
    )


def looks_quoted_command(text: str) -> bool:
    raw = str(text or "")
    return bool(
        re.search(
            r"(?:"
            r"\bpowiedzia[lł]em\s+(?:mu|jej|kole)\w*\b|"
            r"\bpowiedzia[lł]em\s*:|"
            r"\bnapisa[lł]em\b|"
            r"\bjest poleceniem\b|"
            r"\bzdanie\b.+\bpolecen|"
            r"\bcytat\s+z\b|"
            r"['\"„”«»].{0,80}(?:otw[oó]rz|wy[lł][aą]cz|zamknij|zga[sś]|w[lł][aą]cz|pu[sś][cć]|rozja[sś]n|uruchom|wygoogluj).{0,40}['\"„”«»]|"
            r"\bkomenda\s*['\"„”]|"
            # Meta / poison wrappers (novel adversarial): cite or discuss, don't execute.
            r"\bto\s+nie\s+request\b|"
            r"\bprzyk[lł]ad\s+komendy\b|"
            r"\btylko\s+cytuj[eę]\b|"
            r"\broleplay\b|"
            r"\bNPC\s+m[oó]wi\b|"
            r"\btweet\s+brzmi\b|"
            r"\b(?:log|dokumentacja)\b.+\b(?:zawiera|pokazuje|przyk[lł]ad)\b|"
            r"\bkolega\s+na\s+discu\s+napisa[lł]\b|"
            r"\bczy\s+samo\s+pytanie\s+o\b|"
            r"\bprzet[lł]umacz\s+na\b|"
            r"\bnie\s+klikaj\s+tego\b"
            r")",
            raw,
            re.I,
        )
    )


def looks_situation_briefing(text: str) -> bool:
    from core.situation_briefing import looks_situation_briefing as _looks

    return _looks(text)


def looks_smart_home_status(text: str) -> bool:
    raw = str(text or "")
    if not _STATUS_Q.search(raw):
        return False
    # Polite commands ("czy możesz zapalić lampę?") are ACT, not status.
    if re.search(r"\b(?:zapal|w[lł][aą]cz|z?ga[sś]|wy[lł][aą]cz|przyciemn|rozja[sś]n)\w*\b", raw, re.I):
        if re.search(r"\b(?:mo[zż]esz|m[oó]g[lł]by[sś]|w[lł][aą]czysz)\b", raw, re.I):
            return False
        if re.search(r"\b(?:w[lł][aą]czon|wy[lł][aą]czon|[sś]wieci)\b", raw, re.I):
            return True
        return False
    return True


def looks_discussion_not_action(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if looks_quoted_command(raw) or looks_hypothetical(raw):
        return True
    if _DISCUSSION.search(raw):
        return True
    if _QUESTION_SHAPE.search(raw) and _ACTION_VERB.search(raw) and not _VOLUME_BARE.match(raw):
        # "czy możesz powiedzieć, co znaczy głośniej?" — question wins.
        if re.search(r"\b(?:znaczy|oznacza|umiesz|potrafisz|jak)\b", raw, re.I):
            return True
    return False


def _core(text: str) -> str:
    raw = str(text or "").strip()
    raw = raw.split("[kontekst:", 1)[0].split("\n(", 1)[0].strip()
    return raw


def _strip_ack_prefix(text: str) -> str:
    raw = _core(text)
    return re.sub(
        r"^(?:ok|okej|okay|dobra|super|jasne|spoko|dzi[eę]ki)[,!.…]+\s*",
        "",
        raw,
        flags=re.I,
    ).strip(" .,!?")


def classify_pass1(
    text: str,
    *,
    conversation: Optional[Dict[str, Any]] = None,
) -> DecisionEnvelope:
    """Utterance-only semantic pass. Conversation state is allowed; execution context is not."""
    raw = _strip_ack_prefix(text) or _core(text)
    lowered = raw.lower().strip()
    conv = dict(conversation or {})
    last_mode = str(conv.get("last_mode") or "")
    last_was_talk = last_mode in (DecisionMode.TALK.value, DecisionMode.ANSWER.value)

    if not lowered:
        return DecisionEnvelope(
            mode=DecisionMode.ASK.value,
            intent="empty",
            user_goal="",
            confidence=0.4,
            needs_tools=False,
            needs_clarification=True,
            missing_information=["utterance"],
        )

    if _WAIT.match(lowered):
        return DecisionEnvelope(
            mode=DecisionMode.WAIT.value,
            intent="wait",
            user_goal="Pause and wait for the user",
            confidence=0.97,
            needs_tools=False,
            response_style="concise",
            wait_requested=True,
        )

    if _CANCEL.match(lowered) or _STOP.match(lowered):
        return DecisionEnvelope(
            mode=DecisionMode.WAIT.value,
            intent="cancel",
            user_goal="Cancel the current plan",
            confidence=0.96,
            needs_tools=False,
            continuation=True,
            context_requested=True,
            cancel_requested=True,
        )

    try:
        from core.procedure_engine import (
            find_by_trigger,
            looks_procedure_definition,
            parse_manage_utterance,
        )

        managed = parse_manage_utterance(raw)
        if managed is not None:
            action, _payload = managed
            return DecisionEnvelope(
                mode=DecisionMode.ACT.value if action == "run" else DecisionMode.ANSWER.value,
                intent="procedure_run" if action == "run" else "procedure_manage",
                user_goal=raw,
                confidence=0.96,
                needs_tools=action == "run",
                allowed_domains=["SMART_HOME", "SYSTEM", "MEDIA", "APP", "COMPUTER"],
                response_style="concise",
                safety_notes=["procedure_manage"],
            )
        if looks_procedure_definition(raw):
            return DecisionEnvelope(
                mode=DecisionMode.ACT.value,
                intent="procedure_define",
                user_goal=raw,
                confidence=0.95,
                needs_tools=False,
                response_style="concise",
                safety_notes=["procedure_define"],
            )
        saved = find_by_trigger(raw, enabled_only=True)
        if saved is not None:
            return DecisionEnvelope(
                mode=DecisionMode.ACT.value,
                intent="procedure_run",
                user_goal=raw,
                confidence=0.95,
                needs_tools=True,
                allowed_domains=["SMART_HOME", "SYSTEM", "MEDIA", "APP", "COMPUTER"],
                response_style="concise",
                safety_notes=["saved_procedure_trigger"],
            )
    except Exception:
        pass

    clauses = [part.strip() for part in _COMPOUND_AND.split(raw) if part and str(part).strip()]
    mixed_humor = looks_creative_humor(raw) and len(clauses) >= 2 and any(
        _ACTION_VERB.search(part) and not looks_creative_humor(part) for part in clauses
    )
    if mixed_humor:
        return DecisionEnvelope(
            mode=DecisionMode.PLAN.value,
            intent="mixed_talk_and_action",
            user_goal=raw,
            confidence=0.93,
            needs_tools=True,
            allowed_domains=_domains_from_text(raw),
            response_style="mixed",
        )

    if looks_creative_humor(raw):
        return DecisionEnvelope(
            mode=DecisionMode.TALK.value,
            intent="creative_humor",
            user_goal="Tell a joke or something funny",
            confidence=0.97,
            needs_tools=False,
            allowed_domains=[],
            continuation=False,
            response_style="conversational",
        )

    if looks_situation_briefing(raw):
        return DecisionEnvelope(
            mode=DecisionMode.ANSWER.value,
            intent="situation_briefing",
            user_goal=raw,
            confidence=0.94,
            needs_tools=True,
            allowed_domains=["SMART_HOME", "SYSTEM"],
            response_style="concise",
            safety_notes=["read_only"],
        )

    try:
        from core.read_intents import (
            looks_activate_scene,
            looks_home_arrival,
            looks_home_overview,
            looks_list_notifications,
            looks_list_reminders,
            looks_missed_digest,
            looks_phone_status,
            looks_simulate,
            looks_today_schedule,
            looks_undo,
            looks_weather,
            undo_count as _undo_count,
        )

        if looks_undo(raw):
            return DecisionEnvelope(
                mode=DecisionMode.ACT.value,
                intent="undo_action",
                user_goal=raw,
                confidence=0.96,
                needs_tools=True,
                allowed_domains=["SYSTEM", "SMART_HOME", "MEDIA"],
                response_style="concise",
                payload=str(_undo_count(raw)),
            )
        if looks_home_arrival(raw):
            return DecisionEnvelope(
                mode=DecisionMode.ANSWER.value,
                intent="home_arrival",
                user_goal=raw,
                confidence=0.95,
                needs_tools=True,
                allowed_domains=["SMART_HOME", "SYSTEM", "WEB"],
                safety_notes=["read_only"],
                response_style="concise",
            )
        if looks_list_notifications(raw):
            return DecisionEnvelope(
                mode=DecisionMode.ANSWER.value,
                intent="list_notifications",
                user_goal=raw,
                confidence=0.94,
                needs_tools=True,
                safety_notes=["read_only"],
                response_style="concise",
            )
        if looks_phone_status(raw):
            return DecisionEnvelope(
                mode=DecisionMode.ANSWER.value,
                intent="phone_status",
                user_goal=raw,
                confidence=0.94,
                needs_tools=True,
                safety_notes=["read_only"],
                response_style="concise",
            )
        if looks_simulate(raw):
            return DecisionEnvelope(
                mode=DecisionMode.SIMULATE.value,
                intent="simulate",
                user_goal=raw,
                confidence=0.93,
                needs_tools=False,
                safety_notes=["simulate_no_side_effect"],
                response_style="concise",
            )
        if looks_missed_digest(raw):
            return DecisionEnvelope(
                mode=DecisionMode.ANSWER.value,
                intent="missed_digest",
                user_goal=raw,
                confidence=0.94,
                needs_tools=True,
                safety_notes=["read_only"],
                response_style="concise",
            )
        if looks_home_overview(raw):
            return DecisionEnvelope(
                mode=DecisionMode.ANSWER.value,
                intent="home_overview",
                user_goal=raw,
                confidence=0.94,
                needs_tools=True,
                allowed_domains=["SMART_HOME"],
                safety_notes=["read_only"],
                response_style="concise",
            )
        if looks_today_schedule(raw):
            return DecisionEnvelope(
                mode=DecisionMode.ANSWER.value,
                intent="today_schedule",
                user_goal=raw,
                confidence=0.93,
                needs_tools=True,
                safety_notes=["read_only"],
                response_style="concise",
            )
        if looks_list_reminders(raw):
            return DecisionEnvelope(
                mode=DecisionMode.ANSWER.value,
                intent="list_reminders",
                user_goal=raw,
                confidence=0.93,
                needs_tools=True,
                safety_notes=["read_only"],
                response_style="concise",
            )
        if looks_weather(raw):
            return DecisionEnvelope(
                mode=DecisionMode.ANSWER.value,
                intent="weather_status",
                user_goal=raw,
                confidence=0.93,
                needs_tools=True,
                safety_notes=["read_only"],
                response_style="concise",
            )
        if looks_activate_scene(raw):
            return DecisionEnvelope(
                mode=DecisionMode.ACT.value,
                intent="smart_home_control",
                user_goal=raw,
                confidence=0.92,
                needs_tools=True,
                allowed_domains=["SMART_HOME"],
                response_style="concise",
            )
    except Exception:
        pass

    if _SMALL_TALK.match(lowered):
        return DecisionEnvelope(
            mode=DecisionMode.TALK.value,
            intent="social_smalltalk",
            user_goal=raw,
            confidence=0.96,
            needs_tools=False,
            response_style="conversational",
        )

    say_payload = extract_say_payload(raw)
    if say_payload:
        spoken = format_say_speech(say_payload)
        return DecisionEnvelope(
            mode=DecisionMode.TALK.value,
            intent="say_text",
            user_goal=raw,
            confidence=0.95,
            needs_tools=False,
            allowed_domains=[],
            payload=say_payload,
            draft_reply=spoken,
            response_style="verbatim",
            safety_notes=["speech_output_only"],
        )

    try:
        from core.collaboration import apply_collaboration_semantics

        collab_env = apply_collaboration_semantics(raw, conv)
        if collab_env is not None:
            return collab_env
    except Exception:
        pass

    try:
        from core.perception.screen_truth import looks_screen_observation_query

        if looks_screen_observation_query(raw):
            return DecisionEnvelope(
                mode=DecisionMode.OBSERVE.value,
                intent="current_screen_observation",
                user_goal="Describe what is currently on screen",
                confidence=0.95,
                needs_tools=True,
                allowed_domains=["READ_ONLY_SCREEN"],
                requires_fresh_observation=True,
            )
    except Exception:
        pass

    if looks_discussion_not_action(raw):
        return DecisionEnvelope(
            mode=DecisionMode.ANSWER.value,
            intent="action_discussion",
            user_goal=f"Discuss or explain, not execute: {raw}",
            confidence=0.93,
            needs_tools=False,
            safety_notes=["do_not_execute"],
            response_style="explanatory",
        )

    if looks_smart_home_status(raw):
        return DecisionEnvelope(
            mode=DecisionMode.ANSWER.value,
            intent="smart_home_status",
            user_goal=raw,
            confidence=0.93,
            needs_tools=True,
            allowed_domains=["SMART_HOME"],
            response_style="concise",
            safety_notes=["read_only"],
        )

    if re.search(r"\bopowiedz\b", raw, re.I) and not _ACTION_VERB.search(raw) and not looks_creative_humor(raw):
        return DecisionEnvelope(
            mode=DecisionMode.ANSWER.value,
            intent="knowledge_explanation",
            user_goal=raw,
            confidence=0.88,
            needs_tools=False,
            response_style="explanatory",
        )

    if _FOLLOWUP_TALK.match(lowered):
        intent = "conversational_followup"
        if last_was_talk or not conv.get("pending_executable"):
            return DecisionEnvelope(
                mode=DecisionMode.ANSWER.value,
                intent=intent,
                user_goal="Continue the previous conversational topic",
                confidence=0.9 if last_was_talk else 0.82,
                needs_tools=False,
                continuation=True,
                context_requested=last_was_talk,
                response_style="conversational",
            )

    if _CORRECTION.search(raw) or (
        _VISUAL_CHOICE.search(raw) and re.match(r"^\s*nie\b", lowered)
    ):
        # Discourse refinement — keep PRIMARY user goal text if known; never invent
        # a new primary objective like "Correct or resolve the previous choice".
        prior = ""
        try:
            prior = str(
                (conversation or {}).get("active_goal", {}) or {}
            )
            if isinstance((conversation or {}).get("active_goal"), dict):
                prior = str(
                    ((conversation or {}).get("active_goal") or {}).get("objective") or ""
                )
        except Exception:
            prior = ""
        return DecisionEnvelope(
            mode=DecisionMode.ACT.value,
            intent="resolve_pending_visual_choice" if _VISUAL_CHOICE.search(raw) else "correction",
            user_goal=prior or str(raw or "")[:240],
            confidence=0.9,
            needs_tools=True,
            continuation=True,
            context_requested=True,
            referents=["pending"],
            correction=True,
        )

    if _TYPE_CONT.search(raw):
        payload = ""
        try:
            from core.computer_semantics import extract_type_payload

            payload = extract_type_payload(raw)
        except Exception:
            payload = re.sub(
                r"^.*?\b(?:wpisz|dopisz)\s+(?:tam|to|tu),?\s*(?:[zż]e\s+)?",
                "",
                raw,
                count=1,
                flags=re.I,
            ).strip(" .\"'„”")
        return DecisionEnvelope(
            mode=DecisionMode.ACT.value,
            intent="continue_typing",
            user_goal="Type the given text into the active editor",
            confidence=0.92,
            needs_tools=True,
            allowed_domains=["COMPUTER"],
            continuation=True,
            context_requested=True,
            referents=["tam"],
            payload=payload,
        )

    if _CLOSE_REF.search(raw):
        # "wyłącz ją" with active light → SMART_HOME, never APP close_referent.
        feminine_off = bool(
            re.search(r"\bwy[lł][aą]cz\b", raw, re.I)
            and re.search(r"\b(?:j[aą]|ni[aą]|t[eę]|t[aą]|to)\b", raw, re.I)
        )
        lightish = bool(re.search(r"lamp|[sś]wiat[lł]|vizzini", raw, re.I))
        world_dev = None
        try:
            world_dev = _world_referent(raw)
        except Exception:
            world_dev = None
        if feminine_off or lightish:
            if lightish or (
                isinstance(world_dev, dict) and str(world_dev.get("kind") or "") == "device"
            ) or feminine_off:
                label = ""
                if isinstance(world_dev, dict):
                    label = str(world_dev.get("entity_id") or world_dev.get("name") or "")
                return DecisionEnvelope(
                    mode=DecisionMode.ACT.value,
                    intent="smart_home_control",
                    user_goal=raw if not label else f"Wyłącz {label}",
                    confidence=0.9,
                    needs_tools=True,
                    allowed_domains=["SMART_HOME"],
                    continuation=True,
                    context_requested=True,
                    referents=[label] if label else ["ją"],
                )
        if not lightish:
            refs = []
            if _MASC_REF.search(raw):
                refs.append("masculine_service_or_window")
            return DecisionEnvelope(
                mode=DecisionMode.ACT.value,
                intent="close_referent",
                user_goal="Close the previously relevant app or window",
                confidence=0.88,
                needs_tools=True,
                allowed_domains=["APP"],
                continuation=True,
                context_requested=True,
                referents=refs or ["it"],
            )

    clauses = [part.strip() for part in _COMPOUND_AND.split(raw) if part and part.strip()]
    talkish = any(_looks_talk_clause(part) for part in clauses)
    actionish = any(_ACTION_VERB.search(part) for part in clauses)
    if len(clauses) >= 2 and talkish and actionish:
        return DecisionEnvelope(
            mode=DecisionMode.PLAN.value,
            intent="mixed_talk_and_action",
            user_goal=raw,
            confidence=0.91,
            needs_tools=True,
            allowed_domains=_domains_from_text(raw),
            response_style="mixed",
        )
    if len(clauses) >= 2 and actionish and all(_ACTION_VERB.search(part) for part in clauses[:2]):
        return DecisionEnvelope(
            mode=DecisionMode.PLAN.value,
            intent="multi_action",
            user_goal=raw,
            confidence=0.92,
            needs_tools=True,
            allowed_domains=_domains_from_text(raw),
        )

    if _VOLUME_BARE.match(lowered):
        return DecisionEnvelope(
            mode=DecisionMode.ACT.value,
            intent="system_volume",
            user_goal="Adjust system volume",
            confidence=0.96,
            needs_tools=True,
            allowed_domains=["SYSTEM"],
            response_style="concise",
        )

    if _KNOWLEDGE_Q.search(raw) and not _OPEN_APP.search(raw):
        return DecisionEnvelope(
            mode=DecisionMode.ANSWER.value,
            intent="knowledge_explanation",
            user_goal=raw,
            confidence=0.9,
            needs_tools=False,
            response_style="explanatory",
        )

    try:
        from core.intent_family import IntentFamily, classify_intent_family

        active_domain = str(
            conv.get("recent_domain")
            or (
                (conv.get("active_goal") or {}).get("domain")
                if isinstance(conv.get("active_goal"), dict)
                else ""
            )
            or ""
        )
        if not active_domain:
            ad = conv.get("active_device")
            if isinstance(ad, dict) and ad.get("domain"):
                dom = str(ad.get("domain") or "").lower()
                active_domain = "SMART_HOME" if dom in ("light", "climate", "switch") else dom
            elif getattr(ad, "domain", None):
                dom = str(getattr(ad, "domain") or "").lower()
                active_domain = "SMART_HOME" if dom in ("light", "climate", "switch") else dom
        info = classify_intent_family(
            raw, allow_compound=False, active_domain=active_domain
        )
        family = info.family
        # Bare/truncated open verb (ASR "włącz…") — ask for target, never false APP.
        if (info.slots or {}).get("open_verb_bare"):
            return DecisionEnvelope(
                mode=DecisionMode.ASK.value,
                intent="incomplete_request",
                user_goal=raw,
                confidence=0.75,
                needs_tools=False,
                needs_clarification=True,
                missing_information=["target"],
                response_style="concise",
                safety_notes=["no_side_effect", "truncated_or_bare_verb"],
                utterance_class="ACTION_REQUEST",
            )
        if family == IntentFamily.APP and re.search(r"lamp|[sś]wiat[lł]|vizzini", raw, re.I):
            family = IntentFamily.SMART_HOME
            info = type(info)(
                family=family,
                is_complete=True,
                slots=dict(info.slots or {}) or {"implicit_light": True},
                raw_text=raw,
            )
        # Pronoun power/dim with live SMART_HOME context is HA, not incomplete APP open.
        if family == IntentFamily.APP:
            try:
                from core.device_pronoun import (
                    detect_device_action_hint,
                    has_device_pronoun,
                )

                _hint = detect_device_action_hint(raw)
                _pronoun_ha = has_device_pronoun(raw) and bool(_hint)
            except Exception:
                _pronoun_ha = False
                _hint = ""
            _dom = str(active_domain or "").upper()
            if _pronoun_ha and _dom in ("SMART_HOME", "LIGHT"):
                family = IntentFamily.SMART_HOME
                info = type(info)(
                    family=family,
                    is_complete=True,
                    slots=dict(info.slots or {})
                    or {"semantic_action": _hint or "turn_on"},
                    raw_text=raw,
                )
        if family == IntentFamily.KNOWLEDGE:
            return DecisionEnvelope(
                mode=DecisionMode.ANSWER.value,
                intent="knowledge_explanation",
                user_goal=raw,
                confidence=0.88 if info.is_complete else 0.8,
                needs_tools=False,
            )
        if family in (
            IntentFamily.APP,
            IntentFamily.SYSTEM,
            IntentFamily.MEDIA,
            IntentFamily.SMART_HOME,
            IntentFamily.COMPUTER,
            IntentFamily.VISION,
            IntentFamily.WEB,
        ) and not looks_discussion_not_action(raw):
            domains = {
                IntentFamily.APP: ["APP"],
                IntentFamily.SYSTEM: ["SYSTEM"],
                IntentFamily.MEDIA: ["MEDIA", "APP"],
                IntentFamily.SMART_HOME: ["SMART_HOME"],
                IntentFamily.COMPUTER: ["COMPUTER"],
                IntentFamily.VISION: ["VISION", "READ_ONLY_SCREEN"],
                IntentFamily.WEB: ["WEB"],
            }.get(family, [])
            mode = DecisionMode.ACT.value if info.is_complete else DecisionMode.ASK.value
            needs = bool(info.is_complete)
            conf = 0.92 if info.is_complete else 0.7
            if family == IntentFamily.VISION:
                action = str((info.slots or {}).get("semantic_action") or "")
                if action in ("inspect_screen", "describe_screen") or not action:
                    try:
                        from core.perception.screen_truth import looks_screen_observation_query

                        if looks_screen_observation_query(raw):
                            mode = DecisionMode.OBSERVE.value
                            needs = True
                            conf = 0.9
                        else:
                            mode = DecisionMode.ACT.value if info.is_complete else DecisionMode.ASK.value
                            needs = bool(info.is_complete)
                            conf = 0.92 if info.is_complete else 0.7
                    except Exception:
                        mode = DecisionMode.ACT.value if info.is_complete else DecisionMode.ASK.value
                        needs = bool(info.is_complete)
                        conf = 0.92 if info.is_complete else 0.7
                else:
                    mode = DecisionMode.ACT.value if info.is_complete else DecisionMode.ASK.value
                    needs = bool(info.is_complete)
                    conf = 0.92 if info.is_complete else 0.7
            intent_label = str((info.slots or {}).get("semantic_action") or family.value.lower())
            if family == IntentFamily.SMART_HOME:
                light_cmd = bool(
                    re.search(
                        r"\b(?:zapal(?:i[cć])?|w[lł][aą]cz(?:y[cć]|ysz)?|"
                        r"z?ga[sś](?:i[cć])?|wyga[sś]|wy[lł][aą]cz(?:y[cć]|ysz)?|"
                        r"przyciemnij|rozja[sś]nij|ustaw)\b",
                        raw,
                        re.I,
                    )
                ) and bool(re.search(r"lamp|[sś]wiat[lł]|vizzini", raw, re.I))
                if light_cmd:
                    intent_label = "smart_home_control"
                    mode = DecisionMode.ACT.value
                    needs = True
                    conf = 0.94
                elif (info.slots or {}).get("read_only") or looks_smart_home_status(raw):
                    return DecisionEnvelope(
                        mode=DecisionMode.ANSWER.value,
                        intent="smart_home_status",
                        user_goal=raw,
                        confidence=0.93,
                        needs_tools=True,
                        allowed_domains=["SMART_HOME"],
                        safety_notes=["read_only"],
                    )
                else:
                    intent_label = "smart_home_control"
                    # Natural state goals ("jest za ciemno") carry slots but are
                    # often marked incomplete by intent_family — still actionable.
                    if (info.slots or {}).get("implicit_light"):
                        mode = DecisionMode.ACT.value
                        needs = True
                        conf = max(conf, 0.9)
                        return DecisionEnvelope(
                            mode=mode,
                            intent=intent_label,
                            user_goal=raw,
                            confidence=conf,
                            needs_tools=True,
                            allowed_domains=domains,
                            needs_clarification=False,
                            utterance_class="GOAL",
                            parameter_hint=str(
                                (info.slots or {}).get("desired") or "brighter"
                            ),
                            goal_objective=raw,
                        )
                    # Pronoun device acts ("zgaś ją") need recent SMART_HOME context,
                    # not a new bare incomplete ask.
                    try:
                        from core.device_pronoun import (
                            detect_device_action_hint,
                            has_device_pronoun,
                        )

                        pronoun_act = has_device_pronoun(raw) and bool(
                            detect_device_action_hint(raw)
                        )
                    except Exception:
                        pronoun_act = False
                    recent_domain = str(
                        conv.get("recent_domain")
                        or (
                            (conv.get("active_goal") or {}).get("domain")
                            if isinstance(conv.get("active_goal"), dict)
                            else ""
                        )
                        or ""
                    ).upper()
                    recent_sh = recent_domain == "SMART_HOME" or bool(
                        conv.get("has_active_goal")
                        or conv.get("has_recent_goal")
                    ) and recent_domain in ("", "SMART_HOME")
                    if pronoun_act and (
                        recent_domain == "SMART_HOME"
                        or (
                            (conv.get("has_active_goal") or conv.get("has_recent_goal"))
                            and recent_domain in ("", "SMART_HOME")
                        )
                        or bool(conv.get("active_parameter") or conv.get("last_parameter"))
                    ):
                        return DecisionEnvelope(
                            mode=DecisionMode.ACT.value,
                            intent="smart_home_control",
                            user_goal=raw,
                            confidence=0.92,
                            needs_tools=True,
                            allowed_domains=["SMART_HOME"],
                            needs_clarification=False,
                            continuation=True,
                            context_requested=True,
                            referents=["current_device"],
                            utterance_class="ACTION_REQUEST",
                        )
                    mode = DecisionMode.ACT.value if info.is_complete else DecisionMode.ASK.value
                    needs = bool(info.is_complete)
                    conf = 0.94 if info.is_complete else conf
            return DecisionEnvelope(
                mode=mode,
                intent=intent_label,
                user_goal=raw,
                confidence=conf,
                needs_tools=needs,
                allowed_domains=domains,
                needs_clarification=not info.is_complete,
            )
    except Exception:
        pass

    if _PLAY_MEDIA.search(raw) and _OPEN_APP.search(raw):
        return DecisionEnvelope(
            mode=DecisionMode.PLAN.value,
            intent="media_playback",
            user_goal="Play the requested track using a suitable music service",
            confidence=0.93,
            needs_tools=True,
            allowed_domains=["APP", "MEDIA"],
        )

    if _OPEN_APP.search(raw) and not looks_discussion_not_action(raw):
        return DecisionEnvelope(
            mode=DecisionMode.ACT.value,
            intent="open_app",
            user_goal=raw,
            confidence=0.92,
            needs_tools=True,
            allowed_domains=["APP"],
            response_style="concise",
        )

    if _PLAY_MEDIA.search(raw) and not looks_discussion_not_action(raw):
        return DecisionEnvelope(
            mode=DecisionMode.ACT.value,
            intent="media_playback",
            user_goal=raw,
            confidence=0.9,
            needs_tools=True,
            allowed_domains=["MEDIA", "APP"],
        )

    # Conversational remainder — first-class TALK, not a command miss.
    if looks_garbled_stt(raw):
        return DecisionEnvelope(
            mode=DecisionMode.ASK.value,
            intent="unclear_utterance",
            user_goal=raw,
            confidence=0.4,
            needs_tools=False,
            needs_clarification=True,
            missing_information=["intended_meaning"],
            response_style="honest_uncertainty",
            safety_notes=["no_side_effect", "garbled_stt"],
        )
    try:
        from core.learned_routines import lookup_learned_routine, looks_unclassified_request

        learned = lookup_learned_routine(raw)
        if learned is not None and not _discourse_blocks_learned(raw, conv):
            return DecisionEnvelope(
                mode=DecisionMode.ACT.value,
                intent="learned_routine",
                user_goal=raw,
                confidence=0.95,
                needs_tools=True,
                safety_notes=["learned_user_phrase"],
                response_style="concise",
            )
        if looks_unclassified_request(raw):
            return DecisionEnvelope(
                mode=DecisionMode.ACT.value,
                intent="unclassified_request",
                user_goal=raw,
                confidence=0.86,
                needs_tools=True,
                allowed_domains=_domains_from_text(raw),
                safety_notes=["agent_all_tools"],
                response_style="concise",
            )
    except Exception:
        pass
    return DecisionEnvelope(
        mode=DecisionMode.TALK.value,
        intent="general_conversation",
        user_goal=raw,
        confidence=0.8,
        needs_tools=False,
        response_style="conversational",
    )


def _domains_from_text(text: str) -> List[str]:
    raw = str(text or "").lower()
    out: List[str] = []
    if re.search(r"\b(?:otw[oó]rz|zamknij|notatnik|spotify|chrome)\b", raw):
        out.append("APP")
    if re.search(r"\b(?:pu[sś][cć]|muzyk|spotify)\b", raw):
        out.append("MEDIA")
    if re.search(r"\b(?:g[lł]o[sś]niej|ciszej|g[lł]o[sś]no)\b", raw):
        out.append("SYSTEM")
    if re.search(r"\b(?:lamp\w*|[sś]wiat[lł]\w*|klima)\b", raw):
        out.append("SMART_HOME")
    if re.search(r"\b(?:wpisz|zaznacz|kliknij)\b", raw):
        out.append("COMPUTER")
    return out


def refine_with_context(
    envelope: DecisionEnvelope,
    runtime_context: Optional[Dict[str, Any]] = None,
) -> DecisionEnvelope:
    """Resolve referents. Never change an unrelated TALK/ANSWER intent."""
    env = envelope
    ctx = dict(runtime_context or {})
    if (
        env.mode
        in (
            DecisionMode.TALK.value,
            DecisionMode.ANSWER.value,
            DecisionMode.PROACTIVE_DIALOGUE.value,
        )
        and not env.continuation
        and not getattr(env, "user_is_responding_to_elian", False)
    ):
        env.context_requested = False
        return env
    if not env.context_requested and not env.continuation:
        return env

    service = ctx.get("active_service") or {}
    service_name = str(
        service.get("service_name") or ctx.get("active_service_name") or ""
    ).strip()
    editor = str(ctx.get("active_editor") or ctx.get("verified_editor") or "").strip()
    pending_visual = bool(ctx.get("pending_visual") or ctx.get("visual_referent"))

    if env.intent == "close_referent" and service_name:
        env.user_goal = f"Close {service_name}"
        env.referents = [service_name]
        env.confidence = max(env.confidence, 0.92)
        env.allowed_domains = ["APP"]
    if env.intent == "close_referent" and not env.referents:
        world_ref = _world_referent(str(ctx.get("utterance") or env.user_goal or ""))
        if world_ref is not None:
            label = str(world_ref.get("entity_id") or world_ref.get("name") or "")
            kind = str(world_ref.get("kind") or "")
            if label and kind == "app":
                env.user_goal = f"Close {label}"
                env.referents = [label]
                env.allowed_domains = ["APP"]
                env.confidence = max(env.confidence, 0.9)
            elif label and kind == "device":
                env.referents = [label]
                env.allowed_domains = ["SMART_HOME"]
                env.confidence = max(env.confidence, 0.9)
    if env.intent == "continue_typing":
        target = editor or service_name or "active editor"
        env.referents = [target]
        env.user_goal = f"Type into {target}: {env.payload}"
        env.confidence = max(env.confidence, 0.91)
    if env.intent == "resolve_pending_visual_choice" and pending_visual:
        env.confidence = max(env.confidence, 0.9)
        env.referents = ["pending_visual"]
    if not env.referents:
        utterance = str(ctx.get("utterance") or env.user_goal or "")
        if _needs_world_referent(utterance):
            world_ref = _world_referent(utterance)
            if world_ref is not None:
                label = str(world_ref.get("entity_id") or world_ref.get("name") or "")
                if label:
                    env.referents = [label]
    return env


def _needs_world_referent(text: str) -> bool:
    raw = str(text or "").lower()
    if re.search(r"\b(?:ją|ja|nią|nia|go|niego|je|to|ten|tę|te|tą|ta|tamto)\b", raw):
        return True
    if re.search(r"\b(?:ciszej|g[lł]o[sś]niej)\b", raw):
        return True
    return False


def _world_referent(text: str) -> Optional[Dict[str, Any]]:
    try:
        from core.world_state import resolve_pronoun_referent

        ref = resolve_pronoun_referent(text)
        if ref is None or ref.ambiguous:
            return None
        return ref.to_dict()
    except Exception:
        return None


def validate_envelope(raw: Dict[str, Any]) -> DecisionEnvelope:
    mode = str(raw.get("mode") or "").upper().strip()
    if mode not in DECISION_MODES:
        raise ValueError(f"invalid mode: {mode}")
    confidence = float(raw.get("confidence") if raw.get("confidence") is not None else 0.0)
    confidence = max(0.0, min(1.0, confidence))
    domains = [
        str(item)
        for item in (raw.get("allowed_domains") or [])
        if str(item) in ALLOWED_DOMAINS
    ]
    needs_tools = bool(raw.get("needs_tools"))
    if mode in (
        DecisionMode.TALK.value,
        DecisionMode.ANSWER.value,
        DecisionMode.WAIT.value,
        DecisionMode.SIMULATE.value,
        DecisionMode.CANCEL.value,
        DecisionMode.PROACTIVE_DIALOGUE.value,
    ):
        if confidence >= CONFIDENCE_USABLE:
            needs_tools = False
            domains = []
    if mode == DecisionMode.SIMULATE.value:
        needs_tools = False
        domains = []
    if mode == DecisionMode.OBSERVE.value:
        needs_tools = True
        if "READ_ONLY_SCREEN" not in domains:
            domains = ["READ_ONLY_SCREEN"]
    if confidence < CONFIDENCE_USABLE and needs_tools and mode in (
        DecisionMode.ACT.value,
        DecisionMode.PLAN.value,
    ):
        # Hard contract: no side effects on weak semantics.
        needs_tools = False
        mode = DecisionMode.ASK.value
        raw = dict(raw)
        raw["needs_clarification"] = True
    referents = [str(item) for item in (raw.get("referents") or []) if str(item).strip()]
    missing = [str(item) for item in (raw.get("missing_information") or []) if str(item).strip()]
    safety = [str(item) for item in (raw.get("safety_notes") or []) if str(item).strip()]
    return DecisionEnvelope(
        mode=mode,
        intent=str(raw.get("intent") or "conversation")[:80],
        user_goal=str(raw.get("user_goal") or "")[:240],
        confidence=confidence,
        needs_tools=needs_tools,
        allowed_domains=domains,
        requires_fresh_observation=bool(raw.get("requires_fresh_observation")),
        referents=referents,
        continuation=bool(raw.get("continuation")),
        needs_clarification=bool(raw.get("needs_clarification")),
        missing_information=missing,
        response_style=str(raw.get("response_style") or "")[:40],
        safety_notes=safety,
        context_requested=bool(raw.get("context_requested")),
        draft_reply=str(raw.get("draft_reply") or ""),
        source=str(raw.get("source") or "model"),
        extra_llm_call=bool(raw.get("extra_llm_call")),
        payload=str(raw.get("payload") or ""),
        conversation_topic=str(raw.get("conversation_topic") or "")[:80],
        task_reference=str(raw.get("task_reference") or "")[:80],
        decision_reference=str(raw.get("decision_reference") or "")[:80],
        user_is_responding_to_elian=bool(raw.get("user_is_responding_to_elian")),
        followup_kind=str(raw.get("followup_kind") or "")[:40],
        recommended_next_action=str(raw.get("recommended_next_action") or "")[:80],
        negated=bool(raw.get("negated")),
        hypothetical=bool(raw.get("hypothetical")),
        quoted=bool(raw.get("quoted")),
        correction=bool(raw.get("correction")),
        wait_requested=bool(raw.get("wait_requested")),
        cancel_requested=bool(raw.get("cancel_requested")),
        utterance_class=str(raw.get("utterance_class") or "")[:40],
        goal_objective=str(raw.get("goal_objective") or "")[:240],
        goal_status=str(raw.get("goal_status") or "")[:40],
        parameter_hint=str(raw.get("parameter_hint") or "")[:40],
        unresolved_awareness=bool(raw.get("unresolved_awareness")),
    )


_SEMANTIC_SYSTEM = (
    "Jesteś warstwą rozumienia Eliana, polskiego asystenta głosowego. "
    "Zwróć WYŁĄCZNIE JSON DecisionEnvelope. Nie wykonuj narzędzi. "
    "mode: TALK|ANSWER|OBSERVE|ACT|PLAN|ASK|WAIT|PROACTIVE_DIALOGUE. "
    "TALK/ANSWER to pełnoprawny sukces, nie fallback. "
    "Krótka odpowiedź na Twoją poprzednią propozycję (tak/pierwsza/czemu) to follow-up, nie nowa komenda. "
    "Żart, rozmowa, wyjaśnienie → needs_tools=false. "
    "Nie wymyślaj akcji z kontekstu wykonywania."
)


def _llm_semantic_pass(text: str, model) -> Optional[DecisionEnvelope]:
    try:
        from core.model_client import FakeModelClient

        if isinstance(model, FakeModelClient):
            return None
    except Exception:
        pass
    messages = [
        {"role": "system", "content": _SEMANTIC_SYSTEM},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "utterance": text,
                    "schema": {
                        "mode": DECISION_MODES,
                        "intent": "string",
                        "user_goal": "string",
                        "confidence": "0..1",
                        "needs_tools": "bool",
                        "allowed_domains": list(ALLOWED_DOMAINS),
                        "draft_reply": "optional Polish reply for TALK/ANSWER/ASK",
                    },
                },
                ensure_ascii=False,
            ),
        },
    ]
    last_error = ""
    for attempt in range(2):
        try:
            raw = model.chat(messages, temperature=0.1, format="json")
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(parsed, dict):
                raise ValueError("not an object")
            parsed["source"] = "model"
            parsed["extra_llm_call"] = True
            return validate_envelope(parsed)
        except Exception as exc:
            last_error = str(exc)
            messages = list(messages) + [
                {"role": "user", "content": "Zwróć poprawny JSON DecisionEnvelope."}
            ]
            log_turn("ai_semantic", event="schema_retry", attempt=attempt, error=last_error[:120])
    log_turn("ai_semantic", event="fallback", error=last_error[:120])
    return None


def stamp_discourse_flags(env: DecisionEnvelope, text: str) -> DecisionEnvelope:
    """Fill discourse flags from existing detectors. Does not change mode/intent."""
    raw = str(text or "")
    env.hypothetical = bool(env.hypothetical or looks_hypothetical(raw))
    env.quoted = bool(env.quoted or looks_quoted_command(raw))
    env.correction = bool(env.correction or bool(_CORRECTION.search(raw)))
    env.wait_requested = bool(env.wait_requested or env.mode == DecisionMode.WAIT.value or env.intent == "wait")
    env.cancel_requested = bool(
        env.cancel_requested or env.intent == "cancel" or env.mode == DecisionMode.CANCEL.value
    )
    if re.search(r"^\s*nie\b|jednak\s+nie|zostaw|nie\s+r[oó]b", raw, re.I) and not env.needs_tools:
        env.negated = bool(env.negated or True)
    if env.hypothetical and env.needs_tools:
        env.needs_tools = False
        env.allowed_domains = []
        if env.mode in (DecisionMode.ACT.value, DecisionMode.PLAN.value):
            env.mode = DecisionMode.ANSWER.value
        env.safety_notes = list(env.safety_notes) + ["hypothetical_no_side_effect"]
    if env.quoted and env.needs_tools:
        env.needs_tools = False
        env.allowed_domains = []
        if env.mode in (DecisionMode.ACT.value, DecisionMode.PLAN.value):
            env.mode = DecisionMode.ANSWER.value
        env.safety_notes = list(env.safety_notes) + ["quoted_no_side_effect"]
    return env


def apply_utterance_goal_semantics(
    env: DecisionEnvelope,
    text: str,
    *,
    conversation: Optional[Dict[str, Any]] = None,
) -> DecisionEnvelope:
    """Overlay utterance-class / ActiveGoal meaning onto pass-1 DecisionEnvelope.

    intent_family stays a signal. This layer routes natural follow-ups that would
    otherwise collapse to general_conversation / smalltalk.
    """
    try:
        from core.utterance_class import UtteranceClass, classify_utterance
    except Exception:
        return env

    conv = dict(conversation or {})
    cls = classify_utterance(text, conversation=conv)
    env.utterance_class = cls.utterance_class or env.utterance_class
    if cls.parameter_hint and not env.parameter_hint:
        env.parameter_hint = cls.parameter_hint
    if cls.signals and "unresolved_awareness" in cls.signals:
        env.unresolved_awareness = True

    active = conv.get("active_goal") if isinstance(conv.get("active_goal"), dict) else None
    if active is None and isinstance(conv.get("recent_goal"), dict):
        active = conv.get("recent_goal")
    unresolved = bool(conv.get("unresolved_goal") or conv.get("pending_executable"))
    has_goal = bool(
        conv.get("has_active_goal")
        or conv.get("has_recent_goal")
        or active
        or unresolved
    )
    objective = ""
    if isinstance(active, dict):
        objective = str(active.get("objective") or active.get("discussed_action") or "")
    env.goal_objective = env.goal_objective or objective or env.user_goal
    if isinstance(active, dict) and active.get("status"):
        env.goal_status = str(active.get("status") or "")

    uclass = cls.utterance_class

    # Pass-1 cancel/stop (and utterance REJECTION) wins over referential continuation.
    # Observed: "anuluj to" -> cancel_requested=True but REFERENCE -> execute_discussed_goal.
    if (
        env.cancel_requested
        or env.intent in ("cancel", "cancel_goal")
        or uclass == UtteranceClass.REJECTION.value
    ):
        env.mode = DecisionMode.WAIT.value
        env.intent = "cancel_goal"
        env.needs_tools = False
        env.continuation = True
        env.cancel_requested = True
        env.confidence = max(env.confidence, 0.9)
        env.user_goal = "Cancel the current goal"
        env.followup_kind = env.followup_kind or "reject_suggestion"
        env.utterance_class = UtteranceClass.REJECTION.value
        return env

    # WHY_FAILURE — answer path, never smalltalk / never invent tools.
    if uclass == UtteranceClass.WHY_FAILURE.value:
        env.mode = DecisionMode.ANSWER.value
        env.intent = "why_failure"
        env.needs_tools = False
        env.continuation = True
        env.context_requested = True
        env.confidence = max(env.confidence, cls.confidence)
        env.user_goal = "Explain the previous failure from verified results"
        env.followup_kind = env.followup_kind or "ask_why_failure"
        env.unresolved_awareness = True
        env.allowed_domains = []
        return env

    # Unresolved awareness ("jeszcze tego nie zrobiłeś") — resume goal, not chat.
    if env.unresolved_awareness or (
        uclass == UtteranceClass.REFERENCE.value and "unresolved_awareness" in cls.signals
    ):
        env.mode = DecisionMode.ACT.value if (unresolved or has_goal) else DecisionMode.ASK.value
        env.intent = "resume_unresolved_goal"
        env.needs_tools = bool(unresolved or has_goal)
        env.continuation = True
        env.context_requested = True
        env.confidence = max(env.confidence, cls.confidence)
        env.user_goal = objective or env.user_goal or str(text or "")
        env.unresolved_awareness = True
        if env.needs_tools and not env.allowed_domains and isinstance(active, dict):
            domain = str(active.get("domain") or "")
            if domain:
                env.allowed_domains = [domain]
        return env

    if uclass == UtteranceClass.RETRY.value and (has_goal or unresolved):
        env.mode = DecisionMode.ACT.value
        env.intent = "retry_goal"
        env.needs_tools = True
        env.continuation = True
        env.context_requested = True
        env.confidence = max(env.confidence, 0.9)
        env.user_goal = objective or env.user_goal or "Retry the previous goal differently"
        env.followup_kind = env.followup_kind or "retry"
        if isinstance(active, dict) and active.get("domain") and not env.allowed_domains:
            env.allowed_domains = [str(active.get("domain"))]
        return env

    # Confirm / "do it" after a weather ask — re-run weather tools (EN+PL).
    # Must run before generic execute_discussed_goal so FastPath can take weather_status.
    if uclass == UtteranceClass.CONFIRMATION.value and str(
        conv.get("last_intent") or ""
    ) == "weather_status":
        env.mode = DecisionMode.ANSWER.value
        env.intent = "weather_status"
        env.needs_tools = True
        env.continuation = True
        env.context_requested = True
        env.confidence = max(env.confidence, 0.93)
        env.utterance_class = UtteranceClass.ACTION_REQUEST.value
        env.user_goal = objective or env.user_goal or "Check the weather"
        env.followup_kind = env.followup_kind or "approve_execute"
        env.safety_notes = list(env.safety_notes or []) + ["read_only"]
        return env

    if uclass == UtteranceClass.CONFIRMATION.value and (has_goal or conv.get("discussed_action")):
        env.mode = DecisionMode.ACT.value
        env.intent = "execute_discussed_goal"
        env.needs_tools = True
        env.continuation = True
        env.context_requested = True
        env.confidence = max(env.confidence, 0.9)
        env.user_goal = objective or env.user_goal or str(text or "")
        env.followup_kind = env.followup_kind or "approve_execute"
        if isinstance(active, dict) and active.get("domain") and not env.allowed_domains:
            env.allowed_domains = [str(active.get("domain"))]
        return env

    if uclass == UtteranceClass.CHANGE_PARAMETER.value and (
        has_goal
        or conv.get("active_parameter")
        or conv.get("last_parameter")
        or conv.get("has_recent_goal")
        or cls.confidence >= 0.9
    ):
        domain = ""
        if isinstance(active, dict):
            domain = str(active.get("domain") or "")
        param = ""
        if isinstance(active, dict):
            param = str(active.get("parameter") or "")
        hint = cls.parameter_hint or env.parameter_hint
        # Bare volume words keep SYSTEM even if an HA brightness goal is open.
        if env.intent == "system_volume" or _VOLUME_BARE.match(_core(text)):
            env.utterance_class = uclass
            env.parameter_hint = hint or env.parameter_hint
            return env
        if param == "brightness" or hint in ("brighter", "darker") or domain == "SMART_HOME":
            env.mode = DecisionMode.ACT.value
            env.intent = "smart_home_control"
            env.needs_tools = True
            env.allowed_domains = ["SMART_HOME"]
            env.continuation = True
            env.context_requested = True
            env.confidence = max(env.confidence, 0.9)
            env.parameter_hint = hint
            if hint in ("brighter", "more", "louder"):
                env.user_goal = "Increase brightness of the active light"
            else:
                env.user_goal = "Decrease brightness of the active light"
            return env
        if param == "volume" or hint in ("louder", "quieter", "more", "less"):
            env.mode = DecisionMode.ACT.value
            env.intent = "system_volume"
            env.needs_tools = True
            env.allowed_domains = ["SYSTEM"]
            env.continuation = True
            env.confidence = max(env.confidence, 0.9)
            env.parameter_hint = hint
            env.user_goal = "Adjust system volume"
            return env

    if uclass == UtteranceClass.GOAL.value and cls.parameter_hint in ("brighter", "darker"):
        if env.mode in (DecisionMode.ASK.value, DecisionMode.TALK.value) or env.confidence < CONFIDENCE_USABLE:
            env.mode = DecisionMode.ACT.value
            env.intent = "smart_home_control"
            env.needs_tools = True
            env.allowed_domains = ["SMART_HOME"]
            env.confidence = max(env.confidence, 0.9)
            env.needs_clarification = False
            env.parameter_hint = cls.parameter_hint
            env.user_goal = str(text or "")
            env.goal_objective = env.user_goal
        return env

    if uclass == UtteranceClass.GOAL.value and "computer_prep" in (cls.signals or []):
        env.mode = DecisionMode.ACT.value
        env.intent = "procedure_run"
        env.needs_tools = True
        env.allowed_domains = ["COMPUTER", "APP"]
        env.confidence = max(env.confidence, 0.9)
        env.needs_clarification = False
        env.user_goal = str(text or "Przygotuj komputer do grania")
        env.goal_objective = env.user_goal
        return env

    if uclass == UtteranceClass.CORRECTION.value:
        domain = ""
        if isinstance(active, dict):
            domain = str(active.get("domain") or "")
        # Never turn a cancel/wait dismiss into a correction ask.
        if env.cancel_requested or env.mode == DecisionMode.WAIT.value:
            env.utterance_class = UtteranceClass.REJECTION.value
            return env
        env.correction = True
        env.continuation = True
        env.context_requested = True
        env.confidence = max(env.confidence, cls.confidence)
        env.followup_kind = env.followup_kind or "correction"
        # Pass-1 already resolved an actionable retarget (named app, ordinal, visual pick).
        if (
            env.needs_tools
            and env.mode in (DecisionMode.ACT.value, DecisionMode.PLAN.value)
            and env.confidence >= CONFIDENCE_USABLE
        ):
            env.utterance_class = uclass
            return env
        env.mode = DecisionMode.ACT.value if (has_goal or domain) else DecisionMode.ASK.value
        env.intent = "correction"
        try:
            from core.utterance import extract_correction_target

            retarget = extract_correction_target(str(text or ""))
        except Exception:
            retarget = ""
        if retarget:
            env.user_goal = retarget
            env.goal_objective = retarget
            env.needs_tools = True
            # Named app retarget → APP domain when prior goal was APP/open.
            if domain in ("APP", "MEDIA", "") or not domain:
                low = retarget.lower()
                if any(k in low for k in ("spotify", "discord", "chrome", "firefox", "steam", "notepad", "notatnik")):
                    env.allowed_domains = env.allowed_domains or ["APP"]
                    env.intent = "open_app"
        else:
            # Prefer prior primary objective / raw text — never invent choice boilerplate.
            prior = ""
            if isinstance(active, dict):
                prior = str(active.get("objective") or "")
            try:
                from core.active_goal import ConversationGoalStore

                if ConversationGoalStore._is_choice_boilerplate(prior):
                    prior = ""
            except Exception:
                pass
            env.user_goal = prior or objective or env.user_goal or str(text or "")[:240]
        if domain == "MEDIA":
            env.needs_tools = True
            env.allowed_domains = ["MEDIA"]
            env.intent = "play_media"
            env.user_goal = "Reject current media and try another"
        elif domain == "SMART_HOME":
            env.needs_tools = True
            env.allowed_domains = ["SMART_HOME"]
            env.intent = "smart_home_control"
        elif has_goal:
            env.needs_tools = True
            if domain and not env.allowed_domains:
                env.allowed_domains = [domain]
        else:
            env.needs_tools = False
            env.mode = DecisionMode.ASK.value
        return env

    if uclass == UtteranceClass.REJECTION.value:
        # Idle dismiss ("nieważne") is WAIT; CM still clears open goals via intent.
        env.mode = DecisionMode.WAIT.value
        env.intent = "cancel_goal"
        env.needs_tools = False
        env.continuation = True
        env.cancel_requested = True
        env.confidence = max(env.confidence, 0.9)
        env.user_goal = "Cancel the current goal"
        env.followup_kind = env.followup_kind or "reject_suggestion"
        return env

    if uclass == UtteranceClass.INFORMATION_REQUEST.value and "media_status" in (cls.signals or []):
        env.mode = DecisionMode.OBSERVE.value
        env.intent = "media_status"
        env.needs_tools = True
        env.allowed_domains = ["MEDIA"]
        env.requires_fresh_observation = True
        env.confidence = max(env.confidence, 0.9)
        env.user_goal = "What is playing now"
        return env

    if uclass == UtteranceClass.ACTION_REQUEST.value and "vague_media" in (cls.signals or []):
        env.mode = DecisionMode.ACT.value
        env.intent = "play_media"
        env.needs_tools = True
        env.allowed_domains = ["MEDIA"]
        env.confidence = max(env.confidence, 0.86)
        env.needs_clarification = False
        env.user_goal = str(text or "Puść coś")
        return env

    if uclass == UtteranceClass.ACTION_REQUEST.value and "search_action" in (cls.signals or []):
        if env.mode in (DecisionMode.TALK.value, DecisionMode.ASK.value) or not env.needs_tools:
            env.mode = DecisionMode.ACT.value
            env.intent = "web"
            env.needs_tools = True
            env.allowed_domains = ["WEB"]
            env.confidence = max(env.confidence, 0.9)
            env.needs_clarification = False
            env.user_goal = str(text or "")
        return env

    if uclass == UtteranceClass.CONTINUATION.value and (
        has_goal or conv.get("has_recent_goal") or conv.get("discussed_action")
    ):
        # After explicit cancel, bare kontynuuj must ASK — never revive cancelled goal.
        cancelled_goal = str(
            env.goal_status
            or (active or {}).get("status")
            or (conv.get("recent_goal") or {}).get("status")
            or ""
        ).upper() == "CANCELLED"
        if cancelled_goal or bool(conv.get("goal_cancelled")):
            env.mode = DecisionMode.ASK.value
            env.intent = "ask_after_cancel"
            env.needs_tools = False
            env.continuation = False
            env.context_requested = False
            env.confidence = max(env.confidence, 0.9)
            env.user_goal = "Zapytaj, co mam zrobić"
            env.allowed_domains = []
            env.followup_kind = "ask_after_cancel"
            return env
        # Fresh device act from pass-1 (pronoun dim/on/off) is not "do the prior goal again".
        try:
            from core.device_pronoun import detect_device_action_hint, has_device_pronoun

            _device_follow = has_device_pronoun(str(text or "")) and bool(
                detect_device_action_hint(str(text or ""))
            )
        except Exception:
            _device_follow = False
        if (
            env.intent == "smart_home_control"
            and env.needs_tools
            and env.mode == DecisionMode.ACT.value
        ) or (
            _device_follow
            and (
                str((active or {}).get("domain") or conv.get("recent_domain") or "").upper()
                in ("SMART_HOME", "LIGHT", "")
            )
        ):
            env.mode = DecisionMode.ACT.value
            env.intent = "smart_home_control"
            env.needs_tools = True
            env.allowed_domains = ["SMART_HOME"]
            env.continuation = True
            env.context_requested = True
            env.confidence = max(env.confidence, 0.9)
            env.user_goal = str(text or "") or env.user_goal
            return env
        env.mode = DecisionMode.ACT.value
        if "repeat_prior" in (cls.signals or []):
            env.intent = "execute_discussed_goal"
        else:
            env.intent = "resume_unresolved_goal" if unresolved else "execute_discussed_goal"
        env.needs_tools = True
        env.continuation = True
        env.context_requested = True
        env.confidence = max(env.confidence, 0.86)
        if not objective and isinstance(conv.get("recent_goal"), dict):
            objective = str(
                (conv.get("recent_goal") or {}).get("objective")
                or (conv.get("recent_goal") or {}).get("discussed_action")
                or ""
            )
            env.goal_objective = objective or env.goal_objective
        env.user_goal = objective or env.user_goal or str(text or "")
        domain = ""
        if isinstance(active, dict):
            domain = str(active.get("domain") or "")
        if not domain and isinstance(conv.get("recent_goal"), dict):
            domain = str((conv.get("recent_goal") or {}).get("domain") or "")
        if domain and not env.allowed_domains:
            env.allowed_domains = [domain]
        return env

    if uclass == UtteranceClass.REFERENCE.value and cls.ordinal and (
        conv.get("presented_options") or (isinstance(active, dict) and active.get("presented_options"))
    ):
        env.mode = DecisionMode.ACT.value
        env.intent = "pick_presented_option"
        env.needs_tools = True
        env.continuation = True
        env.context_requested = True
        env.confidence = max(env.confidence, 0.9)
        env.followup_kind = env.followup_kind or "pick_option"
        env.payload = cls.ordinal
        env.user_goal = f"Select the {cls.ordinal} presented option"
        return env

    if uclass == UtteranceClass.INFORMATION_REQUEST.value and "more_about_referent" in cls.signals:
        env.mode = DecisionMode.ACT.value
        env.intent = "web"
        env.needs_tools = True
        env.allowed_domains = ["WEB"]
        env.continuation = True
        env.context_requested = True
        env.confidence = max(env.confidence, 0.88)
        ref = ""
        if isinstance(active, dict):
            ref = str(active.get("target") or active.get("objective") or "")
        env.user_goal = f"Wyszukaj coś więcej o: {ref or 'tym'}"
        env.referents = list(env.referents) + ["current_topic"]
        return env

    # Stamp class even when mode already strong.
    if not env.utterance_class:
        env.utterance_class = uclass
    return env



def _discourse_blocks_learned(text: str, conversation: Optional[Dict[str, Any]] = None) -> bool:
    """Learned recipes must not shadow discourse follow-ups / volume degrees."""
    raw = str(text or "").strip()
    if not raw:
        return False
    if _VOLUME_BARE.match(_core(raw)):
        return True
    try:
        from core.utterance_class import UtteranceClass, classify_utterance

        cls = classify_utterance(raw, conversation=conversation)
        if cls.utterance_class in (
            UtteranceClass.CHANGE_PARAMETER.value,
            UtteranceClass.RETRY.value,
            UtteranceClass.WHY_FAILURE.value,
            UtteranceClass.CONFIRMATION.value,
            UtteranceClass.REJECTION.value,
            UtteranceClass.CORRECTION.value,
            UtteranceClass.CONTINUATION.value,
        ):
            return True
        if "relative_dampen" in (cls.signals or []) or "repeat_prior" in (cls.signals or []):
            return True
        if cls.parameter_hint in ("brighter", "darker", "louder", "quieter", "more", "less"):
            return True
    except Exception:
        pass
    return False


def decide_semantics(
    text: str,
    *,
    runtime_context: Optional[Dict[str, Any]] = None,
    conversation: Optional[Dict[str, Any]] = None,
    model=None,
    allow_llm: bool = True,
) -> DecisionEnvelope:
    """Canonical semantic decision. Pass 1 ignores execution context."""
    try:
        from memory.vocabulary import apply_aliases

        text = apply_aliases(text) or text
    except Exception:
        pass
    try:
        from core.learned_routines import lookup_learned_routine, should_auto_learn

        # Do not let auto-learned phrases shadow strong deterministic intents
        # (e.g. bare "głośniej" must stay system_volume for FastPath).
        if (
            should_auto_learn(text)
            and lookup_learned_routine(text) is not None
            and not _VOLUME_BARE.match(_core(text))
            and not looks_creative_humor(text)
            and not _discourse_blocks_learned(text, conversation)
        ):
            return stamp_discourse_flags(
                DecisionEnvelope(
                    mode=DecisionMode.ACT.value,
                    intent="learned_routine",
                    user_goal=str(text or ""),
                    confidence=0.95,
                    needs_tools=True,
                    safety_notes=["learned_user_phrase"],
                    response_style="concise",
                    utterance_class="ACTION_REQUEST",
                ),
                text,
            )
    except Exception:
        pass
    env = classify_pass1(text, conversation=conversation)
    env = apply_utterance_goal_semantics(env, text, conversation=conversation)
    if env.context_requested or (env.continuation and env.needs_tools):
        env.context_requested = True
        env = refine_with_context(env, runtime_context)
    else:
        env.context_requested = False

    weak = env.confidence < CONFIDENCE_USABLE
    # TALK/ANSWER without tools is already a decision. Do not stall RC on a
    # second Ollama pass just because general_conversation defaults to 0.80.
    conversational = (
        env.mode in (DecisionMode.TALK.value, DecisionMode.ANSWER.value)
        and not env.needs_tools
        and env.intent not in ("why_failure",)
    )
    goal_locked = env.intent in (
        "why_failure",
        "resume_unresolved_goal",
        "retry_goal",
        "execute_discussed_goal",
        "pick_presented_option",
    ) or env.unresolved_awareness
    if (
        allow_llm
        and model is not None
        and weak
        and not conversational
        and not goal_locked
        and env.mode != DecisionMode.WAIT.value
    ):
        llm = _llm_semantic_pass(_core(text), model)
        if llm is not None:
            # Model may not invent ACT when pass-1 already locked TALK/humor.
            if env.intent == "creative_humor" and llm.needs_tools:
                llm.needs_tools = False
                llm.mode = DecisionMode.TALK.value
                llm.allowed_domains = []
            # Preserve discourse class if LLM omits it.
            if env.utterance_class and not llm.utterance_class:
                llm.utterance_class = env.utterance_class
            if env.unresolved_awareness:
                llm.unresolved_awareness = True
            env = llm
            env = apply_utterance_goal_semantics(env, text, conversation=conversation)
            if env.context_requested or env.continuation:
                env = refine_with_context(env, runtime_context)

    if env.confidence < CONFIDENCE_USABLE and env.needs_tools:
        env.needs_tools = False
        env.mode = DecisionMode.ASK.value
        env.needs_clarification = True
        env.allowed_domains = []
        env.safety_notes = list(env.safety_notes) + ["weak_confidence_no_side_effect"]
    return stamp_discourse_flags(env, text)


def fast_path_permitted(text: str, envelope: Optional[DecisionEnvelope] = None) -> bool:
    """FastPath is an optimization, not a competing understanding engine."""
    env = envelope or classify_pass1(text)
    if looks_discussion_not_action(text) or looks_creative_humor(text):
        return False
    if looks_quoted_command(text) or looks_hypothetical(text):
        return False
    if getattr(env, "hypothetical", False) or getattr(env, "quoted", False):
        return False
    if env.mode in (
        DecisionMode.OBSERVE.value,
        DecisionMode.PLAN.value,
        DecisionMode.WAIT.value,
        DecisionMode.SIMULATE.value,
        DecisionMode.CANCEL.value,
        DecisionMode.RESUME.value,
        DecisionMode.PROACTIVE_DIALOGUE.value,
        DecisionMode.TALK.value,
    ):
        return False
    if getattr(env, "intent", "") == "why_failure":
        return False
    if getattr(env, "user_is_responding_to_elian", False) and str(
        getattr(env, "followup_kind", "") or ""
    ) in (
        "pick_option",
        "approve_recommended",
        "ask_why",
        "ask_why_failure",
        "ask_how",
        "ask_alternative",
        "ask_recommendation",
        "task_status",
        "return_to_task",
        "continue_conversation",
        "defer_proactive",
        "reject_suggestion",
        "no_task_status",
    ):
        return False
    if env.mode in (DecisionMode.TALK.value, DecisionMode.ANSWER.value) and env.intent in (
        "creative_humor",
        "social_smalltalk",
        "action_discussion",
        "conversational_followup",
        "knowledge_explanation",
        "task_status",
        "return_to_task",
        "decision_followup",
        "no_task_status",
        "defer_proactive",
        "general_conversation",
        "say_text",
        "why_failure",
    ):
        return False
    if env.intent == "smart_home_status" and env.needs_tools:
        return True
    if env.intent == "situation_briefing" and env.needs_tools:
        return True
    if env.intent == "learned_routine" and env.needs_tools:
        return True
    if env.intent == "procedure_run" and env.needs_tools:
        return True
    if env.intent == "unclassified_request":
        try:
            from core.learned_routines import lookup_learned_routine

            return lookup_learned_routine(text) is not None and env.needs_tools
        except Exception:
            return False
    if env.intent in (
        "home_overview",
        "today_schedule",
        "list_reminders",
        "weather_status",
        "missed_digest",
        "home_arrival",
        "list_notifications",
        "phone_status",
    ) and env.needs_tools:
        return True
    if env.mode != DecisionMode.ACT.value:
        return False
    if not env.needs_tools:
        return False
    # Agent is the default brain. FastPath only for rock-solid confidence.
    if env.confidence < CONFIDENCE_STRONG:
        return False
    return True


def is_obvious_fast_path_intent(
    envelope: Optional[DecisionEnvelope] = None,
    text: str = "",
) -> bool:
    """Narrow set of intents safe to skip Planner (volume / clear APP / briefing / complete HA)."""
    env = envelope
    if env is None:
        return False
    intent = str(getattr(env, "intent", "") or "")
    domains = {str(d).upper() for d in (getattr(env, "allowed_domains", None) or [])}
    if intent in (
        "situation_briefing",
        "smart_home_status",
        "home_overview",
        "today_schedule",
        "list_reminders",
        "weather_status",
        "missed_digest",
        "list_notifications",
        "phone_status",
        "learned_routine",
        "procedure_run",
        "home_arrival",
    ):
        return True
    if intent in ("volume_control", "system_volume") or (
        "SYSTEM" in domains and domains <= {"SYSTEM"}
    ):
        return True
    if intent.startswith("volume"):
        return True
    if intent in ("open_app", "close_app", "play_media", "app", "app_control", "media_control"):
        if not domains or domains <= {"APP", "MEDIA"}:
            return True
    if domains <= {"APP"} or domains <= {"MEDIA"} or domains <= {"APP", "MEDIA"}:
        if intent in (
            "open_app",
            "close_app",
            "play_media",
            "app",
            "app_control",
            "media_control",
            "",
        ):
            return True
    # Complete single CommandParser hit (incl. light/climate) — still Agent for soft/vague.
    raw = str(text or "").strip()
    if raw and (
        "SMART_HOME" in domains
        or intent in ("smart_home_control", "climate_control", "unclassified_request")
    ):
        try:
            from CommandParser import parse_commands

            cmds = parse_commands(raw) or []
            if len(cmds) == 1:
                cmd = cmds[0]
                tool = (
                    cmd.get("tool") or cmd.get("action")
                    if isinstance(cmd, dict)
                    else getattr(cmd, "action", None) or getattr(cmd, "tool", None)
                )
                if tool:
                    return True
        except Exception:
            pass
    return False


def side_effects_allowed(envelope: DecisionEnvelope, *, extra_evidence: bool = False) -> bool:
    if not envelope.needs_tools:
        return False
    if envelope.mode not in (DecisionMode.ACT.value, DecisionMode.PLAN.value, DecisionMode.OBSERVE.value):
        return False
    if getattr(envelope, "hypothetical", False) or getattr(envelope, "quoted", False):
        return False
    if envelope.confidence >= CONFIDENCE_STRONG:
        return True
    if envelope.confidence >= CONFIDENCE_USABLE and (
        extra_evidence or envelope.mode == DecisionMode.OBSERVE.value
    ):
        return True
    return False


def log_semantic_trace(envelope: DecisionEnvelope) -> None:
    line = (
        f"[AI SEMANTIC] mode={envelope.mode} intent={envelope.intent} "
        f"confidence={envelope.confidence:.2f} goal={envelope.user_goal!r} "
        f"needs_tools={str(envelope.needs_tools).lower()} "
        f"allowed_domains={envelope.allowed_domains} "
        f"continuation={str(envelope.continuation).lower()} "
        f"referents={envelope.referents} "
        f"needs_clarification={str(envelope.needs_clarification).lower()} "
        f"context_requested={str(envelope.context_requested).lower()}"
        + (
            f" utterance_class={envelope.utterance_class}"
            if getattr(envelope, "utterance_class", "")
            else ""
        )
        + (
            f" followup_kind={envelope.followup_kind}"
            if getattr(envelope, "followup_kind", "")
            else ""
        )
    )
    print(line)
    log_turn(
        "ai_semantic",
        mode=envelope.mode,
        intent=envelope.intent,
        confidence=round(float(envelope.confidence), 3),
        goal=(envelope.user_goal or "")[:160],
        needs_tools=envelope.needs_tools,
        allowed_domains=",".join(envelope.allowed_domains),
        continuation=envelope.continuation,
        referents=",".join(envelope.referents),
        needs_clarification=envelope.needs_clarification,
        context_requested=envelope.context_requested,
        utterance_class=str(getattr(envelope, "utterance_class", "") or ""),
    )


def semantic_model_name() -> str:
    return str(get_config_value("ai", "ollama_model", default="llama3.1:8b", legacy_key="ollama_model") or "")


def fallback_when_unavailable(text: str, conversation: Optional[Dict[str, Any]] = None) -> DecisionEnvelope:
    """No provider: deterministic only. Never UNKNOWN → stale Spotify."""
    env = classify_pass1(text, conversation=conversation)
    env.source = "deterministic_fallback"
    env.extra_llm_call = False
    if env.confidence < CONFIDENCE_STRONG and env.needs_tools and env.mode != DecisionMode.OBSERVE.value:
        env.needs_tools = False
        env.mode = DecisionMode.TALK.value
        env.intent = "safe_conversation_fallback"
        env.allowed_domains = []
        env.safety_notes = list(env.safety_notes) + ["semantic_ai_unavailable"]
    return env
