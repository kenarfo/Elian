# ==========================
# ELIAN — structured clarification slots (Phase 10.4)
# ==========================
"""Pending clarification with missing/known slots + light target resolution."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from DeviceResolver import normalize_pl, resolve_device


LIGHT_TARGET_WORDS = re.compile(
    r"\b(?:lamp\w*|światł\w*|swiatl\w*|oświetlen\w*|oswietlen\w*|żarówk\w*|zarowk\w*|"
    r"vizzini|salon\w*|pokoj\w*|pokój\w*)\b",
    re.I,
)
GENERIC_LIGHT_ANSWER = re.compile(
    r"^(?:lamp[eęyaąi]|światł[oa]|swiatl[oa]|oświetlenie|oswietlenie)"
    r"(?:\s+proszę|\s+prosze)?[!.…]*$",
    re.I,
)

GENERIC_LIGHT_QUESTION = re.compile(
    r"kt[oó]re\s+[sś]wiat[lł]o\s+mam\s+rozja[sś]ni[cć]",
    re.I,
)

ROOM_HINTS = (
    ("pokoj", ("pokoj", "pokoju", "moim pokoju", "room")),
    ("salon", ("salon", "salonie", "living")),
    ("sypialnia", ("sypialnia", "sypialni", "bedroom")),
    ("kuchnia", ("kuchnia", "kuchni", "kitchen")),
    ("biuro", ("biuro", "biurze", "office")),
)


def extract_room_hint(text: str) -> str:
    """Canonical room key from utterance, or empty. Shared by intent + bind gates."""
    try:
        norm = normalize_pl(str(text or ""))
    except Exception:
        norm = str(text or "").lower()
    if not norm:
        return ""
    for key, aliases in ROOM_HINTS:
        if key in norm or any(alias in norm for alias in aliases):
            return key
    return ""


def room_matches_device(room_key: str, device: Any) -> bool:
    """True when ActiveDevice / entity blob mentions the utterance room."""
    key = str(room_key or "").strip().lower()
    if not key:
        return True
    aliases = ()
    for cand, als in ROOM_HINTS:
        if cand == key:
            aliases = als
            break
    parts = [
        str(getattr(device, "name", "") or ""),
        str(getattr(device, "alias", "") or ""),
        str(getattr(device, "entity_id", "") or ""),
        str(getattr(device, "friendly_name", "") or ""),
        str(getattr(device, "object_id", "") or ""),
    ]
    if isinstance(device, dict):
        parts.extend(
            [
                str(device.get("name") or ""),
                str(device.get("alias") or ""),
                str(device.get("entity_id") or ""),
                str(device.get("friendly_name") or ""),
            ]
        )
    try:
        blob = normalize_pl(" ".join(parts))
    except Exception:
        blob = " ".join(parts).lower()
    if key in blob:
        return True
    return any(alias in blob for alias in aliases)


@dataclass
class SlotResolveResult:
    status: str  # resolved | ambiguous | no_progress | unclear
    progress: bool = False
    target: str = ""
    question: str = ""
    known_slots: Dict[str, Any] = field(default_factory=dict)
    missing_slots: List[str] = field(default_factory=list)
    candidate_entities: List[Dict[str, str]] = field(default_factory=list)
    message: str = ""


def _entity_payload(entity) -> Dict[str, str]:
    return {
        "entity_id": str(getattr(entity, "entity_id", "") or ""),
        "name": str(
            getattr(entity, "display_name", lambda: "")()
            if callable(getattr(entity, "display_name", None))
            else getattr(entity, "friendly_name", "")
            or getattr(entity, "entity_id", "")
        ),
    }


def _candidates_from_entities(entities: Sequence[Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for entity in entities:
        payload = _entity_payload(entity)
        if payload.get("entity_id"):
            out.append(payload)
    return out


def _option_question(candidates: Sequence[Dict[str, str]]) -> str:
    names = [item.get("name") or item.get("entity_id") or "" for item in candidates]
    names = [name for name in names if name]
    if len(names) == 2:
        return f"Masz na myśli {names[0]} czy {names[1]}?"
    if names:
        listed = ", ".join(names[:4])
        return f"Którą lampę: {listed}?"
    return "Które światło mam rozjaśnić?"


def build_pending_clarification(
    *,
    goal: str,
    question: str,
    intent_family: str = "",
    action: str = "",
    question_type: str = "",
    missing_slots: Optional[Sequence[str]] = None,
    known_slots: Optional[Dict[str, Any]] = None,
    candidate_entities: Optional[Sequence[Dict[str, str]]] = None,
    attempts: int = 1,
    round_num: int = 1,
    raw: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    raw = dict(raw or {})
    family = intent_family or str(raw.get("intent_family") or "")
    slots_missing = list(missing_slots or raw.get("missing_slots") or [])
    q_type = question_type or str(raw.get("question_type") or "")
    act = action or str(raw.get("action") or "")

    if not family and raw.get("implicit_light"):
        family = "SMART_HOME"
    if raw.get("implicit_light") and not slots_missing:
        slots_missing = ["target"]
    if raw.get("implicit_light") and not q_type:
        q_type = "light_target"
    if raw.get("implicit_light") and not act:
        act = "brightness_increase"

    original = str(goal or "").split("\nUżytkownik uzupełnił:")[0].strip()
    return {
        "goal": original,
        "original_goal": original,
        "question": str(question or "").strip(),
        "question_type": q_type,
        "intent_family": family,
        "action": act,
        "missing_slots": slots_missing,
        "known_slots": dict(known_slots or raw.get("known_slots") or {}),
        "candidate_entities": list(candidate_entities or raw.get("candidate_entities") or []),
        "attempts": int(attempts),
        "round": int(round_num),
        "started_at": time.monotonic(),
        "last_answer": "",
    }


def is_light_target_pending(pending: Optional[Dict[str, Any]]) -> bool:
    if not pending:
        return False
    if pending.get("question_type") == "light_target":
        return True
    missing = pending.get("missing_slots") or []
    if "target" in missing and str(pending.get("intent_family") or "") == "SMART_HOME":
        return True
    return bool(GENERIC_LIGHT_QUESTION.search(str(pending.get("question") or "")))


def looks_like_light_slot_answer(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if GENERIC_LIGHT_ANSWER.match(raw):
        return True
    if LIGHT_TARGET_WORDS.search(raw) and len(raw.split()) <= 8:
        return True
    return False


def _pool_from_pending(pending: Dict[str, Any], entities: Optional[Sequence[Any]]):
    if entities is not None:
        return list(entities)
    stored = pending.get("candidate_entities") or []
    if not stored:
        return None
    # Lightweight stand-ins for resolve_device when only ids/names are stored.
    class _E:
        def __init__(self, entity_id: str, name: str):
            self.entity_id = entity_id
            self.domain = "light"
            self.object_id = entity_id.split(".", 1)[-1]
            self.friendly_name = name
            self.aliases = ()

        def display_name(self):
            return self.friendly_name or self.object_id

    return [_E(item.get("entity_id", ""), item.get("name", "")) for item in stored if item.get("entity_id")]


def _room_filtered(answer: str, entities: Sequence[Any]) -> List[Any]:
    norm = normalize_pl(answer)
    if not norm or not entities:
        return []
    matched_keys = []
    for key, aliases in ROOM_HINTS:
        if any(alias in norm for alias in aliases) or key in norm:
            matched_keys.append(key)
    if not matched_keys:
        return []
    filtered = []
    for entity in entities:
        blob = normalize_pl(
            " ".join(
                [
                    str(getattr(entity, "friendly_name", "") or ""),
                    str(getattr(entity, "entity_id", "") or ""),
                    str(getattr(entity, "object_id", "") or ""),
                ]
            )
        )
        if any(key in blob for key in matched_keys):
            filtered.append(entity)
    return filtered


def _resolved_result(entity, known: Dict[str, Any]) -> SlotResolveResult:
    target = entity.entity_id
    known = {
        **known,
        "target": target,
        "entity_id": target,
        "domain": "light",
        "default_light": target,
    }
    return SlotResolveResult(
        status="resolved",
        progress=True,
        target=target,
        known_slots=known,
        missing_slots=[],
        candidate_entities=_candidates_from_entities([entity]),
        message=f"Wybrałem {entity.display_name()}.",
    )


def resolve_light_target_answer(
    pending: Dict[str, Any],
    answer: str,
    *,
    entities: Optional[Sequence[Any]] = None,
) -> SlotResolveResult:
    answer_text = str(answer or "").strip()
    known = dict(pending.get("known_slots") or {})
    previous_q = str(pending.get("question") or "").strip()
    previous_answer = str(pending.get("last_answer") or "").strip().lower()
    pool = _pool_from_pending(pending, entities)

    # Room / name hints beat near-tied fuzzy scores.
    if pool:
        room_hits = _room_filtered(answer_text, pool)
        if len(room_hits) == 1:
            return _resolved_result(room_hits[0], known)

    match = resolve_device(answer_text, domain="light", entities=pool)
    if match.status == "exact" and match.entity is not None:
        return _resolved_result(match.entity, known)

    if match.status == "ambiguous" and match.candidates:
        room_hits = _room_filtered(answer_text, match.candidates)
        if len(room_hits) == 1:
            return _resolved_result(room_hits[0], known)
        candidates = _candidates_from_entities(room_hits or match.candidates)
        question = match.message or _option_question(candidates)
        # Prefer room-style wording when generic answer + multiple lights.
        if GENERIC_LIGHT_ANSWER.match(answer_text) and len(candidates) >= 2:
            question = _option_question(candidates)
        progress = normalize_pl(question) != normalize_pl(previous_q)
        if not progress and normalize_pl(answer_text) != normalize_pl(previous_answer):
            # Same question after new answer → force more specific options.
            question = _option_question(candidates)
            progress = normalize_pl(question) != normalize_pl(previous_q)
        return SlotResolveResult(
            status="ambiguous",
            progress=progress or bool(candidates),
            question=question,
            known_slots=known,
            missing_slots=["target"],
            candidate_entities=candidates,
            message=question,
        )

    # Generic "lampę" with no registry → still treat as slot progress (narrowed hint).
    if GENERIC_LIGHT_ANSWER.match(answer_text) or looks_like_light_slot_answer(answer_text):
        known = {**known, "target_hint": answer_text}
        question = (
            "Którą lampę dokładnie — podaj pokój lub nazwę."
            if GENERIC_LIGHT_QUESTION.search(previous_q)
            else previous_q or "Które światło mam rozjaśnić?"
        )
        if GENERIC_LIGHT_QUESTION.search(question) and GENERIC_LIGHT_QUESTION.search(previous_q):
            question = "Podaj nazwę lub pokój lampy."
        progress = normalize_pl(question) != normalize_pl(previous_q) or bool(
            known.get("target_hint")
        )
        if not progress:
            question = "Podaj nazwę lub pokój lampy."
            progress = True
        return SlotResolveResult(
            status="no_progress" if not progress else "ambiguous",
            progress=True,
            question=question,
            known_slots=known,
            missing_slots=["target"],
            candidate_entities=list(pending.get("candidate_entities") or []),
            message=question,
        )

    identical = (
        GENERIC_LIGHT_QUESTION.search(previous_q)
        and normalize_pl(answer_text) == normalize_pl(previous_answer)
    )
    question = "Podaj nazwę lub pokój lampy."
    if identical or GENERIC_LIGHT_QUESTION.search(previous_q):
        return SlotResolveResult(
            status="no_progress",
            progress=False,
            question=question,
            known_slots=known,
            missing_slots=["target"],
            candidate_entities=list(pending.get("candidate_entities") or []),
            message=question,
        )
    return SlotResolveResult(
        status="unclear",
        progress=False,
        question=question,
        known_slots=known,
        missing_slots=["target"],
        candidate_entities=list(pending.get("candidate_entities") or []),
        message=question,
    )


def apply_pending_update(
    pending: Dict[str, Any],
    result: SlotResolveResult,
    *,
    answer: str = "",
) -> Dict[str, Any]:
    updated = dict(pending)
    updated["known_slots"] = dict(result.known_slots)
    updated["missing_slots"] = list(result.missing_slots)
    updated["candidate_entities"] = list(result.candidate_entities)
    updated["attempts"] = int(updated.get("attempts") or 1) + 1
    updated["last_answer"] = str(answer or "")
    updated["started_at"] = time.monotonic()
    if result.question:
        updated["question"] = result.question
    if result.target:
        updated["known_slots"]["target"] = result.target
    return updated


def enrichment_goal(pending: Dict[str, Any], answer: str) -> str:
    original = str(pending.get("original_goal") or pending.get("goal") or "").strip()
    hint = str(answer or "").strip()
    known = pending.get("known_slots") or {}
    target = known.get("target") or known.get("entity_id") or ""
    if target:
        return f"{original}\nUżytkownik uzupełnił target: {target}"
    if hint:
        return f"{original}\nUżytkownik uzupełnił: {hint}"
    return original
