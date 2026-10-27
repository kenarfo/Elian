# ==========================
# ELIAN — RC validation runner (Phase 9.5 + 9.6)
# ==========================

from __future__ import annotations

import sys
import threading
import time
from typing import Any, Dict, Optional

from Config import get_config_value
from Diagnostics import log_turn
from HomeAssistant import start_registry
from HomeAssistantMqttBridge import start_mqtt_bridge, stop_mqtt_bridge
from Memory import load_memory
from SkillsLoader import load_skills
from Voice import (
    get_voice_settings,
    initialize_voice,
    listen,
    shutdown_voice,
    speak,
    stop_speaking,
)

from core.conversation_manager import ConversationManager
from core.latency import current_tracker, set_current_tracker
from core.metrics import reset_metrics
from core.rc_trace import log_rc_trace
from core.rc_validation.campaign import (
    add_issue,
    count_completed,
    delete_campaign,
    end_session,
    finish_campaign,
    get_or_resume_campaign,
    merge_session_metrics,
    print_campaign_status,
    record_test_result,
    save_campaign,
    start_session,
)
from core.rc_validation.collector import (
    activate_collector,
    deactivate_collector,
    get_rc_collector,
    record_interaction_if_active,
    record_wake_if_active,
)
from core.rc_validation.inject import parse_rc_inject_line, log_rc_inject, prepare_rc_inject_session
from core.rc_validation.report import build_campaign_report, print_summary, write_reports
from core.rc_validation.status import RCStatusDisplay
from core.rc_validation.tests import RC_TEST_CASES
from core.rc_validation.wake_policy import print_rc_wake_banner, resolve_rc_wake_listener
from core.runtime import ElianRuntime
from core.version import version_banner
from voice.pipeline import VoicePipeline
from voice.settings import validate_voice_config
from voice.state import SpeechState


# Module state for stdin command handler
_CAMPAIGN: Optional[Dict[str, Any]] = None
_FINISH_WHOLE_CAMPAIGN = False
_RESET_REQUESTED = False
_TURN_LOCK = threading.Lock()
_INJECT_SEQ = 0
_RC_MANAGER = None
_RC_PIPELINE = None
_RC_STATUS = None


def _print_intro(store_transcripts: bool) -> None:
    print("=" * 60)
    print("ELIAN 2.0 — RC VALIDATION MODE")
    print(version_banner())
    print("=" * 60)
    print()
    print("Ten tryb prowadzi przez bezpieczne testy na Twoim sprzęcie.")
    print("Nie wykonuje destrukcyjnych operacji automatycznie.")
    print()
    if store_transcripts:
        print("*** WARNING: Conversation transcripts will be stored locally. ***")
        print()
    else:
        print("Privacy: store_transcripts=false (domyślnie)")
        print("Pełne wypowiedzi NIE są zapisywane — tylko kategorie/fingerprint.")
        print()
    print("Komendy konsoli (wpisuj w trakcie sesji):")
    print("  rc status              — bieżące metryki sesji")
    print("  rc test <id> pass|fail|partial|skip [notatka]")
    print("  rc wake fp             — false positive wake")
    print("  rc wake miss           — missed wake")
    print("  rc inject <tekst>     — komenda bez mic/VAD/STT/wake (debug/RC)")
    print("  rc note <tekst>        — wpis do issue log")
    print("  rc list                — lista testów")
    print("  rc show <id>           — instrukcja testu")
    print("  rc campaign status     — status kampanii (24 testy)")
    print("  rc campaign reset      — wyczyść kampanię (wymaga potwierdzenia)")
    print("  rc campaign finish     — zamknij kampanię + raport")
    print("  rc finish              — zakończ sesję i zapisz raport (kampania zostaje)")
    print("  Ctrl+C                 — czysty shutdown sesji + zapis raportu")
    print()
    _print_test_plan()


