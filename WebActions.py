import re
import unicodedata
import webbrowser
from urllib.parse import quote_plus, urlsplit

from Config import config


KNOWN_WEBSITES = {
    "allegro": "https://allegro.pl",
    "facebook": "https://www.facebook.com",
    "github": "https://github.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "instagram": "https://www.instagram.com",
    "onet": "https://www.onet.pl",
    "reddit": "https://www.reddit.com",
    "twitch": "https://www.twitch.tv",
    "wikipedia": "https://pl.wikipedia.org",
    "youtube": "https://www.youtube.com",
}


def normalize_name(text):
    normalized = unicodedata.normalize("NFKD", text.lower())
    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    normalized = re.sub(r"[^a-z0-9. -]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip(" .")


def _open(url):
    try:
        opened = webbrowser.open(url, new=2)
    except (OSError, webbrowser.Error) as error:
        print("Błąd otwierania przeglądarki:", error)
        return False

    return opened is not False


def resolve_website(target):
    if not isinstance(target, str):
        return None

    target = target.strip().strip(".,!?;:'\"")
    if not target or len(target) > 300 or "\n" in target:
        return None

    name = normalize_name(target)
    for prefix in (
        "strone internetowa ",
        "strone ",
        "witryne ",
        "serwis ",
    ):
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
            break

    if name in ("home assistant", "homeassistant"):
        return config.get("smart_home", {}).get(
            "url",
            "http://homeassistant.local:8123",
        ).rstrip("/")

    if name in KNOWN_WEBSITES:
        return KNOWN_WEBSITES[name]

    try:
        from core.official_services import official_url_for

        official = official_url_for(name) or official_url_for(target)
        if official:
            return official
    except Exception:
        pass

    candidate = target
    if not re.match(r"^[a-z][a-z0-9+.-]*://", candidate, re.I):
        if not re.match(
            r"^(?:www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:[/:?#].*)?$",
            candidate,
            re.I,
        ):
            return None
        candidate = "https://" + candidate

    parsed = urlsplit(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None

    return candidate


def open_website(target):
    url = resolve_website(target)
    if url is None:
        return False, "Nie rozpoznałem adresu strony."

    if not _open(url):
        return False, "Nie udało mi się otworzyć strony."

    name = normalize_name(target) or "stronę"
    return True, f"Otwieram {name}."


def search_web(query):
    """Legacy: open Google results in the browser (Commands / IntentRouter)."""
    query = str(query).strip().strip(".,!?;:")
    if not query:
        return False, "Co mam wyszukać?"

    url = "https://www.google.com/search?q=" + quote_plus(query[:300])
    if not _open(url):
        return False, "Nie udało mi się otworzyć wyszukiwania."

    return True, f"Wyszukuję w internecie: {query}."


def search_web_structured(query, *, max_results=5):
    """Phase 11 — structured search results for the Agent (no browser required)."""
    from core.web_search_provider import print_web_trace, search_structured

    result = search_structured(query, max_results=max_results)
    print_web_trace(result)
    return result


def search_youtube(query):
    query = str(query).strip().strip(".,!?;:")
    if not query:
        return open_website("youtube")

    url = (
        "https://www.youtube.com/results?search_query="
        + quote_plus(query[:300])
    )
    if not _open(url):
        return False, "Nie udało mi się otworzyć YouTube."

    return True, f"Wyszukuję na YouTube: {query}."
