# ==========================
# ELIAN — Cognitive Core (2.0.0-rc1)
# ==========================
"""Understand facts, intentions, importance, and mind-changes — without tools.

Sits after Semantic Executive (DecisionEnvelope) and before FastPath/Agent action
choice. Does not replace Semantic Executive, Planner, or ConversationManager.
Does not invoke ToolRegistry; only assesses and guides the existing pipeline.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from Diagnostics import log_turn

from Config import get_config_value


# ---------------------------------------------------------------------------
# Enumerations / assessment contract
# ---------------------------------------------------------------------------


class Importance(str, Enum):
    TRIVIAL = "TRIVIAL"
    LOW = "LOW"
    NORMAL = "NORMAL"
    IMPORTANT = "IMPORTANT"
    CRITICAL = "CRITICAL"


class Persistence(str, Enum):
    NONE = "NONE"
    SESSION = "SESSION"
    CANDIDATE = "CANDIDATE"
    TEMPORARY = "TEMPORARY"
    PERSISTENT = "PERSISTENT"


class TemporalScope(str, Enum):
    PAST = "PAST"
    PRESENT = "PRESENT"
    NEAR_FUTURE = "NEAR_FUTURE"
    FUTURE = "FUTURE"
    ONGOING = "ONGOING"
    NONE = "NONE"


class Actionability(str, Enum):
    NONE = "NONE"
    INFORMATIONAL = "INFORMATIONAL"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    ACTIONABLE = "ACTIONABLE"
    SCHEDULED = "SCHEDULED"


class CognitiveIntent(str, Enum):
    GOAL = "GOAL"
    FUTURE_INTENTION = "FUTURE_INTENTION"
    POSSIBLE_INTENTION = "POSSIBLE_INTENTION"
    REMINDER_REQUEST = "REMINDER_REQUEST"
    CASUAL_THOUGHT = "CASUAL_THOUGHT"
    PREFERENCE = "PREFERENCE"
    FACT = "FACT"
    CORRECTION = "CORRECTION"
    FORGET = "FORGET"
    INVALIDATION = "INVALIDATION"
    QUESTION = "QUESTION"
    ACTION_REQUEST = "ACTION_REQUEST"
    ACK = "ACK"
    UNKNOWN = "UNKNOWN"


# Short diagnostic codes — never raw chain-of-thought.
RATIONALE_CODES = (
    "USER_REQUESTED_REMINDER",
    "LOW_VALUE_CHAT",
    "TEMPORARY_FUTURE_INTENT",
    "EXPLICIT_FORGET",
    "PREFERENCE_UPDATE",
    "IMPORTANT_CONTEXT",
    "GOAL_UPDATE",
    "CORRECTION",
    "NO_ACTION_REQUIRED",
    "EXPLICIT_REMEMBER",
    "CANDIDATE_PREFERENCE",
    "REINFORCED_PREFERENCE",
    "CHANGE_OF_MIND",
    "UNCERTAIN_FUTURE",
    "CONFLICTING_MEMORY",
    "IMPORTANT_DEADLINE",
    "CONTEXT_RESOLVED",
    "AMBIGUOUS_REFERENT",
    "DISABLE_REMINDER_RULE",
    "RECURRING_REMINDER_RULE",
)


@dataclass
class CognitiveAssessment:
    meaning: str = ""
    intent: str = CognitiveIntent.UNKNOWN.value
    importance: str = Importance.NORMAL.value
    confidence: float = 0.0
    persistence: str = Persistence.NONE.value
    temporal_scope: str = TemporalScope.NONE.value
    actionability: str = Actionability.NONE.value
    referenced_entities: List[str] = field(default_factory=list)
    related_memory_ids: List[str] = field(default_factory=list)
    related_goal_ids: List[str] = field(default_factory=list)
    should_remember: bool = False
    should_forget: bool = False
    should_act: bool = False
    should_ask: bool = False
    should_reply: bool = True
    suggested_next_step: str = "NO_ACTION"
    rationale_code: str = "NO_ACTION_REQUIRED"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["confidence"] = round(float(self.confidence), 3)
        return data


@dataclass
class CandidateMemory:
    """Observation → candidate → reinforce → optional persistent Memory 2.0."""

    id: str
    text: str
    kind: str = "fact"  # fact | preference | intention | rule
    importance: str = Importance.LOW.value
    confidence: float = 0.35
    confirmations: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0
    entities: List[str] = field(default_factory=list)
    status: str = "candidate"  # candidate | reinforced | promoted | invalidated
    key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TemporaryIntention:
    id: str
    text: str
    kind: str = CognitiveIntent.POSSIBLE_INTENTION.value
    importance: str = Importance.NORMAL.value
    certainty: str = "uncertain"  # uncertain | likely | committed
    created_at: float = 0.0
    entities: List[str] = field(default_factory=list)
    reminder_requested: bool = False
    reminder_time_hint: str = ""
    status: str = "active"  # active | reminded | invalidated | completed
    linked_memory_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReminderRule:
    """Persistent recurring reminder / procedure intent (not ToolRegistry)."""

    id: str
    text: str
    trigger: str = ""
    importance: str = Importance.IMPORTANT.value
    enabled: bool = True
    created_at: float = 0.0
    entities: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CognitiveGuidance:
    """Pipeline guidance — never executes tools."""

    assessment: CognitiveAssessment
    short_circuit_reply: str = ""
    enriched_goal: str = ""
    skip_fast_path: bool = False
    skip_agent_tools: bool = False
    route_chat: bool = False


@dataclass
class CognitiveState:
    """Session cognitive scratchpad. Cleared on deactivate."""

    candidates: List[CandidateMemory] = field(default_factory=list)
    intentions: List[TemporaryIntention] = field(default_factory=list)
    reminder_rules: List[ReminderRule] = field(default_factory=list)
    last_assessments: List[CognitiveAssessment] = field(default_factory=list)
    last_user_texts: List[str] = field(default_factory=list)

    def clear(self) -> None:
        self.candidates.clear()
        self.intentions.clear()
        self.reminder_rules.clear()
        self.last_assessments.clear()
        self.last_user_texts.clear()

    def active_intentions(self) -> List[TemporaryIntention]:
        return [item for item in self.intentions if item.status == "active"]

    def enabled_rules(self) -> List[ReminderRule]:
        return [item for item in self.reminder_rules if item.enabled]

    def has_cancelable_referent(self) -> bool:
        if self.active_intentions():
            return True
        if self.enabled_rules():
            return True
        if any(c.status in ("candidate", "reinforced") for c in self.candidates):
            return True
        return False


# ---------------------------------------------------------------------------
# Lightweight signal patterns (focused — not a command pile)
# ---------------------------------------------------------------------------

_UNCERTAIN = re.compile(
    r"\b(?:chyba|mo[zż]e|by[cć]\s+mo[zż]e|nie\s+wiem\s+czy|ewentualnie|"
    r"w\s+sumie(?!\s+przypomn)|prawdopodobnie|raczej)\b",
    re.I,
)
_OBLIGATION = re.compile(
    r"\b(?:musz[eę]|b[eę]d[eę]\s+musia[lł]|trzeba|mam\s+(?:zrobi[cć]|i[sś][cć]|by[cć])|"
    r"musisz\s+mi|powinienem|powinnam)\b",
    re.I,
)
_REMINDER = re.compile(
    r"\b(?:przypomnij|przypominaj|przypomina[cć]|przypomnienie|"
    r"nie\s+zapomnij\s+(?:mi\s+)?(?:przypomnie[cć]|o))\b",
    re.I,
)
_RECURRING = re.compile(
    r"(?:musisz\s+mi\s+przypomina[cć]|przypominaj\s+mi|codziennie|"
    r"przed\s+szko[lł][aą]|ka[zż]dego\s+(?:rana|dnia)|zawsze\s+mi\s+przypomin)",
    re.I,
)
_DISABLE_RULE = re.compile(
    r"(?:ju[zż]\s+nie\s+musisz(?:\s+mi)?(?:\s+o\s+tym)?\s+przypomina[cć]|"
    r"przesta[nń]\s+(?:mi\s+)?przypomina[cć]|"
    r"nie\s+przypominaj(?:\s+(?:mi|o\s+tym))?|"
    r"wy[lł][aą]cz\s+przypomnienie)",
    re.I,
)
_INVALIDATE = re.compile(
    r"(?:"
    r"\bniewa[zż]ne\b|"
    r"\bto\s+nieaktualne\b|"
    r"\bju[zż]\s+tego\s+nie\s+r[oó]b\b|"
    r"\bju[zż]\s+niewa[zż]ne\b|"
    r"\bdobra\s*,?\s*niewa[zż]ne\b|"
    r"\bniewa[zż]ne\s+z\s+tym\b|"
    r"\bw\s+sumie\s+(?:to\s+)?niewa[zż]ne\b|"
    r"\bzmieni[lł]em\s+zdanie\b|"
    r"\bzmieni[lł]am\s+zdanie\b|"
    r"\bjednak\s+nie\b|"
    r"\bjednak\s+niewa[zż]ne\b|"
    r"\bodosu[nń]\s+to\b|"
    r"\banuluj\s+(?:to|przypomnienie)\b"
    r")",
    re.I,
)
_EXPLICIT_FORGET = re.compile(
    r"\b(?:zapomnij(?:\s+o)?|nie\s+pami[eę]taj|usu[nń]\s+(?:t[eę]\s+)?informacj)\b",
    re.I,
)
_EXPLICIT_REMEMBER = re.compile(
    r"\b(?:zapami[eę]taj|pami[eę]taj(?:\s+[zż]e)?)\b",
    re.I,
)
_PREFERENCE = re.compile(
    r"\b(?:wol[eę]|lubi[eę]|preferuj[eę]|od\s+teraz|zawsze\s+u[zż]ywaj)\b",
    re.I,
)
_CORRECTION = re.compile(
    r"(?:"
    r"^\s*nie\s*,\s*chodzi[lł]o\s+mi\s+o\b|"
    r"^\s*chodzi[lł]o\s+mi\s+o\b|"
    r"^\s*nie\s+to\b|"
    r"\bpoprawka\b|"
    r"\bmyli[lł]em\s+si[eę]\b|"
    r"\bmyli[lł]am\s+si[eę]\b"
    r")",
    re.I,
)
_IMPORTANT = re.compile(
    r"\b(?:egzamin|deadline|termin|pilne|krytyczn|prezentacj|rozmowa\s+kwalifikacyjn|"
    r"operacj|szpital|lot|poci[aą]g|odbi[oó]r\s+dziecka)\b",
    re.I,
)
_DEADLINE = re.compile(
    r"\b(?:jutro|pojutrze|dzi[sś]|za\s+(?:godzin[eę]|2\s+godziny)|o\s+\d{1,2}(?::\d{2 dig?))\b"
    r".{0,40}\b(?:egzamin|deadline|termin|prezentacj|rozmow)\b|"
    r"\b(?:egzamin|deadline|termin|prezentacj).{0,40}"
    r"\b(?:jutro|pojutrze|dzi[sś]|o\s+\d{1,2})\b",
    re.I,
)
_TIME_HINT = re.compile(
    r"\b(?:o\s+\d{1,2}(?::\d{2})?|o\s+(?:szesnastej|pi[aą]tej|czwartej|trzeciej|"
    r"drugiej|pierwszej|dwunastej|jedenastej|dziesi[aą]tej|dziewi[aą]tej|"
    r"[oó]smej|si[oó]dmej|sz[oó]stej)|rano|wieczorem|po\s+po[lł]udniu)\b",
    re.I,
)
_FUTURE = re.compile(
    r"\b(?:jutro|pojutrze|p[oó][zź]niej|nied[lł]ugo|za\s+(?:tydzie[nń]|kilka\s+dni)|"
    r"w\s+przysz[lł]ym|b[eę]d[eę])\b",
    re.I,
)
_CASUAL = re.compile(
    r"(?:"
    r"\b(?:pizza|kebab|netflix|serial|mem|memy|pogoda\s+fajna|nudzi\s+mi\s+si[eę])\b|"
    r"^(?:hej|cze[sś][cć]|siema|jak\s+tam)[.!?…]*$"
    r")",
    re.I,
)
_DEVICE_FOLLOWUP = re.compile(
    r"(?:"
    r"^\s*na\s+\d+"
    r"|^\s*na\s+(?:sto|dziewi[eę][cć]dziesi[aą]t|osiemdziesi[aą]t|siedemdziesi[aą]t|"
    r"sze[sś][cć]dziesi[aą]t|pi[eę][cć]dziesi[aą]t|czterdzie[sś]ci|trzydzie[sś]ci|"
    r"dwadzie[sś]cia|pi[eę][cć]na[sś]cie|dziesi[eę][cć])\b"
    r"|\b(?:podnie[sś]|obni[zż]|ch[lł]odniej|cieplej|ja[sś]niej|ciemniej|"
    r"g[lł]o[sś]niej|ciszej|procent)\b"
    r"|^\s*(?:stop|na\s+[sś]rodek|na\s+g[oó]r[eę]|na\s+d[oó][lł]|teraz\s+\d+)\b"
    r"|\b(?:ustaw|w[lł][aą]cz|wy[lł][aą]cz|zga[sś])\b"
    r")",
    re.I,
)
_QUESTION = re.compile(
    r"(?:\?|^\s*(?:co|jak|dlaczego|czemu|kiedy|gdzie|czy)\b)",
    re.I,
)


def _enabled() -> bool:
    return bool(get_config_value("settings", "cognitive_core_enabled", default=True))


def _norm(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def _cf(text: str) -> str:
    return _norm(text).casefold()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def looks_invalidation(text: str) -> bool:
    raw = _norm(text)
    if not raw:
        return False
    if _DISABLE_RULE.search(raw):
        return True
    if _EXPLICIT_FORGET.search(raw):
        return True
    if _INVALIDATE.search(raw):
        return True
    return False


def has_active_referent(state: CognitiveState | None) -> bool:
    if state is None:
        return False
    return state.has_cancelable_referent()


def _extract_entities(text: str) -> List[str]:
    tokens = re.findall(r"[a-ząćęłńóśźżA-ZĄĆĘŁŃÓŚŹŻ]{3,}", text or "")
    stop = {
        "będę",
        "bedę",
        "bede",
        "muszę",
        "musze",
        "jutro",
        "chyba",
        "wsumie",
        "sumie",
        "przypomnij",
        "nieważne",
        "niewazne",
        "jednak",
        "tego",
        "może",
        "moze",
        "żebym",
        "zebym",
        "żeby",
        "zeby",
        "musisz",
        "przed",
        "szkołą",
        "szkola",
        "brał",
        "bral",
    }
    out: List[str] = []
    for tok in tokens:
        low = tok.casefold()
        if low in stop:
            continue
        if low not in out:
            out.append(low)
        if len(out) >= 8:
            break
    return out


def _importance_rank(value: str) -> int:
    order = {
        Importance.TRIVIAL.value: 0,
        Importance.LOW.value: 1,
        Importance.NORMAL.value: 2,
        Importance.IMPORTANT.value: 3,
        Importance.CRITICAL.value: 4,
    }
    return int(order.get(str(value or ""), 2))


def _score_intention_match(query: str, intention: TemporaryIntention) -> float:
    q_ents = set(_extract_entities(query))
    i_ents = set(intention.entities or _extract_entities(intention.text))
    if not q_ents and not i_ents:
        return 0.2
    if q_ents & i_ents:
        return 0.55 + 0.15 * len(q_ents & i_ents)
    q_cf = _cf(query)
    i_cf = _cf(intention.text)
    hits = sum(1 for ent in i_ents if ent in q_cf)
    if hits:
        return 0.4 + 0.1 * hits
    # bare invalidation with only one active intention
    if looks_invalidation(query) and len(q_ents) <= 2:
        return 0.35
    if "przypomn" in q_cf and intention.status == "active":
        return 0.45
    return 0.0


def resolve_intention_referent(
    text: str,
    state: CognitiveState,
) -> tuple[Optional[TemporaryIntention], bool]:
    """Return (intention, ambiguous). Ambiguous → ASK, never delete randomly."""
    active = state.active_intentions()
    if not active:
        return None, False
    scored = [(_score_intention_match(text, item), item) for item in active]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best = scored[0]
    if best_score < 0.3:
        return None, len(active) > 1
    if len(scored) > 1 and scored[1][0] >= best_score - 0.05 and scored[1][0] >= 0.35:
        return None, True
    if len(active) == 1 and looks_invalidation(text) and best_score >= 0.3:
        return best, False
    return best, False


def resolve_rule_referent(
    text: str,
    state: CognitiveState,
) -> tuple[Optional[ReminderRule], bool]:
    rules = state.enabled_rules()
    if not rules:
        return None, False
    q_ents = set(_extract_entities(text))
    scored: List[tuple[float, ReminderRule]] = []
    for rule in rules:
        r_ents = set(rule.entities or _extract_entities(rule.text))
        overlap = len(q_ents & r_ents)
        score = 0.2 * overlap
        if _DISABLE_RULE.search(text or ""):
            score += 0.4
        if overlap:
            score += 0.3
        scored.append((score, rule))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best = scored[0]
    if best_score < 0.35:
        return None, len(rules) > 1
    if len(scored) > 1 and scored[1][0] >= best_score - 0.05 and scored[1][0] >= 0.4:
        return None, True
    if len(rules) == 1 and _DISABLE_RULE.search(text or ""):
        return best, False
    return best, False


# ---------------------------------------------------------------------------
# Candidate memory lifecycle
# ---------------------------------------------------------------------------


def upsert_candidate(
    state: CognitiveState,
    text: str,
    *,
    kind: str = "fact",
    importance: str = Importance.LOW.value,
    confidence: float = 0.35,
    key: str = "",
    entities: Sequence[str] | None = None,
) -> CandidateMemory:
    now = time.time()
    key_norm = (key or ".".join(_extract_entities(text)[:4]) or "note").casefold()
    for item in state.candidates:
        if item.status == "invalidated":
            continue
        same_key = item.key.casefold() == key_norm
        same_kind = item.kind == kind
        if same_key and same_kind:
            item.confirmations += 1
            item.confidence = min(0.95, float(item.confidence) + 0.2)
            item.updated_at = now
            item.text = _norm(text) or item.text
            if item.confirmations >= 2:
                item.status = "reinforced"
                if _importance_rank(item.importance) < _importance_rank(Importance.NORMAL.value):
                    item.importance = Importance.NORMAL.value
            return item
    cand = CandidateMemory(
        id=_new_id("cand"),
        text=_norm(text),
        kind=kind,
        importance=importance,
        confidence=float(confidence),
        confirmations=1,
        created_at=now,
        updated_at=now,
        entities=list(entities or _extract_entities(text)),
        status="candidate",
        key=key_norm,
    )
    state.candidates.append(cand)
    return cand


def promote_candidate_to_memory(
    candidate: CandidateMemory,
    memory: Any,
    *,
    explicit: bool = False,
) -> Optional[str]:
    """Promote reinforced/explicit candidate into Memory 2.0. Returns entry id.

    Soft uncertain CandidateMemory stays session-only until reinforced (or
    explicit remember). Intention/temporary kinds never auto-promote.
    """
    if memory is None or not hasattr(memory, "remember"):
        return None
    if candidate.status == "invalidated":
        return None
    # Gate: soft/uncertain ≠ persistent. Reinforce OR explicit required.
    if not explicit and candidate.status != "reinforced":
        return None
    if candidate.kind in ("intention", "temporary", "session", "note") and not explicit:
        # Soft future / CHANGE_OF_MIND subjects stay in CognitiveState only.
        return None
    try:
        from memory.types import MemoryType

        kind_map = {
            "preference": MemoryType.PREFERENCE,
            "fact": MemoryType.FACT,
            "intention": MemoryType.TEMPORARY,
            "rule": MemoryType.ROUTINE,
        }
        mtype = kind_map.get(candidate.kind, MemoryType.FACT)
        conf = 1.0 if explicit else max(0.55, float(candidate.confidence))
        result = memory.remember(
            type=mtype,
            key=candidate.key or "note",
            value=candidate.text,
            text=candidate.text,
            confidence=conf,
            explicit=bool(explicit),
            importance=(
                0.9
                if candidate.importance in (Importance.IMPORTANT.value, Importance.CRITICAL.value)
                else 0.55
            ),
            provenance={
                "source": "cognitive_core",
                "candidate_id": candidate.id,
                "confirmations": candidate.confirmations,
            },
            ask_on_conflict=True,
        )
        status = getattr(result, "status", None)
        status_val = str(getattr(status, "value", status) or "")
        if status_val == "conflict_ask":
            return None
        entry = getattr(result, "entry", None) or {}
        entry_id = str((entry or {}).get("id") or "")
        if not entry_id and hasattr(memory, "typed"):
            found = memory.typed.find_by_key(candidate.key or "note", type=mtype)
            if isinstance(found, dict):
                entry_id = str(found.get("id") or "")
        if entry_id:
            candidate.status = "promoted"
        return entry_id or None
    except Exception:
        return None


def invalidate_intention(intention: TemporaryIntention) -> None:
    intention.status = "invalidated"


def invalidate_session_intention(
    state: "CognitiveState",
    intention: TemporaryIntention,
    memory: Any = None,
) -> None:
    """Invalidate session TemporaryIntention + linked candidates.

    Soft CHANGE_OF_MIND / "nieważne" must NOT wipe durable FACT / PREFERENCE /
    ROUTINE from Memory 2.0. Only explicit forget / DISABLE_RULE may touch disk.
    Also clears short-term focus topic when it mirrors the soft intent.
    """
    invalidate_intention(intention)
    linked = set(intention.linked_memory_ids or [])
    intent_ents = set(intention.entities or []) | set(_extract_entities(intention.text))
    for cand in state.candidates:
        if cand.id in linked:
            cand.status = "invalidated"
            continue
        if cand.status == "invalidated":
            continue
        # Same soft subject as the cancelled intention → drop session candidate.
        cand_ents = set(cand.entities or []) | set(_extract_entities(cand.text or cand.key or ""))
        if intent_ents and (intent_ents & cand_ents):
            if cand.kind in ("intention", "note", "temporary", "session", ""):
                cand.status = "invalidated"
    short = getattr(memory, "short", None) if memory is not None else None
    if short is not None:
        try:
            focus = getattr(short, "focus", None)
            if isinstance(focus, dict):
                topic = str(focus.get("current_topic") or "")
                topic_ents = set(_extract_entities(topic))
                mirrors = False
                if topic and (
                    _cf(topic) in _cf(intention.text)
                    or _cf(intention.text) in _cf(topic)
                ):
                    mirrors = True
                elif intent_ents and topic_ents and (intent_ents & topic_ents):
                    mirrors = True
                if mirrors:
                    focus["current_topic"] = ""
                    focus["last_question_summary"] = ""
                    focus["last_answer_summary"] = ""
            # Never short.clear() here — preserves device/app session entities.
        except Exception:
            pass
    # Soft cancel: mark matching TEMPORARY typed rows invalidated so retrieval
    # cannot resurface them. Never wipe durable FACT / PREFERENCE / ROUTINE.
    typed = getattr(memory, "typed", None) if memory is not None else None
    if typed is not None and hasattr(typed, "mark_status"):
        try:
            from memory.types import MemoryType

            marked = False
            for entry in list(typed.all(include_session=True) or []):
                kind = str(entry.get("type") or "").upper()
                if kind != MemoryType.TEMPORARY.value:
                    continue
                status = str(entry.get("status") or "active").lower()
                if status in ("invalidated", "cancelled", "forgotten", "expired"):
                    continue
                eid = str(entry.get("id") or "")
                prov = entry.get("provenance") if isinstance(entry.get("provenance"), dict) else {}
                cand_id = str(prov.get("candidate_id") or "")
                blob = f"{entry.get('text') or ''} {entry.get('value') or ''} {entry.get('key') or ''}"
                blob_ents = set(_extract_entities(blob))
                match = False
                if eid and eid in linked:
                    match = True
                elif cand_id and cand_id in linked:
                    match = True
                elif intent_ents and blob_ents and (intent_ents & blob_ents):
                    match = True
                elif intention.text and (
                    _cf(intention.text) in _cf(blob) or _cf(blob) in _cf(intention.text)
                ):
                    match = True
                if match:
                    if typed.mark_status(eid, "invalidated"):
                        marked = True
            if marked and hasattr(memory, "save"):
                try:
                    memory.save()
                except Exception:
                    pass
        except Exception:
            pass
    try:
        from core.world_state import note_session

        note_session(
            {
                "cognitive_invalidation": True,
                "invalidated_intention_id": intention.id,
                "invalidated_text": (intention.text or "")[:160],
            }
        )
    except Exception:
        pass


def disable_reminder_rule(rule: ReminderRule) -> None:
    rule.enabled = False


def _routine_is_cognitive_reminder(entry: dict) -> bool:
    """True for ROUTINE entries that mirror ReminderRule (not unrelated habits)."""
    if not isinstance(entry, dict):
        return False
    kind = str(entry.get('type') or '').upper()
    if kind != 'ROUTINE':
        return False
    status = str(entry.get('status') or 'active').lower()
    if status not in ('', 'active', 'enabled'):
        return False
    prov = entry.get('provenance') or {}
    if not isinstance(prov, dict):
        prov = {}
    source = str(prov.get('source') or '').casefold()
    if source in ('cognitive_core', 'reminder', 'reminder_rule'):
        return True
    # Explicit rule.* keys from promote_candidate_to_memory
    key = str(entry.get('key') or '').casefold()
    if key.startswith('rule.'):
        return True
    # Fallback: reminder-ish text written by cognitive core
    blob = f"{entry.get('text') or ''} {entry.get('value') or ''}".casefold()
    if 'przypomin' in blob:
        return True
    return False


def _reminder_rule_from_routine(entry: dict) -> ReminderRule:
    text = _norm(str(entry.get('text') or entry.get('value') or ''))
    entities = list(entry.get('entities') or []) or _extract_entities(text)
    created = 0.0
    raw_created = entry.get('created_at')
    if isinstance(raw_created, (int, float)):
        created = float(raw_created)
    else:
        created = time.time()
    entry_id = str(entry.get('id') or '') or _new_id('rule')
    return ReminderRule(
        id=f"rule_rehyd_{entry_id[-12:]}" if not entry_id.startswith('rule_') else entry_id,
        text=text,
        trigger=text,
        importance=Importance.IMPORTANT.value,
        enabled=True,
        created_at=created,
        entities=entities,
    )


def rehydrate_reminder_rules_from_memory(
    state: CognitiveState,
    memory: Any,
) -> List[ReminderRule]:
    """Load active ROUTINE (cognitive_core / reminder) into session reminder_rules.

    Safe to call when CognitiveState is fresh after cold start. Skips duplicates
    by overlapping entities/text. Does not clear existing enabled session rules.
    """
    if state is None or memory is None:
        return []
    typed = getattr(memory, 'typed', None)
    if typed is None or not hasattr(typed, 'all'):
        return []
    try:
        entries = typed.all(include_session=False) or []
    except Exception:
        return []
    existing_blobs = {
        _cf(f"{r.text} {' '.join(r.entities or [])}")
        for r in state.reminder_rules
        if r.enabled
    }
    existing_ids = {r.id for r in state.reminder_rules}
    loaded: List[ReminderRule] = []
    for entry in entries:
        if not _routine_is_cognitive_reminder(entry):
            continue
        rule = _reminder_rule_from_routine(entry)
        blob = _cf(f"{rule.text} {' '.join(rule.entities or [])}")
        if rule.id in existing_ids or blob in existing_blobs:
            continue
        state.reminder_rules.append(rule)
        existing_ids.add(rule.id)
        existing_blobs.add(blob)
        loaded.append(rule)
    return loaded


def _subject_query_for_disable(text: str) -> str:
    """Extract subject fragment for ROUTINE forget (e.g. 'ładowarce')."""
    raw = _norm(text)
    # "... o X" / "... o tym X"
    m = re.search(
        r"\bo\s+(?:tym\s+)?(.+)$",
        raw,
        flags=re.I,
    )
    if m:
        subj = _norm(m.group(1))
        subj = re.sub(r"[.!?…]+$", '', subj).strip()
        if subj and subj.casefold() not in ('tym', 'tym przypominać', 'tym przypominac'):
            return subj
    ents = _extract_entities(raw)
    # Drop disable-ish tokens
    drop = {
        'przypominaj', 'przypominać', 'przypominac', 'przypomnienie',
        'już', 'juz', 'musisz', 'przestań', 'przestan', 'wyłącz', 'wylacz',
    }
    kept = [e for e in ents if e not in drop]
    if kept:
        return kept[-1]
    return raw


def forget_matching_reminder_routines(
    memory: Any,
    text: str,
) -> List[dict]:
    """Forget/deactivate durable ROUTINE entries matching disable subject."""
    if memory is None:
        return []
    subject = _subject_query_for_disable(text)
    removed: List[dict] = []
    typed = getattr(memory, 'typed', None)
    # Prefer precise typed forget_by_id after filtering ROUTINE matches
    if typed is not None and hasattr(typed, 'all'):
        try:
            entries = typed.all(include_session=False) or []
        except Exception:
            entries = []
        subj_cf = _cf(subject)
        subj_ents = set(_extract_entities(subject)) | set(_extract_entities(text))
        # Drop stopwords that appear in disable utterances
        for noise in (
            'przypominaj', 'przypominać', 'przypominac', 'przypomnienie',
            'już', 'juz', 'musisz', 'nie',
        ):
            subj_ents.discard(noise)
        targets = []
        for entry in entries:
            if not _routine_is_cognitive_reminder(entry):
                continue
            blob = _cf(f"{entry.get('key') or ''} {entry.get('value') or ''} {entry.get('text') or ''}")
            ents = set(_extract_entities(blob))
            overlap = len(subj_ents & ents) if subj_ents else 0
            match = False
            if subj_cf and len(subj_cf) >= 3 and subj_cf in blob:
                match = True
            elif overlap >= 1:
                # Stem-ish: ładowarce / ładowarka share prefix
                match = True
            else:
                # Prefix stem overlap for Polish inflections
                for a in subj_ents:
                    for b in ents:
                        if len(a) >= 5 and len(b) >= 5 and (a[:5] == b[:5] or a.startswith(b[:4]) or b.startswith(a[:4])):
                            match = True
                            break
                    if match:
                        break
            if match:
                targets.append(entry)
        for entry in targets:
            entry_id = str(entry.get('id') or '')
            got = None
            if entry_id and hasattr(typed, 'forget_by_id'):
                try:
                    got = typed.forget_by_id(entry_id)
                except Exception:
                    got = None
            if got is None and hasattr(memory, 'forget'):
                try:
                    q = str(entry.get('text') or entry.get('value') or subject)
                    memory.forget(q)
                    got = entry
                except Exception:
                    got = None
            if got:
                removed.append(dict(got) if isinstance(got, dict) else dict(entry))
        if removed and hasattr(memory, 'save'):
            try:
                memory.save()
            except Exception:
                pass
        return removed
    if hasattr(memory, 'forget') and subject:
        try:
            got = memory.forget(subject) or []
            if isinstance(got, list):
                removed.extend([g for g in got if isinstance(g, dict)])
            elif got:
                removed.append({'query': subject})
        except Exception:
            pass
    return removed


# ---------------------------------------------------------------------------
# Core assessment
# ---------------------------------------------------------------------------


def _base_from_envelope(envelope: Any) -> Dict[str, Any]:
    if envelope is None:
        return {}
    return {
        "mode": str(getattr(envelope, "mode", "") or ""),
        "intent": str(getattr(envelope, "intent", "") or ""),
        "needs_tools": bool(getattr(envelope, "needs_tools", False)),
        "correction": bool(getattr(envelope, "correction", False)),
        "hypothetical": bool(getattr(envelope, "hypothetical", False)),
        "confidence": float(getattr(envelope, "confidence", 0.0) or 0.0),
        "user_goal": str(getattr(envelope, "user_goal", "") or ""),
    }


def assess(
    text: str,
    *,
    envelope: Any = None,
    state: CognitiveState | None = None,
    memory: Any = None,
    recent_user: Sequence[str] | None = None,
) -> CognitiveAssessment:
    """Produce a CognitiveAssessment. Pure-ish: may read state/memory, not mutate."""
    raw = _norm(text)
    lowered = _cf(raw)
    env = _base_from_envelope(envelope)
    entities = _extract_entities(raw)
    meaning = env.get("user_goal") or raw

    assessment = CognitiveAssessment(
        meaning=meaning[:240],
        referenced_entities=entities,
        confidence=0.55,
    )

    if not raw:
        assessment.intent = CognitiveIntent.ACK.value
        assessment.importance = Importance.TRIVIAL.value
        assessment.rationale_code = "NO_ACTION_REQUIRED"
        assessment.suggested_next_step = "NO_ACTION"
        assessment.should_reply = False
        return assessment

    # --- Disable recurring reminder rule ---
    if _DISABLE_RULE.search(raw):
        assessment.intent = CognitiveIntent.INVALIDATION.value
        assessment.importance = Importance.IMPORTANT.value
        assessment.persistence = Persistence.PERSISTENT.value
        assessment.actionability = Actionability.ACTIONABLE.value
        assessment.should_forget = True
        assessment.should_act = False
        assessment.rationale_code = "DISABLE_REMINDER_RULE"
        assessment.suggested_next_step = "DISABLE_RULE"
        assessment.confidence = 0.9
        if state is not None:
            rule, ambiguous = resolve_rule_referent(raw, state)
            if ambiguous:
                assessment.should_ask = True
                assessment.suggested_next_step = "ASK"
                assessment.rationale_code = "AMBIGUOUS_REFERENT"
            elif rule is not None:
                assessment.related_goal_ids = [rule.id]
                assessment.referenced_entities = list(
                    dict.fromkeys(entities + list(rule.entities or []))
                )
            elif state.enabled_rules():
                assessment.should_ask = True
                assessment.suggested_next_step = "ASK"
                assessment.rationale_code = "AMBIGUOUS_REFERENT"
        return assessment

    # --- Explicit forget ---
    if _EXPLICIT_FORGET.search(raw):
        assessment.intent = CognitiveIntent.FORGET.value
        assessment.importance = Importance.NORMAL.value
        assessment.should_forget = True
        assessment.should_act = False
        assessment.persistence = Persistence.NONE.value
        assessment.rationale_code = "EXPLICIT_FORGET"
        assessment.suggested_next_step = "FORGET"
        assessment.confidence = 0.88
        if memory is not None and hasattr(memory, "retrieve"):
            try:
                hits = memory.retrieve(raw, limit=5) or []
                assessment.related_memory_ids = [
                    str(item.get("id") or "") for item in hits if item.get("id")
                ]
                if len(assessment.related_memory_ids) > 3:
                    assessment.should_ask = True
                    assessment.suggested_next_step = "ASK"
                    assessment.rationale_code = "AMBIGUOUS_REFERENT"
            except Exception:
                pass
        return assessment

    # --- Invalidation / change of mind ---
    if _INVALIDATE.search(raw) or (
        state is not None
        and state.has_cancelable_referent()
        and re.search(r"^\s*(?:jednak\s+)?niewa[zż]ne\b", raw, re.I)
    ):
        assessment.intent = CognitiveIntent.INVALIDATION.value
        assessment.importance = Importance.NORMAL.value
        # Session-scoped only: do NOT mark durable forget. Soft "nieważne"
        # clears TemporaryIntention / CandidateMemory / short-term focus.
        assessment.should_forget = False
        assessment.should_act = False
        assessment.rationale_code = "CHANGE_OF_MIND"
        assessment.suggested_next_step = "INVALIDATE"
        assessment.persistence = Persistence.NONE.value
        assessment.confidence = 0.85
        if state is not None:
            intention, ambiguous = resolve_intention_referent(raw, state)
            if ambiguous:
                assessment.should_ask = True
                assessment.suggested_next_step = "ASK"
                assessment.rationale_code = "AMBIGUOUS_REFERENT"
            elif intention is not None:
                assessment.related_goal_ids = [intention.id]
                assessment.related_memory_ids = list(intention.linked_memory_ids or [])
                assessment.referenced_entities = list(
                    dict.fromkeys(entities + list(intention.entities or []))
                )
                assessment.meaning = f"invalidate:{intention.text[:160]}"
            elif not state.active_intentions() and not state.enabled_rules():
                # No referent — low-value cancel chatter
                assessment.importance = Importance.TRIVIAL.value
                assessment.rationale_code = "NO_ACTION_REQUIRED"
                assessment.suggested_next_step = "NO_ACTION"
                assessment.should_forget = False
        return assessment

    # --- Correction ---
    if _CORRECTION.search(raw) or env.get("correction"):
        assessment.intent = CognitiveIntent.CORRECTION.value
        assessment.importance = Importance.NORMAL.value
        assessment.actionability = Actionability.NEEDS_CONFIRMATION.value
        assessment.should_act = False
        assessment.should_ask = False
        assessment.rationale_code = "CORRECTION"
        assessment.suggested_next_step = "APPLY_CORRECTION"
        assessment.confidence = 0.8
        return assessment

    # --- Explicit remember ---
    if _EXPLICIT_REMEMBER.search(raw):
        assessment.intent = CognitiveIntent.FACT.value
        assessment.importance = Importance.IMPORTANT.value
        assessment.persistence = Persistence.PERSISTENT.value
        assessment.actionability = Actionability.INFORMATIONAL.value
        assessment.should_remember = True
        assessment.should_act = False
        assessment.rationale_code = "EXPLICIT_REMEMBER"
        assessment.suggested_next_step = "PERSIST_MEMORY"
        assessment.confidence = 0.92
        if memory is not None and hasattr(memory, "retrieve"):
            try:
                from memory.classify import classify_remember_text, derive_key
                from memory.types import MemoryType

                classified = classify_remember_text(raw)
                key = str(classified.get("key") or derive_key(raw, MemoryType.FACT))
                hits = memory.retrieve("", key=key, limit=3) or []
                if hits:
                    assessment.related_memory_ids = [
                        str(item.get("id") or "") for item in hits if item.get("id")
                    ]
                    existing_val = str(hits[0].get("value") or hits[0].get("text") or "")
                    new_val = str(classified.get("value") or raw)
                    if existing_val and new_val and existing_val.casefold() != new_val.casefold():
                        if float(hits[0].get("confidence") or 0) >= 0.8:
                            assessment.should_ask = True
                            assessment.rationale_code = "CONFLICTING_MEMORY"
                            assessment.suggested_next_step = "ASK"
            except Exception:
                pass
        return assessment

    # --- Recurring reminder rule ---
    if _RECURRING.search(raw) and _REMINDER.search(raw):
        assessment.intent = CognitiveIntent.REMINDER_REQUEST.value
        assessment.importance = Importance.IMPORTANT.value
        assessment.persistence = Persistence.PERSISTENT.value
        assessment.temporal_scope = TemporalScope.ONGOING.value
        assessment.actionability = Actionability.SCHEDULED.value
        assessment.should_remember = True
        assessment.should_act = True
        assessment.rationale_code = "RECURRING_REMINDER_RULE"
        assessment.suggested_next_step = "STORE_REMINDER_RULE"
        assessment.confidence = 0.9
        return assessment

    # --- Reminder request (may resolve prior intention) ---
    if _REMINDER.search(raw):
        assessment.intent = CognitiveIntent.REMINDER_REQUEST.value
        assessment.importance = Importance.IMPORTANT.value
        assessment.persistence = Persistence.TEMPORARY.value
        assessment.temporal_scope = TemporalScope.NEAR_FUTURE.value
        assessment.actionability = Actionability.ACTIONABLE.value
        assessment.should_act = True
        assessment.should_remember = True
        assessment.rationale_code = "USER_REQUESTED_REMINDER"
        assessment.suggested_next_step = "ROUTE_REMINDER"
        assessment.confidence = 0.9
        if state is not None:
            intention, ambiguous = resolve_intention_referent(raw, state)
            if ambiguous:
                assessment.should_ask = True
                assessment.should_act = False
                assessment.suggested_next_step = "ASK"
                assessment.rationale_code = "AMBIGUOUS_REFERENT"
            elif intention is not None:
                assessment.related_goal_ids = [intention.id]
                assessment.referenced_entities = list(
                    dict.fromkeys(entities + list(intention.entities or []))
                )
                assessment.meaning = f"remind:{intention.text[:160]}"
                assessment.rationale_code = "CONTEXT_RESOLVED"
                time_hint = ""
                match = _TIME_HINT.search(raw)
                if match:
                    time_hint = match.group(0)
                assessment.suggested_next_step = "ROUTE_REMINDER"
                if time_hint:
                    assessment.persistence = Persistence.TEMPORARY.value
        return assessment

    # --- Preference (soft → candidate; reinforced later) ---
    if _PREFERENCE.search(raw) and not env.get("needs_tools"):
        reinforced = False
        if state is not None:
            key = ".".join(entities[:4]) or "pref"
            for cand in state.candidates:
                if cand.kind == "preference" and cand.key == key and cand.status != "invalidated":
                    reinforced = cand.confirmations >= 1
                    break
        assessment.intent = CognitiveIntent.PREFERENCE.value
        assessment.importance = Importance.NORMAL.value if reinforced else Importance.LOW.value
        assessment.persistence = Persistence.CANDIDATE.value
        assessment.actionability = Actionability.INFORMATIONAL.value
        assessment.should_remember = True
        assessment.should_act = False
        assessment.rationale_code = (
            "REINFORCED_PREFERENCE" if reinforced else "CANDIDATE_PREFERENCE"
        )
        assessment.suggested_next_step = (
            "REINFORCE_CANDIDATE" if reinforced else "STORE_CANDIDATE"
        )
        assessment.confidence = 0.75 if reinforced else 0.55
        return assessment

    # --- Important deadline ---
    if _DEADLINE.search(raw) or (_IMPORTANT.search(raw) and _FUTURE.search(raw)):
        uncertain = bool(_UNCERTAIN.search(raw))
        assessment.intent = (
            CognitiveIntent.POSSIBLE_INTENTION.value
            if uncertain
            else CognitiveIntent.FUTURE_INTENTION.value
        )
        assessment.importance = (
            Importance.CRITICAL.value
            if re.search(r"\b(?:egzamin|operacj|lot)\b", lowered)
            else Importance.IMPORTANT.value
        )
        assessment.persistence = Persistence.TEMPORARY.value
        assessment.temporal_scope = TemporalScope.NEAR_FUTURE.value
        assessment.actionability = (
            Actionability.INFORMATIONAL.value
            if uncertain
            else Actionability.NEEDS_CONFIRMATION.value
        )
        assessment.should_remember = True
        assessment.should_act = False
        assessment.rationale_code = (
            "UNCERTAIN_FUTURE" if uncertain else "IMPORTANT_DEADLINE"
        )
        assessment.suggested_next_step = "STORE_INTENTION"
        assessment.confidence = 0.7 if uncertain else 0.85
        return assessment

    # --- Uncertain / temporary future intention (no auto reminder) ---
    if (_FUTURE.search(raw) or _OBLIGATION.search(raw)) and (
        _UNCERTAIN.search(raw) or env.get("hypothetical")
    ):
        assessment.intent = CognitiveIntent.POSSIBLE_INTENTION.value
        assessment.importance = Importance.NORMAL.value
        if _IMPORTANT.search(raw):
            assessment.importance = Importance.IMPORTANT.value
        assessment.persistence = Persistence.SESSION.value
        assessment.temporal_scope = TemporalScope.NEAR_FUTURE.value
        assessment.actionability = Actionability.INFORMATIONAL.value
        assessment.should_remember = True
        assessment.should_act = False
        assessment.rationale_code = "TEMPORARY_FUTURE_INTENT"
        assessment.suggested_next_step = "STORE_INTENTION"
        assessment.confidence = 0.72
        return assessment

    # --- Committed future intention without reminder verb ---
    if _FUTURE.search(raw) and _OBLIGATION.search(raw) and not _REMINDER.search(raw):
        assessment.intent = CognitiveIntent.FUTURE_INTENTION.value
        assessment.importance = Importance.NORMAL.value
        if _IMPORTANT.search(raw):
            assessment.importance = Importance.IMPORTANT.value
        assessment.persistence = Persistence.TEMPORARY.value
        assessment.temporal_scope = TemporalScope.NEAR_FUTURE.value
        assessment.actionability = Actionability.INFORMATIONAL.value
        assessment.should_remember = True
        assessment.should_act = False
        assessment.rationale_code = "TEMPORARY_FUTURE_INTENT"
        assessment.suggested_next_step = "STORE_INTENTION"
        assessment.confidence = 0.78
        return assessment

    # --- Device / climate follow-ups (never treat as low-value chat) ---
    if _DEVICE_FOLLOWUP.search(raw):
        assessment.intent = CognitiveIntent.ACTION_REQUEST.value
        assessment.importance = Importance.NORMAL.value
        assessment.actionability = Actionability.ACTIONABLE.value
        assessment.should_act = True
        assessment.rationale_code = "GOAL_UPDATE"
        assessment.suggested_next_step = "ROUTE_AGENT"
        assessment.confidence = 0.7
        return assessment

    # --- Casual / low-value ---
    if _CASUAL.search(raw):
        assessment.intent = CognitiveIntent.CASUAL_THOUGHT.value
        assessment.importance = Importance.TRIVIAL.value
        assessment.persistence = Persistence.NONE.value
        assessment.actionability = Actionability.NONE.value
        assessment.should_remember = False
        assessment.should_act = False
        assessment.rationale_code = "LOW_VALUE_CHAT"
        assessment.suggested_next_step = "NO_ACTION"
        assessment.confidence = 0.8
        return assessment

    # --- Question ---
    if _QUESTION.search(raw) and not env.get("needs_tools"):
        assessment.intent = CognitiveIntent.QUESTION.value
        assessment.importance = Importance.NORMAL.value
        assessment.actionability = Actionability.INFORMATIONAL.value
        assessment.should_act = False
        assessment.rationale_code = "NO_ACTION_REQUIRED"
        assessment.suggested_next_step = "ROUTE_CHAT"
        assessment.confidence = max(0.6, float(env.get("confidence") or 0.6))
        return assessment

    # --- Envelope-driven action ---
    if env.get("needs_tools") or env.get("mode") in ("ACT", "PLAN"):
        assessment.intent = CognitiveIntent.ACTION_REQUEST.value
        assessment.importance = Importance.NORMAL.value
        assessment.actionability = Actionability.ACTIONABLE.value
        assessment.should_act = True
        assessment.rationale_code = "GOAL_UPDATE"
        assessment.suggested_next_step = "ROUTE_AGENT"
        assessment.confidence = max(0.65, float(env.get("confidence") or 0.65))
        return assessment

    # Implicit low-value fact (stated but not asked to remember)
    if len(raw.split()) >= 4 and not env.get("needs_tools"):
        assessment.intent = CognitiveIntent.FACT.value
        assessment.importance = Importance.LOW.value
        assessment.persistence = Persistence.CANDIDATE.value
        assessment.actionability = Actionability.INFORMATIONAL.value
        assessment.should_remember = False
        assessment.should_act = False
        assessment.rationale_code = "LOW_VALUE_CHAT"
        assessment.suggested_next_step = "STORE_CANDIDATE_WEAK"
        assessment.confidence = 0.45
        return assessment

    assessment.intent = CognitiveIntent.UNKNOWN.value
    assessment.importance = Importance.LOW.value
    assessment.rationale_code = "NO_ACTION_REQUIRED"
    assessment.suggested_next_step = "NO_ACTION"
    assessment.confidence = 0.4
    return assessment


def apply_assessment(
    assessment: CognitiveAssessment,
    text: str,
    *,
    state: CognitiveState,
    memory: Any = None,
) -> CognitiveGuidance:
    """Mutate session cognitive state + optional Memory 2.0; return pipeline guidance."""
    raw = _norm(text)
    guidance = CognitiveGuidance(assessment=assessment)
    state.last_user_texts.append(raw)
    state.last_user_texts = state.last_user_texts[-12:]
    state.last_assessments.append(assessment)
    state.last_assessments = state.last_assessments[-12:]

    code = assessment.rationale_code
    step = assessment.suggested_next_step

    # Soft world note for ProactivePolicy / Planner consumers.
    try:
        from core.world_state import note_session

        note_session(
            {
                "cognitive_intent": assessment.intent,
                "cognitive_importance": assessment.importance,
                "cognitive_rationale": assessment.rationale_code,
                "cognitive_should_act": bool(assessment.should_act),
            }
        )
    except Exception:
        pass

    if assessment.should_ask and step == "ASK":
        if code == "CONFLICTING_MEMORY":
            guidance.short_circuit_reply = (
                "Mam inną informację na ten temat. Którą mam zachować?"
            )
        elif code == "AMBIGUOUS_REFERENT":
            guidance.short_circuit_reply = "O czym dokładnie mam zapomnieć?"
        else:
            guidance.short_circuit_reply = "Doprecyzuj, proszę."
        guidance.skip_fast_path = True
        guidance.skip_agent_tools = True
        return guidance

    if step == "DISABLE_RULE" or code == "DISABLE_REMINDER_RULE":
        rule = None
        if assessment.related_goal_ids:
            for item in state.reminder_rules:
                if item.id in assessment.related_goal_ids:
                    rule = item
                    break
        if rule is None:
            rule, _amb = resolve_rule_referent(raw, state)
        # Cold restart: session may be empty while ROUTINE still lives on disk.
        if rule is None and memory is not None and not state.enabled_rules():
            try:
                rehydrate_reminder_rules_from_memory(state, memory)
            except Exception:
                pass
            rule, _amb = resolve_rule_referent(raw, state)
        if rule is not None:
            disable_reminder_rule(rule)
            if memory is not None and hasattr(memory, "forget"):
                try:
                    memory.forget(rule.trigger or rule.text)
                except Exception:
                    pass
            # Also clear any leftover ROUTINE mirrors by subject
            try:
                forget_matching_reminder_routines(memory, raw)
            except Exception:
                pass
            guidance.short_circuit_reply = "Dobrze, już nie będę o tym przypominać."
            guidance.skip_fast_path = True
            guidance.skip_agent_tools = True
            return guidance
        # No session rule: still deactivate matching durable ROUTINE by subject.
        removed = []
        try:
            removed = forget_matching_reminder_routines(memory, raw)
        except Exception:
            removed = []
        if removed:
            guidance.short_circuit_reply = (
                "Dobrze, usunąłem to przypomnienie z pamięci — już nie będę o tym przypominać."
            )
            guidance.skip_fast_path = True
            guidance.skip_agent_tools = True
            return guidance
        guidance.short_circuit_reply = "Nie mam takiego aktywnego przypomnienia."
        guidance.skip_fast_path = True
        guidance.skip_agent_tools = True
        return guidance

    if step in ("INVALIDATE", "FORGET") or assessment.should_forget:
        # Soft CHANGE_OF_MIND / INVALIDATE: session scope only.
        # Durable FACT/PREFERENCE/ROUTINE wiped only via EXPLICIT_FORGET (CM)
        # or DISABLE_RULE (subject-matched ROUTINE) — never via memory.forget
        # on TemporaryIntention.text.
        if code == "CHANGE_OF_MIND" or step == "INVALIDATE":
            if assessment.related_goal_ids:
                for intention in state.intentions:
                    if intention.id in assessment.related_goal_ids:
                        invalidate_session_intention(state, intention, memory)
                        guidance.short_circuit_reply = "Dobrze, pomijam to."
                        guidance.skip_fast_path = True
                        guidance.skip_agent_tools = True
                        return guidance
            if state.active_intentions() and looks_invalidation(raw):
                intention, ambiguous = resolve_intention_referent(raw, state)
                if not ambiguous and intention is not None:
                    invalidate_session_intention(state, intention, memory)
                    guidance.short_circuit_reply = "Dobrze, pomijam to."
                    guidance.skip_fast_path = True
                    guidance.skip_agent_tools = True
                    return guidance
            # No referent — acknowledge without durable wipe.
            if looks_invalidation(raw):
                guidance.short_circuit_reply = "Dobrze, pomijam to."
                guidance.skip_fast_path = True
                guidance.skip_agent_tools = True
            return guidance
        if code == "EXPLICIT_FORGET":
            # Leave durable MemoryManager forget to CM when parse_explicit hits;
            # here only soft cognitive forget of candidates/intentions.
            query = re.sub(
                r"^(?:zapomnij(?:\s+o)?|nie\s+pami[eę]taj)\s+",
                "",
                raw,
                flags=re.I,
            ).strip()
            for cand in state.candidates:
                blob = f"{cand.text} {cand.key}".casefold()
                if query and query.casefold() in blob:
                    cand.status = "invalidated"
            for intention in list(state.active_intentions()):
                blob = _cf(intention.text)
                if query and _cf(query) in blob:
                    invalidate_session_intention(state, intention, memory)
            guidance.short_circuit_reply = ""
            return guidance
        # Other should_forget paths (e.g. residual): session intentions only.
        if state.active_intentions() and looks_invalidation(raw):
            intention, ambiguous = resolve_intention_referent(raw, state)
            if not ambiguous and intention is not None:
                invalidate_session_intention(state, intention, memory)
                guidance.short_circuit_reply = "Dobrze, pomijam to."
                guidance.skip_fast_path = True
                guidance.skip_agent_tools = True
                return guidance
        return guidance

    if step == "STORE_REMINDER_RULE" or code == "RECURRING_REMINDER_RULE":
        rule = ReminderRule(
            id=_new_id("rule"),
            text=raw,
            trigger=raw,
            importance=assessment.importance,
            enabled=True,
            created_at=time.time(),
            entities=list(assessment.referenced_entities),
        )
        state.reminder_rules.append(rule)
        assessment.related_goal_ids = [rule.id]
        cand = upsert_candidate(
            state,
            raw,
            kind="rule",
            importance=assessment.importance,
            confidence=0.9,
            key="rule." + ".".join(assessment.referenced_entities[:3]),
            entities=assessment.referenced_entities,
        )
        entry_id = promote_candidate_to_memory(cand, memory, explicit=True)
        if entry_id:
            assessment.related_memory_ids.append(entry_id)
        # Feed Agent so it may create reminder / procedure — do not invoke tools here.
        guidance.enriched_goal = (
            f"Użytkownik chce stałe przypominanie: {raw}. "
            "Utwórz przypomnienie lub regułę, jeśli to możliwe."
        )
        guidance.skip_fast_path = True
        return guidance

    if step == "ROUTE_REMINDER" or code in ("USER_REQUESTED_REMINDER", "CONTEXT_RESOLVED"):
        intention = None
        if assessment.related_goal_ids:
            for item in state.intentions:
                if item.id in assessment.related_goal_ids:
                    intention = item
                    break
        time_hint = ""
        match = _TIME_HINT.search(raw)
        if match:
            time_hint = match.group(0)
        topic = intention.text if intention is not None else raw
        if intention is not None:
            intention.reminder_requested = True
            intention.reminder_time_hint = time_hint
            intention.certainty = "committed"
            intention.kind = CognitiveIntent.REMINDER_REQUEST.value
        goal = f"Przypomnij mi {time_hint}: {topic}".strip()
        if not time_hint:
            goal = f"Przypomnij mi o: {topic}"
        guidance.enriched_goal = goal
        guidance.skip_fast_path = False
        return guidance

    if step == "STORE_INTENTION" or code in (
        "TEMPORARY_FUTURE_INTENT",
        "UNCERTAIN_FUTURE",
        "IMPORTANT_DEADLINE",
    ):
        intention = TemporaryIntention(
            id=_new_id("intent"),
            text=raw,
            kind=assessment.intent,
            importance=assessment.importance,
            certainty=(
                "uncertain"
                if code in ("TEMPORARY_FUTURE_INTENT", "UNCERTAIN_FUTURE")
                else "likely"
            ),
            created_at=time.time(),
            entities=list(assessment.referenced_entities),
            status="active",
        )
        cand = upsert_candidate(
            state,
            raw,
            kind="intention",
            importance=assessment.importance,
            confidence=assessment.confidence,
            entities=assessment.referenced_entities,
        )
        intention.linked_memory_ids = [cand.id]
        state.intentions.append(intention)
        assessment.related_goal_ids = [intention.id]
        # Never auto-create reminder for uncertain/temporary future intent.
        guidance.short_circuit_reply = _ack_for_intention(assessment)
        guidance.skip_fast_path = True
        guidance.skip_agent_tools = True
        guidance.route_chat = True
        return guidance

    if step in ("STORE_CANDIDATE", "REINFORCE_CANDIDATE", "STORE_CANDIDATE_WEAK"):
        kind = "preference" if assessment.intent == CognitiveIntent.PREFERENCE.value else "fact"
        conf = 0.35 if step == "STORE_CANDIDATE_WEAK" else float(assessment.confidence)
        cand = upsert_candidate(
            state,
            raw,
            kind=kind,
            importance=assessment.importance,
            confidence=conf,
            entities=assessment.referenced_entities,
        )
        assessment.related_memory_ids = [cand.id]
        if cand.status == "reinforced" or code == "REINFORCED_PREFERENCE":
            entry_id = promote_candidate_to_memory(cand, memory, explicit=False)
            if entry_id:
                assessment.related_memory_ids.append(entry_id)
                guidance.short_circuit_reply = "Zapamiętam."
                guidance.skip_fast_path = True
                guidance.skip_agent_tools = True
                return guidance
        if code == "CANDIDATE_PREFERENCE":
            # Soft prefer — no hard persist yet.
            guidance.short_circuit_reply = "OK, biorę pod uwagę."
            guidance.skip_fast_path = True
            guidance.skip_agent_tools = True
            return guidance
        # Weak implicit fact — stay silent to pipeline (no persist).
        return guidance

    if step == "PERSIST_MEMORY" or code == "EXPLICIT_REMEMBER":
        # Explicit remember usually handled earlier by MemoryManager command.
        # If we reach here, still mark candidate + guidance only.
        cand = upsert_candidate(
            state,
            raw,
            kind="fact",
            importance=Importance.IMPORTANT.value,
            confidence=0.95,
            entities=assessment.referenced_entities,
        )
        cand.status = "reinforced"
        entry_id = promote_candidate_to_memory(cand, memory, explicit=True)
        if entry_id:
            assessment.related_memory_ids.append(entry_id)
        return guidance

    if step == "ROUTE_CHAT":
        # Do not skip FastPath here — device follow-ups may still match.
        guidance.route_chat = True
        return guidance

    if code == "LOW_VALUE_CHAT" and step == "NO_ACTION":
        # Guidance only — never block FastPath/climate continuation.
        return guidance

    if step == "ROUTE_AGENT" or assessment.should_act:
        # Keep FastPath available for smart-home / climate continuations.
        guidance.skip_fast_path = False
        return guidance

    if code == "CORRECTION":
        guidance.skip_fast_path = True
        return guidance

    if code == "NO_ACTION_REQUIRED":
        guidance.skip_agent_tools = False
        return guidance

    return guidance


def _ack_for_intention(assessment: CognitiveAssessment) -> str:
    if assessment.importance in (Importance.IMPORTANT.value, Importance.CRITICAL.value):
        if assessment.rationale_code == "UNCERTAIN_FUTURE":
            return "Rozumiem, na razie tylko biorę to pod uwagę."
        return "OK, mam to na uwadze."
    return "Rozumiem."


def assess_and_apply(
    text: str,
    *,
    envelope: Any = None,
    state: CognitiveState | None = None,
    memory: Any = None,
    recent_user: Sequence[str] | None = None,
) -> CognitiveGuidance:
    """Canonical Cognitive Core entry used by ConversationManager."""
    if not _enabled():
        return CognitiveGuidance(
            assessment=CognitiveAssessment(
                meaning=_norm(text)[:240],
                rationale_code="NO_ACTION_REQUIRED",
                suggested_next_step="NO_ACTION",
            )
        )
    cog_state = state if state is not None else CognitiveState()
    assessment = assess(
        text,
        envelope=envelope,
        state=cog_state,
        memory=memory,
        recent_user=recent_user,
    )
    guidance = apply_assessment(
        assessment,
        text,
        state=cog_state,
        memory=memory,
    )
    try:
        log_turn(
            "cognitive",
            intent=assessment.intent,
            importance=assessment.importance,
            rationale=assessment.rationale_code,
            next=assessment.suggested_next_step,
            should_act=assessment.should_act,
            should_ask=assessment.should_ask,
        )
    except Exception:
        pass
    return guidance


def cognitive_blocks_auto_reminder(assessment: CognitiveAssessment | None) -> bool:
    if assessment is None:
        return False
    return assessment.rationale_code in (
        "TEMPORARY_FUTURE_INTENT",
        "UNCERTAIN_FUTURE",
        "LOW_VALUE_CHAT",
        "NO_ACTION_REQUIRED",
        "CANDIDATE_PREFERENCE",
    ) and not assessment.should_act
