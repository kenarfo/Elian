import re

from AppRegistry import (
    find_game_launchers,
    find_games,
    find_launcher,
    find_music_apps,
    launch_entry,
    open_music_service,
)
from AppLauncher import launch_application
from ConversationContext import (
    clear_pending_goal,
    get_pending_goal,
    set_pending_goal,
    update as update_context,
)
from DeviceResolver import normalize_pl
from Memory import record_action
from WebActions import normalize_name, search_youtube


MUSIC_GENERIC = re.compile(
    r"(?:chce|chcialbym|chcialabym|mam ochote|mozemy)\s+"
    r"(?:posluchac(?: muzyki| muzyke)?|odsluchac|wlaczyc muzyke|"
    r"odpalic muzyke|cos posluchac)\b",
    re.I,
)

MUSIC_WANT_TRACK = re.compile(
    r"chce\s+posluchac\s+(?!muzyki?\b|muzyke\b|cos\b)(.+)",
    re.I,
)

MUSIC_DIRECT_TRACK = re.compile(
    r"(?:posluchaj|puszcz|odtworz|odpal)\s+(?:muzyke\s+|utwor\s+)?(.+)",
    re.I,
)

MUSIC_OCHOTA_TRACK = re.compile(
    r"mam ochote(?: posluchac)?\s+(?!muzyki?\b|muzyke\b|cos\b)(.+)",
    re.I,
)

GAME_GENERIC = re.compile(
    r"(?:chce|chcialbym|chcialabym|mam ochote)\s+"
    r"(?:w cos\s+)?(?:pograc|zagrac|pobawic sie)",
    re.I,
)

GAME_WITH_TITLE = re.compile(
    r"(?:chce|chcialbym|chcialabym|mam ochote)\s+"
    r"(?:pograc|zagrac)\s+w\s+(.+)",
    re.I,
)

LAUNCH_LAUNCHER = re.compile(
    r"(?:odpal|uruchom|otworz)\s+(?:mi\s+)?"
    r"(steam|steama|epic games(?: launcher)?|xbox(?: app)?|"
    r"ea app|origin|ubisoft(?: connect)?|gog galaxy|battle\.net)\b",
    re.I,
)

WATCH_GENERIC = re.compile(
    r"(?:chce|chcialbym|chcialabym|mam ochote)\s+"
    r"(?:cos\s+)?(?:obejrzec|zobaczyc|cos obejrzec)",
    re.I,
)

WEB_GENERIC = re.compile(
    r"(?:sprawdz|sprawdz mi|poszukaj|znajdz)\s+(?:cos\s+)?(?:w\s+)?internecie",
    re.I,
)

SERVICE_CHOICE = re.compile(
    r"^(spotify|youtube music|tidal|deezer|apple music|steam|epic games|"
    r"ea app|ubisoft|xbox|netflix|youtube)$",
    re.I,
)


def _normalize(text):
    return normalize_pl(str(text or "").lower().strip())


def _clean_fragment(text):
    text = str(text or "").strip(" .,!?:;\"'")
    text = re.sub(
        r"^(?:prosze|prosze cie|mi|teraz)\s+",
        "",
        text,
        flags=re.I,
    )
    return text.strip(" .,!?:;\"'")


def _format_options(options):
    if len(options) == 2:
        return f"{options[0]} czy {options[1]}"
    return ", ".join(options[:-1]) + f" czy {options[-1]}"


def _set_pending(goal_type, stage, **extra):
    goal = {"type": goal_type, "stage": stage}
    goal.update(extra)
    set_pending_goal(goal)


def _continue_pending(text):
    pending = get_pending_goal()
    if not pending:
        return None

    normalized = _normalize(text)
    goal_type = pending.get("type")
    stage = pending.get("stage")

    if goal_type == "music":
        if stage == "awaiting_service":
            choice = _resolve_service_choice(normalized, pending.get("options", []))
            if choice is None:
                return "Którą usługę mam użyć?"
            clear_pending_goal()
            if pending.get("query"):
                success, message = open_music_service(choice, pending["query"])
            else:
                _set_pending("music", "awaiting_track", service=choice)
                update_context(last_application=choice, last_app=choice)
                return f"Jasne. Czego chcesz posłuchać w {choice}?"
            record_action("music_play", argument=pending.get("query", ""), result=message, success=success)
            update_context(last_application=choice, last_app=choice)
            return message

        if stage == "awaiting_track":
            track = _clean_fragment(text)
            if not track or len(track) < 2:
                return "Czego chcesz posłuchać?"
            service = pending.get("service")
            clear_pending_goal()
            if not service:
                apps = find_music_apps()
                service = apps[0].name if apps else "spotify"
            success, message = open_music_service(service, track)
            record_action("music_play", argument=track, result=message, success=success)
            update_context(last_application=service, last_app=service, last_query=track)
            return message

    if goal_type == "game":
        if stage == "awaiting_game":
            title = _clean_fragment(text)
            if not title:
                return "W co chcesz zagrać?"
            clear_pending_goal()
            return _launch_game(title)

    if goal_type == "watch":
        if stage == "awaiting_title":
            title = _clean_fragment(text)
            if not title:
                return "Co chcesz obejrzeć?"
            clear_pending_goal()
            success, message = search_youtube(title)
            record_action("youtube_search", argument=title, result=message, success=success)
            return message

    if goal_type == "web_research":
        if stage == "awaiting_query":
            query = _clean_fragment(text)
            if not query:
                return "Co mam sprawdzić w internecie?"
            clear_pending_goal()
            from WebResearch import research
            return research(query)

    clear_pending_goal()
    return None


