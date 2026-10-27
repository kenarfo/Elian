import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from Config import config
from HomeAssistant import CONTROLLABLE_DOMAINS, get_registry


SMART_HOME_CONFIG = config.get("smart_home", {})

DOMAIN_WORDS = {
    "climate": {
        "klimatyzacja",
        "klimatyzacje",
        "klima",
        "klime",
        "klimy",
        "klimatyzator",
        "ac",
        "aircon",
    },
    "light": {
        "swiatlo",
        "swiatla",
        "lampa",
        "lampe",
        "lampy",
        "lampka",
        "lampke",
        "oswietlenie",
        "zarowka",
    },
    "switch": {
        "wylacznik",
        "przelacznik",
        "gniazdko",
        "wtyczka",
    },
    "fan": {
        "wentylator",
        "wiatraczek",
        "fan",
    },
    "media_player": {
        "telewizor",
        "tv",
        "glosnik",
        "glosniki",
        "soundbar",
        "radio",
        "odtwarzacz",
    },
    "cover": {
        "roleta",
        "rolety",
        "zaluzje",
        "zaslona",
        "brama",
        "garaz",
    },
}

FILLER_WORDS = {
    "wlacz",
    "wylacz",
    "zapal",
    "zgas",
    "ustaw",
    "zmien",
    "zwieksz",
    "zmniejsz",
    "otworz",
    "zamknij",
    "scisz",
    "sciszy",
    "pauza",
    "odtworz",
    "na",
    "do",
    "w",
    "z",
    "ze",
    "mi",
    "prosze",
    "stopni",
    "stopnie",
    "procent",
    "jasnosc",
    "temperature",
    "temperatura",
    "kolor",
    "glosnosc",
}


def normalize_pl(text):
    if not text:
        return ""
    value = unicodedata.normalize("NFKD", str(text).lower())
    value = "".join(
        character
        for character in value
        if not unicodedata.combining(character)
    )
    value = value.replace("ł", "l")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def infer_domain(text):
    tokens = set(normalize_pl(text).split())
    matches = [
        domain
        for domain, words in DOMAIN_WORDS.items()
        if any(tok in words or _stem(tok) in {_stem(w) for w in words} | words for tok in tokens)
    ]
    if len(matches) == 1:
        return matches[0]
    return ""


def _cutoff():
    return float(SMART_HOME_CONFIG.get("match_cutoff", 0.78))


def _margin():
    return float(SMART_HOME_CONFIG.get("ambiguous_margin", 0.08))


def _entity_names(entity):
    names = [entity.friendly_name, entity.object_id.replace("_", " ")]
    names.extend(entity.aliases)
    return [normalize_pl(name) for name in names if name]


def _token_span_in(haystack, needle):
    """True if needle is a contiguous token span in haystack — never a substring of a token.

    Stops object_id 'a' from matching inside 'sypialnia' / 'jasniej'.
    """
    hay = [tok for tok in str(haystack or "").split() if tok]
    need = [tok for tok in str(needle or "").split() if tok]
    if not hay or not need:
        return False
    n = len(need)
    for i in range(len(hay) - n + 1):
        if hay[i : i + n] == need:
            return True
    return False


def _stem(token):
    token = str(token or "")
    for suffix in ("ami", "ach", "owi", "cie", "cia", "ke", "ka", "ie", "e", "y", "a"):
        min_stem = 4 if len(suffix) == 1 else 3
        if token.endswith(suffix) and len(token) - len(suffix) >= min_stem:
            return token[: -len(suffix)]
    return token


def _domain_stems(domain=""):
    if domain:
        words = DOMAIN_WORDS.get(domain, set())
        return words | {_stem(word) for word in words}
    stems = set()
    for words in DOMAIN_WORDS.values():
        stems |= words
        stems |= {_stem(word) for word in words}
    return stems


def _is_domainish(token, domain=""):
    token = str(token or "")
    if not token:
        return False
    stems = _domain_stems(domain)
    return token in stems or _stem(token) in stems


def _specific_query_tokens(query_tokens, domain):
    """Tokens that can uniquely identify a device (not domain/filler)."""
    return [tok for tok in query_tokens if not _is_domainish(tok, domain)]


def _entity_identifier_tokens(entity):
    names = _entity_names(entity)
    tokens = set()
    for name in names:
        tokens.update(name.split())
    oid = normalize_pl(str(getattr(entity, "object_id", "") or "").replace("_", " "))
    if oid:
        tokens.update(oid.split())
    return tokens


