# ==========================
# Live mic wake → session tool probe (volume_up)
# ==========================
"""1) Real mic wake (Hi Elian)
2) TTS Listening.
3) Same CM session: inject 'volume up' (proves post-wake tool path)
4) TTS tool reply

Exit 0 only when wake was from live STT and volume_up tool succeeded.
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
    wait_s = 30.0
    if "--wait" in sys.argv:
        i = sys.argv.index("--wait")
        if i + 1 < len(sys.argv):
            wait_s = float(sys.argv[i + 1])

    from Config import DATA_DIR
    import Voice as V
    from WakeWord import extract_wake, is_wake_word
    from core.text_inject import _make_manager, run_one_turn
    from core.model_client import reset_model_client
    from core.tools import reset_registry_for_tests
    from voice.mic_mute import ensure_capture_unmuted
    from voice.tts_engine import RoutedTTS

    out = {
        "name": "live_wake_tool_probe",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "wait_s": wait_s,
        "status": "FAIL",
    }
    diag = Path(DATA_DIR) / "diagnostics"
    diag.mkdir(parents=True, exist_ok=True)

    ensure_capture_unmuted(auto_unmute=True)
    V.initialize_voice()
    out["input_device"] = V._INPUT_DEVICE
    print()
    print("=" * 60)
    print(f"  POWIEDZ WAKE (do {wait_s:.0f}s):  Hi Elian")
    print("  (potem automatycznie: volume up w tej samej sesji)")
    print("=" * 60)
    print()

    t0 = time.perf_counter()
    try:
        audio = V.record_until_silence(wait_timeout=wait_s)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        _write(diag, out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1
    out["listen_wall_s"] = round(time.perf_counter() - t0, 3)
    if audio is None or len(audio) < 1600:
        out["error"] = "no_speech"
        _write(diag, out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 3

    stt = V.transcribe_audio(audio, llm_correct=False)
    out["stt_text"] = stt
    if not stt or not is_wake_word(stt):
        out["error"] = "wake_not_detected"
        out["wake"] = False
        _write(diag, out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 4
    out["wake"] = True
    out["command_after_wake"] = extract_wake(stt).raw_command

    mgr = _make_manager(live_tools=False)
    tts = RoutedTTS()
    try:
        # Bare wake utterance into CM → Listening ack
        row1 = run_one_turn(mgr, stt, debug=True)
        out["cm_wake"] = row1
        ack = str((row1 or {}).get("FINAL_RESPONSE") or (row1 or {}).get("text") or "Listening.")
        out["tts_ack"] = {
            "ok": bool(tts.speak(ack)),
            "text": ack,
        }
        # Same session: tool path after live wake
        row2 = run_one_turn(mgr, "volume up", debug=True)
        out["cm_tool"] = row2
        soft = str((row2 or {}).get("soft_kind") or "")
        tools = (row2 or {}).get("TOOL") or []
        tool_ok = soft == "volume_up" or any(
            isinstance(t, dict) and t.get("tool") == "volume_up" and t.get("success")
            for t in tools
        )
        out["tool_ok"] = tool_ok
        reply = str((row2 or {}).get("FINAL_RESPONSE") or (row2 or {}).get("text") or "Głośniej.")
        out["tts_tool"] = {
            "ok": bool(tts.speak(reply)),
            "text": reply,
        }
        if (
            out["tts_ack"].get("ok")
            and tool_ok
            and out["tts_tool"].get("ok")
        ):
            out["status"] = "PASS"
            out["pass_level"] = "LIVE_WAKE_PLUS_SESSION_TOOL"
        else:
            out["error"] = "tool_or_tts_failed"
    finally:
        try:
            mgr.deactivate(reason="live_wake_tool_probe_end")
        except Exception:
            pass
        reset_model_client()
        reset_registry_for_tests(None)

    _write(diag, out)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["status"] == "PASS" else 5


def _write(diag: Path, out: dict) -> None:
    ts = time.strftime("%Y%m%dT%H%M%S")
    path = diag / f"live_wake_tool_probe_{ts}.json"
    latest = diag / "live_wake_tool_probe_latest.json"
    payload = json.dumps(out, ensure_ascii=False, indent=2)
    path.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")
    print(f"[probe] wrote {path}")


if __name__ == "__main__":
    raise SystemExit(main())
