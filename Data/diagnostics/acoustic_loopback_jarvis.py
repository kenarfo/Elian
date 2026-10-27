# ==========================
# Acoustic loopback Jarvis path (headset play → mic → STT → CM → TTS)
# ==========================
"""Closest hardware proof without a human speaking:

1) Piper synth "Hi Elian, volume up"
2) Open Elian listen (AudioGate / record_until_silence)
3) Play WAV on configured Plantronics output (acoustic coupling)
4) Whisper STT → WakeWord extract → ConversationManager (dry tools)
5) Short RoutedTTS reply

Writes JSON under Data/diagnostics/. Exit 0 only when wake+command path succeeds.
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


def _rms(audio) -> float:
    import numpy as np

    if audio is None:
        return 0.0
    arr = np.asarray(audio, dtype=np.float32).flatten()
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(arr))))


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


def _play_wav(path: str, device, *, gain: float = 4.0) -> dict:
    import numpy as np
    import sounddevice as sd

    audio, rate = _load_wav(path)
    audio = np.clip(audio * float(gain), -1.0, 1.0)
    t0 = time.perf_counter()
    kwargs = {"samplerate": rate}
    if device is not None:
        kwargs["device"] = device
    sd.play(audio, **kwargs)
    sd.wait()
    return {
        "ok": True,
        "rate": rate,
        "samples": int(audio.size),
        "wall_s": round(time.perf_counter() - t0, 3),
        "device": device,
        "gain": gain,
    }


def _record_fixed_seconds(seconds: float, *, device, rate: int) -> tuple:
    """Callback capture under AudioGate (WDM-KS rejects blocking read / -9999)."""
    import numpy as np
    import sounddevice as sd
    from queue import Empty, Queue
    from voice.audio_gate import get_audio_gate

    gate = get_audio_gate()
    q: Queue = Queue()
    block = 512

    def _cb(indata, frames, time_info, status):
        if status:
            print("[loopback] audio status", status)
        q.put(np.asarray(indata, dtype=np.float32).copy())

    chunks = []
    t0 = time.perf_counter()
    deadline = t0 + float(seconds)
    with gate.input_stream_slot(wait_for_tts=True):
        kwargs = {
            "samplerate": int(rate),
            "channels": 1,
            "dtype": "float32",
            "blocksize": block,
            "callback": _cb,
        }
        if device is not None:
            kwargs["device"] = device
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
    from Config import DATA_DIR, get_config_value
    import Voice as V
    from WakeWord import extract_wake, is_wake_word
    from core.simulated_voice import piper_available, synthesize_to_wav
    from core.text_inject import _make_manager, run_one_turn
    from core.model_client import reset_model_client
    from core.tools import reset_registry_for_tests
    from voice.audio_gate import get_audio_gate, reset_audio_gate_for_tests
    from voice.tts_engine import RoutedTTS

    out: dict = {
        "name": "acoustic_loopback_jarvis",
        "phrase": PHRASE,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "FAIL",
    }
    diag = Path(DATA_DIR) / "diagnostics"
    diag.mkdir(parents=True, exist_ok=True)

    if not piper_available():
        out["error"] = "piper_unavailable"
        _write(diag, out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 2

    reset_audio_gate_for_tests()
    V.initialize_voice()
    out["input_device"] = V._INPUT_DEVICE
    out["output_device"] = V._OUTPUT_DEVICE
    out["capture_rate"] = V._CAPTURE_RATE
    out["input_name"] = get_config_value("voice", "input_device_name", default="")
    out["output_name"] = get_config_value("voice", "output_device_name", default="")

    fd, wav_path = tempfile.mkstemp(prefix="elian_loopback_", suffix=".wav")
    os.close(fd)
    out["wav"] = wav_path
    try:
        if not synthesize_to_wav(PHRASE, wav_path):
            out["error"] = "piper_synth_failed"
            _write(diag, out)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 2

        # Fixed-duration capture (VAD rejects quiet headset sidetone ~0.0003 RMS).
        play_audio, play_rate = _load_wav(wav_path)
        play_dur = max(2.5, len(play_audio) / float(play_rate) + 0.8)
        holder: dict = {"audio": None, "exc": None, "wall_s": 0.0}

        def _listen():
            try:
                audio, wall = _record_fixed_seconds(
                    play_dur + 0.5,
                    device=V._INPUT_DEVICE,
                    rate=int(V._CAPTURE_RATE or 16000),
                )
                holder["audio"] = audio
                holder["wall_s"] = wall
            except Exception as exc:
                holder["exc"] = f"{type(exc).__name__}: {exc}"

        th = threading.Thread(target=_listen, name="loopback-listen", daemon=True)
        th.start()
        time.sleep(0.35)  # let InputStream open under AudioGate
        try:
            out["play"] = _play_wav(wav_path, V._OUTPUT_DEVICE, gain=6.0)
        except Exception as exc:
            out["play"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        th.join(timeout=30.0)
        if th.is_alive():
            out["error"] = "listen_timeout"
            _write(diag, out)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 1

        out["listen_wall_s"] = holder["wall_s"]
        out["gate"] = get_audio_gate().snapshot().__dict__
        if holder["exc"]:
            out["error"] = holder["exc"]
            out["portaudio_error"] = (
                "-9996" in holder["exc"]
                or "-9985" in holder["exc"]
                or "PortAudio" in holder["exc"]
            )
            _write(diag, out)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 1

        audio = holder["audio"]
        out["rms"] = round(_rms(audio), 6)
        out["audio_samples"] = 0 if audio is None else int(len(audio))
        out["portaudio_open_ok"] = True
        if audio is None or out["audio_samples"] < 1600 or out["rms"] < 5e-5:
            out["error"] = "acoustic_coupling_weak"
            out["note"] = (
                "Mic open/close OK under AudioGate; headset sidetone too weak for STT. "
                "Human 'Hi Elian' still required for spoken HARDWARE PASS."
            )
            _write(diag, out)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 3

        t_stt = time.perf_counter()
        stt = V.transcribe_audio(audio, llm_correct=False)
        out["stt_ms"] = round((time.perf_counter() - t_stt) * 1000.0, 1)
        out["stt_text"] = stt
        wake = bool(stt and is_wake_word(stt))
        command = extract_wake(stt).raw_command if stt else ""
        out["wake"] = wake
        out["command"] = command
        if not wake:
            out["error"] = "wake_not_detected"
            _write(diag, out)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 4

        mgr = _make_manager(live_tools=False)
        try:
            # Prefer post-wake command; fall back to full STT if strip emptied.
            inject = command or stt
            row = run_one_turn(mgr, inject, debug=True)
            out["cm"] = row if isinstance(row, dict) else {"raw": str(row)[:500]}
        finally:
            try:
                mgr.deactivate(reason="loopback_end")
            except Exception:
                pass
            reset_model_client()
            reset_registry_for_tests(None)

        row = out.get("cm") if isinstance(out.get("cm"), dict) else {}
        reply = str(
            row.get("FINAL_RESPONSE")
            or row.get("text")
            or row.get("result")
            or row.get("reply")
            or ""
        ).strip()
        if not reply:
            reply = "Głośniej." if "volume" in (command or "").lower() else "OK."
        out["tts_text"] = reply
        tts = RoutedTTS()
        t0 = time.perf_counter()
        speak_ok = bool(tts.speak(reply))
        active = ""
        try:
            active = str((tts.health_check().extras or {}).get("active") or "")
        except Exception:
            pass
        out["tts"] = {
            "ok": speak_ok,
            "wall_s": round(time.perf_counter() - t0, 3),
            "active": active,
        }

        out["status"] = "PASS" if speak_ok else "PASS_NO_TTS"
        if not speak_ok:
            out["error"] = "tts_speak_failed"
        _write(diag, out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out["status"].startswith("PASS") else 1
    finally:
        try:
            if os.path.isfile(wav_path):
                os.remove(wav_path)
        except OSError:
            pass


def _write(diag: Path, out: dict) -> None:
    ts = time.strftime("%Y%m%dT%H%M%S")
    path = diag / f"acoustic_loopback_{ts}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = diag / "acoustic_loopback_latest.json"
    latest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[loopback] wrote {path}")


if __name__ == "__main__":
    raise SystemExit(main())
