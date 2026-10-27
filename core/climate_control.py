# ==========================
# ELIAN — climate natural language + capability-safe plans (Phase 12.3)
# ==========================
"""Intent from language; execution only if HA exposes the capability."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from core.climate_profile import (
    ClimateCapabilityProfile,
    profile_for_entity_id,
)
from core.device_context import ActiveDevice, LastDeviceOperation


PHYS_NOT_HA = (
    "Ta klimatyzacja obsługuje tę funkcję, ale Home Assistant "
    "nie udostępnia mi obecnie sterowania nią."
)
NOT_IN_HA = "Nie widzę tej funkcji w Home Assistant."
PERCENT_CLIMATE = "Co w klimatyzacji chcesz ustawić na 50 procent?"
DEGREES_ON_LIGHT = "To światło — stopni nie ustawiam jako jasności."
HOT_CLARIFY = "Chcesz, żebym obniżył temperaturę?"
COLD_CLARIFY = "Chcesz, żebym podniósł temperaturę?"

_CLIMATE_WORDS = re.compile(
    r"\b(?:klima|klimy|klimę|klime|klimatyz\w*|gree|pular|"
    r"chłodz\w*|chlodz\w*|grza[nc]\w*|osusz\w*|nawiew\w*|"
    r"wentylator\w*|swing|żaluz\w*|zaluz\w*|stopn\w*|°c|celsj)\b",
    re.I,
)
_PERCENT = re.compile(r"\d+\s*(?:%|procent)\b", re.I)
_DEGREES = re.compile(r"(?:stopn\w*|°\s*c|\bcelsj\w*)\b", re.I)
_NUMBER = re.compile(r"(-?\d{1,2})(?:[.,](\d))?")
_PRONOUN = re.compile(r"\b(?:ją|ja|nią|nia|go|je|to|tę|te)\b", re.I)

_COOL = re.compile(
    r"\b(?:chłodzen\w*|chlodzen\w*|chłodzi\w*|chlodzi\w*|niech\s+chłodzi)\b",
    re.I,
)
_HEAT = re.compile(r"\b(?:grzan\w*|grzej\w*|niech\s+grzeje)\b", re.I)
_DRY = re.compile(r"\b(?:osusz\w*|osuszanie)\b", re.I)
_FAN_ONLY = re.compile(
    r"\b(?:sam\s+nawiew|sam\s+wentylator|tylko\s+dmuchaj|tryb\s+wentylatora|"
    r"nie\s+chłodź,\s+tylko)\b",
    re.I,
)
_AUTO = re.compile(
    r"\b(?:tryb\s+automat|ustaw\s+auto|włącz\s+auto|sama\s+dobierze)\b",
    re.I,
)
_OFF = re.compile(r"\b(?:wy[lł][aą]cz|zga[sś])\b", re.I)
_ON = re.compile(r"\b(?:w[lł][aą]cz|zapal)\b", re.I)

_REL_COOL = re.compile(
    r"\b(?:chłodniej|chlodniej|zimniej|zmniejsz\s+temperatur|"
    r"obniż|obniz|zniż)\b",
    re.I,
)
_REL_WARM = re.compile(
    r"\b(?:cieplej|podnie[sś]\s+temperatur|podnie[sś]\s+o|"
    r"zwiększ\s+temperatur|zwieksz\s+temperatur)\b",
    re.I,
)
_DELTA_N = re.compile(
    r"\bo\s+(\d{1,2})\s*(?:stopn\w*)?",
    re.I,
)

_FAN_UP = re.compile(
    r"\b(?:zwiększ\s+nawiew|zwieksz\s+nawiew|mocniejsz\w*\s+nawiew|"
    r"daj\s+mocniej|mocniej(?!\s+świat))\b",
    re.I,
)
_FAN_DOWN = re.compile(
    r"\b(?:zmniejsz\s+nawiew|słabsz\w*\s+nawiew|slab\w*\s+nawiew|"
    r"daj\s+słabiej|daj\s+slabiej|lżejsz\w*\s+nawiew|"
    r"niech\s+nie\s+dmucha\s+tak\s+mocno|ciszej)\b",
    re.I,
)
_FAN_ABS = re.compile(
    r"\b(?:nawiew|wentylator)\b",
    re.I,
)

_SWING_V_ON = re.compile(
    r"\b(?:pionow\w*\s+swing|ruch\s+góra-dół|gora-dol|żaluzja\s+rusz|"
    r"niech\s+żaluzja\s+się\s+rusz|włącz\s+swing|wlacz\s+swing)\b",
    re.I,
)
_SWING_V_OFF = re.compile(
    r"\b(?:wyłącz\s+swing|wylacz\s+swing|zatrzymaj\s+ruch\s+góra|"
    r"zatrzymaj\s+swing)\b",
    re.I,
)
_SWING_H_ON = re.compile(
    r"\b(?:poziom\w*\s+swing|lewo-prawo|lewo\s+prawo)\b",
    re.I,
)
_SWING_RANGE = re.compile(
    r"\bniech\s+rusza\s+się\b|\brusz(?:a|ać)\s+się\b",
    re.I,
)
_V_POS = re.compile(
    r"\b(?:do\s+gór[yeę]|na\s+gór[eę]|najwyżej|najwyzej|"
    r"górny\s+środek|gorny\s+srodek|"
    r"na\s+środek|na\s+srodek|"
    r"dolny\s+środek|dolny\s+srodek|"
    r"w\s+dół|w\s+dol|na\s+dół|na\s+dol|najniżej|najnizej|"
    r"skieruj\s+nawiew|nawiew\s+do\s+gór|nawiew\s+w\s+dół|"
    r"trochę\s+niżej|troche\s+nizej|trochę\s+wyżej|troche\s+wyzej)\b",
    re.I,
)
_QUIET = re.compile(r"(?:tryb\s+cich\w*|cichy\s+nawiew|\bquiet\b)", re.I)
_TURBO = re.compile(
    r"\b(?:turbo|boost|schłodź\s+szybko|schlodz\s+szybko|maksymalną\s+moc|maksymalna\s+moc)\b",
    re.I,
)
_SLEEP = re.compile(r"(?:tryb\s+nocn\w*|\bsleep\b|do\s+spania|\bsenny\b)", re.I)
_ECO = re.compile(
    r"\b(?:\beco\b|\beko\b|ekonomicz\w*|oszczędzaj\s+energi\w*|"
    r"oszczedzaj\s+energi\w*|oszczędzanie\s+energi\w*|oszczedzanie\s+energi\w*)\b",
    re.I,
)
_ECHO = re.compile(r"\becho\b", re.I)
_HEAT8 = re.compile(
    r"(?:8\s*stopn\w*|osiem\s+stopn\w*|zamarzan\w*|frost|przeciw\s+zamarz\w*)",
    re.I,
)
_DISPLAY = re.compile(
    r"(?:wyświetlacz|wyswietlacz|ekran\s+na\s+klim|diody\s+na\s+klim|"
    r"panel)",
    re.I,
)
_BEEPER = re.compile(r"(?:pikan\w*|dźwięk\s+klim|dzwiek\s+klim|nie\s+pika)", re.I)
_HEALTH = re.compile(r"\b(?:health|jonizac\w*|oczyszczan\w*)\b", re.I)
_XFAN = re.compile(r"(?:x-fan|\bxfan\b|suszenie\s+wymiennika|osuszanie\s+parownika)", re.I)
_CLEAN = re.compile(r"(?:samooczyszcz\w*|czyszczenie\s+klimatyz\w*)", re.I)
_FRESH = re.compile(
    r"(?:śwież[eę]\s+powietrz\w*|swiez\w*\s+powietrz\w*|\bfresh\s+air\b)",
    re.I,
)
_ANTI = re.compile(
    r"\b(?:nie\s+dmuchaj\s+bezpośred|anti\s+direct|ochron[ęe]\s+przed\s+bezpośred)\b",
    re.I,
)
_STATUS = re.compile(
    r"\b(?:co\s+teraz\s+robi|czy\s+klima\s+jest|na\s+ile\s+jest|"
    r"jaka\s+jest\s+ustawiona|jaka\s+jest\s+temperatur|"
    r"jaki\s+jest\s+tryb|jaki\s+jest\s+nawiew|czy\s+teraz\s+chłodzi|"
    r"czy\s+działa\s+turbo|czy\s+jest\s+tryb\s+nocn|"
    r"jak\s+ustawione\s+są\s+żaluz|czy\s+swing|"
    r"temperatur[ae]\s+na\s+zewnątrz|temperatur[ae]\s+w\s+pokoju)\b",
    re.I,
)
_COMFORT_HOT = re.compile(r"\b(?:jest\s+mi\s+gorąc|gorąco\s+mi|za\s+gorąc)\b", re.I)
_COMFORT_COLD = re.compile(r"\b(?:jest\s+mi\s+zimno|zimno\s+mi|za\s+zimno)\b", re.I)

FAN_ORDER = ("low", "medium-low", "medium", "medium-high", "high")
FAN_SPECIAL = {"auto", "quiet", "turbo", "boost"}

FAN_ALIASES = {
    "auto": ("auto", "automat"),
    "low": ("low", "niski", "słaby", "slaby", "minimum"),
    "medium-low": ("medium-low", "medium_low", "średnio-niski", "srednio-niski"),
    "medium": ("medium", "średni", "sredni"),
    "medium-high": ("medium-high", "medium_high", "średnio-wysoki"),
    "high": ("high", "wysoki", "mocny", "maksimum", "max", "maks"),
    "quiet": ("quiet", "cichy"),
    "turbo": ("turbo", "boost"),
}

SWING_V_ALIASES = {
    "off": ("off", "default", "wyłącz", "wylacz", "stop"),
    "auto": ("full_swing", "full swing", "auto", "swing"),
    "upper": ("fixed_upper", "upper", "up", "góra", "gora", "góry", "gory", "wyżej", "wyzej"),
    "middle": ("fixed_middle", "center", "środek", "srodek", "middle"),
    "lower": ("fixed_lower", "lower", "down", "dół", "dol", "niżej", "nizej"),
}
SWING_H_ALIASES = {
    "off": ("off", "default", "wyłącz", "wylacz", "stop"),
    "auto": ("full_swing", "full swing", "auto"),
    "left": ("left", "lewo", "lewej"),
    "center": ("center", "middle", "środek", "srodek"),
    "right": ("right", "prawo", "prawej"),
}

V_FIXED_ORDER = (
    "fixed_upper",
    "fixed_upper_middle",
    "fixed_middle",
    "fixed_lower_middle",
    "fixed_lower",
)


@dataclass
class ClimateStep:
    semantic_action: str
    tool: str
    arguments: Dict[str, Any]
    ha_action: str = ""


@dataclass
class ClimatePlan:
    steps: List[ClimateStep] = field(default_factory=list)
    clarification: str = ""
    status_text: str = ""
    unsupported_message: str = ""
    fake_blocked: bool = False
    entity_id: str = ""
    domain: str = "climate"
    semantic_action: str = ""
    resolved_capability: str = ""
    axis: str = ""
    target_mode: str = ""

    @property
    def handled(self) -> bool:
        return bool(
            self.steps
            or self.clarification
            or self.status_text
            or self.unsupported_message
        )


def looks_climate_utterance(text: str) -> bool:
    raw = str(text or "")
    if _CLIMATE_WORDS.search(raw):
        return True
    if _REL_COOL.search(raw) or _REL_WARM.search(raw):
        return True
    if _COOL.search(raw) or _HEAT.search(raw) or _DRY.search(raw):
        return True
    if _FAN_ABS.search(raw) or _FAN_UP.search(raw) or _FAN_DOWN.search(raw):
        return True
    if _SWING_V_ON.search(raw) or _SWING_H_ON.search(raw):
        return True
    if _QUIET.search(raw):
        return True
    if _BEEPER.search(raw) or _DISPLAY.search(raw) or _HEAT8.search(raw):
        return True
    if _TURBO.search(raw) or _SLEEP.search(raw) or _ECO.search(raw):
        return True
    if wants_climate_eco(raw, climate_context=False):
        return True
    if _V_POS.search(raw) or _SWING_RANGE.search(raw):
        return True
    try:
        from core.local_device_status import (
            is_climate_capability_question,
            looks_climate_status,
        )

        if looks_climate_status(raw, climate_context=False) or is_climate_capability_question(raw):
            return True
    except Exception:
        pass
    if _STATUS.search(raw) or _COMFORT_HOT.search(raw) or _COMFORT_COLD.search(raw):
        return True
    return False


def wants_climate_eco(text: str, *, climate_context: bool = False) -> bool:
    """eco/eko always; echo only with climate context or explicit climate words."""
    raw = str(text or "")
    if _ECO.search(raw):
        return True
    if _ECHO.search(raw) and (climate_context or _CLIMATE_WORDS.search(raw)):
        return True
    return False


def looks_climate_louver(text: str) -> bool:
    raw = str(text or "")
    return bool(
        _SWING_V_ON.search(raw)
        or _SWING_H_ON.search(raw)
        or _V_POS.search(raw)
        or _SWING_RANGE.search(raw)
        or re.search(r"\b(?:w\s+lewo|na\s+lewo|w\s+prawo|na\s+prawo|poziom)", raw, re.I)
    )


def _norm_mode(text: str) -> str:
    return str(text or "").lower().replace("_", " ").replace("-", " ").strip()


def _match_mode(requested: str, available: Sequence[str]) -> Optional[str]:
    want = _norm_mode(requested)
    if not want:
        return None
    exact = [item for item in available if _norm_mode(item) == want]
    if exact:
        return str(exact[0])
    token_hits = [
        item
        for item in available
        if want in _norm_mode(item).split()
    ]
    if token_hits:
        return str(min(token_hits, key=lambda item: len(_norm_mode(item))))
    contains = [
        item
        for item in available
        if want in _norm_mode(item) or _norm_mode(item) in want
    ]
    if contains:
        return str(min(contains, key=lambda item: len(_norm_mode(item))))
    return None


def match_fan_mode(text: str, fan_modes: Sequence[str]) -> Optional[str]:
    lowered = str(text or "").lower()
    available = [str(item) for item in fan_modes]
    by_lower = {item.lower(): item for item in available}
    for canonical, aliases in FAN_ALIASES.items():
        if any(alias in lowered for alias in aliases):
            if canonical in by_lower:
                return by_lower[canonical]
            found = _match_mode(canonical, available)
            if found:
                return found
    return None


def ordered_fan_modes(fan_modes: Sequence[str]) -> List[str]:
    available = [str(item) for item in fan_modes]
    by_norm = {_norm_mode(item): item for item in available}
    ordered = []
    for name in FAN_ORDER:
        key = _norm_mode(name)
        if key in by_norm and by_norm[key] not in ordered:
            ordered.append(by_norm[key])
    return ordered


def step_fan_mode(current: str, fan_modes: Sequence[str], delta: int) -> Optional[str]:
    ordered = ordered_fan_modes(fan_modes)
    if len(ordered) < 2:
        return None
    cur = _norm_mode(current)
    special = {_norm_mode(item) for item in FAN_SPECIAL}
    ordered_n = [_norm_mode(item) for item in ordered]
    if cur in special or cur not in set(ordered_n):
        index = len(ordered) // 2
    else:
        index = ordered_n.index(cur)
    nxt = index + (1 if delta > 0 else -1)
    if nxt < 0 or nxt >= len(ordered):
        return ordered[index]
    return ordered[nxt]


def match_swing(text: str, modes: Sequence[str], aliases: Dict[str, tuple]) -> Optional[str]:
    lowered = str(text or "").lower()
    available = [str(item) for item in modes]
    by_norm = {_norm_mode(item): item for item in available}
    for canonical, words in aliases.items():
        if not any(word in lowered for word in words):
            continue
        if _norm_mode(canonical) in by_norm:
            return by_norm[_norm_mode(canonical)]
        for word in words:
            key = _norm_mode(word)
            if key in by_norm:
                return by_norm[key]
        found = _match_mode(canonical, available)
        if found:
            return found
    return None


def _pick_mode(available: Sequence[str], *candidates: str) -> Optional[str]:
    for name in candidates:
        found = _match_mode(name, available)
        if found:
            return found
    return None


def resolve_vertical_swing(
    text: str,
    modes: Sequence[str],
    *,
    current: str = "",
) -> tuple[Optional[str], str]:
    """Return (ha_mode, kind) where kind is move/stop/fixed/range."""
    raw = str(text or "")
    available = [str(item) for item in modes]
    if _SWING_V_OFF.search(raw) or (
        _OFF.search(raw)
        and ("swing" in raw.lower() or "góra-dół" in raw.lower() or "gora-dol" in raw.lower())
    ):
        return _pick_mode(available, "default", "off"), "stop"
    if _SWING_RANGE.search(raw):
        if re.search(r"\b(?:u\s+gór|gór[yea]|wyżej|wyzej)\b", raw, re.I) and "środ" not in raw.lower():
            return _pick_mode(available, "swing_upper", "swing_upper_middle"), "range"
        if re.search(r"\b(?:środ|srod)\b", raw, re.I):
            return _pick_mode(available, "swing_middle"), "range"
        if re.search(r"\b(?:do[lł]|niżej|nizej)\b", raw, re.I):
            return _pick_mode(available, "swing_lower", "swing_lower_middle"), "range"
    if _SWING_V_ON.search(raw) and not _V_POS.search(raw):
        return _pick_mode(available, "full_swing", "auto"), "move"
    lowered = raw.lower()
    if re.search(
        r"\b(?:najwyżej|najwyzej|do\s+gór|na\s+gór|skieruj\s+nawiew\s+do\s+gór)\b",
        lowered,
    ):
        return _pick_mode(available, "fixed_upper", "upper"), "fixed"
    if re.search(r"\bgórny\s+środek\b|\bgorny\s+srodek\b", lowered):
        return _pick_mode(available, "fixed_upper_middle"), "fixed"
    if re.search(r"\bdolny\s+środek\b|\bdolny\s+srodek\b", lowered):
        return _pick_mode(available, "fixed_lower_middle"), "fixed"
    if re.search(
        r"\b(?:najniżej|najnizej|nawiew\s+w\s+dół|nawiew\s+w\s+dol|na\s+dół|na\s+dol)\b",
        lowered,
    ):
        return _pick_mode(available, "fixed_lower", "lower"), "fixed"
    if re.search(r"\bna\s+środek\b|\bna\s+srodek\b", lowered):
        return _pick_mode(available, "fixed_middle", "middle", "center"), "fixed"
    if re.search(r"\btrochę\s+niżej\b|\btroche\s+nizej\b", lowered):
        nxt = _step_fixed_vertical(current, available, 1)
        if nxt:
            return nxt, "fixed"
        return _pick_mode(available, "fixed_upper_middle", "fixed_middle"), "fixed"
    if re.search(r"\btrochę\s+wyżej\b|\btroche\s+wyzej\b", lowered):
        nxt = _step_fixed_vertical(current, available, -1)
        if nxt:
            return nxt, "fixed"
        return _pick_mode(available, "fixed_upper", "upper"), "fixed"
    if _V_POS.search(raw):
        return _pick_mode(available, "fixed_upper", "upper"), "fixed"
    return None, ""


def _step_fixed_vertical(current: str, available: Sequence[str], delta: int) -> Optional[str]:
    ordered = []
    for name in V_FIXED_ORDER:
        found = _match_mode(name, available)
        if found and found not in ordered:
            ordered.append(found)
    if len(ordered) < 2:
        return None
    cur_n = _norm_mode(current)
    ordered_n = [_norm_mode(item) for item in ordered]
    if cur_n in ordered_n:
        index = ordered_n.index(cur_n)
    else:
        index = 0 if delta > 0 else len(ordered) - 1
    nxt = index + delta
    if nxt < 0 or nxt >= len(ordered):
        return ordered[index]
    return ordered[nxt]


def resolve_horizontal_swing(text: str, modes: Sequence[str]) -> tuple[Optional[str], str]:
    raw = str(text or "")
    available = [str(item) for item in modes]
    lowered = raw.lower()
    if _OFF.search(raw) or "zatrzymaj" in lowered:
        return _pick_mode(available, "default", "off"), "stop"
    if re.search(r"\btrochę\s+w\s+lewo\b|\btroche\s+w\s+lewo\b", lowered):
        return _pick_mode(available, "left_center", "left"), "fixed"
    if re.search(r"\btrochę\s+w\s+prawo\b|\btroche\s+w\s+prawo\b", lowered):
        return _pick_mode(available, "right_center", "right"), "fixed"
    if re.search(r"\bna\s+środek\b|\bna\s+srodek\b", lowered):
        return _pick_mode(available, "center", "middle"), "fixed"
    if re.search(r"\b(?:w\s+lewo|na\s+lewo|lewej)\b", lowered) and "prawo" not in lowered:
        return _pick_mode(available, "left"), "fixed"
    if re.search(r"\b(?:w\s+prawo|na\s+prawo|prawej)\b", lowered):
        return _pick_mode(available, "right"), "fixed"
    if _SWING_H_ON.search(raw) or "swing" in lowered:
        return _pick_mode(available, "full_swing", "auto") or (
            available[1] if len(available) > 1 else None
        ), "move"
    return match_swing(raw, available, SWING_H_ALIASES), "fixed"


def parse_temperature_celsius(text: str) -> Optional[float]:
    raw = str(text or "")
    if _PERCENT.search(raw):
        return None
    match = re.search(
        r"(?:na|do|ustaw|daj|teraz)?\s*(-?\d{1,2})(?:[.,](\d))?\s*(?:stopn\w*|°\s*c)?",
        raw,
        re.I,
    )
    if not match:
        return None
    if not _DEGREES.search(raw) and not re.search(
        r"\b(?:na|do|teraz|ustaw|daj)\b", raw, re.I
    ):
        return None
    whole = int(match.group(1))
    frac = match.group(2)
    value = float(whole) + (int(frac) / 10.0 if frac else 0.0)
    if value < 5 or value > 40:
        return None
    return value


def parse_delta_celsius(text: str) -> Optional[float]:
    raw = str(text or "")
    if not (_REL_COOL.search(raw) or _REL_WARM.search(raw)):
        return None
    amount = 0.0
    numbered = _DELTA_N.search(raw)
    if numbered:
        amount = float(numbered.group(1))
    sign = -1.0 if _REL_COOL.search(raw) else 1.0
    if amount <= 0:
        amount = 0.0  # caller fills step
    return sign * amount


def unsupported_message(profile: Optional[ClimateCapabilityProfile], physical_key: str) -> str:
    if profile is not None and profile.physical(physical_key):
        return PHYS_NOT_HA
    return NOT_IN_HA


def _base_args(entity_id: str, semantic: str, **extra: Any) -> Dict[str, Any]:
    args = {
        "target": entity_id,
        "entity_id": entity_id,
        "domain": "climate",
        "semantic_action": semantic,
    }
    args.update(extra)
    return args


def _temp_step(profile: ClimateCapabilityProfile) -> float:
    return profile.spoken_step_c()


def _format_spoken_limit(value: float) -> str:
    rounded = round(float(value), 1)
    if abs(rounded - round(rounded)) < 0.05:
        return str(int(round(rounded)))
    return f"{rounded:.1f}".replace(".", ",")


def validate_temperature(
    profile: ClimateCapabilityProfile,
    requested: float,
) -> tuple[Optional[float], Optional[str]]:
    minimum = profile.spoken_min_c()
    maximum = profile.spoken_max_c()
    if maximum is not None and requested > float(maximum) + 0.05:
        return None, (
            f"Ta klimatyzacja pozwala ustawić maksymalnie "
            f"{_format_spoken_limit(maximum)} stopni."
        )
    if minimum is not None and requested < float(minimum) - 0.05:
        return None, (
            f"Ta klimatyzacja pozwala ustawić minimalnie "
            f"{_format_spoken_limit(minimum)} stopni."
        )
    step = _temp_step(profile)
    snapped = round(requested / step) * step
    if abs(snapped - requested) <= max(0.15, step * 0.15):
        return snapped, None
    return requested, None


HVAC_SPOKEN = {
    "cool": "chłodzenia",
    "heat": "grzania",
    "dry": "osuszania",
    "fan_only": "samego nawiewu",
    "auto": "automatycznym",
    "heat_cool": "automatycznym",
    "off": "off",
}

HVAC_VERB = {
    "cool": "chłodzi",
    "heat": "grzeje",
    "dry": "osusza",
    "fan_only": "działa w trybie samego nawiewu",
    "auto": "działa automatycznie",
    "heat_cool": "działa automatycznie",
    "off": "jest wyłączona",
}

FAN_SPOKEN = {
    "low": "niski",
    "medium-low": "średnio niski",
    "medium_low": "średnio niski",
    "medium low": "średnio niski",
    "medium": "średni",
    "medium-high": "średnio wysoki",
    "medium_high": "średnio wysoki",
    "medium high": "średnio wysoki",
    "high": "wysoki",
    "auto": "automatyczny",
    "quiet": "cichy",
}

PRESET_SPOKEN = {
    "eco": "eco",
    "boost": "turbo",
    "turbo": "turbo",
    "sleep": "nocny",
    "away": "ochrona przed zamarzaniem",
}


def _spoken_hvac(mode: str) -> str:
    key = str(mode or "").lower().replace(" ", "_")
    return HVAC_SPOKEN.get(key, key)


def _spoken_fan(mode: str) -> str:
    key = str(mode or "").lower().replace("_", "-")
    return FAN_SPOKEN.get(key, FAN_SPOKEN.get(key.replace("-", " "), key))


def _spoken_preset(mode: str) -> str:
    key = str(mode or "").lower()
    return PRESET_SPOKEN.get(key, key)


def _switch_on(profile: ClimateCapabilityProfile, kind: str) -> Optional[bool]:
    related = profile.related_by_kind(kind)
    if related is None:
        return None
    return related.state in ("on", "true", "1")


def climate_capability_text(profile: ClimateCapabilityProfile) -> str:
    modes = []
    for item in profile.hvac_modes:
        key = str(item or "").lower()
        if key in ("off", ""):
            continue
        spoken = _spoken_hvac(key)
        if spoken == "automatycznym":
            spoken = "automatyczny"
        elif spoken == "chłodzenia":
            spoken = "chłodzenie"
        elif spoken == "grzania":
            spoken = "grzanie"
        elif spoken == "osuszania":
            spoken = "osuszanie"
        elif spoken == "samego nawiewu":
            spoken = "sam nawiew"
        modes.append(spoken)
    name = profile.friendly_name or "klimatyzacja"
    if not modes:
        return f"Nie widzę listy trybów {name} w Home Assistant."
    if len(modes) == 1:
        return f"{name} ma tryb {modes[0]}."
    return f"{name} ma tryby: {', '.join(modes[:-1])} i {modes[-1]}."


def climate_status_text(
    profile: ClimateCapabilityProfile,
    kind: str = "",
    semantic_action: str = "",
) -> str:
    name = profile.friendly_name or "klimatyzacja"
    action = str(semantic_action or "").strip()
    lowered = str(kind or "").lower()
    if not action:
        try:
            from core.local_device_status import climate_status_semantic_action

            action = climate_status_semantic_action(kind or lowered)
        except Exception:
            action = ""

    if action == "climate_status_capabilities" or "jakie tryby ma" in lowered:
        return climate_capability_text(profile)

    if action == "climate_status_outdoor" or "zewnątrz" in lowered or "zewnatrz" in lowered:
        sensor = profile.related_by_kind("outdoor_temp")
        if sensor is None or not sensor.state or sensor.state in ("unknown", "unavailable"):
            return "Nie mam tej informacji z Home Assistanta."
        return f"Na zewnątrz jest {sensor.state} stopni."

    if action == "climate_status_current_temperature" or "pokoju" in lowered or "w pomieszczeniu" in lowered:
        current_c = profile.spoken_current_c()
        if current_c is None:
            return "Nie mam tej informacji z Home Assistanta."
        return f"W pokoju jest {_format_spoken_limit(current_c)} stopni."

    if action == "climate_status_fan" or "nawiew" in lowered or "wentylator" in lowered:
        if not profile.fan_mode:
            return "Nie mam tej informacji z Home Assistanta."
        spoken = _spoken_fan(profile.fan_mode)
        return f"Nawiew jest ustawiony na {spoken}."

    if action == "climate_status_hvac":
        mode = str(profile.hvac_mode or profile.state or "").lower()
        if not mode:
            return "Nie mam tej informacji z Home Assistanta."
        if mode in ("off", "idle"):
            return f"{name} jest wyłączona."
        if mode == "auto":
            return f"Jest teraz w trybie automatycznym."
        if mode == "cool":
            return f"{name} jest teraz w trybie chłodzenia."
        if mode == "heat":
            return "Jest teraz w trybie grzania."
        if mode == "dry":
            return "Jest teraz w trybie osuszania."
        if mode == "fan_only":
            return "Jest teraz w trybie samego nawiewu."
        return f"{name} jest teraz w trybie {_spoken_hvac(mode)}."

    if action == "climate_status_preset" or "turbo" in lowered or "eco" in lowered or "eko" in lowered or "nocn" in lowered or "sleep" in lowered:
        if "turbo" in lowered or "boost" in lowered:
            related_on = _switch_on(profile, "turbo")
            if related_on is not None:
                return "Turbo jest włączone." if related_on else "Turbo jest wyłączone."
            if "turbo" in (profile.preset_mode or "").lower() or "boost" in (profile.preset_mode or "").lower():
                return "Turbo jest aktywne."
            return "Turbo jest wyłączone."
        if "nocn" in lowered or "sleep" in lowered:
            related_on = _switch_on(profile, "sleep")
            if related_on is not None:
                return "Tryb nocny jest włączony." if related_on else "Tryb nocny jest wyłączony."
            if "sleep" in (profile.preset_mode or "").lower():
                return "Tryb nocny jest włączony."
            return "Tryb nocny jest wyłączony."
        related_on = _switch_on(profile, "eco")
        if related_on is not None:
            return "Eco jest włączone." if related_on else "Eco jest wyłączone."
        if "eco" in (profile.preset_mode or "").lower():
            return "Eco jest włączone."
        return "Eco jest wyłączone."

    if action == "climate_status_quiet" or "cich" in lowered or "quiet" in lowered:
        related_on = _switch_on(profile, "quiet")
        if related_on is not None:
            return "Tryb cichy jest włączony." if related_on else "Tryb cichy jest wyłączony."
        if "quiet" in (profile.fan_mode or "").lower() or "cich" in (profile.fan_mode or "").lower():
            return "Tryb cichy jest włączony."
        return "Tryb cichy jest wyłączony."

    if action == "climate_status_display" or "wyświetl" in lowered or "wyswietl" in lowered:
        related_on = _switch_on(profile, "display")
        if related_on is None:
            return "Nie mam tej informacji z Home Assistanta."
        return "Wyświetlacz jest włączony." if related_on else "Wyświetlacz jest wyłączony."

    if action == "climate_status_xfan" or "xfan" in lowered or "x-fan" in lowered:
        related_on = _switch_on(profile, "xfan")
        if related_on is None:
            return "Nie mam tej informacji z Home Assistanta."
        return "X-Fan jest włączony." if related_on else "X-Fan jest wyłączony."

    if action in ("climate_status_vertical_swing", "climate_status_horizontal_swing") or "swing" in lowered or "żaluz" in lowered or "zaluz" in lowered:
        if "poziom" in lowered or action == "climate_status_horizontal_swing":
            mode = str(profile.swing_horizontal_mode or "").lower()
            on = mode not in ("", "off", "default", "none")
            return "Poziomy swing jest włączony." if on else "Poziomy swing jest wyłączony."
        mode = str(profile.swing_mode or "").lower()
        on = mode not in ("", "off", "default", "none")
        if "żaluz" in lowered or "zaluz" in lowered:
            parts = []
            if profile.swing_mode:
                parts.append(f"pionowy {profile.swing_mode}")
            if profile.swing_horizontal_mode:
                parts.append(f"poziomy {profile.swing_horizontal_mode}")
            if parts:
                return "Żaluzje: " + ", ".join(parts) + "."
        return "Swing jest włączony." if on else "Swing jest wyłączony."

    if action == "climate_status_target_temperature" or "ile" in lowered or "ustawiona" in lowered or "docelow" in lowered:
        target_c = profile.spoken_target_c()
        if target_c is None:
            return "Nie mam tej informacji z Home Assistanta."
        return f"Ustawiona temperatura to {_format_spoken_limit(target_c)} stopni."

    if action == "climate_status_power":
        mode = str(profile.hvac_mode or profile.state or "").lower()
        if mode in ("off", "idle", ""):
            return f"{name} jest wyłączona."
        return f"{name} działa, jest w trybie {_spoken_hvac(mode)}."

    if action == "climate_status_summary" or "co teraz robi" in lowered:
        mode = str(profile.hvac_mode or profile.state or "").lower()
        verb = HVAC_VERB.get(mode, f"jest w trybie {_spoken_hvac(mode)}")
        if mode in ("off", "idle"):
            return f"{name} jest wyłączona."
        bits = [f"{name} {verb}"]
        target_c = profile.spoken_target_c()
        if target_c is not None:
            bits.append(f"ma ustawione {_format_spoken_limit(target_c)} stopnie")
        if profile.fan_mode:
            fan = _spoken_fan(profile.fan_mode)
            bits.append(f"a nawiew jest na {fan}m poziomie" if fan == "średni" else f"a nawiew jest {fan}")
        if len(bits) == 1:
            return bits[0] + "."
        if len(bits) == 2:
            return f"{bits[0]}, {bits[1]}."
        return f"{bits[0]}, {bits[1]}, {bits[2]}."

    if "tryb" in lowered or "chłodzi" in lowered or "grzeje" in lowered:
        return climate_status_text(profile, kind, "climate_status_hvac")

    on = (profile.state or "").lower() not in ("off", "idle", "")
    target_c = profile.spoken_target_c()
    target = (
        f" Ustawiona na {_format_spoken_limit(target_c)} stopni."
        if target_c is not None
        else ""
    )
    mode = profile.hvac_mode or profile.state or "nieznany"
    if on:
        spoken = _spoken_hvac(mode)
        return f"{name} jest włączona, tryb {spoken}.{target}"
    return f"{name} jest wyłączona.{target}"


def _climate_is_off(profile: ClimateCapabilityProfile) -> bool:
    return (profile.state or "").lower() in ("off", "idle", "")


def _preset_plan(
    profile: ClimateCapabilityProfile,
    semantic: str,
    preset: str,
    kind: str,
    *,
    power_on: bool = True,
) -> ClimatePlan:
    steps: List[ClimateStep] = []
    if power_on and _climate_is_off(profile):
        steps.append(
            ClimateStep(
                semantic_action=semantic,
                tool="turn_on_device",
                ha_action="device_on",
                arguments=_base_args(profile.entity_id, semantic),
            )
        )
    steps.append(
        ClimateStep(
            semantic_action=semantic,
            tool="set_device_value",
            ha_action="set_preset_mode",
            arguments=_base_args(
                profile.entity_id,
                semantic,
                action="set_preset_mode",
                extra=preset,
                value=preset,
            ),
        )
    )
    return ClimatePlan(
        steps=steps,
        entity_id=profile.entity_id,
        semantic_action=semantic,
        resolved_capability=f"preset_{kind}",
        target_mode=preset,
    )


def _related_step(
    profile: ClimateCapabilityProfile,
    kind: str,
    turn_on: bool,
    semantic: str,
    physical_key: str,
) -> ClimatePlan:
    related = profile.related_by_kind(kind)
    if related is None:
        # preset / fan fallback for quiet/turbo/sleep/eco
        if kind in ("quiet", "turbo", "sleep", "eco", "8c_heat"):
            fan_modes = [item.lower() for item in profile.fan_modes]
            tokens = {
                "quiet": ("quiet",),
                "turbo": ("turbo", "boost"),
                "sleep": ("sleep",),
                "eco": ("eco",),
                "8c_heat": ("8", "frost", "away"),
            }[kind]
            if kind == "quiet" and "quiet" in fan_modes:
                mode = _match_mode("quiet", profile.fan_modes)
                return ClimatePlan(
                    steps=[
                        ClimateStep(
                            semantic_action=semantic,
                            tool="set_device_value",
                            ha_action="set_fan_mode",
                            arguments=_base_args(
                                profile.entity_id,
                                semantic,
                                action="set_fan_mode",
                                extra=mode,
                                value=mode,
                            ),
                        )
                    ],
                    entity_id=profile.entity_id,
                    semantic_action=semantic,
                    resolved_capability="fan_quiet",
                )
            matched_preset = next(
                (
                    item
                    for item in profile.preset_modes
                    if any(token in item.lower() for token in tokens)
                ),
                None,
            )
            if matched_preset and turn_on:
                return _preset_plan(profile, semantic, matched_preset, kind)
            if matched_preset and not turn_on:
                none_mode = next(
                    (item for item in profile.preset_modes if item.lower() == "none"),
                    None,
                )
                if none_mode:
                    return _preset_plan(profile, semantic, none_mode, kind, power_on=False)
        return ClimatePlan(
            unsupported_message=unsupported_message(profile, physical_key),
            entity_id=profile.entity_id,
            semantic_action=semantic,
            resolved_capability=kind,
        )
    if related.domain == "button":
        if not turn_on:
            return ClimatePlan(
                unsupported_message="Tego przycisku nie wyłącza się osobno.",
                entity_id=profile.entity_id,
                semantic_action=semantic,
            )
        return ClimatePlan(
            steps=[
                ClimateStep(
                    semantic_action=semantic,
                    tool="set_device_value",
                    ha_action="press_button",
                    arguments={
                        "target": related.entity_id,
                        "entity_id": related.entity_id,
                        "domain": related.domain,
                        "action": "press_button",
                        "semantic_action": semantic,
                    },
                )
            ],
            entity_id=profile.entity_id,
            semantic_action=semantic,
            resolved_capability=kind,
        )
    tool = "turn_on_device" if turn_on else "turn_off_device"
    return ClimatePlan(
        steps=[
            ClimateStep(
                semantic_action=semantic,
                tool=tool,
                ha_action="device_on" if turn_on else "device_off",
                arguments={
                    "target": related.entity_id,
                    "entity_id": related.entity_id,
                    "domain": related.domain,
                    "semantic_action": semantic,
                },
            )
        ],
        entity_id=profile.entity_id,
        semantic_action=semantic,
        resolved_capability=kind,
    )


def _hvac_plan(profile: ClimateCapabilityProfile, mode: str, semantic: str) -> ClimatePlan:
    found = _match_mode(mode, profile.hvac_modes)
    if not found:
        return ClimatePlan(
            unsupported_message=(
                "Ta klimatyzacja nie udostępnia tego trybu przez Home Assistant."
            ),
            entity_id=profile.entity_id,
            semantic_action=semantic,
            resolved_capability="hvac_mode",
        )
    return ClimatePlan(
        steps=[
            ClimateStep(
                semantic_action=semantic,
                tool="set_device_value",
                ha_action="set_hvac_mode",
                arguments=_base_args(
                    profile.entity_id,
                    semantic,
                    action="set_hvac_mode",
                    extra=found,
                    value=found,
                ),
            )
        ],
        entity_id=profile.entity_id,
        semantic_action=semantic,
        resolved_capability=f"hvac_{found}",
    )


def parse_clause(
    text: str,
    profile: ClimateCapabilityProfile,
    *,
    last_op: Optional[LastDeviceOperation] = None,
) -> Optional[ClimatePlan]:
    raw = str(text or "").strip()
    if not raw:
        return None
    entity_id = profile.entity_id

    if _PERCENT.search(raw) and not _DEGREES.search(raw):
        return ClimatePlan(
            clarification=PERCENT_CLIMATE.replace("50", re.search(r"\d+", raw).group(0) if re.search(r"\d+", raw) else "50"),
            entity_id=entity_id,
            semantic_action="climate_percent_rejected",
            resolved_capability="percent_lock",
        )

    if _COMFORT_HOT.search(raw) and not _REL_COOL.search(raw) and "ustaw" not in raw.lower():
        return ClimatePlan(clarification=HOT_CLARIFY, entity_id=entity_id, semantic_action="comfort")
    if _COMFORT_COLD.search(raw) and not _REL_WARM.search(raw) and "ustaw" not in raw.lower():
        return ClimatePlan(clarification=COLD_CLARIFY, entity_id=entity_id, semantic_action="comfort")

    try:
        from core.local_device_status import (
            climate_status_semantic_action,
            is_climate_capability_question,
            is_climate_knowledge_question,
            looks_climate_status,
        )

        if is_climate_knowledge_question(raw):
            return None
        if is_climate_capability_question(raw):
            return ClimatePlan(
                status_text=climate_capability_text(profile),
                entity_id=entity_id,
                semantic_action="climate_status_capabilities",
                resolved_capability="hvac_modes",
            )
        if looks_climate_status(raw, climate_context=True) or _STATUS.search(raw):
            action = climate_status_semantic_action(raw)
            return ClimatePlan(
                status_text=climate_status_text(profile, raw, action),
                entity_id=entity_id,
                semantic_action=action,
                resolved_capability="status",
            )
    except Exception:
        if _STATUS.search(raw):
            return ClimatePlan(
                status_text=climate_status_text(profile, raw),
                entity_id=entity_id,
                semantic_action="climate_status",
                resolved_capability="status",
            )

    if _DISPLAY.search(raw):
        return _related_step(profile, "display", not _OFF.search(raw), "climate_display", "display_possible")
    if _BEEPER.search(raw):
        return _related_step(profile, "beeper", not _OFF.search(raw), "climate_beeper", "beeper_possible")
    if _HEALTH.search(raw):
        return _related_step(profile, "health", not _OFF.search(raw), "climate_health", "health_possible")
    if _XFAN.search(raw):
        return _related_step(profile, "xfan", not _OFF.search(raw), "climate_xfan", "xfan_possible")
    if _CLEAN.search(raw):
        return _related_step(profile, "self_clean", True, "climate_self_clean", "self_clean_possible")
    if _FRESH.search(raw):
        return _related_step(profile, "fresh_air", not _OFF.search(raw), "climate_fresh_air", "fresh_air_possible")
    if _ANTI.search(raw):
        return _related_step(profile, "anti_direct", True, "climate_anti_direct", "anti_direct_blow_possible")
    if _HEAT8.search(raw):
        return _related_step(profile, "8c_heat", not _OFF.search(raw), "climate_8c_heat", "8c_heat_possible")
    if _TURBO.search(raw):
        return _related_step(profile, "turbo", not _OFF.search(raw), "climate_turbo", "turbo_possible")
    if _SLEEP.search(raw):
        sleeps = [item for item in profile.preset_modes if "sleep" in item.lower()]
        if len(sleeps) > 1 and not re.search(r"sleep\s*[123]", raw, re.I):
            return ClimatePlan(
                clarification="Który tryb snu mam ustawić?",
                entity_id=entity_id,
                semantic_action="climate_sleep",
            )
        return _related_step(profile, "sleep", not _OFF.search(raw), "climate_sleep", "sleep_possible")
    if wants_climate_eco(raw, climate_context=True):
        return _related_step(profile, "eco", not _OFF.search(raw), "climate_eco", "eco_possible")
    if _QUIET.search(raw) and "nawiew" not in raw.lower() or (
        _QUIET.search(raw) and "cichy nawiew" in raw.lower()
    ):
        if "ciszej" in raw.lower() and _FAN_ABS.search(raw) is None and "tryb" not in raw.lower():
            # "Ciszej" alone with climate → fan down, not quiet unless explicit tryb cichy
            if "tryb cich" not in raw.lower() and "quiet" not in raw.lower() and "cichy nawiew" not in raw.lower():
                pass
            else:
                return _related_step(profile, "quiet", not _OFF.search(raw), "climate_quiet", "quiet_possible")
        elif "tryb cich" in raw.lower() or "quiet" in raw.lower() or "cichy nawiew" in raw.lower():
            return _related_step(profile, "quiet", not _OFF.search(raw), "climate_quiet", "quiet_possible")

    last_action = str((last_op.action if last_op else "") or "")
    horizontal_context = last_action in (
        "set_swing_horizontal_mode",
        "climate_horizontal_swing",
    )
    vertical_context = last_action in ("set_swing_mode", "climate_vertical_swing")
    midpoint = bool(re.search(r"\bna\s+środek\b|\bna\s+srodek\b", raw, re.I))

    if (
        _SWING_H_ON.search(raw)
        or re.search(r"\b(?:w\s+lewo|na\s+lewo|w\s+prawo|na\s+prawo|poziom)", raw, re.I)
        or (horizontal_context and midpoint)
    ):
        if not profile.swing_horizontal_modes:
            return ClimatePlan(
                unsupported_message=unsupported_message(profile, "horizontal_louver"),
                entity_id=entity_id,
                semantic_action="climate_horizontal_swing",
            )
        mode, kind = resolve_horizontal_swing(raw, profile.swing_horizontal_modes)
        if not mode:
            return ClimatePlan(
                clarification="Jak ustawić poziomy nawiew?",
                entity_id=entity_id,
                semantic_action="climate_horizontal_swing",
            )
        return ClimatePlan(
            steps=[
                ClimateStep(
                    semantic_action="climate_horizontal_swing",
                    tool="set_device_value",
                    ha_action="set_swing_horizontal_mode",
                    arguments=_base_args(
                        entity_id,
                        "climate_horizontal_swing",
                        action="set_swing_horizontal_mode",
                        extra=mode,
                        value=mode,
                    ),
                )
            ],
            entity_id=entity_id,
            semantic_action="climate_horizontal_swing",
            resolved_capability="horizontal_swing",
            axis="horizontal",
            target_mode=mode,
        )

    if (
        _SWING_V_ON.search(raw)
        or _SWING_V_OFF.search(raw)
        or _V_POS.search(raw)
        or _SWING_RANGE.search(raw)
        or (vertical_context and midpoint)
        or (midpoint and not horizontal_context)
    ):
        if not profile.swing_modes:
            return ClimatePlan(
                unsupported_message=unsupported_message(profile, "vertical_louver"),
                entity_id=entity_id,
                semantic_action="climate_vertical_swing",
            )
        mode, kind = resolve_vertical_swing(
            raw,
            profile.swing_modes,
            current=profile.swing_mode,
        )
        if not mode:
            return ClimatePlan(
                clarification="Jak ustawić pionowy nawiew?",
                entity_id=entity_id,
                semantic_action="climate_vertical_swing",
            )
        return ClimatePlan(
            steps=[
                ClimateStep(
                    semantic_action="climate_vertical_swing",
                    tool="set_device_value",
                    ha_action="set_swing_mode",
                    arguments=_base_args(
                        entity_id,
                        "climate_vertical_swing",
                        action="set_swing_mode",
                        extra=mode,
                        value=mode,
                    ),
                )
            ],
            entity_id=entity_id,
            semantic_action="climate_vertical_swing",
            resolved_capability="vertical_swing",
            axis="vertical",
            target_mode=mode,
        )

    if _FAN_UP.search(raw) or _FAN_DOWN.search(raw) or (
        last_op
        and last_op.action in ("set_fan_mode", "climate_fan_mode")
        and re.search(r"^\s*(?:mocniej|słabiej|slabiej|ciszej)\s*[.!?]*\s*$", raw, re.I)
    ):
        if not profile.fan_modes:
            return ClimatePlan(
                unsupported_message=unsupported_message(profile, "fan_only"),
                entity_id=entity_id,
                semantic_action="climate_fan_step",
            )
        ordered = ordered_fan_modes(profile.fan_modes)
        if len(ordered) < 2:
            return ClimatePlan(
                clarification="Nie umiem bezpiecznie zmienić nawiewu o jeden poziom.",
                entity_id=entity_id,
                semantic_action="climate_fan_step",
            )
        delta = -1 if _FAN_DOWN.search(raw) or re.search(r"słabiej|slabiej|ciszej", raw, re.I) else 1
        nxt = step_fan_mode(profile.fan_mode, profile.fan_modes, delta)
        if not nxt:
            return ClimatePlan(
                clarification="Nie umiem bezpiecznie zmienić nawiewu o jeden poziom.",
                entity_id=entity_id,
                semantic_action="climate_fan_step",
            )
        return ClimatePlan(
            steps=[
                ClimateStep(
                    semantic_action="climate_fan_step",
                    tool="set_device_value",
                    ha_action="set_fan_mode",
                    arguments=_base_args(
                        entity_id,
                        "climate_fan_step",
                        action="set_fan_mode",
                        extra=nxt,
                        value=nxt,
                    ),
                )
            ],
            entity_id=entity_id,
            semantic_action="climate_fan_step",
            resolved_capability="fan",
        )

    if _FAN_ONLY.search(raw):
        return _hvac_plan(profile, "fan_only", "climate_hvac_mode")
    if _DRY.search(raw):
        return _hvac_plan(profile, "dry", "climate_hvac_mode")
    if _COOL.search(raw):
        return _hvac_plan(profile, "cool", "climate_hvac_mode")
    if _HEAT.search(raw) and not _HEAT8.search(raw):
        return _hvac_plan(profile, "heat", "climate_hvac_mode")
    if _AUTO.search(raw):
        return _hvac_plan(profile, "auto", "climate_hvac_mode")

    if _FAN_ABS.search(raw) or match_fan_mode(raw, profile.fan_modes):
        if not profile.fan_modes:
            return ClimatePlan(
                unsupported_message=unsupported_message(profile, "fan_only"),
                entity_id=entity_id,
                semantic_action="climate_fan_mode",
            )
        mode = match_fan_mode(raw, profile.fan_modes)
        if not mode:
            return ClimatePlan(
                clarification="Na jaki nawiew mam ustawić?",
                entity_id=entity_id,
                semantic_action="climate_fan_mode",
            )
        return ClimatePlan(
            steps=[
                ClimateStep(
                    semantic_action="climate_fan_mode",
                    tool="set_device_value",
                    ha_action="set_fan_mode",
                    arguments=_base_args(
                        entity_id,
                        "climate_fan_mode",
                        action="set_fan_mode",
                        extra=mode,
                        value=mode,
                    ),
                )
            ],
            entity_id=entity_id,
            semantic_action="climate_fan_mode",
            resolved_capability="fan",
        )

    delta = parse_delta_celsius(raw)
    if delta is not None:
        current_c = profile.spoken_target_c()
        if current_c is None:
            return ClimatePlan(
                unsupported_message="Nie mam aktualnej temperatury z Home Assistanta.",
                entity_id=entity_id,
                semantic_action="climate_temperature_delta",
            )
        step = _temp_step(profile)
        applied = delta if abs(delta) > 0.05 else (-step if delta < 0 or _REL_COOL.search(raw) else step)
        if abs(delta) < 0.05:
            applied = -step if _REL_COOL.search(raw) else step
        requested = float(current_c) + applied
        valid, err = validate_temperature(profile, requested)
        if err:
            return ClimatePlan(unsupported_message=err, entity_id=entity_id, semantic_action="climate_temperature_delta")
        return ClimatePlan(
            steps=[
                ClimateStep(
                    semantic_action="climate_temperature_delta",
                    tool="set_device_value",
                    ha_action="set_temperature",
                    arguments=_base_args(
                        entity_id,
                        "climate_temperature_delta",
                        action="set_temperature",
                        value=valid,
                    ),
                )
            ],
            entity_id=entity_id,
            semantic_action="climate_temperature_delta",
            resolved_capability="temperature",
        )

    temp = parse_temperature_celsius(raw)
    if temp is not None:
        valid, err = validate_temperature(profile, temp)
        if err:
            return ClimatePlan(
                unsupported_message=err,
                entity_id=entity_id,
                semantic_action="climate_temperature",
            )
        return ClimatePlan(
            steps=[
                ClimateStep(
                    semantic_action="climate_temperature",
                    tool="set_device_value",
                    ha_action="set_temperature",
                    arguments=_base_args(
                        entity_id,
                        "climate_temperature",
                        action="set_temperature",
                        value=valid,
                    ),
                )
            ],
            entity_id=entity_id,
            semantic_action="climate_temperature",
            resolved_capability="temperature",
        )

    if _OFF.search(raw) and (
        _CLIMATE_WORDS.search(raw) or _PRONOUN.search(raw) or raw.lower().strip(" .!?") in ("wyłącz ją", "wylacz ja", "wyłącz ją.")
    ):
        return ClimatePlan(
            steps=[
                ClimateStep(
                    semantic_action="climate_power",
                    tool="turn_off_device",
                    ha_action="device_off",
                    arguments=_base_args(entity_id, "climate_power"),
                )
            ],
            entity_id=entity_id,
            semantic_action="climate_power",
            resolved_capability="on_off",
        )
    if _ON.search(raw) and (
        _CLIMATE_WORDS.search(raw) or _PRONOUN.search(raw)
    ):
        return ClimatePlan(
            steps=[
                ClimateStep(
                    semantic_action="climate_power",
                    tool="turn_on_device",
                    ha_action="device_on",
                    arguments=_base_args(entity_id, "climate_power"),
                )
            ],
            entity_id=entity_id,
            semantic_action="climate_power",
            resolved_capability="on_off",
        )
    return None


def _split_climate_clauses(text: str) -> List[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    parts = re.split(r"\s*(?:,|;|\boraz\b)\s*|\s+\bi\s+(?=(?:ustaw|włącz|wlacz|wyłącz|wylacz|chłodz|grzan|nawiew|tryb))", raw, flags=re.I)
    return [item.strip(" ,.;") for item in parts if item.strip(" ,.;")]


def plan_climate_utterance(
    text: str,
    device: Optional[ActiveDevice],
    last_op: Optional[LastDeviceOperation] = None,
    *,
    profile: Optional[ClimateCapabilityProfile] = None,
) -> Optional[ClimatePlan]:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        from core.local_device_status import (
            is_climate_knowledge_question,
            looks_light_status,
        )

        if is_climate_knowledge_question(raw):
            return None
        if looks_light_status(raw) and not _CLIMATE_WORDS.search(raw):
            return None
    except Exception:
        pass
    if _PERCENT.search(raw) and device and device.domain == "light" and not _CLIMATE_WORDS.search(raw):
        return None
    if _DEGREES.search(raw) and device and device.domain == "light" and not _CLIMATE_WORDS.search(raw):
        return ClimatePlan(
            unsupported_message=DEGREES_ON_LIGHT,
            entity_id=device.entity_id,
            domain="light",
            semantic_action="degree_on_light",
        )
    climate_named = bool(_CLIMATE_WORDS.search(raw))
    active_climate = bool(device and device.domain == "climate" and device.is_valid)
    eco_intent = wants_climate_eco(raw, climate_context=active_climate)
    status_q = False
    try:
        from core.local_device_status import looks_climate_status

        status_q = looks_climate_status(raw, climate_context=True)
    except Exception:
        status_q = False
    if not climate_named and not active_climate and not looks_climate_utterance(raw) and not eco_intent and not status_q:
        return None
    if not climate_named and not active_climate and not status_q:
        if looks_climate_utterance(raw) and not (device and device.domain == "light"):
            # Need a climate entity.
            pass
        else:
            return None

    entity_id = ""
    if active_climate and device is not None:
        entity_id = device.entity_id
    if not entity_id and (
        climate_named
        or status_q
        or (looks_climate_utterance(raw) and not (device and device.domain == "light"))
    ):
        from HomeAssistant import get_registry

        climates = get_registry().all(domains="climate")
        if len(climates) == 1:
            entity_id = climates[0].entity_id
        elif climates:
            gree = [
                item
                for item in climates
                if "gree" in item.entity_id.lower() or "gree" in item.friendly_name.lower()
            ]
            if len(gree) == 1:
                entity_id = gree[0].entity_id
            elif not active_climate:
                names = ", ".join(item.display_name() for item in climates[:3])
                return ClimatePlan(clarification=f"Którą klimatyzację mam wybrać: {names}?")
    if not entity_id:
        return None

    used = profile or profile_for_entity_id(entity_id)
    if used is None:
        return None

    clauses = _split_climate_clauses(raw)
    if len(clauses) <= 1:
        return parse_clause(raw, used, last_op=last_op)

    steps: List[ClimateStep] = []
    notes: List[str] = []
    last_semantic = ""
    for clause in clauses:
        part = parse_clause(clause, used, last_op=last_op)
        if part is None:
            continue
        if part.unsupported_message:
            notes.append(part.unsupported_message)
            continue
        if part.clarification and not part.steps:
            notes.append(part.clarification)
            continue
        steps.extend(part.steps)
        last_semantic = part.semantic_action or last_semantic
    if not steps and notes:
        return ClimatePlan(
            unsupported_message=" ".join(notes),
            entity_id=entity_id,
            semantic_action=last_semantic,
        )
    if steps and notes:
        plan = ClimatePlan(
            steps=steps,
            entity_id=entity_id,
            semantic_action="climate_multi",
            resolved_capability="multi",
        )
        plan.unsupported_message = " ".join(notes)
        return plan
    if steps:
        return ClimatePlan(
            steps=steps,
            entity_id=entity_id,
            semantic_action=last_semantic or "climate_multi",
            resolved_capability="multi",
        )
    return None
