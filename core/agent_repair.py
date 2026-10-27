# ==========================
# ELIAN — family-based agent repair (Phase 10.1)
# ==========================

from __future__ import annotations

import re
from typing import Optional

from core.agent_safety import (
    is_acknowledgement,
    is_correction,
    is_side_effect_tool,
    preference_tool_blocked,
    side_effect_blocked_for_intent,
    unknown_blocks_side_effects,
    validate_side_effect_arguments,
)
from core.agent_types import AgentDecision
from core.clarification_policy import (
    OPEN_APP_CLARIFICATION,
    is_inappropriate_open_app_clarification,
    looks_like_tool_dump,
)
from core.intent_family import IntentClassification, IntentFamily, classify_intent_family


GENERIC_OPEN_CLARIFICATION = re.compile(
    r"czy\s+(?:chcesz|mam|mog[eę])\s+(?:otworzy[cć]|uruchomi[cć]|odpali[cć])\s+(?:aplikacj[eę])?",
    re.I,
)


def _brightness_set_decision(target: str, percent: int) -> AgentDecision:
    """Absolute brightness from percent follow-up — never climate."""
    from HaActions import entity_supports_brightness
    from HomeAssistant import get_registry

    pct = max(0, min(100, int(percent)))
    entity = None
    try:
        entity = get_registry().get(str(target or "").strip())
    except Exception:
        entity = None

    if entity is not None and getattr(entity, "domain", "") != "light":
        return AgentDecision(
            type="final_answer",
            message=(
                f"{entity.display_name()} nie jest światłem — "
                "nie ustawiam jasności."
            ),
            raw={"domain_guard": True, "domain": entity.domain},
        )

    if entity is not None and not entity_supports_brightness(entity):
        return AgentDecision(
            type="final_answer",
            message=f"{entity.display_name()} nie obsługuje regulacji jasności.",
            raw={"unsupported_brightness": True},
        )

    return AgentDecision(
        type="tool_call",
        tool="set_device_value",
        arguments={
            "action": "light_brightness",
            "target": target,
            "entity_id": target,
            "domain": "light",
            "value": pct,
        },
    )


def _brightness_adjust_decision(target: str, delta: int) -> AgentDecision:
    """Relative brightness — never climate. delta sign selects dim vs brighten."""
    from HaActions import entity_supports_brightness
    from HomeAssistant import get_registry

    entity = None
    try:
        entity = get_registry().get(str(target or "").strip())
    except Exception:
        entity = None

    if entity is not None and getattr(entity, "domain", "") != "light":
        return AgentDecision(
            type="final_answer",
            message=(
                f"{entity.display_name()} nie jest światłem — "
                "nie zmieniam jasności."
            ),
            raw={"domain_guard": True, "domain": entity.domain},
        )

    if entity is not None and not entity_supports_brightness(entity):
        return AgentDecision(
            type="final_answer",
            message=f"{entity.display_name()} nie obsługuje regulacji jasności.",
            raw={"unsupported_brightness": True},
        )

    return AgentDecision(
        type="tool_call",
        tool="set_device_value",
        arguments={
            "action": "adjust_brightness",
            "target": target,
            "entity_id": target,
            "domain": "light",
            "value": int(delta),
        },
    )


def _brightness_increase_decision(target: str) -> AgentDecision:
    """Map implicit 'ciemno' to canonical adjust_brightness when capability allows."""
    from HaActions import DEFAULT_BRIGHTNESS_STEP

    return _brightness_adjust_decision(target, int(DEFAULT_BRIGHTNESS_STEP))


