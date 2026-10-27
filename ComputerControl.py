import ctypes
import os
import re
import subprocess
import time
import unicodedata
from ctypes import wintypes
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import psutil
import pyautogui

from Config import config
from Memory import get_context, set_context


CONTROL_CONFIG = config.get("computer_control", {})
SEARCH_TIME_LIMIT = float(CONTROL_CONFIG.get("search_time_limit", 5))
SEARCH_FILE_LIMIT = int(CONTROL_CONFIG.get("search_file_limit", 100000))

BLOCKED_OPEN_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".com",
    ".exe",
    ".js",
    ".lnk",
    ".msi",
    ".ps1",
    ".reg",
    ".scr",
    ".url",
    ".vbs",
}

SKIPPED_DIRECTORIES = {
    "$recycle.bin",
    ".cache",
    ".codex",
    ".git",
    ".gradle",
    ".idea",
    ".npm",
    ".nuget",
    ".vscode",
    "__pycache__",
    "appdata",
    "node_modules",
    "system volume information",
}

PROTECTED_PROCESSES = {
    "csrss",
    "dwm",
    "explorer",
    "lsass",
    "memory compression",
    "registry",
    "services",
    "smss",
    "svchost",
    "system",
    "wininit",
    "winlogon",
}

PROCESS_ALIASES = {
    "discorda": "discord",
    "google chrome": "chrome",
    "microsoft edge": "msedge",
    "visual studio code": "code",
    "vs code": "code",
    "notatnik": "notepad",
    "notepad": "notepad",
}

PROCESS_DISPLAY_NAMES = {
    "chatgpt": "ChatGPT",
    "chrome": "Chrome",
    "code": "Visual Studio Code",
    "discord": "Discord",
    "explorer": "Eksplorator plików",
    "firefox": "Firefox",
    "msedge": "Microsoft Edge",
    "obs64": "OBS Studio",
    "steam": "Steam",
}


def _normalize(text):
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _clean_argument(text, limit=300):
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text).strip(" .,!?:;'\"")[:limit]


def get_search_roots():
    configured = CONTROL_CONFIG.get("search_roots", [])
    roots = []

    for value in configured:
        if not isinstance(value, str):
            continue
        path = Path(os.path.expandvars(value)).expanduser()
        if path.is_dir():
            roots.append(path.resolve())

    if not roots:
        roots.append(Path.home().resolve())

    return roots


def _search_score(name, query, terms):
    normalized = _normalize(name)
    if normalized == query:
        return 100
    if normalized.startswith(query):
        return 80
    if query in normalized:
        return 60

    overlap = sum(term in normalized for term in terms)
    if overlap == len(terms) and terms:
        return 40 + overlap

    name_terms = normalized.split()
    fuzzy_overlap = sum(
        any(
            SequenceMatcher(None, term, name_term).ratio() >= 0.74
            for name_term in name_terms
        )
        for term in terms
    )
    if fuzzy_overlap == len(terms) and terms:
        return 30 + fuzzy_overlap

    if SequenceMatcher(None, query, normalized).ratio() >= 0.72:
        return 25
    return 0


def find_files(query, limit=8):
    query = _clean_argument(query, 120)
    normalized_query = _normalize(query)
    terms = normalized_query.split()
    if not normalized_query:
        return []

    started = time.monotonic()
    checked = 0
    matches = []

    for root in get_search_roots():
        for current_root, directories, files in os.walk(root):
            directories[:] = [
                directory
                for directory in directories
                if directory.lower() not in SKIPPED_DIRECTORIES
            ]

            for name in directories + files:
                checked += 1
                if checked > SEARCH_FILE_LIMIT:
                    break

                score = _search_score(name, normalized_query, terms)
                if score:
                    path = Path(current_root, name)
                    matches.append((score, len(str(path)), path))

            if (
                checked > SEARCH_FILE_LIMIT
                or time.monotonic() - started > SEARCH_TIME_LIMIT
            ):
                break

        if (
            checked > SEARCH_FILE_LIMIT
            or time.monotonic() - started > SEARCH_TIME_LIMIT
        ):
            break

    paths = [
        str(item[2])
        for item in sorted(
            matches,
            key=lambda item: (-item[0], item[1], str(item[2]).lower()),
        )[:max(1, limit)]
    ]
    set_context("last_file_results", paths)
    return paths


