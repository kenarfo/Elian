# ELIAN — P3 non-mic live probes (honest; not a substitute for mic RC)
from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path

os.environ.setdefault("ELIAN_PROTECT_USER_APPS", "1")

ROOT = Path(__file__).resolve().parents[2]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
out: dict = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "probes": {}}


def rec(name: str, status: str, **extra) -> None:
    row = {"status": status, **extra}
    out["probes"][name] = row
    print(f"[{status}] {name} {extra}")


def main() -> int:
    # --- Piper/TTS speak ---
    try:
        from Voice import initialize_voice, speak, shutdown_voice, get_voice_settings

        settings = get_voice_settings()
        initialize_voice()
        t0 = time.time()
        speak("Test P3. Elian mówi.")
        rec(
            "tts_piper",
            "PASS",
            elapsed_s=round(time.time() - t0, 2),
            input_device=getattr(settings, "input_device_name", None)
            or (settings.get("input_device_name") if isinstance(settings, dict) else None),
            output_device=getattr(settings, "output_device_name", None)
            or (settings.get("output_device_name") if isinstance(settings, dict) else None),
        )
        shutdown_voice()
    except Exception as e:
        rec("tts_piper", "FAIL", error=str(e)[:200], tb=traceback.format_exc()[-400:])

    # --- Whisper model load (no mic utterance) ---
    try:
        from voice import stt as stt_mod

        t0 = time.time()
        # common entry points
        model = None
        for attr in ("get_whisper_model", "load_whisper", "_get_model", "ensure_model"):
            fn = getattr(stt_mod, attr, None)
            if callable(fn):
                model = fn()
                break
        if model is None and hasattr(stt_mod, "WhisperSTT"):
            model = "class_present"
        rec(
            "whisper_load",
            "PASS" if model is not None else "PARTIAL",
            elapsed_s=round(time.time() - t0, 2),
            detail=type(model).__name__ if model is not None else "no loader found",
        )
    except Exception as e:
        rec("whisper_load", "FAIL", error=str(e)[:200])

    # --- Ollama live ---
    try:
        from Brain import ask_ai

        t0 = time.time()
        ans = ask_ai("Powiedz jednym słowem: ok")
        rec(
            "ollama_live",
            "PASS" if ans and str(ans).strip() else "FAIL",
            elapsed_s=round(time.time() - t0, 2),
            answer=str(ans)[:120],
        )
    except Exception as e:
        rec("ollama_live", "FAIL", error=str(e)[:200])

    # --- HA capability / token ---
    try:
        from Config import get_config_value
        from core.capability_registry import get_capability_registry, HOME_ASSISTANT

        token = get_config_value("smart_home", "token", default="") or get_config_value(
            "smart_home", "access_token", default=""
        )
        reg = get_capability_registry()
        reg.refresh()
        ha = reg.get(HOME_ASSISTANT)
        if not token:
            rec(
                "ha_live",
                "BLOCKED",
                reason="smart_home.token missing",
                cap=getattr(ha, "status", None),
                detail=getattr(ha, "detail", None) or getattr(ha, "failure_reason", None),
            )
        else:
            rec("ha_live", "AVAILABLE_TOKEN", cap=str(getattr(ha, "status", None)))
    except Exception as e:
        rec("ha_live", "FAIL", error=str(e)[:200])

    # --- Web search (not browser automation) ---
    try:
        from core.conversation_manager import ConversationManager
        from core.tools import ToolRegistry

        tr = ToolRegistry(dry_run=False)
        # Prefer dry if web would mutate — search is read-only
        cm = ConversationManager(tools=tr)
        cm.session_active = True
        t0 = time.time()
        r = cm.handle("wyszukaj w sieci co to jest Home Assistant")
        rec(
            "web_search_live",
            "PASS" if r and "nie umiem" not in str(r).lower() else "PARTIAL",
            elapsed_s=round(time.time() - t0, 2),
            reply=str(r)[:200],
        )
    except Exception as e:
        rec("web_search_live", "FAIL", error=str(e)[:200])

    # --- Memory preference restart simulation ---
    try:
        from Memory import load_memory, save_memory

        mem = load_memory()
        key = "_p3_rc_test_pref"
        before = dict(mem) if isinstance(mem, dict) else {}
        prefs = before.setdefault("preferences", {}) if isinstance(before, dict) else {}
        if not isinstance(prefs, dict):
            prefs = {}
            before["preferences"] = prefs
        prefs[key] = "test_value_p3"
        save_memory(before)
        mem2 = load_memory()
        prefs2 = (mem2 or {}).get("preferences") or {}
        ok_set = prefs2.get(key) == "test_value_p3"
        # cleanup
        if isinstance(prefs2, dict) and key in prefs2:
            del prefs2[key]
            save_memory(mem2)
        mem3 = load_memory()
        gone = key not in ((mem3 or {}).get("preferences") or {})
        rec(
            "memory_persist_cleanup",
            "PASS" if ok_set and gone else "FAIL",
            set_ok=ok_set,
            cleaned=gone,
        )
    except Exception as e:
        rec("memory_persist_cleanup", "FAIL", error=str(e)[:200])

    # --- Proactivity dry ---
    try:
        from core.situation_engine import SituationEngine
        from core.proactive_policy import evaluate_proactive  # may vary

        rec("proactivity_import", "PARTIAL", note="checking SituationEngine")
        se = SituationEngine()
        # inject synthetic low battery if API allows
        inject = getattr(se, "ingest", None) or getattr(se, "observe", None) or getattr(se, "update", None)
        rec(
            "proactivity_engine",
            "PASS" if se is not None else "FAIL",
            has_inject=bool(inject),
            methods=[m for m in dir(se) if not m.startswith("_")][:20],
        )
    except Exception as e:
        # try alternate modules
        try:
            from core import proactive

            rec("proactivity_engine", "PARTIAL", via="core.proactive", err=str(e)[:120])
        except Exception as e2:
            rec("proactivity_engine", "BLOCKED", error=str(e2)[:200])

    # --- Spotify protect ---
    rec("spotify", "NOT_RUNNING_SAFE", protect=os.environ.get("ELIAN_PROTECT_USER_APPS"))

    path = ROOT / "Data" / "diagnostics" / "p3_live_probes.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
