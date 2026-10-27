# ==========================
# ELIAN — media providers (Phase 7)
# ==========================

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from Config import config
from integrations.base import (
    Availability,
    IntegrationProvider,
    IntegrationResult,
    UNAVAILABLE,
    integration_enabled,
)


@dataclass
class NowPlaying:
    title: str = ""
    artist: str = ""
    app: str = ""


class MediaProvider(IntegrationProvider):
    name = "media"
    label = "media"

    def play(self, query: str = "", *, app: str = "") -> IntegrationResult:
        raise NotImplementedError

    def pause(self) -> IntegrationResult:
        raise NotImplementedError

    def resume(self) -> IntegrationResult:
        raise NotImplementedError

    def next_track(self) -> IntegrationResult:
        raise NotImplementedError

    def previous_track(self) -> IntegrationResult:
        raise NotImplementedError

    def now_playing(self) -> IntegrationResult:
        raise NotImplementedError


class FakeMediaProvider(MediaProvider):
    def __init__(self, name="fake_media", label="Fake Media"):
        self.name = name
        self.label = label
        self.available = True
        self.last_query = ""
        self.playing = NowPlaying()
        self.play_calls = 0
        self.paused = False

    def availability(self):
        return Availability.AVAILABLE if self.available else Availability.UNAVAILABLE

    def play(self, query="", *, app=""):
        if not self.available:
            return IntegrationResult(
                False, f"{self.label} niedostępne.", error_code=UNAVAILABLE
            )
        self.play_calls += 1
        self.last_query = query
        self.paused = False
        # naive split "Artist – Title" or store query as title
        self.playing = NowPlaying(title=query or "Unknown", artist="", app=self.name)
        return IntegrationResult(
            True,
            f"Odtwarzam: {query}." if query else "Odtwarzam.",
            data={"query": query, "app": self.name},
        )

    def pause(self):
        self.paused = True
        return IntegrationResult(True, "Pauza.", data={"paused": True})

    def resume(self):
        self.paused = False
        return IntegrationResult(True, "Wznawiam.", data={"paused": False})

    def next_track(self):
        return IntegrationResult(True, "Następny utwór.")

    def previous_track(self):
        return IntegrationResult(True, "Poprzedni utwór.")

    def now_playing(self):
        if not self.playing.title:
            return IntegrationResult(True, "Nic teraz nie leci.", data={"playing": None})
        label = self.playing.title
        if self.playing.artist:
            label = f"{self.playing.artist} – {self.playing.title}"
        return IntegrationResult(
            True,
            f"Teraz leci: {label}.",
            data={
                "playing": {
                    "title": self.playing.title,
                    "artist": self.playing.artist,
                    "app": self.playing.app,
                }
            },
        )


class WindowsMediaProvider(MediaProvider):
    """Local media keys / SMTC-ish control via pyautogui (+ optional AppLauncher)."""

    name = "windows_media"
    label = "Windows Media"

    def availability(self):
        if not integration_enabled("media"):
            return Availability.DISABLED
        try:
            import pyautogui  # noqa: F401

            return Availability.AVAILABLE
        except Exception:
            return Availability.UNAVAILABLE

    def play(self, query="", *, app=""):
        try:
            if app or query:
                from AppLauncher import launch_application
                from AppRegistry import find_music_apps

                target = app
                if not target:
                    # preference handled by router
                    apps = find_music_apps()
                    target = apps[0].name if apps else "spotify"
                if query:
                    # Open service search URL / app — GoalRouter path
                    from AppRegistry import open_music_service

                    ok, message = open_music_service(target, query)
                    return IntegrationResult(
                        bool(ok),
                        message,
                        data={"query": query, "app": target},
                        error_code="" if ok else UNAVAILABLE,
                    )
                ok, message = launch_application(target)
                return IntegrationResult(bool(ok), message, data={"app": target})
            import pyautogui

            pyautogui.press("playpause")
            return IntegrationResult(True, "Odtwarzanie.")
        except Exception as exc:
            return IntegrationResult(
                False, f"Nie udało się uruchomić mediów: {exc}", error_code=UNAVAILABLE
            )

    def pause(self):
        import pyautogui

        pyautogui.press("playpause")
        return IntegrationResult(True, "Pauza.")

    def resume(self):
        return self.pause()

    def next_track(self):
        import pyautogui

        pyautogui.press("nexttrack")
        return IntegrationResult(True, "Następny utwór.")

    def previous_track(self):
        import pyautogui

        pyautogui.press("prevtrack")
        return IntegrationResult(True, "Poprzedni utwór.")

    def now_playing(self):
        return IntegrationResult(
            True,
            "Nie odczytuję teraz tytułu utworu (brak sesji SMTC).",
            data={"playing": None},
        )


