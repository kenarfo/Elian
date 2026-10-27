# ==========================
# ELIAN — single-turn natural corpus (P2)
# ==========================
from __future__ import annotations

from typing import List

from core.jarvis_behavioral.corpus.templates import expand_template, seeded_rng, stable_id, take_unique
from core.jarvis_behavioral.types import ExpectMode, Scenario, TurnSpec

SEED = 42

# Hand seeds (examples for diversity — not hardcoded into production).
_HAND = {
    "HOME": [
        "ciemno tu",
        "trochę za jasno",
        "weź zapal",
        "możesz coś zrobić ze światłem?",
        "zgaś to",
        "zgaś ją",
        "daj trochę jaśniej",
        "nie aż tak",
        "ustaw jakoś normalnie",
        "jest mi ciemno",
        "przygas trochę",
        "zapal lampę",
        "wyłącz światło",
        "rozjaśnij biurko",
        "zrób jaśniej w pokoju",
    ],
    "MEDIA": [
        "odpal coś michaela",
        "daj coś spokojniejszego",
        "trochę ciszej",
        "następna",
        "co teraz leci?",
        "cofnij tę piosenkę",
        "puść coś podobnego",
        "nie to",
        "podgłośnij",
        "jest za głośno",
        "puść billie jean",
        "pauza",
        "wznów odtwarzanie",
    ],
    "APPS": [
        "weź odpal spotify",
        "chrome poproszę",
        "zamknij discord",
        "otwórz to czego używaliśmy wcześniej",
        "potrzebuję przeglądarki",
        "odpal notatnik",
        "uruchom kalkulator",
        "zamknij chrome",
    ],
    "WEB": [
        "sprawdź w necie kto to jest",
        "wyszukaj coś o tym",
        "znajdź mi informacje o tym",
        "co internet mówi na ten temat?",
        "otwórz pierwszy sensowny wynik",
        "poszukaj michael jackson wikipedia",
        "wyszukaj pogodę w gdańsku",
    ],
    "SYSTEM": [
        "ciszej systemowo",
        "podgłoś komputer",
        "wycisz",
        "jaka jest głośność",
        "ustaw głośność na pół",
    ],
    "MEMORY": [
        "zapamiętaj to",
        "tego nie zapisuj",
        "zapomnij o tym",
        "od teraz wolę żebyś mówił krócej",
        "jednak nie zapisuj tej preferencji",
        "zapamiętaj że wolę spotify",
        "lubię teraz tę piosenkę",
    ],
    "REMINDERS": [
        "przypomnij mi później",
        "jutro mi o tym powiedz",
        "przed spotkaniem przypomnij o ładowarce",
        "już nie musisz mi o tym przypominać",
        "przypomnij o 16",
        "ustaw przypomnienie na jutro rano",
    ],
    "CALENDAR": [
        "co mam jutro w kalendarzu",
        "dodaj spotkanie na piątek",
        "przenieś dzisiejsze spotkanie",
        "czy jestem wolny o 15",
    ],
    "VISION": [
        "co widzisz na ekranie",
        "opisz ekran",
        "gdzie jest przycisk zapisz",
        "co jest na środku",
    ],
    "COMPUTER": [
        "kliknij ok",
        "wpisz cześć",
        "przewiń w dół",
        "skopiuj to",
    ],
    "STATUS": [
        "jesteś online?",
        "co potrafisz",
        "czy działa home assistant",
        "status systemu",
    ],
    "CASUAL": [
        "co tam?",
        "jak się masz?",
        "opowiedz żart",
        "dzięki",
        "spoko",
        "fajny masz głos",
        "nie wiem co robić",
    ],
    "MIXED": [
        "otwórz spotify i ciszej",
        "wyszukaj to i otwórz pierwszy",
        "zapal lampę i podgłośnij",
        "zamknij discord i wyłącz światło",
    ],
}

