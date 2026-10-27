import os
import re
from dataclasses import dataclass, field
from difflib import get_close_matches

from AppLauncher import get_app_index, launch_application, normalize_name
from Config import get_config_value


@dataclass
class AppEntry:
    name: str
    path: str
    capabilities: set = field(default_factory=set)
    priority: int = 0


MUSIC_PATTERNS = (
    (re.compile(r"\bspotify\b"), "spotify", 100),
    (re.compile(r"\byoutube music\b"), "youtube music", 95),
    (re.compile(r"\btidal\b"), "tidal", 90),
    (re.compile(r"\bdeezer\b"), "deezer", 90),
    (re.compile(r"\bapple music\b"), "apple music", 88),
    (re.compile(r"\bamazon music\b"), "amazon music", 85),
    (re.compile(r"\bsoundcloud\b"), "soundcloud", 80),
)

GAME_LAUNCHER_PATTERNS = (
    (re.compile(r"\bsteam\b"), "steam", 100),
    (re.compile(r"\bepic games\b"), "epic games", 95),
    (re.compile(r"\bea app\b|\borigin\b"), "ea app", 90),
    (re.compile(r"\bubisoft connect\b|\buplay\b"), "ubisoft connect", 90),
    (re.compile(r"\bxbox\b"), "xbox", 88),
    (re.compile(r"\bgog galaxy\b"), "gog galaxy", 85),
    (re.compile(r"\bbattle\.net\b"), "battle.net", 85),
)

BROWSER_PATTERNS = (
    (re.compile(r"\bgoogle chrome\b|\bchrome\b"), "chrome", 100),
    (re.compile(r"\bmicrosoft edge\b|\bedge\b"), "edge", 95),
    (re.compile(r"\bmozilla firefox\b|\bfirefox\b"), "firefox", 95),
    (re.compile(r"\bopera\b"), "opera", 85),
    (re.compile(r"\bbrave\b"), "brave", 85),
)

AUDIO_UTILITY_EXCLUDE = re.compile(
    r"(realtek|nvidia|audio driver|driver audio|sound recorder|"
    r"nagrywanie dzwieku|voice recorder|equalizer|mixer|vb-audio|"
    r"dolby|sonic studio|nahimic|waves|control panel audio|"
    r"windows media player|groove music|windows media|"
    r"obs studio|obs\b|audacity|ffmpeg|klite|codec|"
    r"bluetooth audio|intel\(r\) audio|high definition audio)",
    re.I,
)

GAME_NAME_HINT = re.compile(
    r"(fortnite|minecraft|valorant|cs2|counter-strike|cyberpunk|"
    r"witcher|gta|rocket league|apex|overwatch|dota|league of legends)",
    re.I,
)

_registry_cache = None


def _steam_common_paths():
    roots = []
    for env_key in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(env_key)
        if root:
            candidate = os.path.join(root, "Steam", "steamapps", "common")
            if os.path.isdir(candidate):
                roots.append(candidate)
    return roots


def _scan_steam_games():
    entries = []
    for root in _steam_common_paths():
        try:
            for name in os.listdir(root):
                folder = os.path.join(root, name)
                if os.path.isdir(folder):
                    entries.append((normalize_name(name), folder))
        except OSError:
            continue
    return entries


def _match_patterns(name, patterns):
    for pattern, canonical, priority in patterns:
        if pattern.search(name):
            return canonical, priority
    return None, 0


def is_audio_utility(name):
    normalized = normalize_name(name)
    return bool(AUDIO_UTILITY_EXCLUDE.search(normalized))


def classify_app(name, path=""):
    normalized = normalize_name(name)
    capabilities = set()
    canonical = normalized
    priority = 0

    if is_audio_utility(normalized):
        return AppEntry(name=canonical or normalized, path=path, capabilities=capabilities)

    music_name, music_priority = _match_patterns(normalized, MUSIC_PATTERNS)
    if music_name:
        capabilities.add("music")
        canonical = music_name
        priority = max(priority, music_priority)

    launcher_name, launcher_priority = _match_patterns(normalized, GAME_LAUNCHER_PATTERNS)
    if launcher_name:
        capabilities.add("game_launcher")
        canonical = launcher_name
        priority = max(priority, launcher_priority)

    browser_name, browser_priority = _match_patterns(normalized, BROWSER_PATTERNS)
    if browser_name:
        capabilities.add("browser")
        canonical = browser_name
        priority = max(priority, browser_priority)

    lower = normalized.lower()
    if (
        " game" in lower
        or lower.endswith(" game")
        or GAME_NAME_HINT.search(normalized)
    ) and "launcher" not in lower:
        capabilities.add("game")

    if path and path.lower().endswith(".exe") and "steamapps\\common" in path.lower():
        capabilities.add("game")

    return AppEntry(
        name=canonical or normalized,
        path=path,
        capabilities=capabilities,
        priority=priority,
    )


