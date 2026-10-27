import datetime
import re

from AppLauncher import APP_ALIASES, launch_application, normalize_name
from WebActions import open_website, search_web, search_youtube


WEEKDAYS = (
    "poniedziałek",
    "wtorek",
    "środa",
    "czwartek",
    "piątek",
    "sobota",
    "niedziela",
)


def clean_text(text):
    text = re.sub(r"\s+", " ", text.lower()).strip()
    replacements = {
        "google chrom": "chrome",
        "jutub": "youtube",
        "jutube": "youtube",
        "spotifaj": "spotify",
    }
    for wrong, correct in replacements.items():
        text = text.replace(wrong, correct)
    return text.strip(" ,!?;")


def _match_query(text, patterns):
    for pattern in patterns:
        match = re.match(pattern, text, re.I)
        if match:
            query = match.group("query").strip(" ,.!?;:")
            query = re.sub(
                r"^(?:mi|dla mnie)\s+",
                "",
                query,
            )
            if query:
                return query
    return None


def _youtube_query(text):
    return _match_query(text, (
        r"^(?:wyszukaj|znajdź|znajdz|poszukaj|pokaż|pokaz|włącz|wlacz|puść|pusc|odtwórz|odtworz)\s+(?P<query>.+?)\s+(?:na|w)\s+(?:youtube|youtubie)$",
        r"^(?:wyszukaj|znajdź|znajdz|poszukaj)\s+(?:na|w)\s+(?:youtube|youtubie)\s+(?P<query>.+)$",
    ))


def _web_query(text):
    return _match_query(text, (
        r"^(?:wyszukaj|znajdź|znajdz|poszukaj|sprawdź|sprawdz)\s+(?P<query>.+?)\s+(?:w internecie|w google|na google)$",
        r"^(?:wyszukaj|znajdź|znajdz|poszukaj|sprawdź|sprawdz)\s+(?:w internecie|w google|na google)\s+(?P<query>.+)$",
    ))


def _direct_app_name(text):
    match = re.match(
        r"^(?:otwórz|otworz|uruchom|włącz|wlacz|odpal)\s+(.+)$",
        text,
    )
    if not match:
        return None

    name = match.group(1).strip()
    normalized = normalize_name(name)
    direct_names = {
        "kalkulator",
        "notatnik",
        "paint",
        "sklep",
        "sklep microsoft",
        "microsoft store",
        "ustawienia",
    }
    if normalized in direct_names or normalized in APP_ALIASES:
        return name
    return None


def _folder_name(text):
    match = re.match(
        r"^(?:otwórz|otworz|pokaż|pokaz)\s+(?:folder\s+)?(?P<name>pulpit|dokumenty|pobrane|obrazy|zdjęcia|zdjecia|muzyka|wideo|video)$",
        text,
    )
    if not match:
        return None
    return match.group("name")


def run_command(text):
    text = clean_text(text)

    compound = re.match(
        r"^(?:otwórz|otworz|uruchom|włącz|wlacz)\s+(?P<app>.+?)\s+i\s+"
        r"(?:wyszukaj|znajdź|znajdz|poszukaj)\s+(?P<query>.+)$",
        text,
    )
    if compound:
        app_name = compound.group("app").strip()
        query = compound.group("query").strip()
        launch_application(app_name)
        if "youtube" in app_name:
            return search_youtube(query)[1]
        return search_web(query)[1]

    query = _youtube_query(text)
    if query:
        return search_youtube(query)[1]

    query = _web_query(text)
    if query:
        return search_web(query)[1]

    if re.match(
        r"^(?:otwórz|otworz|uruchom|włącz|wlacz|wejdź na|wejdz na)\s+(?:stronę\s+|strone\s+)?youtube$",
        text,
    ):
        return open_website("youtube")[1]

    if re.match(
        r"^(?:otwórz|otworz|uruchom|włącz|wlacz|wejdź na|wejdz na)\s+(?:stronę\s+|strone\s+)?google$",
        text,
    ):
        return open_website("google")[1]

    website_match = re.match(
        r"^(?:otwórz|otworz|wejdź na|wejdz na)\s+(?:stronę\s+|strone\s+)?((?:www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?)$",
        text,
        re.I,
    )
    if website_match:
        return open_website(website_match.group(1))[1]

    app_name = _direct_app_name(text)
    if app_name:
        return launch_application(app_name)[1]

    if text in (
        "która godzina",
        "ktora godzina",
        "jaka jest godzina",
        "która jest godzina",
        "ktora jest godzina",
    ):
        now = datetime.datetime.now()
        return f"Jest godzina {now.hour}:{now.minute:02d}."

    if text in (
        "jaki dzisiaj dzień",
        "jaki jest dzisiaj dzień",
        "jaki dziś mamy dzień",
        "jaki dzis mamy dzień",
        "który dziś jest dzień",
        "ktory dzis jest dzien",
        "jaka jest data",
    ):
        today = datetime.datetime.now()
        weekday = WEEKDAYS[today.weekday()]
        return (
            f"Dzisiaj jest {weekday}, "
            f"{today.day}.{today.month}.{today.year}."
        )

    explicit_app = re.match(
        r"^(?:otwórz|otworz|uruchom|włącz|wlacz|odpal)\s+(?:aplikację|aplikacje|program)\s+(.+)$",
        text,
    )
    if explicit_app:
        return launch_application(explicit_app.group(1))[1]

    folder = _folder_name(text)
    if folder:
        from pathlib import Path
        import os
        labels = {
            "pulpit": ("desktop", "Pulpit"),
            "dokumenty": ("documents", "Dokumenty"),
            "pobrane": ("downloads", "Pobrane"),
            "obrazy": ("pictures", "Obrazy"),
            "zdjęcia": ("pictures", "Obrazy"),
            "zdjecia": ("pictures", "Obrazy"),
            "muzyka": ("music", "Muzyka"),
            "wideo": ("videos", "Wideo"),
            "video": ("videos", "Wideo"),
        }
        key, label = labels[folder]
        home = Path.home()
        path = next(
            (
                candidate
                for candidate in (
                    home / label,
                    home / key.capitalize(),
                    home / "OneDrive" / label,
                )
                if candidate.is_dir()
            ),
            None,
        )
        if path is None:
            return f"Nie znalazłem folderu {label}."
        try:
            os.startfile(path)
        except OSError:
            return f"Nie udało mi się otworzyć folderu {label}."
        return f"Otwieram folder {label}."

    return None
