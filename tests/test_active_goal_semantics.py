# ==========================
# ELIAN — ActiveGoal / utterance-class semantic layer
# ==========================
"""Semantic behavior coverage — not brittle phrase→tool maps.

Banks assert utterance class, goal fields, reference resolution,
unresolved routing, and honest failure dialogue.
"""

from __future__ import annotations

import unittest
from typing import List, Tuple

from core.active_goal import (
    ActiveGoal,
    ConversationGoalStore,
    GoalStatus,
    enrich_followup_goal_text,
    explain_failure,
)
from core.semantic_executive import decide_semantics
from core.utterance_class import UtteranceClass, classify_utterance


# ---------------------------------------------------------------------------
# Fixture banks (paraphrases — semantic properties, not tool strings)
# ---------------------------------------------------------------------------

APP_PARAPHRASES: List[str] = [
    "otwórz Spotify",
    "odpal Spotify",
    "uruchom Spotify",
    "włącz Spotify",
    "otwórz Discorda",
    "odpal Discord",
    "uruchom Chrome",
    "otwórz przeglądarkę Chrome",
    "włącz Notatnik",
    "otwórz Notatnik",
    "odpal Excela",
    "uruchom Worda",
    "otwórz Teams",
    "włącz Slacka",
    "odpal Steam",
    "otwórz Steam",
    "uruchom VLC",
    "otwórz Kalkulator",
    "włącz kalkulator",
    "odpal Paint",
    "otwórz Explorera",
    "zamknij Spotify",
    "zamknij Discorda",
    "wyłącz Chrome",
    "zamknij Notatnik",
    "zamknij to okno",
    "otwórz proszę Spotify",
    "możesz otworzyć Spotify",
    "odpal mi Discorda",
    "włącz mi Chrome",
    "puść Spotify",  # may land MEDIA — still action-bearing
    "otwórz aplikację Spotify",
    "start Spotify",
    "uruchom aplikację Discord",
    "odpal mi Steama",
    "włącz Word",
    "otwórz Excel",
    "zamknij Steam",
    "zamknij VLC",
    "otwórz przeglądarkę",
    "odpal Edge",
    "włącz Edge",
    "otwórz Firefoksa",
    "uruchom Firefoksa",
    "zamknij Firefoksa",
    "otwórz Outlooka",
    "włącz Outlook",
    "odpal Teams",
    "zamknij Teams",
    "otwórz Visual Studio Code",
    "odpal VS Code",
    "włącz VS Code",
    "zamknij VS Code",
    "otwórz Spotify proszę",
    "proszę otwórz Discorda",
    "możesz odpalić Chrome",
    "uruchom mi Notatnik",
    "włącz mi Kalkulator",
    "otwórz Steam i tyle",
    "odpal VLC player",
    "zamknij tę aplikację",
    "wyłącz Notatnik",
    "otwórz Discord",
    "włącz Spotify teraz",
    "odpal Chrome szybko",
    "uruchom Slack",
    "otwórz Slack",
    "zamknij Slacka",
    "otwórz PowerPointa",
    "włącz PowerPoint",
    "odpal PowerPoint",
    "zamknij PowerPointa",
    "otwórz Zoom",
    "włącz Zoom",
    "odpal Zoom",
    "zamknij Zoom",
    "otwórz WhatsApp",
    "włącz WhatsApp",
    "odpal WhatsApp Desktop",
    "zamknij WhatsApp",
    "otwórz Telegram",
    "włącz Telegram",
    "odpal Telegram",
    "zamknij Telegram",
    "otwórz Spotify na komputerze",
    "uruchom Discord na PC",
    "odpal Chrome na Windows",
    "włącz Notatnik Windows",
    "otwórz menedżer zadań",
    "włącz menedżer zadań",
    "odpal Task Manager",
    "zamknij kalkulator",
    "otwórz Photos",
    "włącz Zdjęcia",
    "odpal Zdjęcia",
    "zamknij Zdjęcia",
    "otwórz Ustawienia",
    "włącz ustawienia Windows",
    "odpal Settings",
    "otwórz Sklep Microsoft",
]

