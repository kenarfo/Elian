"""E2E OLD (spawn/init/exit) vs NEW (persistent worker session) Fish benchmark."""
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

DIAG = ROOT / "Data" / "diagnostics"
DIAG.mkdir(parents=True, exist_ok=True)
REPORT = DIAG / "fish_persistent_e2e_benchmark.json"
TEXTS = [
    "Elian is online.",
    "Systems nominal.",
    "Ready for the next command.",
]


def vram_mb():
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


def s2_pids():
    pids = []
    try:
        import psutil
    except Exception:
        return pids
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = (p.info.get("name") or "").lower()
            exe = (p.info.get("exe") or "").lower()
            if name == "s2.exe" or exe.endswith("\\s2.exe"):
                pids.append(int(p.info["pid"]))
        except Exception:
            continue
    return pids


def unload_ollama(model: str):
    from core.ollama_lifecycle import unload_model

    return unload_model(model=model, wait_s=8.0)


def run_old(model: str) -> dict:
    os.environ["ELIAN_FISH_PERSISTENT"] = "0"
    os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "0"
    from voice.fish_s2_backend import reset_fish_health_for_tests, synthesize_s2_wav
    from voice.tts_engine import reset_tts_engine_for_tests

    reset_tts_engine_for_tests()
    reset_fish_health_for_tests()
    unload_ollama(model)
    before = vram_mb()
    runs = []
    peak = before
    t0 = time.perf_counter()
    for i, text in enumerate(TEXTS, 1):
        path = DIAG / f"bench_old_{i}.wav"
        if path.exists():
            path.unlink()
        samples = []
        stop = threading.Event()

        def poll():
            while not stop.is_set():
                samples.append(vram_mb())
                time.sleep(0.25)

        th = threading.Thread(target=poll, daemon=True)
        th.start()
        ok, lat, det = synthesize_s2_wav(text, str(path), max_tokens=256)
        stop.set()
        th.join(timeout=2)
        pv = max([v for v in samples if v is not None] or [peak or 0])
        peak = max(peak or 0, pv)
        runs.append(
            {
                "i": i,
                "ok": ok,
                "latency_ms": round(lat, 1),
                "detail": det,
                "wav_bytes": path.stat().st_size if path.is_file() else 0,
            }
        )
    total = (time.perf_counter() - t0) * 1000.0
    after = vram_mb()
    return {
        "mode": "old_spawn_per_request",
        "runs": runs,
        "session_total_ms": round(total, 1),
        "avg_request_ms": round(sum(r["latency_ms"] for r in runs) / max(1, len(runs)), 1),
        "peak_vram_mb": peak,
        "vram_before_mb": before,
        "vram_after_mb": after,
        "orphan_s2": s2_pids(),
    }


