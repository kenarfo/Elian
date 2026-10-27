# ==========================
# ELIAN — app vs HA power-off target (Phase 13 Stage A hardware)
# ==========================
"""wyłącz Spotify → APP; wyłącz lampę → SMART_HOME. Overlap → CLARIFY."""

from __future__ import annotations

import re
from typing import Optional, Tuple

POWER_OFF_VERBS = re.compile(
    r"\b(?:wyłącz|wylacz|zamknij|zakończ|zakoncz|close|z?ga[sś]|wyga[sś]|zgas)\b",
    re.I,
)

HA_DEVICE_NOUNS = re.compile(
    r"\b(?:"
    r"świat[łl]\w*|swiat[lł]\w*|lamp\w*|"
    r"klimatyz\w*|klima|klimy|klimę|klime|"
    r"salon\w*|kuchni\w*|łazien\w*|lazien\w*|"
    r"telewiz\w*|\btv\b|rol[eę]t\w*|wentylat\w*|"
    r"grzejn\w*|ogrzew\w*|gniazdk\w*"
    r")\b",
    re.I,
)

RESOLVE_APP = "APP"
RESOLVE_HA = "SMART_HOME"
RESOLVE_CLARIFY = "CLARIFY"
RESOLVE_NONE = ""


def looks_ha_device_target(text: str) -> bool:
    return bool(HA_DEVICE_NOUNS.search(str(text or "")))


def resolve_power_off_target(text: str) -> Tuple[str, str]:
    """Return (resolution, app_name). resolution is APP / SMART_HOME / CLARIFY / empty."""
    raw = str(text or "").strip()
    if not raw or not POWER_OFF_VERBS.search(raw):
        return RESOLVE_NONE, ""
    try:
        from core.intent_family import _find_app_name

        app_name = str(_find_app_name(raw.lower()) or "").strip()
    except Exception:
        app_name = ""
    ha = looks_ha_device_target(raw)
    if app_name and ha:
        # "zamknij Chrome i zgaś lampę" is compound, not ambiguous single target.
        try:
            from core.intent_family import COMPOUND_CONNECTOR, _is_compound_intent

            if _is_compound_intent(raw) or (
                COMPOUND_CONNECTOR.search(raw)
                and POWER_OFF_VERBS.findall(raw)
                and len(POWER_OFF_VERBS.findall(raw)) >= 2
            ):
                return RESOLVE_NONE, ""
        except Exception:
            pass
        return RESOLVE_CLARIFY, app_name
    if app_name:
        return RESOLVE_APP, app_name
    if ha:
        return RESOLVE_HA, ""
    return RESOLVE_NONE, ""
