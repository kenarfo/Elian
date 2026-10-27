# ==========================
# Verify normal runtime: conversation + self-dev + GUI + English TTS
# ==========================
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from core.event_bus import EventBus
    from core.multi_agent import get_orchestrator, set_orchestrator
    from core.runtime import ElianRuntime
    from voice.tts_engine import get_tts_engine, measure_english_synthesis, reset_tts_engine_for_tests

    reset_tts_engine_for_tests()
    tts_m = measure_english_synthesis("Elian speaking English.")
    print("TTS_MEASURE", json.dumps(tts_m, indent=2))
    if not tts_m.get("ok"):
        print("FAIL: English TTS synthesis")
        return 2

    cm = MagicMock()
    cm.active = True
    cm.registry = None
    cm.handle.return_value = MagicMock(
        text="I am available while self-development continues.",
        source="chat",
    )

    runtime = ElianRuntime(
        bus=EventBus(),
        watchers=[],
        speak_fn=lambda t: None,
        busy_check=lambda: False,
        preference_get=lambda k, d=None: d,
        start_background=True,
        conversation_manager=cm,
        poll_sleep=0.5,
    )
    runtime.start()
    orch = getattr(runtime, "_orchestrator", None) or get_orchestrator()
    if orch is None:
        print("FAIL: orchestrator missing")
        runtime.stop()
        return 3

    # Wait briefly for background self-dev thread / core agents.
    time.sleep(1.0)
    types = {a.agent_type: a.status for a in orch.registry.list_all()}
    print("AGENTS", types)
    snap = orch.snapshot()
    sm = snap["self_model"]
    print(
        "SELF_MODEL",
        {
            "health": sm.get("health"),
            "language": sm.get("language"),
            "voice_engine": sm.get("voice_engine"),
            "development": sm.get("development"),
            "tts": (sm.get("runtime_state") or {}).get("tts"),
            "hardware": {
                k: (sm.get("runtime_state") or {}).get("hardware", {}).get(k)
                for k in (
                    "gpu_name",
                    "vram_total_gb",
                    "vram_free_gb",
                    "ram_total_gb",
                )
            },
            "bg_alive": (sm.get("runtime_state") or {}).get("bg_thread_alive"),
        },
    )

    # Conversation while background may be active.
    orch.resources.note_interactive(True)
    user = orch.handle_user_text("What are you doing?")
    orch.resources.note_interactive(False)
    print("USER", user.status, user.summary[:160])

    # GUI snapshot
    cc = getattr(runtime, "_control_center", None)
    if cc is not None:
        port = cc.port
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/snapshot", timeout=3) as resp:
            gui = json.loads(resp.read().decode("utf-8"))
        live = gui.get("live") or {}
        print(
            "GUI_LIVE",
            {
                "tts": live.get("tts"),
                "self_development": {
                    k: (live.get("self_development") or {}).get(k)
                    for k in ("active", "phase", "cycle", "bg_thread_alive")
                },
                "agents": len(gui.get("agents") or []),
            },
        )
    else:
        print("GUI_LIVE skipped")

    ok = (
        "conversation" in types
        and "memory" in types
        and "self_development" in types
        and user.status == "ok"
        and tts_m.get("ok")
        and (sm.get("runtime_state") or {}).get("bg_thread_alive")
    )
    # Graceful stop — request self-dev halt + recover unfinished mutations.
    try:
        orch.pause_background()
        from core.self_improve.self_development.state import (
            DevelopmentStateStore,
            default_state_path,
        )

        DevelopmentStateStore(default_state_path()).request_stop()
    except Exception:
        pass
    runtime.stop()
    set_orchestrator(None)
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
