# ==========================
# ELIAN — computer goal semantics (Phase 13 Stage C)
# ==========================
"""Natural computer intents. Anchors only — not a giant phrase dictionary."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core.compound_execute import LOCAL_COMPUTER
from core.intent_family import KNOWN_APPS, _find_app_name

SOURCE_LOCAL_COMPUTER = LOCAL_COMPUTER

STATUS_WINDOWS = re.compile(
    r"(?:"
    r"\bjakie\s+mam\s+(?:otwarte\s+)?okna\b|"
    r"\bco\s+mam\s+otwarte\b|"
    r"\bco\s+jest\s+otwarte\b|"
    r"\blista\s+okien\b"
    r")",
    re.I,
)
STATUS_ACTIVE = re.compile(
    r"(?:"
    r"\bjaka\s+aplikacja\s+(?:jest\s+)?teraz\s+aktywna\b|"
    r"\bjaka\s+aplikacja\s+jest\s+aktywna\b|"
    r"\bkt[oó]re\s+okno\s+jest\s+aktywne\b|"
    r"\bco\s+jest\s+aktywne\b"
    r")",
    re.I,
)
STATUS_APP_OPEN = re.compile(
    r"\bczy\s+(?P<app>[\wąćęłńóśźż]+)(?:a|e)?\s+(?:jest\s+)?(?:otwart|uruchomion|dzia[lł]a)",
    re.I,
)
STATUS_SCREEN = re.compile(
    r"(?:"
    r"\bco\s+mam\s+(?:teraz\s+)?na\s+ekranie\b|"
    r"\bco\s+tu\s+jest\s+napisane\b|"
    r"\bco\s+widz[eę]\s+na\s+ekranie\b|"
    r"\bgdzie\s+jest\s+(?:przycisk\s+)?zapisz\b"
    r")",
    re.I,
)
TYPE_TEXT = re.compile(
    r"\b(?:wpisz|napisz\s+tam|dopisz(?:\s+jeszcze)?|wpisz\s+tam)\b",
    re.I,
)
SELECT_ALL = re.compile(
    r"\b(?:zaznacz\s+(?:wszystko|ca[lł]y\s+tekst|ca[lł]e))\b",
    re.I,
)
UNDO = re.compile(r"\b(?:cofnij|undo)\b", re.I)
COPY = re.compile(r"\b(?:skopiuj|copy)\b", re.I)
PASTE = re.compile(r"\b(?:wklej|paste)\b", re.I)
CUT = re.compile(r"\b(?:wytnij|cut)\b", re.I)
DELETE_LAST_WORD = re.compile(r"\busu[nń]\s+ostatni(?:e)?\s+s[lł]owo\b", re.I)
MINIMIZE = re.compile(r"\bzminimalizuj\b", re.I)
MAXIMIZE = re.compile(r"\bzmaksymalizuj\b", re.I)
RESTORE = re.compile(r"\bprzywr[oó][cć]\s+(?:okno|to)\b", re.I)
FOCUS = re.compile(
    r"\b(?:przejd[zź]\s+do|prze[lł][aą]cz\s+na|aktywuj)\b",
    re.I,
)
CLICK = re.compile(r"\b(?:kliknij|naci[sś]nij\s+przycisk)\b", re.I)
SCROLL = re.compile(
    r"\b(?:przewi[nń]|scroll|troch[eę]\s+ni[zż]ej|na\s+sam\s+d[oó][lł]|jeszcze\s+ni[zż]ej)\b",
    re.I,
)
HOTKEY_ENTER = re.compile(r"\bnaci[sś]nij\s+enter\b", re.I)
CLOSE_WINDOW = re.compile(
    r"\b(?:zamknij\s+(?:to\s+)?okno|zamknij\s+(?:to|je|go|j[aą]))\b",
    re.I,
)
CLIPBOARD_ASK = re.compile(r"\bco\s+mam\s+w\s+schowku\b", re.I)
ORDINAL = re.compile(
    r"\b(?:pierwsz\w*|drug\w*|trzeci\w*|poprzedni|ostatni|ten\s+nad\s+nim|to\s+pole)\b",
    re.I,
)
APPEND = re.compile(r"\bdopisz\b", re.I)


@dataclass
class ComputerGoal:
    action: str = ""
    complete: bool = False
    query: str = ""
    text: str = ""
    app_name: str = ""
    direction: str = ""
    amount: str = ""
    source_kind: str = SOURCE_LOCAL_COMPUTER
    slots: Dict[str, Any] = field(default_factory=dict)

    def to_slots(self) -> Dict[str, Any]:
        data = dict(self.slots)
        data.update(
            {
                "semantic_action": self.action,
                "source_kind": self.source_kind,
                "query": self.query,
                "text_payload": self.text,
                "app_name": self.app_name,
                "direction": self.direction,
                "web_allowed": False,
            }
        )
        return data


def looks_computer_status(text: str) -> bool:
    raw = str(text or "")
    return bool(
        STATUS_WINDOWS.search(raw)
        or STATUS_ACTIVE.search(raw)
        or STATUS_APP_OPEN.search(raw)
        or STATUS_SCREEN.search(raw)
        or CLIPBOARD_ASK.search(raw)
    )


def looks_computer_action(text: str) -> bool:
    raw = str(text or "")
    return bool(
        TYPE_TEXT.search(raw)
        or SELECT_ALL.search(raw)
        or UNDO.search(raw)
        or COPY.search(raw)
        or PASTE.search(raw)
        or CUT.search(raw)
        or DELETE_LAST_WORD.search(raw)
        or MINIMIZE.search(raw)
        or MAXIMIZE.search(raw)
        or RESTORE.search(raw)
        or FOCUS.search(raw)
        or CLICK.search(raw)
        or SCROLL.search(raw)
        or HOTKEY_ENTER.search(raw)
        or CLOSE_WINDOW.search(raw)
        or ORDINAL.search(raw)
    )


_DICTATION_VERB = re.compile(
    r"\b(?:wpisz|dopisz|napisz(?:\s+tam)?)\b",
    re.I,
)
_DICTATION_DO_TARGET = re.compile(
    r"^(?:do\s+)(?P<target>[\wąćęłńóśźż]+)(?:a|u|ie)?\s+",
    re.I,
)
_DICTATION_W_TARGET = re.compile(
    r"^(?:w\s+)(?P<target>notatniku|notatnik|chrome|kursorze|cursorze|cursor)\s*,?\s*",
    re.I,
)
_DICTATION_REF = re.compile(r"^(?:tam|tu|to)\s*,?\s*", re.I)
_DICTATION_ZE = re.compile(r"^(?:[zż]e)\s+", re.I)
_KNOWN_TYPE_TARGETS = {
    "notatniku": "Notatnik",
    "notatnik": "Notatnik",
    "notatnika": "Notatnik",
    "notatnika": "Notatnik",
    "chrome": "Chrome",
    "kursorze": "Cursor",
    "cursorze": "Cursor",
    "cursor": "Cursor",
}


def parse_dictation(text: str) -> Dict[str, str]:
    """Split ACTION / TARGET / PAYLOAD. Payload stays the raw span (no lowercasing)."""
    raw = str(text or "").strip()
    out = {"action": "type_text", "target": "", "payload": ""}
    work = re.sub(
        r"^(?:no\s+i|i\s+teraz|a\s+teraz|to\s+teraz|dobra(?:\s+i)?|okej(?:\s+i)?|i)\s+",
        "",
        raw,
        count=1,
        flags=re.I,
    ).strip()
    lead = _DICTATION_DO_TARGET.match(work)
    if lead:
        token = str(lead.group("target") or "").casefold()
        out["target"] = _KNOWN_TYPE_TARGETS.get(token, lead.group("target"))
        work = work[lead.end() :]
    verb = _DICTATION_VERB.search(work)
    if not verb:
        out["payload"] = _legacy_type_payload(raw)
        return out
    rest = work[verb.end() :].lstrip(" ,.")
    w_target = _DICTATION_W_TARGET.match(rest)
    if w_target:
        token = str(w_target.group("target") or "").casefold()
        if not out["target"]:
            out["target"] = _KNOWN_TYPE_TARGETS.get(token, w_target.group("target"))
        rest = rest[w_target.end() :]
    else:
        rest = _DICTATION_REF.sub("", rest, count=1)
    rest = _DICTATION_ZE.sub("", rest.strip(" ,"), count=1)
    out["payload"] = rest.strip(" \"'„”").rstrip(".,!")
    if not out["target"] and re.search(r"\bnotatnik", raw, re.I):
        out["target"] = "Notatnik"
    return out


def _legacy_type_payload(text: str) -> str:
    raw = str(text or "").strip()
    cleaned = TYPE_TEXT.sub("", raw, count=1)
    cleaned = re.sub(r"^(?:że|ze)\s+", "", cleaned.strip(" .,!"), flags=re.I)
    cleaned = re.sub(r"^(?:tam[,]?\s+)", "", cleaned, flags=re.I)
    cleaned = re.sub(
        r"^(?:w\s+notatniku|do\s+notatnika)\s+",
        "",
        cleaned,
        flags=re.I,
    )
    return cleaned.strip(" .,!")


def _type_payload(text: str) -> str:
    return parse_dictation(text).get("payload") or ""


def extract_type_payload(text: str) -> str:
    """Content span from the original STT/raw utterance. Do not lowercase."""
    return parse_dictation(text).get("payload") or ""


def dictation_target_leaked(text: str, payload: str) -> bool:
    pay = str(payload or "")
    if re.search(r"\b(?:w\s+notatniku|do\s+notatnika)\b", pay, re.I):
        return True
    if pay.strip().lower().startswith("że ") or pay.strip().lower().startswith("ze "):
        return True
    return False


def overlay_raw_type_payload(classification, original: str):
    """Keep intent from normalized text; replace type_text payload with raw span."""
    source = str(original or "").strip()
    if classification is None or not source:
        return classification
    slots = dict(getattr(classification, "slots", None) or {})
    if slots.get("compound"):
        return classification
    action = str(slots.get("semantic_action") or "")
    if action != "type_text" and not TYPE_TEXT.search(source):
        return classification
    if action and action != "type_text":
        return classification
    payload = extract_type_payload(source)
    if payload:
        slots["text_payload"] = payload
        classification.slots = slots
    return classification


def _verb_surface(text: str) -> str:
    """ASR-repaired verbs for classification. Never used as a type payload."""
    try:
        from core.asr_repair import semantic_verb_surface

        return semantic_verb_surface(text)
    except Exception:
        return str(text or "").lower()


def parse_computer_goal(text: str) -> Optional[ComputerGoal]:
    raw = str(text or "").strip()
    if not raw:
        return None
    verbs = _verb_surface(raw)
    app = _find_app_name(raw.lower())
    if STATUS_WINDOWS.search(raw) or STATUS_WINDOWS.search(verbs):
        return ComputerGoal(action="list_windows", complete=True, app_name=app)
    if STATUS_ACTIVE.search(raw) or STATUS_ACTIVE.search(verbs):
        return ComputerGoal(action="get_active_window", complete=True, app_name=app)
    match = STATUS_APP_OPEN.search(raw) or STATUS_APP_OPEN.search(verbs)
    if match:
        token = str(match.group("app") or "")
        mapped = KNOWN_APPS.get(token.lower()) or _find_app_name(token.lower()) or token
        return ComputerGoal(
            action="window_exists",
            complete=True,
            app_name=mapped,
            query=mapped,
        )
    if CLIPBOARD_ASK.search(raw):
        return ComputerGoal(action="read_clipboard", complete=True)
    if STATUS_SCREEN.search(raw):
        return ComputerGoal(action="inspect_ui", complete=True, query=raw)
    if SELECT_ALL.search(raw) or SELECT_ALL.search(verbs):
        return ComputerGoal(action="select_all", complete=True, app_name=app)
    if DELETE_LAST_WORD.search(raw):
        return ComputerGoal(action="delete_last_word", complete=True, app_name=app)
    if UNDO.search(raw) and not TYPE_TEXT.search(raw):
        return ComputerGoal(action="undo", complete=True, app_name=app)
    if COPY.search(raw) and not TYPE_TEXT.search(raw):
        return ComputerGoal(action="copy", complete=True)
    if PASTE.search(raw) and not TYPE_TEXT.search(raw):
        return ComputerGoal(action="paste", complete=True)
    if CUT.search(raw) and not TYPE_TEXT.search(raw):
        return ComputerGoal(action="cut", complete=True)
    if HOTKEY_ENTER.search(raw):
        return ComputerGoal(action="press_key", complete=True, query="enter")
    if MINIMIZE.search(raw):
        return ComputerGoal(action="minimize_window", complete=True, app_name=app, query=app)
    if MAXIMIZE.search(raw):
        return ComputerGoal(action="maximize_window", complete=True, app_name=app, query=app)
    if RESTORE.search(raw):
        return ComputerGoal(action="restore_window", complete=True, app_name=app)
    if FOCUS.search(raw) and app:
        return ComputerGoal(action="focus_window", complete=True, app_name=app, query=app)
    if CLOSE_WINDOW.search(raw):
        return ComputerGoal(action="close_window", complete=True, app_name=app, query=app)
    if TYPE_TEXT.search(raw):
        parsed = parse_dictation(raw)
        payload = str(parsed.get("payload") or "")
        target = str(parsed.get("target") or app or "")
        return ComputerGoal(
            action="type_text",
            complete=bool(payload),
            text=payload,
            app_name=target,
            slots={"append": bool(APPEND.search(raw))},
        )
    if CLICK.search(raw) or CLICK.search(verbs):
        query = CLICK.sub("", raw, count=1).strip(" .,!")
        return ComputerGoal(
            action="click_ui_element",
            complete=bool(query),
            query=query,
            app_name=app,
        )
    if SCROLL.search(raw):
        direction = "up" if re.search(r"g[oó]r|wy[zż]ej", raw, re.I) else "down"
        amount = "end" if re.search(r"sam\s+d[oó][lł]|koniec", raw, re.I) else (
            "small" if re.search(r"troch[eę]|jeszcze", raw, re.I) else "page"
        )
        return ComputerGoal(
            action="scroll",
            complete=True,
            direction=direction,
            amount=amount,
            app_name=app,
        )
    if ORDINAL.search(raw) and re.search(r"\b(?:kliknij|wybierz|ten|to)\b", raw, re.I):
        return ComputerGoal(action="click_referent", complete=True, query=raw)
    return None
