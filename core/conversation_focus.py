# ==========================
# ELIAN — conversation focus (Phase 11 / 11.2)
# ==========================
"""Light structured follow-up context — not a full transcript dump."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from core.freshness import extract_person_candidates, is_meta_utterance
from core.response_language import is_language_only_request


_FOLLOWUP = re.compile(
    r"^(?:a\s+|i\s+)?(?:"
    r"kiedy|ile|dlaczego|czemu|gdzie|kto|jaki|jaka|jakie|"
    r"serio|naprawd[eę]|w\s+sensie|czyli"
    r")\b",
    re.I,
)

_REEXPLAIN = re.compile(
    r"(?:"
    r"\bnie\s+rozumiem\b|"
    r"\bwcale\s+nie\s+rozumiem\b|"
    r"\bdalej\s+nie\s+rozumiem\b|"
    r"\bnadal\s+nie\s+rozumiem\b|"
    r"\bjeszcze\s+nie\s+rozumiem\b|"
    r"\bwci[aą][zż]\s+nie\s+rozumiem\b|"
    r"\bwyt[lł]umacz\b|"
    r"\bwyja[sś]nij\b|"
    r"\bdok[lł]adniej\b|"
    r"\bpro[sś]ciej\b|"
    r"\ba\s+dok[lł]adniej\b|"
    r"\bjak\s+to\s+dzia[lł]a\b|"
    r"\bnie\s+kapuj[eę]\b"
    r")",
    re.I,
)

_APP_HINTS = (
    "spotify", "discord", "chrome", "blender", "youtube", "steam", "firefox",
)

_TOPIC_BLOCKLIST = re.compile(
    r"^(?:"
    r"teraz\s+wiem|ju[zż]\s+wiem|rozumiem|okej|ok|dobra|dzięki|dzieki|"
    r"nie\s+rozumiem|aha|to\s+ma\s+sens|zgadzam\s+si[eę]"
    r")[.!…]*$",
    re.I,
)


def empty_focus() -> Dict[str, Any]:
    return {
        "current_topic": "",
        "referenced_people": [],
        "referenced_apps": [],
        "referenced_devices": [],
        "referenced_web_entities": [],
        "last_question_summary": "",
        "last_answer_summary": "",
        "last_web_consensus": "",
        "last_web_query": "",
    }


def is_followup_utterance(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    try:
        from core.local_device_status import looks_local_device_status, refers_to_previous_answer

        if looks_local_device_status(raw) and not refers_to_previous_answer(raw):
            return False
    except Exception:
        pass
    lowered = raw.lower()
    if is_meta_utterance(raw):
        return True
    try:
        from core.factual_followup import is_claim_verification_followup

        if is_claim_verification_followup(raw):
            return True
    except Exception:
        pass
    # Discourse retry / replay / param follow-ups need prior context — same class as "a kiedy?".
    try:
        from core.utterance_class import UtteranceClass, classify_utterance

        cls = classify_utterance(raw)
        if cls.utterance_class in (
            UtteranceClass.RETRY.value,
            UtteranceClass.CONFIRMATION.value,
            UtteranceClass.CONTINUATION.value,
            UtteranceClass.CHANGE_PARAMETER.value,
            UtteranceClass.REFERENCE.value,
            UtteranceClass.CORRECTION.value,
            UtteranceClass.WHY_FAILURE.value,
        ):
            return True
        if cls.needs_active_goal or cls.needs_referent:
            return True
    except Exception:
        pass
    try:
        from core.utterance_class import _EXPECTANT_CONTINUE

        # Shape alone is a follow-up cue; goal binding happens in classify/semantics.
        if _EXPECTANT_CONTINUE.match(raw):
            return True
    except Exception:
        pass
    try:
        from core.soft_goals import _REPLAY_LAST

        if _REPLAY_LAST.search(raw):
            return True
    except Exception:
        pass
    if lowered.startswith("a ") or lowered.startswith("a,"):
        return True
    if _REEXPLAIN.search(lowered):
        return True
    words = lowered.split()
    if len(words) <= 5 and _FOLLOWUP.search(lowered):
        return True
    return False


_NEW_TOPIC_EXPLAIN = re.compile(
    r"(?:"
    r"\b(?:wyja[sś]nij|wyt[lł]umacz)\b.+\b(?:co\s+to|czym\s+jest|co\s+oznacza|co\s+znaczy)\b|"
    r"\b(?:co\s+to|czym\s+jest|co\s+oznacza|co\s+znaczy)\b.+\b(?:wyja[sś]nij|wyt[lł]umacz)\b|"
    r"\b(?:wyja[sś]nij|wyt[lł]umacz)\s+(?:mi\s+)?(?:prosz[eę]\s+)?(?:co\s+to\s+)?(?:jest\s+)?[\wąćęłńóśźż-]{4,}"
    r")",
    re.I,
)


def is_new_topic_knowledge(text: str) -> bool:
    """True when user asks to explain a *new* subject (not 'wyjaśnij jeszcze raz')."""
    raw = str(text or "").strip()
    if not raw:
        return False
    if _NEW_TOPIC_EXPLAIN.search(raw):
        return True
    try:
        from core.utterance_class import UtteranceClass, classify_utterance

        cls = classify_utterance(raw)
        if cls.utterance_class != UtteranceClass.INFORMATION_REQUEST.value:
            return False
        # Bare "co to jest?" / "a ile?" keep prior topic — need contentful subject.
        words = [w for w in re.findall(r"\w+", raw.lower()) if len(w) > 2]
        stop = {
            "co", "to", "jest", "czym", "znaczy", "oznacza", "wyjasnij", "wyjaśnij",
            "wytlumacz", "wytłumacz", "prosze", "proszę", "mi", "mi", "o", "a",
            "jak", "dlaczego", "czemu", "ile", "kiedy", "gdzie", "czy",
        }
        content = [w for w in words if w not in stop]
        return len(content) >= 1 and not is_followup_utterance(raw)
    except Exception:
        return False


def is_comprehension_repair(text: str) -> bool:
    """User did not understand — re-explain last topic (NOT an ACK).

    Bare „wyjaśnij” / „nie rozumiem” repair the prior topic. „Wyjaśnij co to X”
    is a *new* knowledge request and must not inherit prior Osoby/Temat.
    """
    raw = str(text or "").strip()
    if not raw:
        return False
    if is_new_topic_knowledge(raw):
        return False
    return bool(_REEXPLAIN.search(raw))


def clear_focus_topic(focus: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Drop sticky topic/people/answer — cancel + domain switch away from knowledge."""
    data = empty_focus()
    if isinstance(focus, dict):
        # Preserve app/device referents for ACT follow-ups; clear talk poison only.
        data["referenced_apps"] = list(focus.get("referenced_apps") or [])
        data["referenced_devices"] = list(focus.get("referenced_devices") or [])
    return data