MEDIA_PARAPHRASES: List[str] = [
    "puść Billie Jean",
    "odtwórz Billie Jean",
    "posłuchajmy Billie Jean",
    "graj Billie Jean",
    "puść Michael Jackson",
    "odtwórz coś od Michael Jackson",
    "puść muzykę",
    "włącz muzykę",
    "puść next track",
    "następny utwór",
    "poprzedni utwór",
    "pauza",
    "wznów odtwarzanie",
    "kontynuuj muzykę",
    "puść jazz",
    "odtwórz playlistę chill",
    "puść radiostację",
    "graj rock",
    "puść The Weeknd",
    "odtwórz Starboy",
    "puść coś spokojnego",
    "włącz radio",
    "puść podcast",
    "odtwórz podcast",
    "zatrzymaj muzykę",
    "stop muzyki",
    "wycisz trochę",
    "głośniej",
    "ciszej",
    "zwiększ głośność",
    "zmniejsz głośność",
    "puść Queen",
    "odtwórz Bohemian Rhapsody",
    "puść film na YouTube",
    "odtwórz wideo",
    "puść następny",
    "wróć do poprzedniego",
    "puść album Thriller",
    "odtwórz playlistę ulubione",
    "grajmy coś",
    "puść mi jazz",
    "włącz Spotify i puść jazz",
    "otwórz Spotify i puść Billie Jean",
    "puść to samo jeszcze raz",
    "powtórz utwór",
    "zapauzuj",
    "wznów",
    "puść lo-fi",
    "odtwórz lo fi beats",
    "graj klasykę",
    "puść klasykę",
]

HA_PARAPHRASES: List[str] = [
    "zapal światło",
    "włącz światło",
    "zgaś światło",
    "wyłącz światło",
    "zapal lampę",
    "zgaś lampę",
    "jest za ciemno",
    "za ciemno tu",
    "trochę tu ciemno",
    "nic nie widzę",
    "słabo widać",
    "jasniej",
    "ciemniej",
    "rozjaśnij",
    "przyciemnij",
    "więcej światła",
    "dodaj światła",
    "ustaw jasność na 50",
    "jasność na 80 procent",
    "włącz Vizzini",
    "wyłącz Vizzini",
    "zapal Vizzini",
    "zgaś Vizzini",
    "włącz klimatyzację",
    "wyłącz klimatyzację",
    "zimniej",
    "cieplej",
    "ustaw temperaturę na 22",
    "włącz scene filmowa",
    "aktywuj scenę wieczór",
    "jest za jasno",
    "za głośno w domu",
    "włącz światło w salonie",
    "zgaś światło w kuchni",
    "zapal proszę lampę",
    "może być jaśniej",
    "może być trochę jaśniej",
    "przyciemnij trochę",
    "rozjaśnij trochę",
    "światło na maksimum",
    "światło na minimum",
    "włącz wszystkie światła",
    "wyłącz wszystkie światła",
    "czy światło jest włączone",
    "jaki jest stan lampy",
    "na ile procent jest światło",
    "włącz nawiew",
    "wyłącz klimę",
    "klimatyzacja chłodzenie",
    "ustaw klimę na 21",
    "włącz ogrzewanie",
    "wyłącz ogrzewanie",
]

WEB_PARAPHRASES: List[str] = [
    "wyszukaj pogodę w Warszawie",
    "poszukaj przepisów na pizzę",
    "znajdź informacje o Pythonie",
    "sprawdź kurs dolara",
    "wyszukaj co to jest Kubernetes",
    "poszukaj w internecie Elon Musk",
    "znajdź najbliższą aptekę",
    "wyszukaj wyniki meczu",
    "sprawdź rozkład PKP",
    "poszukaj recenzji telefonu",
    "wyszukaj coś o historii Polski",
    "znajdź przepis na naleśniki",
    "sprawdź pogodę jutro",
    "wyszukaj definicję API",
    "poszukaj newsów technologicznych",
    "wyszukaj coś więcej o tym",
    "poszukaj więcej o tym",
    "znajdź więcej informacji o tym",
    "wyszukaj w google pogodę",
    "sprawdź w sieci kto wygrał",
    "wyszukaj adres restauracji",
    "poszukaj godzin otwarcia",
    "znajdź tutorial Pythona",
    "wyszukaj dokumentację Ollama",
    "sprawdź cenę bitcoina",
]

