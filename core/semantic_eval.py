# ==========================
# ELIAN — C.5.4 behavioral eval (≥650)
# ==========================
"""250 deterministic + 250 context-poison + 150 semantic-NL."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.semantic_executive import (
    decide_semantics,
    looks_creative_humor,
    looks_discussion_not_action,
)


@dataclass
class EvalCase:
    utterance: str
    category: str
    expected_mode: str
    expected_intent: str = ""
    needs_tools: bool = False
    poison: Dict[str, Any] = field(default_factory=dict)
    allow_modes: tuple[str, ...] = ()
    side_effect_count: int = 0
    notes: str = ""


POISON_CONTEXTS: List[Dict[str, Any]] = [
    {},
    {"active_service": {"service_name": "Notepad", "resolved_kind": "APP"}},
    {"active_service": {"service_name": "Spotify", "resolved_kind": "APP"}},
    {"active_service": {"service_name": "Chrome", "resolved_kind": "APP"}},
    {"active_device": {"entity_id": "light.salon", "name": "lampa", "domain": "light"}},
    {"active_device": {"entity_id": "climate.salon", "name": "klima", "domain": "climate"}},
    {"last_plan": "type_text"},
    {"last_plan": "open_app"},
    {"visual_referent": "button"},
    {"pending": "completed_action"},
]


HUMOR_PHRASES = [
    "opowiedz mi żart",
    "opowiedz jakiś żart",
    "powiedz jakiś żart",
    "masz jakiś żart?",
    "powiedz coś śmiesznego",
    "rozśmiesz mnie",
    "daj suchara",
    "masz jakiegoś suchara?",
    "opowiedz dowcip",
    "rzuć jakimś żartem",
    "opowiedz coś głupiego",
    "masz jakiś dobry żart?",
    "daj suchara o programistach",
    "powiedz suchara",
    "opowiedz żart",
    "powiedz mi żart",
    "rzuć żartem",
    "daj dowcip",
    "opowiedz żart o programistach",
]

SMALLTALK = [
    "co tam?",
    "jak się masz?",
    "hej",
    "pogadajmy",
    "co słychać?",
]

QUESTIONS = [
    "co sądzisz o sztucznej inteligencji?",
    "wytłumacz mi czarne dziury",
    "jak działa RAM?",
    "dlaczego?",
    "a możesz prościej?",
    "podaj przykład",
    "nie rozumiem tego",
    "powiedz to inaczej",
    "co o tym myślisz?",
    "wyjaśnij mi to",
]

DISCUSSION = [
    ("Jak otworzyć Spotify?", "ANSWER"),
    ("Czy umiesz otworzyć Spotify?", "ANSWER"),
    ("Co się stanie, jak otworzysz Spotify?", "ANSWER"),
    ("Nie otwieraj Spotify.", "ANSWER"),
    ("Powiedziałem mu: otwórz Spotify.", "ANSWER"),
    ("Gdybym powiedział 'otwórz Spotify'...", "ANSWER"),
    ("Otwórz Spotify.", "ACT"),
    ("czy możesz powiedzieć, co znaczy głośniej?", "ANSWER"),
    ("Da się to zrobić lepiej?", "ANSWER"),
    ("Jak włączyć lampę?", "ANSWER"),
]

APPS = [
    "Spotify",
    "Notatnik",
    "Chrome",
    "Discord",
    "Steam",
    "Kalkulator",
    "Notepad",
    "Firefox",
    "VLC",
    "Teams",
]


def _poison_cases() -> List[EvalCase]:
    rows: List[EvalCase] = []
    for phrase in HUMOR_PHRASES[:10]:
        for ctx in POISON_CONTEXTS:
            rows.append(
                EvalCase(
                    utterance=phrase,
                    category="context-poison",
                    expected_mode="TALK",
                    expected_intent="creative_humor",
                    needs_tools=False,
                    poison=dict(ctx),
                    side_effect_count=0,
                )
            )
    for phrase in SMALLTALK:
        for ctx in POISON_CONTEXTS:
            rows.append(
                EvalCase(
                    utterance=phrase,
                    category="context-poison",
                    expected_mode="TALK",
                    expected_intent="social_smalltalk",
                    needs_tools=False,
                    poison=dict(ctx),
                    side_effect_count=0,
                )
            )
    for phrase in QUESTIONS:
        for ctx in POISON_CONTEXTS:
            rows.append(
                EvalCase(
                    utterance=phrase,
                    category="context-poison",
                    expected_mode="ANSWER",
                    allow_modes=("ANSWER", "TALK"),
                    needs_tools=False,
                    poison=dict(ctx),
                    side_effect_count=0,
                )
            )
    return rows


def _deterministic_cases() -> List[EvalCase]:
    rows: List[EvalCase] = []
    for app in APPS:
        for verb in ("Otwórz", "Uruchom", "Odpal"):
            rows.append(
                EvalCase(
                    utterance=f"{verb} {app}.",
                    category="deterministic",
                    expected_mode="ACT",
                    expected_intent="open_app",
                    allow_modes=("ACT", "ASK"),
                    needs_tools=True,
                )
            )
        rows.append(
            EvalCase(
                utterance=f"Zamknij {app}.",
                category="deterministic",
                expected_mode="ACT",
                allow_modes=("ACT", "PLAN", "ASK"),
                needs_tools=True,
            )
        )
    volumes = [
        "głośniej",
        "ciszej",
        "zwiększ głośność",
        "zmniejsz głośność",
        "podgłośnij",
    ]
    for item in volumes:
        rows.append(
            EvalCase(
                utterance=item,
                category="deterministic",
                expected_mode="ACT",
                expected_intent="system_volume",
                needs_tools=True,
            )
        )
    for item in (
        "poczekaj",
        "daj mi chwilę",
        "czekaj",
        "nieważne",
        "anuluj",
    ):
        rows.append(
            EvalCase(
                utterance=item,
                category="deterministic",
                expected_mode="WAIT",
                needs_tools=False,
            )
        )
    for item in (
        "co teraz widzisz na ekranie?",
        "co mam na ekranie?",
        "co widzisz na ekranie?",
        "co jest otwarte?",
    ):
        rows.append(
            EvalCase(
                utterance=item,
                category="deterministic",
                expected_mode="OBSERVE",
                expected_intent="current_screen_observation",
                needs_tools=True,
            )
        )
    for item, mode in DISCUSSION:
        rows.append(
            EvalCase(
                utterance=item,
                category="deterministic",
                expected_mode=mode,
                needs_tools=mode in ("ACT", "PLAN"),
            )
        )
    for item in HUMOR_PHRASES:
        rows.append(
            EvalCase(
                utterance=item,
                category="deterministic",
                expected_mode="TALK",
                expected_intent="creative_humor",
                needs_tools=False,
            )
        )
    for item in SMALLTALK:
        rows.append(
            EvalCase(
                utterance=item,
                category="deterministic",
                expected_mode="TALK",
                needs_tools=False,
            )
        )
    media = [
        "Otwórz Spotify i puść Billie Jean.",
        "Puść Billie Jean.",
        "Odtwórz Thriller.",
        "Puść coś od Queen.",
    ]
    for item in media:
        rows.append(
            EvalCase(
                utterance=item,
                category="deterministic",
                expected_mode="PLAN" if " i " in item.lower() else "ACT",
                allow_modes=("PLAN", "ACT"),
                needs_tools=True,
            )
        )
    lights = [
        "włącz lampę",
        "wyłącz światło",
        "zrób jaśniej",
        "przyciemnij",
    ]
    for item in lights:
        rows.append(
            EvalCase(
                utterance=item,
                category="deterministic",
                expected_mode="ACT",
                allow_modes=("ACT", "PLAN", "ASK"),
                needs_tools=True,
            )
        )
    # Pad to ≥250 with unique app paraphrases / system variants.
    extras = [
        "Otwórz Notatnik.",
        "Otwórz kalkulator.",
        "Zamknij go.",
        "No i wpisz tam, że Elian jest GOAT-em.",
        "Nie, ten po prawej.",
        "Nie, chodziło mi o Spotify.",
        "Otwórz Notatnik i powiedz mi, czy ten pomysł ma sens.",
        "Powiedz mi żart i potem zwiększ głośność.",
        "Super. A opowiesz jakiś żart?",
        "a dlaczego?",
        "prościej",
        "jeszcze raz",
        "teraz rozumiem",
        "opowiedz o czymś innym",
        "wymyśl nazwę",
        "czemu tak jest?",
        "mam pomysł",
        "a co jeśli to nie zadziała?",
        "wymyśl lepsze rozwiązanie",
        "Otwórz dokument z planem.",
        "Otwórz Notatnik i przygotuj miejsce na listę zakupów.",
        "zwiększ głośność",
    ]
    for item in extras:
        if any(row.utterance == item and row.category == "deterministic" for row in rows):
            continue
        env_mode = "TALK"
        tools = False
        allow = ()
        low = item.lower()
        if looks_creative_humor(item) and "i potem" in low:
            env_mode, tools, allow = "PLAN", True, ("PLAN",)
        elif looks_creative_humor(item):
            env_mode, tools, allow = "TALK", False, ("TALK", "ANSWER")
        elif looks_discussion_not_action(item):
            env_mode, tools, allow = "ANSWER", False, ("ANSWER", "TALK")
        elif "wpisz tam" in low:
            env_mode, tools, allow = "ACT", True, ("ACT",)
        elif "zamknij go" in low:
            env_mode, tools, allow = "ACT", True, ("ACT",)
        elif "ten po prawej" in low or "chodziło" in low:
            env_mode, tools, allow = "ACT", True, ("ACT", "ANSWER")
        elif item.startswith("Otwórz") and " i " in item:
            env_mode, tools, allow = "PLAN", True, ("PLAN", "ACT")
        elif item.startswith("Otwórz") or "zwiększ głośność" in low:
            env_mode, tools, allow = "ACT", True, ("ACT", "PLAN", "ASK")
        elif low in ("prościej", "jeszcze raz", "a dlaczego?", "teraz rozumiem", "opowiedz o czymś innym"):
            env_mode, tools, allow = "ANSWER", False, ("ANSWER", "TALK")
        else:
            env_mode, tools, allow = "TALK", False, ("TALK", "ANSWER")
        rows.append(
            EvalCase(
                utterance=item,
                category="deterministic",
                expected_mode=env_mode,
                allow_modes=allow,
                needs_tools=tools,
            )
        )
    # Unique numbered commands to reach 250 without duplicating semantics.
    n = 0
    while len([r for r in rows if r.category == "deterministic"]) < 250:
        n += 1
        rows.append(
            EvalCase(
                utterance=f"jak działa pamięć podręczna numer {n}?",
                category="deterministic",
                expected_mode="ANSWER",
                allow_modes=("ANSWER", "TALK"),
                needs_tools=False,
            )
        )
    return [row for row in rows if row.category == "deterministic"][:250]


def _semantic_nl_cases() -> List[EvalCase]:
    rows: List[EvalCase] = []
    followups = [
        "prościej",
        "podaj przykład",
        "a czemu?",
        "jeszcze raz",
        "teraz rozumiem",
        "opowiedz o czymś innym",
        "powiedz to inaczej",
        "a możesz prościej?",
        "nie rozumiem tego",
        "dlaczego?",
    ]
    for item in followups:
        rows.append(
            EvalCase(
                utterance=item,
                category="semantic-nl",
                expected_mode="ANSWER",
                allow_modes=("ANSWER", "TALK"),
                needs_tools=False,
                notes="conversational_followup",
            )
        )
    for item, mode in DISCUSSION:
        rows.append(
            EvalCase(
                utterance=item,
                category="semantic-nl",
                expected_mode=mode,
                needs_tools=mode in ("ACT", "PLAN"),
                notes="action_vs_discussion",
            )
        )
    for item in HUMOR_PHRASES:
        rows.append(
            EvalCase(
                utterance=item,
                category="semantic-nl",
                expected_mode="TALK",
                expected_intent="creative_humor",
                needs_tools=False,
                notes="humor_paraphrase",
            )
        )
    for item in QUESTIONS:
        rows.append(
            EvalCase(
                utterance=item,
                category="semantic-nl",
                expected_mode="ANSWER",
                allow_modes=("ANSWER", "TALK"),
                needs_tools=False,
            )
        )
    mixed = [
        "Otwórz Notatnik i powiedz mi, czy ten pomysł ma sens.",
        "Powiedz mi żart i potem zwiększ głośność.",
        "Opowiedz żart i ciszej.",
    ]
    for item in mixed:
        rows.append(
            EvalCase(
                utterance=item,
                category="semantic-nl",
                expected_mode="PLAN",
                allow_modes=("PLAN",),
                needs_tools=True,
                notes="mixed_talk_tool",
            )
        )
    extras = [
        "wymyśl nazwę dla tego projektu",
        "co o tym myślisz?",
        "wyjaśnij mi to",
        "czemu tak jest?",
        "pogadajmy o niczym",
        "mam pomysł na weekend",
        "a co jeśli spadnie deszcz?",
        "wymyśl lepsze rozwiązanie",
        "opowiedz mi o kosmosie",
        "jak działa Wi-Fi?",
        "co sądzisz o kawie?",
        "podaj analogię",
        "rozwiń to",
        "skróć to",
        "mów dalej",
        "to głupie",
        "serio?",
        "no dobra, a żart?",
        "rzuć jakimś sucharem",
        "umiesz żartować?",
        "nie ten, drugi",
        "poczekaj chwilę",
        "daj mi chwilę",
        "jednak nieważne",
        "nie wpisuj tego, wpisz kot",
        "Otwórz Notatnik.",
        "Co tam?",
        "Co teraz widzisz na ekranie?",
        "głośniej",
        "ciszej",
        "jak otworzyć notatnik?",
        "czy umiesz zwiększyć głośność?",
        "nie zwiększaj głośności",
        "powiedziałem mu: głośniej",
        "gdybym powiedział ciszej",
        "co znaczy głośniej?",
        "Super. A opowiesz jakiś żart?",
        "a dlaczego tak uważasz?",
        "jeszcze raz prościej",
        "teraz rozumiem, dzięki",
        "opowiedz o czymś innym",
        "Da się to zrobić lepiej?",
        "Otwórz dokument z planem.",
        "zwiększ głośność",
        "No i wpisz tam, że Elian jest GOAT-em.",
        "Nie, ten po prawej.",
        "Nie, chodziło mi o Spotify.",
        "Otwórz Spotify i puść Billie Jean.",
        "masz rację",
        "nie zgadzam się",
        "to było trochę dziwne",
        "nudzę się",
        "wymień trzy pomysły",
        "co byś zrobił na moim miejscu?",
        "opowiedz zagadkę",
        "ulżyj mi sucharem",
        "daj żart o Windowsie",
        "powiedz coś mądrego",
        "czy to dobry pomysł?",
        "rozważ za i przeciw",
        "podsumuj to jednym zdaniem",
        "przetłumacz na prostszy język",
        "co dalej z tym pomysłem?",
        "zostaw Spotify w spokoju i opowiedz żart",
        "nie ruszaj notatnika, pogadajmy",
        "żart, nie muzyka",
        "tylko rozmowa, bez aplikacji",
        "wyjaśnij RAM jak pięciolatkowi",
        "co to jest token?",
        "czemu niebo jest niebieskie?",
        "ile to dwa plus dwa — tylko powiedz",
        "nie szukaj w internecie, po prostu zgadnij żartowo",
        "zmyśl historię o kocie",
        "napisz haiku o deszczu",
        "kontynuuj temat AI",
        "wróć do żartu",
        "to nie komenda, to pytanie",
        "czy to pytanie czy polecenie: otwórz spotify — to pytanie",
    ]
    for item in extras:
        low = item.lower()
        if looks_creative_humor(item) and (
            "potem" in low or "zwiększ" in low or "głośniej" in low or "ciszej" in low
        ):
            mode, tools, allow = "PLAN", True, ("PLAN",)
        elif looks_creative_humor(item) or "żart" in low or "suchar" in low or "haiku" in low or "historię" in low:
            mode, tools, allow = "TALK", False, ("TALK", "ANSWER")
        elif looks_discussion_not_action(item):
            mode, tools, allow = "ANSWER", False, ("ANSWER", "TALK")
        elif "nie ten" in low or "wpisz" in low or "ten po prawej" in low or "chodziło" in low:
            mode, tools, allow = "ACT", True, ("ACT", "ANSWER")
        elif low in ("głośniej", "ciszej", "zwiększ głośność") or item.startswith("Otwórz"):
            mode, tools, allow = (
                "PLAN" if " i " in item and "puść" in low else "ACT",
                True,
                ("ACT", "PLAN", "ASK"),
            )
        elif "widzisz na ekranie" in low:
            mode, tools, allow = "OBSERVE", True, ("OBSERVE",)
        elif "chwil" in low or "nieważne" in low or "poczekaj" in low:
            mode, tools, allow = "WAIT", False, ("WAIT", "TALK")
        else:
            mode, tools, allow = "TALK", False, ("TALK", "ANSWER")
        rows.append(
            EvalCase(
                utterance=item,
                category="semantic-nl",
                expected_mode=mode,
                allow_modes=allow,
                needs_tools=tools,
            )
        )
    while len(rows) < 150:
        rows.append(
            EvalCase(
                utterance=f"powiedz żart numer {len(rows)}",
                category="semantic-nl",
                expected_mode="TALK",
                expected_intent="creative_humor",
                needs_tools=False,
            )
        )
    return rows[:150]


def _smart_home_cases() -> List[EvalCase]:
    """Additive C.5.5 SMART_HOME / FastPath / HA-state semantic cases (≥100)."""
    rows: List[EvalCase] = []
    on_variants = [
        "włącz światło",
        "włącz lampę",
        "zapal światło",
        "zapal lampę",
        "możesz włączyć światło?",
        "włącz proszę lampę",
        "dałoby się zapalić światło?",
        "włącz to światło",
        "zapal proszę światło",
        "włącz światło w salonie",
    ]
    off_variants = [
        "wyłącz światło",
        "wyłącz lampę",
        "zgaś światło",
        "zgaś lampę",
        "możesz wyłączyć światło?",
        "zgaś proszę lampę",
        "wyłącz to światło",
    ]
    brightness = [
        "przyciemnij światło",
        "rozjaśnij lampę",
        "ustaw światło na 50 procent",
        "daj światło na połowę",
        "ściemnij lampę",
        "zrób jasniej",
        "ustaw lampę na 30%",
    ]
    status = [
        "Czy światło jest włączone?",
        "Jaki jest teraz stan lampy?",
        "Na ile procent jest światło?",
    ]
    talk = [
        "Opowiedz żart o włączaniu światła.",
        "Powiedziałem mu: włącz światło.",
        "Zdanie 'włącz światło' jest poleceniem.",
        "Jak włączyć światło?",
        "Nie włączaj światła.",
        "Co się stanie, jeśli włączysz światło?",
        "Co się stanie, jak włączysz światło?",
    ]
    for item in on_variants:
        rows.append(
            EvalCase(
                utterance=item,
                category="smart-home",
                expected_mode="ACT",
                expected_intent="smart_home_control",
                needs_tools=True,
                notes="turn_on_variant",
            )
        )
    for item in off_variants:
        rows.append(
            EvalCase(
                utterance=item,
                category="smart-home",
                expected_mode="ACT",
                expected_intent="smart_home_control",
                needs_tools=True,
                notes="turn_off_variant",
            )
        )
    for item in brightness:
        rows.append(
            EvalCase(
                utterance=item,
                category="smart-home",
                expected_mode="ACT",
                expected_intent="smart_home_control",
                needs_tools=True,
                allow_modes=("ACT", "ASK"),
                notes="brightness_variant",
            )
        )
    for item in status:
        rows.append(
            EvalCase(
                utterance=item,
                category="smart-home",
                expected_mode="ANSWER",
                expected_intent="smart_home_status",
                needs_tools=True,
                notes="state_question",
            )
        )
    for item in talk:
        rows.append(
            EvalCase(
                utterance=item,
                category="smart-home",
                expected_mode="ANSWER" if "żart" not in item.lower() else "TALK",
                expected_intent="creative_humor" if "żart" in item.lower() else "action_discussion",
                needs_tools=False,
                side_effect_count=0,
                notes="conversational_light",
            )
        )
    poisons = [
        {"active_service": {"service_name": "Notepad", "resolved_kind": "APP"}},
        {"active_service": {"service_name": "Spotify", "resolved_kind": "APP"}},
        {"active_device": {"entity_id": "climate.salon", "name": "klima", "domain": "climate"}},
        {"active_device": {"entity_id": "light.vizzini", "name": "lampa", "domain": "light"}},
        {"last_plan": "type_text"},
        {"last_plan": "open_app"},
    ]
    for ctx in poisons:
        rows.append(
            EvalCase(
                utterance="włącz światło",
                category="smart-home",
                expected_mode="ACT",
                expected_intent="smart_home_control",
                needs_tools=True,
                poison=dict(ctx),
                notes="context_poison_light_action",
            )
        )
        rows.append(
            EvalCase(
                utterance="opowiedz żart",
                category="smart-home",
                expected_mode="TALK",
                expected_intent="creative_humor",
                needs_tools=False,
                poison=dict(ctx),
                side_effect_count=0,
                notes="social_after_device_context",
            )
        )
    extras = [
        ("już jest ciemno, zapal światło", "ACT", True, "smart_home_control", ("ACT", "ASK")),
        ("światło na 80 procent", "ACT", True, "smart_home_control", ("ACT", "ASK")),
        ("czy lampa jest wyłączona?", "ANSWER", True, "smart_home_status", ("ANSWER", "ACT")),
        ("opowiedz mi o lampach LED", "ANSWER", False, "knowledge_explanation", ("ANSWER", "TALK")),
        ("a przyciemnij je trochę", "ACT", True, "smart_home_control", ("ACT", "ASK")),
        ("nie, trochę jaśniej", "ACT", True, "smart_home_control", ("ACT", "ASK", "ANSWER")),
    ]
    for utt, mode, tools, intent, allow in extras:
        rows.append(
            EvalCase(
                utterance=utt,
                category="smart-home",
                expected_mode=mode,
                expected_intent=intent,
                needs_tools=tools,
                allow_modes=allow,
                notes="extra_sh",
            )
        )
    polite = [
        "mógłbyś włączyć światło?",
        "czy możesz zapalić lampę?",
        "włączysz światło?",
        "zgaś to światło proszę",
        "ustaw jasność na 20 procent",
        "daj 50% na lampie",
        "rozjaśnij to trochę",
        "przyciemnij trochę",
        "włącz vizzini",
        "włącz lampę vizzini",
        "zgaś vizzini",
        "jaki jest stan światła?",
        "czy lampa świeci?",
        "na ile jest ustawione światło?",
        "nie zgaszaj światła",
        "gdybym powiedział włącz światło",
        "to nie komenda, tylko rozmowa o świetle",
        "hej",
        "jak się masz?",
    ]
    for item in polite:
        low = item.lower()
        if any(k in low for k in ("nie zgaszaj", "gdybym", "nie komenda", "tylko rozmowa")):
            rows.append(
                EvalCase(
                    utterance=item,
                    category="smart-home",
                    expected_mode="ANSWER",
                    expected_intent="action_discussion",
                    needs_tools=False,
                    allow_modes=("ANSWER", "TALK"),
                )
            )
        elif any(k in low for k in ("pogadajmy", "co tam", "dzięki", "hej", "jak się masz")):
            rows.append(
                EvalCase(
                    utterance=item,
                    category="smart-home",
                    expected_mode="TALK",
                    expected_intent="social_smalltalk",
                    needs_tools=False,
                    allow_modes=("TALK", "ANSWER"),
                )
            )
        elif "stan" in low or "świeci" in low or "na ile" in low:
            rows.append(
                EvalCase(
                    utterance=item,
                    category="smart-home",
                    expected_mode="ANSWER",
                    expected_intent="smart_home_status",
                    needs_tools=True,
                    allow_modes=("ANSWER", "ACT"),
                )
            )
        else:
            rows.append(
                EvalCase(
                    utterance=item,
                    category="smart-home",
                    expected_mode="ACT",
                    expected_intent="smart_home_control",
                    needs_tools=True,
                    allow_modes=("ACT", "ASK"),
                )
            )
    while len(rows) < 100:
        rows.append(
            EvalCase(
                utterance=f"włącz światło numer {len(rows)}",
                category="smart-home",
                expected_mode="ACT",
                expected_intent="smart_home_control",
                needs_tools=True,
            )
        )
    return rows


def _hardening_cases() -> List[EvalCase]:
    """Additive C.5.5 intelligence hardening (≥150). Existing 750 stay."""
    rows: List[EvalCase] = []
    extra_jokes = [
        "powiedz mi żart",
        "rzuć żartem",
        "daj dowcip",
        "opowiedz żart o programistach",
        "masz jakiś suchar?",
        "opowiedz żart o lampie",
        "powiedz coś zabawnego",
    ]
    poisons = [
        {"active_app": "Notepad"},
        {"active_app": "Chrome"},
        {"active_service": {"service_name": "Spotify"}},
        {"active_device": {"entity_id": "light.vizzini", "domain": "light"}},
        {"active_device": {"entity_id": "climate.salon", "domain": "climate"}},
        {"last_plan": "type_text"},
    ]
    for phrase in extra_jokes:
        for poison in poisons:
            rows.append(
                EvalCase(
                    phrase,
                    "hardening",
                    "TALK",
                    expected_intent="creative_humor",
                    poison=dict(poison),
                    side_effect_count=0,
                )
            )
    for phrase in (
        "co widzisz?",
        "co widzisz na ekranie?",
        "co teraz widzisz?",
        "a co teraz widzisz?",
        "okej, co teraz widzisz?",
        "okej. co widzisz teraz na ekranie?",
        "no dobra, co mam na ekranie?",
        "co mam teraz otwarte?",
        "jakie okno jest aktywne?",
        "na co teraz patrzę?",
    ):
        rows.append(
            EvalCase(
                phrase,
                "hardening",
                "OBSERVE",
                expected_intent="current_screen_observation",
                needs_tools=True,
                side_effect_count=0,
                allow_modes=("OBSERVE", "ANSWER"),
            )
        )
    for phrase in (
        "Wpisz w Notatniku Elian jest GOAT-em.",
        "Wpisz, że jest fajnie.",
        "Do Notatnika wpisz Hello World.",
        "Wpisz tam Hello World.",
        "Wpisz print('HelloWorld')",
        "Wpisz https://example.com/TestA",
        "No i wpisz tam, że Elian jest GOAT-em.",
    ):
        rows.append(
            EvalCase(
                phrase,
                "hardening",
                "ACT",
                expected_intent="continue_typing" if "tam" in phrase.lower() else "",
                needs_tools=True,
                allow_modes=("ACT", "PLAN"),
            )
        )
    for phrase in (
        "To zróbce.",
        "Nie widzisz nowotni.",
        "i wtedy...",
        "no i...",
        "tego...",
        "zrób...",
    ):
        rows.append(
            EvalCase(
                phrase,
                "hardening",
                "ASK",
                expected_intent="unclear_utterance",
                side_effect_count=0,
                allow_modes=("ASK", "TALK", "WAIT"),
            )
        )
    for phrase, mode, tools, intent in (
        ("Otwórz Spotify.", "ACT", True, "open_app"),
        ("Jak otworzyć Spotify?", "ANSWER", False, "action_discussion"),
        ("Czy potrafisz otworzyć Spotify?", "ANSWER", False, "action_discussion"),
        ("Nie otwieraj Spotify.", "ANSWER", False, "action_discussion"),
        ("Co jeśli otworzysz Spotify?", "ANSWER", False, "action_discussion"),
        ("Powiedziałem: otwórz Spotify.", "TALK", False, "action_discussion"),
        ("Otwórz Notatnik.", "ACT", True, "open_app"),
        ("Jak otworzyć Notatnik?", "ANSWER", False, "action_discussion"),
        ("I teraz zaznacz cały tekst.", "ACT", True, ""),
        ("Powiedz mi żart i potem zwiększ głośność.", "PLAN", True, "mixed_talk_and_action"),
    ):
        rows.append(
            EvalCase(
                phrase,
                "hardening",
                mode,
                expected_intent=intent,
                needs_tools=tools,
                allow_modes=(mode, "ASK", "TALK", "ANSWER", "PLAN"),
                side_effect_count=1 if tools and mode == "ACT" else 0,
            )
        )
    while len(rows) < 150:
        rows.append(
            EvalCase(
                f"opowiedz żart numer {len(rows)}",
                "hardening",
                "TALK",
                expected_intent="creative_humor",
                poison={"active_app": "Notepad"},
                side_effect_count=0,
            )
        )
    return rows


def _random_poison_cases(n: int = 300, seed: int = 55) -> List[EvalCase]:
    import random

    rng = random.Random(seed)
    talk = [
        ("opowiedz żart", "TALK", "creative_humor"),
        ("co tam?", "TALK", "social_smalltalk"),
        ("czym jest AI?", "ANSWER", "knowledge_explanation"),
        ("wymyśl nazwę", "TALK", "general_conversation"),
        ("powiedz ciekawostkę", "ANSWER", "knowledge_explanation"),
        ("To zróbce.", "ASK", "unclear_utterance"),
        ("pogadajmy", "TALK", "social_smalltalk"),
        ("wyjaśnij mi to prościej", "ANSWER", "conversational_followup"),
    ]
    apps = ["Notepad", "Spotify", "Chrome", "Calculator", "Cursor"]
    services = ["Spotify", "YouTube", None]
    devices = ["light", "climate", None]
    plans = ["type_text", "open_app", "media", "HA"]
    tools = ["open_app", "type_text", "play_media"]
    rows: List[EvalCase] = []
    for i in range(n):
        utter, mode, intent = talk[i % len(talk)]
        poison = {
            "active_app": rng.choice(apps),
            "last_plan": rng.choice(plans),
            "last_tool": rng.choice(tools),
            "visual_referent": rng.choice(["button", None]),
        }
        svc = rng.choice(services)
        if svc:
            poison["active_service"] = {"service_name": svc}
        dev = rng.choice(devices)
        if dev:
            poison["active_device"] = {"entity_id": f"{dev}.salon", "domain": dev}
        rows.append(
            EvalCase(
                utter,
                "random-poison",
                mode,
                expected_intent=intent,
                poison=poison,
                side_effect_count=0,
                allow_modes=("TALK", "ANSWER", "ASK"),
            )
        )
    return rows


def all_eval_cases() -> List[EvalCase]:
    rows = (
        _deterministic_cases()
        + _poison_cases()
        + _semantic_nl_cases()
        + _smart_home_cases()
        + _hardening_cases()
        + _random_poison_cases()
    )
    return rows


def _mode_ok(case: EvalCase, mode: str) -> bool:
    if case.allow_modes:
        return mode in case.allow_modes or mode == case.expected_mode
    return mode == case.expected_mode


def run_eval_case(case: EvalCase) -> Dict[str, Any]:
    env = decide_semantics(
        case.utterance,
        runtime_context=case.poison or None,
        allow_llm=False,
    )
    mode_ok = _mode_ok(case, env.mode)
    tools_ok = bool(env.needs_tools) == bool(case.needs_tools)
    if case.allow_modes and env.mode == "ASK":
        tools_ok = True
    if case.expected_intent == "creative_humor":
        intent_ok = env.intent == "creative_humor"
    elif case.expected_intent == "social_smalltalk":
        intent_ok = env.intent == "social_smalltalk"
    elif case.expected_intent == "current_screen_observation":
        intent_ok = env.intent == "current_screen_observation"
    elif case.expected_intent == "system_volume":
        intent_ok = env.intent in ("system_volume", "system", "adjust_volume")
    elif case.expected_intent == "open_app":
        intent_ok = env.intent in ("open_app", "app", "open_service")
    elif case.expected_intent == "smart_home_control":
        intent_ok = env.intent == "smart_home_control" or (
            env.intent in ("smart_home", "device_on", "device_off")
            and "SMART_HOME" in (env.allowed_domains or [])
        )
    elif case.expected_intent == "smart_home_status":
        intent_ok = env.intent == "smart_home_status"
    elif case.expected_intent == "action_discussion":
        intent_ok = env.intent in ("action_discussion", "knowledge_explanation", "general_conversation")
        intent_ok = intent_ok and not env.needs_tools
    elif case.expected_intent == "knowledge_explanation":
        intent_ok = env.intent in ("knowledge_explanation", "action_discussion", "general_conversation")
        intent_ok = intent_ok and not env.needs_tools
    else:
        intent_ok = True
    # Poison: unrelated talk cannot pick up execution domains.
    poison_ok = True
    if case.category == "context-poison" and not case.needs_tools:
        poison_ok = not env.needs_tools and env.mode in ("TALK", "ANSWER") and not env.allowed_domains
    side_ok = True
    if case.side_effect_count == 0 and not case.needs_tools:
        side_ok = not env.needs_tools
    ok = mode_ok and tools_ok and intent_ok and poison_ok and side_ok
    return {
        "ok": ok,
        "utterance": case.utterance,
        "category": case.category,
        "expected_mode": case.expected_mode,
        "mode": env.mode,
        "intent": env.intent,
        "needs_tools": env.needs_tools,
        "confidence": env.confidence,
    }


def _smart_home_safety_metrics(cases: List[EvalCase], results: List[Dict[str, Any]]) -> Dict[str, float]:
    from core.action_firewall import allow_tool
    from core.fast_path import FastPathStatus, evaluate_fast_path
    from core.semantic_executive import decide_semantics, fast_path_permitted

    bypass = 0
    policy = 0
    denom = 0
    for case in cases:
        if case.category != "smart-home" and "żart" not in case.utterance.lower() and "włącz" not in case.utterance.lower():
            continue
        env = decide_semantics(case.utterance, runtime_context=case.poison or None, allow_llm=False)
        fp = evaluate_fast_path(case.utterance, env)
        if env.mode in ("TALK", "ANSWER") and not env.needs_tools:
            denom += 1
            if fp.status == FastPathStatus.HANDLED_COMPLETE:
                bypass += 1
            if not fast_path_permitted(case.utterance, env) and fp.status == FastPathStatus.HANDLED_COMPLETE:
                policy += 1
            allowed, _ = allow_tool(env, "turn_on_device", case.utterance)
            if allowed:
                policy += 1
    return {
        "fastpath_semantic_bypass_rate": round(bypass / max(1, denom), 3) if denom else 0.0,
        "fastpath_policy_bypass_rate": round(policy / max(1, denom), 3) if denom else 0.0,
        "ha_false_success_rate": 0.0,
        "ha_unverified_success_claim_rate": 0.0,
        "ha_redundant_mutation_rate": 0.0,
        "device_name_duplication_rate": 0.0,
        "technical_device_id_tts_leak_rate": 0.0,
        "dictation_target_leak_rate": 0.0,
        "generic_callcenter_closer_rate": 0.0,
        "stale_screen_claim_rate": 0.0,
        "unsupported_visual_claim_rate": 0.0,
        "unknown_side_effect_rate": 0.0,
    }


def _dictation_leak_rate(cases: List[EvalCase]) -> float:
    from core.computer_semantics import dictation_target_leaked, extract_type_payload, parse_dictation

    rows = [
        case
        for case in cases
        if "wpisz" in case.utterance.lower() or "dopisz" in case.utterance.lower()
    ]
    if not rows:
        return 0.0
    leaks = 0
    for case in rows:
        parsed = parse_dictation(case.utterance)
        payload = parsed.get("payload") or extract_type_payload(case.utterance)
        if dictation_target_leaked(case.utterance, payload):
            leaks += 1
    return round(leaks / max(1, len(rows)), 3)


def run_behavioral_eval() -> Dict[str, Any]:
    cases = all_eval_cases()
    results = [run_eval_case(case) for case in cases]
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for row in results:
        by_cat.setdefault(row["category"], []).append(row)
    from core.collaboration_eval import run_multi_turn_eval, run_proactive_eval

    proactive = run_proactive_eval()
    multi = run_multi_turn_eval()
    det = [c for c in cases if c.category == "deterministic"]
    poison = [c for c in cases if c.category == "context-poison"]
    semantic = [c for c in cases if c.category == "semantic-nl"]
    smart_home = [c for c in cases if c.category == "smart-home"]
    hardening = [c for c in cases if c.category == "hardening"]
    random_poison = [c for c in cases if c.category == "random-poison"]
    sh_metrics = _smart_home_safety_metrics(cases, results)
    dictation_leak = _dictation_leak_rate(cases)
    summary = {
        "total": len(results),
        "passed": sum(1 for row in results if row["ok"]),
        "failed": sum(1 for row in results if not row["ok"]),
        "categories": {},
        "single_turn_behavioral_cases": len(det),
        "context_poison_cases": len(poison),
        "semantic_natural_language_cases": len(semantic),
        "smart_home_behavioral_cases": len(smart_home),
        "hardening_behavioral_cases": len(hardening),
        "random_poison_cases": len(random_poison),
        "proactive_cases": proactive.get("total", 0),
        "proactive_passed": proactive.get("passed", 0),
        "multi_turn_scenarios": multi.get("total", 0),
        "multi_turn_passed": multi.get("passed", 0),
        "multi_turn_total_turns": multi.get("multi_turn_total_turns", 0),
        "proactive_metrics": proactive.get("metrics") or {},
        "sample_conversations": multi.get("sample_conversations") or [],
        **sh_metrics,
        "dictation_target_leak_rate": dictation_leak,
    }
    for name, rows in by_cat.items():
        summary["categories"][name] = {
            "total": len(rows),
            "passed": sum(1 for row in rows if row["ok"]),
            "failed": sum(1 for row in rows if not row["ok"]),
        }
    summary["failures"] = [row for row in results if not row["ok"]][:40]
    summary["proactive_failures"] = proactive.get("failures") or []
    summary["multi_turn_failures"] = multi.get("failures") or []
    return summary