def describe_file_results(query, paths):
    if not paths:
        return f"Nie znalazłem plików pasujących do: {query}."

    names = ", ".join(Path(path).name for path in paths[:5])
    if len(paths) == 1:
        return f"Znalazłem: {names}."
    return f"Znalazłem {len(paths)} wyników: {names}."


def _context_path(target):
    normalized = _normalize(target)
    results = get_context("last_file_results", [])
    if not isinstance(results, list) or not results:
        return None

    index_words = {
        "pierwszy": 0,
        "pierwszy wynik": 0,
        "drugi": 1,
        "drugi wynik": 1,
        "trzeci": 2,
        "trzeci wynik": 2,
        "ostatni": len(results) - 1,
        "to": 0,
        "ten plik": 0,
    }
    index = index_words.get(normalized)
    if index is None or not 0 <= index < len(results):
        return None
    return Path(results[index])


def _direct_path(target):
    expanded = os.path.expandvars(target)
    path = Path(expanded).expanduser()
    if path.exists():
        return path.resolve()
    return None


def _open_path(path):
    if path.is_file() and path.suffix.lower() in BLOCKED_OPEN_EXTENSIONS:
        return (
            False,
            "Nie uruchamiam plików wykonywalnych jako dokumentów. "
            "Podaj nazwę aplikacji.",
        )

    try:
        os.startfile(path)
    except OSError as error:
        print("Błąd otwierania pliku:", error)
        return False, f"Nie udało mi się otworzyć {path.name}."

    kind = "folder" if path.is_dir() else "plik"
    return True, f"Otwieram {kind} {path.name}."


def open_file_or_folder(target):
    target = _clean_argument(target, 300)
    if not target:
        return "Co mam otworzyć?"

    path = _context_path(target) or _direct_path(target)
    if path is None:
        results = find_files(target, limit=5)
        if not results:
            return f"Nie znalazłem: {target}."
        path = Path(results[0])

    _success, message = _open_path(path)
    return message


def _visible_application_names():
    if os.name != "nt":
        return set()

    process_ids = set()
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )

    def visit_window(window, _parameter):
        if (
            user32.IsWindowVisible(window)
            and user32.GetWindowTextLengthW(window) > 0
        ):
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(
                window,
                ctypes.byref(process_id),
            )
            if process_id.value:
                process_ids.add(process_id.value)
        return True

    user32.EnumWindows(callback_type(visit_window), 0)
    names = set()
    for process_id in process_ids:
        try:
            name = _normalize(Path(psutil.Process(process_id).name()).stem)
        except (psutil.Error, OSError):
            continue
        if name:
            names.add(PROCESS_DISPLAY_NAMES.get(name, name.title()))
    return names


def list_running_apps(limit=12):
    visible = _visible_application_names()
    if visible:
        selected = sorted(visible, key=str.casefold)[:max(1, limit)]
        return "Widoczne aplikacje: " + ", ".join(selected) + "."

    names = set()
    for process in psutil.process_iter(["name"]):
        try:
            name = Path(process.info.get("name") or "").stem
        except (psutil.Error, OSError):
            continue

        normalized = _normalize(name)
        if not normalized or normalized in PROTECTED_PROCESSES:
            continue
        names.add(name)

    selected = sorted(names, key=str.casefold)[:max(1, limit)]
    if not selected:
        return "Nie udało mi się odczytać uruchomionych aplikacji."
    return "Uruchomione programy to między innymi: " + ", ".join(selected) + "."


