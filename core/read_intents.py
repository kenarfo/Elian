# ==========================
# ELIAN — read-only voice intents (Wave A/B)
# ==========================
"""Detectors for tools that already exist but were never routed."""

from __future__ import annotations

import re

_HOME_ARRIVAL = re.compile(
    r"^(?:"
    r"(?:ju[zż]\s+)?jestem\s+w\s+domu|"
    r"w[lł]a[sś]nie\s+wr[oó]ci[lł]em(?:\s+do\s+domu)?|"
    r"wr[oó]ci[lł]em\s+do\s+domu|"
    r"i(?:['’]m|\s+am)\s+home|"
    r"i(?:['’]m|\s+am)\s+back"
    r")[.!?…]*$",
    re.I,
)
_HOME_OVERVIEW = re.compile(
    r"^(?:"
    r"co\s+w\s+domu|"
    r"co\s+(?:s[lł]ycha[cć]|slychac)\s+w\s+domu|"
    r"jak\s+tam\s+w\s+domu|"
    r"co\s+jest\s+w[lł][aą]czone|"
    r"jaki\s+(?:jest\s+)?stan\s+domu|"
    r"co\s+si[eę]\s+dzieje\s+w\s+domu|"
    r"podsumuj\s+dom"
    r")[.!?…]*$",
    re.I,
)
_SCENE = re.compile(
    r"\b(?:w[lł][aą]cz|odpal|aktywuj)\s+scen[eęa]|"
    r"\bscen[aeę]\s+\w+",
    re.I,
)
_LIST_REMINDERS = re.compile(
    r"^(?:"
    r"jakie\s+mam\s+przypomn|"
    r"poka[zż]\s+przypomn|"
    r"lista\s+przypomn|"
    r"co\s+mam\s+do\s+przypomn"
    r")",
    re.I,
)
_TODAY_SCHEDULE = re.compile(
    r"^(?:"
    r"co\s+mam\s+dzi[sś]|"
    r"plan\s+na\s+dzi[sś]|"
    r"jaki\s+mam\s+plan|"
    r"co\s+w\s+kalendarzu|"
    r"harmonogram\s+(?:na\s+)?dzi[sś]"
    r")",
    re.I,
)
_WEATHER = re.compile(
    r"(?:"
    # Polish — allow fillers between question word and "pogoda"
    r"\bjaka\s+(?:jest\s+|b[eę]dzie\s+)?(?:\w+\s+){0,3}pogoda\b|"
    r"\bjaka\s+pogoda\b|"
    r"\bsprawd[zź]\s+(?:mi\s+)?(?:\w+\s+){0,2}pogod[eęa]\b|"
    r"\bpogod[eęa]\s+(?:dla|w|na)\b|"
    r"\bpogoda\s+(?:na\s+)?(?:dzi[sś]|jutro|dzisiaj)\b|"
    r"\bile\s+(?:jest\s+)?stopni\b|"
    r"\bjaka\s+temperatura\s+na\s+zewn|"
    # English
    r"\bwhat(?:['’]?s|\s+is)\s+the\s+weather\b|"
    r"\bhow(?:['’]?s|\s+is)\s+the\s+weather\b|"
    r"\bcheck\s+(?:the\s+)?weather\b|"
    r"\bcan\s+you\s+check\s+(?:the\s+)?weather\b|"
    r"\bweather\s+(?:today|tomorrow|for|in)\b|"
    r"\b(?:today|tomorrow)(?:['’]?s)?\s+weather\b"
    r")",
    re.I,
)
_LIST_NOTIFICATIONS = re.compile(
    r"^(?:"
    r"jakie\s+mam\s+powiadom|"
    r"poka[zż]\s+powiadom|"
    r"odczytaj\s+powiadom|"
    r"co\s+mi\s+przysz[lł]o|"
    r"czy\s+kto[sś]\s+pisa[lł]"
    r")",
    re.I,
)
_PHONE_STATUS = re.compile(
    r"^(?:"
    r"czy\s+masz\s+numer|"
    r"jaki\s+masz\s+numer|"
    r"jaki\s+jest\s+tw[oó]j\s+numer|"
    r"masz\s+numer\s+telefon"
    r")",
    re.I,
)
_MISSED = re.compile(
    r"^(?:"
    r"co\s+mnie\s+omin[eę][lł]o|"
    r"co\s+przegapi[lł]em|"
    r"co\s+si[eę]\s+dzia[lł]o\s+w\s+mi[eę]dzyczasie"
    r")[.!?…]*$",
    re.I,
)
_SIMULATE = re.compile(
    r"\b(?:symuluj|na\s+sucho|nie\s+r[oó]b\s+tego\s+naprawd[eę]|"
    r"tylko\s+poka[zż]\s+co\s+by[sś]\s+zrobi[lł])\b",
    re.I,
)
_SCENE_NAME = re.compile(
    r"(?:scen[eęa]\s+|scena\s+)([a-ząćęłńóśźż\w-]+)",
    re.I,
)
_UNDO_WORDS = {
    "jeden": 1,
    "jedną": 1,
    "jedna": 1,
    "dwa": 2,
    "dwie": 2,
    "trzy": 3,
    "cztery": 4,
    "pięć": 5,
    "piec": 5,
    "sześć": 6,
    "szesc": 6,
    "siedem": 7,
    "osiem": 8,
    "dziewięć": 9,
    "dziewiec": 9,
    "dziesięć": 10,
    "dziesiec": 10,
}
_UNDO = re.compile(
    r"^(?:"
    r"cofnij(?:\s+to)?|"
    r"undo|"
    r"cofnij\s+ostatni[ąa]\s+(?:rzecz|akcj[ęe]|czynno[sś][cć]|ruch)|"
    r"cofnij\s+ostatnie(?:\s+(?P<digits>\d+)|"
    r"\s+(?P<word>jeden|jedn[aą]|dwa|dwie|trzy|cztery|pi[eę][cć]|sze[sś][cć]|"
    r"siedem|osiem|dziewi[eę][cć]|dziesi[eę][cć]))?"
    r"\s+(?:rzeczy|akcje|ruchy|czynno[sś]ci)|"
    r"przywr[oó][cć]\s+jak\s+by[łl]o(?:\s+wcze[sś]niej)?|"
    r"przywr[oó][cć]\s+poprzedni\s+stan"
    r")[.!?…]*$",
    re.I,
)