def family_retry_prompt(family: IntentFamily, *, goal: str = "") -> str:
    base = (
        "Poprzednia odpowiedź nie była poprawnym JSON decyzji agenta. "
        "Zwróć WYŁĄCZNIE jeden obiekt JSON z polem type. "
    )
    prompts = {
        IntentFamily.APP: (
            base
            + "Intent=APP. Jeśli aplikacja jest w goal → tool_call open_app z arguments.name. "
            "NIE używaj clarification typu „Czy chcesz otworzyć aplikację?”."
        ),
        IntentFamily.MEDIA: (
            base
            + "Intent=MEDIA. → tool_call play_media z arguments.query (artysta/utwór). "
            "NIE open_app clarification."
        ),
        IntentFamily.KNOWLEDGE: (
            base
            + "Intent=KNOWLEDGE. → final_answer z krótkim wyjaśnieniem po polsku. "
            "ZERO tool_call. ZERO clarification."
        ),
        IntentFamily.VISION: (
            base + "Intent=VISION. → tool_call describe_screen lub find_ui_element. NIE open_app."
        ),
        IntentFamily.COMPUTER: (
            base + "Intent=COMPUTER. → list_windows / inspect_ui / type_text / click_ui_element. NIE web_search."
        ),
        IntentFamily.REMINDER: (
            base + "Intent=REMINDER. → tool_call create_reminder. Clarification tylko o brakującym czasie."
        ),
        IntentFamily.CALENDAR: (
            base + "Intent=CALENDAR. → tool_call create_calendar_event."
        ),
        IntentFamily.WEB: (
            base + "Intent=WEB. → tool_call web_search."
        ),
        IntentFamily.SMART_HOME: (
            base
            + "Intent=SMART_HOME. Światło → turn_on_device / set_device_value. "
            "Jeśli brak targetu → clarification o świetle (NIGDY open_app)."
        ),
        IntentFamily.CORRECTION: (
            base
            + "Intent=CORRECTION. → final_answer. ZERO tool_call. ZERO set_preference."
        ),
        IntentFamily.PREFERENCE: (
            base
            + "Intent=PREFERENCE. Tylko wtedy set_preference z key/value. Inaczej final_answer."
        ),
        IntentFamily.UNKNOWN: (
            base
            + "Intent=UNKNOWN (hint only). Wnioskuj goal z naturalnego języka. "
            "Decyduj: final_answer (rozmowa), clarification (dopytaj po polsku), "
            "albo tool_call z kompletnymi argumentami z katalogu. "
            "NIGDY open_app bez nazwy aplikacji. NIGDY „Nie zrozumiałem”."
        ),
    }
    return prompts.get(family, base + "Nie defaultuj do open_app clarification. Wnioskuj goal semantycznie.")