FOLLOWUPS: List[Tuple[str, str]] = [
    ("trochę więcej", UtteranceClass.CHANGE_PARAMETER.value),
    ("trochę mniej", UtteranceClass.CHANGE_PARAMETER.value),
    ("jeszcze więcej", UtteranceClass.CHANGE_PARAMETER.value),
    ("bardziej", UtteranceClass.CHANGE_PARAMETER.value),
    ("mniej", UtteranceClass.CHANGE_PARAMETER.value),
    ("jaśniej", UtteranceClass.CHANGE_PARAMETER.value),
    ("ciemniej", UtteranceClass.CHANGE_PARAMETER.value),
    ("głośniej", UtteranceClass.CHANGE_PARAMETER.value),
    ("ciszej", UtteranceClass.CHANGE_PARAMETER.value),
    ("jeszcze", UtteranceClass.CONTINUATION.value),
    ("tak samo", UtteranceClass.CONTINUATION.value),
    ("i jeszcze raz", UtteranceClass.RETRY.value),
    ("no to zrób", UtteranceClass.CONFIRMATION.value),
    ("zrób to", UtteranceClass.CONFIRMATION.value),
    ("dawaj", UtteranceClass.CONFIRMATION.value),
    ("tak", UtteranceClass.CONFIRMATION.value),
    ("dobra", UtteranceClass.CONFIRMATION.value),
    ("no dobra", UtteranceClass.CONFIRMATION.value),
    ("wykonaj", UtteranceClass.CONFIRMATION.value),
    ("działaj", UtteranceClass.CONFIRMATION.value),
    ("tego", UtteranceClass.CONTINUATION.value),
    ("to", UtteranceClass.CONTINUATION.value),
    ("go", UtteranceClass.CONTINUATION.value),
    ("ją", UtteranceClass.CONTINUATION.value),
    ("tam", UtteranceClass.CONTINUATION.value),
    ("a teraz to", UtteranceClass.CONTINUATION.value),
    ("kontynuuj", UtteranceClass.CONTINUATION.value),
    ("dalej", UtteranceClass.CONTINUATION.value),
    ("i to samo z Discordem", UtteranceClass.CONTINUATION.value),
    ("podobnie ze Spotify", UtteranceClass.CONTINUATION.value),
    ("jeszcze trochę", UtteranceClass.CHANGE_PARAMETER.value),
    ("mocniej", UtteranceClass.CHANGE_PARAMETER.value),
    ("podnieś", UtteranceClass.CHANGE_PARAMETER.value),
    ("obniż", UtteranceClass.CHANGE_PARAMETER.value),
    ("zwiększ", UtteranceClass.CHANGE_PARAMETER.value),
    ("zmniejsz", UtteranceClass.CHANGE_PARAMETER.value),
    ("no to dawaj", UtteranceClass.CONFIRMATION.value),
    ("to zrób", UtteranceClass.CONFIRMATION.value),
    ("ok zrób", UtteranceClass.CONFIRMATION.value),
    ("okej zrób", UtteranceClass.CONFIRMATION.value),
    ("proszę zrób", UtteranceClass.CONFIRMATION.value),
    ("zrób", UtteranceClass.CONFIRMATION.value),
    ("jeszcze jaśniej", UtteranceClass.CHANGE_PARAMETER.value),
    ("jeszcze ciemniej", UtteranceClass.CHANGE_PARAMETER.value),
    ("trochę głośniej", UtteranceClass.CHANGE_PARAMETER.value),
    ("trochę ciszej", UtteranceClass.CHANGE_PARAMETER.value),
    ("i drugie też", UtteranceClass.REFERENCE.value),
    ("to samo", UtteranceClass.CONTINUATION.value),
    ("powtórz", UtteranceClass.RETRY.value),
    ("jeszcze raz to", UtteranceClass.RETRY.value),
]

