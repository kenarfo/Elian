# ==========================
# ELIAN — Fish Audio S2 Pro via s2.exe (GGUF / CUDA)
# ==========================
"""Fish TTS backend with optional persistent ``s2.exe --server`` session.

Exclusive GPU handoff (Ollama unload ↔ Fish) is owned by the TTS *session*,
not each utterance. Warm requests reuse one resident model.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_LOCK = threading.RLock()  # serializes Fish jobs
_PROC_LOCK = threading.Lock()  # short lock for active one-shot proc (cancel-safe)
_ACTIVE_PROC: Optional[subprocess.Popen] = None
_CUDA_VERIFIED: Optional[bool] = None
_CUDA_DETAIL = "not_probed"
_LEASE_HOLDER = "fish_s2_tts"

# ---- TTS session (persistent worker) ----
_SESSION_LOCK = threading.RLock()
_SESSION_ACTIVE = False
_SESSION_EXCLUSIVE: Dict[str, Any] = {}
_SESSION_REQUESTS = 0
_SESSION_COLD_MS = 0.0
_IDLE_TIMER: Optional[threading.Timer] = None
_IDLE_GEN = 0


@dataclass
class FishS2Config:
    exe: str
    model_dir: str
    model_file: str
    tokenizer: str
    quant: str
    voice_profile: str
    voice_dir: str
    gpu_policy: str
    gpu_device: int
    dll_dir: str
    cuda_bin: str

    @property
    def model_path(self) -> str:
        if os.path.isabs(self.model_file):
            return self.model_file
        return str(Path(self.model_dir) / self.model_file)

    @property
    def tokenizer_path(self) -> str:
        if os.path.isabs(self.tokenizer):
            return self.tokenizer
        p = Path(self.model_dir) / self.tokenizer
        if p.is_file():
            return str(p)
        return self.tokenizer


def _default_research_root() -> Path:
    return Path(r"C:\Users\kenar\ElianResearch")


def load_fish_s2_config() -> FishS2Config:
    research = _default_research_root()
    exe = str(research / "s2.cpp" / "build" / "Release" / "s2.exe")
    model_dir = str(research / "models" / "s2-pro")
    model_file = "s2-pro-q8_0.gguf"
    tokenizer = "tokenizer.json"
    quant = "q8_0"
    voice_profile = ""
    voice_dir = str(Path(__file__).resolve().parents[1] / "Data" / "voices")
    gpu_policy = "exclusive"
    gpu_device = 0
    dll_dir = str(research / "s2.cpp" / "build" / "bin" / "Release")
    cuda_bin = str(research / "cuda-toolkit-12.8" / "bin")
    try:
        from Config import config, get_config_value

        vc = config.get("voice") if isinstance(config.get("voice"), dict) else {}
        exe = str(
            os.environ.get("ELIAN_FISH_S2_EXE")
            or vc.get("fish_s2_exe")
            or get_config_value("voice", "fish_s2_exe", default=exe)
            or exe
        )
        model_dir = str(
            vc.get("fish_model_dir")
            or get_config_value("voice", "fish_model_dir", default=model_dir)
            or model_dir
        )
        quant = str(
            vc.get("fish_quant")
            or get_config_value("voice", "fish_quant", default=quant)
            or quant
        ).strip().lower()
        model_file = str(
            vc.get("fish_model_file")
            or get_config_value("voice", "fish_model_file", default="")
            or ""
        ).strip()
        if not model_file:
            model_file = f"s2-pro-{quant}.gguf"
        tokenizer = str(
            vc.get("fish_tokenizer")
            or get_config_value("voice", "fish_tokenizer", default=tokenizer)
            or tokenizer
        )
        voice_profile = str(
            vc.get("fish_voice_profile")
            or get_config_value("voice", "fish_voice_profile", default="")
            or ""
        )
        voice_dir = str(
            vc.get("fish_voice_dir")
            or get_config_value("voice", "fish_voice_dir", default=voice_dir)
            or voice_dir
        )
        gpu_policy = str(
            vc.get("fish_gpu_policy")
            or get_config_value("voice", "fish_gpu_policy", default=gpu_policy)
            or gpu_policy
        ).strip().lower()
        gpu_device = int(
            vc.get("fish_gpu_device")
            or get_config_value("voice", "fish_gpu_device", default=gpu_device)
            or gpu_device
        )
        dll_dir = str(vc.get("fish_dll_dir") or dll_dir)
        cuda_bin = str(vc.get("fish_cuda_bin") or cuda_bin)
    except Exception:
        pass
    return FishS2Config(
        exe=exe,
        model_dir=model_dir,
        model_file=model_file,
        tokenizer=tokenizer,
        quant=quant,
        voice_profile=voice_profile,
        voice_dir=voice_dir,
        gpu_policy=gpu_policy,
        gpu_device=gpu_device,
        dll_dir=dll_dir,
        cuda_bin=cuda_bin,
    )


def force_unavailable() -> bool:
    return str(os.environ.get("ELIAN_FISH_FORCE_UNAVAILABLE") or "").strip() in (
        "1",
        "true",
        "yes",
    )


def persistent_worker_enabled() -> bool:
    env = str(os.environ.get("ELIAN_FISH_PERSISTENT") or "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    try:
        from Config import get_config_value

        return bool(get_config_value("voice", "fish_persistent_worker", default=True))
    except Exception:
        return True


def session_idle_s() -> float:
    """How long Fish stays warm after TTS (multi-turn). Default 45s, not 1.5s."""
    try:
        from Config import get_config_value

        return float(
            get_config_value("voice", "fish_session_idle_s", default=45.0) or 45.0
        )
    except Exception:
        return 45.0


def files_ready(cfg: Optional[FishS2Config] = None) -> Tuple[bool, str]:
    cfg = cfg or load_fish_s2_config()
    if force_unavailable():
        return False, "forced_unavailable"
    if not cfg.exe or not (os.path.isfile(cfg.exe) or shutil.which(cfg.exe)):
        return False, "s2_exe_missing"
    if not os.path.isfile(cfg.model_path):
        return False, f"model_missing:{cfg.model_path}"
    if not os.path.isfile(cfg.tokenizer_path):
        return False, f"tokenizer_missing:{cfg.tokenizer_path}"
    if cfg.voice_profile:
        s2voice = Path(cfg.voice_dir) / f"{cfg.voice_profile}.s2voice"
        if not s2voice.is_file():
            return False, f"voice_profile_missing:{s2voice}"
    cuda_dll = Path(cfg.dll_dir) / "ggml-cuda.dll"
    if cfg.dll_dir and not cuda_dll.is_file():
        if not (Path(cfg.exe).parent / "ggml-cuda.dll").is_file():
            return False, "ggml_cuda_dll_missing"
    return True, "files_ok"


def _subprocess_env(cfg: FishS2Config) -> Dict[str, str]:
    env = os.environ.copy()
    parts = [
        str(Path(cfg.exe).parent),
        cfg.dll_dir,
        cfg.cuda_bin,
        env.get("PATH", ""),
    ]
    env["PATH"] = os.pathsep.join(p for p in parts if p)
    if cfg.cuda_bin:
        env["CUDA_PATH"] = str(Path(cfg.cuda_bin).parent)
    env["CUDA_VISIBLE_DEVICES"] = str(max(0, int(cfg.gpu_device)))
    return env


def _acquire_exclusive(cfg: FishS2Config) -> Dict[str, Any]:
    info: Dict[str, Any] = {"policy": cfg.gpu_policy, "unload": None, "lease": False}
    if cfg.gpu_policy != "exclusive":
        return info
    t0 = time.perf_counter()
    try:
        from core.multi_agent.orchestrator import get_orchestrator

        orch = get_orchestrator()
        if orch is not None and getattr(orch, "resources", None) is not None:
            got = orch.resources.acquire(
                _LEASE_HOLDER, kind="tts", priority="CRITICAL"
            )
            info["lease"] = bool(got)
    except Exception:
        pass
    try:
        from core.ollama_lifecycle import unload_model

        # Shorter wait: with keep_alive=0 under exclusive Fish, VRAM frees quickly.
        info["unload"] = unload_model(wait_s=3.0)

    except Exception as exc:
        info["unload"] = {"ok": False, "detail": str(exc)}
    info["lease_wait_ms"] = (time.perf_counter() - t0) * 1000.0
    try:
        from core.latency import record_current_ms

        record_current_ms("gpu_lease_wait_ms", float(info["lease_wait_ms"]))
    except Exception:
        pass
    return info


def _release_exclusive(cfg: FishS2Config) -> None:
    if cfg.gpu_policy != "exclusive":
        return
    try:
        from core.multi_agent.orchestrator import get_orchestrator

        orch = get_orchestrator()
        if orch is not None and getattr(orch, "resources", None) is not None:
            orch.resources.release_all(_LEASE_HOLDER)
    except Exception:
        pass


def _kill_s2_orphans(*, keep_pid: Optional[int] = None) -> int:
    """Best-effort: terminate stray s2.exe left after crashes/cancels."""
    killed = 0
    try:
        import psutil
    except Exception:
        return 0
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            pid = int(p.info.get("pid") or 0)
            if keep_pid and pid == keep_pid:
                continue
            name = (p.info.get("name") or "").lower()
            exe = (p.info.get("exe") or "").lower()
            if name == "s2.exe" or exe.endswith("\\s2.exe"):
                p.kill()
                killed += 1
        except Exception:
            continue
    return killed


def cancel_s2_synthesis() -> None:
    """Cancel active Fish synth (one-shot proc and/or persistent worker)."""
    global _ACTIVE_PROC
    with _PROC_LOCK:
        proc = _ACTIVE_PROC
        _ACTIVE_PROC = None
    if proc is not None:
        try:
            proc.kill()
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
    try:
        from voice.fish_s2_worker import get_worker

        w = get_worker(create=False)
        if w is not None and (w.is_alive() or w.is_ready()):
            w.cancel_active()
    except Exception:
        pass
    # Release GPU so Ollama can return; next Fish request starts a new session.
    end_fish_tts_session(reason="cancel")


def _cancel_idle_timer() -> None:
    global _IDLE_TIMER
    with _SESSION_LOCK:
        t = _IDLE_TIMER
        _IDLE_TIMER = None
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass


def schedule_fish_session_end(*, delay_s: Optional[float] = None) -> None:
    """End session after idle; Ollama path also calls end_fish_tts_session on demand."""
    global _IDLE_TIMER, _IDLE_GEN
    delay = session_idle_s() if delay_s is None else float(delay_s)
    with _SESSION_LOCK:
        if not _SESSION_ACTIVE:
            return
        _IDLE_GEN += 1
        gen = _IDLE_GEN
        if _IDLE_TIMER is not None:
            try:
                _IDLE_TIMER.cancel()
            except Exception:
                pass

        def _fire() -> None:
            with _SESSION_LOCK:
                if gen != _IDLE_GEN or not _SESSION_ACTIVE:
                    return
            end_fish_tts_session(reason="idle")

        _IDLE_TIMER = threading.Timer(max(0.05, delay), _fire)
        _IDLE_TIMER.daemon = True
        _IDLE_TIMER.start()


def begin_fish_tts_session(
    *, cfg: Optional[FishS2Config] = None, timeout_s: float = 120.0
) -> Tuple[bool, str]:
    """Acquire exclusive GPU + start persistent worker (lazy, idempotent)."""
    global _SESSION_ACTIVE, _SESSION_EXCLUSIVE, _SESSION_COLD_MS
    cfg = cfg or load_fish_s2_config()
    ok_files, detail = files_ready(cfg)
    if not ok_files:
        return False, detail
    _cancel_idle_timer()
    with _SESSION_LOCK:
        from voice.fish_s2_worker import drop_worker_singleton, get_worker

        w = get_worker(cfg=cfg, create=True)
        assert w is not None
        if _SESSION_ACTIVE and w.is_ready():
            return True, "session_active"
        # Crash recovery: previous session marked active but worker died.
        if _SESSION_ACTIVE and not w.is_ready():
            try:
                w.stop(reason="crash_recovery")
            except Exception:
                pass
            drop_worker_singleton(stop=False, reason="crash_recovery")
            w = get_worker(cfg=cfg, create=True)
            assert w is not None
        # Fresh exclusive hold for this session.
        if not _SESSION_ACTIVE:
            _SESSION_EXCLUSIVE = _acquire_exclusive(cfg)
        cold_before = float(getattr(getattr(w, "stats", None), "last_cold_start_ms", 0.0) or 0.0)
        ok, det = w.start(timeout_s=timeout_s)
        if not ok:
            _release_exclusive(cfg)
            _SESSION_ACTIVE = False
            _SESSION_EXCLUSIVE = {}
            _kill_s2_orphans()
            drop_worker_singleton(stop=True, reason="start_failed")
            return False, det
        _SESSION_ACTIVE = True
        _SESSION_COLD_MS = float(w.stats.last_cold_start_ms or 0.0)
        # Record cold startup only when this begin() actually started the worker.
        startup_ms = float(_SESSION_COLD_MS or 0.0)
        if startup_ms > 0 and startup_ms != cold_before:
            try:
                from core.latency import record_current_ms

                record_current_ms("fish_worker_startup_ms", startup_ms)
            except Exception:
                pass
        elif startup_ms > 0 and not cold_before:
            try:
                from core.latency import record_current_ms

                record_current_ms("fish_worker_startup_ms", startup_ms)
            except Exception:
                pass
        return True, det


def end_fish_tts_session(*, reason: str = "end") -> Dict[str, Any]:
    """Stop worker, sweep orphans, release TTS/GPU lease."""
    global _SESSION_ACTIVE, _SESSION_EXCLUSIVE, _SESSION_REQUESTS
    _cancel_idle_timer()
    info: Dict[str, Any] = {"reason": reason, "was_active": False, "orphans_killed": 0}
    cfg = load_fish_s2_config()
    with _SESSION_LOCK:
        info["was_active"] = bool(_SESSION_ACTIVE)
        info["requests"] = int(_SESSION_REQUESTS)
        try:
            from voice.fish_s2_worker import drop_worker_singleton, get_worker

            w = get_worker(create=False)
            if w is not None:
                info["worker"] = w.snapshot()
                w.stop(reason=reason)
            drop_worker_singleton(stop=False, reason=reason)
        except Exception as exc:
            info["worker_error"] = str(exc)
        info["orphans_killed"] = _kill_s2_orphans()
        if _SESSION_ACTIVE or _SESSION_EXCLUSIVE:
            _release_exclusive(cfg)
        _SESSION_ACTIVE = False
        _SESSION_EXCLUSIVE = {}
        _SESSION_REQUESTS = 0
    return info


def fish_session_snapshot() -> Dict[str, Any]:
    with _SESSION_LOCK:
        snap = {
            "active": _SESSION_ACTIVE,
            "requests": _SESSION_REQUESTS,
            "cold_start_ms": _SESSION_COLD_MS,
            "persistent_enabled": persistent_worker_enabled(),
            "exclusive": {
                "lease": bool((_SESSION_EXCLUSIVE or {}).get("lease")),
                "unload_ok": bool(((_SESSION_EXCLUSIVE or {}).get("unload") or {}).get("ok")),
            },
        }
    try:
        from voice.fish_s2_worker import get_worker

        w = get_worker(create=False)
        snap["worker"] = w.snapshot() if w is not None else None
    except Exception:
        snap["worker"] = None
    return snap


def _synthesize_oneshot(
    text: str,
    wav_path: str,
    *,
    cfg: FishS2Config,
    max_tokens: int,
    timeout_s: float,
    acquire_gpu: bool,
) -> Tuple[bool, float, str]:
    """Legacy path: spawn s2.exe per request (kept for fallback / benchmarks)."""
    global _ACTIVE_PROC, _CUDA_VERIFIED, _CUDA_DETAIL
    t0 = time.perf_counter()
    exclusive = {}
    if acquire_gpu:
        exclusive = _acquire_exclusive(cfg)

    cmd = [
        cfg.exe,
        "--model",
        cfg.model_path,
        "--tokenizer",
        cfg.tokenizer_path,
        "--text",
        text,
        "--output",
        wav_path,
        "--cuda",
        str(max(0, int(cfg.gpu_device))),
        "--gpu-layers",
        "-1",
        "--max-tokens",
        str(max(16, int(max_tokens))),
        "--log-level",
        "error",
    ]
    if cfg.voice_profile:
        cmd.extend(["--voice", cfg.voice_profile, "--voice-dir", cfg.voice_dir])

    proc = None
    out = ""
    try:
        with _LOCK:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=_subprocess_env(cfg),
                cwd=str(Path(cfg.exe).parent),
                text=True,
            )
            with _PROC_LOCK:
                _ACTIVE_PROC = proc
            try:
                out, _ = proc.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                cancel_s2_synthesis()
                latency = (time.perf_counter() - t0) * 1000.0
                _CUDA_VERIFIED = False
                _CUDA_DETAIL = "timeout"
                return False, latency, "s2_timeout"
            finally:
                with _PROC_LOCK:
                    if _ACTIVE_PROC is proc:
                        _ACTIVE_PROC = None
            latency = (time.perf_counter() - t0) * 1000.0
            code = int(proc.returncode or 0)
            if code != 0 or not os.path.isfile(wav_path) or os.path.getsize(wav_path) < 1000:
                err = (out or "")[-300:].replace("\n", " ")
                _CUDA_VERIFIED = False
                _CUDA_DETAIL = f"s2_failed:{code}:{err}"
                return False, latency, _CUDA_DETAIL
            _CUDA_VERIFIED = True
            _CUDA_DETAIL = "cuda_ok"
            if exclusive:
                return True, latency, f"s2_ok:exclusive:{cfg.quant}"
            return True, latency, f"s2_ok:{cfg.quant}"
    except Exception as exc:
        latency = (time.perf_counter() - t0) * 1000.0
        _CUDA_VERIFIED = False
        _CUDA_DETAIL = f"s2_error:{exc}"
        return False, latency, _CUDA_DETAIL
    finally:
        with _PROC_LOCK:
            if _ACTIVE_PROC is proc:
                _ACTIVE_PROC = None
        if acquire_gpu:
            _release_exclusive(cfg)


def _synthesize_persistent(
    text: str,
    wav_path: str,
    *,
    cfg: FishS2Config,
    max_tokens: int,
    timeout_s: float,
) -> Tuple[bool, float, str]:
    global _CUDA_VERIFIED, _CUDA_DETAIL, _SESSION_REQUESTS
    t0 = time.perf_counter()
    ok_s, det_s = begin_fish_tts_session(cfg=cfg, timeout_s=min(120.0, timeout_s))
    if not ok_s:
        return False, (time.perf_counter() - t0) * 1000.0, f"session_failed:{det_s}"

    from voice.fish_s2_worker import get_worker

    w = get_worker(create=False)
    if w is None or not w.is_ready():
        end_fish_tts_session(reason="worker_missing")
        return False, (time.perf_counter() - t0) * 1000.0, "worker_missing"

    # Serialize generates (server accepts one active /generate).
    with _LOCK:
        ok, gen_ms, detail = w.generate_to_file(
            text,
            wav_path,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            voice_profile=cfg.voice_profile,
            voice_dir=cfg.voice_dir,
        )
    latency = (time.perf_counter() - t0) * 1000.0
    try:
        from core.latency import record_current_ms

        if gen_ms is not None:
            record_current_ms("fish_synthesis_ms", float(gen_ms))
    except Exception:
        pass
    if not ok:
        _CUDA_VERIFIED = False
        _CUDA_DETAIL = detail
        if detail in ("cancelled",) or str(detail).startswith("worker_crash"):
            end_fish_tts_session(reason=detail)
        else:
            # Recoverable HTTP failure: kill worker so next call cold-starts cleanly.
            end_fish_tts_session(reason=f"generate_fail:{detail}")
        return False, latency, detail

    with _SESSION_LOCK:
        _SESSION_REQUESTS += 1
        req_n = _SESSION_REQUESTS
    _CUDA_VERIFIED = True
    _CUDA_DETAIL = f"worker_ok:{cfg.quant}:req={req_n}"
    schedule_fish_session_end()
    # Include cold start only on first request wall time already in latency.
    return True, latency, _CUDA_DETAIL


def synthesize_s2_wav(
    text: str,
    wav_path: str,
    *,
    cfg: Optional[FishS2Config] = None,
    max_tokens: int = 256,
    timeout_s: float = 180.0,
    acquire_gpu: bool = True,
) -> Tuple[bool, float, str]:
    """Synthesize text → WAV (persistent worker session by default)."""
    cfg = cfg or load_fish_s2_config()
    ok_files, detail = files_ready(cfg)
    if not ok_files:
        return False, 0.0, detail
    text = str(text or "").strip()
    if not text:
        return False, 0.0, "empty_text"

    if persistent_worker_enabled():
        # Session owns exclusive GPU for its lifetime.
        return _synthesize_persistent(
            text, wav_path, cfg=cfg, max_tokens=max_tokens, timeout_s=timeout_s
        )

    # Legacy one-shot path (benchmark / ELIAN_FISH_PERSISTENT=0).
    return _synthesize_oneshot(
        text,
        wav_path,
        cfg=cfg,
        max_tokens=max_tokens,
        timeout_s=timeout_s,
        acquire_gpu=acquire_gpu,
    )


def probe_cuda_once(*, force: bool = False) -> Tuple[bool, str]:
    """One-time short CUDA synth; cached for health_check READY."""
    global _CUDA_VERIFIED, _CUDA_DETAIL
    if force_unavailable():
        return False, "forced_unavailable"
    if _CUDA_VERIFIED is not None and not force:
        return bool(_CUDA_VERIFIED), _CUDA_DETAIL
    cfg = load_fish_s2_config()
    ok_files, detail = files_ready(cfg)
    if not ok_files:
        _CUDA_VERIFIED = False
        _CUDA_DETAIL = detail
        return False, detail
    fd, path = tempfile.mkstemp(prefix="elian_fish_probe_", suffix=".wav")
    os.close(fd)
    try:
        ok, _lat, det = synthesize_s2_wav(
            "Elian online.",
            path,
            cfg=cfg,
            max_tokens=64,
            timeout_s=120.0,
            acquire_gpu=True,
        )
        return ok, det
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
        # Probe should not pin the GPU.
        end_fish_tts_session(reason="probe_done")


def health_snapshot() -> Dict[str, Any]:
    cfg = load_fish_s2_config()
    ok_files, file_detail = files_ready(cfg)
    verified = _CUDA_VERIFIED
    detail = file_detail if not ok_files else (_CUDA_DETAIL or file_detail)
    ready = bool(ok_files and verified is True)
    if ok_files and verified is None:
        detail = "files_ok_cuda_unverified"
    sess = fish_session_snapshot()
    return {
        "ready": ready,
        "detail": detail,
        "exe": cfg.exe,
        "model_path": cfg.model_path,
        "tokenizer_path": cfg.tokenizer_path,
        "quant": cfg.quant,
        "gpu_policy": cfg.gpu_policy,
        "gpu_device": cfg.gpu_device,
        "voice_profile": cfg.voice_profile or "",
        "voice_dir": cfg.voice_dir or "",
        "s2voice_path": str(Path(cfg.voice_dir) / f"{cfg.voice_profile}.s2voice")
        if cfg.voice_profile
        else "",
        "cuda_verified": verified,
        "forced_unavailable": force_unavailable(),
        "persistent_worker": persistent_worker_enabled(),
        "session": sess,
    }


def reset_fish_health_for_tests() -> None:
    global _CUDA_VERIFIED, _CUDA_DETAIL
    end_fish_tts_session(reason="test_reset")
    _CUDA_VERIFIED = None
    _CUDA_DETAIL = "not_probed"
    try:
        from voice.fish_s2_worker import reset_worker_for_tests

        reset_worker_for_tests()
    except Exception:
        pass
