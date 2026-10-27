# ==========================
# ELIAN — compound cross-domain plans (Phase 12.3.3 / 13 Stage B)
# ==========================
"""Connector is a candidate boundary. Semantic layer confirms independent actions."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from core.compound_execute import source_kind_for_step
from core.intent_family import (
    APP_CLOSE_VERBS,
    COMPOUND_CONNECTOR,
    IntentFamily,
    classify_intent_family,
)
from core.service_open import (
    POLICY_APP_FIRST,
    looks_explicit_website,
    resolution_policy_for,
)
from core.target_resolve import POWER_OFF_VERBS, looks_ha_device_target


_CLAUSE_VERB = re.compile(
    r"\b(?:"
    r"otw[oó]rz|zamknij|zamkn[aą][cć]|zwi[eę]ksz|zmniejsz|[sś]cisz|przycisz|"
    r"odpal|uruchom|włącz|wlacz|wyłącz|wylacz|zapal|z?ga[sś]|wyga[sś]|"
    r"wyszukaj|znajd[zź]|poszukaj|pu[sś][cć]|kliknij|g[lł]o[sś]niej|ciszej|"
    r"ustaw|wejd[zź]|powiedz|odtw[oó]rz|"
    r"wpisz|zaznacz|zminimalizuj|zmaksymalizuj|przewi[nń]|cofnij|skopiuj|wklej|"
    r"przejd[zź]"
    r")\b",
    re.I,
)
_COMMA_BEFORE_ACTION = re.compile(
    r"\s*,\s+(?="
    r"(?:otw[oó]rz|zamknij|zwi[eę]ksz|zmniejsz|[sś]cisz|przycisz|"
    r"odpal|uruchom|włącz|wlacz|wyłącz|wylacz|zapal|z?ga[sś]|wyga[sś]|"
    r"wyszukaj|znajd[zź]|pu[sś][cć]|ustaw|wejd[zź]|kliknij|"
    r"g[lł]o[sś]niej|ciszej|wpisz|zaznacz|zminimalizuj|przewi[nń])"
    r")",
    re.I,
)
_VOLUME_UP = re.compile(
    r"(?:\b(?:g[lł]o[sś]niej|ustaw\s+głośniej|podgłośn|volume\s+up)\b|"
    r"\bzwi[eę]ksz\s+g[lł]o[sś])",
    re.I,
)
_VOLUME_DOWN = re.compile(
    r"(?:\b(?:ciszej|volume\s+down|przycisz)\b|"
    r"\bzmniejsz\s+g[lł]o[sś]|"
    r"\b[sś]cisz)",
    re.I,
)
_ENTER_SITE = re.compile(r"\bwejd[zź]\s+na\b", re.I)
_BROWSER_SEARCH = re.compile(r"\b(?:wyszukaj|poszukaj|znajd[zź])\b", re.I)
_KNOWLEDGE_ABOUT = re.compile(r"\b(?:powiedz|opowiedz|co[sś]\s+o)\b", re.I)
_ALLOWED_FAMILIES = {
    IntentFamily.APP.value,
    IntentFamily.SYSTEM.value,
    IntentFamily.SMART_HOME.value,
    IntentFamily.WEB.value,
    IntentFamily.MEDIA.value,
    IntentFamily.VISION.value,
    IntentFamily.COMPUTER.value,
    IntentFamily.PROCEDURE.value if hasattr(IntentFamily, "PROCEDURE") else "PROCEDURE",
    IntentFamily.KNOWLEDGE.value,
}


@dataclass
class CompoundStep:
    family: str
    semantic_action: str
    text: str = ""
    complete: bool = True
    slots: Dict[str, Any] = field(default_factory=dict)
    raw_clause: str = ""
    normalized_clause: str = ""
    raw_start: int = 0
    raw_end: int = 0

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return data


@dataclass
class CompoundGoal:
    complete: bool = False
    detected: bool = False
    steps: List[CompoundStep] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "complete": self.complete,
            "detected": self.detected,
            "steps": [step.to_dict() for step in self.steps],
            "raw_text": self.raw_text,
            "families": [step.family for step in self.steps],
        }


def _volume_direction(text: str) -> str:
    if _VOLUME_DOWN.search(text) and not _VOLUME_UP.search(text):
        return "decrease"
    if _VOLUME_UP.search(text) or _VOLUME_DOWN.search(text):
        return "increase" if _VOLUME_UP.search(text) else "decrease"
    if re.search(r"\bciszej\b|[sś]cisz|przycisz", text, re.I):
        return "decrease"
    if re.search(r"\bg[lł]o[sś]niej\b|zwi[eę]ksz", text, re.I):
        return "increase"
    return "increase"


def _looks_like_np_coordination(parts: List[str]) -> bool:
    cleaned = [str(item or "").strip() for item in parts if str(item or "").strip()]
    if len(cleaned) < 2:
        return False
    first_has_verb = bool(_CLAUSE_VERB.search(cleaned[0]))
    later_have_verb = any(_CLAUSE_VERB.search(part) for part in cleaned[1:])
    return first_has_verb and not later_have_verb


def _looks_like_information_coordination(parts: List[str]) -> bool:
    cleaned = [str(item or "").strip() for item in parts if str(item or "").strip()]
    if len(cleaned) < 2:
        return False
    if _KNOWLEDGE_ABOUT.search(cleaned[0]) and not any(
        _CLAUSE_VERB.search(part) for part in cleaned[1:]
    ):
        return True
    return False


def _stripped_span(text: str, start: int, end: int) -> tuple[str, int, int]:
    start = max(0, int(start))
    end = min(len(text), int(end))
    if end <= start:
        return "", start, start
    chunk = text[start:end]
    left = len(chunk) - len(chunk.lstrip(" .,!?;:"))
    trimmed = chunk.strip(" .,!?;:")
    inner_start = start + left
    inner_end = inner_start + len(trimmed)
    return trimmed, inner_start, inner_end


def split_candidate_clauses_with_spans(text: str) -> List[Dict[str, Any]]:
    """Split on connectors while keeping offsets into the original utterance."""
    raw = str(text or "")
    if not raw.strip():
        return []
    chunks: List[tuple[int, int]] = []
    last = 0
    for match in COMPOUND_CONNECTOR.finditer(raw):
        chunks.append((last, match.start()))
        last = match.end()
    chunks.append((last, len(raw)))
    expanded: List[Dict[str, Any]] = []
    for start, end in chunks:
        piece = raw[start:end]
        comma_hits = list(_COMMA_BEFORE_ACTION.finditer(piece))
        if not comma_hits:
            clause, inner_s, inner_e = _stripped_span(raw, start, end)
            if clause:
                expanded.append(
                    {
                        "raw_clause": clause,
                        "normalized_clause": clause.lower(),
                        "raw_start": inner_s,
                        "raw_end": inner_e,
                    }
                )
            continue
        last_rel = 0
        for match in comma_hits:
            clause, inner_s, inner_e = _stripped_span(raw, start + last_rel, start + match.start())
            if clause:
                expanded.append(
                    {
                        "raw_clause": clause,
                        "normalized_clause": clause.lower(),
                        "raw_start": inner_s,
                        "raw_end": inner_e,
                    }
                )
            last_rel = match.end()
        clause, inner_s, inner_e = _stripped_span(raw, start + last_rel, end)
        if clause:
            expanded.append(
                {
                    "raw_clause": clause,
                    "normalized_clause": clause.lower(),
                    "raw_start": inner_s,
                    "raw_end": inner_e,
                }
            )
    return expanded


def split_candidate_clauses(text: str) -> List[str]:
    return [str(item.get("raw_clause") or "") for item in split_candidate_clauses_with_spans(text)]


def _app_step(text: str, classification) -> CompoundStep:
    slots = dict(classification.slots or {})
    name = str(slots.get("app_name") or "").strip()
    if not name:
        try:
            from core.intent_family import _find_app_name

            name = str(_find_app_name(str(text or "").lower()) or "").strip()
        except Exception:
            name = ""
    if name:
        slots["app_name"] = name
    if (
        APP_CLOSE_VERBS.search(text)
        or POWER_OFF_VERBS.search(text)
        or str(slots.get("semantic_action") or "") in ("close_app", "close_service")
    ) and not looks_ha_device_target(text):
        slots["target"] = name
        slots["semantic_action"] = "close_service"
        return CompoundStep(
            family=IntentFamily.APP.value,
            semantic_action="close_service",
            text=text,
            complete=bool(name),
            slots=slots,
        )
    policy = resolution_policy_for(text)
    website_direct = (
        looks_explicit_website(text)
        or policy == "OFFICIAL_WEBSITE"
        or bool(_ENTER_SITE.search(text))
    )
    slots.update(
        {
            "target": name,
            "resolution_policy": policy,
            "website_direct": website_direct,
            "explicit_app": policy == "EXPLICIT_APP",
        }
    )
    return CompoundStep(
        family=IntentFamily.APP.value,
        semantic_action="open_service",
        text=text,
        complete=bool(name),
        slots=slots,
    )


def _system_step(text: str) -> CompoundStep:
    direction = _volume_direction(text)
    return CompoundStep(
        family=IntentFamily.SYSTEM.value,
        semantic_action="adjust_volume",
        text=text,
        complete=True,
        slots={"direction": direction},
    )


def _smart_home_step(text: str, classification) -> CompoundStep:
    slots = dict(classification.slots or {})
    lowered = str(text or "").lower()
    temp = None
    try:
        from core.climate_control import looks_climate_utterance, parse_temperature_celsius

        temp = parse_temperature_celsius(text)
        climate = looks_climate_utterance(text) or bool(
            re.search(r"\b(?:klima|klimy|klimę|klime|klimatyz)\w*\b", lowered)
        )
    except Exception:
        climate = bool(re.search(r"\b(?:klima|klimatyz)\w*\b", lowered))
    if temp is not None and climate:
        slots.update(
            {
                "domain": "climate",
                "action": "set_temperature",
                "value": temp,
                "target": "klima",
                "semantic_action": "set_temperature",
            }
        )
        return CompoundStep(
            family=IntentFamily.SMART_HOME.value,
            semantic_action="set_temperature",
            text=text,
            complete=True,
            slots=slots,
        )
    try:
        from core.local_device_status import looks_climate_status, looks_local_device_status

        if looks_climate_status(text) or looks_local_device_status(text):
            slots.update({"domain": "climate", "semantic_action": "get_device_state"})
            return CompoundStep(
                family=IntentFamily.SMART_HOME.value,
                semantic_action="get_device_state",
                text=text,
                complete=True,
                slots=slots,
            )
    except Exception:
        pass
    if climate and re.search(r"\bco\s+robi\b", lowered):
        slots.update({"domain": "climate", "target": "klima", "semantic_action": "get_device_state"})
        return CompoundStep(
            family=IntentFamily.SMART_HOME.value,
            semantic_action="get_device_state",
            text=text,
            complete=True,
            slots=slots,
        )
    light = bool(re.search(r"\b(?:lamp|świat[łl]|swiat[lł])\w*\b", lowered))
    if POWER_OFF_VERBS.search(text) or re.search(r"\bwyłącz|wylacz\b", lowered):
        if climate and not light:
            slots.update({"domain": "climate", "target": "klima", "action": "turn_off"})
            return CompoundStep(
                family=IntentFamily.SMART_HOME.value,
                semantic_action="turn_off",
                text=text,
                complete=True,
                slots=slots,
            )
        slots.update(
            {
                "domain": "light",
                "target": "lampę",
                "action": "turn_off",
                "semantic_action": "turn_off",
            }
        )
        return CompoundStep(
            family=IntentFamily.SMART_HOME.value,
            semantic_action="turn_off",
            text=text,
            complete=True,
            slots=slots,
        )
    if re.search(r"\b(?:włącz|wlacz)\b", lowered):
        domain = "climate" if climate and not light else "light"
        slots.update(
            {
                "domain": domain,
                "target": "klima" if domain == "climate" else "lampę",
                "action": "turn_on",
            }
        )
        return CompoundStep(
            family=IntentFamily.SMART_HOME.value,
            semantic_action="turn_on",
            text=text,
            complete=True,
            slots=slots,
        )
    return CompoundStep(
        family=IntentFamily.SMART_HOME.value,
        semantic_action=str(slots.get("semantic_action") or "smart_home"),
        text=text,
        complete=True,
        slots=slots,
    )


def _web_or_browser_step(text: str, classification) -> CompoundStep:
    slots = dict(classification.slots or {})
    query = str(text or "")
    query = re.sub(
        r"^(?:wyszukaj|poszukaj|znajd[zź])\s+",
        "",
        query,
        flags=re.I,
    ).strip(" .,!?")
    if _BROWSER_SEARCH.search(text):
        slots.update({"query": query, "semantic_action": "browser_search_service"})
        return CompoundStep(
            family=IntentFamily.WEB.value,
            semantic_action="browser_search_service",
            text=text,
            complete=bool(query),
            slots=slots,
        )
    return CompoundStep(
        family=IntentFamily.WEB.value,
        semantic_action="web_search",
        text=text,
        complete=True,
        slots=slots,
    )


def _media_step(text: str, classification) -> CompoundStep:
    slots = dict(classification.slots or {})
    try:
        from core.semantic_media import parse_media_goal

        goal = parse_media_goal(text)
        if goal is not None:
            slots.update(goal.to_slots())
            return CompoundStep(
                family=IntentFamily.MEDIA.value,
                semantic_action=goal.semantic_action,
                text=text,
                complete=goal.complete,
                slots=slots,
            )
    except Exception:
        pass
    return CompoundStep(
        family=IntentFamily.MEDIA.value,
        semantic_action="play_media",
        text=text,
        complete=True,
        slots=slots,
    )


def _annotate_source(step: CompoundStep) -> CompoundStep:
    step.slots = dict(step.slots or {})
    step.slots["source_kind"] = source_kind_for_step(
        step.family, step.semantic_action, step.text
    )
    return step


def _step_from_clause(
    text: str,
    *,
    raw_clause: str = "",
    normalized_clause: str = "",
    raw_start: int = 0,
    raw_end: int = 0,
) -> Optional[CompoundStep]:
    clause = str(raw_clause or text or "").strip(" .,!?;:")
    if not clause:
        return None
    normalized = str(normalized_clause or clause).lower().strip(" .,!?;:")
    asr_confirm = False
    try:
        from core.asr_repair import repair_action_verbs

        repaired = repair_action_verbs(normalized)
        if repaired.applied and repaired.canonical:
            normalized = repaired.canonical
        asr_confirm = bool(repaired.needs_confirmation)
    except Exception:
        asr_confirm = False
    info = classify_intent_family(normalized, allow_compound=False)

    def finish(step: CompoundStep) -> CompoundStep:
        step.raw_clause = clause
        step.normalized_clause = normalized
        step.raw_start = int(raw_start or 0)
        step.raw_end = int(raw_end or 0)
        step.slots = dict(step.slots or {})
        step.slots["raw_clause"] = clause
        step.slots["normalized_clause"] = normalized
        step.slots["raw_start"] = step.raw_start
        step.slots["raw_end"] = step.raw_end
        if asr_confirm:
            step.slots["needs_confirmation"] = True
            step.slots["asr_repair"] = "destructive_uncertain"
            step.complete = False
        if str(step.semantic_action or "") == "type_text":
            try:
                from core.computer_semantics import extract_type_payload

                payload = extract_type_payload(clause)
            except Exception:
                payload = ""
            if payload:
                step.slots["text_payload"] = payload
                step.complete = True
        return _annotate_source(step)

    if _ENTER_SITE.search(clause):
        step = _app_step(clause, info)
        step.family = IntentFamily.WEB.value
        step.slots["website_direct"] = True
        step.semantic_action = "open_service"
        return finish(step)
    if info.family == IntentFamily.APP:
        return finish(_app_step(clause, info))
    if info.family == IntentFamily.SYSTEM or _VOLUME_UP.search(clause) or _VOLUME_DOWN.search(clause):
        return finish(_system_step(clause))
    if info.family == IntentFamily.SMART_HOME or looks_ha_device_target(clause):
        return finish(_smart_home_step(clause, info))
    if info.family == IntentFamily.MEDIA:
        return finish(_media_step(clause, info))
    if info.family == IntentFamily.WEB or _BROWSER_SEARCH.search(clause) or _ENTER_SITE.search(clause):
        if _ENTER_SITE.search(clause):
            step = _app_step(clause, info)
            step.family = IntentFamily.WEB.value
            step.slots["website_direct"] = True
            step.semantic_action = "open_service"
            return finish(step)
        return finish(_web_or_browser_step(clause, info))
    if info.family == IntentFamily.VISION:
        return finish(
            CompoundStep(
                family=IntentFamily.VISION.value,
                semantic_action="inspect_screen",
                text=clause,
                complete=True,
                slots=dict(info.slots or {}),
            )
        )
    if info.family == IntentFamily.COMPUTER:
        slots = dict(info.slots or {})
        action = str(slots.get("semantic_action") or "inspect_ui")
        return finish(
            CompoundStep(
                family=IntentFamily.COMPUTER.value,
                semantic_action=action,
                text=clause,
                complete=bool(info.is_complete),
                slots=slots,
            )
        )
    if info.family in (
        IntentFamily.REMINDER,
        IntentFamily.CALENDAR,
        IntentFamily.KNOWLEDGE,
    ) and info.is_complete:
        action = "talk" if info.family == IntentFamily.KNOWLEDGE else info.family.value.lower()
        return finish(
            CompoundStep(
                family=info.family.value,
                semantic_action=action,
                text=clause,
                complete=True,
                slots=dict(info.slots or {}),
            )
        )
    try:
        from core.semantic_executive import looks_creative_humor, _looks_talk_clause

        if looks_creative_humor(clause) or _looks_talk_clause(clause):
            intent = "creative_humor" if looks_creative_humor(clause) else "talk"
            return finish(
                CompoundStep(
                    family=IntentFamily.KNOWLEDGE.value,
                    semantic_action=intent,
                    text=clause,
                    complete=True,
                    slots={"talk": True, "intent": intent},
                )
            )
    except Exception:
        pass
    try:
        from core.procedure_contract import classify_procedure

        procedure_id = classify_procedure(clause)
        if procedure_id:
            return finish(
                CompoundStep(
                    family="PROCEDURE",
                    semantic_action=f"procedure_{procedure_id}",
                    text=clause,
                    complete=True,
                    slots={"procedure_id": procedure_id},
                )
            )
    except Exception:
        pass
    return None


def parse_compound_goal(text: str) -> CompoundGoal:
    raw = str(text or "").strip()
    goal = CompoundGoal(raw_text=raw)
    if not raw:
        return goal
    spans = split_candidate_clauses_with_spans(raw)
    parts = [str(item.get("raw_clause") or "") for item in spans]
    if len(parts) < 2:
        return goal
    if _looks_like_np_coordination(parts) or _looks_like_information_coordination(parts):
        return goal
    steps: List[CompoundStep] = []
    for item in spans:
        step = _step_from_clause(
            str(item.get("raw_clause") or ""),
            raw_clause=str(item.get("raw_clause") or ""),
            normalized_clause=str(item.get("normalized_clause") or ""),
            raw_start=int(item.get("raw_start") or 0),
            raw_end=int(item.get("raw_end") or 0),
        )
        if step is None:
            continue
        steps.append(step)
    if len(steps) < 2:
        return goal
    families = {step.family for step in steps}
    if not families.issubset(_ALLOWED_FAMILIES | {"PROCEDURE"}):
        extra = families - _ALLOWED_FAMILIES - {"PROCEDURE"}
        if extra:
            return goal
    goal.detected = True
    goal.steps = steps
    goal.complete = all(step.complete for step in steps)
    return goal


def is_cross_domain_compound(text: str) -> bool:
    plan = parse_compound_goal(text)
    return bool(plan.detected and len(plan.steps) >= 2)


def print_compound_diagnostics(plan: CompoundGoal) -> None:
    if not plan.detected:
        return
    families = ",".join(step.family for step in plan.steps)
    print(
        f"[MULTI]\ndetected=True\nclauses={len(plan.steps)}\n"
        f"families={families}\ncomplete={plan.complete}"
    )
    for index, step in enumerate(plan.steps, start=1):
        extra = ""
        if step.semantic_action == "open_service":
            extra = (
                f"\ntarget={step.slots.get('target')}\n"
                f"policy={step.slots.get('resolution_policy') or POLICY_APP_FIRST}"
            )
        elif step.semantic_action == "close_service":
            extra = f"\ntarget={step.slots.get('target')}\naction=close_app"
        elif step.semantic_action == "adjust_volume":
            extra = f"\ndirection={step.slots.get('direction')}"
        elif step.semantic_action == "type_text":
            extra = (
                f"\nraw_span={step.raw_clause or step.slots.get('raw_clause') or ''}\n"
                f"payload={step.slots.get('text_payload') or ''}"
            )
        elif step.slots.get("source_kind"):
            extra = f"\nsource={step.slots.get('source_kind')}\ntarget={step.slots.get('target') or ''}"
        print(f"[STEP {index}]\naction={step.semantic_action}{extra}")