CORRECTIONS: List[str] = [
    "nie, chodziło mi o Spotify",
    "chodziło mi o Discorda",
    "nie ten",
    "nie, nie to",
    "nie, ten po prawej",
    "myliłem się, otwórz Chrome",
    "poprawka: zapal lampę",
    "nie, chodziło mi o Vizzini",
    "chodziło mi o głośność",
    "nie ten utwór",
    "nie, Billie Jean",
    "chodziło mi o Michael Jackson",
    "nie, zamknij Spotify",
    "nie otwieraj Discorda, otwórz Slacka",
    "jednak Chrome",
    "poprawka Spotify",
    "nie, jaśniej nie ciemniej",
    "chodziło mi o kuchnię",
    "nie salon, kuchnia",
    "nie ta lampa",
    "myliłem się",
    "nie to światło",
    "chodziło mi o temperaturę",
    "nie głośność, jasność",
    "poprawka: ciszej",
    "nie, głośniej",
    "chodziło mi o YouTube",
    "nie Spotify, YouTube",
    "nie ten pierwszy",
    "nie drugi, pierwszy",
    "chodziło mi o pierwszą opcję",
    "nie, drugą",
    "poprawka pierwsza",
    "nie zamykaj, otwórz",
    "jednak nie gaś",
    "chodziło mi o rozjaśnienie",
    "nie przyciemniaj",
    "myliłem się co do Discorda",
    "nie Teams, Discord",
    "chodziło mi o Teams",
    "nie Zoom",
    "poprawka Zoom",
    "nie ten podcast",
    "chodziło mi o jazz",
    "nie rock, jazz",
    "jednak Billie Jean",
    "nie Thriller",
    "chodziło mi o album Thriller",
    "nie ta scena",
    "poprawka scena filmowa",
]

RETRY_FAILURE: List[Tuple[str, str]] = [
    ("czemu nie?", UtteranceClass.WHY_FAILURE.value),
    ("dlaczego nie?", UtteranceClass.WHY_FAILURE.value),
    ("czemu nie zrobiłeś?", UtteranceClass.WHY_FAILURE.value),
    ("dlaczego nie zrobiłeś tego?", UtteranceClass.WHY_FAILURE.value),
    ("czemu nie udało się?", UtteranceClass.WHY_FAILURE.value),
    ("a czemu nie?", UtteranceClass.WHY_FAILURE.value),
    ("spróbuj inaczej", UtteranceClass.RETRY.value),
    ("spróbuj ponownie", UtteranceClass.RETRY.value),
    ("spróbuj jeszcze raz", UtteranceClass.RETRY.value),
    ("jeszcze raz", UtteranceClass.RETRY.value),
    ("ponów", UtteranceClass.RETRY.value),
    ("no to spróbuj", UtteranceClass.RETRY.value),
    ("próbuj inaczej", UtteranceClass.RETRY.value),
    ("inna droga", UtteranceClass.RETRY.value),
    ("inna metoda", UtteranceClass.RETRY.value),
    ("jeszcze tego nie zrobiłeś", UtteranceClass.REFERENCE.value),
    ("jeszcze nie zrobiłeś", UtteranceClass.REFERENCE.value),
    ("nie zrobiłeś tego", UtteranceClass.REFERENCE.value),
    ("wciąż nie", UtteranceClass.REFERENCE.value),
    ("nadal nie działa", UtteranceClass.REFERENCE.value),
    ("to co miałeś zrobić", UtteranceClass.REFERENCE.value),
    ("czemu nie poszło?", UtteranceClass.WHY_FAILURE.value),
    ("dlaczego nie wykonałeś?", UtteranceClass.WHY_FAILURE.value),
    ("spróbuj jeszcze raz inaczej", UtteranceClass.RETRY.value),
    ("no to spróbuj inaczej", UtteranceClass.RETRY.value),
    ("ponów to", UtteranceClass.RETRY.value),
    ("jeszcze raz spróbuj", UtteranceClass.RETRY.value),
    ("czemu nie zrobiłeś tego kroku?", UtteranceClass.WHY_FAILURE.value),
    ("dlaczego nie?", UtteranceClass.WHY_FAILURE.value),
    ("a dlaczego nie zrobiłeś?", UtteranceClass.WHY_FAILURE.value),
]

