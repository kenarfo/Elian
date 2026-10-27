# ==========================
# ELIAN — Persistent Fish S2 HTTP worker (s2.exe --server)
# ==========================
"""Lifecycle for a resident s2.cpp HTTP server.

Lazy start → POST /generate (warm) → clean shutdown. Never leaves orphan s2.exe.
Does not own the GPU lease; callers (fish_s2_backend session) acquire/release.
"""
from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.35):
            return True
    except OSError:
        return False


def find_free_port(host: str = "127.0.0.1", start: int = 3037, span: int = 32) -> int:
    for port in range(start, start + max(1, span)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, port))
            except OSError:
                continue
            return port
    return start


@dataclass
class WorkerStats:
    starts: int = 0
    stops: int = 0
    generates: int = 0
    cancels: int = 0
    crashes: int = 0
    last_cold_start_ms: float = 0.0
    last_generate_ms: float = 0.0
    last_detail: str = ""


@dataclass
class FishS2ServerWorker:
    """One persistent ``s2.exe --server`` process."""

    cfg: Any
    host: str = "127.0.0.1"
    port: int = 3037
    _proc: Optional[subprocess.Popen] = field(default=None, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _log_thread: Optional[threading.Thread] = field(default=None, init=False, repr=False)
    _log_lines: List[str] = field(default_factory=list, init=False, repr=False)
    _ready: bool = field(default=False, init=False)
    _cancel: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _active_request: bool = field(default=False, init=False)
    stats: WorkerStats = field(default_factory=WorkerStats, init=False)

    @property
    def pid(self) -> Optional[int]:
        proc = self._proc
        if proc is None:
            return None
        return int(proc.pid) if proc.poll() is None else None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{int(self.port)}"

    def is_alive(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def is_ready(self) -> bool:
        return bool(self._ready and self.is_alive() and _port_open(self.host, self.port))

    def _env(self) -> Dict[str, str]:
        from voice.fish_s2_backend import _subprocess_env

        return _subprocess_env(self.cfg)

    def _drain_logs(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for raw in proc.stdout:
                line = (raw or "").rstrip("\n")
                if not line:
                    continue
                self._log_lines.append(line)
                if len(self._log_lines) > 400:
                    self._log_lines = self._log_lines[-200:]
        except Exception:
            pass

    def start(self, *, timeout_s: float = 120.0) -> Tuple[bool, str]:
        with self._lock:
            if self.is_ready():
                return True, "already_ready"
            self.stop_locked(reason="restart")
            if not Path(self.cfg.exe).is_file():
                return False, "s2_exe_missing"
            # Orphan s2.exe often still holds :3037. Bumping the port without a
            # sweep stacks GPU workers (seen: 5× s2.exe → hung TTS). Kill first.
            try:
                from voice.fish_s2_backend import _kill_s2_orphans

                _kill_s2_orphans()
            except Exception:
                pass
            # Keep the port this worker was constructed with (config or test bind).
            self.port = int(self.port or 3037)
            port_free = False
            for _ in range(30):
                if not _port_open(self.host, self.port):
                    port_free = True
                    break
                time.sleep(0.05)
            if not port_free:
                new_port = find_free_port(
                    self.host, start=max(3037, int(self.port) + 1)
                )
                print(
                    f"[FISH] port {self.port} still busy after orphan sweep — "
                    f"using {new_port} (should be rare)"
                )
                self.port = new_port

            cmd = [
                self.cfg.exe,
                "--model",
                self.cfg.model_path,
                "--tokenizer",
                self.cfg.tokenizer_path,
                "--cuda",
                str(max(0, int(self.cfg.gpu_device))),
                "--gpu-layers",
                "-1",
                "--log-level",
                "error",
                "--server",
                "--host",
                self.host,
                "--port",
                str(int(self.port)),
            ]
            if self.cfg.voice_dir:
                cmd.extend(["--voice-dir", self.cfg.voice_dir])

            t0 = time.perf_counter()
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=self._env(),
                    cwd=str(Path(self.cfg.exe).parent),
                    text=True,
                )
            except Exception as exc:
                self._proc = None
                self.stats.last_detail = f"spawn_failed:{exc}"
                return False, self.stats.last_detail

            self._log_thread = threading.Thread(
                target=self._drain_logs, name="elian-fish-s2-log", daemon=True
            )
            self._log_thread.start()
            self.stats.starts += 1

            deadline = time.time() + max(5.0, float(timeout_s))
            while time.time() < deadline:
                if self._proc.poll() is not None:
                    self.stats.crashes += 1
                    self.stats.last_detail = "worker_exited_during_boot"
                    self.stop_locked(reason="boot_exit")
                    return False, self.stats.last_detail
                if _port_open(self.host, self.port):
                    self._ready = True
                    self.stats.last_cold_start_ms = (time.perf_counter() - t0) * 1000.0
                    self.stats.last_detail = "ready"
                    return True, "ready"
                time.sleep(0.1)

            self.stats.last_detail = "ready_timeout"
            self.stop_locked(reason="ready_timeout")
            return False, "ready_timeout"

    def stop(self, *, reason: str = "stop") -> None:
        with self._lock:
            self.stop_locked(reason=reason)

    def stop_locked(self, *, reason: str = "stop") -> None:
        self._ready = False
        self._cancel.set()
        proc = self._proc
        self._proc = None
        if proc is not None:
            self.stats.stops += 1
            self.stats.last_detail = reason
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        try:
                            proc.wait(timeout=2.0)
                        except Exception:
                            pass
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        # Always refresh cancel event so the next generate is not born cancelled.
        self._cancel = threading.Event()
        self._active_request = False

    def cancel_active(self) -> None:
        """Cancel in-flight generate by killing the worker (safe for s2 busy lock)."""
        self.stats.cancels += 1
        self._cancel.set()
        self.stop(reason="cancel")

    def generate_to_file(
        self,
        text: str,
        wav_path: str,
        *,
        max_tokens: int = 256,
        timeout_s: float = 180.0,
        voice_profile: str = "",
        voice_dir: str = "",
    ) -> Tuple[bool, float, str]:
        """POST /generate → write WAV. Returns ok, latency_ms, detail."""
        import urllib.request

        t0 = time.perf_counter()
        text = str(text or "").strip()
        if not text:
            return False, 0.0, "empty_text"
        if not self.is_ready():
            return False, 0.0, "worker_not_ready"
        if self._cancel.is_set():
            return False, 0.0, "cancelled"

        voice_profile = voice_profile or self.cfg.voice_profile
        voice_dir = voice_dir or self.cfg.voice_dir
        boundary = f"----ElianFish{int(time.time() * 1000)}"
        params = json.dumps(
            {
                "max_new_tokens": int(max(16, max_tokens)),
                "temperature": 0.8,
                "top_p": 0.8,
                "top_k": 30,
            }
        )

        def _part(name: str, value: str) -> bytes:
            return (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")

        body = b"".join(
            [
                _part("text", text),
                _part("voice", voice_profile) if voice_profile else b"",
                _part("voice_dir", voice_dir) if voice_dir else b"",
                _part("params", params),
                f"--{boundary}--\r\n".encode("utf-8"),
            ]
        )
        req = urllib.request.Request(
            f"{self.base_url}/generate",
            data=body,
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
        )

        self._active_request = True
        self._cancel.clear()
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                chunks: List[bytes] = []
                while True:
                    if self._cancel.is_set() or not self.is_alive():
                        try:
                            resp.close()
                        except Exception:
                            pass
                        latency = (time.perf_counter() - t0) * 1000.0
                        return False, latency, "cancelled"
                    block = resp.read(65536)
                    if not block:
                        break
                    chunks.append(block)
                data = b"".join(chunks)
            latency = (time.perf_counter() - t0) * 1000.0
            if len(data) < 1000:
                self.stats.last_detail = f"short_audio:{len(data)}"
                return False, latency, self.stats.last_detail
            Path(wav_path).write_bytes(data)
            self.stats.generates += 1
            self.stats.last_generate_ms = latency
            self.stats.last_detail = "generate_ok"
            return True, latency, "generate_ok"
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000.0
            if self._cancel.is_set():
                return False, latency, "cancelled"
            if not self.is_alive():
                self.stats.crashes += 1
                self._ready = False
                return False, latency, f"worker_crash:{exc}"
            self.stats.last_detail = f"generate_failed:{exc}"
            return False, latency, self.stats.last_detail
        finally:
            self._active_request = False

    def snapshot(self) -> Dict[str, Any]:
        return {
            "alive": self.is_alive(),
            "ready": self.is_ready(),
            "pid": self.pid,
            "host": self.host,
            "port": self.port,
            "base_url": self.base_url,
            "active_request": self._active_request,
            "stats": {
                "starts": self.stats.starts,
                "stops": self.stats.stops,
                "generates": self.stats.generates,
                "cancels": self.stats.cancels,
                "crashes": self.stats.crashes,
                "last_cold_start_ms": round(self.stats.last_cold_start_ms, 1),
                "last_generate_ms": round(self.stats.last_generate_ms, 1),
                "last_detail": self.stats.last_detail,
            },
            "log_tail": self._log_lines[-20:],
        }


_WORKER: Optional[FishS2ServerWorker] = None
_WORKER_LOCK = threading.RLock()


def get_worker(*, cfg: Any = None, create: bool = True) -> Optional[FishS2ServerWorker]:
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is not None:
            return _WORKER
        if not create:
            return None
        if cfg is None:
            from voice.fish_s2_backend import load_fish_s2_config

            cfg = load_fish_s2_config()
        host = "127.0.0.1"
        port = 3037
        try:
            from Config import get_config_value

            host = str(
                get_config_value("voice", "fish_server_host", default=host) or host
            )
            port = int(
                get_config_value("voice", "fish_server_port", default=port) or port
            )
        except Exception:
            pass
        _WORKER = FishS2ServerWorker(cfg=cfg, host=host, port=port)
        return _WORKER


def drop_worker_singleton(*, stop: bool = True, reason: str = "drop") -> None:
    """Forget the process-wide worker handle (optionally stop first)."""
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is not None and stop:
            try:
                _WORKER.stop(reason=reason)
            except Exception:
                pass
        _WORKER = None


def reset_worker_for_tests() -> None:
    drop_worker_singleton(stop=True, reason="test_reset")
