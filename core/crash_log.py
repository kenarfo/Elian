# ==========================
# ELIAN — runtime crash / exit diagnostics (Phase 10 + 14)
# ==========================
"""Persist rich runtime snapshots. Never hide serious failures.

Serious exceptions: (1) log, (2) classify, (3) do not silently kill process,
(4) drive DEGRADED/ERROR recovery when possible.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Optional

from Config import DATA_DIR, get_config_value

# Process-wide last-known context (heartbeat / hard-kill breadcrumb).
_RUNTIME_CTX: dict[str, Any] = {
    "session_id": "",
    "voice_state": "",
    "cm_active": None,
    "active_tool": "",
    "tts_active": False,
    "stt_active": False,
    "barge_in": False,
    "gpu_op": "",
    "last_semantic": {},
    "last_planner_step": {},
    "last_exception": "",
    "exit_reason": "",
    "degraded_subsystems": [],
}
_CTX_LOCK = threading.RLock()
_HOOKS_INSTALLED = False
# Routine barge stop_speaking() used to write one JSON per call (thousands of
# files under Data/diagnostics during smoke/RC). Heartbeat always updates;
# disk persist for barge* is rate-limited.
_PERSIST_LOCK = threading.Lock()
_LAST_RATE_LIMITED_PERSIST_MONO: dict[str, float] = {}
_RATE_LIMITED_SNAPSHOT_PREFIXES = ("barge", "before_barge", "recover_")


def _diagnostics_dir() -> Path:
    root = Path(DATA_DIR) / "diagnostics"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _store_transcripts() -> bool:
    return bool(get_config_value("diagnostics", "store_transcripts", default=False))


def _recent_trace_events(limit: int = 50) -> list:
    try:
        from core.trace import get_action_trace

        events = get_action_trace().snapshot()
        return events[-limit:]
    except Exception:
        return []


def update_runtime_context(**kwargs: Any) -> None:
    """Merge known runtime fields for heartbeat / crash snapshots."""
    with _CTX_LOCK:
        for key, value in kwargs.items():
            if key in _RUNTIME_CTX or key.startswith("extra_"):
                _RUNTIME_CTX[key] = value


def note_runtime_event(kind: str, *, detail: str = "") -> None:
    with _CTX_LOCK:
        _RUNTIME_CTX["last_event"] = kind
        _RUNTIME_CTX["last_event_detail"] = detail
        _RUNTIME_CTX["last_event_ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")


def note_subprocess_exit(name: str, code: int, *, detail: str = "") -> None:
    note_runtime_event(
        "subprocess_exit",
        detail=f"{name} exit_code={code} {detail}".strip(),
    )
    try:
        path = _diagnostics_dir() / f"subprocess_exit_{time.strftime('%Y%m%dT%H%M%S')}.json"
        path.write_text(
            json.dumps(
                {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "name": name,
                    "exit_code": code,
                    "detail": detail,
                    "thread": threading.current_thread().name,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def _thread_info() -> dict:
    cur = threading.current_thread()
    return {
        "name": cur.name,
        "ident": cur.ident,
        "native_id": getattr(cur, "native_id", None),
        "alive_threads": [
            {"name": t.name, "ident": t.ident, "daemon": t.daemon}
            for t in threading.enumerate()
        ],
    }


def _collect_pipeline_state(pipeline: Any) -> dict:
    if pipeline is None:
        return {}
    state = getattr(pipeline, "state", None)
    return {
        "speech_state": str(getattr(state, "state", None)),
        "cancellation_requested": bool(
            getattr(pipeline, "cancellation_requested", False)
        ),
        "was_interrupted": bool(getattr(pipeline, "was_interrupted", False)),
        "closed": bool(getattr(pipeline, "closed", getattr(pipeline, "_closed", False))),
        "wake_mode": getattr(pipeline, "_wake_mode", None),
    }


def _collect_manager_state(manager: Any) -> dict:
    if manager is None:
        return {}
    pending = getattr(manager, "pending_clarification", None)
    return {
        "active": getattr(manager, "active", None),
        "pending_clarification": pending if _store_transcripts() else bool(pending),
        "pending_tool": bool(getattr(manager, "pending_tool", None)),
        "cancel_requested": bool(getattr(manager, "cancel_requested", False)),
        "last_raw_text": (
            getattr(manager, "_last_raw_text", "")
            if _store_transcripts()
            else bool(getattr(manager, "_last_raw_text", ""))
        ),
    }


def _audio_gate_state() -> dict:
    try:
        from voice.audio_gate import get_audio_gate

        snap = get_audio_gate().snapshot()
        return {
            "active_input_streams": snap.active_input_streams,
            "active_tts_playback": snap.active_tts_playback,
            "barge_signal_pending": snap.barge_signal_pending,
            "last_op": snap.last_op,
            "last_thread": snap.last_thread,
        }
    except Exception:
        return {}


def build_runtime_snapshot(
    *,
    reason: str = "",
    manager: Any = None,
    pipeline: Any = None,
    exception: Any = None,
    barge_in: Optional[bool] = None,
    gpu_op: str = "",
    exit_reason: str = "",
    current_interaction: Optional[dict] = None,
) -> dict:
    with _CTX_LOCK:
        ctx = dict(_RUNTIME_CTX)
    thread = _thread_info()
    pipe = _collect_pipeline_state(pipeline)
    mgr = _collect_manager_state(manager)
    if barge_in is not None:
        ctx["barge_in"] = bool(barge_in)
    if gpu_op:
        ctx["gpu_op"] = gpu_op
    if exit_reason:
        ctx["exit_reason"] = exit_reason
    if pipe.get("speech_state"):
        ctx["voice_state"] = pipe["speech_state"]
    if mgr.get("active") is not None:
        ctx["cm_active"] = mgr["active"]

    last_exc = ""
    if exception is not None:
        last_exc = f"{type(exception).__name__}: {exception}"
        ctx["last_exception"] = last_exc

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "reason": reason,
        "session_id": ctx.get("session_id") or "",
        "thread": thread,
        "voice_state": ctx.get("voice_state") or pipe.get("speech_state") or "",
        "conversation_manager": mgr,
        "pipeline": pipe,
        "active_tool": ctx.get("active_tool") or "",
        "tts_active": bool(
            ctx.get("tts_active")
            or (_audio_gate_state().get("active_tts_playback") or 0) > 0
        ),
        "stt_active": bool(ctx.get("stt_active")),
        "barge_in": bool(ctx.get("barge_in") or pipe.get("was_interrupted")),
        "gpu_op": ctx.get("gpu_op") or "",
        "last_semantic_decision": ctx.get("last_semantic") or {},
        "last_planner_step": ctx.get("last_planner_step") or {},
        "last_exception": last_exc or ctx.get("last_exception") or "",
        "exit_reason": ctx.get("exit_reason") or exit_reason or "",
        "degraded_subsystems": list(ctx.get("degraded_subsystems") or []),
        "audio_gate": _audio_gate_state(),
        "current_interaction": current_interaction or {},
        "trace_events": _recent_trace_events(40),
        "store_transcripts": _store_transcripts(),
    }


def write_runtime_snapshot(
    *,
    reason: str,
    manager: Any = None,
    pipeline: Any = None,
    exception: Any = None,
    barge_in: Optional[bool] = None,
    gpu_op: str = "",
    exit_reason: str = "",
    current_interaction: Optional[dict] = None,
    persist: bool = True,
) -> dict:
    """Build and optionally persist a Phase-14 diagnostic snapshot."""
    payload = build_runtime_snapshot(
        reason=reason,
        manager=manager,
        pipeline=pipeline,
        exception=exception,
        barge_in=barge_in,
        gpu_op=gpu_op,
        exit_reason=exit_reason,
        current_interaction=current_interaction,
    )
    # Always refresh heartbeat so hard kills leave a breadcrumb.
    try:
        heartbeat = _diagnostics_dir() / "runtime_heartbeat.json"
        heartbeat.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
    reason_s = str(reason or "")
    persist_eligible = persist and any(
        reason_s.startswith(prefix)
        for prefix in (
            "barge",
            "crash",
            "fatal",
            "watchdog",
            "recover_",
            "exit",
            "before_barge",
        )
    )
    if persist_eligible and _rate_limited_snapshot_reason(reason_s):
        # Keep latest state in heartbeat; skip another identical barge file.
        persist_eligible = False
    if persist_eligible:
        try:
            ts = time.strftime("%Y%m%dT%H%M%S")
            raw_name = f"runtime_snapshot_{ts}_{reason_s[:40]}"
            safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in raw_name)
            path = _diagnostics_dir() / f"{safe}.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
    return payload


def _barge_snapshot_min_interval_s() -> float:
    try:
        raw = get_config_value(
            "diagnostics", "barge_snapshot_min_interval_s", default=30.0
        )
        return max(0.0, float(raw if raw is not None else 30.0))
    except Exception:
        return 30.0


def _rate_limited_snapshot_reason(reason: str) -> bool:
    """Return True when this write should skip disk persist (still heartbeats)."""
    if not any(reason.startswith(p) for p in _RATE_LIMITED_SNAPSHOT_PREFIXES):
        return False
    interval = _barge_snapshot_min_interval_s()
    if interval <= 0:
        return False
    # barge* share one bucket; recover_* keyed by full reason (classification).
    if reason.startswith("recover_"):
        bucket = reason[:64]
    else:
        bucket = "barge"
    now = time.monotonic()
    with _PERSIST_LOCK:
        last = float(_LAST_RATE_LIMITED_PERSIST_MONO.get(bucket, 0.0) or 0.0)
        if last > 0.0 and (now - last) < interval:
            return True
        _LAST_RATE_LIMITED_PERSIST_MONO[bucket] = now
        return False


def reset_snapshot_rate_limits_for_tests() -> None:
    """Test helper — clear barge persist cooldown."""
    with _PERSIST_LOCK:
        _LAST_RATE_LIMITED_PERSIST_MONO.clear()


def classify_and_recover(
    exc: BaseException,
    *,
    subsystem: str,
    classification: str,
) -> str:
    """Log + classify failure; mark subsystem DEGRADED; do not kill process."""
    print(f"[RUNTIME][{classification}] subsystem={subsystem}: {exc}")
    with _CTX_LOCK:
        degraded = list(_RUNTIME_CTX.get("degraded_subsystems") or [])
        marker = f"{subsystem}:{classification}"
        if marker not in degraded:
            degraded.append(marker)
        _RUNTIME_CTX["degraded_subsystems"] = degraded
        _RUNTIME_CTX["last_exception"] = f"{type(exc).__name__}: {exc}"
    try:
        write_runtime_snapshot(
            reason=f"recover_{classification}",
            exception=exc,
            persist=True,
        )
    except Exception:
        pass
    try:
        from core.hud_events import emit_integration

        emit_integration(subsystem, "degraded")
    except Exception:
        pass
    return classification


def clear_degraded_subsystem(subsystem: str, *, classification: str = "") -> None:
    """Clear sticky DEGRADED markers after a successful recovery path."""
    sub = str(subsystem or "").strip()
    if not sub:
        return
    prefix = f"{sub}:"
    marker = f"{sub}:{classification}" if classification else ""
    with _CTX_LOCK:
        degraded = list(_RUNTIME_CTX.get("degraded_subsystems") or [])
        if marker:
            kept = [m for m in degraded if m != marker]
        else:
            kept = [m for m in degraded if not str(m).startswith(prefix)]
        _RUNTIME_CTX["degraded_subsystems"] = kept
        if marker and _RUNTIME_CTX.get("last_exception") and classification in str(
            _RUNTIME_CTX.get("last_exception") or ""
        ):
            _RUNTIME_CTX["last_exception"] = ""
        elif not kept:
            _RUNTIME_CTX["last_exception"] = ""
    try:
        from core.hud_events import emit_integration

        emit_integration(sub, "ok")
    except Exception:
        pass


def write_runtime_crash(
    exc: BaseException,
    *,
    thread_name: str = "",
    speech_state: str = "",
    manager: Any = None,
    pipeline: Any = None,
    current_interaction: Optional[dict] = None,
) -> Path:
    """Persist crash context without transcripts unless explicitly enabled."""
    ts = time.strftime("%Y%m%dT%H%M%S")
    path = _diagnostics_dir() / f"runtime_crash_{ts}.json"

    payload = build_runtime_snapshot(
        reason="crash",
        manager=manager,
        pipeline=pipeline,
        exception=exc,
        current_interaction=current_interaction,
    )
    payload.update(
        {
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "thread_name_arg": thread_name or "main",
            "speech_state_arg": speech_state,
        }
    )
    if speech_state:
        payload["voice_state"] = speech_state

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[CRASH] Wrote diagnostics: {path}")
    try:
        write_runtime_snapshot(reason="crash_mirror", exception=exc, persist=False)
    except Exception:
        pass
    return path


def log_runtime_exit(
    *,
    reason: str,
    source: str,
    exception: Any = None,
    user_requested: bool = False,
) -> None:
    """Print a structured line before every process exit. Idle/session end must not call this."""
    exc_text = ""
    if exception is not None:
        exc_text = f"{type(exception).__name__}: {exception}"
    print("[RUNTIME EXIT]")
    print(f"reason={reason}")
    print(f"source={source}")
    print(f"exception={exc_text or '(none)'}")
    print(f"user_requested={user_requested}")
    try:
        write_runtime_snapshot(
            reason="exit",
            exit_reason=reason,
            exception=exception,
            persist=True,
        )
    except Exception:
        pass


def install_faulthandler() -> None:
    """Best-effort native abort dumps (PortAudio / CUDA)."""
    try:
        import faulthandler

        crash_path = _diagnostics_dir() / "faulthandler.log"
        # Keep file handle process-lifetime.
        fh = open(crash_path, "a", encoding="utf-8")  # noqa: SIM115
        faulthandler.enable(file=fh, all_threads=True)
        print(f"[RUNTIME] faulthandler → {crash_path}")
    except Exception as exc:
        print(f"[RUNTIME] faulthandler unavailable: {exc}")


def install_asyncio_handler() -> None:
    try:
        import asyncio

        def _handler(loop, context):  # type: ignore[no-untyped-def]
            msg = context.get("message") or "asyncio exception"
            exc = context.get("exception")
            print(f"[ASYNCIO] {msg}: {exc}")
            try:
                if exc is not None:
                    write_runtime_crash(exc, thread_name="asyncio")
                    classify_and_recover(
                        exc,
                        subsystem="asyncio",
                        classification="ASYNCIO_EXCEPTION",
                    )
                else:
                    write_runtime_snapshot(reason="asyncio_error", persist=True)
            except Exception:
                pass

        try:
            loop = asyncio.get_event_loop()
            if loop is not None:
                loop.set_exception_handler(_handler)
        except Exception:
            pass
    except Exception:
        pass


def install_global_excepthook() -> None:
    """Log uncaught exceptions on main and worker threads (best effort)."""
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
    _HOOKS_INSTALLED = True

    install_faulthandler()
    install_asyncio_handler()

    original = sys.excepthook

    def _hook(exc_type, exc, tb):
        if exc is not None and exc_type is not BaseException:
            try:
                write_runtime_crash(exc, thread_name="uncaught")
                classify_and_recover(
                    exc,
                    subsystem="main",
                    classification="UNHANDLED_EXCEPTION",
                )
                log_runtime_exit(
                    reason="unhandled_exception",
                    source="sys.excepthook",
                    exception=exc,
                    user_requested=False,
                )
            except Exception:
                pass
        original(exc_type, exc, tb)

    sys.excepthook = _hook
    try:
        original_thread = getattr(threading, "excepthook", None)

        def _thread_hook(args):
            try:
                write_runtime_crash(
                    args.exc_value or Exception(str(args.exc_type)),
                    thread_name=str(getattr(args.thread, "name", "thread")),
                )
                classify_and_recover(
                    args.exc_value or Exception(str(args.exc_type)),
                    subsystem="thread",
                    classification="THREAD_EXCEPTION",
                )
                log_runtime_exit(
                    reason="thread_exception",
                    source=str(getattr(args.thread, "name", "thread")),
                    exception=args.exc_value,
                    user_requested=False,
                )
            except Exception:
                pass
            if original_thread is not None:
                original_thread(args)

        threading.excepthook = _thread_hook
    except Exception:
        pass
