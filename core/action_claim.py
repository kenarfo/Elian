# ==========================
# ELIAN — action-claim hard guard (Phase 12.2 / 12.3.3)
# ==========================
"""Block user-facing side-effect claims without a matching CURRENT-TURN tool."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence


CLAIM_DIM = "light_dim"
CLAIM_BRIGHTEN = "light_brighten"
CLAIM_ON = "device_on"
CLAIM_OFF = "device_off"
CLAIM_VOLUME = "volume"
CLAIM_SET = "device_set"
CLAIM_OPEN = "app_open"
CLAIM_CLOSE = "app_close"
CLAIM_CREATE = "create"
CLAIM_SEND = "send"
CLAIM_CLICK = "click"
CLAIM_TYPE = "type"
CLAIM_SELECT = "select"
CLAIM_COPY = "copy"
CLAIM_MOVE = "move"
CLAIM_SAVE = "save"
CLAIM_CHECK = "check"
CLAIM_GENERIC = "generic"


_CLAIMS = (
    (CLAIM_DIM, re.compile(r"\b(?:przyciemniam|ściemniam|sciemniam|przyciemni[łl][ae]m|ściemni[łl][ae]m)\b", re.I)),
    (CLAIM_BRIGHTEN, re.compile(r"\b(?:rozja[sś]niam|rozja[sś]ni[łl][ae]m)\b", re.I)),
    (CLAIM_ON, re.compile(
        r"\b(?:włączam|wlaczam|włączy[łl][ae]m|wlaczy[łl]em|włącz[ęe]|wlacz[ęe]|zapalam)\b",
        re.I,
    )),
    (CLAIM_OFF, re.compile(
        r"\b(?:wyłączam|wylaczam|wyłączy[łl][ae]m|wylaczy[łl]em|wyłącz[ęe]|wylacz[ęe]|zgaszam)\b",
        re.I,
    )),
    (CLAIM_VOLUME, re.compile(
        r"(?:"
        r"\b(?:zwi[eę]ksz[ęe]|zwi[eę]kszam|zwi[eę]kszy[łl][ae]m|"
        r"zmniejsz[ęe]|zmniejszam|zmniejszy[łl][ae]m|"
        r"[sś]cisz[ęe]|[sś]ciszy[łl][ae]m|"
        r"podgło[sś]ni[ęe]|podgło[sś]ni[łl][ae]m)\b.{0,40}g[lł]o[sś]|"
        r"g[lł]o[sś]no[sś][cć].{0,24}(?:zwi[eę]ksz|zmniejsz|[sś]cisz|podgło)|"
        r"\bg[lł]o[sś]niej\b|"
        r"\bciszej\b"
        r")",
        re.I,
    )),
    (CLAIM_SET, re.compile(
        r"\b(?:ustawiam|ustawi[łl][ae]m|ustawi[ęe]|podnoszę|podnosze|obniżam|obnizam|"
        r"przełączam|przelaczam|przełącz[ęe]|przelacz[ęe])\b",
        re.I,
    )),
    (CLAIM_OPEN, re.compile(
        r"\b(?:otwieram|otworzy[łl][ae]m|otworz[ęe]|uruchamiam|uruchomi[ęe]|odpalam|odpal[ęe])\b",
        re.I,
    )),
    (CLAIM_CLOSE, re.compile(r"\b(?:zamykam|zamkną[łl][ae]m|zamknalem|zamkn[ęe])\b", re.I)),
    (CLAIM_CREATE, re.compile(r"\b(?:tworzę|tworze|stworzy[łl][ae]m)\b", re.I)),
    (CLAIM_SEND, re.compile(
        r"\b(?:wysyłam|wysylam|wysła[łl][ae]m|wyslalem|"
        r"i(?:['’]m|\s+am)\s+sending|i\s+sent|sending)\b",
        re.I,
    )),
    (CLAIM_CLICK, re.compile(r"\b(?:klikn[aą][łl][ae]m|klikam|klikn[ęe])\b", re.I)),
    (CLAIM_TYPE, re.compile(r"\b(?:wpisa[łl][ae]m|wpisuj[ęe]|wpisuję|wpisuje)\b", re.I)),
    (CLAIM_SELECT, re.compile(r"\b(?:zaznaczy[łl][ae]m|zaznaczam)\b", re.I)),
    (CLAIM_COPY, re.compile(r"\b(?:skopiowa[łl][ae]m|kopiuje|kopiu[jęe])\b", re.I)),
    (CLAIM_MOVE, re.compile(r"\b(?:przeni[oó]s[łl][ae]m|przenosz[ęe])\b", re.I)),
    (CLAIM_SAVE, re.compile(r"\b(?:zapisa[łl][ae]m|zapisuj[ęe]|zapisuję)\b", re.I)),
    # Check / fetch / update claims (PL + EN) — require a matching ToolResult.
    (CLAIM_CHECK, re.compile(
        r"(?:"
        r"\b(?:sprawdzam|sprawdz[ęe]|sprawdzi[łl][ae]m|"
        r"pobieram|pobra[łl][ae]m|odczytuj[ęe]|odczyta[łl][ae]m|"
        r"zaktualizowa[łl][ae]m|aktualizuj[ęe]|"
        r"znalaz[łl][ae]m|szukam)\b|"
        r"\bi(?:['’]ll|\s+will)\s+(?:check|fetch|get|look\s+up|find|read|update)\b|"
        r"\bi(?:['’]m|\s+am)\s+(?:checking|fetching|getting|looking\s+up|reading|updating)\b|"
        r"\bi\s+(?:checked|fetched|got|found|read|updated)\b|"
        r"\blet\s+me\s+(?:check|fetch|look\s+up|get)\b|"
        r"\bchecking\s+(?:the\s+)?(?:weather|status|that)\b"
        r")",
        re.I,
    )),
    (CLAIM_GENERIC, re.compile(r"\b(?:gotowe|zrobione)\b", re.I)),
)

_APP_HINT = re.compile(
    r"\b(?:spotify|discord|chrome|steam|youtube|epic|notatnik|kalkulator|firefox|edge)\b",
    re.I,
)

HONEST_NO_TOOL = "Nie wykonałem jeszcze tej czynności."
HONEST_NO_TOOL_EN = "I haven't done that yet."
HONEST_TOOL_FAILED = "Nie udało się wykonać tej czynności."
HONEST_TOOL_FAILED_EN = "That action didn't succeed."
HONEST_TOOL_UNAVAILABLE = "To narzędzie jest teraz niedostępne."
HONEST_TOOL_UNAVAILABLE_EN = "That tool is unavailable right now."
HONEST_TOOL_TIMEOUT = "Przekroczono czas oczekiwania na wynik."
HONEST_TOOL_TIMEOUT_EN = "That action timed out."


def honest_no_tool_message() -> str:
    try:
        from Config import get_config_value

        lang = str(get_config_value("language", default="pl") or "pl").lower()
        if lang.startswith("en"):
            return HONEST_NO_TOOL_EN
    except Exception:
        pass
    return HONEST_NO_TOOL


def honest_tool_outcome_message(status: str = "") -> str:
    """User-facing honest reply for failed/unavailable/timeout tools."""
    key = str(status or "").strip().lower()
    try:
        from Config import get_config_value

        lang = str(get_config_value("language", default="pl") or "pl").lower()
    except Exception:
        lang = "pl"
    en = lang.startswith("en")
    if key in ("timeout", "timed_out", "tool_timeout"):
        return HONEST_TOOL_TIMEOUT_EN if en else HONEST_TOOL_TIMEOUT
    if key in ("unavailable", "missing", "not_available", "no_capability"):
        return HONEST_TOOL_UNAVAILABLE_EN if en else HONEST_TOOL_UNAVAILABLE
    if key in ("error", "failed", "failure", "tool_error"):
        return HONEST_TOOL_FAILED_EN if en else HONEST_TOOL_FAILED
    return HONEST_NO_TOOL_EN if en else HONEST_NO_TOOL


@dataclass
class ClaimGuardResult:
    text: str
    blocked: bool = False
    claim_kind: str = ""


def detect_action_claim(text: str) -> str:
    kinds = detect_all_action_claims(text)
    return kinds[0] if kinds else ""


def detect_all_action_claims(text: str) -> List[str]:
    raw = str(text or "")
    if not raw.strip():
        return []
    if "?" in raw:
        return []
    found: List[str] = []
    for kind, pattern in _CLAIMS:
        if not pattern.search(raw):
            continue
        if kind == CLAIM_SET and CLAIM_VOLUME in found:
            continue
        if kind == CLAIM_ON and CLAIM_OPEN in found:
            continue
        found.append(kind)
    return found


def claims_side_effect(text: str) -> bool:
    return bool(detect_action_claim(text))


def _obs_entity(item: Dict[str, Any]) -> str:
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    return str(
        data.get("resolved_entity_id")
        or data.get("entity_id")
        or data.get("resolved_target")
        or ""
    )


def _obs_action(item: Dict[str, Any]) -> str:
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    tool = str(item.get("tool") or "")
    if tool == "turn_on_device":
        return "turn_on"
    if tool == "turn_off_device":
        return "turn_off"
    return str(data.get("action") or tool)


def _obs_value(item: Dict[str, Any]) -> Optional[int]:
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    try:
        if data.get("value") is None:
            return None
        return int(data.get("value"))
    except (TypeError, ValueError):
        return None


def _computer_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    nested = data.get("computer_observation")
    if isinstance(nested, dict):
        return nested
    return {}


def observation_matches_claim(kind: str, item: Dict[str, Any], text: str = "") -> bool:
    if not item.get("success"):
        return False
    tool = str(item.get("tool") or "")
    action = _obs_action(item)
    value = _obs_value(item)
    nested = _computer_payload(item)
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    if kind == CLAIM_GENERIC:
        if nested.get("target_mismatch") or data.get("target_mismatch"):
            return False
        computer_action = str(nested.get("semantic_action") or nested.get("action") or action or "")
        if tool in ("press_key", "hotkey", "type_text") or computer_action in (
            "select_all",
            "hotkey",
            "type_text",
            "press_key",
            "paste",
            "delete_last_word",
        ):
            verified = bool(
                nested.get("target_verified")
                or data.get("target_verified")
            )
            level = str(
                nested.get("verification_level")
                or data.get("verification_level")
                or item.get("verification_level")
                or ""
            )
            if not verified:
                return False
            if level not in ("BASIC", "STRONG"):
                return False
            return True
        # Bare Gotowe/Zrobione: require explicit verification for side-effect tools.
        if tool in (
            "open_app",
            "close_app",
            "volume_up",
            "volume_down",
            "play_media",
            "pause_media",
            "resume_media",
            "turn_on_device",
            "turn_off_device",
            "set_device_value",
            "open_website",
            "open_service",
        ):
            ha = data.get("ha_verify") if isinstance(data.get("ha_verify"), dict) else {}
            verified = bool(
                item.get("verified_success") is True
                or item.get("verified") is True
                or str(data.get("verification") or "").upper()
                in ("SUCCESS", "PASSED", "ALREADY_SATISFIED")
                or str(ha.get("verification") or "").upper()
                in ("SUCCESS", "PASSED", "ALREADY_SATISFIED")
            )
            return verified
        return True
    if kind == CLAIM_DIM:
        if tool != "set_device_value":
            return False
        if action == "adjust_brightness" and value is not None and value < 0:
            return True
        return False
    if kind == CLAIM_BRIGHTEN:
        if tool != "set_device_value":
            return False
        if action == "adjust_brightness" and (value is None or value > 0):
            return True
        return False
    if kind == CLAIM_ON:
        if tool == "turn_on_device" or action in ("turn_on", "device_on", "light_on"):
            return True
        if tool in ("open_app", "open_website") and _APP_HINT.search(str(text or "")):
            return True
        return False
    if kind == CLAIM_OFF:
        return tool == "turn_off_device" or action in ("turn_off", "device_off", "light_off")
    if kind == CLAIM_VOLUME:
        return tool in ("volume_up", "volume_down") or action == "adjust_volume"
    if kind == CLAIM_SET:
        return tool == "set_device_value"
    if kind == CLAIM_OPEN:
        return tool in ("open_app", "open_website")
    if kind == CLAIM_CLOSE:
        return tool == "close_app"
    if kind == CLAIM_CREATE:
        return tool in ("create_reminder", "create_calendar_event")
    if kind == CLAIM_SEND:
        return tool in ("send_message",)
    if kind == CLAIM_CLICK:
        return tool in (
            "click_ui_element",
            "double_click_ui_element",
            "right_click_ui_element",
        )
    if kind == CLAIM_TYPE:
        return tool in ("type_text", "type_into_element")
    if kind == CLAIM_SELECT:
        return tool in ("hotkey", "press_key") or action in ("select_all", "hotkey")
    if kind == CLAIM_COPY:
        return tool in ("hotkey", "press_key", "copy_file")
    if kind == CLAIM_MOVE:
        return tool in ("move_file", "drag_element", "drag_to_element")
    if kind == CLAIM_SAVE:
        return tool in ("press_key", "hotkey") or "save" in action
    if kind == CLAIM_CHECK:
        return tool in (
            "get_weather",
            "web_search",
            "fetch_web_page",
            "get_home_summary",
            "get_situation_briefing",
            "list_reminders",
            "get_today_schedule",
            "list_os_notifications",
            "get_phone_status",
            "get_now_playing",
            "describe_screen",
            "inspect_ui",
            "browser_read_page",
        )
    return False


def successful_matching_observation(
    text: str,
    observations: Sequence[Dict[str, Any]] | None,
) -> Optional[Dict[str, Any]]:
    kind = detect_action_claim(text)
    if not kind:
        return None
    for item in observations or []:
        if not isinstance(item, dict):
            continue
        if observation_matches_claim(kind, item, text=text):
            return item
    return None


def guard_action_claim(
    message: str,
    observations: Sequence[Dict[str, Any]] | None,
    *,
    fallback: str = "",
) -> ClaimGuardResult:
    text = str(message or "").strip()
    kinds = detect_all_action_claims(text)
    if not kinds:
        return ClaimGuardResult(text=text, blocked=False)
    current = [item for item in (observations or []) if isinstance(item, dict)]
    successes = [item for item in current if item.get("success")]
    honest = fallback or honest_no_tool_message()
    if not successes:
        # Prefer specific honesty when the only observations failed / timed out / unavailable.
        if current and not successes:
            statuses = [
                str(item.get("status") or item.get("error") or "").lower()
                for item in current
            ]
            if any("timeout" in s or "timed" in s for s in statuses):
                honest = honest_tool_outcome_message("timeout")
            elif any(
                s in ("unavailable", "missing", "no_capability")
                or "unavail" in s
                for s in statuses
            ):
                honest = honest_tool_outcome_message("unavailable")
            elif any(item.get("success") is False for item in current):
                honest = honest_tool_outcome_message("failed")
        return ClaimGuardResult(text=honest, blocked=True, claim_kind=kinds[0])
    remaining = list(successes)
    for kind in kinds:
        match_idx = None
        for index, item in enumerate(remaining):
            if observation_matches_claim(kind, item, text=text):
                match_idx = index
                break
        if match_idx is None:
            return ClaimGuardResult(text=honest, blocked=True, claim_kind=kind)
        remaining.pop(match_idx)
    return ClaimGuardResult(text=text, blocked=False, claim_kind=kinds[0])