def _print_test_plan() -> None:
    print("Plan testów (oznacz ręcznie po wykonaniu):")
    for index, test in enumerate(RC_TEST_CASES, start=1):
        skip = f" [optional skip_if={test.skip_if}]" if test.skip_if else ""
        status = ""
        if _CAMPAIGN:
            entry = (_CAMPAIGN.get("tests") or {}).get(test.id) or {}
            st = entry.get("status", "PENDING")
            if st != "PENDING":
                status = f" [{st}]"
        print(f"  {index:2}. {test.id}{skip}{status} — {test.title}")
    print()


def _persist_campaign() -> None:
    if _CAMPAIGN is not None:
        save_campaign(_CAMPAIGN)


def _handle_rc_command(line: str) -> bool:
    """Returns True if should finish current session loop."""
    global _CAMPAIGN, _FINISH_WHOLE_CAMPAIGN, _RESET_REQUESTED

    col = get_rc_collector()
    if not col:
        return False

    parts = line.strip().split(maxsplit=2)
    if not parts:
        return False

    cmd = parts[0].lower()
    if cmd != "rc":
        return False

    if len(parts) < 2:
        print("[RC] Użycie: rc status | rc inject | rc test | rc campaign | rc finish")
        return False

    action = parts[1].lower()

    if action == "finish":
        return True

    if action == "campaign":
        return _handle_campaign_command(parts)

    if action == "inject":
        return _handle_rc_inject(parts)

    if action == "status":
        snap = col.snapshot_metrics()
        print("[RC] --- session status ---")
        print(f"  interactions: {snap['interactions']}")
        print(f"  routable: {snap['routable_interactions']}")
        dist = snap.get("routing_distribution_pct") or {}
        print(
            f"  routing: FastPath={dist.get('fast_path')}% "
            f"Agent={dist.get('agent')}% Brain={dist.get('brain')}% "
            f"Legacy={dist.get('legacy')}%"
        )
        print(f"  legacy rate: {snap.get('legacy_fallback_rate_pct')}%")
        print(
            f"  wake: {snap.get('wake_count')} "
            f"fp={snap.get('wake_false_positive_manual')} "
            f"miss={snap.get('wake_missed_manual')}"
        )
        if _CAMPAIGN:
            done, total = count_completed(_CAMPAIGN)
            print(f"  campaign progress: {done}/{total}")
        return False

    if action == "list":
        for test in RC_TEST_CASES:
            st = ""
            if _CAMPAIGN:
                st = f" [{((_CAMPAIGN.get('tests') or {}).get(test.id) or {}).get('status', 'PENDING')}]"
            print(f"  {test.id}{st}: {test.title}")
        return False

    if action == "show" and len(parts) >= 3:
        test_id = parts[2].split()[0]
        _print_test_detail(test_id)
        return False

    if action == "wake" and len(parts) >= 3:
        sub = parts[2].lower()
        if sub == "fp":
            col.record_wake_false_positive()
            if _CAMPAIGN:
                wake = _CAMPAIGN.setdefault("wake", {})
                wake["false_positives"] = int(wake.get("false_positives") or 0) + 1
                _persist_campaign()
            print("[RC] Recorded false positive wake")
        elif sub == "miss":
            col.record_wake_missed()
            if _CAMPAIGN:
                wake = _CAMPAIGN.setdefault("wake", {})
                wake["missed"] = int(wake.get("missed") or 0) + 1
                _persist_campaign()
            print("[RC] Recorded missed wake")
        return False

    if action == "note":
        note = parts[2] if len(parts) >= 3 else ""
        if note:
            col.add_issue(note)
            if _CAMPAIGN:
                add_issue(_CAMPAIGN, note, severity="INFO")
                _persist_campaign()
            print("[RC] Issue logged")
        return False

    if action == "test" and len(parts) >= 3:
        rest = parts[2].split(maxsplit=2)
        if len(rest) < 2:
            print("[RC] rc test <id> pass|fail|partial|skip [note]")
            return False
        test_id, result = rest[0], rest[1]
        note = rest[2] if len(rest) > 2 else ""
        col.record_manual_test(test_id, result, note)
        if _CAMPAIGN:
            ok = record_test_result(
                _CAMPAIGN,
                test_id,
                result,
                note=note,
                session_id=_CAMPAIGN.get("active_session_id") or "",
            )
            if ok:
                _persist_campaign()
                done, total = count_completed(_CAMPAIGN)
                print(f"[RC] Test {test_id} → {result.upper()}  (campaign {done}/{total})")
            else:
                print(f"[RC] Invalid result: {result}")
        else:
            print(f"[RC] Test {test_id} → {result.upper()}")
        return False

    print("[RC] Nieznana komenda. Wpisz: rc status | rc inject | rc campaign status")
    return False