_TEMPLATES = {
    "HOME": [
        "{ciemno tu|jest ciemno|trochę ciemno|za ciemno|nie widzę}",
        "{zapal|włącz|odpal} {lampę|światło|oświetlenie}",
        "{zgaś|wyłącz} {lampę|światło|to|ją}",
        "{daj|zrób|ustaw} {trochę|odrobinę|} {jaśniej|ciemniej}",
        "możesz {coś zrobić ze światłem|rozjaśnić|przygasić}",
        "{przygaś|rozjaśnij} {trochę|bardziej|}",
        "ustaw {jasność|światło} {normalnie|na średnio|jakoś}",
    ],
    "MEDIA": [
        "{odpal|puść|włącz} coś {michaela|jacksona|spokojnego|energicznego}",
        "{trochę|} {ciszej|głośniej|podgłośnij|ścisz}",
        "{następna|poprzednia|dalej|wstecz}",
        "co {teraz|} leci",
        "{pauza|zatrzymaj|wznów}",
        "daj coś {spokojniejszego|głośniejszego|innego}",
    ],
    "APPS": [
        "{odpal|otwórz|uruchom|weź odpal} {spotify|chrome|discord|notatnik|steam}",
        "{zamknij|wyłącz} {spotify|chrome|discord}",
        "potrzebuję {przeglądarki|spotify|notatnika}",
        "{chrome|spotify|discord} {poproszę|proszę}",
    ],
    "WEB": [
        "{wyszukaj|poszukaj|znajdź} {coś o|informacje o|} {tym|michael jackson|pogodzie}",
        "sprawdź w {necie|internecie} {kto to jest|co to jest}",
        "co {internet|sieć} mówi na ten temat",
        "otwórz {pierwszy|drugi} {wynik|link}",
    ],
    "SYSTEM": [
        "{ciszej|głośniej} {system|komputer|}",
        "{wycisz|odcisz}",
        "ustaw głośność {niżej|wyżej|na pół}",
    ],
    "MEMORY": [
        "{zapamiętaj|zapisz} {to|że wolę spotify|żebym mówił krócej}",
        "{nie zapisuj|nie zapamiętuj|zapomnij} {tego|o tym|tej preferencji}",
        "od teraz wolę {krócej|ciszej|spotify}",
    ],
    "REMINDERS": [
        "przypomnij mi {później|jutro|za godzinę|o 16}",
        "{jutro|później} mi o tym {powiedz|przypomnij}",
        "już nie {musisz|trzeba} mi o tym przypominać",
        "ustaw przypomnienie {na jutro|o ładowarce}",
    ],
    "CALENDAR": [
        "co mam {jutro|dziś|w piątek} w kalendarzu",
        "dodaj {spotkanie|wydarzenie} na {jutro|piątek}",
        "czy jestem wolny o {15|16|południu}",
    ],
    "VISION": [
        "{co widzisz|opisz} {na ekranie|ekran}",
        "gdzie jest {przycisk|okno} {zapisz|ok|anuluj}",
    ],
    "COMPUTER": [
        "{kliknij|naciśnij} {ok|zapisz|anuluj}",
        "wpisz {cześć|test|hasło nie}",
        "przewiń {w dół|w górę}",
    ],
    "STATUS": [
        "{jesteś|czy jesteś} {online|gotowy|tu}",
        "co {potrafisz|umiesz}",
        "{status|stan} {systemu|asystenta}",
    ],
    "CASUAL": [
        "{hej|cześć|siema}",
        "{co tam|jak się masz|co słychać}",
        "{opowiedz|daj} {żart|suchara}",
        "{dzięki|dziękuję|spoko|ok}",
    ],
    "MIXED": [
        "otwórz {spotify|chrome} i {ciszej|głośniej}",
        "{zapal|zgaś} lampę i {podgłośnij|ścisz}",
        "wyszukaj {to|coś} i otwórz {pierwszy|drugi}",
    ],
}

_MODE = {
    "HOME": ExpectMode.ACT,
    "MEDIA": ExpectMode.ACT,
    "APPS": ExpectMode.ACT,
    "WEB": ExpectMode.ACT,
    "SYSTEM": ExpectMode.ACT,
    "MEMORY": ExpectMode.INFO,  # preference/memory often conversational without side-effect tools
    "REMINDERS": ExpectMode.INFO,
    "CALENDAR": ExpectMode.INFO,
    "VISION": ExpectMode.INFO,
    "COMPUTER": ExpectMode.ACT,
    "STATUS": ExpectMode.INFO,
    "CASUAL": ExpectMode.TALK,
    "MIXED": ExpectMode.ACT,
}

