import json
import os
import re
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path

import ollama

from AppLauncher import launch_application
import ComputerControl
from Config import get_config_value
from Contacts import resolve_contact
from ConversationContext import (
    clear_pending_goal,
    get as get_context,
    update as update_context,
)
from GoalRouter import handle_goal
from WebResearch import needs_web_research, research
from Diagnostics import log_turn
from HaActions import execute_device_command, summarize_results
from CommandParser import looks_multi_command, parse_commands, parse_natural_comfort
from HomeAssistant import test_connection as ha_test_connection
from Integrations import send_message
from Memory import (
    add_memory,
    clear_all_memory,
    forget_memory,
    get_last_action,
    load_memory,
    memory_stats,
    record_action,
    search_memory,
)
from Skills import Music, System, Weather
from WebActions import (
    normalize_name,
    open_website,
    search_web,
    search_youtube,
)


MODEL = get_config_value(
    "ai",
    "ollama_model",
    default="llama3.1:8b",
    legacy_key="ollama_model",
)

ROUTING_ENABLED = bool(
    get_config_value("ai", "intent_routing", default=True)
)

COMPUTER_CONTROL_ENABLED = bool(
    get_config_value("computer_control", "enabled", default=True)
)

CONFIRM_RISKY_ACTIONS = bool(
    get_config_value(
        "computer_control",
        "confirm_risky_actions",
        default=True,
    )
)

MIN_CONFIDENCE = float(
    get_config_value("ai", "intent_confidence", default=0.68)
)

CLIENT = ollama.Client(
    timeout=float(
        get_config_value("ai", "request_timeout", default=30)
    )
)

