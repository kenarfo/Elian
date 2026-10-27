# ==========================
# ELIAN — app-first service open (Phase 12.3.3)
# ==========================
"""Local installed app first; verified official website fallback. No guessed URLs."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from AppLauncher import find_start_menu_app, launch_application
from core.official_services import (
    canonical_service_name,
    explicit_domain_in_text,
    official_url_for,
)


KIND_APP = "APP"
KIND_WEBSITE = "WEBSITE"

POLICY_APP_FIRST = "APP_FIRST_OFFICIAL_WEB_FALLBACK"
POLICY_WEBSITE_DIRECT = "OFFICIAL_WEBSITE"
POLICY_EXPLICIT_APP = "EXPLICIT_APP"

_WEBSITE_WORDS = re.compile(
    r"(?:"
    r"\bstron[aęey]\b|"
    r"\bwitryn[aęey]\b|"
    r"\bwebsite\b|"
    r"\bwww\b|"
    r"\burl\b|"
    r"\blink\b|"
    r"\.(?:com|pl|net|org)\b"
    r")",
    re.I,
)

_EXPLICIT_APP = re.compile(
    r"\b(?:aplikacj[aęei]|program)\b",
    re.I,
)

_WEBSITE_FOLLOWUP = re.compile(
    r"^(?:a\s+)?(?:teraz\s+)?(?:stron[aęey]|witryn[aęey]|website)\b",
    re.I,
)

_APP_FOLLOWUP = re.compile(
    r"^(?:nie[,.]?\s+)?(?:aplikacj[aęei]|program)\b",
    re.I,
)


@dataclass
class ServiceOpenResult:
    requested_name: str = ""
    resolved_kind: str = ""
    canonical_name: str = ""
    app_found: bool = False
    launched: bool = False
    official_url: str = ""
    official_url_verified: bool = False
    success: bool = False
    fallback_used: bool = False
    app_checked: bool = False
    message: str = ""
    tool: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def looks_explicit_website(text: str) -> bool:
    raw = str(text or "")
    if explicit_domain_in_text(raw):
        return True
    return bool(_WEBSITE_WORDS.search(raw))


def looks_explicit_app_wording(text: str) -> bool:
    return bool(_EXPLICIT_APP.search(str(text or ""))) and not looks_explicit_website(text)


def looks_website_followup(text: str) -> bool:
    return bool(_WEBSITE_FOLLOWUP.match(str(text or "").strip()))


def looks_app_followup(text: str) -> bool:
    return bool(_APP_FOLLOWUP.match(str(text or "").strip()))


def resolution_policy_for(text: str) -> str:
    if looks_explicit_website(text):
        return POLICY_WEBSITE_DIRECT
    if looks_explicit_app_wording(text):
        return POLICY_EXPLICIT_APP
    return POLICY_APP_FIRST


def _open_official(name: str, *, fallback: bool, checked: bool, text: str = "") -> ServiceOpenResult:
    from WebActions import open_website

    canon = canonical_service_name(name)
    verified = official_url_for(name) or official_url_for(text)
    url = verified or explicit_domain_in_text(text or name) or ""
    result = ServiceOpenResult(
        requested_name=name,
        canonical_name=canon,
        official_url=url,
        official_url_verified=bool(verified),
        app_checked=checked,
        app_found=False,
        fallback_used=fallback,
        resolved_kind=KIND_WEBSITE,
        tool="open_website",
    )
    if not url:
        result.message = (
            f"Nie znalazłem aplikacji ani pewnej oficjalnej strony {canon}."
        )
        return result
    ok, message = open_website(url)
    result.success = bool(ok)
    result.launched = bool(ok)
    if ok:
        if fallback:
            result.message = (
                f"Nie znalazłem aplikacji {canon}, więc otworzyłem jej oficjalną stronę."
            )
        else:
            result.message = f"Otwieram oficjalną stronę {canon}."
    else:
        result.message = message or f"Nie udało mi się otworzyć strony {canon}."
    return result


def open_named_service(
    name: str,
    *,
    text: str = "",
    website_direct: Optional[bool] = None,
    explicit_app: Optional[bool] = None,
) -> ServiceOpenResult:
    requested = str(name or "").strip()
    if not requested:
        return ServiceOpenResult(message="Nie podano nazwy aplikacji.")

    policy = resolution_policy_for(text or requested)
    if website_direct is True:
        policy = POLICY_WEBSITE_DIRECT
    if explicit_app is True:
        policy = POLICY_EXPLICIT_APP

    canon = canonical_service_name(requested)

    if policy == POLICY_WEBSITE_DIRECT:
        return _open_official(requested, fallback=False, checked=False, text=text or requested)

    path = find_start_menu_app(requested)
    app_found = bool(path)
    if app_found:
        success, message = launch_application(requested)
        return ServiceOpenResult(
            requested_name=requested,
            resolved_kind=KIND_APP,
            canonical_name=canon,
            app_found=True,
            launched=bool(success),
            success=bool(success),
            fallback_used=False,
            app_checked=True,
            tool="open_app",
            message=("Spotify otwarte." if success and canon.lower() == "spotify" else (
                f"{canon} otwarte." if success else (message or f"Nie udało mi się otworzyć {canon}.")
            )),
        )

    # Missing local app
    if policy == POLICY_EXPLICIT_APP:
        fallback = _open_official(
            requested, fallback=True, checked=True, text=text or requested
        )
        if fallback.success:
            fallback.message = (
                f"Nie znalazłem aplikacji {canon}, więc otworzyłem oficjalną stronę {canon}."
            )
        return fallback

    return _open_official(requested, fallback=True, checked=True, text=text or requested)
