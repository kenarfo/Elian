# ==========================
# ELIAN — Soft goals (state desires → structured slots)
# ==========================
"""Map natural state/wish language onto UserGoal slots.

Feature extractors only — not a FastPath phrase dump / second IntentRouter.
Reuses existing lighting/volume extractors where possible.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from core.intent_family import (
    IMPLICIT_LIGHT_BRIGHTER,
    IMPLICIT_LIGHT_DARK,
    looks_insufficient_light,
)

# Soft volume as state description (not only imperative ciszej/głośniej).
_TOO_LOUD = re.compile(
    r"(?:"
    r"\bza\s+g[lł]o[sś]no\b|"
    r"\bjest\s+(?:tu\s+)?za\s+g[lł]o[sś]no\b|"
    r"\btroch[eę]\s+za\s+g[lł]o[sś]no\b|"
    r"\bza\s+du[zż]o\s+g[lł]o[sś]u\b|"
    r"\bog[lł]usz|"
    r"\btoo\s+loud\b|"
    r"\bit'?s\s+(?:too\s+)?loud\b"
    r")",
    re.I,
)
_TOO_QUIET = re.compile(
    r"(?:"
    r"\bza\s+cicho\b|"
    r"\bjest\s+(?:tu\s+)?za\s+cicho\b|"
    r"\btroch[eę]\s+za\s+cicho\b|"
    r"\bs[lł]abo\s+s[lł]ycha[cć]\b|"
    r"\btoo\s+quiet\b|"
    r"\bcan'?t\s+hear\b|"
    r"\bit'?s\s+(?:too\s+)?quiet\b"
    r")",
    re.I,
)
_CALMER_MEDIA = re.compile(
    r"(?:"
    r"\bco[sś]\s+spokojniejsz|"
    r"\bspokojniejsz\w*\b|"
    r"\bco[sś]\s+chill|"
    r"\bco[sś]\s+relaks|"
    r"\bco[sś]\s+[lł]agodniej|"
    r"\bzmie[nń]\s+na\s+co[sś]\s+spokoj"
    r")",
    re.I,
)
_REPLAY_LAST = re.compile(
    r"(?:"
    r"\bjak\s+(?:wcze[sś]niej|ostatnio|poprzednio)\b|"
    r"\bzr[oó]b\s+tak\s+jak\s+(?:wcze[sś]niej|ostatnio|poprzednio)\b|"
    r"\bpowt[oó]rz\s+(?:to|ostatni[eą]|czynno[sś][cć])\b|"
    r"\bjeszcze\s+raz\b|"
    r"\bspr[oó]buj(?:\s+ponownie|\s+jeszcze\s+raz)\b|"
    r"\bpon[oó]w\b|"
    r"\bno\s+to\s+dawaj\b|"
    r"^\s*zr[oó]b\s*(?:to)?\s*[.!?…]*$"
    r")",
    re.I,
)
_OPEN_FIRST = re.compile(
    r"(?:"
    r"\botw[oó]rz\s+(?:ten\s+)?pierwsz|"
    r"\bno\s+to\s+otw[oó]rz\s+(?:ten\s+)?pierwsz|"
    r"\bwe[zź]\s+(?:ten\s+)?pierwsz|"
    r"\bpierwszy\s+wynik|"
    r"\botw[oó]rz\s+pierwszy\s+wynik|"
    r"\botw[oó]rz\s+(?:ten\s+)?(?:sensown\w*|dobr\w*|najlepsz\w*|jak[iı]\s+|g[oó]rn\w*)\s+"
    r"(?:wynik|link)|"
    r"\bwe[zź]\s+(?:ten\s+)?(?:sensown\w*|dobr\w*|g[oó]rn\w*)\s+(?:wynik|link)|"
    r"\botw[oó]rz\s+(?:ten\s+)?wynik\b"
    r")",
    re.I,
)
# Bare / explicit ordinal follow-ups after web search ("a drugi?", "nie, niższy").
_OPEN_ORDINAL = re.compile(
    r"(?:"
    r"\botw[oó]rz\s+(?:ten\s+)?(?:drug|trzec|ostatni|ni[zż]sz|doln)|"
    r"\bno\s+to\s+(?:otw[oó]rz\s+)?(?:ten\s+)?(?:drug|trzec|ostatni|ni[zż]sz|doln)|"
    r"\bwe[zź]\s+(?:ten\s+)?(?:drug|trzec|ostatni|ni[zż]sz|doln)|"
    r"\b(?:a\s+)?(?:to\s+)?(?:ten\s+)?(?:drugi|trzeci|ostatni|ni[zż]szy|dolny)(?:\s+(?:wynik|link))?\b|"
    r"\b(?:nie[,!]?\s+)?(?:ten\s+)?(?:ni[zż]szy|dolny)(?:\s+(?:wynik|link))?\b|"
    r"\bdrugi\s+wynik|\btrzeci\s+wynik|\bostatni\s+wynik"
    r")",
    re.I,
)
# Summarize prior web/context — not a new search ("streść to krótko").
_SUMMARIZE_CONTEXT = re.compile(
    r"(?:"
    r"^\s*(?:stre[sś][cć]\w*|podsumuj|skr[oó][cć])"
    r"(?:\s+(?:mi|prosz[eę]))*"
    r"(?:\s+(?:to|te\s+wyniki|wyniki|to\s+kr[oó]tko|kr[oó]tko|"
    r"jednym\s+zdaniem|w\s+skr[oó]cie))*"
    r"\s*[.!?…]*$|"
    r"\b(?:stre[sś][cć]\w*|podsumuj|skr[oó][cć])\s+"
    r"(?:to|te\s+wyniki|wyniki|to\s+kr[oó]tko)\b"
    r")",
    re.I,
)
_SUMMARIZE_EXCLUDE = re.compile(
    r"\b(?:sytuacj|dom\b|dzie[nń]|poranek|briefing)\w*",
    re.I,
)


def _web_result_ordinal_index(raw: str) -> int | None:
    text = str(raw or "").lower()
    if re.search(r"pierwsz|g[oó]rn", text):
        return 0
    if re.search(r"drug|ni[zż]sz|doln", text):
        return 1
    if re.search(r"trzec", text):
        return 2
    if re.search(r"ostatni", text):
        return -1
    return None


def _soft_open_web_result(raw: str, ctx: Any, *, index: int, source: str) -> Optional[Dict[str, Any]]:
    results = list(getattr(ctx, "last_web_results", None) or []) if ctx is not None else []
    labels = {0: "pierwszy", 1: "drugi", 2: "trzeci", -1: "ostatni"}
    label = labels.get(index, f"nr {index + 1}")
    if not results:
        return {
            "kind": "unclear",
            "needs_clarification": True,
            "missing": ["web_result"],
            "objective": f"Otwórz {label} wynik wyszukiwania",
            "confidence": 0.7,
            "source": f"{source}_missing",
        }
    pick_i = index if index >= 0 else len(results) - 1
    if pick_i < 0 or pick_i >= len(results):
        return {
            "kind": "unclear",
            "needs_clarification": True,
            "missing": ["web_result"],
            "objective": f"Nie mam {label} wyniku do otwarcia",
            "confidence": 0.72,
            "source": f"{source}_oob",
        }
    item = results[pick_i] if isinstance(results[pick_i], dict) else {}
    url = str(item.get("url") or item.get("link") or "").strip()
    title = str(item.get("title") or "").strip()
    if not url:
        return {
            "kind": "unclear",
            "needs_clarification": True,
            "missing": ["web_result"],
            "objective": f"Otwórz {label} wynik wyszukiwania",
            "confidence": 0.7,
            "source": f"{source}_missing",
        }
    return {
        "kind": "open_result",
        "intent_family": "WEB",
        "objective": f"Otwórz {label} wynik: {title or url}",
        "target": url,
        "continuation": True,
        "confidence": 0.92,
        "source": source,
        "slots": {"url": url, "title": title, "index": pick_i},
        "expected_result": {"kind": "open_website", "url": url},
    }


def _soft_summarize_web(raw: str, ctx: Any, *, source: str) -> Optional[Dict[str, Any]]:
    """Reuse last web evidence / assistant answer — never re-search 'streść to'."""
    if not _SUMMARIZE_CONTEXT.search(str(raw or "")):
        return None
    if _SUMMARIZE_EXCLUDE.search(str(raw or "")):
        return None
    results = list(getattr(ctx, "last_web_results", None) or []) if ctx is not None else []
    query = str(getattr(ctx, "last_web_query", "") or "") if ctx is not None else ""
    assistant = str(getattr(ctx, "last_assistant", "") or "") if ctx is not None else ""
    if not results and not assistant.strip():
        return {
            "kind": "unclear",
            "needs_clarification": True,
            "missing": ["web_result"],
            "objective": "Nie mam jeszcze treści do streszczenia",
            "confidence": 0.72,
            "source": f"{source}_missing",
        }
    return {
        "kind": "summarize_web",
        "intent_family": "WEB",
        "objective": "Streść ostatnie wyniki wyszukiwania",
        "continuation": True,
        "confidence": 0.93,
        "source": source,
        "slots": {
            "query": query,
            "results": results,
            "last_assistant": assistant,
        },
        "expected_result": {"kind": "summarize_web"},
    }


_SEARCH_REFERENT = re.compile(
    r"\b(?:wyszukaj|poszukaj|szukaj|znajd[zź])\b.{0,48}\bo\s+tym\b",
    re.I,
)
_SEARCH_ABOUT = re.compile(
    r"\b(?:wyszukaj|poszukaj|szukaj|znajd[zź])\b(?:\s+(?:mi|prosz[eę]|w\s+internecie))*\s+(?:co[sś]\s+)?o\s+(.+)$",
    re.I,
)
_SEARCH_BARE = re.compile(
    r"^\s*(?:wyszukaj|poszukaj|szukaj|znajd[zź])\s+(?:mi\s+)?(.+?)\s*[.!?…]*$",
    re.I,
)
# Factual definition → web grounding (no Ollama required).
# Exclude bare "jak działa …" (chat/knowledge in behavioral corpus).
_DEFINE_TOPIC = re.compile(
    r"^\s*(?:(?:powiedz|wyja[sś]nij|wyt[lł]umacz)\s+(?:mi\s+)?)?"
    r"(?:czym\s+jest|co\s+to\s+(?:jest|za)|co\s+oznacza)\s+(.+?)\s*[.!?…]*$",
    re.I,
)
# Colloquial web ask — not a whitelist of novel topics.
_NETWORK_SAYS = re.compile(
    r"(?:"
    r"\bco\s+(?:sie[cć]|w\s+sieci|internet|google)\s+(?:m[oó]wi|pisze)\s+o\s+(.+?)\s*[.!?…]*$|"
    r"\bco\s+(?:m[oó]wi|pisze)\s+(?:sie[cć]|w\s+sieci|internet|google)\s+o\s+(.+?)\s*[.!?…]*$|"
    r"\bwygoogluj(?:\s+mi)?\s+(.+?)\s*[.!?…]*$"
    r")",
    re.I,
)
_WHY_NOT_DONE = re.compile(
    r"(?:"
    r"\bczemu\s+nie\s+zrobi[lł]|"
    r"\bdlaczego\s+nie\s+zrobi[lł]|"
    r"\bjeszcze\s+tego\s+nie\s+zrobi[lł]|"
    r"\bnie\s+zrobi[lł]e[sś]\s+(?:tego|tego\s+jeszcze)?"
    r")",
    re.I,
)
_GAMING_SETUP = re.compile(
    r"(?:"
    r"\b(?:ogarnij|przygotuj|ustaw)\b.{0,48}\b(?:komputer|pc|pecet)\b.{0,24}\b(?:grania|gaming|gry)\b|"
    r"\b(?:komputer|pc)\b.{0,24}\bdo\s+grania\b|"
    r"\btryb\s+(?:do\s+)?grania\b|"
    r"\bprocedur\w*\s+gaming\b|"
    r"\bdo\s+grania\s+(?:prosz[eę]|dzi[sś])\b"
    r")",
    re.I,
)
_MEDIA_NOW = re.compile(
    r"(?:"
    r"^\s*co\s+(?:teraz\s+)?leci\s*\??\s*$|"
    r"^\s*co\s+gra\s*\??\s*$|"
    r"^\s*co\s+jest\s+odtwarzane\s*\??\s*$"
    r")",
    re.I,
)
_VAGUE_PLAY = re.compile(
    r"^\s*(?:odpal|pu[sś][cć]|odtw[oó]rz)\s+(?:mi\s+)?co[sś](?:\s+\w+)?\s*[.!?…]*$",
    re.I,
)
# Named media launch — content after verb, not a fixed artist list.
_NAMED_PLAY = re.compile(
    r"^\s*(?:odpal|pu[sś][cć]|odtw[oó]rz)\s+(?:mi\s+)?(?!co[sś]\b)(.+?)\s*[.!?…]*$",
    re.I,
)
_CANCEL_SOFT = re.compile(
    r"(?:"
    r"^\s*(?:dobra|okej|ok|no)\s*,?\s*(?:niewa[zż]ne|anuluj|odpu[sś][cć]|zostaw)\s*[.!?…]*$|"
    r"^\s*(?:niewa[zż]ne|anuluj(?:\s+to)?|to\s+niewa[zż]ne)\s*[.!?…]*$"
    r")",
    re.I,
)
_ACT_ON_CONTEXT = re.compile(
    r"(?:"
    r"\bwe[zź]\s+(?:co[sś]\s+)?(?:z\s+tym\s+)?zr[oó]b\b|"
    r"\bzr[oó]b\s+(?:co[sś]\s+)?(?:z\s+tym|z\s+t[aą])\b|"
    r"\bco[sś]\s+z\s+tym\s+(?:zr[oó]b|zrobi[cć])\b|"
    r"\bogarnij\s+(?:to|ten\s+temat)\b"
    r")",
    re.I,
)
_LIGHT_MENTION = re.compile(r"\b(?:[sś]wiat[lł]|lamp)\w*\b", re.I)
_LIGHT_OFF = re.compile(
    r"(?:"
    r"\b(?:z?ga[sś]|wyga[sś])\w*\b|"
    r"\bodetnij\s+(?:pr[aą]d|zasilanie)\b|"
    r"\b(?:ściemn|sciemn|przyciemn)\w*\b.{0,16}\b(?:totaln|zupełn|calkowit|całkowit|do\s+zera|na\s+zero)\w*\b|"
    r"\b(?:totaln|zupełn|calkowit|całkowit)\w*.{0,16}\b(?:ściemn|sciemn|przyciemn)\w*\b|"
    r"\bwy[lł][aą]cz\w*\b.{0,24}\b(?:j[aą]|go|to|t[eę]|ten|t[aą]|[sś]wiat[lł]\w*|lamp\w*)\b|"
    r"\bwy[lł][aą]cz\w*\b\s*(?:j[aą]|go|to)\b"
    r")",
    re.I,
)
_SOFT_VOLUME_DOWN = re.compile(
    r"(?:"
    r"\btroch[eę]\s+ciszej\b|"
    r"\bciszej\b|"
    r"\btroch[eę]\s+t[iy]szej\b|"  # STT typo: tiszej ≈ ciszej
    r"\bt[iy]szej\b|"
    r"\b[sś]cisz\w*\b|"
    r"\bprzycisz\w*\b|"
    r"\bza\s+g[lł]o[sś]no\b|"
    r"\bzejd[zź]\s+z\s+(?:vol|g[lł]o[sś]\w*)\b|"
    r"\b(?:ściągnij|sciagnij|zmniejsz)\s+(?:vol|g[lł]o[sś]\w*)\b|"
    r"\bvol\s*(?:down|w\s+d[oó][lł])\b|"
    r"\b(?:na\s+)?vol\b.{0,12}\b(?:ciszej|ni[zż]ej|mniej)\b|"
    r"\bvolume\s+down\b|"
    r"\bturn\s+(?:the\s+)?volume\s+down\b|"
    r"\bturn\s+down\s+(?:the\s+)?volume\b|"
    r"\bquieter\b|\blower\s+(?:the\s+)?volume\b"
    r")",
    re.I,
)
_SOFT_VOLUME_UP = re.compile(
    r"(?:"
    r"\btroch[eę]\s+g[lł]o[sś]niej\b|"
    r"\bg[lł]o[sś]niej\b|"
    r"\bpodg[lł]o[sś]n\w*\b|"
    r"\bzwi[eę]ksz\s+(?:vol|g[lł]o[sś]\w*)\b|"
    r"\bpodnie[sś]\s+(?:vol|g[lł]o[sś]\w*)\b|"
    r"\bpodkr[eę][cć]\s+(?:vol|g[lł]o[sś]\w*|decybel\w*)\b|"
    r"\bwi[eę]cej\s+decybel\w*\b|"
    r"\bvol\s*(?:up|w\s+g[oó]r[eę])\b|"
    r"\b(?:na\s+)?vol\b.{0,12}\b(?:g[lł]o[sś]niej|wy[zż]ej|wi[eę]cej)\b|"
    r"\bvolume\s+up\b|"
    r"\bturn\s+(?:the\s+)?volume\s+up\b|"
    r"\bturn\s+up\s+(?:the\s+)?volume\b|"
    r"\blouder\b|\braise\s+(?:the\s+)?volume\b"
    r")",
    re.I,
)
_NOT_THAT_MUCH = re.compile(
    r"(?:"
    r"\bnie\s+a[zż]\s+tak\b|"
    r"\btroch[eę]\s+mniej\b|"
    r"\bza\s+mocno\b"
    r")",
    re.I,
)
# Absolute comfort complaint — not a relative "dial back" cue.
_TOO_BRIGHT = re.compile(r"\b(?:za\s+jasno|jest\s+za\s+jasno|troch[eę]\s+za\s+jasno)\b", re.I)


def _looks_replay_with_new_question(text: str) -> bool:
    """True when 'jeszcze raz' prefixes a new contentful question (not bare replay)."""
    raw = str(text or "").strip()
    if not re.search(r"\bjeszcze\s+raz\b", raw, re.I):
        return False
    if not re.search(r"\bjeszcze\s+raz\b[\s,.:;…-]+(?=\S).{8,}", raw, re.I):
        return False
    if re.search(
        r"\b(?:kto|co|gdzie|kiedy|dlaczego|czemu|jak|czy|ile|który|ktory)\b",
        raw,
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:premier|pogod|wynik|wiadomo[sś][cć]|szukaj|wyszukaj)\w*\b",
        raw,
        re.I,
    ):
        return True
    return False


def _bind_light_entity(
    payload: Dict[str, Any],
    *,
    text: str,
    ctx: Any,
    prefer_active: bool = True,
) -> Dict[str, Any]:
    """Attach unique light entity via active → resolve_light_target."""
    target = ""
    reason = ""
    if ctx is not None:
        active = getattr(ctx, "active_entities", None) or {}
        if str(active.get("domain") or "") == "light":
            target = str(active.get("entity_id") or active.get("device_id") or "")
            if target:
                reason = "active"
        if not target:
            prev = dict(getattr(ctx, "previous_goal", None) or {})
            slots = dict(prev.get("slots") or {})
            target = str(slots.get("entity_id") or slots.get("target") or prev.get("target") or "")
            if target:
                reason = "previous_slot"
    # Pronoun-only acts without a bound referent must ASK — never invent default_config.
    pronoun_only = False
    try:
        from core.device_pronoun import has_device_pronoun

        pronoun_only = bool(has_device_pronoun(text)) and not target
    except Exception:
        pronoun_only = False
    if pronoun_only:
        payload.setdefault("slots", {})["resolve_reason"] = "ambiguous"
        payload["needs_clarification"] = True
        return payload
    if not target or not prefer_active:
        try:
            from core.device_catalog import resolve_light_target

            eid, why = resolve_light_target(
                str(text or ""),
                memory=getattr(ctx, "memory", None) if ctx is not None else None,
                prefer_active=prefer_active,
            )
            if eid and why != "ambiguous":
                target = eid
                reason = why
            elif why == "ambiguous":
                payload.setdefault("slots", {})["resolve_reason"] = "ambiguous"
                payload["needs_clarification"] = True
                return payload
        except Exception:
            pass
    if target:
        payload["target"] = target
        payload.setdefault("slots", {})
        payload["slots"]["target"] = target
        payload["slots"]["entity_id"] = target
        payload["slots"]["resolved_context"] = True
        payload["slots"]["resolve_reason"] = reason or "bound"
    elif not payload.get("slots", {}).get("resolve_reason"):
        payload.setdefault("slots", {})["resolve_reason"] = "none"
        payload["needs_clarification"] = True
    return payload


def detect_soft_goal(
    text: str,
    *,
    goal_context: Any = None,
    envelope: Any = None,
) -> Optional[Dict[str, Any]]:
    """Return soft goal dict or None. Order: continuation cues → state desires."""
    raw = str(text or "").strip()
    if not raw:
        return None
    # Quoted / meta / roleplay wrappers must never soft-mutate (POISON_ACT).
    try:
        from core.semantic_executive import looks_quoted_command

        if looks_quoted_command(raw) or bool(getattr(envelope, "quoted", False)):
            return None
    except Exception:
        pass
    lowered = raw.lower()
    insufficient_light = looks_insufficient_light(lowered)
    ctx = goal_context
    env_intent = str(getattr(envelope, "intent", "") or "") if envelope is not None else ""
    env_utt = str(getattr(envelope, "utterance_class", "") or "") if envelope is not None else ""
    env_mode = str(getattr(envelope, "mode", "") or "") if envelope is not None else ""
    env_needs = bool(getattr(envelope, "needs_tools", False)) if envelope is not None else False
    talk_locked = (env_mode in ("TALK", "ANSWER", "WAIT") and not env_needs) or env_intent in (
        "action_discussion",
        "creative_humor",
        "social_smalltalk",
    )

    if _WHY_NOT_DONE.search(raw):
        prev = dict(getattr(ctx, "previous_goal", None) or {}) if ctx is not None else {}
        if str(prev.get("status") or "").upper() == "CANCELLED":
            return None
        if str(prev.get("soft_kind") or "") == "cancel_goal":
            return None
        pending = getattr(ctx, "pending_executable", None) or {}
        unresolved = getattr(ctx, "unresolved_task", None) or {}
        return {
            "kind": "explain_failure",
            "intent_family": "CONVERSATION_CONTROL",
            "objective": "Wyjaśnij, czemu poprzednia czynność nie doszła do skutku.",
            "continuation": True,
            "confidence": 0.9,
            "source": "soft_why_not",
            "slots": {
                "pending": dict(pending),
                "unresolved": dict(unresolved),
                "previous_result": dict(getattr(ctx, "previous_result", None) or {}),
            },
            "expected_result": {"kind": "explain_failure"},
        }

    try:
        from core.pending_goal import looks_alternate_utterance

        if looks_alternate_utterance(raw):
            prev_res = (
                dict(getattr(ctx, "previous_result", None) or {}) if ctx is not None else {}
            )
            failed = prev_res and not bool(prev_res.get("success", True))
            pending = (
                dict(getattr(ctx, "pending_executable", None) or {}) if ctx is not None else {}
            )
            if failed or pending:
                return {
                    "kind": "ask_goal",
                    "intent_family": "CONVERSATION_CONTROL",
                    "objective": "Zapytaj, jak zrobić inaczej",
                    "continuation": True,
                    "confidence": 0.88,
                    "source": "soft_ask_alternate",
                    "slots": {"alternate_after_fail": True},
                    "expected_result": {"kind": "ask"},
                }
    except Exception:
        pass

    try:
        from core.utterance_class import _EXPECTANT_CONTINUE

        if _EXPECTANT_CONTINUE.match(raw) or env_intent == "ask_after_cancel":
            pending = dict(getattr(ctx, "pending_executable", None) or {}) if ctx is not None else {}
            prev = dict(getattr(ctx, "previous_goal", None) or {}) if ctx is not None else {}
            if (
                env_intent == "ask_after_cancel"
                or str(prev.get("status") or "").upper() == "CANCELLED"
                or str(prev.get("soft_kind") or "") == "cancel_goal"
            ):
                return {
                    "kind": "ask_goal",
                    "intent_family": "CONVERSATION_CONTROL",
                    "objective": "Zapytaj, co mam zrobić",
                    "continuation": False,
                    "confidence": 0.9,
                    "source": "soft_ask_after_cancel",
                    "expected_result": {"kind": "ask"},
                }
            objective = str(
                (pending.get("goal") if pending else None)
                or prev.get("objective")
                or ""
            ).strip()
            if not objective:
                # P0#1 rule E: after cancel / empty slate, ASK — do not invent resume.
                return {
                    "kind": "ask_goal",
                    "intent_family": "CONVERSATION_CONTROL",
                    "objective": "Zapytaj, co mam zrobić",
                    "continuation": False,
                    "confidence": 0.88,
                    "source": "soft_ask_empty",
                    "expected_result": {"kind": "ask"},
                }
            return {
                "kind": "resume_previous",
                "intent_family": "CONVERSATION_CONTROL",
                "objective": objective,
                "continuation": True,
                "confidence": 0.84,
                "source": "soft_expectant_continue",
                "slots": {"pending": pending, "previous_goal": prev},
                "expected_result": {"kind": "resume_previous"},
            }
    except Exception:
        pass

    if _CANCEL_SOFT.search(raw):
        return {
            "kind": "cancel_goal",
            "intent_family": "CONVERSATION_CONTROL",
            "objective": "Anuluj bieżące zadanie",
            "continuation": True,
            "confidence": 0.92,
            "source": "soft_cancel",
            "expected_result": {"kind": "cancel"},
        }

    # Discussion / hypothetical / humor: no mutating soft shortcuts.
    if talk_locked:
        # Live P3: "a drugi?" after web search is REFERENCE continuation, not chat.
        # Semantic often marks TALK — still allow open_result (or honest missing).
        if _OPEN_FIRST.search(raw) or _OPEN_ORDINAL.search(raw):
            idx = 0 if _OPEN_FIRST.search(raw) else _web_result_ordinal_index(raw)
            if idx is not None:
                return _soft_open_web_result(
                    raw,
                    ctx,
                    index=idx,
                    source="soft_open_ordinal_talk_cont",
                )
        # "streść to" under ANSWER/TALK must reuse evidence, not re-search.
        summarized = _soft_summarize_web(raw, ctx, source="soft_summarize_talk_cont")
        if summarized is not None:
            return summarized
        # Definitional fact questions: unlock web even when semantic said ANSWER.
        defined = _DEFINE_TOPIC.match(raw)
        if defined:
            topic = str(defined.group(1) or "").strip().rstrip(".!?…")
            if topic and len(topic) >= 2:
                try:
                    from core.web_grounding import clean_web_query

                    topic = clean_web_query(topic) or topic
                except Exception:
                    pass
                return {
                    "kind": "web_search",
                    "intent_family": "WEB",
                    "objective": f"Wyjaśnij: {topic}",
                    "confidence": 0.9,
                    "source": "soft_define_talk",
                    "slots": {"query": topic},
                    "expected_result": {"kind": "web_search", "query": topic},
                }
        # After media play, "coś łagodniejszego" often arrives as TALK — keep soft mood.
        # Bare mood request (no prior media) is still a clear play intent.
        if _CALMER_MEDIA.search(raw):
            return {
                "kind": "media_calm",
                "intent_family": "MEDIA",
                "objective": "Puść coś spokojniejszego",
                "confidence": 0.86,
                "source": "soft_media_mood_talk",
                "mood": "calm",
                "slots": {"media_query": "spokojna muzyka", "mood": "calm"},
                "expected_result": {"kind": "media_play", "mood": "calm"},
            }
        if _SOFT_VOLUME_DOWN.search(raw) and not _TOO_QUIET.search(raw) and not _LIGHT_MENTION.search(raw):
            if not IMPLICIT_LIGHT_BRIGHTER.search(lowered) and not insufficient_light:
                return {
                    "kind": "volume_down",
                    "intent_family": "SYSTEM",
                    "objective": "Ścisz głośność",
                    "confidence": 0.9,
                    "source": "soft_volume_phrase_talk",
                    "slots": {
                        "volume_direction": "decrease",
                        "semantic_action": "adjust_volume",
                        "clear_light_context": True,
                    },
                    "expected_result": {"kind": "volume_down"},
                }
        if (
            _SOFT_VOLUME_UP.search(raw)
            and not _TOO_LOUD.search(raw)
            and not _SOFT_VOLUME_DOWN.search(raw)
            and not _LIGHT_MENTION.search(raw)
        ):
            if not IMPLICIT_LIGHT_BRIGHTER.search(lowered) and not insufficient_light:
                return {
                    "kind": "volume_up",
                    "intent_family": "SYSTEM",
                    "objective": "Podgłośnij",
                    "confidence": 0.9,
                    "source": "soft_volume_phrase_talk",
                    "slots": {
                        "volume_direction": "increase",
                        "semantic_action": "adjust_volume",
                        "clear_light_context": True,
                    },
                    "expected_result": {"kind": "volume_up"},
                }
        if insufficient_light and not IMPLICIT_LIGHT_BRIGHTER.search(lowered):
            payload = {
                "kind": "brightness_up",
                "intent_family": "SMART_HOME",
                "objective": "Rozjaśnij — jest ciemno",
                "confidence": 0.88,
                "source": "soft_light_talk",
                "slots": {
                    "implicit_light": True,
                    "desired": "brighter",
                    "domain": "light",
                },
                "expected_result": {"kind": "brightness_up"},
            }
            return _bind_light_entity(payload, text=raw, ctx=ctx, prefer_active=True)
        # Colloquial "gaś to" under TALK/REFERENCE — still extinguish active light.
        if _LIGHT_OFF.search(raw):
            prev = dict(getattr(ctx, "previous_goal", None) or {}) if ctx is not None else {}
            soft_prev = str(prev.get("soft_kind") or "")
            active_domain = str(getattr(ctx, "active_domain", "") or "").lower()
            prev_tool = str(getattr(ctx, "previous_tool", "") or "") if ctx is not None else ""
            lightish = (
                active_domain in ("light", "smart_home")
                or soft_prev.startswith(("light_", "brightness_"))
                or prev_tool
                in ("turn_on_device", "turn_off_device", "set_device_value")
                or str(prev.get("intent_family") or "") == "SMART_HOME"
            )
            if lightish and not re.search(
                r"\b(?:spotify|chrome|discord|aplikacj|komputer)\w*\b", raw, re.I
            ):
                payload = {
                    "kind": "light_off",
                    "intent_family": "SMART_HOME",
                    "objective": "Zgaś światło",
                    "confidence": 0.9,
                    "source": "soft_light_off_talk",
                    "slots": {
                        "implicit_light": True,
                        "desired": "off",
                        "domain": "light",
                        "action": "turn_off",
                    },
                    "expected_result": {"kind": "light_off"},
                }
                return _bind_light_entity(payload, text=raw, ctx=ctx, prefer_active=True)
        # Relative "nie aż tak" under TALK after a completed soft nudge.
        if _NOT_THAT_MUCH.search(raw) and ctx is not None:
            prev = dict(getattr(ctx, "previous_goal", None) or {})
            soft_prev = str(prev.get("soft_kind") or "")
            if soft_prev == "volume_up":
                return {
                    "kind": "volume_down",
                    "intent_family": "SYSTEM",
                    "objective": "Ścisz — nie aż tak",
                    "continuation": True,
                    "confidence": 0.9,
                    "source": "soft_volume_relative_talk",
                    "slots": {
                        "volume_direction": "decrease",
                        "semantic_action": "adjust_volume",
                        "clear_light_context": True,
                    },
                    "expected_result": {"kind": "volume_down"},
                }
            if soft_prev == "volume_down":
                return {
                    "kind": "volume_up",
                    "intent_family": "SYSTEM",
                    "objective": "Podgłośnij — nie aż tak",
                    "continuation": True,
                    "confidence": 0.9,
                    "source": "soft_volume_relative_talk",
                    "slots": {
                        "volume_direction": "increase",
                        "semantic_action": "adjust_volume",
                        "clear_light_context": True,
                    },
                    "expected_result": {"kind": "volume_up"},
                }
            if soft_prev == "brightness_down":
                payload = {
                    "kind": "brightness_up",
                    "intent_family": "SMART_HOME",
                    "objective": "Rozjaśnij — nie aż tak",
                    "continuation": True,
                    "confidence": 0.9,
                    "source": "soft_light_relative_talk",
                    "slots": {
                        "implicit_light": True,
                        "desired": "brighter",
                        "domain": "light",
                    },
                    "expected_result": {"kind": "brightness_up"},
                }
                return _bind_light_entity(payload, text=raw, ctx=ctx, prefer_active=True)
            if soft_prev == "brightness_up":
                payload = {
                    "kind": "brightness_down",
                    "intent_family": "SMART_HOME",
                    "objective": "Zmniejsz jasność — nie aż tak",
                    "continuation": True,
                    "confidence": 0.9,
                    "source": "soft_light_relative_talk",
                    "slots": {
                        "implicit_light": True,
                        "desired": "darker",
                        "domain": "light",
                    },
                    "expected_result": {"kind": "brightness_down"},
                }
                return _bind_light_entity(payload, text=raw, ctx=ctx, prefer_active=True)
        return None

    # Multi-step / PLAN utterances belong to Agent/Planner — soft must not
    # steal one clause (e.g. volume half of "zamknij X i zmniejsz głośność").
    if env_mode == "PLAN" or env_intent == "multi_action":
        return None
    try:
        from core.intent_family import _is_compound_intent

        if _is_compound_intent(raw):
            return None
    except Exception:
        pass

    # Pronoun/action turn-off beats stale brightness soft context ("zgaś ją").
    # Active climate (etc.) must not be stolen by soft light_off on bare "wyłącz ją".
    if _LIGHT_OFF.search(raw) and not re.search(
        r"\b(?:nie\s+zga[sś]|nie\s+wy[lł][aą]cz)\b", raw, re.I
    ):
        active_domain = ""
        if ctx is not None:
            active_domain = str(
                (getattr(ctx, "active_entities", None) or {}).get("domain") or ""
            ).lower()
        light_named = bool(_LIGHT_MENTION.search(raw))
        if active_domain and active_domain != "light" and not light_named:
            pass  # defer to device-pronoun / Agent for climate etc.
        else:
            payload = {
                "kind": "light_off",
                "intent_family": "SMART_HOME",
                "objective": "Zgaś światło",
                "confidence": 0.91,
                "source": "soft_light_off",
                "slots": {
                    "implicit_light": True,
                    "desired": "off",
                    "domain": "light",
                    "action": "turn_off",
                },
                "expected_result": {"kind": "light_off"},
                "continuation": bool(
                    ctx is not None
                    and (
                        active_domain == "light"
                        or str(
                            (getattr(ctx, "previous_goal", None) or {}).get("intent_family")
                            or ""
                        )
                        == "SMART_HOME"
                    )
                ),
            }
            return _bind_light_entity(payload, text=raw, ctx=ctx, prefer_active=True)

    if _MEDIA_NOW.search(raw):
        return {
            "kind": "media_now",
            "intent_family": "MEDIA",
            "objective": "Sprawdź, co teraz leci",
            "confidence": 0.9,
            "source": "soft_media_now",
            "expected_result": {"kind": "now_playing"},
        }

    if _VAGUE_PLAY.search(raw):
        # Keep query soft — Planner/tool may refine; never invent a track.
        return {
            "kind": "media_play",
            "intent_family": "MEDIA",
            "objective": "Puść coś (nieokreślone)",
            "confidence": 0.86,
            "source": "soft_vague_media",
            "slots": {"media_query": "muzyka", "vague": True},
            "expected_result": {"kind": "media_play"},
        }

    named = _NAMED_PLAY.match(raw)
    if named:
        query = str(named.group(1) or "").strip()
        # "odpal <target>" without "coś" is APP launch — soft media must not steal it.
        if re.match(r"^\s*odpal\b", raw, re.I) and not re.search(r"\bco[sś]\b", raw, re.I):
            query = ""
        # Skip if it looks like an app open without media vibe.
        if query and len(query.split()) <= 6 and not re.search(
            r"\b(?:przegl[aą]dark|notatnik|kalkulator|discord|teams|chrome|steam)\b",
            query,
            re.I,
        ):
            return {
                "kind": "media_play",
                "intent_family": "MEDIA",
                "objective": f"Puść: {query}",
                "confidence": 0.88,
                "source": "soft_named_media",
                "slots": {"media_query": query},
                "expected_result": {"kind": "media_play", "query": query},
            }

    if _OPEN_FIRST.search(raw):
        return _soft_open_web_result(raw, ctx, index=0, source="soft_open_first")

    if _OPEN_ORDINAL.search(raw):
        idx = _web_result_ordinal_index(raw)
        if idx is not None:
            return _soft_open_web_result(
                raw, ctx, index=idx, source="soft_open_ordinal"
            )

    summarized = _soft_summarize_web(raw, ctx, source="soft_summarize_web")
    if summarized is not None:
        return summarized

    if _SEARCH_REFERENT.search(raw) and ctx is not None:
        active = getattr(ctx, "active_entities", None) or {}
        topic = str(
            active.get("topic")
            or active.get("web_query")
            or getattr(ctx, "last_web_query", "")
            or ""
        ).strip()
        if topic:
            return {
                "kind": "web_search",
                "intent_family": "WEB",
                "objective": f"Wyszukaj: {topic}",
                "confidence": 0.9,
                "source": "soft_search_referent",
                "slots": {"query": topic},
                "expected_result": {"kind": "web_search", "query": topic},
            }

    about = _SEARCH_ABOUT.search(raw)
    if about:
        query = str(about.group(1) or "").strip().rstrip(".!?…")
        if query and query.lower() != "tym":
            try:
                from core.web_grounding import clean_web_query

                query = clean_web_query(query) or query
            except Exception:
                pass
            return {
                "kind": "web_search",
                "intent_family": "WEB",
                "objective": f"Wyszukaj: {query}",
                "confidence": 0.9,
                "source": "soft_search_about",
                "slots": {"query": query},
                "expected_result": {"kind": "web_search", "query": query},
            }

    network = _NETWORK_SAYS.search(raw)
    if network:
        query = str(
            next((g for g in network.groups() if g), "") or ""
        ).strip().rstrip(".!?…")
        query = re.sub(r"\s+#\d+\s*$", "", query).strip()
        if query:
            try:
                from core.web_grounding import clean_web_query

                query = clean_web_query(query) or query
            except Exception:
                pass
            return {
                "kind": "web_search",
                "intent_family": "WEB",
                "objective": f"Wyszukaj: {query}",
                "confidence": 0.9,
                "source": "soft_network_says",
                "slots": {"query": query},
                "expected_result": {"kind": "web_search", "query": query},
            }

    bare = _SEARCH_BARE.match(raw)
    if bare:
        query = str(bare.group(1) or "").strip().rstrip(".!?…")
        # Avoid swallowing "szukaj coś o tym" without topic (handled above).
        if query and not re.match(r"^(?:co[sś]\s+)?o\s+tym$", query, re.I):
            query = re.sub(r"^(?:co[sś]\s+)?o\s+", "", query, flags=re.I).strip() or query
            try:
                from core.web_grounding import clean_web_query

                # Re-attach verb shell for clean_web_query wrapper strip when needed.
                query = clean_web_query(f"wyszukaj {query}") or query
            except Exception:
                pass
            return {
                "kind": "web_search",
                "intent_family": "WEB",
                "objective": f"Wyszukaj: {query}",
                "confidence": 0.88,
                "source": "soft_search_bare",
                "slots": {"query": query},
                "expected_result": {"kind": "web_search", "query": query},
            }

    defined = _DEFINE_TOPIC.match(raw)
    if defined:
        topic = str(defined.group(1) or "").strip().rstrip(".!?…")
        if topic and len(topic) >= 2:
            try:
                from core.web_grounding import clean_web_query

                topic = clean_web_query(topic) or topic
            except Exception:
                pass
            return {
                "kind": "web_search",
                "intent_family": "WEB",
                "objective": f"Wyjaśnij: {topic}",
                "confidence": 0.9,
                "source": "soft_define",
                "slots": {"query": topic},
                "expected_result": {"kind": "web_search", "query": topic},
            }

    if _GAMING_SETUP.search(raw):
        return {
            "kind": "gaming_setup",
            "intent_family": "PROCEDURE",
            "objective": "Przygotuj komputer do grania",
            "confidence": 0.88,
            "source": "soft_gaming",
            "slots": {"procedure_id": "gaming"},
            "expected_result": {"kind": "procedure", "id": "gaming"},
        }

    if _REPLAY_LAST.search(raw) or env_intent == "retry_goal" or env_utt == "RETRY":
        # "Jeszcze raz, kto jest premierem…?" carries a new question — not journal replay.
        if _looks_replay_with_new_question(raw):
            pass
        else:
            journal = dict(getattr(ctx, "last_journal_action", None) or {}) if ctx is not None else {}
            pending = dict(getattr(ctx, "pending_executable", None) or {}) if ctx is not None else {}
            if pending:
                return {
                    "kind": "retry_pending",
                    "intent_family": "CONVERSATION_CONTROL",
                    "objective": str(pending.get("goal") or "Ponów nieudane kroki"),
                    "continuation": True,
                    "confidence": 0.9,
                    "source": "soft_retry_pending",
                    "slots": {"pending": pending},
                    "expected_result": {"kind": "retry_pending"},
                }
            # Session observations beat process-global journal (stale set_preference etc.).
            obs_tool = str(getattr(ctx, "previous_tool", "") or "").strip() if ctx is not None else ""
            journal_tool = str(journal.get("tool") or "").strip()
            tool = obs_tool or journal_tool
            args: dict = {}
            if obs_tool and (not journal_tool or journal_tool == obs_tool):
                args = dict(journal.get("arguments") or {}) if journal_tool == obs_tool else {}
            elif journal_tool and not obs_tool:
                tool = journal_tool
                args = dict(journal.get("arguments") or {})
            if obs_tool and journal_tool and obs_tool != journal_tool:
                tool = obs_tool
                args = {}
            if not args and ctx is not None:
                prev_res = dict(getattr(ctx, "previous_result", None) or {})
                data = prev_res.get("data") if isinstance(prev_res.get("data"), dict) else {}
                if isinstance(data, dict) and data:
                    args = {
                        k: v
                        for k, v in data.items()
                        if k in ("name", "query", "entity_id", "target", "url", "action", "domain", "value")
                    }
                # Failed FastPath open_app often stores name only on the call args via observation.
                if tool == "open_app" and not args.get("name"):
                    for key in ("name", "app", "app_name"):
                        cand = str(prev_res.get(key) or "").strip()
                        if cand:
                            args = {**args, "name": cand}
                            break
                if tool == "open_app" and not args.get("name"):
                    active = getattr(ctx, "active_entities", None) or {}
                    app = str(active.get("app") or "").strip()
                    if app:
                        args = {"name": app}
            # Retry goal text / primary objective often carries the app when args were lost.
            if tool == "open_app" and not str(args.get("name") or "").strip() and ctx is not None:
                prev = dict(getattr(ctx, "previous_goal", None) or {})
                obj = str(
                    prev.get("objective")
                    or getattr(ctx, "active_goal_objective", "")
                    or ""
                )
                m = re.search(
                    r"(?:otw[oó]rz|odpal|uruchom|open)\s+(?:mi\s+)?([a-zA-Z0-9][\w\-]*)",
                    obj,
                    re.I,
                )
                if m:
                    args = {**args, "name": str(m.group(1) or "").strip()}
            # Explicit retry_goal envelope: recover open_app from goal string even if tool wrong.
            if (env_intent == "retry_goal" or env_utt == "RETRY") and ctx is not None:
                prev = dict(getattr(ctx, "previous_goal", None) or {})
                obj = str(prev.get("objective") or "")
                m = re.search(
                    r"(?:otw[oó]rz|odpal|uruchom|open)\s+(?:mi\s+)?([a-zA-Z0-9][\w\-]*)",
                    obj,
                    re.I,
                )
                if m and (not tool or tool in ("open_app", "set_preference", "remember_fact")):
                    tool = "open_app"
                    args = {"name": str(m.group(1) or "").strip()}
            if tool:
                return {
                    "kind": "replay_last",
                    "intent_family": "SYSTEM",
                    "objective": f"Powtórz ostatnią czynność: {tool}",
                    "continuation": True,
                    "confidence": 0.9,
                    "source": "soft_retry_last",
                    "slots": {
                        "tool": tool,
                        "arguments": args,
                        "action_id": journal.get("action_id"),
                    },
                    "expected_result": {"kind": "replay_last", "tool": tool},
                }
            prev = dict(getattr(ctx, "previous_goal", None) or {}) if ctx is not None else {}
            if prev.get("soft_kind") or prev.get("objective"):
                if str(prev.get("status") or "").upper() == "CANCELLED":
                    return None
                if str(prev.get("soft_kind") or "") == "cancel_goal":
                    return None
                return {
                    "kind": "resume_previous",
                    "intent_family": str(prev.get("intent_family") or "SYSTEM"),
                    "objective": str(prev.get("objective") or "Powtórz poprzedni cel"),
                    "continuation": True,
                    "confidence": 0.84,
                    "source": "soft_resume_previous",
                    "slots": {"previous_goal": prev},
                    "expected_result": {"kind": "resume_previous"},
                }
            # No bound prior action/goal after cancel — do not invent resume.
            return None

    if _ACT_ON_CONTEXT.search(raw) and ctx is not None:
        prev = dict(getattr(ctx, "previous_goal", None) or {})
        soft_prev = str(prev.get("soft_kind") or "")
        lightish = bool(_LIGHT_MENTION.search(raw)) or soft_prev.startswith(
            ("brightness", "light_")
        ) or str(prev.get("intent_family") or "") == "SMART_HOME"
        if lightish:
            desired = "brighter" if soft_prev != "brightness_down" else "darker"
            kind = "brightness_up" if desired == "brighter" else "brightness_down"
            if soft_prev == "light_off":
                kind = "light_off"
                desired = "off"
            payload = {
                "kind": kind,
                "intent_family": "SMART_HOME",
                "objective": str(prev.get("objective") or "Dostosuj światło"),
                "continuation": True,
                "confidence": 0.88,
                "source": "soft_act_on_context",
                "slots": {
                    "implicit_light": True,
                    "desired": desired,
                    "domain": "light",
                },
                "expected_result": {"kind": kind},
            }
            return _bind_light_entity(payload, text=raw, ctx=ctx, prefer_active=True)
        if soft_prev in ("volume_down", "volume_up", "media_play", "media_calm", "gaming_setup"):
            return {
                "kind": soft_prev,
                "intent_family": str(prev.get("intent_family") or ""),
                "objective": str(prev.get("objective") or "Kontynuuj poprzedni cel"),
                "continuation": True,
                "confidence": 0.86,
                "source": "soft_act_on_context",
                "slots": dict(prev.get("slots") or {}),
                "expected_result": dict(prev.get("expected_result") or {"kind": soft_prev}),
            }

    # Discourse resume: confirmation / retry against an open SMART_HOME or soft goal.
    if ctx is not None and (
        env_intent in ("execute_discussed_goal", "retry_goal", "resume_unresolved_goal")
        or env_utt in ("CONFIRMATION", "RETRY", "CONTINUATION")
    ):
        prev = dict(getattr(ctx, "previous_goal", None) or {})
        if str(prev.get("status") or "").upper() == "CANCELLED":
            return None
        if str(prev.get("soft_kind") or "") == "cancel_goal":
            return None
        soft_prev = str(prev.get("soft_kind") or "")
        family = str(prev.get("intent_family") or "")
        active = getattr(ctx, "active_entities", None) or {}
        if not soft_prev and family != "SMART_HOME" and str(active.get("domain") or "") != "light":
            # Nothing concrete to resume after cancel / empty previous.
            if not prev.get("objective"):
                return None
        if soft_prev.startswith(("brightness", "light_")) or family == "SMART_HOME" or str(
            active.get("domain") or ""
        ) == "light":
            kind = soft_prev if soft_prev.startswith(("brightness", "light_")) else "brightness_up"
            if env_intent == "retry_goal" and soft_prev == "brightness_down":
                kind = "brightness_down"
            # Explicit on/off goals prefer turn_on path via light_on soft.
            obj = str(prev.get("objective") or getattr(ctx, "unresolved_task", {}) or "")
            if re.search(r"\bw[lł][aą]cz\b", obj, re.I) or re.search(
                r"\bw[lł][aą]cz\b", str(prev.get("objective") or ""), re.I
            ):
                kind = "light_on"
            if soft_prev == "light_off" or re.search(r"\bzga[sś]\b", obj, re.I):
                kind = "light_off"
            payload = {
                "kind": kind,
                "intent_family": "SMART_HOME",
                "objective": str(prev.get("objective") or "Kontynuuj sterowanie światłem"),
                "continuation": True,
                "confidence": 0.9,
                "source": "soft_discourse_resume",
                "slots": {
                    "implicit_light": True,
                    "desired": (
                        "off"
                        if kind == "light_off"
                        else ("darker" if kind == "brightness_down" else "brighter")
                    ),
                    "domain": "light",
                },
                "expected_result": {"kind": kind},
            }
            return _bind_light_entity(payload, text=raw, ctx=ctx, prefer_active=True)
        if soft_prev in ("volume_down", "volume_up", "media_play", "media_calm", "gaming_setup", "web_search"):
            return {
                "kind": soft_prev,
                "intent_family": family,
                "objective": str(prev.get("objective") or "Kontynuuj poprzedni cel"),
                "continuation": True,
                "confidence": 0.88,
                "source": "soft_discourse_resume",
                "slots": dict(prev.get("slots") or {}),
                "expected_result": dict(prev.get("expected_result") or {"kind": soft_prev}),
            }

    # Domain-switch volume: never inherit brightness clarify slots.
    if _SOFT_VOLUME_DOWN.search(raw) and not _TOO_QUIET.search(raw) and not _LIGHT_MENTION.search(raw):
        if not IMPLICIT_LIGHT_BRIGHTER.search(lowered) and not IMPLICIT_LIGHT_DARK.search(lowered):
            return {
                "kind": "volume_down",
                "intent_family": "SYSTEM",
                "objective": "Ścisz głośność",
                "confidence": 0.9,
                "source": "soft_volume_phrase",
                "slots": {
                    "volume_direction": "decrease",
                    "semantic_action": "adjust_volume",
                    "clear_light_context": True,
                },
                "expected_result": {"kind": "volume_down"},
            }

    if (
        _SOFT_VOLUME_UP.search(raw)
        and not _TOO_LOUD.search(raw)
        and not _SOFT_VOLUME_DOWN.search(raw)
        and not _LIGHT_MENTION.search(raw)
    ):
        if not IMPLICIT_LIGHT_BRIGHTER.search(lowered) and not IMPLICIT_LIGHT_DARK.search(lowered):
            return {
                "kind": "volume_up",
                "intent_family": "SYSTEM",
                "objective": "Podgłośnij",
                "confidence": 0.9,
                "source": "soft_volume_phrase",
                "slots": {
                    "volume_direction": "increase",
                    "semantic_action": "adjust_volume",
                    "clear_light_context": True,
                },
                "expected_result": {"kind": "volume_up"},
            }

    if _TOO_LOUD.search(raw) and not _TOO_QUIET.search(raw):
        return {
            "kind": "volume_down",
            "intent_family": "SYSTEM",
            "objective": "Ścisz — jest za głośno",
            "confidence": 0.9,
            "source": "soft_volume",
            "slots": {
                "volume_direction": "decrease",
                "semantic_action": "adjust_volume",
                "clear_light_context": True,
            },
            "expected_result": {"kind": "volume_down"},
        }
    if _TOO_QUIET.search(raw) and not _TOO_LOUD.search(raw):
        return {
            "kind": "volume_up",
            "intent_family": "SYSTEM",
            "objective": "Podgłośnij — jest za cicho",
            "confidence": 0.9,
            "source": "soft_volume",
            "slots": {
                "volume_direction": "increase",
                "semantic_action": "adjust_volume",
                "clear_light_context": True,
            },
            "expected_result": {"kind": "volume_up"},
        }

    media_active = bool(getattr(ctx, "media_active", False))
    prev_family = ""
    if ctx is not None:
        prev = getattr(ctx, "previous_goal", None) or {}
        if isinstance(prev, dict):
            prev_family = str(prev.get("intent_family") or "")
        if not media_active and prev_family == "MEDIA":
            media_active = True
        # Recent media tool observation
        prev_tool = str(getattr(ctx, "previous_tool", "") or "")
        if prev_tool in ("play_media", "resume_media", "get_now_playing"):
            media_active = True

    if _CALMER_MEDIA.search(raw):
        return {
            "kind": "media_calm",
            "intent_family": "MEDIA",
            "objective": "Puść coś spokojniejszego",
            "confidence": 0.86 if media_active else 0.84,
            "source": "soft_media_mood" if media_active else "soft_media_mood_bare",
            "mood": "calm",
            "slots": {"media_query": "spokojna muzyka", "mood": "calm"},
            "expected_result": {"kind": "media_play", "mood": "calm"},
        }

    if insufficient_light and not IMPLICIT_LIGHT_BRIGHTER.search(lowered):
        # Imperative dim (przyciemnij) vs room-is-dark wish (jest ciemno → brighten).
        if re.search(r"\b(?:przyciemn|ściemn|sciemn|ciemniej)\w*\b", lowered):
            kind = "brightness_down"
            desired = "darker"
            objective = "Przyciemnij światło"
        else:
            kind = "brightness_up"
            desired = "brighter"
            objective = "Rozjaśnij — jest ciemno"
        payload = {
            "kind": kind,
            "intent_family": "SMART_HOME",
            "objective": objective,
            "confidence": 0.88,
            "source": "soft_light",
            "slots": {
                "implicit_light": True,
                "desired": desired,
                "domain": "light",
            },
            "expected_result": {"kind": kind},
        }
        return _bind_light_entity(payload, text=raw, ctx=ctx, prefer_active=True)

    if IMPLICIT_LIGHT_BRIGHTER.search(lowered):
        desired = "brighter"
        kind = "brightness_up"
        if _NOT_THAT_MUCH.search(raw):
            desired = "darker"
            kind = "brightness_down"
        payload = {
            "kind": kind,
            "intent_family": "SMART_HOME",
            "objective": "Dostosuj jasność światła",
            "confidence": 0.88,
            "source": "soft_light",
            "slots": {
                "implicit_light": True,
                "desired": desired,
                "domain": "light",
            },
            "expected_result": {"kind": kind},
        }
        return _bind_light_entity(payload, text=raw, ctx=ctx, prefer_active=True)

    # Absolute "too bright" → dim (and store soft_kind for later reverse).
    if _TOO_BRIGHT.search(raw):
        payload = {
            "kind": "brightness_down",
            "intent_family": "SMART_HOME",
            "objective": "Przyciemnij — za jasno",
            "confidence": 0.9,
            "source": "soft_light_too_bright",
            "slots": {
                "implicit_light": True,
                "desired": "darker",
                "domain": "light",
            },
            "expected_result": {"kind": "brightness_down"},
        }
        return _bind_light_entity(payload, text=raw, ctx=ctx, prefer_active=True)

    if _NOT_THAT_MUCH.search(raw) and ctx is not None:
        prev = dict(getattr(ctx, "previous_goal", None) or {})
        soft_prev = str(prev.get("soft_kind") or "")
        prev_obj = str(prev.get("objective") or "").casefold()
        prev_slots = dict(prev.get("slots") or {})
        desired_prev = str(prev_slots.get("desired") or "").casefold()
        active = getattr(ctx, "active_entities", None) or {}

        # Reverse last volume nudge.
        if soft_prev == "volume_up":
            return {
                "kind": "volume_down",
                "intent_family": "SYSTEM",
                "objective": "Ścisz — nie aż tak",
                "continuation": True,
                "confidence": 0.9,
                "source": "soft_volume_relative",
                "slots": {
                    "volume_direction": "decrease",
                    "semantic_action": "adjust_volume",
                    "clear_light_context": True,
                },
                "expected_result": {"kind": "volume_down"},
            }
        if soft_prev == "volume_down":
            return {
                "kind": "volume_up",
                "intent_family": "SYSTEM",
                "objective": "Podgłośnij — nie aż tak",
                "continuation": True,
                "confidence": 0.9,
                "source": "soft_volume_relative",
                "slots": {
                    "volume_direction": "increase",
                    "semantic_action": "adjust_volume",
                    "clear_light_context": True,
                },
                "expected_result": {"kind": "volume_up"},
            }

        lightish = soft_prev.startswith(("brightness", "light_")) or str(
            prev.get("intent_family") or ""
        ) == "SMART_HOME" or str(active.get("domain") or "") == "light"
        if lightish:
            went_darker = (
                soft_prev == "brightness_down"
                or desired_prev == "darker"
                or bool(
                    re.search(
                        r"za\s+jasno|przyciemn|ściemn|sciemn|ciemniej",
                        prev_obj,
                        re.I,
                    )
                )
            )
            # Dial back the last brightness move; default after brighten / unknown.
            if went_darker and soft_prev != "brightness_up" and desired_prev != "brighter":
                kind = "brightness_up"
                desired = "brighter"
                objective = "Rozjaśnij — nie aż tak"
            else:
                kind = "brightness_down"
                desired = "darker"
                objective = "Zmniejsz jasność — nie aż tak"
            payload = {
                "kind": kind,
                "intent_family": "SMART_HOME",
                "objective": objective,
                "continuation": True,
                "confidence": 0.9,
                "source": "soft_light_relative",
                "slots": {
                    "implicit_light": True,
                    "desired": desired,
                    "domain": "light",
                },
                "expected_result": {"kind": kind},
            }
            return _bind_light_entity(payload, text=raw, ctx=ctx, prefer_active=True)

    return None
