"""Deterministic multi-intent and natural-command parsing for Elian."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ConversationContext import get as get_context
from DeviceResolver import infer_domain, normalize_pl


PRONOUNS = {
    "ja",
    "go",
    "je",
    "jej",
    "niego",
    "nia",
    "tam",
    "to",
    "ten",
    "te",
    "ta",
    "teraz",
    "jeszcze",
}

ACTION_VERBS = (
    "wlacz",
    "wlaczyc",
    "wylacz",
    "zapal",
    "zapalic",
    "zgas",
    "zgasic",
    "ustaw",
    "otworz",
    "zamknij",
    "zwieksz",
    "zmniejsz",
    "scisz",
    "poszukaj",
    "wyszukaj",
    "znajdz",
    "odpal",
    "uruchom",
    "zrob",
    "przyciemnij",
    "rozjasnij",
    "sciemnij",
)

LIGHT_COLOR_ALIASES = {
    "czerwony": "czerwony",
    "czerwona": "czerwony",
    "czerwone": "czerwony",
    "czerwono": "czerwony",
    "czerwien": "czerwony",
    "zielony": "zielony",
    "zielona": "zielony",
    "zielone": "zielony",
    "zielono": "zielony",
    "zielen": "zielony",
    "niebieski": "niebieski",
    "niebieska": "niebieski",
    "niebieskie": "niebieski",
    "niebiesko": "niebieski",
    "zolty": "żółty",
    "zolta": "żółty",
    "zolte": "żółty",
    "zolto": "żółty",
    "fioletowy": "fioletowy",
    "fioletowa": "fioletowy",
    "fioletowe": "fioletowy",
    "fioletowo": "fioletowy",
}

LIGHT_TEMP_ALIASES = {
    "bardzo cieple": "very_warm",
    "bardzo ciepla": "very_warm",
    "cieple": "warm",
    "ciepla": "warm",
    "cieplo": "warm",
    "cieple swiatlo": "warm",
    "ciepla biel": "warm",
    "neutralne": "neutral",
    "neutralna": "neutral",
    "neutralny": "neutral",
    "neutralne swiatlo": "neutral",
    "neutralna biel": "neutral",
    "neutralny kolor": "neutral",
    "dzienne": "daylight",
    "dzienne swiatlo": "daylight",
    "zimne": "cool",
    "zimna": "cool",
    "zimne swiatlo": "cool",
    "zimna biel": "cool",
    "chlodne": "cool",
    "chlodna": "cool",
}

DOMAIN_DEFAULTS = {
    "climate": "klimatyzacja",
    "light": "swiatlo",
    "fan": "wentylator",
    "media_player": "telewizor",
    "cover": "rolety",
    "switch": "przelacznik",
}


@dataclass(frozen=True)
class ParsedCommand:
    action: str
    target: str = ""
    domain: str = ""
    value: int = 0
    extra: str = ""
    relative: bool = False
    confidence: float = 1.0
    entity_id: str = ""
    application: str = ""
    query: str = ""
    clarification: str = ""


def _norm(text):
    return normalize_pl(text)


def _uses_pronoun(text):
    return bool(set(_norm(text).split()) & PRONOUNS)


def _context_target(domain=""):
    context = get_context()
    if not context:
        return "", "", ""
    if domain == "climate" and context.get("last_climate"):
        return (
            context.get("last_climate_name") or context.get("last_device_name") or "",
            "climate",
            context.get("last_climate", ""),
        )
    last_device = str(context.get("last_device") or "")
    last_domain = str(context.get("last_domain") or "")
    if domain == "light":
        light_id = str(context.get("last_light") or "")
        if not light_id and (last_device.startswith("light.") or last_domain == "light"):
            light_id = last_device
        if light_id:
            return (
                context.get("last_light_name") or context.get("last_device_name") or "",
                "light",
                light_id,
            )
    # Prefer typed last entity when domain unspecified.
    if not domain:
        if context.get("last_light") and not context.get("last_climate"):
            return (
                context.get("last_light_name") or context.get("last_device_name") or "",
                "light",
                context.get("last_light", ""),
            )
        if context.get("last_climate") and not context.get("last_light"):
            return (
                context.get("last_climate_name") or context.get("last_device_name") or "",
                "climate",
                context.get("last_climate", ""),
            )
    if domain and context.get("last_domain") and context.get("last_domain") != domain:
        return "", "", ""
    return (
        context.get("last_device_name") or context.get("last_device") or "",
        context.get("last_domain", ""),
        context.get("last_device", ""),
    )


def _split_clauses(text):
    normalized = _norm(text)
    if not normalized:
        return []

    verb_alt = "|".join(ACTION_VERBS)
    # Coordinating "i" before a new action becomes a hard separator.
    normalized = re.sub(rf"\s+i\s+(?=(?:{verb_alt})\b)", ", ", normalized)

    attr_alt = r"jasnosc|temperatur|kolor|glosnosc|ja\b|go\b|je\b|to\b|jeszcze\b"

    parts = re.split(r"\s*(?:,|;|\boraz\b)\s*", normalized)
    clauses = []
    for part in parts:
        pieces = re.split(rf"\s+(?=(?:{verb_alt})\b)", part)
        for piece in pieces:
            piece = re.sub(r"\s+\bi\b\s*$", "", piece).strip(" ,.;")
            if not piece:
                continue
            and_parts = re.split(rf"\s+i\s+(?=(?:{attr_alt}))", piece)
            for item in and_parts:
                item = item.strip(" ,.;")
                if item:
                    clauses.append(item)
    return clauses or [normalized]


def _parse_light_temp(text):
    normalized = _norm(text)
    for phrase, key in sorted(LIGHT_TEMP_ALIASES.items(), key=lambda item: -len(item[0])):
        if re.search(rf"\b{re.escape(phrase)}\b", normalized):
            return key, phrase
    return "", ""


def _parse_color(text):
    normalized = _norm(text)
    if not any(marker in normalized for marker in ("kolor", "ustaw", "swiat", "lamp")):
        return ""
    if "klim" in normalized or "stopn" in normalized:
        return ""
    for phrase, canonical in sorted(LIGHT_COLOR_ALIASES.items(), key=lambda item: -len(item[0])):
        if re.search(rf"\b{re.escape(_norm(phrase))}\b", normalized):
            return canonical
    return ""


def _parse_brightness(text):
    normalized = _norm(text)
    raw = str(text or "")
    if "jasn" in normalized or "%" in raw or "procent" in normalized:
        match = re.search(r"(\d{1,3})\s*(?:%|procent)?", raw if "%" in raw else normalized)
        if match:
            return max(0, min(100, int(match.group(1))))
    match = re.search(r"\b(\d{1,3})\s*(?:%|procent)\b", raw if "%" in raw else normalized)
    if match:
        return max(0, min(100, int(match.group(1))))
    # "ustaw … na 40" after % was stripped by normalize_pl — still brightness-capable.
    match = re.search(r"\b(?:na|do)\s+(\d{1,3})\b", normalized)
    if match:
        return max(0, min(100, int(match.group(1))))
    return None


def _parse_absolute_temperature(text):
    normalized = _norm(text)
    if "jasn" in normalized or "procent" in normalized:
        return None
    if infer_domain(normalized) == "light" or "swiat" in normalized or "lamp" in normalized:
        return None
    match = re.search(
        r"(?:na|do)\s+(\d{1,2})(?:\s*(?:st(?:opni(?:e|a)?)?))?\b",
        normalized,
    )
    if match:
        return int(match.group(1))
    match = re.search(r"\b(\d{1,2})\s*(?:stopni(?:e|a)?|c)\b", normalized)
    if match:
        return int(match.group(1))
    return None


def _device_phrase(text, domain_hint=""):
    cleaned = _norm(text)
    cleaned = re.sub(
        rf"^(?:{'|'.join(ACTION_VERBS)})\s+",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"\b(?:na|do)\s+\d{1,3}(?:\s*(?:procent|stopni(?:e|a)?))?\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"\b\d{1,3}\b", " ", cleaned)
    for phrase in LIGHT_TEMP_ALIASES:
        cleaned = cleaned.replace(phrase, " ")
    for phrase in LIGHT_COLOR_ALIASES:
        cleaned = re.sub(rf"\b{re.escape(_norm(phrase))}\b", " ", cleaned)
    cleaned = re.sub(
        r"\b(?:jasnosc|jasniej|ciemniej|cieplej|zimniej|kolor|"
        r"temperature|temperatura|procent|stopni|stopnie|stopnia|"
        r"troche|zrob|o)\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(
        r"\b(?:ja|go|je|jej|to|tam|teraz|jeszcze)\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"\b(?:na|do|i)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned and domain_hint:
        return DOMAIN_DEFAULTS.get(domain_hint, "")
    return cleaned


def _resolve_target(clause, domain_hint="", carry_target="", carry_domain="", carry_entity=""):
    explicit = _device_phrase(clause, domain_hint)
    if _uses_pronoun(clause) or not explicit:
        if carry_target and (not domain_hint or not carry_domain or carry_domain == domain_hint):
            return carry_target, carry_domain or domain_hint, carry_entity
        ctx_target, ctx_domain, ctx_entity = _context_target(domain_hint)
        if ctx_target or ctx_entity:
            return ctx_target or DOMAIN_DEFAULTS.get(domain_hint, ""), ctx_domain or domain_hint, ctx_entity
    domain = domain_hint or infer_domain(clause) or infer_domain(explicit)
    return explicit or DOMAIN_DEFAULTS.get(domain, ""), domain, ""


def _parse_clause(clause, carry_target="", carry_domain="", carry_entity=""):
    text = _norm(clause)
    if not text:
        return None

    relative_temp = re.search(
        r"(?:o\s+)?(\d{1,2})\s*(?:stopni(?:e|a)?)?\s*(cieplej|zimniej|mniej|wiecej)",
        text,
    )
    if relative_temp or re.search(r"\b(?:troche\s+)?(?:cieplej|zimniej)\b", text):
        delta = 2
        if relative_temp:
            delta = int(relative_temp.group(1))
            direction = relative_temp.group(2)
        else:
            direction = "cieplej" if "cieplej" in text else "zimniej"
        if direction in ("zimniej", "mniej"):
            delta = -delta
        # Relative comfort phrases rarely name the device; prefer context.
        device_text = text if infer_domain(text) == "climate" or "klim" in text else ""
        target, domain, entity_id = _resolve_target(
            device_text, "climate", carry_target, carry_domain, carry_entity
        )
        return ParsedCommand(
            action="adjust_temperature",
            target=target or "klimatyzacja",
            domain="climate",
            value=delta,
            relative=True,
            entity_id=entity_id,
        )

    if re.search(
        r"\b(?:jasniej|ciemniej|troche\s+jasniej|troche\s+ciemniej|"
        r"przyciemn\w*|rozjasn\w*|sciemn\w*)\b",
        text,
    ):
        brighter = bool(re.search(r"\b(?:jasniej|rozjasn)", text))
        delta = 15 if brighter else -15
        target, domain, entity_id = _resolve_target(
            text, "light", carry_target, carry_domain, carry_entity
        )
        if _uses_pronoun(text) and not entity_id:
            # Pronoun without a canonical entity — clarification only as last resort.
            return ParsedCommand(
                action="adjust_brightness",
                target=target,
                domain="light",
                value=delta,
                relative=True,
                clarification="Które światło mam rozjaśnić?" if brighter else "Które światło mam przyciemnić?",
            )
        return ParsedCommand(
            action="adjust_brightness",
            target=target or "swiatlo",
            domain="light",
            value=delta,
            relative=True,
            entity_id=entity_id,
        )

    color = _parse_color(text)
    if color:
        target, domain, entity_id = _resolve_target(
            text, "light", carry_target, carry_domain, carry_entity
        )
        return ParsedCommand(
            action="light_color",
            target=target or "swiatlo",
            domain="light",
            extra=color,
            entity_id=entity_id,
        )

    if re.search(r"\b(?:troche\s+)?(?:ciszej|glosniej)\b", text) and infer_domain(text) != "media_player":
        return ParsedCommand(
            action="volume_down" if "ciszej" in text else "volume_up",
        )

    brightness = _parse_brightness(text)
    temp_key, _temp_phrase = _parse_light_temp(text)
    abs_temp = _parse_absolute_temperature(text)
    domain = infer_domain(text)

    # Follow-up "ustaw na 50" after light — brightness, never climate.
    brightness_from_temp = False
    if (
        brightness is None
        and abs_temp is not None
        and abs_temp <= 100
        and carry_domain == "light"
        and "klim" not in text
        and "stopn" not in text
        and "stopni" not in text
    ):
        brightness = abs_temp
        brightness_from_temp = True

    if brightness is not None and (
        "jasn" in text
        or "procent" in text
        or domain == "light"
        or carry_domain == "light"
        or brightness_from_temp
        or (not domain and not abs_temp and "klim" not in text)
    ) and "klim" not in text and (
        abs_temp is None
        or brightness_from_temp
        or domain == "light"
        or carry_domain == "light"
        or "procent" in text
    ):
        target, domain, entity_id = _resolve_target(
            text, "light", carry_target, carry_domain, carry_entity
        )
        return ParsedCommand(
            action="light_brightness",
            target=target or "swiatlo",
            domain="light",
            value=brightness,
            entity_id=entity_id,
        )

    if temp_key and domain in ("", "light"):
        target, domain, entity_id = _resolve_target(
            text, "light", carry_target, carry_domain, carry_entity
        )
        return ParsedCommand(
            action="light_temperature",
            target=target or "swiatlo",
            domain="light",
            extra=temp_key,
            entity_id=entity_id,
        )

    if abs_temp is not None and (
        domain == "climate"
        or "klim" in text
        or "stopn" in text
        or (carry_domain == "climate" and "klim" not in text and carry_domain != "light")
        or (_uses_pronoun(text) and carry_domain == "climate")
        or (not domain and carry_domain == "climate" and carry_domain != "light")
    ):
        # Avoid treating light brightness-like numbers as climate when light words present.
        if domain == "light" or "swiat" in text or "lamp" in text or carry_domain == "light":
            pass
        else:
            target, domain, entity_id = _resolve_target(
                text, "climate", carry_target, carry_domain, carry_entity
            )
            return ParsedCommand(
                action="set_temperature",
                target=target or "klimatyzacja",
                domain="climate",
                value=abs_temp,
                entity_id=entity_id,
            )

    if re.search(r"\b(?:wlacz|wlaczyc|zapal|zapalic|odpal)\b", text):
        domain = domain or infer_domain(text)
        target, domain, entity_id = _resolve_target(
            text, domain, carry_target, carry_domain, carry_entity
        )
        if domain == "light" or "lamp" in text or "swiat" in text:
            return ParsedCommand(
                action="device_on",
                target=target or "swiatlo",
                domain="light",
                entity_id=entity_id,
            )
        if domain == "climate" or "klim" in text:
            if abs_temp is not None:
                return ParsedCommand(
                    action="set_temperature",
                    target=target or "klimatyzacja",
                    domain="climate",
                    value=abs_temp,
                    entity_id=entity_id,
                )
            return ParsedCommand(
                action="device_on",
                target=target or "klimatyzacja",
                domain="climate",
                entity_id=entity_id,
            )
        if domain:
            return ParsedCommand(
                action="device_on",
                target=target,
                domain=domain,
                entity_id=entity_id,
            )

    if re.search(r"\b(?:wylacz|zgas|zgasic)\b", text):
        domain = domain or infer_domain(text)
        target, domain, entity_id = _resolve_target(
            text, domain, carry_target, carry_domain, carry_entity
        )
        if domain == "light" or "lamp" in text or "swiat" in text:
            return ParsedCommand(
                action="device_off",
                target=target or "swiatlo",
                domain="light",
                entity_id=entity_id,
            )
        if domain == "climate" or "klim" in text:
            return ParsedCommand(
                action="device_off",
                target=target or "klimatyzacja",
                domain="climate",
                entity_id=entity_id,
            )
        if domain:
            return ParsedCommand(
                action="device_off",
                target=target,
                domain=domain,
                entity_id=entity_id,
            )

    if re.search(r"\b(?:otworz|wlacz|uruchom|odpal)\b", text):
        for app in ("spotify", "chrome", "discord", "kalkulator", "notatnik"):
            if app in text:
                return ParsedCommand(action="open_app", target=app, application=app)

    context = get_context()
    if context.get("last_application") == "spotify" or context.get("last_app") == "spotify":
        play = re.match(r"^(?:wlacz|pusc|odtworz)\s+(.+)$", text)
        if play and infer_domain(text) == "":
            return ParsedCommand(
                action="spotify_play",
                query=play.group(1).strip(),
                application="spotify",
            )

    return None


def parse_natural_comfort(text):
    normalized = _norm(text)
    context = get_context()

    if re.search(r"\bjest\s+mi\s+zimno\b", normalized) or normalized in {
        "zimno mi",
        "jest zimno",
        "za zimno",
    }:
        return ParsedCommand(
            action="comfort_cold",
            domain="climate",
            target=context.get("last_climate_name")
            or context.get("last_device_name")
            or "klimatyzacja",
            entity_id=context.get("last_climate")
            or (
                context.get("last_device")
                if context.get("last_domain") == "climate"
                else ""
            ),
            value=2,
            relative=True,
        )

    if re.search(r"\bjest\s+mi\s+goraco\b", normalized) or normalized in {
        "goraco mi",
        "jest goraco",
        "za goraco",
    }:
        return ParsedCommand(
            action="comfort_hot",
            domain="climate",
            target=context.get("last_climate_name")
            or context.get("last_device_name")
            or "klimatyzacja",
            entity_id=context.get("last_climate")
            or (
                context.get("last_device")
                if context.get("last_domain") == "climate"
                else ""
            ),
            value=-2,
            relative=True,
        )

    if re.search(r"\b(?:za\s+ciemno|jest\s+ciemno|ciemno\s+tu)\b", normalized):
        return ParsedCommand(
            action="comfort_dark",
            domain="light",
            target=context.get("last_light_name") or "swiatlo",
            entity_id=context.get("last_light")
            or (
                context.get("last_device")
                if context.get("last_domain") == "light"
                else ""
            ),
            value=15,
            relative=True,
        )

    if re.search(r"\b(?:za\s+jasno|jest\s+za\s+jasno)\b", normalized):
        return ParsedCommand(
            action="comfort_bright",
            domain="light",
            target=context.get("last_light_name") or "swiatlo",
            entity_id=context.get("last_light")
            or (
                context.get("last_device")
                if context.get("last_domain") == "light"
                else ""
            ),
            value=-15,
            relative=True,
        )

    return None


def looks_multi_command(text):
    normalized = _norm(text)
    if "," in text or ";" in text or " oraz " in normalized:
        return True
    verb_alt = "|".join(ACTION_VERBS)
    if len(re.findall(rf"\b(?:{verb_alt})\b", normalized)) >= 2:
        return True
    if re.search(
        rf"\bi\s+(?:jasnosc|temperatur|ustaw|wlacz|wylacz|zgas|zapal|otworz|ja\b|go\b)",
        normalized,
    ):
        return True
    return False


def parse_commands(text):
    if not str(text or "").strip():
        return []

    natural = parse_natural_comfort(text)
    if natural is not None and not looks_multi_command(text):
        return [natural]

    clauses = _split_clauses(text)
    commands = []
    carry_target = ""
    carry_domain = ""
    carry_entity = ""

    # Seed carry from conversation context for follow-ups / pronouns.
    ctx_target, ctx_domain, ctx_entity = _context_target()
    if ctx_domain:
        carry_target = ctx_target
        carry_domain = ctx_domain
        carry_entity = ctx_entity

    for clause in clauses:
        command = _parse_clause(clause, carry_target, carry_domain, carry_entity)
        if command is None and commands:
            if (
                _parse_brightness(clause) is not None
                or _parse_light_temp(clause)[0]
                or _parse_absolute_temperature(clause) is not None
            ):
                command = _parse_clause(
                    clause,
                    carry_target or commands[-1].target,
                    carry_domain or commands[-1].domain,
                    carry_entity or commands[-1].entity_id,
                )
        if command is None:
            continue
        commands.append(command)
        if command.domain:
            carry_domain = command.domain
            carry_target = command.target or carry_target
            carry_entity = command.entity_id or carry_entity

    collapsed = []
    for command in commands:
        if (
            collapsed
            and collapsed[-1].action == "device_on"
            and collapsed[-1].domain == "climate"
            and command.action == "set_temperature"
            and (
                not command.target
                or not collapsed[-1].target
                or command.target == collapsed[-1].target
                or "klim" in command.target
                or "klim" in collapsed[-1].target
            )
        ):
            collapsed[-1] = ParsedCommand(
                action="set_temperature",
                target=collapsed[-1].target or command.target,
                domain="climate",
                value=command.value,
                entity_id=collapsed[-1].entity_id or command.entity_id,
            )
            continue
        collapsed.append(command)

    return collapsed


def is_actionable_utterance(text):
    if parse_natural_comfort(text):
        return True
    if looks_multi_command(text):
        return True
    return bool(parse_commands(text))