def close_application(app_name, *, allow_user_owned: bool = True):
    target = _normalize(_clean_argument(app_name, 100))
    target = PROCESS_ALIASES.get(target, target)
    if not target:
        return "Nie podano nazwy aplikacji."
    if target in PROTECTED_PROCESSES:
        return "Nie zamknę krytycznego procesu systemowego."

    matches = []
    for process in psutil.process_iter(["pid", "name"]):
        try:
            process_name = _normalize(
                Path(process.info.get("name") or "").stem
            )
            if (
                process.pid != os.getpid()
                and process_name == target
                and process_name not in PROTECTED_PROCESSES
            ):
                matches.append(process)
        except (psutil.Error, OSError):
            continue

    if not matches:
        return f"Nie znalazłem uruchomionej aplikacji {app_name}."

    # Automated suite only: never terminate pre-existing / playing user Spotify.
    # Voice path keeps allow_user_owned=True (default) so "zamknij Spotify" works.
    if not allow_user_owned:
        try:
            from core.app_lifecycle_guard import (
                filter_close_pids,
                protect_user_apps_enabled,
                should_block_close,
            )

            if protect_user_apps_enabled():
                block, reason = should_block_close(target, explicit_user_request=False)
                if block:
                    return f"Pomijam zamknięcie {app_name} ({reason})."
                allowed = filter_close_pids(target, {p.pid for p in matches})
                matches = [p for p in matches if p.pid in allowed]
                if not matches:
                    return f"Pomijam zamknięcie {app_name} (USER_OWNED_PROCESS)."
        except Exception:
            pass

    closed = 0
    for process in matches:
        try:
            process.terminate()
            closed += 1
        except psutil.Error:
            continue

    if not closed:
        return f"Nie udało mi się zamknąć aplikacji {app_name}."
    return f"Zamykam aplikację {app_name}."


def take_screenshot():
    folder = Path.home() / "Pictures" / "Elian" / "Screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    filename = folder / (
        "screenshot-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".png"
    )

    try:
        image = pyautogui.screenshot()
        image.save(filename)
    except Exception as error:
        print("Błąd zrzutu ekranu:", error)
        return "Nie udało mi się zrobić zrzutu ekranu."

    set_context("last_screenshot", str(filename))
    return f"Zapisuję zrzut ekranu jako {filename.name}."


def show_desktop():
    pyautogui.hotkey("win", "d")
    return "Pokazuję pulpit."


def switch_window():
    pyautogui.hotkey("alt", "tab")
    return "Przełączam okno."


def close_active_window():
    pyautogui.hotkey("alt", "f4")
    return "Zamykam aktywne okno."


def _open_clipboard():
    for _attempt in range(8):
        if ctypes.windll.user32.OpenClipboard(None):
            return True
        time.sleep(0.05)
    return False


def read_clipboard():
    if os.name != "nt" or not _open_clipboard():
        return "Nie udało mi się odczytać schowka."

    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalLock.restype = ctypes.c_void_p
        handle = user32.GetClipboardData(13)
        if not handle:
            return "Schowek nie zawiera tekstu."
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return "Nie udało mi się odczytać schowka."
        try:
            text = ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        ctypes.windll.user32.CloseClipboard()

    text = _clean_argument(text, 500)
    if not text:
        return "Schowek nie zawiera tekstu."
    return f"W schowku jest: {text}"


def copy_to_clipboard(text):
    text = _clean_argument(text, 2000)
    if not text:
        return "Co mam skopiować?"
    if os.name != "nt" or not _open_clipboard():
        return "Nie udało mi się otworzyć schowka."

    data = (text + "\0").encode("utf-16-le")
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.restype = ctypes.c_void_p
    user32.SetClipboardData.restype = wintypes.HANDLE
    handle = kernel32.GlobalAlloc(0x0002, len(data))
    if not handle:
        user32.CloseClipboard()
        return "Nie udało mi się zapisać tekstu w schowku."

    transferred = False
    try:
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return "Nie udało mi się zapisać tekstu w schowku."
        try:
            ctypes.memmove(pointer, data, len(data))
        finally:
            kernel32.GlobalUnlock(handle)

        user32.EmptyClipboard()
        if not user32.SetClipboardData(13, handle):
            return "Nie udało mi się zapisać tekstu w schowku."
        transferred = True
    finally:
        user32.CloseClipboard()
        if not transferred:
            kernel32.GlobalFree(handle)

    return "Skopiowałem tekst do schowka."


def power_action(action):
    if action == "shutdown":
        subprocess.Popen(["shutdown", "/s", "/t", "10"])
        return "Wyłączam komputer za dziesięć sekund."
    if action == "restart":
        subprocess.Popen(["shutdown", "/r", "/t", "10"])
        return "Uruchamiam komputer ponownie za dziesięć sekund."
    if action == "lock":
        if ctypes.windll.user32.LockWorkStation():
            return "Blokuję komputer."
        return "Nie udało mi się zablokować komputera."
    return None
