# ==========================
# Boot Elian to WAKE_LISTENING then exit (no speech)
# ==========================
"""Verify main voice stack reaches wake without PortAudio crash."""

from __future__ import annotations

import sys
import threading
import time

sys.path.insert(0, r"C:\Users\kenar\Elian")


def main() -> int:
    from Voice import get_voice_settings, initialize_voice, listen
    from voice.pipeline import VoicePipeline
    from voice.state import SpeechState
    from voice.tts_engine import get_tts_engine
    from Memory import load_memory
    from core.conversation_manager import ConversationManager

    initialize_voice()
    settings = get_voice_settings()
    tts = get_tts_engine()
    manager = ConversationManager(memory=load_memory())
    pipeline = VoicePipeline(
        settings=settings,
        listen_fn=lambda **kwargs: listen(**kwargs),
        speak_fn=tts.speak,
        stop_speak_fn=tts.stop,
    )
    pipeline.start()
    print("wake_mode", pipeline._wake_mode)
    print("state_after_start", pipeline.state.state)

    done = threading.Event()
    result = {"wake": None, "err": None}

    def _await():
        try:
            result["wake"] = pipeline.await_wake(timeout=2.0)
        except Exception as exc:
            result["err"] = f"{type(exc).__name__}: {exc}"
        finally:
            done.set()

    thr = threading.Thread(target=_await, name="boot-wake", daemon=True)
    thr.start()
    # Let wake path open mic briefly.
    time.sleep(1.2)
    print("state_during_await", pipeline.state.state)
    done.wait(timeout=5.0)
    if result["err"]:
        print("FAIL", result["err"])
        pipeline.close()
        return 1
    wake = result["wake"]
    print(
        "wake_result",
        getattr(wake, "detected", None),
        getattr(wake, "mode", None),
    )
    print("state_after_await", pipeline.state.state)
    pipeline.close()
    print("PASS boot_to_wake")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