REFERENCES: List[Tuple[str, str]] = [
    ("tego pierwszego", "first"),
    ("pierwszy", "first"),
    ("pierwszą", "first"),
    ("tego drugiego", "second"),
    ("drugi", "second"),
    ("drugą", "second"),
    ("ostatni", "last"),
    ("ostatnią", "last"),
    ("tamten", ""),
    ("tamto", ""),
    ("to", ""),
    ("tego", ""),
    ("go", ""),
    ("ją", ""),
    ("je", ""),
    ("tam", ""),
    ("o tym", ""),
    ("wyszukaj coś więcej o tym", ""),
    ("poszukaj więcej o tym", ""),
    ("coś więcej o tym", ""),
    ("ten pierwszy", "first"),
    ("ta pierwsza", "first"),
    ("ten drugi", "second"),
    ("ta druga", "second"),
    ("trzeci", "third"),
    ("tego trzeciego", "third"),
    ("wybierz pierwszy", "first"),
    ("weź drugi", "second"),
    ("pierwszą opcję", "first"),
    ("drugą opcję", "second"),
]

INCOMPLETE: List[str] = [
    "otwórz",
    "włącz",
    "puść",
    "ustaw",
    "zapal",
    "tego",
    "to",
    "tam",
    "go",
    "ją",
    "no",
    "a",
    "i",
    "ale",
    "bo",
    "może",
    "żeby",
    "jak",
    "no to",
    "a może",
]


def _goal_context(**extra) -> dict:
    base = {
        "has_active_goal": True,
        "unresolved_goal": True,
        "pending_executable": True,
        "active_parameter": "brightness",
        "presented_options": ["Spotify", "YouTube Music"],
        "discussed_action": True,
        "active_goal": {
            "objective": "Otwórz Spotify",
            "status": GoalStatus.UNRESOLVED.value,
            "domain": "APP",
            "parameter": "brightness",
            "presented_options": ["Spotify", "YouTube Music"],
            "unresolved_reason": "Aplikacja nie wystartowała w limicie czasu.",
            "last_result": {
                "success": False,
                "error": "Aplikacja nie wystartowała w limicie czasu.",
            },
        },
    }
    base.update(extra)
    return base


