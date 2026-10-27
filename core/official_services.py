# ==========================
# ELIAN — trusted official service domains (Phase 12.3.3)
# ==========================
"""Never guess name.com. Only known/verified official URLs."""

from __future__ import annotations

import re
from typing import Dict, Optional


# Canonical service key → official HTTPS homepage (navigation, not search).
OFFICIAL_SERVICE_URLS: Dict[str, str] = {
    "spotify": "https://open.spotify.com",
    "discord": "https://discord.com/app",
    "steam": "https://store.steampowered.com",
    "youtube music": "https://music.youtube.com",
    "youtube": "https://www.youtube.com",
    "epic games": "https://store.epicgames.com",
    "epic": "https://store.epicgames.com",
    "chrome": "https://www.google.com/chrome/",
    "google chrome": "https://www.google.com/chrome/",
    "google": "https://www.google.com",
    "netflix": "https://www.netflix.com",
    "github": "https://github.com",
    "gmail": "https://mail.google.com",
    "google calendar": "https://calendar.google.com",
    "calendar.google": "https://calendar.google.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "reddit": "https://www.reddit.com",
    "twitch": "https://www.twitch.tv",
    "wikipedia": "https://pl.wikipedia.org",
    "allegro": "https://allegro.pl",
    "edge": "https://www.microsoft.com/edge",
    "microsoft edge": "https://www.microsoft.com/edge",
    "firefox": "https://www.mozilla.org/firefox/",
    "notepad": "https://apps.microsoft.com/detail/9msmlrh6lzf3",
    "notatnik": "https://apps.microsoft.com/detail/9msmlrh6lzf3",
}

_ALIASES: Dict[str, str] = {
    "spotiify": "spotify",
    "steama": "steam",
    "discorda": "discord",
    "epic games launcher": "epic games",
    "epica": "epic games",
    "yt music": "youtube music",
    "youtubemusic": "youtube music",
}

_DOMAIN_IN_TEXT = re.compile(
    r"\b(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)\b",
    re.I,
)


def normalize_service_key(name: str) -> str:
    raw = str(name or "").strip().lower()
    raw = re.sub(r"^https?://", "", raw)
    raw = re.sub(r"^www\.", "", raw)
    raw = re.sub(r"/(?:.*)?$", "", raw)
    raw = re.sub(r"\.(com|pl|net|org|app)$", "", raw)
    raw = re.sub(r"[.]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return _ALIASES.get(raw, raw)


def official_url_for(name: str) -> Optional[str]:
    """Return a trusted official URL or None — never invent a TLD."""
    key = normalize_service_key(name)
    if not key:
        return None
    if key in OFFICIAL_SERVICE_URLS:
        return OFFICIAL_SERVICE_URLS[key]
    # User typed an explicit host (spotify.com) — map to registry if known.
    host = key.replace(" ", "")
    for canon, url in OFFICIAL_SERVICE_URLS.items():
        compact = canon.replace(" ", "")
        if host == compact or host.startswith(compact + "."):
            return url
    return None


def explicit_domain_in_text(text: str) -> Optional[str]:
    match = _DOMAIN_IN_TEXT.search(str(text or ""))
    if not match:
        return None
    host = match.group(1).lower()
    mapped = official_url_for(host.split(".")[0])
    if mapped:
        return mapped
    # User-supplied host is allowed as navigation target (not a guessed brand.com).
    return "https://" + host


def canonical_service_name(name: str) -> str:
    key = normalize_service_key(name)
    pretty = {
        "spotify": "Spotify",
        "discord": "Discord",
        "steam": "Steam",
        "youtube music": "YouTube Music",
        "youtube": "YouTube",
        "epic games": "Epic Games",
        "epic": "Epic Games",
        "chrome": "Chrome",
        "google chrome": "Chrome",
        "gmail": "Gmail",
        "google calendar": "Google Calendar",
        "netflix": "Netflix",
        "github": "GitHub",
        "google": "Google",
    }
    if key in pretty:
        return pretty[key]
    text = str(name or "").strip()
    return text[:1].upper() + text[1:] if text else ""


def register_official_service(key: str, url: str, *, display_name: str = "") -> None:
    """Extend the verified registry. Never call this with a guessed domain."""
    canon = normalize_service_key(key)
    href = str(url or "").strip()
    if not canon or not href.startswith("https://"):
        return
    OFFICIAL_SERVICE_URLS[canon] = href.rstrip("/")
    if display_name:
        _ALIASES[str(display_name or "").strip().lower()] = canon
