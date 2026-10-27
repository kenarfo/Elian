# ==========================
# ELIAN — integration diagnostics (Phase 7)
# Read/health only — no writes, no messages sent.
# ==========================

from __future__ import annotations


def run_integration_diagnostics() -> int:
    print("=== ELIAN INTEGRATION DIAGNOSTICS ===")
    print("(read/health only — no writes)")
    print()
    from integrations import integration_status_summary

    status = integration_status_summary()
    labels = {
        "calendar": "Calendar",
        "reminders": "Reminders",
        "media_spotify": "Media (Spotify API)",
        "media_windows": "Media (Windows)",
        "messaging": "Messaging",
        "home_assistant": "Home Assistant",
    }
    for key, label in labels.items():
        value = status.get(key, "unknown").upper().replace("_", " ")
        print(f"{label}: {value}")
    print()
    print("Secrets are not displayed.")
    print("=== END INTEGRATION DIAGNOSTICS ===")
    return 0