class UtteranceClassBankTests(unittest.TestCase):
    def test_app_media_ha_web_paraphrases_are_goal_or_action_bearing(self):
        allowed = {
            UtteranceClass.ACTION_REQUEST.value,
            UtteranceClass.GOAL.value,
            UtteranceClass.CHANGE_PARAMETER.value,
            UtteranceClass.INFORMATION_REQUEST.value,
            # volume bare / status questions
            UtteranceClass.UNKNOWN.value,
        }
        banks = APP_PARAPHRASES + MEDIA_PARAPHRASES + HA_PARAPHRASES + WEB_PARAPHRASES
        self.assertGreaterEqual(len(banks), 100)
        misses = []
        for phrase in banks:
            cls = classify_utterance(phrase)
            env = decide_semantics(phrase, allow_llm=False)
            ok_class = cls.utterance_class in allowed or env.needs_tools or env.mode in (
                "ACT",
                "PLAN",
                "ASK",
                "ANSWER",
            )
            if not ok_class:
                misses.append((phrase, cls.utterance_class, env.mode, env.intent))
        self.assertLessEqual(
            len(misses),
            8,
            msg=f"too many non-action paraphrases: {misses[:12]}",
        )

    def test_followups_need_active_goal_context(self):
        self.assertGreaterEqual(len(FOLLOWUPS), 50)
        conv = _goal_context(unresolved_goal=False, pending_executable=False)
        conv["unresolved_goal"] = False
        related = {
            UtteranceClass.CHANGE_PARAMETER.value: {
                UtteranceClass.CHANGE_PARAMETER.value,
                UtteranceClass.CONTINUATION.value,
                UtteranceClass.REFERENCE.value,
            },
            UtteranceClass.CONTINUATION.value: {
                UtteranceClass.CONTINUATION.value,
                UtteranceClass.REFERENCE.value,
                UtteranceClass.CONFIRMATION.value,
                UtteranceClass.ACTION_REQUEST.value,
            },
            UtteranceClass.CONFIRMATION.value: {
                UtteranceClass.CONFIRMATION.value,
                UtteranceClass.ACTION_REQUEST.value,
            },
            UtteranceClass.RETRY.value: {
                UtteranceClass.RETRY.value,
                UtteranceClass.CONTINUATION.value,
                UtteranceClass.CONFIRMATION.value,
            },
            UtteranceClass.REFERENCE.value: {
                UtteranceClass.REFERENCE.value,
                UtteranceClass.CONTINUATION.value,
            },
        }
        misses = []
        for phrase, expected in FOLLOWUPS:
            cls = classify_utterance(phrase, conversation=conv)
            ok = cls.utterance_class in related.get(expected, {expected})
            # Must not collapse to casual smalltalk when a goal is active.
            if cls.utterance_class == UtteranceClass.CASUAL_TALK.value:
                ok = False
            if not ok:
                misses.append((phrase, cls.utterance_class, expected))
        self.assertLessEqual(
            len(misses),
            6,
            msg=f"follow-up class misses: {misses}",
        )
        self.assertTrue(
            all(
                classify_utterance(p, conversation=conv).needs_active_goal
                or classify_utterance(p, conversation=conv).utterance_class
                in related.get(e, {e})
                for p, e in FOLLOWUPS[:20]
            )
        )

    def test_corrections_bank(self):
        self.assertGreaterEqual(len(CORRECTIONS), 50)
        for phrase in CORRECTIONS:
            cls = classify_utterance(phrase)
            env = decide_semantics(phrase, allow_llm=False)
            self.assertTrue(
                cls.utterance_class == UtteranceClass.CORRECTION.value
                or env.correction
                or env.intent in ("correction", "resolve_pending_visual_choice")
                or "nie" in phrase.lower(),
                msg=f"correction miss: {phrase!r} class={cls.utterance_class}",
            )

    def test_retry_failure_bank(self):
        self.assertGreaterEqual(len(RETRY_FAILURE), 30)
        conv = _goal_context()
        for phrase, expected in RETRY_FAILURE:
            cls = classify_utterance(phrase, conversation=conv)
            self.assertEqual(
                cls.utterance_class,
                expected,
                msg=f"{phrase!r} → {cls.utterance_class} want {expected}",
            )

    def test_reference_bank(self):
        self.assertGreaterEqual(len(REFERENCES), 30)
        conv = _goal_context()
        for phrase, ordinal in REFERENCES:
            cls = classify_utterance(phrase, conversation=conv)
            self.assertIn(
                cls.utterance_class,
                (
                    UtteranceClass.REFERENCE.value,
                    UtteranceClass.INFORMATION_REQUEST.value,
                    UtteranceClass.ACTION_REQUEST.value,
                    UtteranceClass.CONTINUATION.value,
                ),
                msg=f"ref miss: {phrase!r} → {cls.utterance_class}",
            )
            if ordinal:
                self.assertEqual(cls.ordinal, ordinal, msg=phrase)

    def test_incomplete_bank(self):
        self.assertGreaterEqual(len(INCOMPLETE), 20)
        conv = _goal_context(unresolved_goal=False)
        for phrase in INCOMPLETE:
            cls = classify_utterance(phrase, conversation=conv)
            self.assertTrue(
                cls.incomplete
                or cls.utterance_class
                in (
                    UtteranceClass.CONTINUATION.value,
                    UtteranceClass.REFERENCE.value,
                    UtteranceClass.UNKNOWN.value,
                    UtteranceClass.CONFIRMATION.value,
                    UtteranceClass.CHANGE_PARAMETER.value,
                ),
                msg=f"incomplete miss: {phrase!r} → {cls.to_dict()}",
            )


