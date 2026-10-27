# ==========================
# ELIAN — voice diagnostics CLI (Phase 4)
# ==========================

from __future__ import annotations

import time


def run_voice_diagnostics() -> int:
    print("=== ELIAN VOICE DIAGNOSTICS ===")
    print()

    from voice.settings import VoiceSettings, validate_voice_config
    from voice.wake import AcousticWakeListener, create_wake_listener
    from voice.state import SpeechStateMachine

    errors = validate_voice_config()
    if errors:
        print("[CONFIG] validation errors:")
        for err in errors:
            print(f"  - {err}")
        print()

    try:
        import Voice as voice_mod

        voice_mod.initialize_voice()
        settings = voice_mod.get_voice_settings()
    except Exception as exc:
        settings = VoiceSettings.from_config()
        print(f"STT/VAD init: FAIL ({exc})")
        print("Continuing with limited checks...")
    else:
        print("STT: READY (Whisper warm)")
        print(f"  model={voice_mod.WHISPER_MODEL}")
        print(f"  device={voice_mod.WHISPER_DEVICE}")
        print("VAD: READY (Silero)")
        print(f"  input_device={settings.input_device}")
        print(f"  output_device={settings.output_device}")

    print()
    print(f"Wake enabled: {settings.wake_enabled}")
    print(f"Wake threshold: {settings.wake_threshold}")
    print(f"Wake cooldown: {settings.wake_cooldown}s")
    print(f"Session idle timeout: {settings.session_idle_timeout}s")
    print(f"VAD silence: {settings.vad_silence_ms} ms")
    print(f"Pre-roll: {settings.pre_roll_ms} ms")
    print(f"Barge-in: {settings.barge_in_enabled}")
    print()

    sm = SpeechStateMachine()
    acoustic = AcousticWakeListener(settings, state_machine=sm)
    if acoustic.load():
        print("Wake model: READY (OpenWakeWord)")
        if settings.wake_model:
            print(f"  custom: {settings.wake_model}")
        else:
            print("  custom elian.onnx: not set (default OWW models)")
    else:
        print(f"Wake model: UNAVAILABLE ({acoustic.unavailable_reason})")
        print("[WAKE] Acoustic unavailable — legacy STT wake fallback possible")

    print()
    piper_ok = False
    try:
        import Voice as voice_mod
        import os
        import shutil

        piper_ok = bool(voice_mod.PIPER) and (
            os.path.isfile(voice_mod.PIPER)
            or shutil.which(voice_mod.PIPER) is not None
        ) and os.path.isfile(voice_mod.VOICE_MODEL)
    except Exception:
        piper_ok = False

    print(f"TTS (Piper): {'READY' if piper_ok else 'NOT CONFIGURED'}")
    print()
    print("Interactive checks (Ctrl+C to skip):")
    print("  1) Say wake word...")
    try:
        listener, mode = create_wake_listener(
            settings,
            state_machine=sm,
            listen_fn=None if acoustic.available else None,
        )
        if mode == "acoustic_failed" or (
            hasattr(listener, "available") and not listener.available
        ):
            print("  Skip wake listen — acoustic not ready.")
        else:
            listener.start()
            t0 = time.perf_counter()
            result = listener.wait_for_wake(timeout=20.0)
            if result.detected:
                print(
                    f"Wake detected: {result.latency_ms:.0f} ms "
                    f"(score={result.score:.2f}, {result.model_name})"
                )
            else:
                print("Wake: timeout / not detected")
            listener.stop()
    except KeyboardInterrupt:
        print("  skipped")
    except Exception as exc:
        print(f"  wake check error: {exc}")

    print()
    print("  2) STT test — speak a short sentence...")
    try:
        import Voice as voice_mod

        t0 = time.perf_counter()
        text = voice_mod.listen(wait_timeout=8.0, llm_correct=False)
        stt_ms = (time.perf_counter() - t0) * 1000.0
        print(f'STT: "{text}"')
        print(f"STT wall time: {stt_ms:.0f} ms")
    except KeyboardInterrupt:
        print("  skipped")
    except Exception as exc:
        print(f"  STT error: {exc}")

    print()
    if piper_ok:
        print("  3) TTS test...")
        try:
            import Voice as voice_mod
            from core.latency import new_turn, set_current_tracker

            tracker = new_turn()
            t0 = time.perf_counter()
            voice_mod.speak("Test diagnostyczny.", cooldown=0)
            wall = (time.perf_counter() - t0) * 1000.0
            snap = tracker.snapshot()
            ttfa = snap.get("tts_playback_start_ms")
            print(f"TTS: READY")
            if ttfa is not None:
                print(f"Time to first audio: {ttfa:.0f} ms")
            print(f"TTS wall: {wall:.0f} ms")
            set_current_tracker(None)
        except KeyboardInterrupt:
            print("  skipped")
        except Exception as exc:
            print(f"  TTS error: {exc}")
    else:
        print("  3) TTS skipped (Piper not configured)")

    print()
    print("=== END DIAGNOSTICS ===")
    return 0