def _build_registry():
    entries = {}
    for raw_name, path in get_app_index():
        entry = classify_app(raw_name, path)
        if not entry.capabilities and not raw_name:
            continue
        key = entry.name
        existing = entries.get(key)
        if existing is None or entry.priority > existing.priority:
            entries[key] = entry

    for raw_name, folder in _scan_steam_games():
        entry = classify_app(raw_name, folder)
        entry.capabilities.add("game")
        existing = entries.get(entry.name)
        if existing is None:
            entries[entry.name] = entry

    return list(entries.values())


def get_registry(refresh=False):
    global _registry_cache
    if _registry_cache is None or refresh:
        _registry_cache = _build_registry()
    return list(_registry_cache)


def reset_registry_cache():
    global _registry_cache
    _registry_cache = None


def find_music_apps():
    apps = [
        entry
        for entry in get_registry()
        if "music" in entry.capabilities and not is_audio_utility(entry.name)
    ]
    apps.sort(key=lambda item: (-item.priority, item.name))
    return apps


def find_game_launchers():
    apps = [
        entry
        for entry in get_registry()
        if "game_launcher" in entry.capabilities
    ]
    apps.sort(key=lambda item: (-item.priority, item.name))
    return apps


def find_browsers():
    apps = [
        entry
        for entry in get_registry()
        if "browser" in entry.capabilities
    ]
    apps.sort(key=lambda item: (-item.priority, item.name))
    return apps


def find_games(query):
    target = normalize_name(query)
    if not target:
        return []

    matches = []
    for entry in get_registry():
        if "game" not in entry.capabilities:
            continue
        name = normalize_name(entry.name)
        if target == name or target in name or name.startswith(target):
            matches.append(entry)

    if not matches:
        names = [
            entry.name
            for entry in get_registry()
            if "game" in entry.capabilities
        ]
        close = get_close_matches(target, names, n=3, cutoff=0.72)
        name_set = set(close)
        matches = [
            entry
            for entry in get_registry()
            if entry.name in name_set
        ]

    matches.sort(key=lambda item: (len(item.name), item.name))
    return matches


def find_launcher(query):
    target = normalize_name(query)
    for entry in find_game_launchers():
        if target == normalize_name(entry.name) or target in normalize_name(entry.name):
            return entry
    names = [entry.name for entry in find_game_launchers()]
    close = get_close_matches(target, names, n=1, cutoff=0.75)
    if close:
        return next(entry for entry in find_game_launchers() if entry.name == close[0])
    return None


def launch_entry(entry):
    if entry.path and os.path.isdir(entry.path):
        try:
            os.startfile(entry.path)
            return True, f"Uruchamiam {entry.name}."
        except OSError as error:
            print("Błąd uruchamiania gry:", error)
            return False, f"Nie udało się uruchomić {entry.name}."
    return launch_application(entry.name)


def preferred_music_web_url(query=""):
    query = str(query or "").strip()
    if query:
        from urllib.parse import quote_plus
        return f"https://open.spotify.com/search/{quote_plus(query)}"
    return "https://open.spotify.com"


def music_search_url(service_name, query):
    from urllib.parse import quote_plus
    service = normalize_name(service_name)
    encoded = quote_plus(query.strip())
    if "spotify" in service:
        return f"https://open.spotify.com/search/{encoded}"
    if "youtube" in service:
        return f"https://music.youtube.com/search?q={encoded}"
    if "tidal" in service:
        return f"https://listen.tidal.com/search?q={encoded}"
    if "deezer" in service:
        return f"https://www.deezer.com/search/{encoded}"
    return preferred_music_web_url(query)


def open_music_service(service_name, query=""):
    import webbrowser
    from urllib.parse import quote_plus

    apps = find_music_apps()
    service = normalize_name(service_name)
    chosen = next(
        (entry for entry in apps if service in normalize_name(entry.name)),
        None,
    )
    if chosen:
        if query:
            url = music_search_url(chosen.name, query)
            try:
                webbrowser.open(url, new=2)
            except (OSError, webbrowser.Error):
                pass
            return launch_entry(chosen)
        return launch_entry(chosen)

    if query:
        url = preferred_music_web_url(query)
    else:
        url = preferred_music_web_url()
    try:
        opened = webbrowser.open(url, new=2)
    except (OSError, webbrowser.Error):
        opened = False
    if opened is False:
        return False, "Nie udało się otworzyć usługi muzycznej."
    if query:
        return True, f"Otwieram wyszukiwanie: {query}."
    return True, "Otwieram usługę muzyczną w przeglądarce."


def registry_for_tests(entries):
    """Inject a synthetic registry in unit tests."""
    global _registry_cache
    _registry_cache = list(entries)
