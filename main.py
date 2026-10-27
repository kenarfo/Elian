# ==========================
# ELIAN AI ASSISTANT
# Version 2.0 — Event Bus + Proactivity + Hardening (Phase 9)
# main.py (bootstrap)
# ==========================

import sys


# Biblioteki Windows mogą pośrednio załadować pythoncom. Ustawienie MTA
# przed pozostałymi importami zapobiega przełączeniu głównego wątku na STA.
sys.coinit_flags = 0


def configure_console():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


configure_console()


from SkillsLoader import load_skills
from Voice import (
    get_voice_settings,
    initialize_voice,
    shutdown_voice,
    listen,
)
from voice.tts_engine import get_tts_engine


def speak(text, **kwargs):
    """Hot-path TTS via RoutedTTS (Fish → Piper). Patchable as ``main.speak``."""
    return get_tts_engine().speak(text, **kwargs)


def stop_speaking(**kwargs):
    """Cancel TTS / barge-safe stop. Patchable as ``main.stop_speaking``."""
    return get_tts_engine().stop(**kwargs)

from Memory import load_memory
from Config import config, get_config_value, validate_config
from Diagnostics import log_turn
from HomeAssistant import start_registry
from HomeAssistantMqttBridge import start_mqtt_bridge, stop_mqtt_bridge
from core.conversation_manager import (
    ConversationManager,
    should_end_conversation,
)
from core.crash_log import install_global_excepthook, write_runtime_crash
from core.latency import set_current_tracker
from core.runtime import ElianRuntime
from core.startup import get_startup_metrics
from core.version import VERSION, version_banner
from voice.pipeline import VoicePipeline
from voice.settings import validate_voice_config
from voice.state import SpeechState


def _configure_logging():
    if "--debug" not in sys.argv:
        return
    import logging

    logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(message)s")


_configure_logging()


def _print_handle(result):
    if result.source:
        log_turn(
            "session",
            event="handle",
            source=result.source,
            active=result.active,
            ended=result.ended,
        )


def _startup_banner(*, voice_ok=True, agent_ok=True, ha_ok=False):
    parts = []
    parts.append(f"Voice {'READY' if voice_ok else 'DEGRADED'}")
    parts.append(f"Agent {'READY' if agent_ok else 'DEGRADED'}")
    parts.append(f"HA {'READY' if ha_ok else 'unavailable'}")
    print(f"{version_banner()} | {' | '.join(parts)}")


def _legacy_skills_enabled():
    return bool(get_config_value("settings", "legacy_skills_startup", default=True))


