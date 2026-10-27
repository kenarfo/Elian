# ==========================
# ELIAN AI ASSISTANT
# AppLauncher.py
# Version 0.8
# ==========================

import os
import subprocess
import re
import threading
from difflib import get_close_matches


# ==========================
# NORMALIZACJA
# ==========================

def normalize_name(text):

    text = text.lower().strip()

    replacements = {
        "ą": "a",
        "ć": "c",
        "ę": "e",
        "ł": "l",
        "ń": "n",
        "ó": "o",
        "ś": "s",
        "ź": "z",
        "ż": "z"
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(
        r"\.(exe|lnk|url)$",
        "",
        text
    )

    text = re.sub(
        r"[^a-z0-9 ]",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ==========================
# WINDOWS START MENU
# ==========================

START_MENU_PATHS = [

    os.path.expandvars(
        r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"
    ),

    os.path.expandvars(
        r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs"
    )
]

_APP_INDEX = None
_APP_INDEX_LOCK = threading.Lock()


# ==========================
# SPECJALNE APLIKACJE WINDOWS
# ==========================

SPECIAL_APPS = {

    "kalkulator":
        "calc.exe",

    "calculator":
        "calc.exe",

    "notatnik":
        "notepad.exe",

    "paint":
        "mspaint.exe",

    "sklep microsoft":
        "ms-windows-store:",

    "microsoft store":
        "ms-windows-store:",

    "sklep":
        "ms-windows-store:",

    "ustawienia":
        "ms-settings:",

    "settings":
        "ms-settings:"
}


APP_ALIASES = {
    "calc": "kalkulator",
    "calculator": "kalkulator",
    "chrome": "Google Chrome",
    "chrom": "Google Chrome",
    "discord": "Discord",
    "discorda": "Discord",
    "edge": "Microsoft Edge",
    "eksplorator": "File Explorer",
    "eksplorator plikow": "File Explorer",
    "firefoxa": "Firefox",
    "firefox": "Firefox",
    "google chrome": "Google Chrome",
    "krom": "Google Chrome",
    "menedzer zadan": "Task Manager",
    "obs": "OBS Studio",
    "obs studio": "OBS Studio",
    "power point": "PowerPoint",
    "spotify": "Spotify",
    "steama": "Steam",
    "steam": "Steam",
    "terminal": "Windows PowerShell",
    "visual studio code": "Visual Studio Code",
    "vs code": "Visual Studio Code",
}


def canonicalize_app_name(app_name):
    normalized = normalize_name(app_name)
    return APP_ALIASES.get(normalized, app_name.strip())


def is_safe_app_name(app_name):
    if not isinstance(app_name, str):
        return False

    app_name = app_name.strip()
    if not app_name or len(app_name) > 100:
        return False

    return not bool(re.search(r"[\\/:*?\"<>|;&`\r\n]", app_name))


# ==========================
# SZUKANIE SKRÓTU
# ==========================

def _build_app_index():
    index = []
    for base_path in START_MENU_PATHS:
        if not os.path.exists(base_path):
            continue

        for root, _directories, files in os.walk(base_path):
            for file in files:
                if not file.lower().endswith((".lnk", ".url", ".exe")):
                    continue
                index.append((
                    normalize_name(file),
                    os.path.join(root, file),
                ))
    return index


def get_app_index(refresh=False):
    global _APP_INDEX

    with _APP_INDEX_LOCK:
        if _APP_INDEX is None or refresh:
            _APP_INDEX = _build_app_index()
        return list(_APP_INDEX)


def find_start_menu_app(app_name):
    target = normalize_name(app_name)
    if not target:
        return None

    index = get_app_index()
    exact = next((path for name, path in index if name == target), None)
    if exact:
        return exact

    partial = sorted(
        (
            (len(name), path)
            for name, path in index
            if name.startswith(target) or target in name
        ),
        key=lambda item: item[0],
    )
    if partial:
        return partial[0][1]

    names = [name for name, _path in index]
    close = get_close_matches(target, names, n=1, cutoff=0.78)
    if not close:
        return None
    return next(path for name, path in index if name == close[0])


# ==========================
# URUCHAMIANIE
# ==========================

def launch_application(app_name):

    if not is_safe_app_name(app_name):
        return False, "Ta nazwa aplikacji jest nieprawidłowa."

    original_name = app_name.strip()

    original_name = canonicalize_app_name(
        original_name
    )

    name = normalize_name(
        original_name
    )

    if not name:
        return False, "Nie podano nazwy aplikacji."


    # ======================
    # SPECJALNA APLIKACJA
    # ======================

    if name in SPECIAL_APPS:

        command = SPECIAL_APPS[name]

        try:

            os.startfile(command)

            return True, (
                f"Uruchomiłem {original_name}."
            )

        except Exception as e:

            print(
                "Błąd uruchamiania:",
                e
            )

            return False, (
                f"Nie udało się uruchomić "
                f"{original_name}."
            )


    # ======================
    # START MENU
    # ======================

    path = find_start_menu_app(
        original_name
    )

    if path:

        try:

            os.startfile(path)

            return True, (
                f"Uruchomiłem {original_name}."
            )

        except Exception as e:

            print(
                "Błąd:",
                e
            )

            return False, (
                f"Znalazłem {original_name}, "
                f"ale nie udało się jej uruchomić."
            )


    # ======================
    # PRÓBA PRZEZ SYSTEMOWĄ ŚCIEŻKĘ
    # ======================

    try:

        subprocess.Popen(
            [original_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        return True, (
            f"Uruchomiłem {original_name}."
        )

    except OSError as error:

        print(
            "Błąd uruchamiania programu:",
            error
        )


    # ======================
    # NIE ZNALEZIONO
    # ======================

    return False, (
        f"Nie znalazłem aplikacji "
        f"{original_name}."
    )
