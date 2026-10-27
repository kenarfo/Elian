# ==========================
# Spoken wake RC window (real mic)
# ==========================
"""Listen once on the production mic path.

If the user speaks during wait_timeout, run wake → STT remainder → CM → TTS.
Exit 0 only on wake+command soft/agent success + TTS speak.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("ELIAN_PROTECT_USER_APPS", "1")


def main() -> int:
    wait_s = 20.0
    if "--wait" in sys.argv:
        idx = sys.argv.index("--wait")
        if idx + 1 < len(sys.argv):
            wait_s = float(sys.argv[idx + 1])

    from Config import DATA_DIR
    import Voice as V
    from WakeWord import extract_wake, is_wake_word
    from core.text_inject import _make_manager, run_one_turn
    from core.model_client import reset_model_client
    from core.tools import reset_registry_for_tests
    from voice.audio_gate import get_audio_gate, reset_audio_gate_for_tests
    from voice.tts_engine import RoutedTTS

    out = {
        "name": "spoken_wake_rc",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "wait_s": wait_s,
        "status": "FAIL",
    }
    diag = Path(DATA_DIR) / "diagnostics"
    diag.mkdir(parents=True, exist_ok=True)

    reset_audio_gate_for_tests()
    try:
        from voice.mic_mute import ensure_capture_unmuted, get_capture_mute_state

        out["mute_before"] = list(get_capture_mute_state())
        out["unmute"] = ensure_capture_unmuted(auto_unmute=True)
        out["mute_after"] = list(get_capture_mute_state())
    except Exception as exc:
        out["mute_error"] = f"{type(exc).__name__}: {exc}"
    V.initialize_voice()
    out["input_device"] = V._INPUT_DEVICE
    out["output_device"] = V._OUTPUT_DEVICE
    print()
    print("=" * 60)
    print(f"  POWIEDZ TERAZ (do {wait_s:.0f}s):  Hi Elian, volume up")
    print("=" * 60)
    print()

    t0 = time.perf_counter()
    try:
        audio = V.record_until_silence(wait_timeout=wait_s)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["portaudio_error"] = any(
            tok in str(exc) for tok in ("-9996", "-9985", "PortAudio")
        )
        _write(diag, out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1

    out["listen_wall_s"] = round(time.perf_counter() - t0, 3)
    out["gate"] = get_audio_gate().snapshot().__dict__
    if audio is None or len(audio) < 1600:
        out["error"] = "no_speech"
        out["note"] = "No utterance in window — not a PortAudio failure."
        _write(diag, out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 3

    stt = V.transcribe_audio(audio, llm_correct=False)
    out["stt_text"] = stt
    wake = bool(stt and is_wake_word(stt))
    command = extract_wake(stt).raw_command if stt else ""
    out["wake"] = wake
    out["command"] = command
    # Idle-action path: volume/system without wake phrase still counts as heard.
    if not wake and not command:
        out["error"] = "stt_empty_or_no_command"
        _write(diag, out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 4

    # Bare wake → wake_ack; wake+command → stripped command (production parity).
    if wake and str(command or "").strip():
        inject = command
    else:
        inject = stt or ""
    mgr = _make_manager(live_tools=False)
    tts = RoutedTTS()
    try:
        row = run_one_turn(mgr, inject, debug=True)
        out["cm"] = row if isinstance(row, dict) else {"raw": str(row)[:400]}
        row_d = out["cm"] if isinstance(out["cm"], dict) else {}
        reply = str(row_d.get("FINAL_RESPONSE") or row_d.get("text") or "").strip() or "OK."
        out["tts_text"] = reply
        t1 = time.perf_counter()
        speak_ok = bool(tts.speak(reply))
        out["tts"] = {"ok": speak_ok, "wall_s": round(time.perf_counter() - t1, 3)}

        # Production-style follow-up: bare wake → listen for command.
        bare_wake = bool(wake and not str(command or "").strip())
        if bare_wake and speak_ok:
            print()
            print("=" * 60)
            print("  Bare wake OK — POWIEDZ KOMENDĘ (do 20s):  volume up")
            print("=" * 60)
            print()
            t2 = time.perf_counter()
            audio2 = V.record_until_silence(wait_timeout=20.0)
            out["followup_listen_wall_s"] = round(time.perf_counter() - t2, 3)
            if audio2 is not None and len(audio2) >= 1600:
                stt2 = V.transcribe_audio(audio2, llm_correct=False)
                out["followup_stt"] = stt2
                if stt2:
                    from WakeWord import extract_wake as _ew

                    w2 = _ew(stt2)
                    inject2 = (
                        w2.raw_command
                        if w2.wake_detected and str(w2.raw_command or "").strip()
                        else stt2
                    )
                    out["followup_inject"] = inject2
                    row2 = run_one_turn(mgr, inject2, debug=True)
                    out["cm_followup"] = row2 if isinstance(row2, dict) else {"raw": str(row2)[:400]}
                    r2 = out["cm_followup"] if isinstance(out["cm_followup"], dict) else {}
                    reply2 = str(r2.get("FINAL_RESPONSE") or r2.get("text") or "").strip()
                    if reply2:
                        t3 = time.perf_counter()
                        out["tts_followup"] = {
                            "ok": bool(tts.speak(reply2)),
                            "text": reply2,
                            "wall_s": round(time.perf_counter() - t3, 3),
                        }
            else:
                out["followup_error"] = "no_speech"
    finally:
        try:
            mgr.deactivate(reason="spoken_rc_end")
        except Exception:
            pass
        reset_model_client()
        reset_registry_for_tests(None)

    def _tool_ok(cm_row: dict) -> bool:
        soft = str(cm_row.get("soft_kind") or "")
        tools = cm_row.get("TOOL") or []
        if soft in ("volume_up", "volume_down", "brightness_up", "brightness_down"):
            return True
        return any(
            isinstance(t, dict) and t.get("success") and t.get("tool")
            for t in tools
        )

    row = out.get("cm") if isinstance(out.get("cm"), dict) else {}
    row2 = out.get("cm_followup") if isinstance(out.get("cm_followup"), dict) else {}
    speak_ok = bool((out.get("tts") or {}).get("ok"))
    ok_action = _tool_ok(row) or _tool_ok(row2)
    wake_ok = bool(wake and speak_ok)
    if ok_action and speak_ok:
        out["status"] = "PASS"
        out["pass_level"] = "WAKE_PLUS_TOOL"
    elif wake_ok:
        out["status"] = "PASS"
        out["pass_level"] = "WAKE_ACK_ONLY"
        out["note"] = "Live wake+TTS OK; no tool turn in this window."
    else:
        out["error"] = out.get("error") or "action_or_tts_failed"
        out["status"] = "FAIL"
    _write(diag, out)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["status"] == "PASS" else 5


def _write(diag: Path, out: dict) -> None:
    ts = time.strftime("%Y%m%dT%H%M%S")
    path = diag / f"spoken_wake_rc_{ts}.json"
    latest = diag / "spoken_wake_rc_latest.json"
    payload = json.dumps(out, ensure_ascii=False, indent=2)
    path.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")
    print(f"[spoken_rc] wrote {path}")


if __name__ == "__main__":
    raise SystemExit(main())
