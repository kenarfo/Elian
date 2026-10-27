"""Measure exclusive GPU handoff: Ollama load → unload → Fish peak VRAM → done."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "0"

samples = []
stop = threading.Event()


def vram():
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        ).strip()
        return float(out.splitlines()[0].split(",")[0].strip())
    except Exception:
        return None


def poller():
    while not stop.is_set():
        samples.append({"t": time.time(), "vram_mb": vram()})
        time.sleep(0.4)


def main() -> int:
    import ollama
    from core.ollama_lifecycle import unload_model
    from voice.tts_engine import FishAudioTTS, reset_tts_engine_for_tests
    from voice.fish_s2_backend import reset_fish_health_for_tests

    reset_tts_engine_for_tests()
    reset_fish_health_for_tests()
    client = ollama.Client(timeout=120)
    model = "llama3.1:8b"
    baseline = vram()
    print("baseline", baseline, flush=True)
    client.chat(
        model=model,
        messages=[{"role": "user", "content": "Say hi in one word."}],
        options={"num_predict": 8},
        keep_alive="30m",
    )
    after_llm = vram()
    print("after_llm", after_llm, flush=True)

    th = threading.Thread(target=poller, daemon=True)
    th.start()
    unload = unload_model(model=model, wait_s=10.0)
    print("unload", unload, flush=True)
    after_unload = vram()
    print("after_unload", after_unload, flush=True)

    wav = ROOT / "Data" / "diagnostics" / "fish_vram_handoff.wav"
    t0 = time.perf_counter()
    ok, lat = FishAudioTTS().synthesize_to_file(
        "Exclusive GPU handoff from Ollama to Fish.", str(wav)
    )
    elapsed = time.perf_counter() - t0
    time.sleep(1.0)
    stop.set()
    th.join(timeout=2)
    peak = max((s["vram_mb"] or 0) for s in samples) if samples else None
    after_fish = vram()
    report = {
        "ok": bool(ok),
        "baseline_mb": baseline,
        "after_llm_mb": after_llm,
        "after_unload_mb": after_unload,
        "peak_during_fish_mb": peak,
        "after_fish_mb": after_fish,
        "fish_latency_ms": round(lat, 1),
        "fish_elapsed_s": round(elapsed, 2),
        "unload": unload,
        "wav_bytes": wav.stat().st_size if wav.is_file() else 0,
        "delta_llm_mb": None
        if baseline is None or after_llm is None
        else round(after_llm - baseline, 1),
        "ollama_released": bool(
            after_llm
            and after_unload
            and after_unload + 500 < after_llm
        ),
    }
    out = ROOT / "Data" / "diagnostics" / "fish_ollama_vram_handoff.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