def start():
    install_global_excepthook()
    startup = get_startup_metrics()

    errors = validate_voice_config()
    cfg_warnings = validate_config(config)
    if cfg_warnings and "--debug" in sys.argv:
        print("[CONFIG] validation warnings:")
        for warn in cfg_warnings:
            print(f"  - {warn}")

    if errors:
        print("[CONFIG] Voice validation failed:")
        for err in errors:
            print(f"  - {err}")
        return

    try:
        initialize_voice()
    except Exception as error:
        print("[VOICE] Nie udało się uruchomić modułów głosowych:", error)
        return
    startup.finish_phase("voice_init_ms", since="start")

    print()
    memory = load_memory()
    startup.finish_phase("memory_init_ms", since="voice_init_ms")
    if _legacy_skills_enabled():
        load_skills()
    startup.finish_phase("tool_registry_ms", since="memory_init_ms")

    ha_ok = False
    try:
        start_mqtt_bridge()
    except Exception as error:
        print("[INTEGRATION] Błąd mostu Home Assistant:", error)

    try:
        start_registry()
        ha_ok = bool(get_config_value("smart_home", "enabled", default=False))
    except Exception as error:
        print("[INTEGRATION] Błąd rejestru Home Assistant:", error)
    startup.finish_phase("integration_init_ms", since="tool_registry_ms")

    # After initialize_voice(): resolved mic/speaker indices (not Windows default LCS).
    settings = get_voice_settings()
    manager = ConversationManager(memory=memory)
    manager.idle_timeout = settings.session_idle_timeout

    # Hot path: RoutedTTS (Fish S2 when ready) → English Piper fallback.
    # Prefer config voice.prefer_fish_tts; do not bypass with Voice.speak (Piper-only).
    tts = get_tts_engine()
    print(
        f"[TTS] engine=routed prefer_fish={getattr(tts, 'prefer_fish', True)} "
        f"active={getattr(getattr(tts, 'active', None), 'name', '?')}"
    )

    pipeline = VoicePipeline(
        settings=settings,
        listen_fn=lambda **kwargs: listen(**kwargs),
        speak_fn=speak,
        stop_speak_fn=stop_speaking,
    )
    pipeline.start()
    startup.finish_phase("watcher_init_ms", since="integration_init_ms")

    def busy_check():
        st = pipeline.state.state
        if st in (
            SpeechState.ACTIVE_LISTENING,
            SpeechState.PROCESSING,
            SpeechState.ACTING,
            SpeechState.SPEAKING,
            SpeechState.INTERRUPTED,
            SpeechState.PAUSED,
            SpeechState.WAITING,
        ):
            return True
        if pipeline.state.is_speaking():
            return True
        return False

    def preference_get(key, default=None):
        try:
            value = manager.mem.get_preference(key)
            return value if value is not None else default
        except Exception:
            return default

    runtime = ElianRuntime(
        speak_fn=speak,
        busy_check=busy_check,
        preference_get=preference_get,
        start_background=True,
        conversation_manager=manager,
    )
    runtime.start()
    try:
        import threading

        def _boot_perception():
            try:
                if not get_config_value("screen_perception", "enabled", default=True):
                    return
                from core.perception.dxgi_capture import DxgiFrameProvider
                from core.perception.foreground import live_foreground_provider
                from core.perception.screen_runtime import ScreenPerceptionRuntime, set_screen_runtime

                runtime_p = ScreenPerceptionRuntime(
                    provider=DxgiFrameProvider(),
                    foreground_provider=live_foreground_provider(),
                )
                set_screen_runtime(runtime_p)
                runtime_p.start()
            except Exception as exc:
                print(f"[PERCEPTION] start skipped: {exc}")

        threading.Thread(target=_boot_perception, daemon=True, name="elian-perception").start()
    except Exception:
        pass
    startup.finish_phase("startup_total_ms", since="start")

    agent_ok = bool(get_config_value("ai", "agent_enabled", default=True))
    _startup_banner(voice_ok=True, agent_ok=agent_ok, ha_ok=ha_ok)
    if "--debug" in sys.argv:
        print("[PERF] startup:", startup.snapshot())
    print()
    mode = getattr(pipeline, "_wake_mode", "") or "unknown"
    if mode == "legacy_stt":
        print("[VOICE] Czekam na wake (legacy STT — powiedz „hej elian”).")
    elif mode == "acoustic":
        print("[VOICE] Czekam na acoustic wake.")
    else:
        print(f"[VOICE] Czekam na wake (mode={mode}).")
    print()

    hud_host = None
    try:
        from ui.hud_host import try_start_hud

        hud_host = try_start_hud(pipeline=pipeline, runtime=runtime)
    except Exception as exc:
        print(f"[HUD] niedostępny — głos działa dalej: {exc}")

    try:
        while True:
            try:
                runtime.proactive.try_deliver()

                if not manager.active:
                    wake = pipeline.await_wake()
                    if not wake.detected:
                        runtime.proactive.try_deliver()
                        continue

                    manager.activate()
                    command = pipeline.transcript_from_wake(wake)
                    if not command:
                        brief = manager.session_start_briefing()
                        if brief is not None and brief.should_speak and brief.text:
                            _print_handle(brief)
                            try:
                                pipeline.speak(brief.text)
                            except Exception:
                                pass
                    print()
                    print("================================")
                    print("WAKE + COMMAND:", command or "(bare wake)")
                    print("================================")
                    log_turn("wake", score=wake.score, mode=wake.mode, text=command)

                    if command:
                        result = pipeline.execute_turn(manager, command)
                        _print_handle(result)
                    elif settings.listening_ack_on_bare_wake:
                        pipeline.speak(settings.listening_ack)
                        pipeline.resume_listening(reason="bare_wake")
                    continue

                text = pipeline.listen_active(
                    wait_timeout=manager.listen_timeout()
                )

                print()
                print("================================")
                print("TEKST DLA ELIANA:", text)
                print("================================")
                log_turn("speech", text=text)

                if pipeline.last_listen_echo:
                    print("[VOICE] echo/self-trigger ignored")
                    continue

                lowered = (text or "").strip().lower()
                if lowered in ("stop", "stop!", "cisza", "wystarczy"):
                    pipeline.stop_speaking()
                    manager.request_cancel()

                result = pipeline.execute_turn(manager, text)
                _print_handle(result)
                if result.ended or not manager.active:
                    print("[VOICE] Powrót do acoustic wake.")
                if pipeline.was_interrupted:
                    continue

            except KeyboardInterrupt:
                print()
                print("[RUNTIME] Elian wyłączony.")
                from core.crash_log import log_runtime_exit

                log_runtime_exit(
                    reason="keyboard_interrupt",
                    source="main_loop",
                    exception=None,
                    user_requested=True,
                )
                manager.deactivate(reason="shutdown")
                break

            except Exception as error:
                print()
                print("[RUNTIME] Błąd:", error)
                print()
                try:
                    write_runtime_crash(
                        error,
                        thread_name="main_loop",
                        speech_state=str(getattr(pipeline.state, "state", "")),
                        manager=manager,
                        pipeline=pipeline,
                        current_interaction={"last_text": getattr(manager, "_last_raw_text", "")},
                    )
                except Exception:
                    pass

    finally:
        from core.crash_log import log_runtime_exit

        log_runtime_exit(
            reason="runtime_finally",
            source="main.start",
            exception=None,
            user_requested=False,
        )
        if hud_host is not None:
            try:
                hud_host.stop()
            except Exception:
                pass
        runtime.stop()
        pipeline.close()
        shutdown_voice()
        stop_mqtt_bridge()
        set_current_tracker(None)


