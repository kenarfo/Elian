"""Isolated Q4 vs Q8 Fish A/B. Does NOT touch production config/TTS lifecycle defaults.
Run: python Data/diagnostics/jarvis_parity/q4_vs_q8_ab_diag.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(r"C:\Users\kenar\Elian")
sys.path.insert(0, str(ROOT))
OUT = ROOT / "Data" / "diagnostics" / "jarvis_parity"
OUT.mkdir(parents=True, exist_ok=True)

TEXT = (
    "Good evening. I've been monitoring the situation. "
    "We should probably talk about what happens next."
)

PARAMS = {"max_new_tokens": 256, "temperature": 0.8, "top_p": 0.8, "top_k": 30}


def vram_used_mb() -> float | None:
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


def s2_rss_mb() -> float | None:
    try:
        import psutil
    except Exception:
        return None
    total = 0
    found = False
    for p in psutil.process_iter(["name", "exe", "memory_info"]):
        try:
            name = (p.info.get("name") or "").lower()
            exe = (p.info.get("exe") or "").lower()
            if name == "s2.exe" or exe.endswith("\\s2.exe"):
                found = True
                mi = p.info.get("memory_info")
                if mi is not None:
                    total += float(mi.rss) / (1024 * 1024)
        except Exception:
            continue
    return round(total, 1) if found else None


def wav_metrics(path: Path) -> dict:
    audio, rate = sf.read(str(path), always_2d=False)
    arr = np.asarray(audio, dtype=np.float64)
    if arr.ndim > 1:
        frames = int(arr.shape[0])
        mono = np.mean(arr, axis=1)
        ch = int(arr.shape[1])
    else:
        frames = int(len(arr))
        mono = arr
        ch = 1
    peak = float(np.max(np.abs(mono))) if frames else 0.0
    rms = float(np.sqrt(np.mean(mono**2))) if frames else 0.0
    # rough spectral proxy (not a listening substitute)
    if frames > 256:
        spec = np.abs(np.fft.rfft(mono[: min(frames, rate)]))
        freqs = np.fft.rfftfreq(len(mono[: min(frames, rate)]), d=1.0 / rate)
        denom = float(np.sum(spec)) or 1.0
        centroid = float(np.sum(freqs * spec) / denom)
    else:
        centroid = 0.0
    amp = np.abs(mono) / (peak or 1.0)
    voiced = np.where(amp >= 0.01)[0]
    lead = float(voiced[0] / rate) if voiced.size else float(frames / rate)
    trail = float((frames - 1 - voiced[-1]) / rate) if voiced.size else 0.0
    return {
        "sample_rate": int(rate),
        "channels": ch,
        "samples": frames,
        "duration_s": round(frames / float(rate), 3) if rate else 0.0,
        "peak": round(peak, 6),
        "rms": round(rms, 6),
        "spectral_centroid_hz": round(centroid, 1),
        "leading_silence_s": round(lead, 3),
        "trailing_silence_s": round(trail, 3),
        "bytes": path.stat().st_size,
    }


def run_variant(*, label: str, model_file: str, quant: str, port: int) -> dict:
    from voice.fish_s2_backend import (
        FishS2Config,
        _acquire_exclusive,
        _release_exclusive,
        _subprocess_env,
        end_fish_tts_session,
        load_fish_s2_config,
    )
    from voice.fish_s2_worker import (
        FishS2ServerWorker,
        drop_worker_singleton,
    )

    # Ensure no leftover production session/worker contaminates the run.
    end_fish_tts_session(reason=f"ab_{label}_reset")
    drop_worker_singleton(stop=True, reason=f"ab_{label}_reset")
    time.sleep(0.5)

    base = load_fish_s2_config()
    cfg = FishS2Config(
        exe=base.exe,
        model_dir=base.model_dir,
        model_file=model_file,
        tokenizer=base.tokenizer,
        quant=quant,
        voice_profile=base.voice_profile,
        voice_dir=base.voice_dir,
        gpu_policy=base.gpu_policy,
        gpu_device=base.gpu_device,
        dll_dir=base.dll_dir,
        cuda_bin=base.cuda_bin,
    )
    model_path = Path(cfg.model_path)
    voice_path = Path(cfg.voice_dir) / f"{cfg.voice_profile}.s2voice"

    result = {
        "label": label,
        "quant": quant,
        "model_file": model_file,
        "model_path": str(model_path),
        "model_exists": model_path.is_file(),
        "model_bytes": model_path.stat().st_size if model_path.is_file() else 0,
        "voice_profile": cfg.voice_profile,
        "voice_path": str(voice_path),
        "voice_exists": voice_path.is_file(),
        "params": dict(PARAMS),
        "exe": cfg.exe,
        "port": port,
        "vram_before_mb": vram_used_mb(),
        "rss_before_mb": s2_rss_mb(),
        "errors": [],
        "warnings": [],
        "log_tail": [],
    }
    if not model_path.is_file():
        result["errors"].append(f"model_missing:{model_path}")
        return result
    if not voice_path.is_file():
        result["errors"].append(f"voice_missing:{voice_path}")
        return result

    exclusive = {}
    worker = None
    peak_vram = result["vram_before_mb"]
    peak_rss = result["rss_before_mb"] or 0.0
    stop_poll = threading.Event()

    def _poll():
        nonlocal peak_vram, peak_rss
        while not stop_poll.wait(0.4):
            v = vram_used_mb()
            r = s2_rss_mb()
            if v is not None:
                peak_vram = v if peak_vram is None else max(peak_vram, v)
            if r is not None:
                peak_rss = max(peak_rss, r)

    poller = threading.Thread(target=_poll, name=f"ab-poll-{label}", daemon=True)
    try:
        exclusive = _acquire_exclusive(cfg)
        worker = FishS2ServerWorker(cfg=cfg, host="127.0.0.1", port=port)
        t_init0 = time.perf_counter()
        ok_start, det_start = worker.start(timeout_s=180.0)
        init_s = time.perf_counter() - t_init0
        result["init_ok"] = ok_start
        result["init_detail"] = det_start
        result["init_time_s"] = round(init_s, 3)
        result["cold_start_ms_worker"] = round(float(worker.stats.last_cold_start_ms or 0.0), 1)
        result["worker_cmd_model"] = cfg.model_path
        result["model_path_matches_quant"] = model_file.lower() in cfg.model_path.lower()
        if not ok_start:
            result["errors"].append(f"start_failed:{det_start}")
            result["log_tail"] = list(worker._log_lines[-30:])
            return result

        # Confirm voice listing / path for this worker config
        result["voice_loaded_request"] = {
            "voice": cfg.voice_profile,
            "voice_dir": cfg.voice_dir,
            "s2voice": str(voice_path),
        }

        poller.start()
        wav_path = OUT / f"ab_{label}.wav"
        if wav_path.exists():
            wav_path.unlink()

        t_gen0 = time.perf_counter()
        ok_gen, gen_ms, det_gen = worker.generate_to_file(
            TEXT,
            str(wav_path),
            max_tokens=int(PARAMS["max_new_tokens"]),
            timeout_s=180.0,
            voice_profile=cfg.voice_profile,
            voice_dir=cfg.voice_dir,
        )
        gen_wall = time.perf_counter() - t_gen0
        result["generate_ok"] = ok_gen
        result["generate_detail"] = det_gen
        result["generation_time_s"] = round(gen_wall, 3)
        result["generation_ms_api"] = round(float(gen_ms), 1)
        result["voice_profile_used"] = bool(ok_gen and cfg.voice_profile and voice_path.is_file())
        result["log_tail"] = list(worker._log_lines[-40:])

        # Parse warnings from logs
        for line in result["log_tail"]:
            low = line.lower()
            if "error" in low or "fail" in low:
                result["errors"].append(line[:240])
            elif "warn" in low:
                result["warnings"].append(line[:240])

        if ok_gen and wav_path.is_file():
            audio = wav_metrics(wav_path)
            result["audio"] = audio
            result["wav_path"] = str(wav_path)
            result["rtf"] = round(
                gen_wall / max(0.001, float(audio["duration_s"])), 3
            )
        else:
            result["errors"].append(f"generate_failed:{det_gen}")
    finally:
        stop_poll.set()
        try:
            poller.join(timeout=1.0)
        except Exception:
            pass
        result["peak_vram_mb"] = peak_vram
        result["peak_rss_mb"] = round(float(peak_rss or 0.0), 1)
        result["vram_after_mb"] = vram_used_mb()
        if worker is not None:
            try:
                worker.stop(reason=f"ab_{label}_done")
            except Exception as exc:
                result["errors"].append(f"stop_failed:{exc}")
        drop_worker_singleton(stop=True, reason=f"ab_{label}_done")
        try:
            _release_exclusive(cfg)
        except Exception:
            pass
        # Extra sweep for orphans
        try:
            from voice.fish_s2_backend import _kill_s2_orphans

            result["orphans_killed"] = _kill_s2_orphans()
        except Exception:
            result["orphans_killed"] = None
        time.sleep(1.0)
        result["vram_settled_mb"] = vram_used_mb()
        result["exclusive"] = {
            "lease": bool((exclusive or {}).get("lease")),
            "unload": (exclusive or {}).get("unload"),
        }
    return result


def main() -> int:
    os.environ.pop("ELIAN_FISH_FORCE_UNAVAILABLE", None)
    # Avoid gluing onto production singleton port if something is leftover.
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    payload = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "text": TEXT,
        "params": PARAMS,
        "note": "Isolated A/B via temporary FishS2Config overrides; Data/config.json untouched.",
        "variants": {},
    }

    # Distinct ports so a stale process cannot silently serve the wrong model.
    payload["variants"]["q4_k_m"] = run_variant(
        label="q4_k_m",
        model_file="s2-pro-q4_k_m.gguf",
        quant="q4_k_m",
        port=3041,
    )
    payload["variants"]["q8_0"] = run_variant(
        label="q8_0",
        model_file="s2-pro-q8_0.gguf",
        quant="q8_0",
        port=3042,
    )

    raw = OUT / f"q4_vs_q8_ab_{ts}_raw.json"
    raw.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print("WROTE", raw)
    # Stash timestamp for report writer
    (OUT / "_last_ab_ts.txt").write_text(ts, encoding="utf-8")
    return 0 if all(
        v.get("generate_ok") for v in payload["variants"].values()
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
