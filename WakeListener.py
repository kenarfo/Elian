# ==========================
# ELIAN AI ASSISTANT
# WakeListener.py — LEGACY facade (Phase 4)
#
# Prefer: from voice.wake import AcousticWakeListener / create_wake_listener
# This module kept for old scripts; model is NOT loaded at import time.
# ==========================

from voice.wake import (  # noqa: F401
    AcousticWakeListener,
    FakeWakeListener,
    LegacySttWakeListener,
    WakeListenerBase,
    WakeResult,
    create_wake_listener,
)

# Back-compat names used by older snippets
SAMPLE_RATE = 16000
CHUNK = 1280

_listener = None


def listen_for_wakeword():
    """
    LEGACY blocking helper.
    Prefer VoicePipeline.await_wake() in main.
    """
    global _listener
    from voice.settings import VoiceSettings

    if _listener is None:
        settings = VoiceSettings.from_config()
        _listener = AcousticWakeListener(settings)
        if not _listener.load():
            raise RuntimeError(
                "[WAKE] Acoustic unavailable — use create_wake_listener fallback"
            )
        _listener.start()
    result = _listener.wait_for_wake()
    return bool(result.detected)


if __name__ == "__main__":
    while True:
        if listen_for_wakeword():
            print("Hej! Słucham.")