def build_deterministic_decision(
    classification: IntentClassification,
) -> Optional[AgentDecision]:
    """Known-slot / safe family decisions without LLM."""
    family = classification.family
    slots = classification.slots or {}

    if slots.get("needs_target_disambiguation"):
        app = str(slots.get("app_name") or "aplikację")
        return AgentDecision(
            type="clarification",
            question=f"Masz na myśli aplikację {app} czy urządzenie w domu?",
            raw={"needs_target_disambiguation": True},
        )

    if family == IntentFamily.CORRECTION:
        return AgentDecision(
            type="final_answer",
            message="Jasne, powiedz co miałeś na myśli.",
            raw={"correction": True},
        )

    if family == IntentFamily.PREFERENCE and classification.is_complete:
        key = slots.get("preference_key") or "music.default_app"
        value = slots.get("preference_value")
        if value:
            return AgentDecision(
                type="tool_call",
                tool="set_preference",
                arguments={"key": key, "value": value},
            )
        return AgentDecision(
            type="clarification",
            question="Jaką preferencję mam zapamiętać?",
            raw={"preference_incomplete": True},
        )

    # Implicit lighting without safe target → light-only clarification (even if incomplete).
    if family == IntentFamily.SMART_HOME and slots.get("implicit_light"):
        if slots.get("default_light") or slots.get("entity_id") or slots.get("target"):
            target = slots.get("default_light") or slots.get("entity_id") or slots.get("target")
            from HaActions import DEFAULT_BRIGHTNESS_STEP

            if slots.get("action") == "adjust_brightness" and slots.get("value") is not None:
                delta = int(slots.get("value"))
            elif slots.get("desired") == "darker":
                delta = -int(DEFAULT_BRIGHTNESS_STEP)
            else:
                delta = int(DEFAULT_BRIGHTNESS_STEP)
            return _brightness_adjust_decision(str(target), delta)
        return AgentDecision(
            type="clarification",
            question="Które światło mam rozjaśnić?",
            raw={
                "implicit_light": True,
                "intent_family": "SMART_HOME",
                "action": "brightness_increase",
                "question_type": "light_target",
                "missing_slots": ["target"],
                "known_slots": {},
            },
        )

    # Phase 12 / 12.2 — resolved canonical device (percent, dim/brighten, on/off).
    if family == IntentFamily.SMART_HOME and slots.get("resolved_context"):
        if slots.get("unsupported"):
            return AgentDecision(
                type="final_answer",
                message=str(
                    slots.get("unsupported_message")
                    or "Tej czynności nie obsługuję dla tego urządzenia."
                ),
                raw={"unsupported_capability": True, "domain": slots.get("domain")},
            )
        entity_id = str(slots.get("entity_id") or slots.get("target") or "")
        domain = str(slots.get("domain") or "")
        value = int(slots.get("value") or slots.get("percent") or 0)
        action = str(slots.get("action") or "")
        if domain == "light" and action == "adjust_brightness" and entity_id:
            return _brightness_adjust_decision(entity_id, value)
        if domain == "light" and action == "light_brightness" and entity_id:
            return _brightness_set_decision(entity_id, value)
        if action == "turn_on" and entity_id:
            return AgentDecision(
                type="tool_call",
                tool="turn_on_device",
                arguments={
                    "target": entity_id,
                    "entity_id": entity_id,
                    "domain": domain,
                },
            )
        if action == "turn_off" and entity_id:
            return AgentDecision(
                type="tool_call",
                tool="turn_off_device",
                arguments={
                    "target": entity_id,
                    "entity_id": entity_id,
                    "domain": domain,
                },
            )
        if domain == "media_player" and action == "media_volume" and entity_id:
            return AgentDecision(
                type="tool_call",
                tool="set_device_value",
                arguments={
                    "action": "media_volume",
                    "target": entity_id,
                    "entity_id": entity_id,
                    "domain": "media_player",
                    "value": value,
                },
            )
        if domain == "climate" and action in ("light_brightness", "adjust_brightness"):
            return AgentDecision(
                type="final_answer",
                message=(
                    "Procentów nie ustawiam jako temperatury. "
                    "Powiedz ile stopni Celsjusza."
                ),
                raw={"domain_lock": True, "domain": "climate"},
            )
        if domain == "climate" and action in (
            "set_temperature",
            "adjust_temperature",
            "climate_temperature",
            "climate_temperature_delta",
        ) and entity_id:
            return AgentDecision(
                type="tool_call",
                tool="set_device_value",
                arguments={
                    "action": "set_temperature" if "adjust" not in action else "adjust_temperature",
                    "target": entity_id,
                    "entity_id": entity_id,
                    "domain": "climate",
                    "value": value,
                    "semantic_action": action,
                },
            )
        if domain == "climate" and action in (
            "set_hvac_mode",
            "set_fan_mode",
            "set_swing_mode",
            "set_swing_horizontal_mode",
            "set_preset_mode",
        ) and entity_id:
            extra = str(slots.get("extra") or slots.get("mode") or slots.get("value") or "")
            return AgentDecision(
                type="tool_call",
                tool="set_device_value",
                arguments={
                    "action": action,
                    "target": entity_id,
                    "entity_id": entity_id,
                    "domain": "climate",
                    "extra": extra,
                    "value": extra,
                    "semantic_action": str(slots.get("semantic_action") or action),
                },
            )
        if domain == "climate" and action in ("light_brightness", "adjust_brightness"):
            return AgentDecision(
                type="final_answer",
                message=(
                    "Procentów nie ustawiam jako temperatury. "
                    "Powiedz ile stopni Celsjusza."
                ),
                raw={"domain_lock": True, "domain": "climate"},
            )

    if slots.get("compound"):
        return None

    if not classification.is_complete:
        return None

    if family == IntentFamily.APP:
        app = slots.get("app_name")
        if app and str(slots.get("semantic_action") or "") in ("close_app", "close_service"):
            return AgentDecision(
                type="tool_call",
                tool="close_app",
                arguments={"name": app},
            )
        if app:
            # Freeform names stay complete for FastPath, but Planner must keep the
            # model in the loop so not-found can repair (e.g. open_website).
            if slots.get("freeform_app"):
                return None
            arguments = {
                "name": app,
                "text": classification.raw_text or "",
            }
            if slots.get("website_direct"):
                arguments["website_direct"] = True
            if slots.get("explicit_app"):
                arguments["explicit_app"] = True
            return AgentDecision(
                type="tool_call",
                tool="open_app",
                arguments=arguments,
            )

    if family == IntentFamily.SYSTEM:
        from core.tool_compat import volume_direction_from_text

        try:
            from core.read_intents import looks_undo, undo_count

            raw_text = classification.raw_text or ""
            if looks_undo(raw_text) or str(slots.get("semantic_action") or "") == "undo":
                n = slots.get("undo_count") or undo_count(raw_text) or 1
                try:
                    n = max(1, int(n))
                except (TypeError, ValueError):
                    n = 1
                return AgentDecision(
                    type="tool_call",
                    tool="undo_last_action",
                    arguments={"count": n},
                )
        except Exception:
            pass
        direction = volume_direction_from_text(classification.raw_text or "")
        if direction == "decrease":
            return AgentDecision(type="tool_call", tool="volume_down", arguments={})
        if direction == "increase":
            return AgentDecision(type="tool_call", tool="volume_up", arguments={})

    if family == IntentFamily.MEDIA:
        query = slots.get("media_query")
        if query:
            args = {"query": query}
            if slots.get("app_name"):
                args["app"] = slots["app_name"]
            return AgentDecision(
                type="tool_call",
                tool="play_media",
                arguments=args,
            )

    if family == IntentFamily.KNOWLEDGE:
        topic = slots.get("topic") or classification.raw_text
        return AgentDecision(
            type="final_answer",
            message=f"Wyjaśnię: {topic}.",
            raw={"delegate_knowledge": True, "topic": topic},
        )

    if family == IntentFamily.WEB:
        from core.freshness import analyze_freshness
        from core.web_grounding import clean_web_query

        fresh = analyze_freshness(classification.raw_text)
        query = (
            slots.get("search_query")
            or fresh.search_query
            or clean_web_query(slots.get("query") or classification.raw_text)
        )
        return AgentDecision(
            type="tool_call",
            tool="web_search",
            arguments={"query": str(query).strip()},
            raw={
                "deterministic_web": True,
                "freshness_required": True,
                "semantic_kind": slots.get("semantic_kind") or fresh.semantic_kind,
                "claim": slots.get("claim") or fresh.claim,
            },
        )

    if family == IntentFamily.VISION:
        return AgentDecision(
            type="tool_call",
            tool="describe_screen",
            arguments={},
        )

    if family == IntentFamily.COMPUTER:
        action = str(slots.get("semantic_action") or "")
        if action in ("list_windows", "get_active_window", "window_exists", "inspect_ui"):
            return AgentDecision(
                type="tool_call",
                tool=action if action != "inspect_ui" else "inspect_ui",
                arguments={
                    "name": slots.get("app_name") or slots.get("query") or "",
                    "query": slots.get("query") or "",
                },
            )
        if action == "type_text":
            return AgentDecision(
                type="tool_call",
                tool="type_text",
                arguments={
                    "text": slots.get("text_payload") or "",
                    "app_name": slots.get("app_name") or "",
                    "append": bool(slots.get("append")),
                },
            )
        if action in ("select_all", "undo", "copy", "paste", "cut"):
            keys = {
                "select_all": "ctrl+a",
                "undo": "ctrl+z",
                "copy": "ctrl+c",
                "paste": "ctrl+v",
                "cut": "ctrl+x",
            }[action]
            return AgentDecision(
                type="tool_call",
                tool="hotkey",
                arguments={"keys": keys, "semantic_action": action, "app_name": slots.get("app_name") or ""},
            )
        if action in (
            "focus_window",
            "minimize_window",
            "maximize_window",
            "restore_window",
            "close_window",
        ):
            return AgentDecision(
                type="tool_call",
                tool=action,
                arguments={"name": slots.get("app_name") or slots.get("query") or "", "query": slots.get("query") or ""},
            )
        if action in ("click_ui_element", "click_referent"):
            return AgentDecision(
                type="tool_call",
                tool="click_ui_element",
                arguments={"query": slots.get("query") or classification.raw_text},
            )
        if action == "scroll":
            tool = "scroll_up" if str(slots.get("direction") or "") == "up" else "scroll_down"
            return AgentDecision(type="tool_call", tool=tool, arguments={"amount": slots.get("amount") or 3})
        if action == "press_key":
            return AgentDecision(
                type="tool_call",
                tool="hotkey",
                arguments={"keys": slots.get("query") or "enter"},
            )
        if action == "read_clipboard":
            return AgentDecision(
                type="tool_call",
                tool="read_clipboard",
                arguments={},
            )

    if family == IntentFamily.REMINDER:
        arguments = {"text": slots.get("reminder_text") or classification.raw_text}
        if slots.get("when"):
            arguments["when"] = slots["when"]
        return AgentDecision(
            type="tool_call",
            tool="create_reminder",
            arguments=arguments,
        )

    return None