if __name__ == "__main__":
    if "--version" in sys.argv:
        print(VERSION)
        raise SystemExit(0)
    if "--doctor" in sys.argv:
        from core.doctor import run_doctor

        raise SystemExit(run_doctor())
    if "--performance-diagnostics" in sys.argv:
        from core.performance_diagnostics import run_performance_diagnostics

        raise SystemExit(run_performance_diagnostics())
    if "--voice-diagnostics" in sys.argv:
        from voice.diagnostics import run_voice_diagnostics

        raise SystemExit(run_voice_diagnostics())
    if "--vision-diagnostics" in sys.argv:
        from vision.diagnostics import run_vision_diagnostics

        save = "--save-screenshot" in sys.argv
        raise SystemExit(run_vision_diagnostics(save_screenshot=save))
    if "--event-diagnostics" in sys.argv:
        from core.event_diagnostics import run_event_diagnostics

        test = ""
        if "--test-event" in sys.argv:
            idx = sys.argv.index("--test-event")
            if idx + 1 < len(sys.argv):
                test = sys.argv[idx + 1]
        raise SystemExit(run_event_diagnostics(test_event=test))
    if "--conversation-diagnostics" in sys.argv:
        from core.conversation_diagnostics import run_conversation_diagnostics

        raise SystemExit(run_conversation_diagnostics())
    if "--integration-diagnostics" in sys.argv:
        from integrations.diagnostics import run_integration_diagnostics

        raise SystemExit(run_integration_diagnostics())
    if "--rc-validation" in sys.argv:
        from core.rc_validation import run_rc_validation

        store_transcripts = "--debug-transcripts" in sys.argv
        raise SystemExit(run_rc_validation(store_transcripts=store_transcripts))
    if "--rc-merge-campaign" in sys.argv:
        from core.rc_validation.merge_campaign import run_merge

        raise SystemExit(run_merge())
    if "--agent-smoke" in sys.argv:
        from core.agent_smoke import run_agent_smoke

        use_real = "--agent-smoke-real" in sys.argv
        raise SystemExit(run_agent_smoke(use_real_model=use_real))
    if "--jarvis-behavioral" in sys.argv:
        from core.jarvis_behavioral import run_jarvis_behavioral

        raise SystemExit(run_jarvis_behavioral(argv=sys.argv))
    if "--jarvis-explore" in sys.argv:
        from core.jarvis_behavioral import run_jarvis_explore

        raise SystemExit(run_jarvis_explore(argv=sys.argv))
    if "--inject-text" in sys.argv or "--text-input" in sys.argv or "--conversation-script" in sys.argv:
        from core.text_inject import run_from_argv

        raise SystemExit(run_from_argv(sys.argv))
    if "--simulated-voice" in sys.argv or "--sim-voice-script" in sys.argv:
        from core.simulated_voice import run_from_argv as run_sim_voice

        raise SystemExit(run_sim_voice(sys.argv))
    if "--hud-smoke" in sys.argv:
        from ui.hud_smoke import run_hud_smoke

        raise SystemExit(run_hud_smoke())
    if "--companion-smoke" in sys.argv:
        from core.companion_smoke import run_companion_smoke

        raise SystemExit(run_companion_smoke())
    if "--behavioral-eval" in sys.argv:
        from core.semantic_eval import run_behavioral_eval

        summary = run_behavioral_eval()
        print(
            f"behavioral-eval {summary['passed']}/{summary['total']} "
            f"smart_home={summary.get('smart_home_behavioral_cases')} "
            f"hardening={summary.get('hardening_behavioral_cases')} "
            f"random_poison={summary.get('random_poison_cases')}"
        )
        if summary.get("failures"):
            for row in summary["failures"][:20]:
                print(
                    f"  FAIL {row.get('category')} {row.get('utterance')!r} "
                    f"mode={row.get('mode')} expected={row.get('expected_mode')} "
                    f"intent={row.get('intent')}"
                )
        metrics = (
            "fastpath_semantic_bypass_rate",
            "fastpath_policy_bypass_rate",
            "ha_false_success_rate",
            "ha_unverified_success_claim_rate",
            "ha_redundant_mutation_rate",
            "device_name_duplication_rate",
            "technical_device_id_tts_leak_rate",
            "dictation_target_leak_rate",
        )
        for key in metrics:
            print(f"  {key}={summary.get(key, 0)}")
        raise SystemExit(0 if summary["failed"] == 0 else 1)
    if "--ha-action-smoke" in sys.argv:
        from core.ha_action_smoke import run_ha_action_smoke

        raise SystemExit(run_ha_action_smoke())
    if "--foreground-smoke" in sys.argv:
        from core.foreground_smoke import run_foreground_smoke

        raise SystemExit(run_foreground_smoke(mock="--mock" in sys.argv))
    if "--conversation-smoke" in sys.argv:
        from core.conversation_smoke import run_conversation_smoke

        raise SystemExit(run_conversation_smoke())
    if "--web-smoke" in sys.argv:
        from core.web_smoke import run_web_smoke

        raise SystemExit(run_web_smoke())
    if "--grounding-smoke" in sys.argv:
        from core.grounding_smoke import run_grounding_smoke

        raise SystemExit(run_grounding_smoke())
    if "--freshness-smoke" in sys.argv:
        from core.freshness_smoke import run_freshness_smoke

        raise SystemExit(run_freshness_smoke())
    if "--language-smoke" in sys.argv:
        from core.language_smoke import run_language_smoke

        raise SystemExit(run_language_smoke())
    if "--meta-smoke" in sys.argv:
        from core.meta_smoke import run_meta_smoke

        raise SystemExit(run_meta_smoke())
    if "--web-state-smoke" in sys.argv:
        from core.web_state_smoke import run_web_state_smoke

        raise SystemExit(run_web_state_smoke())
    if "--web-synthesis-smoke" in sys.argv:
        from core.web_synthesis_smoke import run_web_synthesis_smoke

        raise SystemExit(run_web_synthesis_smoke())
    if "--ha-context-smoke" in sys.argv:
        from core.ha_context_smoke import run_ha_context_smoke

        raise SystemExit(run_ha_context_smoke())
    if "--device-continuation-smoke" in sys.argv:
        from core.device_continuation_smoke import run_device_continuation_smoke

        raise SystemExit(run_device_continuation_smoke())
    if "--pronoun-smoke" in sys.argv:
        from core.pronoun_smoke import run_pronoun_smoke

        raise SystemExit(run_pronoun_smoke())
    if "--climate-capabilities" in sys.argv:
        from core.climate_capabilities import run_climate_capabilities

        raise SystemExit(run_climate_capabilities())
    if "--climate-smoke" in sys.argv:
        from core.climate_smoke import run_climate_smoke

        raise SystemExit(run_climate_smoke())
    if "--gree-hardware-fixes-smoke" in sys.argv:
        from core.gree_hardware_fixes_smoke import run_gree_hardware_fixes_smoke

        raise SystemExit(run_gree_hardware_fixes_smoke())
    if "--local-status-smoke" in sys.argv:
        from core.local_status_smoke import run_local_status_smoke

        raise SystemExit(run_local_status_smoke())
    if "--agent-multi-smoke" in sys.argv:
        from core.agent_multi_smoke import run_agent_multi_smoke

        raise SystemExit(run_agent_multi_smoke())
    if "--jarvis-core-smoke" in sys.argv:
        from core.jarvis_core_smoke import run_jarvis_core_smoke

        raise SystemExit(run_jarvis_core_smoke())
    if "--computer-agent-smoke" in sys.argv:
        from core.computer_agent_smoke import run_computer_agent_smoke

        raise SystemExit(run_computer_agent_smoke())
    if "--browser-agent-smoke" in sys.argv:
        from core.browser_agent_smoke import run_browser_agent_smoke

        raise SystemExit(run_browser_agent_smoke())
    if "--screen-perception-smoke" in sys.argv:
        from core.screen_perception_smoke import run_screen_perception_smoke

        raise SystemExit(run_screen_perception_smoke())
    try:
        start()
    except SystemExit as exc:
        from core.crash_log import log_runtime_exit

        log_runtime_exit(
            reason="system_exit",
            source="main",
            exception=exc,
            user_requested=False,
        )
        raise
