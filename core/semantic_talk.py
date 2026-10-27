# ==========================
# ELIAN — conversational generation (C.5.4)
# ==========================
"""TALK is a first-class success. No tools. Original Elian voice — not Marvel."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

import re

from core.semantic_executive import DecisionEnvelope


_JOKES = (
    "Programista wchodzi do baru. Barman mówi: „Nie serwujemy napojów rekurencyjnych.” "
    "Programista: „To poproszę napój rekurencyjny.”",
    "Dlaczego komputer poszedł do terapeuty? Miał za dużo okien i żadnego focusa.",
    "Suchar: jak nazywa się spokojny Python? Indented, ale nie zestresowany.",
    "Spotify zna twoje gusty. Ja znam tylko to, co właśnie powiedziałeś — i to mi wystarcza.",
    "Dwa bity idą ulicą. Jeden mówi: „Nie mam pary.” Drugi: „To się zaokrąglij.”",
    "Użytkownik prosi o żart. Asystent nie otwiera Notatnika. Koniec żartu. "
    "Chyba że chcesz jeszcze jednego — wtedy naprawdę opowiem.",
    "Czemu RAM nigdy nie kłamie? Bo trzyma tylko to, co akurat pamięta.",
    "Debugowanie: jak szukać okularów, które masz na nosie, tylko w kodzie.",
)


def _pick(items: tuple[str, ...], seed: str) -> str:
    digest = hashlib.sha1(str(seed or "elian").encode("utf-8")).hexdigest()
    return items[int(digest[:8], 16) % len(items)]


_NUM_WORDS = {
    "zero": 0,
    "jeden": 1,
    "jedna": 1,
    "jedno": 1,
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

_ONES = (
    "zero",
    "jeden",
    "dwa",
    "trzy",
    "cztery",
    "pięć",
    "sześć",
    "siedem",
    "osiem",
    "dziewięć",
)
_TEENS = (
    "dziesięć",
    "jedenaście",
    "dwanaście",
    "trzynaście",
    "czternaście",
    "piętnaście",
    "szesnaście",
    "siedemnaście",
    "osiemnaście",
    "dziewiętnaście",
)
_TENS = (
    "",
    "",
    "dwadzieścia",
    "trzydzieści",
    "czterdzieści",
    "pięćdziesiąt",
    "sześćdziesiąt",
    "siedemdziesiąt",
    "osiemdziesiąt",
    "dziewięćdziesiąt",
)

_MATH = re.compile(
    r"(?:ile\s+(?:to|jest|wynosi)\s+)?(\d+|[a-ząćęłńóśźż]+)\s+"
    r"(plus|\+|minus|-|razy|×|x|podziel(?:one|i[cć])?(?:\s+przez)?|/)\s+"
    r"(\d+|[a-ząćęłńóśźż]+)",
    re.I,
)


def humor_reply(goal: str = "") -> str:
    return _pick(_JOKES, goal or "żart")


def _parse_small_number(token: str) -> int | None:
    raw = str(token or "").strip().lower()
    if raw.isdigit():
        return int(raw)
    return _NUM_WORDS.get(raw)


def _pl_small_int(value: int) -> str:
    n = int(value)
    if n < 0 or n > 99:
        return str(n)
    if n < 10:
        return _ONES[n]
    if n < 20:
        return _TEENS[n - 10]
    tens, ones = divmod(n, 10)
    if ones == 0:
        return _TENS[tens]
    return f"{_TENS[tens]} {_ONES[ones]}"


def try_simple_math(text: str) -> str:
    """Deterministic Polish arithmetic. Empty string if not a simple sum."""
    match = _MATH.search(str(text or ""))
    if not match:
        return ""
    left = _parse_small_number(match.group(1))
    right = _parse_small_number(match.group(3))
    if left is None or right is None:
        return ""
    op = str(match.group(2) or "").lower()
    if op in ("plus", "+"):
        result = left + right
    elif op in ("minus", "-"):
        result = left - right
    elif op in ("razy", "×", "x"):
        result = left * right
    else:
        if right == 0:
            return ""
        result = left // right
    spoken = _pl_small_int(result)
    return spoken[0].upper() + spoken[1:] + "."


_TIME_QUESTION = re.compile(
    r"(?:"
    r"kt[oó]r[aeey]\s+(?:jest\s+)?godzin"
    r"|jaka\s+(?:jest\s+)?godzin"
    r"|ile\s+(?:jest\s+)?godzin"
    r"|powiedz\s+(?:mi\s+)?(?:kt[oó]r[ae]\s+)?godzin"
    r"|jaki\s+(?:mamy\s+)?czas"
    r")",
    re.I,
)
_DATE_QUESTION = re.compile(
    r"(?:"
    r"jaka\s+(?:jest\s+)?data"
    r"|jaki\s+(?:dzisiaj|dzi[sś]|dzi[sś]\s+mamy)\s+dzie[nń]"
    r"|kt[oó]ry\s+(?:dzi[sś]|dzisiaj)\s+(?:jest\s+)?dzie[nń]"
    r"|jaki\s+dzie[nń]\s+(?:jest\s+)?dzi[sś]"
    r")",
    re.I,
)


def try_clock_reply(text: str) -> str:
    """Wall-clock time/date from the OS — never invent from the LLM."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    try:
        from datetime import datetime, timedelta

        now = datetime.now()
    except Exception:
        return ""
    # Bare follow-ups: "za dwie?" after a clock turn → minutes (not LLM guesswork).
    bare = re.fullmatch(
        r"za\s+(jedn[aą]|dwie|trzy|cztery|pi[eę][cć]|sze[sś][cć]|siedem|osiem|dziewi[eę][cć]|\d+)\s*\??",
        raw.strip(),
        re.I,
    )
    if bare:
        word = str(bare.group(1) or "").lower()
        words = {
            "jedną": 1,
            "jedna": 1,
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
        }
        amount = words.get(word) or (int(word) if word.isdigit() else 0)
        if amount > 0:
            then = now + timedelta(minutes=amount)
            if amount == 1:
                span = "minutę"
            elif 2 <= amount <= 4:
                span = f"{amount} minuty"
            else:
                span = f"{amount} minut"
            return f"Za {span} będzie {then.hour}:{then.minute:02d}."
    # "jaka godzina będzie za minutę / za N minut / za godzinę"
    rel = re.search(
        r"(?:b[eę]dzie\s+)?za\s+(?:(jedn[aą]|1)\s+)?(minut[eę]?|godzin[eę]?)\b",
        raw,
        re.I,
    )
    if rel or re.search(r"\bza\s+(\d+)\s*(minut|godzin)", raw, re.I):
        amount = 1
        unit = "minut"
        m_num = re.search(r"\bza\s+(\d+)\s*(minut|godzin)", raw, re.I)
        if m_num:
            amount = int(m_num.group(1))
            unit = m_num.group(2).lower()
        elif rel:
            unit = str(rel.group(2) or "minut").lower()
        delta = timedelta(hours=amount) if unit.startswith("godzin") else timedelta(minutes=amount)
        then = now + delta
        if unit.startswith("godzin"):
            span = "godzinę" if amount == 1 else f"{amount} godzin"
        else:
            span = "minutę" if amount == 1 else f"{amount} minut"
        return f"Za {span} będzie {then.hour}:{then.minute:02d}."
    if re.search(r"\b(?:jutro|pojutrze)\b", raw, re.I) and _TIME_QUESTION.search(raw):
        return ""
    if _DATE_QUESTION.search(raw):
        weekdays = (
            "poniedziałek",
            "wtorek",
            "środa",
            "czwartek",
            "piątek",
            "sobota",
            "niedziela",
        )
        return (
            f"Dzisiaj jest {weekdays[now.weekday()]}, "
            f"{now.day}.{now.month}.{now.year}."
        )
    if _TIME_QUESTION.search(raw):
        return f"Jest godzina {now.hour}:{now.minute:02d}."
    return ""


