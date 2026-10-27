# ==========================
# ELIAN — local device status vs web/knowledge (Phase 12.3.2)
# ==========================
"""Subject + source before temporal words. Read-only. No climate mutations."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict


SOURCE_LOCAL_HA = "LOCAL_HA"
SOURCE_LOCAL_RUNTIME = "LOCAL_RUNTIME"
SOURCE_MEMORY = "MEMORY"
SOURCE_KNOWLEDGE = "KNOWLEDGE"
SOURCE_WEB = "WEB"
SOURCE_UNKNOWN = "UNKNOWN"

INTENT_LOCAL_DEVICE_STATUS = "LOCAL_DEVICE_STATUS"
INTENT_LOCAL_DEVICE_CAPABILITY = "LOCAL_DEVICE_CAPABILITY"
INTENT_KNOWLEDGE = "KNOWLEDGE"
INTENT_WEB = "WEB"
INTENT_RUNTIME = "LOCAL_RUNTIME"

REASON_EXPLICIT_STATUS = "explicit_local_device_status"
REASON_CAPABILITY = "local_device_capability"
REASON_CLIMATE_KNOWLEDGE = "climate_mode_knowledge"
REASON_GENERIC_HVAC_KNOWLEDGE = "generic_hvac_knowledge"
REASON_WEB_FRESHNESS = "external_freshness"
REASON_RUNTIME = "local_runtime_status"


_STATUS_CUE = re.compile(
    r"(?:"
    r"\b(?:czy|jaki|jaka|jakie|jaką|jaka|jakim)\b|"
    r"\bw\s+jakim\s+trybie\b|"
    r"\bna\s+ile\b|"
    r"\bile\s+stopn\b|"
    r"\bco\s+teraz\s+robi\b|"
    r"\bco\s+robi\b|"
    r"\bgdzie\s+jest\s+skierowan\b"
    r")",
    re.I,
)

_COMMAND_LEAD = re.compile(
    r"^\s*(?:ustaw|włącz|wlacz|wyłącz|wylacz|zmień|zmien|zrób|zrob|daj)\b",
    re.I,
)

_KNOWLEDGE_MEANING = re.compile(
    r"\b(?:co\s+oznacza|czym\s+jest|co\s+to\s+jest|wyja[sś]nij|wyjasnij)\b",
    re.I,
)

_OWN_CAPABILITY = re.compile(
    r"\bjakie\s+tryby\s+ma\s+(?:moja\s+)?(?:klima|klimy|klimę|klime|klimatyz\w*)\b",
    re.I,
)

_GENERIC_HVAC_KNOWLEDGE = re.compile(
    r"\bjakie\s+tryby\s+(?:zwykle\s+)?mają\s+klimatyz\w*\b",
    re.I,
)

_CLIMATE_DEVICE = re.compile(
    r"\b(?:klima|klimy|klimę|klime|klimatyz\w*|gree|pular)\b",
    re.I,
)

_CLIMATE_ATTR = re.compile(
    r"(?:"
    r"\b(?:tryb|nawiew|wentylator|żaluz\w*|zaluz\w*|swing|eco|eko|echo|"
    r"turbo|cich\w*|quiet|xfan|x-fan|wy[sś]wietlacz|ekran|"
    r"nocn\w*|sleep|zamarz\w*|away)\b|"
    r"temperatur\w*|stopn\w*|chłodz\w*|chlodz\w*|grzeje|osusz\w*"
    r")",
    re.I,
)

_LIGHT_DEVICE = re.compile(
    r"\b(?:lamp\w*|świat[łl]\w*|swiat[lł]\w*)\b",
    re.I,
)

_NOT_LOCAL_WEB = re.compile(
    r"\b(?:"
    r"pogod[ay]|premier\w*|prezydent\w*|kr[oó]l\w*|papie[zż]\w*|"
    r"minister\w*|kanclerz\w*|wiadomo[sś]ci|google|internet|"
    r"wielk(?:iej|a)\s+brytan|usa|stanów\s+zjednoczon"
    r")\b",
    re.I,
)

_RUNTIME_APP = re.compile(
    r"\b(?:jaka|kt[oó]ra)\s+aplikacj\w*\s+(?:jest\s+)?(?:teraz\s+)?(?:aktywna|otwarta)\b",
    re.I,
)

_REFERS_PREVIOUS = re.compile(
    r"(?:"
    r"\bo\s+kt[oó]rym\s+m[oó]wi[łl]|"
    r"\ba\s+co\s+z\s+tym\s+trybem|"
    r"\bto\s+o\s+czym\s+m[oó]wi"
    r")",
    re.I,
)

_MUSIC_CUE = re.compile(
    r"\b(?:muzyk|spotify|youtube|piosenk|utw[oó]r|playlist)\b",
    re.I,
)

_HVAC = re.compile(
    r"(?:"
    r"\btryb\b|"
    r"\bw\s+jakim\s+trybie\b|"
    r"\bdziała\s+klima|"
    r"\bdziała\s+klimatyz"
    r")",
    re.I,
)
_SUMMARY = re.compile(r"\bco\s+teraz\s+robi\b", re.I)
_TARGET_TEMP = re.compile(
    r"(?:"
    r"ustawion\w*\s+temperatur|"
    r"temperatur\w*\s+ustawion|"
    r"na\s+ile\s+jest|"
    r"jaką\s+temperatur\w*\s+ustawi|"
    r"docelow"
    r")",
    re.I,
)
_CURRENT_TEMP = re.compile(
    r"(?:"
    r"temperatur\w*\s+w\s+pokoju|"
    r"w\s+pomieszczeniu|"
    r"ile\s+stopn\w*\s+pokaz|"
    r"pokazuje\s+klima|"
    r"aktualn\w*\s+temperatur"
    r")",
    re.I,
)
_OUTDOOR = re.compile(r"temperatur\w*\s+na\s+zewn", re.I)
_FAN = re.compile(
    r"\b(?:nawiew|prędko[sś][cć]\s+wentylator|predkosc\s+wentylator|wentylator)\b",
    re.I,
)
_SWING = re.compile(
    r"(?:"
    r"\bswing\b|"
    r"żaluz|"
    r"zaluz|"
    r"skierowan\w*\s+nawiew|"
    r"gdzie\s+jest\s+skierowan"
    r")",
    re.I,
)
_QUIET = re.compile(r"(?:tryb\s+cich|\bcichy\b|\bquiet\b)", re.I)
_DISPLAY = re.compile(r"(?:wyświetlacz|wyswietlacz|ekran\s+na\s+klim|\bpanel\b)", re.I)
_XFAN = re.compile(r"(?:x-fan|\bxfan\b)", re.I)
_PRESET_ECO = re.compile(r"\b(?:eco|eko|ekonomicz)\b", re.I)
_PRESET_TURBO = re.compile(r"\b(?:turbo|boost)\b", re.I)
_PRESET_SLEEP = re.compile(r"(?:tryb\s+nocn|\bsleep\b|\bsenny\b)", re.I)
_PRESET_AWAY = re.compile(r"(?:zamarz|\baway\b|8\s*stopn)", re.I)
_POWER = re.compile(
    r"(?:"
    r"czy\s+(?:klima|klimatyz\w*|lampa|świat[łl]\w*|swiat[lł]\w*)\s+"
    r"(?:jest\s+)?(?:włącz|wlacz|działa)|"
    r"czy\s+(?:jest\s+)?(?:włączon|wlaczon)|"
    r"czy\s+(?:klima|klimatyz\w*)\s+działa"
    r")",
    re.I,
)
_BRIGHTNESS = re.compile(
    r"(?:na\s+ile\s+jest\s+ustawion\w*\s+lamp|jasno[sś][cć]\s+lamp)",
    re.I,
)


@dataclass
class SourceResolution:
    kind: str = SOURCE_UNKNOWN
    intent: str = ""
    reason: str = ""
    web_allowed: bool = True
    semantic_action: str = ""
    domain: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def print_source_diagnostic(source: SourceResolution) -> None:
    print(
        f"[SOURCE]\nkind={source.kind}\nreason={source.reason}\n"
        f"web_allowed={source.web_allowed}"
    )


def _goal_core(text: str) -> str:
    return str(text or "").strip().split("\n(", 1)[0].strip()


def refers_to_previous_answer(text: str) -> bool:
    return bool(_REFERS_PREVIOUS.search(_goal_core(text)))


def is_climate_knowledge_question(text: str) -> bool:
    raw = _goal_core(text)
    if _GENERIC_HVAC_KNOWLEDGE.search(raw):
        return True
    if _KNOWLEDGE_MEANING.search(raw) and (
        _CLIMATE_DEVICE.search(raw) or re.search(r"\btryb\b", raw, re.I)
    ):
        return True
    return False


def is_climate_capability_question(text: str) -> bool:
    return bool(_OWN_CAPABILITY.search(_goal_core(text)))


def looks_status_question(text: str) -> bool:
    raw = _goal_core(text)
    if not raw:
        return False
    if _COMMAND_LEAD.search(raw) and not _STATUS_CUE.search(raw):
        return False
    if raw.rstrip().endswith("?"):
        return True
    return bool(_STATUS_CUE.search(raw))


def looks_light_status(text: str) -> bool:
    raw = _goal_core(text)
    if is_climate_knowledge_question(raw) or is_climate_capability_question(raw):
        return False
    if not looks_status_question(raw):
        return False
    if _CLIMATE_DEVICE.search(raw):
        return False
    return bool(_LIGHT_DEVICE.search(raw))


def looks_climate_status(text: str, *, climate_context: bool = False) -> bool:
    raw = _goal_core(text)
    if is_climate_knowledge_question(raw) or is_climate_capability_question(raw):
        return False
    if not looks_status_question(raw):
        return False
    if _NOT_LOCAL_WEB.search(raw) and not _CLIMATE_DEVICE.search(raw):
        return False
    if looks_light_status(raw) and not _CLIMATE_DEVICE.search(raw):
        return False
    if _CLIMATE_DEVICE.search(raw):
        return True
    if _FAN.search(raw) or _SWING.search(raw):
        return True
    if _CURRENT_TEMP.search(raw) or _TARGET_TEMP.search(raw) or _OUTDOOR.search(raw):
        return True
    if (
        _QUIET.search(raw)
        or _PRESET_ECO.search(raw)
        or _PRESET_TURBO.search(raw)
        or _PRESET_SLEEP.search(raw)
        or _DISPLAY.search(raw)
        or _XFAN.search(raw)
    ):
        return True
    if climate_context and _CLIMATE_ATTR.search(raw):
        return True
    return False


def looks_local_device_status(
    text: str,
    *,
    active_domain: str = "",
) -> bool:
    raw = _goal_core(text)
    domain = str(active_domain or "").lower()
    if looks_climate_status(raw, climate_context=domain == "climate"):
        return True
    if looks_light_status(raw):
        return True
    if domain in ("light", "switch", "fan", "cover") and looks_status_question(raw):
        if _NOT_LOCAL_WEB.search(raw):
            return False
        if is_climate_knowledge_question(raw):
            return False
        return True
    return False


def climate_status_semantic_action(text: str) -> str:
    raw = _goal_core(text)
    if is_climate_capability_question(raw):
        return "climate_status_capabilities"
    if _SUMMARY.search(raw):
        return "climate_status_summary"
    if _OUTDOOR.search(raw):
        return "climate_status_outdoor"
    if _CURRENT_TEMP.search(raw):
        return "climate_status_current_temperature"
    if _TARGET_TEMP.search(raw):
        return "climate_status_target_temperature"
    if _FAN.search(raw) and not _SWING.search(raw):
        return "climate_status_fan"
    if re.search(r"poziom\w*\s+swing|swing\s+poziom|lewo-prawo", raw, re.I):
        return "climate_status_horizontal_swing"
    if _SWING.search(raw):
        return "climate_status_vertical_swing"
    if _QUIET.search(raw):
        return "climate_status_quiet"
    if _DISPLAY.search(raw):
        return "climate_status_display"
    if _XFAN.search(raw):
        return "climate_status_xfan"
    if _PRESET_ECO.search(raw):
        return "climate_status_preset"
    if _PRESET_TURBO.search(raw):
        return "climate_status_preset"
    if _PRESET_SLEEP.search(raw):
        return "climate_status_preset"
    if _PRESET_AWAY.search(raw):
        return "climate_status_preset"
    if _POWER.search(raw) and not _HVAC.search(raw):
        return "climate_status_power"
    if _HVAC.search(raw):
        return "climate_status_hvac"
    if re.search(r"\btemperatur", raw, re.I):
        return "climate_status_target_temperature"
    return "climate_status_hvac"


def classify_local_request(
    text: str,
    *,
    active_domain: str = "",
) -> SourceResolution:
    raw = _goal_core(text)
    domain = str(active_domain or "").lower()

    if _RUNTIME_APP.search(raw):
        return SourceResolution(
            kind=SOURCE_LOCAL_RUNTIME,
            intent=INTENT_RUNTIME,
            reason=REASON_RUNTIME,
            web_allowed=False,
            semantic_action="runtime_active_app",
        )

    if is_climate_knowledge_question(raw):
        reason = (
            REASON_GENERIC_HVAC_KNOWLEDGE
            if _GENERIC_HVAC_KNOWLEDGE.search(raw)
            else REASON_CLIMATE_KNOWLEDGE
        )
        return SourceResolution(
            kind=SOURCE_KNOWLEDGE,
            intent=INTENT_KNOWLEDGE,
            reason=reason,
            web_allowed=False,
            semantic_action="knowledge",
            domain="climate",
        )

    if is_climate_capability_question(raw):
        return SourceResolution(
            kind=SOURCE_LOCAL_HA,
            intent=INTENT_LOCAL_DEVICE_CAPABILITY,
            reason=REASON_CAPABILITY,
            web_allowed=False,
            semantic_action="climate_status_capabilities",
            domain="climate",
        )

    if looks_climate_status(raw, climate_context=domain == "climate"):
        action = climate_status_semantic_action(raw)
        return SourceResolution(
            kind=SOURCE_LOCAL_HA,
            intent=INTENT_LOCAL_DEVICE_STATUS,
            reason=REASON_EXPLICIT_STATUS,
            web_allowed=False,
            semantic_action=action,
            domain="climate",
        )

    if looks_light_status(raw) or (
        domain == "light" and looks_status_question(raw) and not _NOT_LOCAL_WEB.search(raw)
    ):
        action = "light_status_brightness" if _BRIGHTNESS.search(raw) else "light_status"
        return SourceResolution(
            kind=SOURCE_LOCAL_HA,
            intent=INTENT_LOCAL_DEVICE_STATUS,
            reason=REASON_EXPLICIT_STATUS,
            web_allowed=False,
            semantic_action=action,
            domain="light",
        )

    if looks_local_device_status(raw, active_domain=domain):
        return SourceResolution(
            kind=SOURCE_LOCAL_HA,
            intent=INTENT_LOCAL_DEVICE_STATUS,
            reason=REASON_EXPLICIT_STATUS,
            web_allowed=False,
            semantic_action="device_status",
            domain=domain,
        )

    return SourceResolution()


def resolve_source(
    text: str,
    *,
    active_domain: str = "",
) -> SourceResolution:
    local = classify_local_request(text, active_domain=active_domain)
    if local.kind != SOURCE_UNKNOWN:
        return local
    raw = _goal_core(text)
    try:
        from core.freshness import analyze_freshness

        fresh = analyze_freshness(raw, skip_local_guard=True)
        if fresh.freshness_required:
            return SourceResolution(
                kind=SOURCE_WEB,
                intent=INTENT_WEB,
                reason=REASON_WEB_FRESHNESS,
                web_allowed=True,
                semantic_action="web_search",
            )
    except Exception:
        pass
    return SourceResolution()


def should_exclude_website_context(text: str, *, active_domain: str = "") -> bool:
    local = classify_local_request(text, active_domain=active_domain)
    return local.kind == SOURCE_LOCAL_HA and not refers_to_previous_answer(text)


def should_exclude_preferences(text: str, *, active_domain: str = "") -> bool:
    if _MUSIC_CUE.search(_goal_core(text)):
        return False
    return should_exclude_website_context(text, active_domain=active_domain)


def should_exclude_stale_web_focus(text: str, *, active_domain: str = "") -> bool:
    return should_exclude_website_context(text, active_domain=active_domain)