def repair_decision(
    decision: AgentDecision,
    *,
    goal: str,
    classification: IntentClassification,
) -> AgentDecision:
    """Fix model decisions that violate intent family contract."""
    if is_acknowledgement(classification) or is_correction(classification):
        if is_correction(classification):
            return AgentDecision(
                type="final_answer",
                message="Jasne, powiedz co miałeś na myśli.",
                raw={"correction": True},
            )
        return AgentDecision(type="final_answer", message="", raw={"ack": True})

    if decision.is_tool_call:
        if preference_tool_blocked(classification, decision.tool or ""):
            det = build_deterministic_decision(classification)
            if det and not (det.is_tool_call and det.tool in ("set_preference", "remember_fact")):
                return det
            if classification.family == IntentFamily.MEDIA and classification.slots.get("media_query"):
                return AgentDecision(
                    type="tool_call",
                    tool="play_media",
                    arguments={"query": classification.slots["media_query"]},
                )
            return AgentDecision(
                type="final_answer",
                message="Nie zapisuję preferencji bez wyraźnej prośby.",
                raw={"preference_blocked": True},
            )
        # Phase 10.5 — incomplete UNKNOWN: allow AI tools with valid args; block probes.
        if unknown_blocks_side_effects(classification) and is_side_effect_tool(decision.tool or ""):
            if side_effect_blocked_for_intent(
                classification,
                decision.tool or "",
                decision.arguments,
            ):
                missing = validate_side_effect_arguments(
                    decision.tool or "",
                    decision.arguments,
                )
                if missing is not None:
                    return AgentDecision(
                        type="clarification",
                        question="Możesz sprecyzować, co mam zrobić?",
                        raw={"unknown_missing_args": True, "tool": decision.tool},
                    )
                return AgentDecision(
                    type="clarification",
                    question="Chcesz, żebym coś konkretnego zrobił?",
                    raw={"unknown_guard": True},
                )
            # Allowed under UNKNOWN policy — keep tool_call.
            return decision
        if (
            classification.family == IntentFamily.SMART_HOME
            and decision.tool in ("open_app", "open_website", "set_preference")
        ):
            return _family_fallback_decision(classification, reason="smart_home_guard")
        if (
            classification.family == IntentFamily.KNOWLEDGE
            and decision.tool in ("open_app", "open_website")
        ):
            det = build_deterministic_decision(classification)
            if det:
                return det
        if (
            classification.family == IntentFamily.MEDIA
            and decision.tool in ("open_app", "set_preference")
        ):
            det = build_deterministic_decision(classification)
            if det:
                return det
        from core.tool_compat import repair_incompatible_tool

        repaired_tool = repair_incompatible_tool(
            decision.tool or "",
            goal=goal,
            classification=classification,
            arguments=decision.arguments,
        )
        if repaired_tool is not None:
            return repaired_tool
        return decision

    if decision.is_clarification:
        question = decision.question or ""
        if (
            GENERIC_OPEN_CLARIFICATION.search(question)
            or is_inappropriate_open_app_clarification(goal, question)
            or looks_like_tool_dump(question)
        ):
            det = build_deterministic_decision(classification)
            if det:
                return det
            if classification.family == IntentFamily.APP and classification.slots.get("app_name"):
                return AgentDecision(
                    type="tool_call",
                    tool="open_app",
                    arguments={"name": classification.slots["app_name"]},
                )
            if classification.family == IntentFamily.MEDIA and classification.slots.get("media_query"):
                return AgentDecision(
                    type="tool_call",
                    tool="play_media",
                    arguments={"query": classification.slots["media_query"]},
                )
            return _family_fallback_decision(classification, reason="bad_clarification")
        if classification.family == IntentFamily.KNOWLEDGE:
            det = build_deterministic_decision(classification)
            if det:
                return det
        if classification.family == IntentFamily.APP and classification.slots.get("app_name"):
            return AgentDecision(
                type="tool_call",
                tool="open_app",
                arguments={"name": classification.slots["app_name"]},
            )
        if classification.family == IntentFamily.MEDIA and classification.slots.get("media_query"):
            return AgentDecision(
                type="tool_call",
                tool="play_media",
                arguments={"query": classification.slots["media_query"]},
            )
        return decision

    if decision.is_final and classification.family == IntentFamily.KNOWLEDGE:
        return decision

    return decision