_BUCKET = {
    "HOME": "ACTION_REQUEST",
    "MEDIA": "ACTION_REQUEST",
    "APPS": "ACTION_REQUEST",
    "WEB": "ACTION_REQUEST",
    "SYSTEM": "ACTION_REQUEST",
    "MEMORY": "MEMORY",
    "REMINDERS": "ACTION_REQUEST",
    "CALENDAR": "INFORMATION",
    "VISION": "INFORMATION",
    "COMPUTER": "ACTION_REQUEST",
    "STATUS": "CAPABILITY",
    "CASUAL": "RESPONSE",
    "MIXED": "MULTI_TOOL",
}

_TOOLS = {
    "HOME": ("turn_on_device", "turn_off_device", "set_device_value"),
    "MEDIA": ("play_media", "volume_down", "volume_up", "get_now_playing", "next_track", "previous_track"),
    "APPS": ("open_app", "close_app"),
    "WEB": ("web_search", "open_website"),
    "SYSTEM": ("volume_down", "volume_up", "set_volume", "get_volume"),
    "MEMORY": ("set_preference", "forget_preference", "save_memory", "forget_memory"),
    "REMINDERS": ("create_reminder", "cancel_reminder"),
    "CALENDAR": ("create_calendar_event",),
    "VISION": ("describe_screen",),
    "COMPUTER": ("click", "type_text", "ui_click"),
    "STATUS": (),
    "CASUAL": (),
    "MIXED": ("open_app", "volume_down", "volume_up", "turn_on_device", "turn_off_device", "web_search"),
}


