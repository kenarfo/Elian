# ==========================
# ELIAN — integrations package (Phase 7)
# ==========================

from integrations.base import Availability, IntegrationProvider, IntegrationResult


def integration_status_summary() -> dict:
    """Lightweight status for diagnostics / agent context (no secrets)."""
    from integrations.base import integration_enabled
    from integrations.calendar.provider import get_calendar_provider
    from integrations.media.provider import SpotifyProvider, WindowsMediaProvider
    from integrations.reminders.store import get_reminder_store

    calendar = get_calendar_provider()
    spotify = SpotifyProvider()
    windows = WindowsMediaProvider()
    reminders_ok = integration_enabled("reminders")
    try:
        get_reminder_store()
        rem_status = "available" if reminders_ok else "disabled"
    except Exception:
        rem_status = "error"

    ha = "disabled"
    try:
        from Config import get_config_value

        if get_config_value("smart_home", "enabled", default=False) and integration_enabled(
            "home_assistant"
        ):
            from HomeAssistant import test_connection

            ha = "available" if test_connection() else "unavailable"
        else:
            ha = "not_configured"
    except Exception:
        ha = "error"

    messaging = "not_configured"
    try:
        from Integrations import get_provider

        messaging = (
            "available" if get_provider().is_configured() else "not_configured"
        )
    except Exception:
        messaging = "error"

    return {
        "calendar": calendar.availability().value,
        "reminders": rem_status,
        "media_spotify": spotify.availability().value,
        "media_windows": windows.availability().value,
        "messaging": messaging,
        "home_assistant": ha,
    }


__all__ = [
    "Availability",
    "IntegrationProvider",
    "IntegrationResult",
    "integration_status_summary",
]
