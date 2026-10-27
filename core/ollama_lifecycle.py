# ==========================
# ELIAN — Ollama GPU lifecycle (unload for exclusive TTS)
# ==========================
from __future__ import annotations

import subprocess
import time
from typing import Any, Dict, Optional


def _model_name() -> str:
    try:
        from Config import get_config_value

        return str(
            get_config_value("ai", "ollama_model", default="llama3.1:8b") or "llama3.1:8b"
        )
    except Exception:
        return "llama3.1:8b"


def fish_exclusive_tts_enabled() -> bool:
    """True when Fish owns the GPU exclusively for TTS (Jarvis local path)."""
    try:
        from Config import get_config_value

        prefer = bool(get_config_value("voice", "prefer_fish_tts", default=True))
        policy = str(
            get_config_value("voice", "fish_gpu_policy", default="exclusive") or ""
        ).lower()
        return prefer and policy == "exclusive"
    except Exception:
        return True


def ollama_keep_alive() -> Any:
    """keep_alive for chat: short/zero under exclusive Fish so TTS handoff is cheap."""
    try:
        from Config import get_config_value

        override = get_config_value("ai", "ollama_keep_alive", default=None)
        if override is not None and str(override).strip() != "":
            return override
    except Exception:
        pass
    if fish_exclusive_tts_enabled():
        # Free VRAM right after Planner so Fish acquire does not wait on a 30m resident.
        return 0
    return "30m"


def ensure_gpu_for_ollama(*, reason: str = "ollama") -> Dict[str, Any]:
    """End Fish TTS session before Ollama loads (exclusive GPU handoff)."""
    info: Dict[str, Any] = {"ended_fish": False, "reason": reason}
    if not fish_exclusive_tts_enabled():
        return info
    try:
        from voice.fish_s2_backend import end_fish_tts_session, fish_session_snapshot

        snap = fish_session_snapshot()
        if snap.get("active"):
            end_fish_tts_session(reason=reason)
            info["ended_fish"] = True
    except Exception as exc:
        info["error"] = str(exc)
    return info


def vram_used_mb() -> Optional[float]:
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


def unload_model(*, model: Optional[str] = None, wait_s: float = 8.0) -> Dict[str, Any]:
    """
    Ask Ollama to drop the model from VRAM (keep_alive=0).
    Does not start a long generation — empty/minimal prompt with keep_alive=0.
    """
    name = model or _model_name()
    before = vram_used_mb()
    detail = "ok"
    ok = False
    try:
        import ollama

        client = ollama.Client(timeout=15)
        # keep_alive=0 unloads immediately after the call returns.
        client.chat(
            model=name,
            messages=[{"role": "user", "content": "."}],
            options={"num_predict": 1},
            keep_alive=0,
        )
        ok = True
    except Exception as exc:
        detail = f"chat_unload_failed:{exc}"
        try:
            import urllib.request
            import json

            payload = json.dumps(
                {
                    "model": name,
                    "keep_alive": 0,
                    "prompt": "",
                    "stream": False,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                "http://127.0.0.1:11434/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
            ok = True
            detail = "generate_unload_ok"
        except Exception as exc2:
            detail = f"{detail}; generate_unload_failed:{exc2}"

    deadline = time.time() + max(0.5, float(wait_s))
    after = vram_used_mb()
    while time.time() < deadline:
        after = vram_used_mb()
        if before is None or after is None:
            break
        # Best-effort: wait for a noticeable drop or stabilize under before.
        if after + 256 < before:
            break
        time.sleep(0.25)
        after = vram_used_mb()

    return {
        "ok": ok,
        "model": name,
        "detail": detail,
        "vram_before_mb": before,
        "vram_after_mb": after,
    }