def _handle_campaign_command(parts: list) -> bool:
    """Handle rc campaign status|reset|finish. Returns True to end session."""
    global _CAMPAIGN, _FINISH_WHOLE_CAMPAIGN, _RESET_REQUESTED

    if _CAMPAIGN is None:
        print("[RC] Brak aktywnej kampanii")
        return False

    if len(parts) < 3:
        print("[RC] rc campaign status|reset|finish")
        return False

    sub = parts[2].split()[0].lower()

    if sub == "status":
        print_campaign_status(_CAMPAIGN)
        return False

    if sub == "finish":
        _FINISH_WHOLE_CAMPAIGN = True
        print("[RC] Campaign finish requested — zapisuję i zamykam sesję")
        return True

    if sub == "reset":
        print("[RC] To wyczyści całą kampanię (wszystkie wyniki testów).")
        print("[RC] Wpisz RESET aby potwierdzić (lub cokolwiek innego aby anulować):")
        try:
            confirm = sys.stdin.readline().strip()
        except Exception:
            confirm = ""
        if confirm == "RESET":
            delete_campaign()
            _CAMPAIGN = None
            _RESET_REQUESTED = True
            print("[RC] Campaign reset. Restart --rc-validation aby zacząć od nowa.")
            return True
        print("[RC] Reset anulowany.")
        return False

    print("[RC] rc campaign status|reset|finish")
    return False


def _handle_rc_inject(parts: list) -> bool:
    """Queue-less immediate inject on stdin thread; canonical handle under turn lock."""
    global _INJECT_SEQ

    reconstructed = "rc " + " ".join(parts[1:])
    is_cmd, text = parse_rc_inject_line(reconstructed)
    if not is_cmd:
        return False
    if not text:
        print("[RC] Użycie: rc inject <tekst>")
        return False
    if _RC_MANAGER is None or _RC_PIPELINE is None or _RC_STATUS is None:
        print("[RC] Inject niedostępny — sesja RC jeszcze się nie uruchomiła.")
        return False
    with _TURN_LOCK:
        _INJECT_SEQ += 1
        _run_rc_injected_turn(text)
    return False


def _run_rc_injected_turn(text: str) -> None:
    """Bypass VAD/STT/WAKE only; same handle → speak → idle path as voice."""
    log_rc_inject(text)
    prepare_rc_inject_session(_RC_MANAGER)
    print()
    print("WAKE + COMMAND:", text)
    log_turn("wake", score=1.0, mode="rc_inject", text=text)
    _execute_canonical_turn(text)