def run_new(model: str) -> dict:
    os.environ["ELIAN_FISH_PERSISTENT"] = "1"
    os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "0"
    from voice.fish_s2_backend import (
        end_fish_tts_session,
        fish_session_snapshot,
        reset_fish_health_for_tests,
        synthesize_s2_wav,
    )
    from voice.tts_engine import reset_tts_engine_for_tests

    reset_tts_engine_for_tests()
    reset_fish_health_for_tests()
    end_fish_tts_session(reason="bench_reset")

    # Ollama → Fish handoff
    t_h0 = time.perf_counter()
    # Load ollama briefly then unload (handoff realism)
    try:
        import ollama

        client = ollama.Client(timeout=60)
        client.chat(
            model=model,
            messages=[{"role": "user", "content": "Say OK"}],
            options={"num_predict": 4},
            keep_alive="30m",
        )
    except Exception:
        pass
    vram_ollama = vram_mb()
    unload = unload_ollama(model)
    vram_after_unload = vram_mb()
    handoff_ms = (time.perf_counter() - t_h0) * 1000.0

    runs = []
    peak = vram_after_unload
    samples = []
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            samples.append(vram_mb())
            time.sleep(0.25)

    th = threading.Thread(target=poll, daemon=True)
    th.start()
    t0 = time.perf_counter()
    for i, text in enumerate(TEXTS, 1):
        path = DIAG / f"bench_new_{i}.wav"
        if path.exists():
            path.unlink()
        ok, lat, det = synthesize_s2_wav(text, str(path), max_tokens=256)
        sess = fish_session_snapshot()
        runs.append(
            {
                "i": i,
                "ok": ok,
                "latency_ms": round(lat, 1),
                "detail": det,
                "session_requests": sess.get("requests"),
                "cold_start_ms": sess.get("cold_start_ms"),
                "wav_bytes": path.stat().st_size if path.is_file() else 0,
            }
        )
    session_ms = (time.perf_counter() - t0) * 1000.0
    stop.set()
    th.join(timeout=2)
    peak = max([v for v in samples if v is not None] or [peak or 0])
    vram_during = vram_mb()
    end_info = end_fish_tts_session(reason="bench_done")
    time.sleep(1.0)
    vram_after_fish = vram_mb()
    orphans = s2_pids()

    # Fish → Ollama handoff
    t_r0 = time.perf_counter()
    reload_ok = False
    try:
        import ollama

        ans = ollama.Client(timeout=60).chat(
            model=model,
            messages=[{"role": "user", "content": "Say OK"}],
            options={"num_predict": 4},
            keep_alive="30m",
        )
        reload_ok = bool((ans.get("message") or {}).get("content"))
    except Exception:
        reload_ok = False
    vram_ollama_reload = vram_mb()
    reload_ms = (time.perf_counter() - t_r0) * 1000.0
    unload_ollama(model)

    warm = [r["latency_ms"] for r in runs[1:] if r.get("ok")]
    return {
        "mode": "new_persistent_session",
        "handoff_ollama_to_fish_ms": round(handoff_ms, 1),
        "unload": unload,
        "vram_ollama_before_fish_mb": vram_ollama,
        "vram_after_unload_mb": vram_after_unload,
        "worker_cold_start_ms": runs[0].get("cold_start_ms") if runs else None,
        "runs": runs,
        "session_total_ms": round(session_ms, 1),
        "avg_warm_latency_ms": round(sum(warm) / len(warm), 1) if warm else None,
        "avg_all_request_ms": round(sum(r["latency_ms"] for r in runs) / max(1, len(runs)), 1),
        "peak_vram_mb": peak,
        "vram_during_fish_mb": vram_during,
        "vram_after_fish_mb": vram_after_fish,
        "end_session": {
            "orphans_killed": end_info.get("orphans_killed"),
            "requests": end_info.get("requests"),
        },
        "orphan_s2_after_end": orphans,
        "ollama_reload_ok": reload_ok,
        "ollama_reload_ms": round(reload_ms, 1),
        "vram_ollama_reload_mb": vram_ollama_reload,
        "ollama_regained_gpu": bool(
            reload_ok
            and vram_ollama_reload is not None
            and vram_after_fish is not None
            and vram_ollama_reload > vram_after_fish + 500
        ),
    }


def run_cancel(model: str) -> dict:
    os.environ["ELIAN_FISH_PERSISTENT"] = "1"
    from voice.fish_s2_backend import (
        cancel_s2_synthesis,
        end_fish_tts_session,
        reset_fish_health_for_tests,
        synthesize_s2_wav,
    )

    reset_fish_health_for_tests()
    end_fish_tts_session(reason="cancel_prep")
    unload_ollama(model)
    path = DIAG / "bench_cancel.wav"
    if path.exists():
        path.unlink()

    def cancel_soon():
        time.sleep(1.2)
        cancel_s2_synthesis()

    threading.Thread(target=cancel_soon, daemon=True).start()
    t0 = time.perf_counter()
    ok, lat, det = synthesize_s2_wav(
        "This long sentence should be cancelled during persistent worker synthesis for Elian.",
        str(path),
        max_tokens=512,
        timeout_s=90,
    )
    elapsed = (time.perf_counter() - t0) * 1000.0
    time.sleep(0.8)
    return {
        "ok_interrupt": (not ok) and not s2_pids(),
        "synth_ok": ok,
        "detail": det,
        "cancel_latency_ms": round(elapsed, 1),
        "orphan_s2": s2_pids(),
    }


def main() -> int:
    from Config import get_config_value

    model = str(get_config_value("ai", "ollama_model", default="llama3.1:8b") or "llama3.1:8b")
    report = {
        "model": model,
        "quant": get_config_value("voice", "fish_quant", default=""),
        "voice_profile": get_config_value("voice", "fish_voice_profile", default=""),
        "texts": TEXTS,
    }
    print("=== OLD ===")
    report["old"] = run_old(model)
    print(json.dumps(report["old"], indent=2))
    print("=== NEW ===")
    report["new"] = run_new(model)
    print(json.dumps(report["new"], indent=2))
    print("=== CANCEL ===")
    report["cancel"] = run_cancel(model)
    print(json.dumps(report["cancel"], indent=2))

    warm = report["new"].get("avg_warm_latency_ms")
    report["pass"] = bool(
        all(r.get("ok") for r in report["old"]["runs"])
        and all(r.get("ok") for r in report["new"]["runs"])
        and warm is not None
        and warm < 10000
        and report["new"].get("ollama_regained_gpu")
        and not report["new"].get("orphan_s2_after_end")
        and report["cancel"].get("ok_interrupt")
        and (report["new"].get("peak_vram_mb") or 0) < 11500
    )
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("OVERALL", "PASS" if report["pass"] else "FAIL", "->", REPORT)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