def _should_replace_topic(user: str, existing_topic: str) -> bool:
    if not user:
        return False
    if is_new_topic_knowledge(user):
        return True
    if is_meta_utterance(user) or is_comprehension_repair(user):
        return False
    if is_language_only_request(user):
        return False
    if _TOPIC_BLOCKLIST.match(user.strip()):
        return False
    # Freshness questions are new topics even if they start with "kto/ile".
    try:
        from core.freshness import analyze_freshness

        fresh = analyze_freshness(user)
        if fresh.freshness_required and (
            fresh.is_question or user.rstrip().endswith("?")
        ):
            return True
    except Exception:
        pass
    if is_followup_utterance(user) and existing_topic:
        return False
    # Too short / empty of content — keep prior topic.
    words = [w for w in re.findall(r"\w+", user.lower()) if len(w) > 1]
    if existing_topic and len(words) <= 2 and not user.endswith("?"):
        return False
    return True


def _uniq(items: List[str], limit: int = 6) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        key = str(item or "").strip()
        if not key:
            continue
        low = key.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(key)
        if len(out) >= limit:
            break
    return out


def update_focus(
    focus: Dict[str, Any] | None,
    *,
    user_text: str = "",
    assistant_text: str = "",
    entities: Dict[str, Any] | None = None,
    web_consensus: str = "",
    web_query: str = "",
) -> Dict[str, Any]:
    data = dict(empty_focus())
    if isinstance(focus, dict):
        data.update({key: focus.get(key, data.get(key)) for key in data})

    user = str(user_text or "").strip()
    answer = str(assistant_text or "").strip()
    existing = str(data.get("current_topic") or "").strip()

    if user and _should_replace_topic(user, existing):
        data["current_topic"] = user[:160]
        data["last_question_summary"] = user[:160]
        # Fresh knowledge subject — drop prior people/answer poison (MJ bios).
        if is_new_topic_knowledge(user):
            data["referenced_people"] = []
            data["last_answer_summary"] = ""
            data["last_web_consensus"] = ""
            data["last_web_query"] = ""
            data["referenced_web_entities"] = []
    elif user and existing and (
        is_followup_utterance(user)
        or is_comprehension_repair(user)
        or is_meta_utterance(user)
    ):
        data["last_question_summary"] = f"{existing} → {user}"[:200]
    elif user and not existing and not is_meta_utterance(user):
        if not _TOPIC_BLOCKLIST.match(user) and not is_language_only_request(user):
            data["current_topic"] = user[:160]
            data["last_question_summary"] = user[:160]

    if answer:
        data["last_answer_summary"] = answer[:200]
        people = list(data.get("referenced_people") or [])
        people.extend(extract_person_candidates(answer))
        data["referenced_people"] = _uniq(people)

    if user:
        people = list(data.get("referenced_people") or [])
        people.extend(extract_person_candidates(user))
        data["referenced_people"] = _uniq(people)

    if web_consensus:
        data["last_web_consensus"] = str(web_consensus)[:120]
    if web_query:
        data["last_web_query"] = str(web_query)[:160]

    lowered = f"{user} {answer}".lower()
    apps = list(data.get("referenced_apps") or [])
    for name in _APP_HINTS:
        if name in lowered:
            apps.append(name.title() if name != "youtube" else "YouTube")
    data["referenced_apps"] = _uniq(apps)

    if isinstance(entities, dict):
        for key, entity in entities.items():
            if not isinstance(entity, dict):
                continue
            name = str(entity.get("name") or entity.get("canonical_name") or "").strip()
            etype = str(entity.get("type") or key).lower()
            if not name:
                continue
            if "app" in etype:
                data["referenced_apps"] = _uniq(
                    list(data.get("referenced_apps") or []) + [name]
                )
            elif "device" in etype or "light" in etype:
                data["referenced_devices"] = _uniq(
                    list(data.get("referenced_devices") or []) + [name]
                )
            elif "person" in etype or "people" in etype:
                if extract_person_candidates(name) or " " in name:
                    data["referenced_people"] = _uniq(
                        list(data.get("referenced_people") or []) + [name]
                    )

    return data


