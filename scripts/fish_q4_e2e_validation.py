"""End-to-end Fish Q4 + local elian_jarvis voice validation — no architecture changes."""
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

# Production path for this validation (Fish enabled).
os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "0"

DIAG = ROOT / "Data" / "diagnostics"
DIAG.mkdir(parents=True, exist_ok=True)
REPORT_PATH = DIAG / "fish_q4_e2e_validation.json"
CLOUD_ID = "7c1a7dc37829497593ab4db29eed387c"


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


def s2_running() -> list[int]:
    pids = []
    for p in __import__("psutil").process_iter(["pid", "name", "exe"]):
        try:
            name = (p.info.get("name") or "").lower()
            exe = (p.info.get("exe") or "").lower()
            if name == "s2.exe" or exe.endswith("\\s2.exe"):
                pids.append(int(p.info["pid"]))
        except Exception:
            continue
    return pids


def main() -> int:
    import ollama
    import psutil  # noqa: F401 — used via s2_running

    from Config import get_config_value
    from core.ollama_lifecycle import unload_model
    from voice.fish_s2_backend import (
        cancel_s2_synthesis,
        health_snapshot,
        load_fish_s2_config,
        reset_fish_health_for_tests,
        synthesize_s2_wav,
    )
    from voice.tts_engine import (
        FishAudioTTS,
        RoutedTTS,
        reset_tts_engine_for_tests,
    )

    cfg = load_fish_s2_config()
    report = {
        "quant_config": {
            "fish_quant": get_config_value("voice", "fish_quant", default=""),
            "fish_model_file": get_config_value("voice", "fish_model_file", default=""),
            "fish_voice_profile": get_config_value("voice", "fish_voice_profile", default=""),
            "fish_voice_dir": get_config_value("voice", "fish_voice_dir", default=""),
            "fish_voice_id": get_config_value("voice", "fish_voice_id", default=""),
            "resolved_model": cfg.model_path,
            "resolved_voice_profile": cfg.voice_profile,
            "resolved_voice_dir": cfg.voice_dir,
            "s2voice_path": str(Path(cfg.voice_dir) / f"{cfg.voice_profile}.s2voice")
            if cfg.voice_profile
            else "",
        },
        "steps": [],
        "metrics": {},
    }

    s2voice = Path(cfg.voice_dir) / f"{cfg.voice_profile}.s2voice"
    cfg_ok = (
        str(report["quant_config"]["fish_quant"]).lower() == "q4_k_m"
        and "q4_k_m" in str(report["quant_config"]["fish_model_file"]).lower()
        and Path(report["quant_config"]["resolved_model"]).is_file()
        and cfg.voice_profile == "elian_jarvis"
        and str(get_config_value("voice", "fish_voice_profile", default="")) == "elian_jarvis"
        and s2voice.is_file()
        and not str(get_config_value("voice", "fish_voice_id", default="") or "").strip()
        and CLOUD_ID not in cfg.voice_profile
        and CLOUD_ID not in cfg.voice_dir
    )
    report["steps"].append(
        {
            "name": "config_q4_jarvis",
            "ok": cfg_ok,
            **report["quant_config"],
            "s2voice_bytes": s2voice.stat().st_size if s2voice.is_file() else 0,
        }
    )

    reset_tts_engine_for_tests()
    reset_fish_health_for_tests()

    # ---- Capture real s2.exe argv from Elian pipeline (FishAudioTTS) ----
    captured_cmds: list[list[str]] = []
    import voice.fish_s2_backend as fish_backend

    _orig_popen = subprocess.Popen

    def _capturing_popen(cmd, *a, **k):
        try:
            if cmd and str(cmd[0]).lower().endswith("s2.exe"):
                captured_cmds.append(list(cmd))
        except Exception:
            pass
        return _orig_popen(cmd, *a, **k)

    # ---- Ollama → unload → Fish Q4 + jarvis → VRAM back → Ollama again ----
    samples = []
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            samples.append({"t": time.time(), "vram": vram_mb(), "s2": s2_running()})
            time.sleep(0.35)

    baseline = vram_mb()
    client = ollama.Client(timeout=120)
    model = str(get_config_value("ai", "ollama_model", default="llama3.1:8b") or "llama3.1:8b")
    t_llm0 = time.perf_counter()
    ans = client.chat(
        model=model,
        messages=[{"role": "user", "content": "Reply with exactly: Elian is online."}],
        options={"num_predict": 24},
        keep_alive="30m",
    )
    llm_text = (ans.get("message") or {}).get("content") or ""
    llm_s = time.perf_counter() - t_llm0
    after_llm = vram_mb()

    th = threading.Thread(target=poll, daemon=True)
    th.start()
    unload1 = unload_model(model=model, wait_s=10.0)
    after_unload = vram_mb()

    wav = DIAG / "e2e_q4_jarvis_reply.wav"
    if wav.exists():
        wav.unlink()
    speak_text = (llm_text.strip() or "Elian is online.")[:160]

    # Cold-start synth through real Elian FishAudioTTS path (captures argv).
    subprocess.Popen = _capturing_popen  # type: ignore[assignment]
    t_fish0 = time.perf_counter()
    fish_ok = FishAudioTTS().speak(speak_text, cooldown=0.0)
    fish_s = time.perf_counter() - t_fish0
    cold_start_s = fish_s
    ok_file, lat_file = FishAudioTTS().synthesize_to_file(
        "Second Q4 Jarvis line for VRAM release check.", str(wav)
    )
    subprocess.Popen = _orig_popen  # type: ignore[assignment]
    time.sleep(1.2)
    stop.set()
    th.join(timeout=2)
    peak = max((s["vram"] or 0) for s in samples) if samples else None
    after_fish = vram_mb()
    orphan_s2 = s2_running()

    # Confirm CLI args used local profile only.
    voice_cli_ok = False
    cmd_summary = []
    for cmd in captured_cmds:
        joined = " ".join(cmd)
        cmd_summary.append(joined[-400:])
        if (
            "--voice" in cmd
            and cmd[cmd.index("--voice") + 1] == "elian_jarvis"
            and "--voice-dir" in cmd
            and Path(cmd[cmd.index("--voice-dir") + 1]) == Path(cfg.voice_dir)
            and CLOUD_ID not in joined
        ):
            voice_cli_ok = True
            break

    # Optional: one info-level synth to confirm s2 loads the profile name.
    profile_log_hit = False
    profile_log_snip = ""
    try:
        info_wav = DIAG / "e2e_q4_jarvis_profile_probe.wav"
        if info_wav.exists():
            info_wav.unlink()
        info_cmd = [
            cfg.exe,
            "--model",
            cfg.model_path,
            "--tokenizer",
            cfg.tokenizer_path,
            "--text",
            "Jarvis profile probe.",
            "--output",
            str(info_wav),
            "--cuda",
            str(max(0, int(cfg.gpu_device))),
            "--gpu-layers",
            "-1",
            "--max-tokens",
            "64",
            "--log-level",
            "info",
            "--voice",
            cfg.voice_profile,
            "--voice-dir",
            cfg.voice_dir,
        ]
        unload_model(model=model, wait_s=6.0)
        env = fish_backend._subprocess_env(cfg)
        t_probe0 = time.perf_counter()
        proc = subprocess.run(
            info_cmd,
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
            cwd=str(Path(cfg.exe).parent),
        )
        probe_s = time.perf_counter() - t_probe0
        profile_log_snip = ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-800:]
        low = profile_log_snip.lower()
        profile_log_hit = (
            "elian_jarvis" in low
            or "loading voice" in low
            or (proc.returncode == 0 and info_wav.is_file() and info_wav.stat().st_size > 1000)
        )
        report["metrics"]["profile_probe_s"] = round(probe_s, 2)
        report["metrics"]["profile_probe_rc"] = proc.returncode
    except Exception as exc:
        profile_log_snip = f"probe_error:{exc}"

    snap = health_snapshot()
    report["steps"].append(
        {
            "name": "voice_profile_cli",
            "ok": bool(voice_cli_ok and profile_log_hit and cfg.voice_profile == "elian_jarvis"),
            "voice_cli_ok": voice_cli_ok,
            "profile_log_hit": profile_log_hit,
            "captured_cmds": len(captured_cmds),
            "cmd_tails": cmd_summary[:3],
            "profile_log_snip": profile_log_snip[-400:],
            "health_voice_profile": snap.get("voice_profile"),
            "health_voice_dir": snap.get("voice_dir"),
            "health_s2voice_path": snap.get("s2voice_path"),
        }
    )

    # Reload Ollama again
    t_llm1 = time.perf_counter()
    ans2 = client.chat(
        model=model,
        messages=[{"role": "user", "content": "Say OK in one word."}],
        options={"num_predict": 8},
        keep_alive="30m",
    )
    llm2_s = time.perf_counter() - t_llm1
    after_reload = vram_mb()
    unload2 = unload_model(model=model, wait_s=8.0)
    after_final = vram_mb()

    e2e_ok = bool(
        fish_ok
        and unload1.get("ok")
        and after_unload is not None
        and after_llm is not None
        and after_unload + 400 < after_llm
        and peak
        and peak > after_unload + 1000
        and after_fish is not None
        and after_fish < peak - 1500
        and after_reload is not None
        and after_reload > after_fish + 500
        and not orphan_s2
        and ok_file
        and voice_cli_ok
    )
    report["steps"].append(
        {
            "name": "e2e_ollama_fish_ollama",
            "ok": e2e_ok,
            "llm_reply": llm_text[:200],
            "speak_text": speak_text,
            "llm_s": round(llm_s, 2),
            "fish_speak_s": round(fish_s, 2),
            "cold_start_s": round(cold_start_s, 2),
            "second_synth_ms": round(lat_file, 1),
            "llm_reload_s": round(llm2_s, 2),
            "llm_reload_reply": ((ans2.get("message") or {}).get("content") or "")[:80],
            "baseline_mb": baseline,
            "after_llm_mb": after_llm,
            "after_unload_mb": after_unload,
            "peak_during_fish_mb": peak,
            "after_fish_mb": after_fish,
            "after_ollama_reload_mb": after_reload,
            "after_final_unload_mb": after_final,
            "vram_returns_near_baseline": bool(
                after_final is not None
                and baseline is not None
                and abs(after_final - baseline) < 800
            ),
            "ollama_released": bool(
                after_llm and after_unload and after_unload + 400 < after_llm
            ),
            "ollama_reloaded": bool(
                after_reload and after_fish and after_reload > after_fish + 500
            ),
            "orphan_s2_pids": orphan_s2,
            "unload1": unload1,
            "unload2": unload2,
            "wav_bytes": wav.stat().st_size if wav.is_file() else 0,
        }
    )
    report["metrics"].update(
        {
            "model_quant": cfg.quant,
            "voice_profile": cfg.voice_profile,
            "peak_vram_mb": peak,
            "cold_start_s": round(cold_start_s, 2),
            "synth_ms": round(lat_file, 1),
            "baseline_mb": baseline,
            "after_fish_mb": after_fish,
            "after_final_mb": after_final,
        }
    )

    # ---- Cancel during Fish synth ----
    reset_fish_health_for_tests()
    cancel_wav = DIAG / "e2e_q4_jarvis_cancel.wav"
    if cancel_wav.exists():
        cancel_wav.unlink()
    seen_pid = {"pid": None}
    cancel_samples = []

    def cancel_poll():
        while not stop_c.is_set():
            pids = s2_running()
            cancel_samples.append({"t": time.time(), "pids": pids, "vram": vram_mb()})
            if pids and seen_pid["pid"] is None:
                seen_pid["pid"] = pids[0]
            time.sleep(0.2)

    stop_c = threading.Event()
    thc = threading.Thread(target=cancel_poll, daemon=True)
    thc.start()

    def cancel_soon():
        deadline = time.time() + 20
        while time.time() < deadline:
            if s2_running():
                time.sleep(0.8)
                cancel_s2_synthesis()
                return
            time.sleep(0.1)
        cancel_s2_synthesis()

    threading.Thread(target=cancel_soon, daemon=True).start()
    t_c0 = time.perf_counter()
    ok_c, lat_c, det_c = synthesize_s2_wav(
        "This long sentence must be cancelled before playback finishes generating lots of audio tokens for Elian validation.",
        str(cancel_wav),
        max_tokens=512,
        timeout_s=90,
        acquire_gpu=True,
    )
    cancel_elapsed = time.perf_counter() - t_c0
    time.sleep(1.0)
    stop_c.set()
    thc.join(timeout=2)
    after_cancel_pids = s2_running()
    wav_size = cancel_wav.stat().st_size if cancel_wav.is_file() else 0
    cancel_ok = (not after_cancel_pids) and (
        (not ok_c) or wav_size < 50000 or "failed" in det_c or "timeout" in det_c
    )
    if ok_c and wav_size > 100000 and not after_cancel_pids:
        cancel_ok = False
    report["steps"].append(
        {
            "name": "cancel_during_fish",
            "ok": cancel_ok,
            "synth_ok": ok_c,
            "detail": det_c,
            "elapsed_s": round(cancel_elapsed, 2),
            "latency_ms": round(lat_c, 1),
            "wav_bytes": wav_size,
            "seen_s2_pid": seen_pid["pid"],
            "orphan_s2_after": after_cancel_pids,
            "playback_after_cancel": bool(ok_c and wav_size > 1000),
            "note": "ok=True means interrupt prevented completed usable output and left no s2.exe",
        }
    )

    # ---- Piper fallback ----
    os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "1"
    reset_tts_engine_for_tests()
    reset_fish_health_for_tests()
    routed = RoutedTTS(prefer_fish=True)
    st = routed.health_check()
    fd, path = __import__("tempfile").mkstemp(suffix=".wav")
    os.close(fd)
    try:
        t_p0 = time.perf_counter()
        ok_p, lat_p = routed.synthesize_to_file("Piper fallback still works.", path)
        piper_s = time.perf_counter() - t_p0
        piper_ok = bool(
            ok_p
            and st.extras.get("fish_readiness") == "UNAVAILABLE"
            and os.path.getsize(path) > 1000
        )
        report["steps"].append(
            {
                "name": "piper_fallback",
                "ok": piper_ok,
                "fish_readiness": st.extras.get("fish_readiness"),
                "active": st.extras.get("active"),
                "latency_ms": round(lat_p, 1),
                "elapsed_s": round(piper_s, 2),
                "bytes": os.path.getsize(path) if ok_p else 0,
            }
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    report["pass"] = all(bool(s.get("ok")) for s in report["steps"])
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("OVERALL", "PASS" if report["pass"] else "FAIL", "->", REPORT_PATH)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