def try_local_knowledge_reply(goal: str, envelope: Optional[DecisionEnvelope] = None) -> str:
    clock = try_clock_reply(goal)
    if clock:
        return clock
    math = try_simple_math(goal)
    if math:
        return math
    intent = str(getattr(envelope, "intent", "") or "")
    if intent == "knowledge_explanation":
        clock = try_clock_reply(str(getattr(envelope, "user_goal", "") or ""))
        if clock:
            return clock
        math = try_simple_math(str(getattr(envelope, "user_goal", "") or ""))
        if math:
            return math
    return ""


def talk_reply(
    envelope: Optional[DecisionEnvelope] = None,
    *,
    goal: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> str:
    env = envelope
    intent = str(getattr(env, "intent", "") or "")
    text = str(goal or getattr(env, "user_goal", "") or "")
    clock = try_clock_reply(text)
    if clock:
        return clock
    draft = str(getattr(env, "draft_reply", "") or "").strip()
    if draft and intent != "creative_humor":
        return draft
    if draft and intent == "creative_humor" and len(draft) > 20:
        return draft
    if intent == "say_text":
        payload = str(getattr(env, "payload", "") or "").strip()
        if not payload:
            from core.semantic_executive import extract_say_payload, format_say_speech

            payload = extract_say_payload(text)
            return format_say_speech(payload) if payload else "Słucham."
        from core.semantic_executive import format_say_speech

        return format_say_speech(payload)
    if intent == "creative_humor" or "żart" in text.lower() or "suchar" in text.lower():
        return humor_reply(text)
    math = try_simple_math(text)
    if math:
        return math
    if intent == "social_smalltalk":
        from core.jarvis_response import social_reply

        return social_reply(text)
    lowered = text.lower()
    if intent == "general_conversation" or not intent:
        from core.jarvis_response import social_reply
        from core.context_selection import looks_small_talk

        if looks_small_talk(text):
            return social_reply(text)
        if "potrafisz" in lowered or "umiesz" in lowered:
            return "Słucham, odpowiadam i wykonuję polecenia, gdy poprosisz wprost."
        clock = try_clock_reply(text)
        if clock:
            return clock
    if intent in (
        "task_status",
        "return_to_task",
        "decision_followup",
        "no_task_status",
        "defer_proactive",
    ):
        store = (context or {}).get("collab_store")
        if store is not None:
            reply = store.try_handle(text, env)
            if reply is not None and not reply.silent:
                return reply.message
        if intent == "no_task_status":
            return "Nie mam teraz otwartego zadania. O czym chcesz usłyszeć?"
        if intent == "defer_proactive":
            return "Jasne, nie teraz."
        if intent in ("task_status", "return_to_task"):
            from core.collaboration import natural_task_status

            task = None
            snap = (context or {}).get("collaboration") or {}
            raw_task = snap.get("task") if isinstance(snap, dict) else None
            if isinstance(raw_task, dict) and raw_task.get("goal"):
                from core.collaboration import ActiveTaskContext

                task = ActiveTaskContext(
                    task_id=str(raw_task.get("task_id") or ""),
                    goal=str(raw_task.get("goal") or ""),
                    status=str(raw_task.get("status") or ""),
                    current_step=str(raw_task.get("current_step") or ""),
                    completed_steps=list(raw_task.get("completed_steps") or []),
                    blockers=list(raw_task.get("blockers") or []),
                    observations=list(raw_task.get("observations") or []),
                )
            return natural_task_status(task)
        return "Dla tego samego problemu mogę omówić inną opcję."
    if intent == "conversational_followup":
        topic = str((context or {}).get("current_topic") or "")
        if "przykład" in text.lower():
            return "Na przykład: model uczy się wzorców z danych, a potem zgaduje kolejne."
        if "prościej" in text.lower() or "inaczej" in text.lower():
            return "Krótko: to program, który szuka regularności i na ich podstawie odpowiada."
        if "czemu" in text.lower() or "dlaczego" in text.lower():
            return "Bo bez tego kroku odpowiedź byłaby tylko etykietą, nie wyjaśnieniem."
        if "innym" in text.lower():
            return "Jasne. O czym chcesz teraz?"
        if "rozumiem" in text.lower():
            return "Dobrze. Jak będziesz chciał iść dalej, mów."
        if topic:
            return f"Jeszcze raz o {topic}: to kwestia wzorców, nie magii."
        return "Jasne — powiedz, od której części zacząć."
    if intent in ("knowledge_explanation", "action_discussion", "general_conversation"):
        local = try_local_knowledge_reply(text, env)
        if local:
            return local
        if "spotify" in text.lower() and text.lower().startswith("jak"):
            return "Otwierasz aplikację Spotify albo stronę, potem szukasz utworu i puszczasz odtwarzanie. Chcesz, żebym to zrobił?"
        if "czy umiesz" in text.lower() or "czy potrafisz" in text.lower() or "co potrafisz" in text.lower():
            return "Słucham, odpowiadam i wykonuję polecenia, gdy poprosisz wprost."
        if "nie otwieraj" in text.lower():
            return "Nie otwieram."
        if draft:
            return draft
        if intent == "general_conversation":
            from core.jarvis_response import social_reply

            return social_reply(text)
        return "Mogę to rozwinąć. Co dokładnie chcesz usłyszeć?"
    if draft:
        return draft
    return "Słucham. Mów, o czym myślisz albo co mam zrobić."
