# ==========================
# ELIAN — deterministic intent family (Phase 10.1 / 10.3)
# ==========================
"""Pre-planner intent classification — constraint only, no side effects."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class IntentFamily(str, Enum):
    APP = "APP"
    MEDIA = "MEDIA"
    SMART_HOME = "SMART_HOME"
    REMINDER = "REMINDER"
    CALENDAR = "CALENDAR"
    VISION = "VISION"
    WEB = "WEB"
    KNOWLEDGE = "KNOWLEDGE"
    SYSTEM = "SYSTEM"
    PREFERENCE = "PREFERENCE"
    CONVERSATION_CONTROL = "CONVERSATION_CONTROL"
    CORRECTION = "CORRECTION"
    PROCEDURE = "PROCEDURE"
    COMPUTER = "COMPUTER"
    UNKNOWN = "UNKNOWN"


KNOWN_APPS: Dict[str, str] = {
    "spotify": "Spotify",
    "discord": "Discord",
    "chrome": "Chrome",
    "youtube music": "YouTube Music",
    "youtube": "YouTube Music",
    "notatnik": "Notatnik",
    "kalkulator": "Kalkulator",
    "notepad": "Notepad",
    "steam": "Steam",
    "epic games": "Epic Games",
    "epic": "Epic Games",
    "netflix": "Netflix",
    "github": "GitHub",
    "gmail": "Gmail",
    "cursor": "Cursor",
    "cursorze": "Cursor",
    "cursora": "Cursor",
    "vscode": "VS Code",
    "vs code": "VS Code",
}

APP_VERBS = re.compile(
    r"\b(?:odpali[cć]|odpal|otw[oó]rz|otworz|uruchom|włącz|wlacz|launch|open)\b",
    re.I,
)
APP_CLOSE_VERBS = re.compile(
    r"\b(?:zamknij|zamkn[aą][cć]|zamknac|zakończ|zakoncz|close)\b",
    re.I,
)
# Includes natural / conditional play phrasing (not only leading "puść").
MEDIA_SIGNAL = re.compile(
    r"\b(?:"
    r"pu[sś]ci[cć]|pu[sś][cć]|pu[sś]cimy|pu[sś]cisz|pu[sś]ci[lł]by[sś]|"
    r"odtw[oó]rz|odtworz|"
    r"graj|zagraj|play|"
    r"pos[lł]ucha[cć]|posluchac|"
    r"chc[eę]\s+pos[lł]ucha[cć]|chce\s+posluchac"
    r")\b",
    re.I,
)
KNOWLEDGE_PATTERNS = re.compile(
    r"\b(?:"
    r"co\s+oznacza|czym\s+jest|co\s+to\s+jest|"
    r"wyjaśnij|wyjasnij|co\s+by\s+si[eę]\s+stało|gdybym"
    r")\b",
    re.I,
)
REMINDER_PATTERNS = re.compile(
    r"\b(?:przypomnij|przypomnienie|przypomnienia|pingnij|pingn[aą][cć]|pingnac)\b",
    re.I,
)
REMINDER_TIME_HINT = re.compile(
    r"\b(?:"
    r"za\s+(?:\d+\s*(?:minut[ey]?|min|godzin[ey]?|h|sekund[ey]?|sec)|"
    r"kwadrans|p[oó]ł\s+godziny|pol\s+godziny|godzin[eę]|godzine|h)|"
    r"jutro(?:\s+o\s+\d{1,2}(?::\d{2})?)?|"
    r"dzi[sś](?:\s+o\s+\d{1,2}(?::\d{2})?)?|"
    r"o\s+\d{1,2}(?::\d{2})?"
    r")\b",
    re.I,
)
VISION_DESCRIBE = re.compile(
    r"\b(?:"
    r"co\s+(?:mam\s+)?(?:teraz\s+)?na\s+ekranie|"
    r"co\s+teraz\s+widz(?:isz|[eę](?:sz)?)|"
    r"co\s+widz(?:isz|[eę](?:sz)?)\s+na\s+(?:ekranie|monitorze)|"
    r"co\s+jest\s+(?:teraz\s+)?(?:otwarte|aktywne)|"
    r"na\s+co\s+patrz[eę]|"
    r"poka[zż]\s+ekran"
    r")\b",
    re.I,
)
VISION_ACTION = re.compile(
    r"\b(?:kliknij|znajd[zź]\s+(?:element|przycisk)|przeci[aą]gnij)\b",
    re.I,
)
COMPOUND_CONNECTOR = re.compile(
    r"\s+(?:i|oraz|potem|a\s+potem|a\s+p[oó][zź]niej|nast[eę]pnie)\s+",
    re.I,
)
SECOND_ACTION = re.compile(
    r"\b(?:"
    r"zwi[eę]ksz|zmniejsz|g[lł]o[sś]niej|ciszej|[sś]cisz|przycisz|wycisz|volume|"
    r"pu[sś][cć]|odtw[oó]rz|graj|"
    r"otw[oó]rz|uruchom|odpal|zamknij|wyłącz|wylacz|zakończ|"
    r"ustaw|wejd[zź]|wyszukaj|"
    r"kliknij|wpisz|zaznacz|zminimalizuj|zmaksymalizuj|przewi[nń]|cofnij|"
    r"wy[sś]lij|napisz|"
    r"przypomnij"
    r")\b",
    re.I,
)
GENERIC_MEDIA_QUERY = re.compile(
    r"^(?:muzyk[aęę]|co[sś]|cos|utw[oó]r|piosenk[eęę]|something)$",
    re.I,
)
CALENDAR_PATTERNS = re.compile(
    r"\b(?:kalendarz|wydarzenie|spotkanie|utw[oó]rz\s+wydarzenie|utworz\s+wydarzenie|"
    r"co\s+mam\s+dzi[sś]|plan\s+na\s+dzi[sś]|harmonogram)\b",
    re.I,
)
WEB_PATTERNS = re.compile(
    r"(?:"
    r"\b(?:wyszukaj|poszukaj|znajd[zź]|google|internet|wygoogluj)\b|"
    r"\bco\s+(?:sie[cć]|w\s+sieci|internet|google)\s+(?:m[oó]wi|pisze)\b|"
    r"\bco\s+(?:m[oó]wi|pisze)\s+(?:sie[cć]|w\s+sieci|internet|google)\b|"
    r"\baktualn\w*|"
    r"\bwiadomo[sś]ci\b|"
    r"\bkto\s+jest\s+teraz\b|"
    r"\bco\s+wydarzy\w*|"
    r"\bile\s+kosztuje\b|"
    r"\bcena\s+(?:obecna|aktualna|teraz)\b"
    r")",
    re.I,
)
HA_EXPLICIT = re.compile(
    r"(?:"
    r"\b(?:włącz|wlacz|wyłącz|wylacz|zapal|z?ga[sś]|wyga[sś]|odetnij\s+(?:pr[aą]d|zasilanie)|świat[łl]\w*|swiatl\w*|"
    r"lamp(?:a|e|ę|y|ie|ą)|klimatyzac|klima|"
    r"temperatur|jasno[sś][cć]|przyciemn|rozja[sś]n|ściemn|sciemn|"
    r"chłodz|chlodz|grzan|osusz|nawiew|stopn|procent)\b"
    r"|\d+\s*%"
    r")",
    re.I,
)
# Situational lighting: compositional cues (darkness / weak vision / want light),
# not a growing whitelist of full phrases.
_LIGHT_DARK_STEM = re.compile(
    r"\b(?:przyciemn\w*|ściemn\w*|sciemn\w*|ciemn\w*|mrok\w*)\b",
    re.I,
)
# English room-dark cues (en-US Jarvis); avoid bare "dark mode"/noun-only noise.
_LIGHT_DARK_EN = re.compile(
    r"(?:"
    r"\btoo\s+dark\b|"
    r"\bdark(?:er)?\s+(?:in\s+here|here|in\s+the\s+room)\b|"
    r"\b(?:a\s+bit|kinda|kind\s+of|rather|pretty)\s+dark(?:er)?\b|"
    r"\bit'?s\s+(?:a\s+bit\s+|too\s+)?dark\b|"
    r"\bcan'?t\s+see\b|"
    r"\bhard\s+to\s+see\b|"
    r"\bdim\s+(?:in\s+here|here)\b"
    r")",
    re.I,
)
_LIGHT_DARK_METAPHOR = re.compile(
    r"\bjak\s+w\s+(?:jaskini|piwnicy|grobie)\b|"
    r"\blike\s+a\s+(?:cave|basement|tomb)\b",
    re.I,
)
_LIGHT_VISION = re.compile(r"\b(?:widz\w*|wida[cć]|see|seeing)\b", re.I)
_LIGHT_VISION_WEAK = re.compile(
    r"\b(?:ledwo|ledwie|s[lł]abo|kiepsko|źle|zle|barely|hardly|poorly)\b",
    re.I,
)
_LIGHT_NEG = re.compile(r"\b(?:nie|nic|żad\w*|zad\w*|can'?t|cannot|no)\b", re.I)
_LIGHT_BRIGHTEN = re.compile(
    r"\b(?:ja[sś]niej\w*|rozja[sś]n\w*|bright(?:en|er)?|more\s+light)\b",
    re.I,
)
_LIGHT_NEED = re.compile(
    r"(?:"
    r"\b(?:dodaj|wi[eę]cej|potrzeb\w*|przyda\w*|brak\w*)\s+świat[lł]\w*\b|"
    r"\bświat[lł]\w*\s+(?:by\s+się\s+)?przyda\w*\b|"
    r"\b(?:coś\s+)?ze\s+świat[lł]\w*\b|"
    r"\b(?:need|want|more)\s+(?:some\s+)?(?:more\s+)?light\b|"
    # Require "light(s)" — bare "turn up" is volume, not lighting.
    r"\bturn\s+(?:the\s+)?lights?\s+up\b|"
    r"\bturn\s+up\s+(?:the\s+)?lights?\b"
    r")",
    re.I,
)
_LIGHT_DIM_IMPERATIVE = re.compile(
    r"\b(?:przyciemn\w*|ściemn\w*|sciemn\w*|ciemniej|dim(?:mer)?|darker)\w*\b",
    re.I,
)

# Backward-compatible aliases for .search callers.
IMPLICIT_LIGHT_DARK = _LIGHT_DARK_STEM
IMPLICIT_LIGHT_BRIGHTER = _LIGHT_BRIGHTEN


_LIGHT_DARK_OPINION = re.compile(
    r"(?:"
    r"\bnie\s+(?:lubi|chc|znosz|prefer|nienawid)\w*.{0,48}ciemn|"
    r"\bciemn\w*.{0,24}nie\s+(?:lubi|chc|znosz|prefer|nienawid)\w*"
    r")",
    re.I,
)


def looks_insufficient_light(text: str) -> bool:
    """Too-dark / hard-to-see — compositional, word-order free."""
    raw = str(text or "")
    if not raw.strip():
        return False
    # Garbled STT ("Nie widzisz nowotni") is ASK / unclear — not a lighting goal.
    if re.search(r"\b(?:zr[oó]bce|nowotni|widzisz\s+nowot|to\s+zr[oó]bce)\b", raw, re.I):
        return False
    # Opinion about darkness ("nie lubię ciemności") is not a lighting goal.
    if _LIGHT_DARK_OPINION.search(raw):
        return False
    if _LIGHT_DARK_STEM.search(raw):
        return True
    if _LIGHT_DARK_EN.search(raw):
        return True
    if _LIGHT_DARK_METAPHOR.search(raw):
        return True
    if _LIGHT_VISION.search(raw) and (
        _LIGHT_NEG.search(raw) or _LIGHT_VISION_WEAK.search(raw)
    ):
        return True
    return False


def looks_want_brighter(text: str) -> bool:
    """Ask for more light / brightness."""
    raw = str(text or "")
    if not raw.strip():
        return False
    if _LIGHT_BRIGHTEN.search(raw):
        return True
    if _LIGHT_NEED.search(raw):
        return True
    return False

SYSTEM_PATTERNS = re.compile(
    r"(?:"
    r"\b(?:głośniej|ciszej|volume|vol|przycisz)\b|"
    r"\bzwiększ\s+gło|"
    r"\bzmniejsz\s+gło|"
    r"\b[sś]cisz|"
    r"\bzejd[zź]\s+z\s+(?:vol|g[lł]o[sś])|"
    r"\bustaw\s+głośniej|"
    r"\bpodgłośn|"
    r"\bza\s+g[lł]o[sś]no\b|"
    r"\bza\s+cicho\b|"
    r"\bza\s+du[zż]o\s+g[lł]o[sś]u\b"
    r")",
    re.I,
)
CONTROL_PATTERNS = re.compile(
    r"^(?:stop|cisza|wystarczy|nieważne|niewazne|anuluj|czekaj|moment)$",
    re.I,
)
# Compound short thanks/acks ("ok spoko", "dzięki stary") — not residual confirmations.
_ACK_CORE = (
    r"ok|okej|okay|dobra|dobrze|spoko|jasne|git|luz|super|wiem|rozumiem|"
    r"dzi[eę]kuj[eę]|dzi[eę]ki|dzi[eę]ks|thx|thanks|thank\s+you"
)
_ACK_CASUAL = r"stary|ziom|bro|ser|kole[sś]|elo"
ACKNOWLEDGEMENT_ONLY = re.compile(
    r"^(?:"
    r"(?:" + _ACK_CORE + r")"
    r"(?:\s+(?:" + _ACK_CORE + r"|" + _ACK_CASUAL + r")){0,3}"
    r"|"
    r"dzi[eę]ki\s+wielkie|wielkie\s+dzi[eę]ki|"
    r"(?:ok|okej|okay|spoko)\s*[,!]?\s*dzi[eę]ki|"
    r"dzi[eę]ki\s*[,!]?\s*(?:ok|okej|okay|spoko)"
    r")[!.…]*$",
    re.I,
)
ACKNOWLEDGEMENT_PREFIX = re.compile(
    r"^(?:"
    r"ok|okej|okay|dobra|dobrze|spoko|"
    r"dzi[eę]kuj[eę]|dzi[eę]ki|dzi[eę]ks|"
    r"thx|thanks|thank\s+you|"
    r"rozumiem|jasne|super"
    r")[,!.…]+\s*",
    re.I,
)
# Polarity guard — these look like ACK tokens but are NOT acknowledgements.
NEGATED_COMPREHENSION = re.compile(
    r"(?:"
    r"\bnie\s+rozumiem\b|"
    r"\bwcale\s+nie\s+rozumiem\b|"
    r"\bdalej\s+nie\s+rozumiem\b|"
    r"\bnadal\s+nie\s+rozumiem\b|"
    r"\bjeszcze\s+nie\s+rozumiem\b|"
    r"\bwci[aą][zż]\s+nie\s+rozumiem\b|"
    r"\bwci[aą][zż]\s+nie\s+wiem\b|"
    r"\bjeszcze\s+nie\s+wiem\b|"
    r"\bnadal\s+nie\s+wiem\b"
    r")",
    re.I,
)
OPINION_ONLY = re.compile(
    r"^(?:spotify|youtube(?:\s+music)?)\s+(?:jest\s+)?(?:fajne|fajny|fajna|super|ok|dobre|nudne|nudny|nudna)[.!]?$"
    r"|^(?:spotify|youtube(?:\s+music)?)\s+nie\s+jest\s+(?:nudne|nudny|nudna|fajne|fajny|fajna)(?:[,.].*)?[.!]?$",
    re.I,
)
CORRECTION_PATTERNS = re.compile(
    r"^\s*nie\s*,\s*nie\b|"
    r"^\s*nie[,.]?\s+nie\s+o\s+to\b|"
    r"^\s*nie[,.]?\s+o\s+to\s+(?:mi\s+)?chodzi|"
    r"^\s*to\s+nie\s+to\b|"
    r"^\s*nie\s+o\s+to\b|"
    r"^\s*nie[.!?…]*\s*$|"
    r"^\s*chodzi(?:ło|lo)\s+mi\s+o\b",
    re.I,
)
# Explicit preference / memory mutation intent only.
PREFERENCE_PATTERNS = re.compile(
    r"(?:"
    r"\bzapami[eę]taj\b.*\b(?:wol[eę]|preferuj|u[zż]ywaj)\b.*\b(?:spotify|youtube|muzyk|aplikac|domy[sś]ln)\b|"
    r"\bzapami[eę]taj\b.*\b(?:spotify|youtube(?:\s+music)?)\b|"
    r"\b(?:wol[eę]|preferuj[eę]?)\b.*\b(?:spotify|youtube|aplikac)\b|"
    r"\bdo\s+muzyki\s+(?:u[zż]ywaj|uzywaj)\b|"
    r"\bustaw\b.*\bdomy[sś]ln\w*\b.*\b(?:aplikac|spotify|muzyk)|"
    r"\bdomy[sś]ln\w*\b.*\b(?:aplikac|spotify)\b.*\bmuzyk|"
    r"\bu[zż]ywaj\s+spotify\s+do\s+muzyki\b"
    r")",
    re.I,
)

CLARIFICATION_ANSWER = re.compile(
    r"^(?:"
    r"tak|nie|spotify|discord|chrome|youtube(?:\s+music)?|"
    r"jutro|dzi[sś]|dzisiaj|"
    r"(?:o\s+)?(?:siedemnastej|17|18|19|20)|"
    r"\d{1,3}\s*(?:%|procent)?"
    r")\.?$",
    re.I,
)


@dataclass
class IntentClassification:
    family: IntentFamily = IntentFamily.UNKNOWN
    is_complete: bool = False
    slots: Dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""


def _find_app_in_region(lowered: str) -> str:
    for key, canonical in sorted(KNOWN_APPS.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{re.escape(key)}\w*\b", lowered):
            return canonical
    return ""


def _find_app_name(text: str) -> str:
    """Resolve app target; honor exclusion after bare ``nie`` (open X nie Y → X)."""
    lowered = str(text or "").lower()
    parts = re.split(r"\bnie\b", lowered, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        # "otwórz Spotify nie YouTube Music" → prefer the app before exclusion.
        primary = _find_app_in_region(parts[0])
        if primary:
            return primary
    return _find_app_in_region(lowered)


def _extract_reminder_slots(raw: str, lowered: str) -> Dict[str, Any]:
    slots: Dict[str, Any] = {"reminder_text": raw}
    match = REMINDER_TIME_HINT.search(lowered)
    if not match:
        return slots

    slots["when"] = match.group(0)
    after = raw[match.end() :].strip(" ,.;")
    before = raw[: match.start()].strip(" ,.;")
    subject = after or before
    subject = re.sub(
        r"^(?:o|że|ze|żeby(?:m)?|zeby(?:m)?|aby(?:m)?|abym)\s+",
        "",
        subject,
        flags=re.I,
    ).strip(" ,.;")
    subject = re.sub(
        r"^(?:przypomnij|pingnij)\s+(?:mi|mnie)?\s*",
        "",
        subject,
        flags=re.I,
    ).strip(" ,.;")
    if subject:
        slots["reminder_text"] = subject
    return slots


def _extract_freeform_app_target(text: str) -> str:
    """App name after open/close verbs when not in KNOWN_APPS.

    Enables generalization: 'odpal figma' is a complete APP goal without a whitelist hit.
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    match = re.search(
        r"\b(?:odpali[cć]|odpal|otw[oó]rz|otworz|uruchom|launch|open)\s+"
        r"(?:mi\s+|prosz[eę]\s+|no\s+)?"
        r"(?!co[sś]\b)(.+?)\s*$",
        raw,
        re.I,
    )
    if not match:
        return ""
    chunk = str(match.group(1) or "").strip(" .,!?#")
    chunk = re.sub(r"\b(?:prosz[eę]|please|no|yyy|ej)\b", "", chunk, flags=re.I)
    chunk = re.sub(r"\s+#\d+\s*$", "", chunk).strip(" .,!?")
    chunk = re.sub(r"\s+", " ", chunk).strip()
    if not chunk or len(chunk.split()) > 4:
        return ""
    # Search-result / link referents — not app names ("otwórz sensowny wynik").
    if re.search(
        r"\b(?:wynik\w*|link\w*|stron[eęay]|tre[sś][cć]|artyku[lł]\w*)\b",
        chunk,
        re.I,
    ):
        return ""
    if re.search(
        r"\b(?:pierwsz\w*|drug\w*|trzec\w*|sensown\w*|dobr\w*|najlepsz\w*|g[oó]rn\w*|doln\w*)\b",
        chunk,
        re.I,
    ) and len(chunk.split()) <= 2:
        return ""
    # HA / media cues — not freeform apps.
    if re.search(
        r"\b(?:lamp|[sś]wiat[lł]|klimatyz|muzyk|piosenk|playlist|album)\w*\b",
        chunk,
        re.I,
    ):
        return ""
    if re.search(r"\bco[sś]\b", chunk, re.I):
        return ""
    # Vague / incomplete app referents — still need clarification, not freeform open.
    if re.match(
        r"^(?:gr[eę]|gre|aplikacj\w*|program\w*|to|ten|t[eę]|tamto|jaki[sś]?)$",
        chunk,
        re.I,
    ):
        return ""
    # OS Settings is a vision/UI flow, not a freeform desktop app name.
    if re.match(r"^(?:ustawienia|settings|parametry)$", chunk, re.I):
        return ""
    # Title-case single tokens; keep mixed-case brands (e.g. VS Code) as-is.
    if chunk.islower() or chunk.isupper():
        return " ".join(part.capitalize() for part in chunk.split())
    return chunk


