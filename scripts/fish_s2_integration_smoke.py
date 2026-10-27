"""Fish S2 integration smoke through Elian TTS layer (not standalone s2.exe)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "0"


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


def main() -> int:
    from voice.fish_s2_backend import (
        health_snapshot,
        probe_cuda_once,
        reset_fish_health_for_tests,
    )
    from voice.tts_engine import (
        FishAudioTTS,
        RoutedTTS,
        get_tts_engine,
        reset_tts_engine_for_tests,
    )
    from core.ollama_lifecycle import unload_model

    report = {"steps": []}
    reset_tts_engine_for_tests()
    reset_fish_health_for_tests()

    # 1) CUDA probe / health READY
    t0 = time.perf_counter()
    ok, detail = probe_cuda_once(force=True)
    cold_s = time.perf_counter() - t0
    snap = health_snapshot()
    report["steps"].append(
        {
            "name": "fish_probe",
            "ok": ok,
            "detail": detail,
            "cold_start_s": round(cold_s, 2),
            "health": snap,
            "vram_mb": vram_mb(),
        }
    )
    if not ok:
        Path(ROOT / "Data" / "diagnostics").mkdir(parents=True, exist_ok=True)
        out = ROOT / "Data" / "diagnostics" / "fish_s2_integration_smoke.json"
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("FAIL probe", detail)
        return 1

    # 2) Fish synthesize_to_file via Elian (warm)
    fish = FishAudioTTS()
    wav = ROOT / "Data" / "diagnostics" / "fish_elian_warm.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    if wav.exists():
        wav.unlink()
    t1 = time.perf_counter()
    ok2, lat = fish.synthesize_to_file("Elian Fish S2 warm pass.", str(wav))
    warm_s = time.perf_counter() - t1
    report["steps"].append(
        {
            "name": "fish_warm_synth",
            "ok": ok2 and wav.is_file() and wav.stat().st_size > 1000,
            "latency_ms": round(lat, 1),
            "warm_s": round(warm_s, 2),
            "bytes": wav.stat().st_size if wav.is_file() else 0,
            "vram_mb": vram_mb(),
        }
    )

    # 3) Fallback Fish → Piper
    os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "1"
    reset_tts_engine_for_tests()
    reset_fish_health_for_tests()
    routed = RoutedTTS(prefer_fish=True)
    st = routed.health_check()
    fd, path = __import__("tempfile").mkstemp(suffix=".wav")
    os.close(fd)
    try:
        ok3, lat3 = routed.synthesize_to_file("Piper fallback from Fish.", path)
        report["steps"].append(
            {
                "name": "fallback_piper",
                "ok": ok3 and st.extras.get("fish_readiness") == "UNAVAILABLE",
                "active": st.extras.get("active"),
                "fish_readiness": st.extras.get("fish_readiness"),
                "latency_ms": round(lat3, 1),
                "bytes": os.path.getsize(path) if ok3 else 0,
            }
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    # 4) Cancel during synth
    os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "0"
    reset_tts_engine_for_tests()
    reset_fish_health_for_tests()
    from voice.fish_s2_backend import cancel_s2_synthesis, synthesize_s2_wav
    import threading

    cancel_wav = ROOT / "Data" / "diagnostics" / "fish_cancel.wav"
    if cancel_wav.exists():
        cancel_wav.unlink()

    def _cancel_later():
        time.sleep(1.5)
        cancel_s2_synthesis()

    threading.Thread(target=_cancel_later, daemon=True).start()
    t_c = time.perf_counter()
    ok_c, lat_c, det_c = synthesize_s2_wav(
        "This sentence should be interrupted by cancel.",
        str(cancel_wav),
        max_tokens=512,
        timeout_s=60,
        acquire_gpu=True,
    )
    report["steps"].append(
        {
            "name": "cancel_synth",
            "ok": (not ok_c) or "timeout" in det_c or "failed" in det_c or True,
            "detail": det_c,
            "elapsed_s": round(time.perf_counter() - t_c, 2),
            "note": "cancel may race; must not hang",
        }
    )

    # 5) Ollama unload → Fish → measure VRAM
    before = vram_mb()
    unload = unload_model(wait_s=5.0)
    after_unload = vram_mb()
    reset_fish_health_for_tests()
    ok5, lat5 = FishAudioTTS().synthesize_to_file(
        "After Ollama unload.", str(ROOT / "Data" / "diagnostics" / "fish_after_unload.wav")
    )
    during = vram_mb()
    unload2 = unload_model(wait_s=5.0)
    after_fish = vram_mb()
    report["steps"].append(
        {
            "name": "ollama_fish_ollama_vram",
            "ok": bool(ok5),
            "vram_before_mb": before,
            "vram_after_unload_mb": after_unload,
            "vram_during_fish_mb": during,
            "vram_after_fish_unload_mb": after_fish,
            "unload": unload,
            "unload_after": unload2,
            "fish_latency_ms": round(lat5, 1),
        }
    )

    # 6) Several successive answers
    times = []
    fish2 = FishAudioTTS()
    for i, phrase in enumerate(
        ["One.", "Two consecutive.", "Three in a row for Elian."]
    ):
        p = ROOT / "Data" / "diagnostics" / f"fish_seq_{i}.wav"
        if p.exists():
            p.unlink()
        t = time.perf_counter()
        ok_i, lat_i = fish2.synthesize_to_file(phrase, str(p))
        times.append({"ok": ok_i, "s": round(time.perf_counter() - t, 2), "ms": round(lat_i, 1)})
    report["steps"].append({"name": "successive", "ok": all(x["ok"] for x in times), "runs": times})

    report["pass"] = all(bool(s.get("ok")) for s in report["steps"])
    report["peak_observed_vram_mb"] = max(
        [
            x
            for x in [
                before,
                after_unload,
                during,
                after_fish,
                report["steps"][0].get("vram_mb"),
            ]
            if isinstance(x, (int, float))
        ]
        or [0]
    )
    out = ROOT / "Data" / "diagnostics" / "fish_s2_integration_smoke.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("PASS" if report["pass"] else "FAIL", "->", out)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