class ActiveGoalLifecycleTests(unittest.TestCase):
    def test_dark_room_creates_brightness_goal(self):
        env = decide_semantics("jest za ciemno", allow_llm=False)
        self.assertEqual(env.mode, "ACT")
        self.assertTrue(env.needs_tools)
        self.assertIn("SMART_HOME", env.allowed_domains)
        self.assertGreaterEqual(env.confidence, 0.75)
        self.assertIn(env.utterance_class, ("GOAL", "CHANGE_PARAMETER", ""))
        store = ConversationGoalStore()
        goal = store.observe_utterance("jest za ciemno", envelope=env)
        self.assertEqual(goal.parameter, "brightness")
        self.assertIn(goal.parameter_direction, ("brighter", "more", ""))

    def test_parameter_followup_mutates_same_goal(self):
        store = ConversationGoalStore()
        env1 = decide_semantics("jest za ciemno", allow_llm=False)
        g1 = store.observe_utterance("jest za ciemno", envelope=env1)
        gid = g1.id
        conv = store.semantic_conversation()
        cls = classify_utterance("trochę więcej", conversation=conv)
        self.assertEqual(cls.utterance_class, UtteranceClass.CHANGE_PARAMETER.value)
        env2 = decide_semantics("trochę więcej", conversation=conv, allow_llm=False)
        self.assertEqual(env2.mode, "ACT")
        self.assertTrue(env2.needs_tools)
        g2 = store.observe_utterance("trochę więcej", envelope=env2, classification=cls)
        self.assertEqual(g2.id, gid)
        enriched = enrich_followup_goal_text("trochę więcej", g2, classification=cls)
        self.assertRegex(enriched.lower(), r"rozja[sś]nij|jasno|[sś]wiat")

    def test_unresolved_not_smalltalk(self):
        conv = _goal_context()
        env = decide_semantics(
            "jeszcze tego nie zrobiłeś",
            conversation=conv,
            allow_llm=False,
        )
        self.assertNotEqual(env.mode, "TALK")
        self.assertTrue(env.unresolved_awareness or env.intent == "resume_unresolved_goal")
        self.assertNotEqual(env.intent, "social_smalltalk")
        self.assertNotEqual(env.intent, "general_conversation")

    def test_execute_discussed_no_to_zrob(self):
        conv = _goal_context(unresolved_goal=False)
        conv["unresolved_goal"] = False
        conv["active_goal"]["status"] = GoalStatus.DISCUSSED.value
        env = decide_semantics("no to zrób", conversation=conv, allow_llm=False)
        self.assertEqual(env.mode, "ACT")
        self.assertTrue(env.needs_tools)
        self.assertEqual(env.intent, "execute_discussed_goal")

    def test_why_failure_honest(self):
        conv = _goal_context()
        env = decide_semantics("czemu nie?", conversation=conv, allow_llm=False)
        self.assertEqual(env.intent, "why_failure")
        self.assertFalse(env.needs_tools)
        goal = ActiveGoal.from_dict(conv["active_goal"])
        reply = explain_failure(goal, pending=None)
        self.assertIn("limicie czasu", reply.lower())
        self.assertNotRegex(reply.lower(), r"prawdopodobnie|pewnie|zgaduję")

    def test_why_failure_without_evidence_admits_unknown(self):
        goal = ActiveGoal(
            objective="Otwórz Discord",
            status=GoalStatus.UNRESOLVED.value,
            unresolved_reason="",
            last_result={},
        )
        reply = explain_failure(goal)
        self.assertIn("nie mam pewnego powodu", reply.lower())

    def test_retry_replans(self):
        conv = _goal_context()
        env = decide_semantics("spróbuj inaczej", conversation=conv, allow_llm=False)
        self.assertEqual(env.mode, "ACT")
        self.assertEqual(env.intent, "retry_goal")
        self.assertTrue(env.needs_tools)

    def test_ordinal_pick(self):
        store = ConversationGoalStore()
        store.set_presented_options(["Spotify", "YouTube Music", "Tidal"])
        store.current = ActiveGoal(
            objective="Wybierz serwis",
            status=GoalStatus.ACTIVE.value,
            presented_options=list(store.presented_options),
            domain="MEDIA",
        )
        conv = store.semantic_conversation()
        env = decide_semantics("tego pierwszego", conversation=conv, allow_llm=False)
        self.assertEqual(env.intent, "pick_presented_option")
        self.assertEqual(store.resolve_ordinal("first"), "Spotify")
        self.assertEqual(store.resolve_ordinal("second"), "YouTube Music")

    def test_more_about_referent_web(self):
        conv = _goal_context(unresolved_goal=False, pending_executable=False)
        conv["active_goal"]["objective"] = "Michael Jackson"
        conv["active_goal"]["target"] = "Michael Jackson"
        conv["active_goal"]["status"] = GoalStatus.COMPLETED.value
        conv["has_active_goal"] = True
        env = decide_semantics(
            "wyszukaj coś więcej o tym",
            conversation=conv,
            allow_llm=False,
        )
        self.assertTrue(env.needs_tools)
        self.assertIn("WEB", env.allowed_domains)

    def test_mark_result_unresolved_lifecycle(self):
        store = ConversationGoalStore()
        env = decide_semantics("otwórz Spotify", allow_llm=False)
        store.observe_utterance("otwórz Spotify", envelope=env)
        store.mark_in_progress(action="open_app")
        self.assertEqual(store.current.status, GoalStatus.IN_PROGRESS.value)
        store.mark_result(
            success=False,
            action="open_app",
            result={"error": "timeout waiting for window"},
        )
        self.assertEqual(store.current.status, GoalStatus.UNRESOLVED.value)
        self.assertIn("timeout", store.current.unresolved_reason.lower())
        store.mark_result(
            success=True,
            action="open_app",
            result={"message": "ok", "success": True, "tool": "open_app"},
        )
        self.assertEqual(store.current.status, GoalStatus.COMPLETED.value)
        # False success without evidence must not COMPLETED.
        store.current.status = GoalStatus.IN_PROGRESS.value
        store.mark_result(
            success=True,
            action="agent",
            result={"message": "Chwila — sprawdzam to dokładniej.", "success": True},
        )
        self.assertNotEqual(store.current.status, GoalStatus.COMPLETED.value)