def _title_case_artist(text: str) -> str:
    cleaned = re.sub(r"[''`]", "", str(text or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return ""
    cleaned = re.sub(r"\bmichaela\b", "michael", cleaned, flags=re.I)
    cleaned = re.sub(r"\bmichael\b", "michael", cleaned, flags=re.I)
    cleaned = re.sub(r"\bjacksona\b", "jackson", cleaned, flags=re.I)
    cleaned = re.sub(r"\bjaxona\b", "jackson", cleaned, flags=re.I)
    return " ".join(word.capitalize() for word in cleaned.split())


def _extract_media_query(text: str) -> str:
    raw = str(text or "").strip()
    lowered = raw.lower()

    patterns = (
        r"(?:a\s+)?pu[sś]cisz\s+(.+?)\??$",
        r"(?:a\s+)?pu[sś]ci[lł]by[sś]\s+(?:co[sś]\s+)?(.+?)\??$",
        r"(?:mo[zż]esz|mozesz)\s+pu[sś]ci[cć]\s+(?:co[sś]\s+)?(.+?)\??$",
        r"(?:mo[zż]esz|mozesz)\s+pu[sś][cć]\s+(?:co[sś]\s+)?(.+?)\??$",
        r"chc[eę]\s+pos[lł]ucha[cć]\s+(.+?)\??$",
        r"pos[lł]ucha[cć]\s+(.+?)\??$",
        r"mo[zż]e\s+(?:co[sś]\s+)?(.+?)\??$",
        r"(?:pu[sś]ci[cć]|pu[sś][cć]|odtw[oó]rz|graj|zagraj)\s+(?:co[sś]\s+)?(.+)$",
        r"(?:co[sś]|something)\s+(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, lowered, re.I)
        if match:
            chunk = (match.group(1) or "").strip(" .,!?")
            chunk = re.sub(r"\b(?:prosz[eę]|please)\b", "", chunk, flags=re.I).strip(" .,!?")
            if chunk:
                return _title_case_artist(chunk)
    return ""


def _is_compound_intent(text: str) -> bool:
    parts = COMPOUND_CONNECTOR.split(str(text or "").strip())
    if len(parts) < 2:
        return False
    return any(SECOND_ACTION.search(part) for part in parts[1:])


def _is_generic_media_query(query: str) -> bool:
    return bool(GENERIC_MEDIA_QUERY.match(str(query or "").strip()))


def _media_slots(raw: str, media_query: str, app_name: str) -> Dict[str, Any]:
    slots: Dict[str, Any] = {"media_query": media_query, "app_name": app_name}
    try:
        from core.semantic_media import parse_media_goal

        goal = parse_media_goal(raw)
        if goal is not None:
            semantic = dict(goal.to_slots())
            if media_query:
                semantic["media_query"] = media_query
                if not semantic.get("query"):
                    semantic["query"] = media_query
            slots.update(semantic)
    except Exception:
        pass
    return slots


def _extract_knowledge_topic(text: str) -> str:
    raw = str(text or "").strip()
    match = re.search(
        r"(?:co\s+oznacza|czym\s+jest|co\s+to\s+jest|wyjaśnij|wyjasnij)\s+(.+?)\??$",
        raw,
        re.I,
    )
    if match:
        return match.group(1).strip(" .,!?")
    return raw


def _finalize_classification(
    classification: IntentClassification,
    raw: str,
) -> IntentClassification:
    if classification.is_complete and _is_compound_intent(raw):
        try:
            from core.compound_plan import parse_compound_goal

            plan = parse_compound_goal(raw)
            if plan.detected and plan.complete:
                classification.slots = dict(classification.slots or {})
                classification.slots["compound"] = True
                classification.slots["compound_steps"] = [step.to_dict() for step in plan.steps]
                classification.is_complete = True
                return classification
        except Exception:
            pass
        classification.is_complete = False
    return classification


def _implicit_lighting(lowered: str) -> Dict[str, Any] | None:
    want_bright = looks_want_brighter(lowered)
    insufficient = looks_insufficient_light(lowered)
    if not want_bright and not insufficient:
        return None
    # Imperative dim verbs → darker. Darkness / hard-to-see / want light → brighter.
    if re.search(r"\b(?:przyciemn|ściemn|sciemn)\w*\b", lowered) and not want_bright:
        desired = "darker"
    elif re.search(r"\bciemniej\b", lowered) and not want_bright and not re.search(
        r"\b(?:jest|za|tu|troch)\w*", lowered
    ):
        desired = "darker"
    else:
        desired = "brighter"
    slots: Dict[str, Any] = {
        "implicit_light": True,
        "desired": desired,
        "domain": "light",
    }
    try:
        from core.clarification_slots import extract_room_hint

        room = extract_room_hint(lowered)
        if room:
            slots["room"] = room
            slots["room_hint"] = room
    except Exception:
        pass
    return slots


def classify_intent_family(
    text: str,
    *,
    active_domain: str = "",
    allow_compound: bool = True,
) -> IntentClassification:
    raw = str(text or "").strip()
    lowered = raw.lower().strip()
    if not lowered:
        return IntentClassification(family=IntentFamily.UNKNOWN, raw_text=raw)

    if CORRECTION_PATTERNS.search(lowered):
        return IntentClassification(
            family=IntentFamily.CORRECTION,
            is_complete=True,
            slots={"rejection": True, "correction": True},
            raw_text=raw,
        )

    # Phase 11.1 — polarity-aware ACK ("Nie rozumiem" is NOT acknowledgement).
    if NEGATED_COMPREHENSION.search(lowered):
        return IntentClassification(
            family=IntentFamily.KNOWLEDGE,
            is_complete=False,
            slots={"comprehension_repair": True, "topic": ""},
            raw_text=raw,
        )

    if ACKNOWLEDGEMENT_ONLY.match(lowered):
        return IntentClassification(
            family=IntentFamily.CONVERSATION_CONTROL,
            is_complete=True,
            slots={"acknowledgement": True},
            raw_text=raw,
        )

    ack_prefix = ACKNOWLEDGEMENT_PREFIX.match(raw)
    if ack_prefix and not NEGATED_COMPREHENSION.search(lowered):
        remainder = raw[ack_prefix.end() :].strip(" .,!?")
        if remainder:
            return classify_intent_family(remainder)

    if CONTROL_PATTERNS.match(lowered):
        return IntentClassification(
            family=IntentFamily.CONVERSATION_CONTROL,
            is_complete=True,
            raw_text=raw,
        )

    if OPINION_ONLY.match(lowered):
        return IntentClassification(
            family=IntentFamily.CONVERSATION_CONTROL,
            is_complete=True,
            slots={"opinion_only": True},
            raw_text=raw,
        )

    if PREFERENCE_PATTERNS.search(lowered):
        app = _find_app_name(lowered)
        if not app:
            # Never invent Spotify when user named a non-music preference.
            return IntentClassification(
                family=IntentFamily.PREFERENCE,
                is_complete=False,
                slots={"explicit_preference": True, "missing": "preference_value"},
                raw_text=raw,
            )
        return IntentClassification(
            family=IntentFamily.PREFERENCE,
            is_complete=True,
            slots={
                "preference_key": "music.default_app",
                "preference_value": app,
                "explicit_preference": True,
            },
            raw_text=raw,
        )

    if VISION_DESCRIBE.search(lowered):
        return IntentClassification(
            family=IntentFamily.VISION,
            is_complete=True,
            raw_text=raw,
        )

    if VISION_ACTION.search(lowered):
        return IntentClassification(
            family=IntentFamily.VISION,
            is_complete=False,
            raw_text=raw,
        )

    if REMINDER_PATTERNS.search(lowered):
        slots = _extract_reminder_slots(raw, lowered)
        complete = bool(slots.get("when"))
        return IntentClassification(
            family=IntentFamily.REMINDER,
            is_complete=complete,
            slots=slots,
            raw_text=raw,
        )

    if CALENDAR_PATTERNS.search(lowered):
        return IntentClassification(
            family=IntentFamily.CALENDAR,
            is_complete=len(lowered.split()) >= 3,
            raw_text=raw,
        )

    # Phase 13 B — multi-domain compound before a single-family local/HA lock.
    if allow_compound:
        try:
            from core.compound_plan import parse_compound_goal

            plan = parse_compound_goal(raw)
            if plan.detected and plan.steps:
                first = plan.steps[0]
                slots = {
                    "compound": True,
                    "compound_steps": [step.to_dict() for step in plan.steps],
                    "families": [step.family for step in plan.steps],
                }
                if first.family == IntentFamily.APP.value:
                    slots["app_name"] = first.slots.get("app_name") or first.slots.get("target")
                    slots["semantic_action"] = first.semantic_action
                    slots["resolution_policy"] = first.slots.get("resolution_policy")
                    slots["website_direct"] = bool(first.slots.get("website_direct"))
                    slots["explicit_app"] = bool(first.slots.get("explicit_app"))
                try:
                    family = IntentFamily(first.family)
                except ValueError:
                    family = IntentFamily.UNKNOWN
                return IntentClassification(
                    family=family,
                    is_complete=plan.complete,
                    slots=slots,
                    raw_text=raw,
                )
        except Exception:
            pass

    # Phase 12.3.2 — explicit local device state / capability beats WEB + freshness.
    try:
        from core.local_device_status import (
            INTENT_KNOWLEDGE,
            INTENT_LOCAL_DEVICE_CAPABILITY,
            INTENT_LOCAL_DEVICE_STATUS,
            classify_local_request,
        )

        local = classify_local_request(raw, active_domain=active_domain)
        if local.intent == INTENT_LOCAL_DEVICE_STATUS:
            return IntentClassification(
                family=IntentFamily.SMART_HOME,
                is_complete=True,
                slots={
                    "domain": local.domain or "climate",
                    "semantic_action": local.semantic_action,
                    "source_kind": local.kind,
                    "web_allowed": False,
                    "read_only": True,
                },
                raw_text=raw,
            )
        if local.intent == INTENT_LOCAL_DEVICE_CAPABILITY:
            return IntentClassification(
                family=IntentFamily.SMART_HOME,
                is_complete=True,
                slots={
                    "domain": "climate",
                    "semantic_action": local.semantic_action,
                    "source_kind": local.kind,
                    "web_allowed": False,
                    "read_only": True,
                },
                raw_text=raw,
            )
        if local.intent == INTENT_KNOWLEDGE:
            return IntentClassification(
                family=IntentFamily.KNOWLEDGE,
                is_complete=True,
                slots={
                    "topic": raw,
                    "source_kind": local.kind,
                    "web_allowed": False,
                },
                raw_text=raw,
            )
    except Exception:
        pass

    try:
        from core.read_intents import looks_undo, undo_count

        if looks_undo(raw):
            return IntentClassification(
                family=IntentFamily.SYSTEM,
                is_complete=True,
                slots={"semantic_action": "undo", "undo_count": undo_count(raw)},
                raw_text=raw,
            )
    except Exception:
        pass

    try:
        from core.computer_semantics import parse_computer_goal

        computer = parse_computer_goal(raw)
        if computer is not None and computer.action != "inspect_ui":
            return IntentClassification(
                family=IntentFamily.COMPUTER,
                is_complete=bool(computer.complete),
                slots=computer.to_slots(),
                raw_text=raw,
            )
    except Exception:
        pass

    if WEB_PATTERNS.search(lowered):
        return IntentClassification(
            family=IntentFamily.WEB,
            is_complete=True,
            raw_text=raw,
        )

    # Phase 11.2 — freshness-sensitive questions/claims win over UNKNOWN
    # even without the word "teraz".
    try:
        from core.freshness import analyze_freshness

        fresh = analyze_freshness(raw)
        if fresh.freshness_required:
            slots = {
                "freshness_required": True,
                "semantic_kind": fresh.semantic_kind,
                "search_query": fresh.search_query,
                "claim": fresh.claim,
            }
            if fresh.is_factual_claim:
                slots["factual_claim"] = True
            return IntentClassification(
                family=IntentFamily.WEB,
                is_complete=True,
                slots=slots,
                raw_text=raw,
            )
    except Exception:
        pass

    lighting = _implicit_lighting(lowered)
    if lighting and not APP_VERBS.search(lowered):
        return IntentClassification(
            family=IntentFamily.SMART_HOME,
            is_complete=False,
            slots=lighting,
            raw_text=raw,
        )

    try:
        from core.climate_control import looks_climate_louver, wants_climate_eco

        climate_named = bool(
            re.search(r"\b(?:klima|klimy|klimę|klime|klimatyz\w*|gree|pular)\b", lowered)
        )
        climate_ctx = climate_named or str(active_domain or "").lower() == "climate"
        if wants_climate_eco(lowered, climate_context=climate_ctx):
            return IntentClassification(
                family=IntentFamily.SMART_HOME,
                is_complete=True,
                slots={
                    "domain": "climate",
                    "semantic_action": "climate_eco",
                    "preset": "eco",
                },
                raw_text=raw,
            )
        if looks_climate_louver(lowered) and (
            climate_ctx or re.search(r"\b(?:swing|nawiew|żaluz|zaluz)\b", lowered)
        ):
            return IntentClassification(
                family=IntentFamily.SMART_HOME,
                is_complete=True,
                slots={"domain": "climate", "semantic_action": "climate_swing"},
                raw_text=raw,
            )
    except Exception:
        pass

    # Phase 13 A hardware — wyłącz Spotify is APP close, not SMART_HOME.
    try:
        from core.target_resolve import (
            RESOLVE_APP,
            RESOLVE_CLARIFY,
            RESOLVE_HA,
            resolve_power_off_target,
        )

        resolution, app_target = resolve_power_off_target(raw)
        if resolution == RESOLVE_CLARIFY:
            return IntentClassification(
                family=IntentFamily.UNKNOWN,
                is_complete=False,
                slots={
                    "needs_target_disambiguation": True,
                    "app_name": app_target,
                    "semantic_action": "close_service",
                },
                raw_text=raw,
            )
        if resolution == RESOLVE_APP and app_target:
            return _finalize_classification(
                IntentClassification(
                    family=IntentFamily.APP,
                    is_complete=True,
                    slots={
                        "app_name": app_target,
                        "semantic_action": "close_service",
                    },
                    raw_text=raw,
                ),
                raw,
            )
        if resolution == RESOLVE_HA:
            named = bool(
                re.search(
                    r"\b(?:lamp|świat[łl]|swiat[lł]|klimatyz|klima|salon|kuchni)\w*\b",
                    lowered,
                )
            )
            return IntentClassification(
                family=IntentFamily.SMART_HOME,
                is_complete=named or len(lowered.split()) >= 2,
                raw_text=raw,
            )
    except Exception:
        pass

    try:
        from core.target_resolve import looks_ha_device_target as _ha_noun
    except Exception:
        def _ha_noun(_t: str) -> bool:
            return False

    # Dual-use verbs (włącz) must not demote named HA targets to APP.
    if HA_EXPLICIT.search(lowered) and (not APP_VERBS.search(lowered) or _ha_noun(lowered)):
        if re.fullmatch(r"(?:lamp[aęey]|[sś]wiat[łl]o|vizzini)[.!?]*", lowered.strip()):
            return IntentClassification(
                family=IntentFamily.SMART_HOME,
                is_complete=False,
                slots={"implicit_light": True, "target_hint": raw},
                raw_text=raw,
            )
        named = bool(
            re.search(r"\b(?:lamp|świat[łl]|swiat[lł]|klimatyz|klima|salon|kuchni)\w*\b", lowered)
        )
        pronoun = bool(
            re.search(r"\b(?:ją|ja|nią|nia|go|niego|je|to|ten|tę|te)\b", lowered)
        )
        complete = named or (len(lowered.split()) >= 2 and not pronoun)
        return IntentClassification(
            family=IntentFamily.SMART_HOME,
            is_complete=complete,
            raw_text=raw,
        )

    if SYSTEM_PATTERNS.search(lowered):
        from core.tool_compat import volume_direction_from_text

        direction = volume_direction_from_text(raw)
        slots: Dict[str, Any] = {}
        if direction:
            slots["volume_direction"] = direction
            slots["semantic_action"] = "adjust_volume"
        return IntentClassification(
            family=IntentFamily.SYSTEM,
            is_complete=True,
            slots=slots,
            raw_text=raw,
        )

    app_name = _find_app_name(lowered)
    media_query = _extract_media_query(raw) if MEDIA_SIGNAL.search(lowered) else ""

    if MEDIA_SIGNAL.search(lowered) and media_query:
        complete = not _is_generic_media_query(media_query)
        return IntentClassification(
            family=IntentFamily.MEDIA,
            is_complete=complete,
            slots=_media_slots(raw, media_query, app_name),
            raw_text=raw,
        )

    # "Może coś Michaela Jacksona?" without explicit play verb but artist+media cue
    if re.search(r"\bmo[zż]e\s+co[sś]\b", lowered) and re.search(
        r"\b(?:michael|michaela|jackson|jacksona|jaxona)\b", lowered
    ):
        query = _extract_media_query(raw) or _title_case_artist(
            re.sub(r"^.*?co[sś]\s+", "", lowered, flags=re.I).strip(" .,!?")
        )
        if query:
            return IntentClassification(
                family=IntentFamily.MEDIA,
                is_complete=True,
                slots={"media_query": query, "app_name": app_name},
                raw_text=raw,
            )

    if app_name and APP_CLOSE_VERBS.search(lowered):
        return _finalize_classification(
            IntentClassification(
                family=IntentFamily.APP,
                is_complete=True,
                slots={
                    "app_name": app_name,
                    "semantic_action": "close_service",
                },
                raw_text=raw,
            ),
            raw,
        )

    if APP_CLOSE_VERBS.search(lowered) and not APP_VERBS.search(lowered):
        return IntentClassification(
            family=IntentFamily.APP,
            is_complete=False,
            slots={"semantic_action": "close_service", "app_name": app_name} if app_name else {"semantic_action": "close_service"},
            raw_text=raw,
        )

    if app_name and (
        APP_VERBS.search(lowered)
        or re.search(r"\bmożesz\b|\bmozesz\b", lowered)
        or re.search(r"\bwejd[zź]\s+na\b", lowered)
    ):
        # "możesz puścić" is MEDIA, already handled; "możesz odpalić spotify" is APP
        if MEDIA_SIGNAL.search(lowered) and media_query:
            pass
        else:
            from core.service_open import looks_explicit_website, looks_explicit_app_wording, resolution_policy_for

            return _finalize_classification(
                IntentClassification(
                    family=IntentFamily.APP,
                    is_complete=True,
                    slots={
                        "app_name": app_name,
                        "website_direct": looks_explicit_website(raw),
                        "explicit_app": looks_explicit_app_wording(raw),
                        "resolution_policy": resolution_policy_for(raw),
                    },
                    raw_text=raw,
                ),
                raw,
            )

    if app_name and re.search(r"\b(?:aplikac|program)\b", lowered):
        return _finalize_classification(
            IntentClassification(
                family=IntentFamily.APP,
                is_complete=True,
                slots={"app_name": app_name},
                raw_text=raw,
            ),
            raw,
        )

    if APP_VERBS.search(lowered):
        try:
            from core.device_pronoun import has_device_pronoun, detect_device_action_hint

            _hint = detect_device_action_hint(lowered)
            _pronoun_power = has_device_pronoun(lowered) and _hint in (
                "turn_on",
                "turn_off",
                "brightness_decrease",
                "brightness_increase",
            )
        except Exception:
            _pronoun_power = False
            _hint = ""
        if _pronoun_power and str(active_domain or "").upper() in ("SMART_HOME", "LIGHT"):
            return IntentClassification(
                family=IntentFamily.SMART_HOME,
                is_complete=True,
                slots={"semantic_action": _hint or "turn_on"},
                raw_text=raw,
            )
        # Bare / truncated open verb (ASR "włącz…") without app or HA target —
        # incomplete, not a false APP domain claim.
        core = re.sub(r"[\s.…]+$", "", lowered).strip()
        core = re.sub(r"^(?:y+|e+|a+|o+|u+|eh+|ah+|mm+|uhm+|hmm+|yyy+|eee+)\s+", "", core)
        resolved_app = app_name or _extract_freeform_app_target(raw)
        freeform_only = bool(resolved_app) and not bool(app_name)
        if not resolved_app:
            try:
                from core.target_resolve import looks_ha_device_target as _ha_tgt
            except Exception:
                def _ha_tgt(_t: str) -> bool:
                    return False
            if not _ha_tgt(lowered) and len(core.split()) <= 1:
                return IntentClassification(
                    family=IntentFamily.UNKNOWN,
                    is_complete=False,
                    slots={"needs_target": True, "open_verb_bare": True},
                    raw_text=raw,
                )
            return IntentClassification(
                family=IntentFamily.APP,
                is_complete=False,
                slots={},
                raw_text=raw,
            )
        # Freeform or known app after open verb → complete APP (no whitelist required).
        if not MEDIA_SIGNAL.search(lowered):
            try:
                from core.service_open import (
                    looks_explicit_website,
                    looks_explicit_app_wording,
                    resolution_policy_for,
                )
            except Exception:
                looks_explicit_website = lambda _t: False  # noqa: E731
                looks_explicit_app_wording = lambda _t: True  # noqa: E731
                resolution_policy_for = lambda _t: "app_first"  # noqa: E731
            return _finalize_classification(
                IntentClassification(
                    family=IntentFamily.APP,
                    is_complete=True,
                    slots={
                        "app_name": resolved_app,
                        "website_direct": looks_explicit_website(raw),
                        "explicit_app": looks_explicit_app_wording(raw),
                        "resolution_policy": resolution_policy_for(raw),
                        "semantic_action": "open_app",
                        "freeform_app": freeform_only,
                    },
                    raw_text=raw,
                ),
                raw,
            )
        return IntentClassification(
            family=IntentFamily.APP,
            is_complete=False,
            slots={"app_name": resolved_app},
            raw_text=raw,
        )

    if KNOWLEDGE_PATTERNS.search(lowered):
        topic = _extract_knowledge_topic(raw)
        return IntentClassification(
            family=IntentFamily.KNOWLEDGE,
            is_complete=len(topic) >= 2,
            slots={"topic": topic},
            raw_text=raw,
        )

    return IntentClassification(
        family=IntentFamily.UNKNOWN,
        is_complete=False,
        raw_text=raw,
    )


def is_complete_intent(text: str) -> bool:
    return classify_intent_family(text).is_complete


def is_clarification_answer(text: str, *, pending_question: str = "") -> bool:
    from core.clarification_slots import looks_like_light_slot_answer

    normalized = str(text or "").lower().strip()
    if not normalized:
        return False
    if classify_intent_family(text).family == IntentFamily.CORRECTION:
        return False
    if is_complete_intent(text):
        return False
    if CLARIFICATION_ANSWER.match(normalized):
        return True
    if len(normalized.split()) <= 2 and _find_app_name(normalized):
        if "aplikac" in (pending_question or "").lower():
            return True
    pending_l = (pending_question or "").lower()
    if looks_like_light_slot_answer(text) and (
        "świat" in pending_l
        or "swiat" in pending_l
        or "lamp" in pending_l
        or "rozjaś" in pending_l
        or "rozjas" in pending_l
    ):
        return True
    return False


def family_label(text: str) -> str:
    return classify_intent_family(text).family.value


def has_explicit_preference_intent(text: str = "", classification: IntentClassification | None = None) -> bool:
    info = classification or classify_intent_family(text)
    if info.family == IntentFamily.PREFERENCE:
        return True
    return bool((info.slots or {}).get("explicit_preference"))