class SpotifyProvider(MediaProvider):
    """
    Spotify API foundation. Without secrets → NOT_CONFIGURED.
    No tokens logged. Real Web API can be added later.
    """

    name = "spotify"
    label = "Spotify"

    def availability(self):
        if not integration_enabled("media"):
            return Availability.DISABLED
        from Config import get_secret

        token = get_secret("spotify_token", env_var="ELIAN_SPOTIFY_TOKEN")
        client = get_secret("spotify_client_id", env_var="ELIAN_SPOTIFY_CLIENT_ID")
        if not token and not client:
            return Availability.NOT_CONFIGURED
        if not token:
            return Availability.AUTH_REQUIRED
        return Availability.AVAILABLE

    def play(self, query="", *, app=""):
        status = self.availability()
        if status is Availability.NOT_CONFIGURED:
            return IntegrationResult(
                False,
                "Nie mam jeszcze połączonego Spotify API.",
                availability=status,
                error_code="NOT_CONFIGURED",
            )
        if status is Availability.AUTH_REQUIRED:
            return IntegrationResult(
                False,
                "Spotify wymaga autoryzacji.",
                availability=status,
                error_code="AUTH_REQUIRED",
            )
        # Token present but full Web API not implemented in this phase —
        # fall through message for router to try Windows media.
        return IntegrationResult(
            False,
            "Spotify API nie jest jeszcze w pełni aktywne.",
            availability=Availability.UNAVAILABLE,
            error_code=UNAVAILABLE,
        )

    def pause(self):
        return IntegrationResult(False, "Spotify API niedostępne.", error_code=UNAVAILABLE)

    def resume(self):
        return self.pause()

    def next_track(self):
        return self.pause()

    def previous_track(self):
        return self.pause()

    def now_playing(self):
        return IntegrationResult(False, "Spotify API niedostępne.", error_code=UNAVAILABLE)


def preferred_media_app(preference_get=None) -> str:
    if preference_get:
        value = preference_get("music.default_app", None)
        if value:
            return str(value)
    # memory manager global optional
    try:
        from memory.manager import get_memory_manager

        value = get_memory_manager().get_preference("music.default_app")
        if value:
            return str(value)
    except Exception:
        pass
    block = config.get("integrations", {})
    if isinstance(block, dict):
        media = block.get("media", {})
        if isinstance(media, dict) and media.get("default_app"):
            return str(media["default_app"])
    return "spotify"


def media_providers() -> List[MediaProvider]:
    return [SpotifyProvider(), WindowsMediaProvider()]


def play_with_fallback(query: str, *, preferred_app: str = "") -> IntegrationResult:
    preferred_app = (preferred_app or preferred_media_app()).lower()
    providers = media_providers()
    # Order: preferred match first
    ordered = sorted(
        providers,
        key=lambda p: (0 if preferred_app in p.name.lower() or preferred_app in p.label.lower() else 1),
    )
    last = None
    for provider in ordered:
        status = provider.availability()
        if status in (
            Availability.NOT_CONFIGURED,
            Availability.DISABLED,
            Availability.AUTH_REQUIRED,
            Availability.UNAVAILABLE,
        ):
            last = IntegrationResult(
                False,
                f"{provider.label}: {status.value}",
                availability=status,
                error_code=status.value.upper(),
            )
            continue
        result = provider.play(query, app=preferred_app)
        if result.success:
            return result
        last = result
    # Final web/app fallback via Windows provider explicitly
    win = WindowsMediaProvider()
    return win.play(query, app=preferred_app) if win.availability() is Availability.AVAILABLE else (
        last
        or IntegrationResult(False, "Brak dostępnego odtwarzacza.", error_code=UNAVAILABLE)
    )


_media_override: Optional[MediaProvider] = None


def set_media_provider_for_tests(provider: Optional[MediaProvider]) -> None:
    global _media_override
    _media_override = provider


def get_media_override() -> Optional[MediaProvider]:
    return _media_override


def get_primary_media_provider() -> MediaProvider:
    if _media_override is not None:
        return _media_override
    for provider in media_providers():
        if provider.availability() is Availability.AVAILABLE:
            return provider
    return WindowsMediaProvider()