ACTIONS = (
    "none",
    "open_app",
    "open_folder",
    "find_file",
    "open_file",
    "open_website",
    "web_search",
    "youtube_search",
    "weather",
    "light_on",
    "light_off",
    "light_status",
    "light_brightness",
    "light_color",
    "light_temperature",
    "volume_up",
    "volume_down",
    "volume_mute",
    "media_toggle",
    "current_time",
    "current_date",
    "system_cpu",
    "system_memory",
    "system_uptime",
    "list_apps",
    "close_app",
    "screenshot",
    "read_clipboard",
    "copy_clipboard",
    "show_desktop",
    "switch_window",
    "close_window",
    "shutdown",
    "restart",
    "lock_computer",
    "remember",
    "recall_memory",
    "forget_memory",
    "repeat_last_action",
    "capabilities",
    "assistant_status",
    "device_on",
    "device_off",
    "set_temperature",
    "fan_speed",
    "media_volume",
    "media_pause",
    "cover_open",
    "cover_close",
    "send_message",
    "send_file",
)

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "argument": {"type": "string"},
        "target": {"type": "string"},
        "domain": {"type": "string"},
        "unit": {"type": "string"},
        "query": {"type": "string"},
        "application": {"type": "string"},
        "recipient": {"type": "string"},
        "content": {"type": "string"},
        "path": {"type": "string"},
        "value": {"type": "integer", "minimum": 0, "maximum": 100},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["action", "argument", "value", "confidence"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """
Jesteś routerem poleceń polskiego asystenta głosowego Elian.
Nie odpowiadasz użytkownikowi. Zwracasz tylko obiekt zgodny ze schematem JSON.

Wybierz dokładnie jedną akcję:
- open_app: uruchomienie aplikacji; argument to sama nazwa aplikacji.
- open_folder: otwarcie jednego z folderów użytkownika; argument to pulpit,
  dokumenty, pobrane, obrazy, muzyka albo wideo.
- find_file: znalezienie pliku lub folderu; argument to nazwa lub fragment nazwy.
- open_file: otwarcie pliku, folderu albo wyniku wcześniejszego wyszukiwania;
  argument to nazwa, ścieżka, "pierwszy", "drugi" albo "ten plik".
- open_website: otwarcie strony; argument to nazwa serwisu albo adres HTTPS.
- web_search: wyszukanie czegoś w internecie; argument to samo zapytanie.
- youtube_search: znalezienie filmu lub muzyki na YouTube; argument to samo zapytanie.
- weather: pytanie o pogodę; argument to miejscowość albo pusty tekst.
  value oznacza dzień: 0 dzisiaj, 1 jutro, 2 pojutrze.
- light_on, light_off, light_status: sterowanie światłem.
- light_brightness: jasność; value to liczba 0-100.
- light_color: argument to polska nazwa koloru.
- light_temperature: argument to ciepłe, neutralne albo zimne.
- device_on, device_off: włączenie lub wyłączenie dowolnego urządzenia HA;
  argument/target to nazwa, domain to light, climate, switch, fan,
  media_player albo cover.
- set_temperature: klimatyzacja; value to stopnie Celsjusza, target to nazwa.
- fan_speed: wentylator; value 0-100.
- media_volume, media_pause: telewizor lub głośnik.
- cover_open, cover_close: rolety, żaluzje, brama.
- send_message: wiadomość; recipient to osoba, content to treść.
- send_file: wysłanie pliku; recipient i path.
- volume_up, volume_down, volume_mute, media_toggle: multimedia komputera.
- current_time, current_date: aktualna godzina lub data.
- system_cpu, system_memory, system_uptime: stan komputera.
- list_apps: lista działających programów.
- close_app: zamknięcie programu; argument to sama nazwa programu.
- screenshot: wykonanie zrzutu ekranu.
- read_clipboard: jawna prośba o odczytanie tekstu ze schowka.
- copy_clipboard: skopiowanie tekstu; argument to dokładny tekst do skopiowania.
- show_desktop, switch_window, close_window: sterowanie oknami.
- shutdown, restart, lock_computer: zasilanie i blokada komputera.
- remember: jawna prośba o zapamiętanie faktu; argument to sam fakt.
- recall_memory: pytanie, co pamiętasz; argument może zawierać temat.
- forget_memory: jawna prośba o zapomnienie faktu; argument to temat faktu.
- repeat_last_action: powtórzenie ostatniej bezpiecznej czynności.
- capabilities: pytanie, co Elian potrafi.
- assistant_status: pytanie, czy Elian i jego połączenia działają.
- none: zwykłe pytanie, rozmowa, niepewna intencja lub niedozwolona akcja.

Rozumiej parafrazy i odmiany gramatyczne. Nie wymagaj słów "otwórz" ani
"wyszukaj", jeśli zamiar jest jasny. Nie wymyślaj brakujących nazw ani treści.
Nigdy nie wybieraj akcji dla usuwania lub przenoszenia plików ani wpisywania
dowolnych poleceń terminala. send_message i send_file są dozwolone, ale
osobna warstwa sprawdzi, czy integracja istnieje. Nie wkładaj poleceń
powłoki do argumentu. Wybieraj shutdown, restart, close_app,
close_window i lock_computer tylko przy jednoznacznej prośbie użytkownika;
osobna warstwa poprosi wtedy o potwierdzenie. Nie przypisuj klimatyzacji
do światła ani odwrotnie.

Przykłady:
"potrzebuję kalkulatora" -> open_app, argument "kalkulator"
"czy możesz odpalić Discorda" -> open_app, argument "Discord"
"włącz film czajnika na YouTubie" -> youtube_search, argument "film czajnika"
"poszukaj dobrego przepisu na pizzę" -> web_search, argument "dobry przepis na pizzę"
"wejdź na Allegro" -> open_website, argument "Allegro"
"pokaż mi pobrane pliki" -> open_folder, argument "pobrane"
"znajdź plik faktura" -> find_file, argument "faktura"
"otwórz pierwszy wynik" -> open_file, argument "pierwszy"
"jakie programy są uruchomione" -> list_apps
"zamknij Discord" -> close_app, argument "Discord"
"zrób zrzut ekranu" -> screenshot
"co mam w schowku" -> read_clipboard
"skopiuj tekst spotkanie o osiemnastej" -> copy_clipboard, argument "spotkanie o osiemnastej"
"pokaż pulpit" -> show_desktop
"przełącz okno" -> switch_window
"powiedz mi, która jest godzina" -> current_time
"zrób tu trochę ciemniej" -> light_brightness tylko jeśli podano wartość; inaczej none
"jaka jutro będzie pogoda w Warszawie" -> weather, argument "Warszawa", value 1
"pamiętaj, że lubię krótkie odpowiedzi" -> remember, argument "lubię krótkie odpowiedzi"
"co pamiętasz o mojej muzyce" -> recall_memory, argument "muzyka"
"zrób to jeszcze raz" -> repeat_last_action
"co potrafisz" -> capabilities
"czy wszystko u ciebie działa" -> assistant_status
"włącz klimatyzację" -> device_on, argument "klimatyzacja", domain "climate"
"ustaw klimatyzację na 22 stopnie" -> set_temperature, argument "klimatyzacja", domain "climate", value 22
"wyślij Kubie że będę za 10 minut" -> send_message, recipient "Kuba", content "będę za 10 minut"
"opowiedz mi o kosmosie" -> none
"usuń folder" -> none
"""

ROUTING_HINTS = (
    "aplik",
    "program",
    "otw",
    "uruch",
    "odpal",
    "włącz",
    "wlacz",
    "wejdź",
    "wejdz",
    "wyszuk",
    "znajd",
    "poszuk",
    "pokaż",
    "pokaz",
    "puść",
    "pusc",
    "odtw",
    "youtube",
    "internet",
    "google",
    "stron",
    "przeglądar",
    "przegladar",
    "świat",
    "swiat",
    "lamp",
    "jasno",
    "ciemno",
    "kolor",
    "pogod",
    "temperatur",
    "głoś",
    "glos",
    "wycisz",
    "pauz",
    "kalkulator",
    "notatnik",
    "discord",
    "spotify",
    "chrome",
    "firefox",
    "potrzeb",
    "przyda",
    "chcę",
    "chce",
    "folder",
    "pulpit",
    "dokument",
    "pobrane",
    "obrazy",
    "zdjęcia",
    "zdjecia",
    "godzin",
    "data",
    "dzień",
    "dzien",
    "procesor",
    "cpu",
    "ram",
    "pamięć",
    "pamiec",
    "jak długo działa",
    "jak dlugo dziala",
    "plik",
    "znajdź",
    "znajdz",
    "uruchomione",
    "działa w tle",
    "dziala w tle",
    "zamknij",
    "zakończ",
    "zakoncz",
    "zrzut",
    "screenshot",
    "schow",
    "skopiuj",
    "pulpit",
    "przełącz okno",
    "przelacz okno",
    "przełącz",
    "przelacz",
    "wyłącz komputer",
    "wylacz komputer",
    "restart",
    "zrestartuj",
    "zablokuj komputer",
    "zapamiętaj",
    "zapamietaj",
    "pamiętaj",
    "pamietaj",
    "pamiętasz",
    "pamietasz",
    "zapomnij",
    "powtórz",
    "powtorz",
    "jeszcze raz",
    "co potrafisz",
    "co umiesz",
    "status eliana",
    "czy wszystko działa",
    "czy wszystko dziala",
    "wszystko u ciebie",
    "czy działasz",
    "czy dzialasz",
    "twój status",
    "twoj status",
    "klimat",
    "klima",
    "klime",
    "stopn",
    "wentylator",
    "telewizor",
    "rolety",
    "zaluzje",
    "wyślij",
    "wyslij",
    "napisz do",
    "wiadomo",
)

RISKY_ACTIONS = {
    "close_app",
    "close_window",
    "shutdown",
    "restart",
    "lock_computer",
    "forget_memory",
}

COMPUTER_ACTIONS = {
    "find_file",
    "open_file",
    "list_apps",
    "close_app",
    "screenshot",
    "read_clipboard",
    "copy_clipboard",
    "show_desktop",
    "switch_window",
    "close_window",
    "shutdown",
    "restart",
    "lock_computer",
}

REPLAYABLE_ACTIONS = set(ACTIONS) - RISKY_ACTIONS - {
    "none",
    "read_clipboard",
    "remember",
    "recall_memory",
    "forget_memory",
    "repeat_last_action",
}

YES_PHRASES = {
    "tak",
    "tak proszę",
    "tak prosze",
    "potwierdzam",
    "zgadza się",
    "zgadza sie",
    "wykonaj",
}

NO_PHRASES = {
    "nie",
    "anuluj",
    "nie rób tego",
    "nie rob tego",
    "jednak nie",
}

REPEAT_PHRASES = {
    "powtórz",
    "powtorz",
    "powtórz to",
    "powtorz to",
    "zrób to jeszcze raz",
    "zrob to jeszcze raz",
    "jeszcze raz",
}

_pending_intent = None

COLOR_ALIASES = {
    "czerwien": "czerwony",
    "czerwony": "czerwony",
    "czerwona": "czerwony",
    "czerwone": "czerwony",
    "zielen": "zielony",
    "zielony": "zielony",
    "zielona": "zielony",
    "zielone": "zielony",
    "niebieski": "niebieski",
    "niebieska": "niebieski",
    "niebieskie": "niebieski",
    "zolty": "żółty",
    "zolta": "żółty",
    "zolte": "żółty",
    "fioletowy": "fioletowy",
    "fioletowa": "fioletowy",
    "fioletowe": "fioletowy",
}

TEMPERATURE_ALIASES = {
    "ciepla": "ciepłe",
    "cieple": "ciepłe",
    "cieple swiatlo": "ciepłe",
    "ciepla biel": "ciepłe",
    "neutralna": "neutralne",
    "neutralne": "neutralne",
    "neutralne swiatlo": "neutralne",
    "neutralna biel": "neutralne",
    "zimna": "zimne",
    "zimne": "zimne",
    "zimne swiatlo": "zimne",
    "zimna biel": "zimne",
}

FOLDER_ALIASES = {
    "desktop": "desktop",
    "pulpit": "desktop",
    "documents": "documents",
    "dokumenty": "documents",
    "downloads": "downloads",
    "pobrane": "downloads",
    "pictures": "pictures",
    "obrazy": "pictures",
    "zdjecia": "pictures",
    "music": "music",
    "muzyka": "music",
    "videos": "videos",
    "video": "videos",
    "wideo": "videos",
}

FOLDER_LABELS = {
    "desktop": "Pulpit",
    "documents": "Dokumenty",
    "downloads": "Pobrane",
    "pictures": "Obrazy",
    "music": "Muzyka",
    "videos": "Wideo",
}


@dataclass(frozen=True)
class Intent:
    action: str
    argument: str = ""
    value: int = 0
    confidence: float = 0.0
    target: str = ""
    domain: str = ""
    unit: str = ""
    query: str = ""
    application: str = ""
    recipient: str = ""
    content: str = ""
    path: str = ""
    confirmation_required: bool = False


def should_route(text):
    text = text.lower()
    return any(hint in text for hint in ROUTING_HINTS)


def _clean_argument(value):
    if not isinstance(value, str):
        return ""

    value = re.sub(r"\s+", " ", value).strip(" .,!?:;'\"")
    if len(value) > 300:
        return ""
    return value


def _canonical_choice(argument, aliases):
    normalized = normalize_name(argument)
    if normalized in aliases:
        return aliases[normalized]

    match = get_close_matches(
        normalized,
        aliases,
        n=1,
        cutoff=0.76,
    )
    if not match:
        return ""
    return aliases[match[0]]


def _clean_youtube_query(argument):
    argument = re.sub(
        r"^(?:mi|dla mnie)\s+",
        "",
        argument,
        flags=re.I,
    )
    argument = re.sub(
        r"\s+(?:na|w)\s+(?:youtube|youtubie)$",
        "",
        argument,
        flags=re.I,
    )
    return argument.strip()


def _argument_from_user_text(text, patterns):
    for pattern in patterns:
        match = re.match(pattern, text.strip(), flags=re.I)
        if match:
            return _clean_argument(match.group(1))
    return ""


def _followup_intent(text):
    commands = parse_commands(text)
    if len(commands) != 1:
        return None
    command = commands[0]
    if command.action in (
        "set_temperature",
        "light_brightness",
        "light_temperature",
        "adjust_temperature",
        "adjust_brightness",
        "comfort_cold",
        "comfort_hot",
        "comfort_dark",
        "comfort_bright",
        "device_on",
        "device_off",
        "spotify_play",
        "volume_up",
        "volume_down",
    ):
        return Intent(
            action=command.action,
            argument=command.target or command.extra or command.query,
            target=command.target,
            domain=command.domain,
            value=int(command.value or 0),
            unit="celsius" if command.domain == "climate" else "",
            query=command.query,
            application=command.application,
            confidence=command.confidence,
        )
    return None


def infer_other_action(text):
    return any(
        marker in text
        for marker in ("otwórz", "otworz", "wyszuk", "chrome", "youtube")
    )


def classify_intent(text):
    followup = _followup_intent(text)
    if followup is not None:
        return followup

    if not ROUTING_ENABLED or not text or not should_route(text):
        return None

    last_action = get_last_action()
    user_content = text
    if last_action:
        user_content += (
            "\nKontekst ostatniej wykonanej czynności: "
            f"{last_action.get('action', '')} "
            f"{last_action.get('argument', '')}."
        )

    try:
        response = CLIENT.chat(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            format=INTENT_SCHEMA,
            options={"temperature": 0},
            keep_alive="30m",
        )
        payload = json.loads(response["message"]["content"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print("Router intencji zwrócił błędny wynik:", error)
        return None
    except Exception as error:
        print("Router intencji Ollama:", error)
        return None

    action = payload.get("action", "none")
    if action not in ACTIONS or action == "none":
        return None

    if action in COMPUTER_ACTIONS and not COMPUTER_CONTROL_ENABLED:
        return None

    lowered_text = text.lower()
    if action == "weather" and not any(
        marker in lowered_text
        for marker in (
            "pogod",
            "temperatur",
            "stopni",
            "deszcz",
            "pada",
            "śnieg",
            "snieg",
            "wiatr",
        )
    ):
        if any(
            marker in lowered_text
            for marker in ("jaki dziś", "jaki dzis", "data", "dzień", "dzien")
        ):
            action = "current_date"
        else:
            return None

    try:
        confidence = float(payload.get("confidence", 0))
        value = int(payload.get("value", 0))
    except (TypeError, ValueError):
        return None

    if confidence < MIN_CONFIDENCE:
        return None

    if action == "light_brightness":
        brightness_match = re.search(r"\b(\d{1,3})\b", text)
        if brightness_match is None:
            return None
        value = int(brightness_match.group(1))

    if action == "weather":
        if "pojutrze" in lowered_text:
            value = 2
        elif "jutro" in lowered_text:
            value = 1
        else:
            value = 0

    argument = _clean_argument(payload.get("argument", ""))
    target = _clean_argument(payload.get("target", "")) or argument
    domain = _clean_argument(payload.get("domain", "")).lower()
    query = _clean_argument(payload.get("query", ""))
    recipient = _clean_argument(payload.get("recipient", ""))
    content = _clean_argument(payload.get("content", ""))
    path = _clean_argument(payload.get("path", ""))
    application = _clean_argument(payload.get("application", ""))
    unit = _clean_argument(payload.get("unit", ""))
    if action == "find_file":
        argument = _argument_from_user_text(text, (
            r"^(?:znajdź|znajdz|wyszukaj|poszukaj)\s+(?:mi\s+)?(?:plik|folder|dokument)?\s*(.+)$",
            r"^(?:gdzie jest|gdzie mam)\s+(.+)$",
        )) or argument
    elif action == "copy_clipboard":
        argument = _argument_from_user_text(text, (
            r"^(?:skopiuj|kopiuj)\s+(?:do schowka\s+)?(?:tekst\s+)?(.+)$",
        )) or argument
    elif action == "remember":
        argument = _argument_from_user_text(text, (
            r"^(?:zapamiętaj|zapamietaj|pamiętaj|pamietaj)[, ]+(?:że|ze\s+)?(.+)$",
        )) or argument
    elif action == "forget_memory":
        argument = _argument_from_user_text(text, (
            r"^(?:zapomnij|usuń z pamięci|usun z pamieci)[, ]+(?:że|ze\s+)?(.+)$",
        )) or argument
    elif action == "youtube_search":
        argument = _clean_youtube_query(argument)
    elif action == "light_color":
        argument = _canonical_choice(argument, COLOR_ALIASES)
    elif action == "light_temperature":
        argument = _canonical_choice(argument, TEMPERATURE_ALIASES)
    elif action == "open_folder":
        argument = _canonical_choice(argument, FOLDER_ALIASES)

    if action == "set_temperature":
        temp_match = re.search(r"\b(\d{1,2})\b", text)
        if temp_match is None and not value:
            return None
        if temp_match is not None:
            value = int(temp_match.group(1))
        domain = domain or "climate"
        unit = unit or "celsius"
        target = target or argument

    if action in ("device_on", "device_off") and not domain:
        from DeviceResolver import infer_domain
        domain = infer_domain(text) or infer_domain(target)

    if action in (
        "open_app",
        "open_folder",
        "find_file",
        "open_file",
        "open_website",
        "web_search",
        "youtube_search",
        "light_color",
        "light_temperature",
        "close_app",
        "copy_clipboard",
        "remember",
        "forget_memory",
        "send_message",
    ) and not (argument or target or recipient):
        return None

    if action == "send_message":
        recipient = recipient or argument
        content = content or target
        if not recipient:
            return None

    return Intent(
        action=action,
        argument=argument or target,
        value=max(0, min(100, value)),
        confidence=confidence,
        target=target or argument,
        domain=domain,
        unit=unit,
        query=query or argument,
        application=application,
        recipient=recipient,
        content=content,
        path=path,
        confirmation_required=action in RISKY_ACTIONS,
    )


def _folder_path(folder):
    home = Path.home()
    candidates = (
        home / FOLDER_LABELS[folder],
        home / folder.capitalize(),
        home / "OneDrive" / FOLDER_LABELS[folder],
    )
    return next((path for path in candidates if path.is_dir()), None)


def _open_folder(folder):
    path = _folder_path(folder)
    label = FOLDER_LABELS[folder]
    if path is None:
        return f"Nie znalazłem folderu {label}."

    try:
        os.startfile(path)
    except OSError as error:
        print("Błąd otwierania folderu:", error)
        return f"Nie udało mi się otworzyć folderu {label}."

    return f"Otwieram folder {label}."


def execute_intent(intent):
    action = intent.action

    if action == "open_app":
        _success, message = launch_application(intent.argument)
        return message

    if action == "open_folder":
        return _open_folder(intent.argument)

    if action == "find_file":
        paths = ComputerControl.find_files(intent.argument)
        return ComputerControl.describe_file_results(
            intent.argument,
            paths,
        )

    if action == "open_file":
        return ComputerControl.open_file_or_folder(intent.argument)

    if action == "open_website":
        _success, message = open_website(intent.argument)
        return message

    if action == "web_search":
        _success, message = search_web(intent.argument)
        return message

    if action == "youtube_search":
        _success, message = search_youtube(intent.argument)
        return message

    if action == "weather":
        return Weather.get_weather(
            intent.argument or None,
            day=intent.value,
        )

    device_actions = {
        "light_on": ("device_on", "light"),
        "light_off": ("device_off", "light"),
        "light_status": ("device_status", "light"),
        "light_brightness": ("light_brightness", "light"),
        "light_color": ("light_color", "light"),
        "light_temperature": ("light_temperature", "light"),
        "adjust_brightness": ("adjust_brightness", "light"),
        "adjust_temperature": ("adjust_temperature", "climate"),
        "comfort_cold": ("comfort_cold", "climate"),
        "comfort_hot": ("comfort_hot", "climate"),
        "comfort_dark": ("comfort_dark", "light"),
        "comfort_bright": ("comfort_bright", "light"),
        "device_on": ("device_on", intent.domain),
        "device_off": ("device_off", intent.domain),
        "set_temperature": ("set_temperature", "climate"),
        "fan_speed": ("fan_speed", "fan"),
        "media_volume": ("media_volume", "media_player"),
        "media_pause": ("media_pause", "media_player"),
        "cover_open": ("device_on", "cover"),
        "cover_close": ("device_off", "cover"),
    }
    if action in device_actions:
        mapped, domain = device_actions[action]
        extra = intent.argument
        target = intent.target or intent.argument
        entity_id = ""
        context = get_context()
        if not target and domain and context.get("last_domain") == domain:
            target = context.get("last_device_name") or ""
            entity_id = context.get("last_device") or ""
        if action in ("light_color", "light_temperature"):
            extra = intent.argument
            if not intent.target or intent.target == intent.argument:
                target = intent.target or "światło"
                if action == "light_temperature" and intent.argument in (
                    "warm", "cool", "neutral", "daylight", "very_warm",
                    "ciepłe", "cieple", "zimne", "neutralne", "dzienne",
                ):
                    extra = intent.argument
        if action == "light_brightness" and not intent.target:
            target = "światło"
        if action in ("light_on", "light_off", "light_status") and not intent.target:
            target = intent.argument or "światło"
        return execute_device_command(
            mapped,
            target=target,
            domain=domain,
            value=intent.value,
            extra=extra,
            entity_id=entity_id,
        )

    if action == "spotify_play":
        update_context(last_application="spotify", last_app="spotify")
        return (
            "Spotify jest otwarte w kontekście, ale nie mam jeszcze "
            f"integracji do odtwarzania utworu: {intent.query or intent.argument}."
        )

    if action in ("send_message", "send_file"):
        status, contacts, message = resolve_contact(intent.recipient or intent.argument)
        if status != "exact":
            return message
        attachment = intent.path if action == "send_file" else None
        return send_message(
            contacts[0],
            intent.content or intent.argument,
            attachment=attachment,
        )

    media_commands = {
        "volume_up": "głośniej",
        "volume_down": "ciszej",
        "volume_mute": "wycisz",
        "media_toggle": "pauza",
    }
    if action in media_commands:
        return Music.run(media_commands[action])

    if action == "list_apps":
        return ComputerControl.list_running_apps()

    if action == "close_app":
        return ComputerControl.close_application(intent.argument)

    if action == "screenshot":
        return ComputerControl.take_screenshot()

    if action == "read_clipboard":
        return ComputerControl.read_clipboard()

    if action == "copy_clipboard":
        return ComputerControl.copy_to_clipboard(intent.argument)

    if action == "show_desktop":
        return ComputerControl.show_desktop()

    if action == "switch_window":
        return ComputerControl.switch_window()

    if action == "close_window":
        return ComputerControl.close_active_window()

    if action in ("shutdown", "restart"):
        return ComputerControl.power_action(action)

    if action == "lock_computer":
        return ComputerControl.power_action("lock")

    if action == "remember":
        if add_memory(intent.argument):
            return "Zapamiętałem."
        return "Już to pamiętam."

    if action == "recall_memory":
        memories = (
            search_memory(intent.argument, limit=8)
            if intent.argument
            else load_memory()[-8:]
        )
        if not memories:
            return "Nie mam jeszcze zapisanych informacji na ten temat."
        return "Pamiętam: " + "; ".join(memories) + "."

    if action == "forget_memory":
        if normalize_name(intent.argument) in (
            "wszystko",
            "cala pamiec",
            "calą pamiec",
        ):
            clear_all_memory()
            from Brain import reset_conversation

            reset_conversation()
            return "Wyczyściłem pamięć i historię rozmowy."

        removed = forget_memory(intent.argument)
        if removed:
            return f"Usunąłem {removed} pasujących informacji z pamięci."
        return "Nie znalazłem takiej informacji w pamięci."

    if action == "capabilities":
        return (
            "Potrafię rozmawiać i pamiętać fakty, uruchamiać aplikacje, "
            "otwierać i wyszukiwać pliki, sterować oknami i multimediami, "
            "korzystać ze schowka, robić zrzuty ekranu, wyszukiwać w sieci, "
            "sprawdzać pogodę oraz sterować urządzeniami Home Assistant."
        )

    if action == "assistant_status":
        stats = memory_stats()
        home_assistant = (
            "Home Assistant jest połączony"
            if ha_test_connection()
            else "Home Assistant nie odpowiada"
        )
        return (
            "Główne moduły Eliana działają. "
            f"{home_assistant}. Pamięć zawiera {stats['facts']} faktów "
            f"i {stats['actions']} ostatnich działań."
        )

    system_commands = {
        "current_time": "która godzina",
        "current_date": "jaka jest data",
        "system_cpu": "użycie procesora",
        "system_memory": "ile mam ramu",
        "system_uptime": "jak długo działa komputer",
    }
    if action in system_commands:
        if action in ("current_time", "current_date"):
            from Commands import run_command

            return run_command(system_commands[action])
        return System.run(system_commands[action])

    return None


def clear_pending_action():
    global _pending_intent
    _pending_intent = None
    clear_pending_goal()


def _confirmation_prompt(intent):
    prompts = {
        "close_app": f"Czy na pewno mam zamknąć {intent.argument}?",
        "close_window": "Czy na pewno mam zamknąć aktywne okno?",
        "shutdown": "Czy na pewno mam wyłączyć komputer?",
        "restart": "Czy na pewno mam uruchomić komputer ponownie?",
        "lock_computer": "Czy na pewno mam zablokować komputer?",
        "forget_memory": (
            f"Czy na pewno mam zapomnieć: {intent.argument}?"
        ),
    }
    return prompts.get(intent.action, "Czy na pewno mam to zrobić?")


def _execute_and_record(intent):
    result = execute_intent(intent)
    if not result:
        return None

    lowered = result.lower()
    success = not any(
        marker in lowered
        for marker in (
            "nie udało",
            "nie znalazłem",
            "nie mam",
            "nie uruchamiam",
        )
    )
    stored_result = (
        "Odczytano schowek."
        if intent.action == "read_clipboard"
        else result
    )
    record_action(
        intent.action,
        argument=intent.argument or intent.target,
        result=stored_result,
        success=success,
        value=intent.value,
    )
    if success:
        update_context(
            last_action=intent.action,
            last_application=intent.application or intent.argument,
            last_query=intent.query or intent.argument,
            last_domain=intent.domain,
        )
    return result


def _repeat_last_action():
    previous = get_last_action(REPLAYABLE_ACTIONS)
    if previous is None:
        return "Nie mam bezpiecznej czynności do powtórzenia."

    intent = Intent(
        action=previous.get("action", "none"),
        argument=previous.get("argument", ""),
        value=int(previous.get("value", 0)),
        confidence=1.0,
    )
    if intent.action not in REPLAYABLE_ACTIONS:
        return "Nie powtórzę tej czynności bez nowego polecenia."
    return _execute_and_record(intent)


def _intent_from_parsed(command):
    return Intent(
        action=command.action,
        argument=command.extra or command.target or command.query,
        target=command.target,
        domain=command.domain,
        value=int(command.value or 0),
        query=command.query,
        application=command.application,
        confidence=command.confidence,
    )


def _execute_parsed_command(command):
    if command.clarification:
        return command.clarification

    if command.action in ("volume_up", "volume_down", "volume_mute", "media_toggle"):
        media_commands = {
            "volume_up": "głośniej",
            "volume_down": "ciszej",
            "volume_mute": "wycisz",
            "media_toggle": "pauza",
        }
        result = Music.run(media_commands[command.action])
        if result:
            record_action(command.action, result=result, success=True)
        return result

    if command.action == "open_app":
        success, message = launch_application(command.target or command.application)
        if success:
            update_context(
                last_action="open_app",
                last_application=command.application or command.target,
                last_app=command.application or command.target,
            )
        record_action("open_app", argument=command.target, result=message, success=success)
        return message

    if command.action == "spotify_play":
        update_context(last_application="spotify", last_app="spotify")
        message = (
            "Nie mam jeszcze integracji do odtwarzania w Spotify: "
            f"{command.query}."
        )
        record_action("spotify_play", argument=command.query, result=message, success=False)
        return message

    entity_id = command.entity_id
    if not entity_id and command.domain:
        context = get_context()
        if command.domain == "climate":
            entity_id = context.get("last_climate") or (
                context.get("last_device")
                if context.get("last_domain") == "climate"
                else ""
            )
        elif command.domain == "light":
            entity_id = context.get("last_light") or (
                context.get("last_device")
                if context.get("last_domain") == "light"
                else ""
            )
        elif context.get("last_domain") == command.domain:
            entity_id = context.get("last_device") or ""

    result = execute_device_command(
        command.action,
        target=command.target,
        domain=command.domain,
        value=command.value,
        extra=command.extra or command.target,
        entity_id=entity_id,
    )
    success = bool(result) and not any(
        marker in result.lower()
        for marker in (
            "nie udało",
            "nie znalazłem",
            "nie mam",
            "nie wykonam",
            "które",
            "ktore",
            "mam włączyć",
            "mam wlaczyc",
        )
    )
    record_action(
        command.action,
        argument=command.target,
        result=result or "",
        success=success,
        value=command.value,
    )
    return result


def _execute_parsed_commands(commands):
    if not commands:
        return None
    results = []
    for command in commands:
        log_turn(
            "intent",
            action=command.action,
            target=command.target,
            domain=command.domain,
            value=command.value,
            entity_id=command.entity_id,
            source="parser",
        )
        print(
            "Intencja:",
            command.action,
            repr(command.target),
            command.domain,
        )
        result = _execute_parsed_command(command)
        if result:
            results.append(result)
            # Stop further actions if clarification is needed.
            if "?" in result and any(
                marker in result.lower()
                for marker in ("które", "ktore", "mam włączyć", "mam wlaczyc")
            ):
                break
    if not results:
        return None
    return summarize_results(results)


def run_intent(text):
    global _pending_intent

    normalized = normalize_name(text)
    if _pending_intent is not None:
        if normalized in YES_PHRASES:
            intent = _pending_intent
            _pending_intent = None
            return _execute_and_record(intent)
        if normalized in NO_PHRASES:
            _pending_intent = None
            return "Anulowałem."

        _pending_intent = None

    if normalized in REPEAT_PHRASES:
        return _repeat_last_action()

    parsed = parse_commands(text)
    if parsed and (
        looks_multi_command(text)
        or len(parsed) > 1
        or parse_natural_comfort(text) is not None
        or parsed[0].action in (
            "set_temperature",
            "light_brightness",
            "light_temperature",
            "adjust_temperature",
            "adjust_brightness",
            "comfort_cold",
            "comfort_hot",
            "comfort_dark",
            "comfort_bright",
            "device_on",
            "device_off",
            "spotify_play",
        )
    ):
        return _execute_parsed_commands(parsed)

    goal_result = handle_goal(text)
    if goal_result:
        log_turn("handler", handler="goal", result=goal_result)
        return goal_result

    if needs_web_research(text):
        result = research(text)
        if result:
            log_turn("handler", handler="web_research", result=result[:120])
            record_action("web_research", argument=text, result=result, success=True)
            return result

    if parsed:
        return _execute_parsed_commands(parsed)

    intent = classify_intent(text)
    if intent is None:
        return None

    print(
        "Intencja AI:",
        intent.action,
        repr(intent.target or intent.argument),
        intent.domain,
        f"({intent.confidence:.2f})",
    )
    log_turn(
        "intent",
        action=intent.action,
        target=intent.target or intent.argument,
        domain=intent.domain,
        value=intent.value,
        confidence=f"{intent.confidence:.2f}",
    )

    if intent.action == "repeat_last_action":
        return _repeat_last_action()

    if CONFIRM_RISKY_ACTIONS and intent.action in RISKY_ACTIONS:
        _pending_intent = intent
        return _confirmation_prompt(intent)

    return _execute_and_record(intent)
