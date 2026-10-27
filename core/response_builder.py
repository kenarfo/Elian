# ==========================
# ELIAN — response builder (Phase 8)
# ==========================
from __future__ import annotations

from typing import Iterable, List

from core.behavior import (
    SHORT_ACK_TOOLS,
    map_error_code,
    should_silent_success,
    voice_max_chars,
)
from core.tool_result import ToolResult, ToolStatus


def _clip(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    limit = voice_max_chars()
    for sep in (". ", "! ", "? "):
        if sep in text:
            first = text.split(sep, 1)[0].strip()
            if first:
                text = first + sep.strip()
                break
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def from_tool_result(result: ToolResult, *, allow_silent: bool = True) -> str:
    if result is None:
        return "Nie udało się."
    tool = result.tool_name or ""
    if result.status == ToolStatus.NEEDS_CONFIRMATION:
        return _clip(result.message or "Potwierdź, proszę.")
    if not result.success:
        code = ""
        if isinstance(result.data, dict):
            code = str(result.data.get("error_code") or result.data.get("availability") or "")
        code = code or result.error or ""
        if code:
            from core.behavior import USER_ERROR_MESSAGES

            key = str(code or "").strip().upper()
            if key in USER_ERROR_MESSAGES:
                return _clip(USER_ERROR_MESSAGES[key])
        try:
            from core.response_finalize import map_provider_error

            provider = map_provider_error(result.message or "", lang="pl")
            if provider:
                return _clip(provider)
        except Exception:
            pass
        return _clip(result.message or "Nie udało się.")
    if allow_silent and should_silent_success(tool, success=True):
        return ""
    msg = str(result.message or "").strip()
    vacuous = msg.lower().rstrip(".!") in ("ok", "gotowe", "zrobione", "")
    if tool in SHORT_ACK_TOOLS:
        if vacuous or not msg or len(msg) > 80:
            data = dict(result.data or {}) if isinstance(result.data, dict) else {}
            ha = data.get("ha_verify") if isinstance(data.get("ha_verify"), dict) else {}
            verified = str(
                data.get("verification")
                or data.get("ha_outcome")
                or ha.get("verification")
                or ""
            ).upper() in ("SUCCESS", "PASSED", "ALREADY_SATISFIED")
            return "Gotowe." if verified else "Wysłałem polecenie, ale nie mogę potwierdzić wyniku."
        return _clip(msg)
    if vacuous:
        return "Wysłałem polecenie, ale nie mogę potwierdzić wyniku."
    return _clip(msg)


def from_observations(observations: Iterable[dict], *, goal: str = "") -> str:
    obs = [dict(item) for item in (observations or []) if isinstance(item, dict)]
    if not obs:
        return "Nie udało mi się tego zrobić."

    successes = [item for item in obs if item.get("success")]
    failures = [item for item in obs if not item.get("success")]

    if successes and failures:
        try:
            from core.jarvis_response import summarize_compound_steps

            return _clip(
                summarize_compound_steps(successful_steps=successes, failed_steps=failures)
            )
        except Exception:
            ok_msg = str(successes[-1].get("message") or "część się udała").rstrip(".")
            fail_msg = str(failures[-1].get("message") or "reszta nie wyszła")
            if fail_msg:
                fail_msg = fail_msg[0].lower() + fail_msg[1:]
            return _clip(f"{ok_msg}, ale {fail_msg}")

    if successes and not failures:
        if len(successes) == 1:
            tool = successes[0].get("tool") or ""
            if should_silent_success(tool, success=True):
                return ""
            return _clip(successes[0].get("message") or "Wysłałem polecenie, ale nie mam potwierdzenia wyniku.")
        try:
            from core.jarvis_response import ResponseContext, execution_reply

            return _clip(
                execution_reply(
                    ResponseContext(
                        successful_steps=successes,
                        failed_steps=[],
                        observations=obs,
                    )
                )
            )
        except Exception:
            return "Wykonałem te kroki."

    last_fail = failures[-1]
    try:
        from core.jarvis_response import summarize_compound_steps

        spoken = summarize_compound_steps(successful_steps=[], failed_steps=failures)
        if spoken:
            return _clip(spoken)
    except Exception:
        pass
    code = ""
    data = last_fail.get("data") or {}
    if isinstance(data, dict):
        code = str(data.get("error_code") or data.get("availability") or "")
    code = code or last_fail.get("error") or ""
    raw_msg = str(last_fail.get("message") or "")
    if code:
        from core.behavior import USER_ERROR_MESSAGES

        key = str(code or "").strip().upper()
        if key in USER_ERROR_MESSAGES:
            return _clip(USER_ERROR_MESSAGES[key])
    try:
        from core.response_finalize import map_provider_error

        provider = map_provider_error(raw_msg, lang="pl")
        if provider:
            return _clip(provider)
    except Exception:
        pass
    return _clip(raw_msg or "Nie udało się.")


def never_fake_success(message: str, observations: List[dict]) -> str:
    text = str(message or "").strip()
    obs = list(observations or [])
    any_success = any(item.get("success") for item in obs)
    any_failure = any(not item.get("success") for item in obs)

    from core.action_claim import guard_action_claim

    if any_failure and not any_success:
        rebuilt = from_observations(obs)
        guarded = guard_action_claim(rebuilt, obs)
        return guarded.text

    guarded = guard_action_claim(text, obs)
    if guarded.blocked:
        if obs:
            rebuilt = from_observations(obs)
            second = guard_action_claim(rebuilt, obs)
            return second.text
        return guarded.text

    claims_ok = bool(guarded.claim_kind)
    if claims_ok and any_success and any_failure:
        if any(
            token in text.lower()
            for token in ("ale", "web", "wersj", "nie udało", "nie udalo", "niedostęp")
        ):
            return text
        return from_observations(obs)
    return text


def _web_success(observations: List[dict]) -> bool:
    for item in observations or []:
        if not item.get("success"):
            continue
        tool = str(item.get("tool") or "").lower()
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        if tool == "web_search" and (data.get("results") or data.get("result_count")):
            return True
        if tool == "fetch_web_page" and data.get("text"):
            return True
    return False


def never_fake_web_claim(message: str, observations: List[dict] | None = None) -> str:
    """Strip pretended internet search when no web_search observation exists."""
    text = str(message or "").strip()
    if not text:
        return text
    lowered = text.lower()
    claims_web = any(
        token in lowered
        for token in (
            "sprawdziłem w internecie",
            "sprawdzilem w internecie",
            "sprawdziłam w internecie",
            "sprawdzilam w internecie",
            "wyszukałem",
            "wyszukalem",
            "wyszukałam",
            "wyszukalam",
            "według internetu",
            "wedlug internetu",
            "znalazłem w sieci",
            "znalazlem w sieci",
            "przeszukałem sieć",
            "przeszukalem siec",
        )
    )
    if not claims_web:
        return text
    if _web_success(list(observations or [])):
        return text
    return (
        "Nie mam teraz dostępu do świeżych wyników z sieci. "
        "Mogę odpowiedzieć na podstawie tego, co już wiem."
    )
