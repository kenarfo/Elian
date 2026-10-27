# Replay a LIVE-captured wake STT through wake_ack → volume_up → TTS.
from __future__ import annotations

import json
import os
import time
from pathlib import Path

os.environ.setdefault("ELIAN_PROTECT_USER_APPS", "1")


def main() -> int:
    from WakeWord import is_wake_word
    from core.text_inject import _make_manager, run_one_turn
    from core.model_client import reset_model_client
    from core.tools import reset_registry_for_tests
    from voice.tts_engine import RoutedTTS

    diag = Path("Data/diagnostics")
    cands = sorted(
        diag.glob("spoken_wake_rc_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    stt = None
    src = None
    for p in cands:
        j = json.loads(p.read_text(encoding="utf-8"))
        text = str(j.get("stt_text") or "")
        if j.get("wake") and text and is_wake_word(text):
            stt = text
            src = str(p)
            break
    out = {
        "name": "replay_live_stt_wake_tool",
        "source": src,
        "stt_text": stt,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "FAIL",
    }
    if not stt:
        out["error"] = "no_live_wake_stt_found"
        _write(diag, out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 2

    print("source", src, "stt", stt, flush=True)
    mgr = _make_manager(live_tools=False)
    # Prefer Piper for this offline replay — avoids Fish s2 hang/VRAM contention.
    tts = RoutedTTS()
    try:
        tts.prefer_fish = False
        tts.active = tts.fallback
    except Exception:
        pass
    try:
        r1 = run_one_turn(mgr, stt, debug=True)
        ack = str(r1.get("FINAL_RESPONSE") or r1.get("text") or "Listening.")
        out["cm_wake"] = r1
        out["tts_ack"] = {"ok": bool(tts.speak(ack)), "text": ack}
        r2 = run_one_turn(mgr, "volume up", debug=True)
        out["cm_tool"] = r2
        soft = str(r2.get("soft_kind") or "")
        tools = r2.get("TOOL") or []
        tool_ok = soft == "volume_up" or any(
            isinstance(t, dict) and t.get("tool") == "volume_up" and t.get("success")
            for t in tools
        )
        reply = str(r2.get("FINAL_RESPONSE") or r2.get("text") or "Głośniej.")
        out["tool_ok"] = tool_ok
        out["tts_tool"] = {"ok": bool(tts.speak(reply)), "text": reply}
        if out["tts_ack"]["ok"] and tool_ok and out["tts_tool"]["ok"]:
            out["status"] = "PASS"
            out["pass_level"] = "LIVE_STT_WAKE_PLUS_SESSION_TOOL"
        else:
            out["error"] = "tool_or_tts_failed"
    finally:
        try:
            mgr.deactivate(reason="replay_end")
        except Exception:
            pass
        reset_model_client()
        reset_registry_for_tests(None)

    _write(diag, out)
    print(
        json.dumps(
            {
                k: out.get(k)
                for k in (
                    "status",
                    "pass_level",
                    "stt_text",
                    "tool_ok",
                    "tts_ack",
                    "tts_tool",
                    "source",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if out["status"] == "PASS" else 1


def _write(diag: Path, out: dict) -> None:
    ts = time.strftime("%Y%m%dT%H%M%S")
    path = diag / f"replay_live_stt_wake_tool_{ts}.json"
    latest = diag / "replay_live_stt_wake_tool_latest.json"
    payload = json.dumps(out, ensure_ascii=False, indent=2)
    path.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")
    print(f"[replay] wrote {path}")


if __name__ == "__main__":
    raise SystemExit(main())