def looks_home_overview(text: str) -> bool:
    return bool(_HOME_OVERVIEW.match(str(text or "").strip()))


def looks_home_arrival(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw or looks_home_overview(raw):
        return False
    return bool(_HOME_ARRIVAL.match(raw))


def looks_activate_scene(text: str) -> bool:
    return bool(_SCENE.search(str(text or "")))


def looks_list_reminders(text: str) -> bool:
    return bool(_LIST_REMINDERS.search(str(text or "").strip()))


def looks_today_schedule(text: str) -> bool:
    raw = str(text or "").strip()
    if re.match(r"^jaki\s+mam\s+dzie[nń]\b", raw, re.I):
        return False
    return bool(_TODAY_SCHEDULE.search(raw))


def looks_weather(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    # Web-search for weather is NOT the get_weather FastPath.
    if re.search(
        r"\b(?:internet|wyszuk|poszukaj|google|szukaj\s+w)\b",
        raw,
        re.I,
    ):
        return False
    return bool(_WEATHER.search(raw))


def looks_missed_digest(text: str) -> bool:
    return bool(_MISSED.match(str(text or "").strip()))


def looks_list_notifications(text: str) -> bool:
    raw = str(text or "").strip()
    if re.match(r"^(?:w[lł][aą]cz|wy[lł][aą]cz)\s+powiadom", raw, re.I):
        return False
    return bool(_LIST_NOTIFICATIONS.search(raw))


def looks_phone_status(text: str) -> bool:
    return bool(_PHONE_STATUS.search(str(text or "").strip()))


def looks_simulate(text: str) -> bool:
    return bool(_SIMULATE.search(str(text or "")))


def looks_undo(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if re.search(r"\bnie\s+cofnij\b", raw, re.I):
        return False
    return bool(_UNDO.match(raw))


def undo_count(text: str) -> int:
    raw = str(text or "").strip()
    match = _UNDO.match(raw)
    if not match:
        return 1
    digits = match.groupdict().get("digits") or ""
    word = match.groupdict().get("word") or ""
    if digits:
        try:
            return max(1, int(digits))
        except (TypeError, ValueError):
            return 1
    if word:
        key = word.casefold()
        return max(1, int(_UNDO_WORDS.get(key) or 1))
    return 1


def extract_scene_name(text: str) -> str:
    match = _SCENE_NAME.search(str(text or ""))
    if match:
        return str(match.group(1) or "").strip()
    return ""