def build_single_turn(*, seed: int = SEED, minimum: int = 1500) -> List[Scenario]:
    rng = seeded_rng(seed)
    rows: List[Scenario] = []
    seen = set()

    def _add(domain: str, text: str, *, core: bool = False, critical: bool = False) -> None:
        key = " ".join(text.casefold().split())
        if not key or key in seen:
            return
        seen.add(key)
        mode = _MODE[domain]
        no_side = False
        if domain == "MEDIA" and text.strip().endswith("?") and "leci" in text.casefold():
            mode = ExpectMode.INFO
        if domain == "MEMORY" and any(
            k in text.casefold()
            for k in ("zapamiętaj że wolę", "zapamietaj ze wole", "zapomnij że", "zapomnij ze")
        ):
            mode = ExpectMode.ACT
        if domain == "REMINDERS" and "przypomnij" in text.casefold() and "nie musisz" not in text.casefold():
            mode = ExpectMode.ACT
        if mode in (ExpectMode.INFO, ExpectMode.TALK):
            no_side = mode == ExpectMode.TALK
        world = {}
        if domain == "HOME":
            world = {"unique_light": True, "no_ask": True}
        rows.append(
            Scenario(
                id=stable_id(f"st_{domain.lower()}", text, seed),
                category="single_turn",
                domain=domain,
                turns=[
                    TurnSpec(
                        user=text,
                        expect_mode=mode,
                        expect_tool_any=_TOOLS.get(domain, ()) if mode == ExpectMode.ACT else (),
                        expect_no_side_effect=True if no_side else None,
                        expect_ask=False if world.get("no_ask") and mode == ExpectMode.ACT else None,
                    )
                ],
                score_bucket=_BUCKET.get(domain, "ACTION_REQUEST"),
                tags=("single_turn", domain.lower()),
                seed=seed,
                critical=critical,
                deterministic_core=core,
                world=world,
                expected_behavior=f"{mode.value} for {domain}",
            )
        )

    for domain, phrases in _HAND.items():
        for p in phrases:
            _add(domain, p, core=True)

    # Full template expansion (no tight limit)
    for domain, tmpls in _TEMPLATES.items():
        for tmpl in tmpls:
            for phrase in expand_template(tmpl, rng, limit=0):
                _add(domain, phrase)

    # Combinatorial fillers to reach minimum with natural variety
    prefixes = ("", "weź", "wez", "proszę", "prosze", "możesz", "mozesz", "ej", "no", "dalej")
    suffixes = ("", "proszę", "prosze", "teraz", "szybko", "jakos", "jakoś", "okej")
    home_objs = ("lampę", "lampe", "światło", "swiatlo", "biurko", "salon", "pokój", "pokoj")
    apps = ("spotify", "chrome", "discord", "steam", "notatnik", "firefox", "teams", "vlc")
    artists = ("michaela", "jacksona", "queen", "beethoven", "chopin", "daft punk", "coldplay")
    topics = ("jarvis", "ai", "pogodzie", "gdańsku", "gdansku", "elonie", "pythonie", "bitcoinie")
    times = ("później", "pozniej", "jutro", "za godzinę", "za godzine", "o 16", "o 18", "rano")

    combos = [
        ("HOME", lambda: f"{rng.choice(prefixes)} {rng.choice(['zapal', 'zgaś', 'zgas', 'włącz', 'wlacz', 'wyłącz', 'wylacz'])} {rng.choice(home_objs)} {rng.choice(suffixes)}".strip()),
        ("HOME", lambda: f"{rng.choice(['ciemno', 'za jasno', 'za ciemno', 'trochę ciemno', 'troche ciemno'])} {rng.choice(['tu', 'w pokoju', 'w salonie', ''])} {rng.choice(suffixes)}".strip()),
        ("MEDIA", lambda: f"{rng.choice(['odpal', 'puść', 'pusc', 'włącz', 'wlacz'])} coś {rng.choice(artists)} {rng.choice(suffixes)}".strip()),
        ("MEDIA", lambda: f"{rng.choice(['trochę', 'troche', ''])} {rng.choice(['ciszej', 'głośniej', 'glosniej', 'podgłośnij', 'podglosnij'])} {rng.choice(suffixes)}".strip()),
        ("APPS", lambda: f"{rng.choice(['odpal', 'otwórz', 'otworz', 'uruchom', 'weź odpal', 'wez odpal'])} {rng.choice(apps)} {rng.choice(suffixes)}".strip()),
        ("APPS", lambda: f"{rng.choice(['zamknij', 'wyłącz', 'wylacz'])} {rng.choice(apps)} {rng.choice(suffixes)}".strip()),
        ("WEB", lambda: f"{rng.choice(['wyszukaj', 'poszukaj', 'znajdź', 'znajdz'])} coś o {rng.choice(topics)} {rng.choice(suffixes)}".strip()),
        ("WEB", lambda: f"sprawdź w {rng.choice(['necie', 'internecie'])} {rng.choice(topics)} {rng.choice(suffixes)}".strip()),
        ("SYSTEM", lambda: f"{rng.choice(['ciszej', 'głośniej', 'glosniej', 'wycisz'])} {rng.choice(['system', 'komputer', ''])} {rng.choice(suffixes)}".strip()),
        ("MEMORY", lambda: f"{rng.choice(['zapamiętaj', 'zapamietaj', 'zapisz'])} że wolę {rng.choice(apps)} {rng.choice(suffixes)}".strip()),
        ("REMINDERS", lambda: f"przypomnij mi {rng.choice(times)} {rng.choice(suffixes)}".strip()),
        ("CALENDAR", lambda: f"co mam {rng.choice(['jutro', 'dziś', 'dzis', 'w piątek', 'w piatek'])} w kalendarzu"),
        ("VISION", lambda: f"{rng.choice(['co widzisz', 'opisz'])} {rng.choice(['ekran', 'na ekranie', 'to okno'])}"),
        ("COMPUTER", lambda: f"{rng.choice(['kliknij', 'naciśnij', 'nacisnij'])} {rng.choice(['ok', 'zapisz', 'anuluj', 'dalej'])}"),
        ("STATUS", lambda: f"{rng.choice(['jesteś online', 'jestes online', 'co potrafisz', 'status systemu', 'czy działasz', 'czy dzialasz'])}"),
        ("CASUAL", lambda: f"{rng.choice(['hej', 'cześć', 'czesc', 'siema', 'co tam', 'jak się masz', 'jak sie masz', 'dzięki', 'dzieki', 'opowiedz żart', 'opowiedz zart'])} {rng.choice(suffixes)}".strip()),
        ("MIXED", lambda: f"otwórz {rng.choice(apps)} i {rng.choice(['ciszej', 'głośniej', 'glosniej', 'zgaś lampę', 'zgas lampe'])}"),
    ]

    guard = 0
    while len(rows) < minimum and guard < 100000:
        guard += 1
        domain, fn = combos[guard % len(combos)]
        _add(domain, " ".join(fn().split()))

    return rows[: max(minimum, len(rows))]
