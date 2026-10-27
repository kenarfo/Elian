# ==========================
# ELIAN — Jarvis conversation layer (C.5)
# ==========================
"""Natural spoken replies from observations. Never invent success."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from Config import get_config_value

_CLOSING = re.compile(
    r"(?:\s*)(?:czy\s+mog[eę]\s+(?:ci\s+)?(?:jeszcze\s+)?pom[oó]c(?:\s+w\s+czym[sś]\s+jeszcze)?"
    r"|w\s+czym\s+mog[eę]\s+pom[oó]c"
    r"|jak\s+mog[eę]\s+pom[oó]c)[?.!]?\s*$",
    re.I,
)
_FAKE_FEELING = re.compile(
    r"\b(?:czuj[eę]|mam\s+emocje|pami[eę]tam\s+jak\s+cz[lł]owiek)\b",
    re.I,
)
_DEBUG_LEAK = re.compile(
    r"(?:"
    r"narz[eę]dzie\s+(?:turn_|open_|play_|set_|get_|web_|fetch_|type_|close_|adjust_)"
    r"|narz[eę]dzie\s+[a-z][a-z0-9]*(?:_[a-z0-9]+)+"
    r"|toolstatus|status\s+timeout|side_effect|unknown_side_effect|"
    r"traceback|exception|toolresult|local_computer|active_window_changed|"
    r"tool=type_text|status=success"
    r")",
    re.I,
)
_JARGON_TOKEN = re.compile(
    r"\b(?:LOCAL_COMPUTER|LOCAL_HA|LOCAL_MEDIA|ToolResult|UNKNOWN_SIDE_EFFECT|"
    r"ACTIVE_WINDOW_CHANGED|SIDE_EFFECT|STEP_SUCCEEDED|PLAN_COMPLETED|"
    r"tool=type_text|status=success)\b",
    re.I,
)
_ACK_SENTENCE = re.compile(r"^(?:gotowe|zrobione|ok|okej|otwarte|otwarty)\.?$", re.I)
_HELP_AGAIN = (
    "czy mogę ci jeszcze pomóc",
    "czy mogę pomóc w czymś jeszcze",
    "czy mogę pomóc",
    "w czym mogę pomóc",
    "jak mogę pomóc",
)


@dataclass
class ResponseContext:
    user_goal: str = ""
    plan_status: str = ""
    successful_steps: List[Dict[str, Any]] = field(default_factory=list)
    failed_steps: List[Dict[str, Any]] = field(default_factory=list)
    observations: List[Dict[str, Any]] = field(default_factory=list)
    verification: Dict[str, Any] = field(default_factory=dict)
    active_context: Dict[str, Any] = field(default_factory=dict)
    selected_conversational_context: Dict[str, Any] = field(default_factory=dict)
    perception_context: Dict[str, Any] = field(default_factory=dict)
    safety: bool = False
    mode: str = ""


def concise_execution() -> bool:
    return bool(get_config_value("personality", "concise_execution", default=True))


def dry_humor_allowed(*, safety: bool = False) -> bool:
    if safety:
        return False
    level = str(
        get_config_value("personality", "dry_humor", default="low")
        or get_config_value("personality", "humor", default="low")
        or "low"
    ).lower()
    return level not in ("off", "none", "0")


def strip_service_closers(text: str) -> str:
    if not get_config_value("personality", "no_service_closing_phrase", default=True):
        return str(text or "")
    out = _CLOSING.sub("", str(text or "")).strip()
    lowered = out.lower()
    for phrase in _HELP_AGAIN:
        if lowered.endswith(phrase) or lowered.endswith(phrase + "?"):
            out = out[: -len(phrase)].rstrip(" ?!.")
    return out.strip()


def strip_fake_feelings(text: str) -> str:
    raw = str(text or "").strip()
    if _FAKE_FEELING.search(raw):
        return "Sprawdziłem to na podstawie obserwacji, nie odczuć."
    return raw


def social_reply(goal: str = "") -> str:
    """Small talk — no lamp/Spotify dump. Greeting word alone is not 'powiedz X'."""
    text = str(goal or "").strip().lower()
    if text in {"casual conversation", "tell a joke or something funny"}:
        text = ""
    core = re.sub(r"[.!?…,]+$", "", text).strip()
    if re.fullmatch(r"cze[sś][cć](?:\s+elian)?", core):
        return "Cześć."
    if re.fullmatch(r"hej(?:\s+elian)?", core):
        return "Hej."
    if re.fullmatch(r"siema", core):
        return "Siema."
    if re.fullmatch(r"dzie[nń]\s+dobry[.!?…]*", text):
        return "Dzień dobry."
    if "tam" in text:
        return "Spokojnie. Jestem, słucham."
    if "masz" in text or "słych" in text:
        return "W porządku. A ty?"
    return "Jestem. Mów."


def observation_from_screen(state: Any, extra: Optional[Dict[str, Any]] = None) -> str:
    extra = extra or {}
    window = {}
    if hasattr(state, "active_window"):
        window = dict(getattr(state, "active_window") or {})
    elif isinstance(state, dict):
        window = dict(state.get("active_window") or {})
    title = str(window.get("title") or extra.get("title") or "").strip()
    process = str(window.get("process") or extra.get("process") or "").strip()
    editor = str(extra.get("editor_text") or extra.get("document") or "").strip()
    app = title or process
    if "notepad" in (process + " " + title).lower() or "notatnik" in (process + " " + title).lower():
        app = "Notatnik"
    if app and editor:
        short = editor if len(editor) <= 80 else editor[:77] + "…"
        return f"Masz otwarty {app} z dokumentem „{short}”."
    if app:
        return f"Masz otwarty {app}."
    return "Widzę pulpit, ale nie mam pewnego opisu aktywnego okna."


def execution_reply(ctx: ResponseContext) -> str:
    successes = list(ctx.successful_steps or [])
    failures = list(ctx.failed_steps or [])
    if not successes and not failures:
        obs = list(ctx.observations or [])
        successes = [item for item in obs if item.get("success")]
        failures = [item for item in obs if not item.get("success")]
    # Prefer situation-based speech over generic "Gotowe/Wykonałem".
    soft = ""
    expected = {}
    try:
        active = ctx.active_context if isinstance(ctx.active_context, dict) else {}
        ug = active.get("user_goal") if isinstance(active.get("user_goal"), dict) else {}
        soft = str(ug.get("soft_kind") or active.get("soft_kind") or "")
        expected = dict(ug.get("expected_result") or active.get("user_goal_expected") or {})
    except Exception:
        soft = ""
        expected = {}
    if successes and failures:
        return strip_service_closers(
            summarize_compound_steps(successful_steps=successes, failed_steps=failures)
        )
    if failures and not successes:
        return strip_service_closers(
            summarize_compound_steps(successful_steps=[], failed_steps=failures)
        )
    if len(successes) > 1:
        return strip_service_closers(
            summarize_compound_steps(successful_steps=successes, failed_steps=[])
        )
    if successes:
        natural = _soft_success_phrase(soft, expected, successes[0])
        if natural:
            return strip_service_closers(natural)
        if _action_key(successes[0]) in ("open_app", "open_service"):
            phrase = _open_phrase(successes[0])
            return strip_service_closers(phrase if phrase.endswith(".") else phrase + ".")
        msg = str(successes[0].get("message") or "").strip()
        if msg and len(msg) <= 80 and not _DEBUG_LEAK.search(msg):
            cleaned = dedup_acks(msg)
            if cleaned.lower().rstrip(".") in ("wykonałem polecenie", "polecenie wykonane"):
                return strip_service_closers(_single_success_ack(successes[0]))
            return strip_service_closers(cleaned)
        return _single_success_ack(successes[0])
    return "Nie udało mi się tego zrobić."


def _soft_success_phrase(
    soft: str, expected: Dict[str, Any], step: Dict[str, Any]
) -> str:
    kind = str(soft or expected.get("kind") or "").lower()
    tool = str(step.get("tool") or "").lower()
    if kind in ("volume_down",) or tool == "volume_down":
        return "Ściszyłem."
    if kind in ("volume_up",) or tool == "volume_up":
        return "Podgłośniłem."
    if kind in ("brightness_up",):
        return "Rozjaśniłem."
    if kind in ("brightness_down",):
        return "Przyciemniłem."
    if kind in ("media_calm", "media_play") or tool == "play_media":
        if kind == "media_calm":
            return "Puściłem coś spokojniejszego."
        data = step.get("data") if isinstance(step.get("data"), dict) else {}
        q = str(data.get("query") or "")
        return f"Puszczam {q}." if q else "Odtwarzam."
    if kind == "open_result" or tool == "open_website":
        return "Otworzyłem wynik."
    if kind == "gaming_setup":
        return "Ustawiam tryb do grania."
    if kind == "replay_last":
        return "Powtórzyłem ostatnią czynność."
    return ""


def clarify_reply(message: str = "", *, candidates: Optional[List[Any]] = None) -> str:
    text = str(message or "").strip()
    if text:
        return strip_service_closers(text)
    rows = list(candidates or [])
    if len(rows) >= 2:
        return "Widzę dwa możliwe elementy. Górny czy dolny?"
    return "Nie wiem, który element masz na myśli."


def _single_success_ack(step: Dict[str, Any]) -> str:
    tool = str(step.get("tool") or step.get("action") or "").lower()
    data = step.get("data") if isinstance(step.get("data"), dict) else {}
    name = str(data.get("app") or data.get("name") or "")
    verified = bool(
        step.get("verified")
        or str(data.get("verification") or "").upper() in ("SUCCESS", "PASSED")
        or (
            isinstance(data.get("ha_verify"), dict)
            and str((data.get("ha_verify") or {}).get("verification") or "").upper()
            in ("SUCCESS", "PASSED", "ALREADY_SATISFIED")
        )
    )
    if tool in ("open_app", "open_service") and name:
        return f"{name} uruchomione." if name.lower() != "notatnik" else "Notatnik jest otwarty."
    if tool in ("open_app", "open_service"):
        return "Gotowe." if verified else "Wysłałem otwarcie, ale nie potwierdziłem okna."
    if tool in ("type_text", "select_all", "hotkey", "click_ui_element"):
        return "Gotowe." if verified else "Wysłałem polecenie, ale nie mogę potwierdzić."
    if tool in ("set_preference", "remember_fact"):
        val = str(data.get("value") or "").strip()
        return f"Zapamiętałem: wolisz {val}." if val else "Zapamiętałem."
    return "Zrobione." if verified else "Wysłałem polecenie, ale nie mogę potwierdzić."


def _step_label(step: Dict[str, Any]) -> str:
    tool = str(step.get("tool") or step.get("action") or "").lower()
    msg = str(step.get("message") or "")
    if tool in ("open_app", "open_service"):
        if "notatnik" in msg.lower():
            return "Notatnik otwarty"
        return "aplikacja otwarta"
    if tool == "type_text":
        return "tekst wpisany"
    if tool == "select_all":
        return "zaznaczony"
    if msg:
        return msg.rstrip(".")
    return ""


def looks_screen_question(text: str) -> bool:
    from core.perception.screen_truth import looks_screen_observation_query

    return looks_screen_observation_query(text)


def polish_spoken(text: str) -> str:
    from core.followup_policy import strip_generic_closers
    from core.speech_guard import guard_user_facing_speech, looks_like_internal_identifier

    raw = str(text or "").strip()
    if looks_like_internal_identifier(raw):
        return ""
    spoken = strip_fake_feelings(strip_service_closers(dedup_acks(raw)))
    spoken = _JARGON_TOKEN.sub("", spoken)
    spoken = re.sub(r"\s+", " ", spoken).strip()
    spoken = spoken.strip(" ,;:")
    spoken = strip_generic_closers(spoken)
    if _DEBUG_LEAK.search(spoken):
        spoken = "Nie udało mi się tego powiedzieć jasno."
    return guard_user_facing_speech(spoken)


def dedup_acks(text: str) -> str:
    """Collapse repeated tool acks like 'Gotowe. Gotowe.' into one utterance."""
    raw = str(text or "").strip()
    if not raw:
        return raw
    parts = re.split(r"(?<=[.!?])\s+", raw)
    out: List[str] = []
    prev_ack = False
    for part in parts:
        token = part.strip()
        if not token:
            continue
        is_ack = bool(_ACK_SENTENCE.match(token))
        same = bool(out) and out[-1].rstrip(".!?").lower() == token.rstrip(".!?").lower()
        if (is_ack and prev_ack) or same:
            continue
        out.append(token)
        prev_ack = is_ack
    return " ".join(out)


def _sanitize_fail(message: str, *, fallback: str = "Nie udało mi się tego zrobić.") -> str:
    text = str(message or "").strip()
    if not text or _DEBUG_LEAK.search(text):
        return fallback
    return text


def _action_key(step: Dict[str, Any]) -> str:
    return str(step.get("semantic_action") or step.get("tool") or step.get("action") or "").lower()


def _app_name(step: Dict[str, Any]) -> str:
    data = step.get("data") if isinstance(step.get("data"), dict) else {}
    name = str(
        data.get("app")
        or data.get("canonical_name")
        or data.get("name")
        or step.get("target")
        or ""
    ).strip()
    msg = str(step.get("message") or "")
    if not name and "notatnik" in msg.lower():
        return "Notatnik"
    if not name and "spotify" in msg.lower():
        return "Spotify"
    return name


def _open_phrase(step: Dict[str, Any]) -> str:
    name = _app_name(step)
    low = name.lower()
    if low in ("notatnik", "notepad"):
        return "Notatnik otwarty"
    if low == "spotify":
        return "Spotify otwarte"
    if name:
        return f"{name} otwarte"
    msg = str(step.get("message") or "")
    if "notatnik" in msg.lower():
        return "Notatnik otwarty"
    return "aplikacja otwarta"


def summarize_compound_steps(
    *,
    successful_steps: List[Dict[str, Any]],
    failed_steps: List[Dict[str, Any]],
) -> str:
    """One spoken sentence from semantic actions — never join ToolResult.message."""
    successes = list(successful_steps or [])
    failures = list(failed_steps or [])
    if not successes and not failures:
        return "Nie udało mi się tego zrobić."

    def _is_open(item):
        return _action_key(item) in ("open_app", "open_service")

    def _is_type(item):
        return _action_key(item) in ("type_text",)

    def _is_select(item):
        return _action_key(item) in ("select_all",)

    def _is_volume(item):
        return _action_key(item) in ("volume_up", "volume_down", "adjust_volume", "set_volume")

    def _is_talk(item):
        return _action_key(item) in ("talk", "knowledge", "creative_humor", "answer") or str(
            item.get("tool") or ""
        ).lower() == "talk"

    if successes and not failures:
        talks = [item for item in successes if _is_talk(item)]
        opens = [item for item in successes if _is_open(item)]
        types = [item for item in successes if _is_type(item)]
        selects = [item for item in successes if _is_select(item)]
        volumes = [item for item in successes if _is_volume(item)]
        if talks and volumes:
            joke = str(talks[0].get("message") or "").strip()
            vol_key = _action_key(volumes[0])
            vol_bit = "Głośność zwiększona." if "down" not in vol_key else "Głośność zmniejszona."
            if joke:
                return f"{joke} {vol_bit}"
            return vol_bit
        if talks and opens:
            joke = str(talks[0].get("message") or "").strip()
            phrase = _open_phrase(opens[0])
            if joke:
                return f"{phrase}. {joke}" if phrase else joke
        if talks and len(successes) == len(talks):
            return str(talks[0].get("message") or "Jasne.").strip()
        if opens and types and selects and len(successes) >= 3:
            phrase = _open_phrase(opens[0])
            if phrase and phrase != "aplikacja otwarta":
                return f"{phrase}, tekst wpisany i zaznaczony."
            return "Gotowe. Tekst jest wpisany i zaznaczony."
        if opens and types and not selects:
            return f"{_open_phrase(opens[0])}, tekst wpisany."
        if opens and selects and not types:
            return f"{_open_phrase(opens[0])}, tekst zaznaczony."
        if types and selects and not opens:
            return "Tekst jest wpisany i zaznaczony."
        if opens and volumes:
            vol = volumes[0]
            key = _action_key(vol)
            vol_bit = "głośność zwiększona" if "down" not in key and "decrease" not in str(vol.get("data") or {}).get("direction", "") else "głośność zmniejszona"
            if str((vol.get("data") or {}).get("direction") or "") == "decrease" or "down" in key:
                vol_bit = "głośność zmniejszona"
            elif "up" in key or str((vol.get("data") or {}).get("direction") or "") == "increase":
                vol_bit = "głośność zwiększona"
            return f"{_open_phrase(opens[0])}, {vol_bit}."
        if opens and len(successes) == 1:
            phrase = _open_phrase(opens[0])
            return phrase if phrase.endswith(".") else phrase + "."
        labels = [_semantic_label(item) for item in successes]
        labels = [item for item in labels if item]
        labels = _unique_keep_order(labels)
        if not labels:
            return "Gotowe."
        if len(labels) == 1:
            body = labels[0]
            return body[0].upper() + body[1:] + ("." if not body.endswith(".") else "")
        body = ", ".join(labels[:-1]) + " i " + labels[-1]
        return body[0].upper() + body[1:] + "."

    if successes and failures:
        ok_labels = [_semantic_label(item) for item in successes]
        ok_labels = [item for item in ok_labels if item]
        fail_item = failures[-1]
        fail_key = _action_key(fail_item)
        if _is_select(fail_item) or fail_key == "select_all":
            fail_bit = "nie udało się zaznaczyć tekstu"
        elif _is_type(fail_item):
            fail_bit = "nie udało mi się wpisać tekstu"
        else:
            fail_bit = _sanitize_fail(
                str(fail_item.get("message") or ""),
                fallback="reszta nie wyszła",
            )
            if fail_bit and fail_bit[0].isupper():
                fail_bit = fail_bit[0].lower() + fail_bit[1:]
        if ok_labels:
            lead = " i ".join(ok_labels) if len(ok_labels) <= 2 else ", ".join(ok_labels[:-1]) + " i " + ok_labels[-1]
            return f"{lead[0].upper() + lead[1:]}, ale {fail_bit}."
        return _sanitize_fail(str(fail_item.get("message") or ""))

    fail = failures[-1]
    key = _action_key(fail)
    if key in ("type_text",):
        name = _app_name(fail)
        if name.lower() in ("notatnik", "notepad"):
            return "Nie udało mi się wpisać tekstu do Notatnika."
        if name:
            return f"Nie udało mi się wpisać tekstu do {name}."
        return "Nie udało mi się wpisać tekstu."
    if key in ("select_all",):
        return "Nie udało mi się zaznaczyć tekstu."
    if key in ("open_app", "open_service"):
        name = _app_name(fail) or "aplikacji"
        return f"Nie udało mi się otworzyć {name}."
    return _sanitize_fail(str(fail.get("message") or ""))


def _unique_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _semantic_label(step: Dict[str, Any]) -> str:
    key = _action_key(step)
    if key in ("open_app", "open_service"):
        return _open_phrase(step)
    if key == "type_text":
        return "tekst wpisany"
    if key == "select_all":
        return "zaznaczony"
    if key in ("volume_up",):
        return "głośność zwiększona"
    if key in ("volume_down",):
        return "głośność zmniejszona"
    if key in ("talk", "knowledge", "creative_humor", "answer"):
        msg = str(step.get("message") or "").strip().rstrip(".")
        return msg
    if key in ("close_app", "close_service"):
        name = _app_name(step)
        return f"{name} zamknięte" if name else "zamknięte"
    msg = str(step.get("message") or "").strip().rstrip(".")
    if msg and not _ACK_SENTENCE.match(msg) and not _DEBUG_LEAK.search(msg):
        return msg
    return ""


def compound_from_executed_rows(executed_rows: List[Dict[str, Any]]) -> str:
    successes: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for row in list(executed_rows or []):
        obs = row.get("observation") if isinstance(row.get("observation"), dict) else {}
        status = str(obs.get("status") or row.get("status") or "").lower()
        row_status = str(row.get("status") or "").upper()
        success = bool(obs.get("success"))
        data = obs.get("data") if isinstance(obs.get("data"), dict) else {}
        uncertain = bool(
            data.get("side_effect_may_have_occurred")
            or status in ("partial", "unknown", "unknown_side_effect")
            or str(obs.get("error") or "") in ("unknown_side_effect",)
        )
        item = {
            "tool": row.get("tool") or obs.get("tool"),
            "semantic_action": row.get("semantic_action") or obs.get("action"),
            "message": obs.get("message"),
            "success": success,
            "status": status,
            "data": data,
            "target": row.get("target"),
        }
        if row_status == "SKIPPED":
            if success and not uncertain:
                successes.append(item)
            continue
        if success and not uncertain:
            successes.append(item)
        else:
            failures.append(item)
    return polish_spoken(summarize_compound_steps(successful_steps=successes, failed_steps=failures))