def _entity_matches_specific(entity, specific_tokens, domain):
    if not specific_tokens:
        return False
    ident = _entity_identifier_tokens(entity)
    domainish = _domain_stems(domain)
    distinctive = {
        tok for tok in ident if tok not in domainish and _stem(tok) not in domainish
    }
    distinctive_stems = {_stem(tok) for tok in distinctive}
    for token in specific_tokens:
        if token in distinctive or _stem(token) in distinctive_stems:
            return True
        if token in ident and token not in domainish:
            return True
    return False


def _utterance_room_hits(query, entities):
    """Room mentioned in speech is a hard filter. Returns (hits, room_key)."""
    try:
        from core.clarification_slots import extract_room_hint, room_matches_device

        room_key = extract_room_hint(query)
    except Exception:
        return list(entities), ""
    if not room_key:
        return list(entities), ""
    hits = [entity for entity in entities if room_matches_device(room_key, entity)]
    return hits, room_key


def _score_entity(entity, query, domain_hint=""):
    names = [name for name in _entity_names(entity) if name]
    if not names:
        return 0.0
    query_norm = normalize_pl(query)
    if not query_norm:
        return 0.0

    best = 0.0
    for name in names:
        if query_norm == name:
            best = max(best, 1.0)
        elif _token_span_in(name, query_norm) or _token_span_in(query_norm, name):
            best = max(best, 0.92)
        else:
            best = max(
                best,
                SequenceMatcher(None, query_norm, name).ratio(),
            )

    query_tokens = [
        token
        for token in query_norm.split()
        if token not in FILLER_WORDS
    ]
    name_tokens = set(" ".join(names).split())
    stemmed_names = {_stem(token) for token in name_tokens}
    for token in query_tokens:
        stemmed = _stem(token)
        if token in name_tokens or stemmed in stemmed_names:
            best = max(best, 0.9)
        else:
            for name in names:
                ratio = SequenceMatcher(None, token, name).ratio()
                if ratio >= 0.8:
                    best = max(best, 0.86)

    if query_tokens:
        overlap = sum(
            _stem(token) in stemmed_names or token in DOMAIN_WORDS.get(entity.domain, set())
            for token in query_tokens
        )
        best = max(best, overlap / len(query_tokens) * 0.88)

    if domain_hint and entity.domain == domain_hint:
        best += 0.05
    elif domain_hint and entity.domain != domain_hint:
        best -= 0.5

    return round(min(best, 1.0), 4)


@dataclass
class DeviceMatch:
    status: str
    entity: object = None
    candidates: tuple = ()
    domain: str = ""
    message: str = ""