def _execute_canonical_turn(text: str) -> None:
    """Voice and rc inject share this path after a command string exists."""
    manager = _RC_MANAGER
    pipeline = _RC_PIPELINE
    status = _RC_STATUS
    col = get_rc_collector()
    if manager is None or pipeline is None or status is None:
        return
    heard = bool(str(text or "").strip())
    log_rc_trace(
        heard="yes" if heard else "no",
        stt_text_present="yes" if heard else "no",
    )
    status.heard(text or "")
    print("TEKST:", text or "(empty)")
    log_turn("speech", text=text)
    _mark_processing(pipeline, status)
    try:
        result = manager.handle(text or "")
        env = getattr(manager, "_decision_envelope", None)
        log_rc_trace(
            semantic_decision=(
                f"{getattr(env, 'mode', '')}/{getattr(env, 'intent', '')}" if env is not None else "none"
            ),
            route=str(getattr(result, "source", "") or "none"),
            response_created="yes" if str(getattr(result, "text", "") or "").strip() else "no",
            response_text_length=len(str(getattr(result, "text", "") or "")),
            policy_result="n/a",
        )
        _after_handle(text or "", result, pipeline)
        if getattr(result, "source", "") in (
            "fast_path",
            "agent",
            "tool",
            "deterministic",
            "intent",
        ):
            pipeline.begin_acting()
        if result.should_speak and result.text:
            log_rc_trace(tts_requested="yes")
            try:
                pipeline.speak(result.text)
                log_rc_trace(
                    tts_generated="yes",
                    playback_started="yes",
                    playback_finished="yes",
                )
            except Exception as exc:
                print("[RC] TTS error:", exc)
                log_rc_trace(tts_requested="yes", tts_generated="no", playback_started="no")
                if col:
                    col.record_tts_failure()
                    col.record_crash("tts", str(exc))
                if pipeline.state.state is SpeechState.SPEAKING:
                    pipeline.state.set(SpeechState.ACTIVE_LISTENING)
        else:
            log_rc_trace(tts_requested="no")
            if pipeline.state.state is SpeechState.SPEAKING:
                pipeline.state.set(SpeechState.ACTIVE_LISTENING)
        if result.ended or not result.active:
            pipeline.go_idle()
            log_rc_trace(session_state="IDLE")
        else:
            if getattr(result, "paused", False) or result.source == "wait":
                pipeline.pause_session()
            _mark_active_session(pipeline, status, active=True)
            log_rc_trace(session_state=getattr(pipeline.state.state, "name", str(pipeline.state.state)))
    finally:
        if pipeline.state.is_processing():
            pipeline.resume_listening(reason="rc_processing_guard")


def _print_test_detail(test_id: str) -> None:
    for test in RC_TEST_CASES:
        if test.id == test_id:
            print()
            print(f"=== {test.id}: {test.title} ===")
            print(test.instructions)
            if test.phrases:
                print("\nFrazy:")
                for phrase in test.phrases:
                    print(f"  • {phrase}")
            if test.verify:
                print("\nSprawdź:")
                for item in test.verify:
                    print(f"  - {item}")
            if test.skip_if:
                print(f"\nSkip if: {test.skip_if}")
            if _CAMPAIGN:
                entry = (_CAMPAIGN.get("tests") or {}).get(test_id) or {}
                print(f"\nCampaign status: {entry.get('status', 'PENDING')}")
            print()
            return
    print(f"[RC] Nie znaleziono testu: {test_id}")