def fallback_after_parse_failure(
    goal: str,
    *,
    classification: Optional[IntentClassification] = None,
) -> AgentDecision:
    """Never open_app on parse failure."""
    info = classification or classify_intent_family(goal)
    det = build_deterministic_decision(info)
    if det:
        return det
    return _family_fallback_decision(info, reason="parse_failure")


def _family_fallback_decision(
    classification: IntentClassification,
    *,
    reason: str = "",
) -> AgentDecision:
    family = classification.family
    if family == IntentFamily.CORRECTION:
        return AgentDecision(
            type="final_answer",
            message="Jasne, powiedz co miałeś na myśli.",
            raw={"fallback": reason, "correction": True},
        )
    if family == IntentFamily.KNOWLEDGE:
        topic = classification.slots.get("topic") or classification.raw_text
        return AgentDecision(
            type="final_answer",
            message=f"Wyjaśnię: {topic}.",
            raw={"delegate_knowledge": True, "fallback": reason},
        )
    if family == IntentFamily.MEDIA:
        if classification.slots.get("media_query"):
            return AgentDecision(
                type="tool_call",
                tool="play_media",
                arguments={"query": classification.slots["media_query"]},
                raw={"fallback": reason},
            )
        return AgentDecision(
            type="clarification",
            question="Co mam puścić?",
            raw={"fallback": reason},
        )
    if family == IntentFamily.SMART_HOME:
        slots = classification.slots or {}
        target = slots.get("entity_id") or slots.get("target") or slots.get("default_light")
        if target:
            det = build_deterministic_decision(
                IntentClassification(
                    family=IntentFamily.SMART_HOME,
                    is_complete=True,
                    slots=dict(slots),
                    raw_text=classification.raw_text,
                )
            )
            if det is not None:
                return det
        return AgentDecision(
            type="clarification",
            question="Które światło mam rozjaśnić?",
            raw={
                "fallback": reason,
                "implicit_light": True,
                "intent_family": "SMART_HOME",
                "action": "brightness_increase",
                "question_type": "light_target",
                "missing_slots": ["target"],
            },
        )
    if family == IntentFamily.APP:
        return AgentDecision(
            type="clarification",
            question="Którą aplikację mam otworzyć?",
            raw={"fallback": reason},
        )
    if family == IntentFamily.REMINDER:
        return AgentDecision(
            type="clarification",
            question="O czym i na kiedy mam przypomnieć?",
            raw={"fallback": reason},
        )
    if family == IntentFamily.VISION:
        return AgentDecision(
            type="tool_call",
            tool="describe_screen",
            arguments={},
            raw={"fallback": reason},
        )
    if family == IntentFamily.COMPUTER:
        det = build_deterministic_decision(
            IntentClassification(
                family=IntentFamily.COMPUTER,
                is_complete=True,
                slots=dict(classification.slots or {}),
                raw_text=classification.raw_text,
            )
        )
        if det is not None:
            return det
        return AgentDecision(
            type="tool_call",
            tool="inspect_ui",
            arguments={},
            raw={"fallback": reason},
        )
    if family == IntentFamily.WEB:
        from core.freshness import analyze_freshness
        from core.web_grounding import clean_web_query

        fresh = analyze_freshness(classification.raw_text)
        query = fresh.search_query or clean_web_query(classification.raw_text)
        return AgentDecision(
            type="tool_call",
            tool="web_search",
            arguments={"query": query},
            raw={"fallback": reason, "freshness_required": True},
        )
    # UNKNOWN / default — conversational, never "Nie zrozumiałem" as dead-end.
    return AgentDecision(
        type="final_answer",
        message="Okej — o czym chcesz pogadać albo co mam zrobić?",
        raw={"fallback": reason, "ai_first_talk": True},
    )