def enrich_goal_with_focus(goal: str, focus: Dict[str, Any] | None) -> str:
    text = str(goal or "").strip()
    if not text or not isinstance(focus, dict):
        return text
    # New knowledge subject must never inherit prior web people/topic poison.
    if is_new_topic_knowledge(text):
        return text
    try:
        from core.utterance_noise import looks_noise_like

        if looks_noise_like(text):
            return text
    except Exception:
        pass
    try:
        from core.service_open import looks_app_followup, looks_website_followup

        apps = [str(item).strip() for item in (focus.get("referenced_apps") or []) if str(item).strip()]
        if apps:
            name = apps[0]
            if looks_website_followup(text):
                return f"Otwórz stronę {name}."
            if looks_app_followup(text):
                return f"Otwórz aplikację {name}."
    except Exception:
        pass
    try:
        from core.local_device_status import should_exclude_stale_web_focus

        if should_exclude_stale_web_focus(text):
            return text
    except Exception:
        pass
    topic = str(focus.get("current_topic") or "").strip()
    if not topic:
        return text
    if not (
        is_followup_utterance(text)
        or is_comprehension_repair(text)
        or is_meta_utterance(text)
    ):
        return text
    # Meta ACKs ("teraz wiem") — do not dump stale entity noise into the goal.
    if is_meta_utterance(text) and not is_comprehension_repair(text):
        return text
    people = ", ".join(focus.get("referenced_people") or [])
    last = str(focus.get("last_answer_summary") or "").strip()
    parts = [f"Temat: {topic}"]
    if people:
        parts.append(f"Osoby: {people}")
    if last:
        parts.append(f"Ostatnia odpowiedź: {last[:120]}")
    if is_comprehension_repair(text):
        parts.append("Użytkownik nie zrozumiał — wyjaśnij TEN SAM temat prościej/dokładniej.")
    return f"{text}\n(" + "; ".join(parts) + ")"


def knowledge_reexplain_prompt(focus: Dict[str, Any] | None, user_text: str) -> str:
    topic = ""
    last = ""
    if isinstance(focus, dict):
        topic = str(focus.get("current_topic") or focus.get("last_question_summary") or "")
        last = str(focus.get("last_answer_summary") or "")
    topic = topic.strip() or "poprzedni temat"
    user = str(user_text or "").strip()
    return (
        f"Wyjaśnij jeszcze raz, prościej i dokładniej, temat: {topic}. "
        f"Poprzednia odpowiedź (do poprawienia stylu, nie kopiuj błędów): {last[:240]}. "
        f"Prośba użytkownika: {user}. "
        "Odpowiedz po polsku, 2–4 krótkie zdania, zgodnie z faktami."
    )