def _stdin_listener(stop_event: threading.Event, finish_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            line = sys.stdin.readline()
        except Exception:
            break
        if not line:
            time.sleep(0.2)
            continue
        if _handle_rc_command(line):
            finish_event.set()
            break


def _legacy_skills_enabled() -> bool:
    return bool(get_config_value("settings", "legacy_skills_startup", default=True))


def _after_handle(text, result, pipeline) -> None:
    latency = None
    tracker = current_tracker()
    if tracker:
        latency = tracker.snapshot()
    elif pipeline.last_latency:
        latency = pipeline.last_latency.snapshot()
    record_interaction_if_active(text, result, latency=latency)


def _mark_processing(pipeline, status: RCStatusDisplay) -> None:
    pipeline.state.set(SpeechState.PROCESSING)
    status.processing()


def _mark_active_session(pipeline, status: RCStatusDisplay, *, active: bool) -> None:
    if active:
        pipeline.state.set(SpeechState.ACTIVE_LISTENING)


def run_rc_validation(*, store_transcripts: bool = False) -> int:
    global _CAMPAIGN, _FINISH_WHOLE_CAMPAIGN, _RESET_REQUESTED
    global _RC_MANAGER, _RC_PIPELINE, _RC_STATUS

    _FINISH_WHOLE_CAMPAIGN = False
    _RESET_REQUESTED = False

    reset_metrics()
    activate_collector(store_transcripts=store_transcripts)

    campaign, resumed, messages = get_or_resume_campaign()
    _CAMPAIGN = campaign
    for msg in messages:
        print(msg)
    print()

    session_id = start_session(_CAMPAIGN)
    _persist_campaign()
    print(f"[RC] Session: {session_id}")
    print()

    try:
        from core.crash_log import install_global_excepthook, update_runtime_context

        install_global_excepthook()
        update_runtime_context(session_id=str(session_id or ""))
    except Exception as hook_exc:
        print(f"[RC] crash hooks unavailable: {hook_exc}")

    _print_intro(store_transcripts)

    errors = validate_voice_config()
    if errors:
        print("[RC] Voice validation failed — kontynuuję w trybie degraded:")
        for err in errors:
            print(f"  - {err}")

    try:
        initialize_voice()
    except Exception as error:
        col = get_rc_collector()
        if col:
            col.record_crash("voice", str(error))
        if _CAMPAIGN:
            add_issue(_CAMPAIGN, f"Voice init failed: {error}", severity="HIGH")
            _persist_campaign()
        print("[RC] Voice init failed:", error)
        print("[RC] Możesz kontynuować z ograniczoną funkcjonalnością lub Ctrl+C.")

    memory = load_memory()
    if _legacy_skills_enabled():
        load_skills()

    try:
        start_mqtt_bridge()
    except Exception:
        pass
    try:
        start_registry()
    except Exception:
        pass

    # Resolved devices from initialize_voice (Plantronics), not Windows default LCS.
    settings = get_voice_settings()
    manager = ConversationManager(memory=memory)
    manager.idle_timeout = settings.session_idle_timeout

    status = RCStatusDisplay(enabled=True)

    wake_listener, wake_mode, wake_meta = resolve_rc_wake_listener(
        settings,
        listen_fn=lambda **kwargs: listen(**kwargs),
    )
    print_rc_wake_banner({**wake_meta, "mode": wake_mode})
    col = get_rc_collector()
    if col:
        col.set_wake_policy_meta(wake_meta, mode=wake_mode)
    if _CAMPAIGN:
        _CAMPAIGN["custom_elian_onnx_installed"] = bool(
            wake_meta.get("custom_elian_onnx_installed")
        )
        agg = _CAMPAIGN.setdefault("aggregated_metrics", {})
        agg["wake_model"] = {
            "path": wake_meta.get("wake_model_path") or "",
            "custom_elian_onnx_installed": bool(
                wake_meta.get("custom_elian_onnx_installed")
            ),
            "wake_mode_used": wake_mode,
            "acoustic_wake_status": wake_meta.get("acoustic_wake_status"),
            "warning": wake_meta.get("warning"),
        }
        _persist_campaign()

    pipeline = VoicePipeline(
        settings=settings,
        listen_fn=lambda **kwargs: listen(**kwargs),
        speak_fn=lambda text, **kwargs: speak(text),
        stop_speak_fn=stop_speaking,
        wake_listener=wake_listener,
    )
    status.attach(pipeline.state)
    pipeline.start()
    status.on_state(SpeechState.IDLE, pipeline.state.state)
    _RC_MANAGER = manager
    _RC_PIPELINE = pipeline
    _RC_STATUS = status

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
        return pipeline.state.is_speaking()

    def preference_get(key, default=None):
        try:
            value = manager.mem.get_preference(key)
            return value if value is not None else default
        except Exception:
            return default

    runtime = ElianRuntime(
        speak_fn=lambda text: speak(text),
        busy_check=busy_check,
        preference_get=preference_get,
        start_background=True,
    )
    runtime.start()

    if resumed:
        done, total = count_completed(_CAMPAIGN)
        print(f"[RC] Resumed campaign — {done}/{total} tests already recorded.")
    print("[RC] Elian działa. Status: IDLE / LISTENING / PROCESSING / SPEAKING.")
    print("[RC] Wyniki testów przetrwają restart (rc_validation_campaign.json).")
    print()

    stop_stdin = threading.Event()
    finish = threading.Event()
    stdin_thread = threading.Thread(
        target=_stdin_listener,
        args=(stop_stdin, finish),
        name="rc-stdin",
        daemon=True,
    )
    stdin_thread.start()

    clean_exit = False
    try:
        while not finish.is_set():
            try:
                runtime.proactive.try_deliver()

                if not manager.active:
                    t_wake = time.perf_counter()
                    wake = pipeline.await_wake()
                    wake_ms = (time.perf_counter() - t_wake) * 1000.0
                    with _TURN_LOCK:
                        if manager.active:
                            # rc inject already entered handle during wake wait
                            continue
                        if not wake.detected:
                            runtime.proactive.try_deliver()
                            continue

                        record_wake_if_active(
                            detected=True,
                            latency_ms=wake_ms,
                            mode=getattr(wake, "mode", ""),
                            score=float(getattr(wake, "score", 0) or 0),
                        )
                        if _CAMPAIGN:
                            wake_c = _CAMPAIGN.setdefault("wake", {})
                            wake_c["detections"] = int(wake_c.get("detections") or 0) + 1
                            _persist_campaign()
                        status.wake_detected()

                        manager.activate()
                        command = pipeline.transcript_from_wake(wake)
                        print()
                        print("WAKE + COMMAND:", command or "(bare wake)")
                        log_turn("wake", score=wake.score, mode=wake.mode, text=command)

                        if command:
                            _execute_canonical_turn(command)
                        elif settings.listening_ack_on_bare_wake:
                            pipeline.speak(settings.listening_ack)
                            _mark_active_session(pipeline, status, active=True)
                        else:
                            _mark_active_session(pipeline, status, active=True)
                    continue

                inject_seq = _INJECT_SEQ
                text = pipeline.listen_active(wait_timeout=manager.listen_timeout())
                with _TURN_LOCK:
                    if _INJECT_SEQ != inject_seq:
                        continue
                    if pipeline.last_listen_echo:
                        continue
                    if not text:
                        if col:
                            col.record_stt_failure()

                    lowered = (text or "").strip().lower()
                    if lowered in ("stop", "stop!", "cisza", "wystarczy"):
                        stop_speaking()
                        manager.request_cancel()

                    _execute_canonical_turn(text or "")

            except KeyboardInterrupt:
                clean_exit = True
                finish.set()
                break
            except Exception as error:
                if col:
                    col.record_crash("runtime", str(error))
                if _CAMPAIGN:
                    add_issue(_CAMPAIGN, f"Runtime error: {error}", severity="HIGH")
                    _persist_campaign()
                print("[RC] Błąd:", error)

        # Normal finish via rc finish / campaign finish / reset
        if finish.is_set() and not _RESET_REQUESTED:
            clean_exit = True

    finally:
        stop_stdin.set()
        _RC_MANAGER = None
        _RC_PIPELINE = None
        _RC_STATUS = None
        status.detach(pipeline.state)
        runtime.stop()
        pipeline.close()
        shutdown_voice()
        stop_mqtt_bridge()
        set_current_tracker(None)

        collector = deactivate_collector()

        if _RESET_REQUESTED:
            print("[RC] Campaign was reset — no aggregate report written.")
            return 0

        if _CAMPAIGN is not None and collector is not None:
            merge_session_metrics(_CAMPAIGN, collector.snapshot_metrics())
            if clean_exit:
                end_session(_CAMPAIGN, clean=True, unexpected=False)
            else:
                # Should not normally reach here without clean_exit; treat as unexpected
                end_session(_CAMPAIGN, clean=False, unexpected=True)
                add_issue(
                    _CAMPAIGN,
                    "RC session ended without clean shutdown.",
                    severity="HIGH",
                )
                _CAMPAIGN["unexpected_exit_pending_review"] = True

            if _FINISH_WHOLE_CAMPAIGN:
                finish_campaign(_CAMPAIGN)

            _persist_campaign()

            stamped, md_path = write_reports(collector, campaign=_CAMPAIGN)
            report = build_campaign_report(
                _CAMPAIGN,
                session_metrics=collector.snapshot_metrics(),
            )
            print_summary(report)
            print()
            print(f"Session stamp: {stamped}")
            print(f"Campaign file: Data/diagnostics/rc_validation_campaign.json")
            print(f"Report latest: {md_path.parent / 'rc_validation_latest.json'}")
            print(f"Report MD: {md_path}")
        elif collector is not None:
            stamped, md_path = write_reports(collector)
            print(f"Report JSON: {stamped}")

    return 0
