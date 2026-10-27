# ==========================
# Virtual/room mic loopback → wake → volume_up tool
# ==========================
"""Play Piper phrase on Plantronics out; capture from EaseUS VoiceWave or Realtek.

Proves acoustic/virtual path: play → capture → STT → wake → soft volume_up → TTS.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("ELIAN_PROTECT_USER_APPS", "1")
PHRASE = "Hi Elian, volume up"


def _load_wav(path: str):
    import numpy as np

    with wave.open(path, "rb") as wf:
        rate = int(wf.getframerate())
        ch = int(wf.getnchannels())
        frames = wf.readframes(wf.getnframes())
        width = wf.getsampwidth()
    if width == 2:
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    else:
        audio = np.frombuffer(frames, dtype=np.float32)
    if ch > 1:
        audio = audio.reshape(-1, ch).mean(axis=1)
    return audio, rate


def _pick_capture(sd) -> tuple:
    prefer = ("voicewave", "easeus", "realtek")
    cands = []
    for i, d in enumerate(sd.query_devices()):
        if int(d.get("max_input_channels") or 0) < 1:
            continue
        name = str(d.get("name") or "")
        ha = str(sd.query_hostapis(d["hostapi"])["name"])
        low = name.lower()
        score = 99
        for rank, token in enumerate(prefer):
            if token in low:
                score = rank
                break
        if score >= 99:
            continue
        # Prefer WASAPI/DirectSound over WDM for blocking-free callback.
        if "WDM" in ha.upper():
            score += 0.5
        cands.append((score, i, name, ha, int(d.get("default_samplerate") or 16000)))
    cands.sort()
    if not cands:
        return None, "", "", 16000
    _, idx, name, ha, rate = cands[0]
    return idx, name, ha, rate


def _record_cb(seconds: float, *, device, rate: int):
    import numpy as np
    import sounddevice as sd
    from queue import Empty, Queue

    q: Queue = Queue()

    def cb(indata, frames, time_info, status):
        q.put(np.asarray(indata, dtype=np.float32).copy())

    chunks = []
    t0 = time.perf_counter()
    deadline = t0 + float(seconds)
    kwargs = {
        "samplerate": int(rate),
        "channels": 1,
        "dtype": "float32",
        "blocksize": 512,
        "callback": cb,
        "device": device,
    }
    with sd.InputStream(**kwargs):
        while time.perf_counter() < deadline:
            try:
                data = q.get(timeout=0.1)
                chunks.append(np.asarray(data, dtype=np.float32).reshape(-1))
            except Empty:
                continue
    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    return audio, round(time.perf_counter() - t0, 3)


def main() -> int:
    import numpy as np
    import sounddevice as sd
    import Voice as V
    from WakeWord import extract_wake, is_wake_word
    from core.simulated_voice import piper_available, synthesize_to_wav
    from core.text_inject import _make_manager, run_one_turn
    from core.model_client import reset_model_client
    from core.tools import reset_registry_for_tests
    from voice.tts_engine import RoutedTTS
    from Config import DATA_DIR

    out = {
        "name": "virtual_loopback_tool_rc",
        "phrase": PHRASE,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "FAIL",
    }
    diag = Path(DATA_DIR) / "diagnostics"
    diag.mkdir(parents=True, exist_ok=True)

    if not piper_available():
        out["error"] = "piper_unavailable"
        _write(diag, out)
        return 2

    V.initialize_voice()
    cap_idx, cap_name, cap_ha, cap_rate = _pick_capture(sd)
    out["capture"] = {"idx": cap_idx, "name": cap_name, "host": cap_ha, "rate": cap_rate}
    out["play_device"] = V._OUTPUT_DEVICE
    if cap_idx is None:
        out["error"] = "no_virtual_or_room_mic"
        _write(diag, out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 2

    fd, wav_path = tempfile.mkstemp(prefix="elian_vloop_", suffix=".wav")
    os.close(fd)
    try:
        if not synthesize_to_wav(PHRASE, wav_path):
            out["error"] = "piper_failed"
            _write(diag, out)
            return 2
        audio_play, play_rate = _load_wav(wav_path)
        dur = max(2.8, len(audio_play) / float(play_rate) + 1.0)
        holder = {"audio": None, "exc": None, "wall": 0.0}

        def _listen():
            try:
                a, w = _record_cb(dur + 0.4, device=cap_idx, rate=int(cap_rate))
                holder["audio"] = a
                holder["wall"] = w
            except Exception as exc:
                holder["exc"] = f"{type(exc).__name__}: {exc}"

        th = threading.Thread(target=_listen, daemon=True)
        th.start()
        time.sleep(0.4)
        boosted = np.clip(audio_play * 6.0, -1.0, 1.0)
        kwargs = {"samplerate": play_rate}
        if V._OUTPUT_DEVICE is not None:
            kwargs["device"] = V._OUTPUT_DEVICE
        sd.play(boosted, **kwargs)
        sd.wait()
        th.join(timeout=30)
        if holder["exc"]:
            out["error"] = holder["exc"]
            _write(diag, out)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 1
        audio = holder["audio"]
        rms = float(np.sqrt(np.mean(np.square(audio)))) if audio is not None and len(audio) else 0.0
        out["rms"] = round(rms, 6)
        out["samples"] = 0 if audio is None else int(len(audio))
        out["listen_wall_s"] = holder["wall"]
        if audio is None or out["samples"] < 1600 or rms < 1e-4:
            out["error"] = "capture_too_quiet"
            _write(diag, out)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 3

        # Resample to 16k for Whisper if needed.
        if int(cap_rate) != 16000:
            audio = V._resample_audio(audio, int(cap_rate), 16000)
        stt = V.transcribe_audio(audio, llm_correct=False)
        out["stt_text"] = stt
        wake = bool(stt and is_wake_word(stt))
        command = extract_wake(stt).raw_command if stt else ""
        out["wake"] = wake
        out["command"] = command
        inject = command if (wake and str(command).strip()) else (stt or "")
        mgr = _make_manager(live_tools=False)
        try:
            row = run_one_turn(mgr, inject, debug=True)
            out["cm"] = row if isinstance(row, dict) else {"raw": str(row)[:400]}
        finally:
            try:
                mgr.deactivate(reason="vloop_end")
            except Exception:
                pass
            reset_model_client()
            reset_registry_for_tests(None)

        row = out.get("cm") if isinstance(out.get("cm"), dict) else {}
        soft = str(row.get("soft_kind") or "")
        tools = row.get("TOOL") or []
        tool_ok = soft == "volume_up" or any(
            isinstance(t, dict) and t.get("tool") == "volume_up" and t.get("success")
            for t in tools
        )
        reply = str(row.get("FINAL_RESPONSE") or row.get("text") or "Głośniej.").strip()
        tts = RoutedTTS()
        t0 = time.perf_counter()
        speak_ok = bool(tts.speak(reply))
        out["tts"] = {"ok": speak_ok, "text": reply, "wall_s": round(time.perf_counter() - t0, 3)}
        if wake and tool_ok and speak_ok:
            out["status"] = "PASS"
            out["pass_level"] = "WAKE_PLUS_TOOL"
        else:
            out["error"] = "incomplete_path"
            out["status"] = "FAIL"
        _write(diag, out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out["status"] == "PASS" else 5
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass


def _write(diag: Path, out: dict) -> None:
    ts = time.strftime("%Y%m%dT%H%M%S")
    path = diag / f"virtual_loopback_tool_{ts}.json"
    latest = diag / "virtual_loopback_tool_latest.json"
    payload = json.dumps(out, ensure_ascii=False, indent=2)
    path.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")
    print(f"[vloop] wrote {path}")


if __name__ == "__main__":
    raise SystemExit(main())