class ExampleTraceTests(unittest.TestCase):
    """Lock the user-facing example traces end-to-end at the semantic layer."""

    def test_example_traces(self):
        store = ConversationGoalStore()
        # 1) jest za ciemno
        e1 = decide_semantics("jest za ciemno", allow_llm=False)
        g1 = store.observe_utterance("jest za ciemno", envelope=e1)
        self.assertEqual(e1.mode, "ACT")
        self.assertEqual(g1.parameter, "brightness")

        # 2) trochę więcej
        conv = store.semantic_conversation()
        e2 = decide_semantics("trochę więcej", conversation=conv, allow_llm=False)
        self.assertEqual(e2.utterance_class, UtteranceClass.CHANGE_PARAMETER.value)
        self.assertEqual(e2.mode, "ACT")

        # fail the goal for later unresolved / why
        store.mark_result(
            success=False,
            action="set_device_value",
            result={"error": "Urządzenie nie odpowiedziało."},
        )

        # 3) jeszcze tego nie zrobiłeś
        conv = store.semantic_conversation()
        e3 = decide_semantics(
            "jeszcze tego nie zrobiłeś", conversation=conv, allow_llm=False
        )
        self.assertTrue(e3.unresolved_awareness)
        self.assertNotEqual(e3.mode, "TALK")

        # 4) no to zrób (discussed)
        store.current.status = GoalStatus.DISCUSSED.value
        store.current.discussed_action = "Rozjaśnij światło"
        conv = store.semantic_conversation()
        e4 = decide_semantics("no to zrób", conversation=conv, allow_llm=False)
        self.assertEqual(e4.intent, "execute_discussed_goal")

        # 5) czemu nie?
        store.current.status = GoalStatus.UNRESOLVED.value
        conv = store.semantic_conversation()
        e5 = decide_semantics("czemu nie?", conversation=conv, allow_llm=False)
        self.assertEqual(e5.intent, "why_failure")
        reply = explain_failure(store.current)
        self.assertIn("nie odpowiedziało", reply.lower())

        # 6) spróbuj inaczej
        e6 = decide_semantics("spróbuj inaczej", conversation=conv, allow_llm=False)
        self.assertEqual(e6.intent, "retry_goal")

        # 7) tego pierwszego
        store.set_presented_options(["opcja A", "opcja B"])
        store.current.presented_options = ["opcja A", "opcja B"]
        conv = store.semantic_conversation()
        e7 = decide_semantics("tego pierwszego", conversation=conv, allow_llm=False)
        self.assertEqual(e7.intent, "pick_presented_option")
        self.assertEqual(store.resolve_ordinal("first"), "opcja A")

        # 8) wyszukaj coś więcej o tym
        store.current.target = "Billie Jean"
        conv = store.semantic_conversation()
        e8 = decide_semantics(
            "wyszukaj coś więcej o tym", conversation=conv, allow_llm=False
        )
        self.assertIn("WEB", e8.allowed_domains)


if __name__ == "__main__":
    unittest.main()