def decide_execution_repair(
    *,
    tool_name: str,
    arguments: dict,
    execution,
    verification,
    classification: IntentClassification,
    goal: str = "",
    repair_used: int = 0,
    max_repair: int = 2,
    diagnosis=None,
    force_alternate: bool = False,
    blocked_fingerprints=None,
):
    """Controlled execution repair. Dual policy unchanged — not ActionPolicy v3.

    Never re-run a mutation after verification already confirmed success.
    ASK on material uncertainty. Relative mutations are not retried after a
    successful execute (would overshoot). Repair action depends on FailureCategory.
    """
    from core.execution_context import FailureCategory, action_fingerprint
    from core.plan_cycle import (
        IDEMPOTENT_MUTATIONS,
        RELATIVE_MUTATIONS,
        RepairAction,
        RepairDecision,
        VerifyStatus,
        diagnose_failure,
        material_uncertainty,
    )

    name = str(tool_name or "")
    args = dict(arguments or {})
    blocked = {str(item) for item in (blocked_fingerprints or []) if item}
    ver_status = getattr(verification, "status", VerifyStatus.SKIPPED)
    if ver_status == VerifyStatus.PASSED:
        return RepairDecision(action=RepairAction.SKIP, reason="already_verified", tool=name)
    if repair_used >= max(1, int(max_repair)):
        return RepairDecision(action=RepairAction.FAIL, reason="repair_exhausted", tool=name)

    unsure = material_uncertainty(name, args)
    if unsure:
        question = "Możesz sprecyzować, co mam zrobić?"
        if name == "play_media":
            question = "Co mam puścić?"
        elif name == "open_app":
            question = "Którą aplikację mam otworzyć?"
        return RepairDecision(
            action=RepairAction.ASK,
            reason="material_uncertainty",
            tool=name,
            question=question,
        )

    if getattr(execution, "blocked", False) and str(
        getattr(execution, "block_reason", "") or ""
    ) in ("safety_block",):
        return RepairDecision(action=RepairAction.FAIL, reason="safety_block", tool=name)

    if diagnosis is None:
        diagnosis = diagnose_failure(
            tool_name=name,
            arguments=args,
            execution=execution,
            verification=verification,
            classification=classification,
        )
    category = getattr(diagnosis, "category", FailureCategory.UNKNOWN)
    if isinstance(category, str):
        try:
            category = FailureCategory(category)
        except ValueError:
            category = FailureCategory.UNKNOWN

    execute_ok = bool(getattr(execution, "success", False))
    retryable = bool(getattr(execution, "retryable", False) or getattr(diagnosis, "retryable", False))
    fp = action_fingerprint(name, args)

    def _alternate(reason: str) -> "RepairDecision":
        fallback = _family_fallback_decision(classification, reason=reason)
        if fallback.is_tool_call and fallback.tool:
            alt_args = dict(fallback.arguments or {})
            alt_fp = action_fingerprint(fallback.tool, alt_args)
            if alt_fp != fp and alt_fp not in blocked:
                return RepairDecision(
                    action=RepairAction.ALTERNATE,
                    reason=reason,
                    tool=fallback.tool,
                    arguments=alt_args,
                )
        # Soft alternate: same tool with slightly different args marker when possible.
        if name == "open_app" and args.get("name"):
            alt_args = dict(args)
            alt_args["fallback"] = "shell_or_uri"
            if action_fingerprint(name, alt_args) not in blocked:
                return RepairDecision(
                    action=RepairAction.ALTERNATE,
                    reason=reason,
                    tool=name,
                    arguments=alt_args,
                )
        return RepairDecision(action=RepairAction.FAIL, reason=f"no_alternate:{reason}", tool=name)

    # Category policy matrix
    if category == FailureCategory.INVALID_ARGS:
        return RepairDecision(
            action=RepairAction.ASK,
            reason="invalid_args",
            tool=name,
            question="Możesz sprecyzować, co mam zrobić?",
        )
    if category == FailureCategory.AMBIGUOUS_TARGET:
        return RepairDecision(
            action=RepairAction.ASK,
            reason="ambiguous_target",
            tool=name,
            question="Który cel masz na myśli?",
        )
    if category in (FailureCategory.PERMISSION_DENIED, FailureCategory.CAPABILITY_MISSING):
        return RepairDecision(action=RepairAction.FAIL, reason=category.value, tool=name)
    if category == FailureCategory.DEPENDENCY_UNAVAILABLE:
        # One soft retry, then fail honestly (partial plan continues elsewhere).
        if repair_used == 0 and retryable and not force_alternate:
            return RepairDecision(
                action=RepairAction.RETRY,
                reason="dependency_retry",
                tool=name,
                arguments=args,
            )
        return RepairDecision(action=RepairAction.FAIL, reason="dependency_unavailable", tool=name)
    if category == FailureCategory.TIMEOUT:
        if force_alternate or fp in blocked:
            return _alternate("timeout_alternate")
        if retryable:
            return RepairDecision(
                action=RepairAction.RETRY,
                reason="timeout_retry",
                tool=name,
                arguments=args,
            )
        return RepairDecision(action=RepairAction.FAIL, reason="timeout", tool=name)

    if execute_ok and ver_status in (VerifyStatus.SKIPPED, VerifyStatus.UNKNOWN):
        return RepairDecision(action=RepairAction.SKIP, reason="execute_ok_unverified", tool=name)

    if execute_ok and name in RELATIVE_MUTATIONS:
        return RepairDecision(
            action=RepairAction.FAIL if ver_status == VerifyStatus.FAILED else RepairAction.SKIP,
            reason="relative_no_remutate",
            tool=name,
        )

    if force_alternate:
        return _alternate("force_alternate")

    if category in (FailureCategory.STATE_NOT_CHANGED, FailureCategory.VERIFICATION_FAILED):
        if name in IDEMPOTENT_MUTATIONS and (not force_alternate or fp not in blocked):
            return RepairDecision(
                action=RepairAction.RETRY,
                reason="verify_failed_idempotent",
                tool=name,
                arguments=args,
            )
        return _alternate("verify_failed_alternate")

    if not execute_ok and retryable and name and (fp not in blocked or not force_alternate):
        return RepairDecision(
            action=RepairAction.RETRY,
            reason="retryable_failure",
            tool=name,
            arguments=args,
        )

    if execute_ok and ver_status == VerifyStatus.FAILED and name in IDEMPOTENT_MUTATIONS:
        return RepairDecision(
            action=RepairAction.RETRY,
            reason="verify_failed_idempotent",
            tool=name,
            arguments=args,
        )

    if not execute_ok:
        return _alternate("family_fallback")

    return RepairDecision(action=RepairAction.FAIL, reason="unrecoverable", tool=name)