def _resolve_service_choice(normalized, options):
    if not options:
        return None
    for option in options:
        if normalize_name(option) in normalized or normalized in normalize_name(option):
            return option
    match = SERVICE_CHOICE.match(normalized)
    if match:
        token = match.group(1).lower()
        for option in options:
            if token in normalize_name(option):
                return option
    return None


def _launch_game(title):
    games = find_games(title)
    if len(games) == 1:
        success, message = launch_entry(games[0])
        record_action("launch_game", argument=title, result=message, success=success)
        if success:
            update_context(last_application=games[0].name, last_app=games[0].name)
        return message

    if len(games) > 1:
        names = [game.name for game in games[:3]]
        _set_pending("game", "awaiting_game", options=names, hint=title)
        return f"Znalazłem kilka gier: {_format_options(names)}. Którą uruchomić?"

    launcher = find_launcher(title)
    if launcher:
        success, message = launch_entry(launcher)
        record_action("launch_launcher", argument=title, result=message, success=success)
        return message

    success, message = launch_application(title)
    record_action("launch_game", argument=title, result=message, success=success)
    if success:
        update_context(last_application=title, last_app=title)
        return message
    return (
        f"Nie znalazłem gry {title} na tym komputerze. "
        "Spróbuj podać dokładniejszą nazwę albo uruchomić launcher."
    )


def _start_music(query=None):
    apps = find_music_apps()
    if not apps:
        if query:
            success, message = open_music_service("spotify", query)
            record_action("music_play", argument=query, result=message, success=success)
            return message
        success, message = open_music_service("spotify")
        record_action("open_music", result=message, success=success)
        return message

    if len(apps) == 1:
        service = apps[0].name
        if query:
            success, message = open_music_service(service, query)
            record_action("music_play", argument=query, result=message, success=success)
            update_context(last_application=service, last_app=service)
            return message
        _set_pending("music", "awaiting_track", service=service)
        update_context(last_application=service, last_app=service)
        return "Jasne. Czego chcesz posłuchać?"

    names = [app.name for app in apps[:4]]
    if query:
        _set_pending(
            "music",
            "awaiting_service",
            options=names,
            query=query,
        )
        return f"Mam {len(names)} usługi muzyczne: {_format_options(names)}. Którą wybrać?"
    _set_pending("music", "awaiting_service", options=names)
    return f"Jasne. Którą usługę muzyczną mam otworzyć: {_format_options(names)}?"


def _detect_music(text):
    normalized = _normalize(text)
    if MUSIC_GENERIC.search(normalized):
        return ""

    for pattern in (MUSIC_WANT_TRACK, MUSIC_OCHOTA_TRACK, MUSIC_DIRECT_TRACK):
        match = pattern.search(normalized)
        if match:
            candidate = _clean_fragment(match.group(1))
            if candidate and candidate not in ("muzyke", "muzyki", "muzyka", "cos"):
                return candidate
    return None


def _try_launcher_command(text):
    normalized = _normalize(text)
    match = LAUNCH_LAUNCHER.search(normalized)
    if not match:
        return None
    target = _clean_fragment(match.group(1))
    if not target:
        return None

    launcher = find_launcher(target)
    if launcher:
        success, message = launch_entry(launcher)
        record_action("launch_launcher", argument=target, result=message, success=success)
        return message
    return None


def handle_goal(text):
    continued = _continue_pending(text)
    if continued is not None:
        return continued

    normalized = _normalize(text)

    launcher_result = _try_launcher_command(text)
    if launcher_result:
        return launcher_result

    game_match = GAME_WITH_TITLE.search(normalized)
    if game_match:
        return _launch_game(_clean_fragment(game_match.group(1)))

    if GAME_GENERIC.search(normalized):
        _set_pending("game", "awaiting_game")
        return "W co chcesz zagrać?"

    music_query = _detect_music(text)
    if music_query is not None:
        return _start_music(music_query or None)

    if WATCH_GENERIC.search(normalized):
        _set_pending("watch", "awaiting_title")
        return "Co chcesz obejrzeć?"

    if WEB_GENERIC.search(normalized):
        _set_pending("web_research", "awaiting_query")
        return "Co mam sprawdzić w internecie?"

    return None