def resolve_device(query, domain="", entities=None, allow_sensors=False):
    query = str(query or "").strip()
    domain_hint = domain or infer_domain(query)
    pool = list(entities) if entities is not None else get_registry().maybe_refresh()

    # User aliases (any brand) → entity_id before fuzzy match.
    try:
        from core.device_catalog import resolve_alias

        alias_id = resolve_alias(query)
        if alias_id:
            for entity in pool:
                if getattr(entity, "entity_id", "") == alias_id:
                    return DeviceMatch(
                        status="exact",
                        entity=entity,
                        candidates=(entity,),
                        domain=getattr(entity, "domain", domain_hint) or domain_hint,
                    )
    except Exception:
        pass

    if domain_hint:
        pool = [entity for entity in pool if entity.domain == domain_hint]
    elif not allow_sensors:
        pool = [entity for entity in pool if entity.domain in CONTROLLABLE_DOMAINS]

    if not pool:
        return DeviceMatch(
            status="none",
            domain=domain_hint,
            message=(
                "Nie znalazłem takiego urządzenia w Home Assistant."
                if query
                else "Nie wiem, którym urządzeniem sterować."
            ),
        )

    query_tokens = [
        token
        for token in normalize_pl(query).split()
        if token not in FILLER_WORDS
    ]
    domain_only = bool(domain_hint) and (
        not query_tokens
        or all(_is_domainish(token, domain_hint) for token in query_tokens)
    )

    if domain_only:
        if len(pool) == 1:
            return DeviceMatch(
                status="exact",
                entity=pool[0],
                candidates=(pool[0],),
                domain=domain_hint,
            )
        names = ", ".join(entity.display_name() for entity in pool[:4])
        return DeviceMatch(
            status="ambiguous",
            candidates=tuple(pool),
            domain=domain_hint,
            message=_clarification(pool, domain_hint),
        )

    # Distinctive tokens (object_id / non-domain name) beat domain-word overlap
    # so "lampe a" is exact A, not ambiguous A/B from stem lampa/lampe.
    specific = _specific_query_tokens(query_tokens, domain_hint)
    if specific:
        hits = [
            entity
            for entity in pool
            if _entity_matches_specific(entity, specific, domain_hint)
        ]
        hits, room_key = _utterance_room_hits(query, hits)
        if room_key and not hits:
            return DeviceMatch(
                status="none",
                domain=domain_hint,
                message=f"Nie znalazłem urządzenia w tym pomieszczeniu: {query}.",
            )
        if len(hits) == 1:
            entity = hits[0]
            return DeviceMatch(
                status="exact",
                entity=entity,
                candidates=(entity,),
                domain=domain_hint or entity.domain,
            )
        if len(hits) > 1:
            return DeviceMatch(
                status="ambiguous",
                candidates=tuple(hits),
                domain=domain_hint or hits[0].domain,
                message=_clarification(hits, domain_hint or hits[0].domain),
            )

    scored = sorted(
        (
            (_score_entity(entity, query, domain_hint), entity)
            for entity in pool
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    cutoff = _cutoff()
    viable = [item for item in scored if item[0] >= cutoff]
    if not viable:
        return DeviceMatch(
            status="none",
            domain=domain_hint,
            message=(
                f"Nie znalazłem urządzenia pasującego do: {query}."
            ),
        )

    top_score, top_entity = viable[0]
    close = [
        entity
        for score, entity in viable
        if top_score - score <= _margin()
    ]
    if len(close) > 1:
        return DeviceMatch(
            status="ambiguous",
            candidates=tuple(close),
            domain=domain_hint or top_entity.domain,
            message=_clarification(close, domain_hint or top_entity.domain),
        )

    # Room in the utterance is a hard constraint: never EXACT an entity
    # that does not mention that room (false light.a when user said sypialnia).
    room_hits, room_key = _utterance_room_hits(query, [entity for _score, entity in viable])
    if room_key:
        if not room_hits:
            return DeviceMatch(
                status="none",
                domain=domain_hint or top_entity.domain,
                message=f"Nie znalazłem urządzenia w tym pomieszczeniu: {query}.",
            )
        if top_entity not in room_hits:
            top_entity = room_hits[0]
            close = room_hits
            if len(close) > 1:
                return DeviceMatch(
                    status="ambiguous",
                    candidates=tuple(close),
                    domain=domain_hint or top_entity.domain,
                    message=_clarification(close, domain_hint or top_entity.domain),
                )

    if domain_hint and top_entity.domain != domain_hint:
        print(
            "[elian] device_mismatch: "
            f"requested={domain_hint} resolved={top_entity.entity_id}"
        )
        return DeviceMatch(
            status="mismatch",
            entity=top_entity,
            domain=domain_hint,
            message=(
                f"To polecenie dotyczy {domain_hint}, "
                f"a {top_entity.display_name()} jest urządzeniem "
                f"typu {top_entity.domain}."
            ),
        )

    return DeviceMatch(
        status="exact",
        entity=top_entity,
        candidates=(top_entity,),
        domain=top_entity.domain,
    )


def _clarification(entities, domain):
    names = [entity.display_name() for entity in entities]
    if len(names) == 2:
        return f"Które urządzenie: {names[0]} czy {names[1]}?"
    listed = ", ".join(names[:4])
    label = {
        "climate": "klimatyzację",
        "light": "światło",
        "fan": "wentylator",
        "media_player": "odtwarzacz",
        "cover": "osłonę",
        "switch": "przełącznik",
    }.get(domain, "urządzenie")
    return f"Które {label} mam wybrać: {listed}?"


def reject_domain_mismatch(requested_domain, entity):
    if not requested_domain or entity is None:
        return None
    if entity.domain == requested_domain:
        return None
    print(
        "[elian] device_mismatch: "
        f"requested={requested_domain} resolved={entity.entity_id}"
    )
    return (
        f"Nie wykonam tego. Prosiłeś o {requested_domain}, "
        f"a {entity.display_name()} to {entity.domain}."
    )
