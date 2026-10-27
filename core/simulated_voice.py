# ==========================
# ELIAN — Level 2 simulated voice (Piper → Whisper → CM)
# ==========================
"""Text → Piper WAV → Whisper STT → production ConversationManager.handle.

Verdict label: SIMULATED_VOICE_PASS / SIMULATED_VOICE_FAIL / SKIPPED.
Never claim HARDWARE PASS (real mic / room / echo).

Dry glue mode (no Piper/Whisper): ELIAN_SIM_VOICE_DRY=1 — STT returns source text.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

os.environ.setdefault("ELIAN_PROTECT_USER_APPS", "1")

# STT-degradation profiles for autonomous L2 lab (not real-room HARDWARE).
_FIXTURE_ALIASES = {
    "clean": "clean",
    "quiet": "quiet",
    "fast": "fast",
    "noise": "noise",
    "truncated": "truncated",
    "repeat": "repeat",
    "partial": "partial",
    "no_diacritics": "no_diacritics",
    "nodiacritics": "no_diacritics",
    "delayed": "delayed",
}


def normalize_fixture(name: str) -> str:
    key = str(name or "clean").strip().lower() or "clean"
    return _FIXTURE_ALIASES.get(key, "clean")


def apply_stt_fixture(text: str, fixture: str = "clean") -> str:
    """Deterministic STT-like degradations for dry L2 lab / post-STT robustness."""
    raw = str(text or "").strip()
    profile = normalize_fixture(fixture)
    if not raw or profile == "clean":
        return raw
    if profile == "no_diacritics":
        table = str.maketrans(
            {
                "ą": "a",
                "ć": "c",
                "ę": "e",
                "ł": "l",
                "ń": "n",
                "ó": "o",
                "ś": "s",
                "ź": "z",
                "ż": "z",
                "Ą": "A",
                "Ć": "C",
                "Ę": "E",
                "Ł": "L",
                "Ń": "N",
                "Ó": "O",
                "Ś": "S",
                "Ź": "Z",
                "Ż": "Z",
            }
        )
        return raw.translate(table)
    if profile == "quiet":
        # Whisper often lowercases and drops trailing clarity.
        return raw.lower().rstrip(".!?…")
    if profile == "fast":
        # Compressed ASR: missing spaces between short tokens.
        parts = raw.split()
        if len(parts) >= 2:
            return parts[0] + parts[1] + (" " + " ".join(parts[2:]) if len(parts) > 2 else "")
        return raw.replace(" ", "")
    if profile == "truncated":
        parts = raw.split()
        if len(parts) <= 1:
            return raw[: max(1, len(raw) // 2)]
        return " ".join(parts[: max(1, len(parts) - 1)])
    if profile == "partial":
        parts = raw.split()
        return parts[0] if parts else raw
    if profile == "repeat":
        parts = raw.split()
        if not parts:
            return raw
        return f"{parts[0]} {parts[0]} " + " ".join(parts[1:])
    if profile == "noise":
        return f"{raw} um"
    if profile == "delayed":
        # Late/partial hypothesis style — leading filler then content.
        return f"... {raw}"
    return raw


def degrade_audio(audio, fixture: str = "clean"):
    """Optional float32 audio transforms before Whisper (live L2 only)."""
    import numpy as np

    profile = normalize_fixture(fixture)
    arr = np.asarray(audio, dtype=np.float32)
    if arr.size == 0 or profile == "clean":
        return arr
    if profile == "quiet":
        return (arr * 0.15).astype(np.float32)
    if profile == "noise":
        rng = np.random.default_rng(42)
        noise = rng.normal(0.0, 0.02, size=arr.shape).astype(np.float32)
        return (arr + noise).astype(np.float32)
    if profile == "fast" and arr.size > 8:
        # Time-compress ~1.35x via decimation + linear interp back to length/1.35.
        step = 1.35
        n = max(1, int(arr.size / step))
        idx = np.linspace(0, arr.size - 1, num=n)
        return np.interp(idx, np.arange(arr.size), arr).astype(np.float32)
    if profile == "truncated" and arr.size > 16:
        return arr[: max(8, arr.size * 2 // 3)].astype(np.float32)
    return arr


def _dry_mode() -> bool:
    return str(os.environ.get("ELIAN_SIM_VOICE_DRY", "")).strip().lower() in (
        "1",
        "true",
        "yes",
    )


def piper_available() -> bool:
    try:
        import Voice as voice_mod

        piper = getattr(voice_mod, "PIPER", "") or ""
        model = getattr(voice_mod, "VOICE_MODEL", "") or ""
        import shutil

        piper_ok = bool(piper) and (
            os.path.isfile(piper) or shutil.which(piper) is not None
        )
        return piper_ok and os.path.isfile(model)
    except Exception:
        return False


def synthesize_to_wav(text: str, wav_path: str) -> bool:
    import Voice as voice_mod

    # Standalone synth must not inherit a prior stream-TTS cancel latch.
    try:
        voice_mod._stream_tts_cancel.clear()
    except Exception:
        pass
    return bool(voice_mod._generate_piper_wav(str(text or ""), wav_path))


def wav_to_float32(wav_path: str):
    import numpy as np
    import soundfile as sf
    import Voice as voice_mod

    audio, sr = sf.read(wav_path, dtype="float32", always_2d=False)
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    target = int(getattr(voice_mod, "RATE", 16000) or 16000)
    if int(sr) != target and len(audio) > 0:
        # Linear resample without requiring librosa.
        duration = len(audio) / float(sr)
        n = max(1, int(round(duration * target)))
        x_old = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=n, endpoint=False)
        audio = np.interp(x_new, x_old, audio).astype(np.float32)
    else:
        audio = np.asarray(audio, dtype=np.float32)
    return audio


def stt_from_audio(audio) -> str:
    import Voice as voice_mod

    return str(voice_mod.transcribe_audio(audio, llm_correct=False) or "").strip()


def simulate_utterance(text: str, *, fixture: str = "clean") -> Dict[str, Any]:
    """Return structural trace for one simulated spoken turn (no CM yet)."""
    source = str(text or "").strip()
    profile = normalize_fixture(fixture)
    out: Dict[str, Any] = {
        "level": "L2_SIMULATED_VOICE",
        "source_text": source,
        "fixture": profile,
        "wav": "",
        "stt_text": "",
        "status": "FAIL",
        "label": "SIMULATED_VOICE_FAIL",
        "dry": _dry_mode(),
    }
    if not source:
        out["error"] = "empty_text"
        return out

    if _dry_mode():
        out["stt_text"] = apply_stt_fixture(source, profile)
        out["status"] = "PASS"
        out["label"] = "SIMULATED_VOICE_PASS"
        out["note"] = "dry_stt_echo" if profile == "clean" else f"dry_fixture:{profile}"
        return out

    if not piper_available():
        out["status"] = "SKIPPED"
        out["label"] = "SIMULATED_VOICE_SKIPPED"
        out["error"] = "piper_unavailable"
        return out

    fd, wav_path = tempfile.mkstemp(prefix="elian_sim_voice_", suffix=".wav")
    os.close(fd)
    try:
        if not synthesize_to_wav(source, wav_path):
            out["error"] = "piper_failed"
            return out
        out["wav"] = wav_path
        audio = wav_to_float32(wav_path)
        if audio is None or len(audio) == 0:
            out["error"] = "empty_audio"
            return out
        audio = degrade_audio(audio, profile)
        stt = stt_from_audio(audio)
        # If Whisper returned text, still apply no_diacritics lab overlay when requested
        # in dry-equivalent live tests is skipped — live uses real STT only.
        out["stt_text"] = stt
        if not stt:
            out["error"] = "stt_empty"
            return out
        out["status"] = "PASS"
        out["label"] = "SIMULATED_VOICE_PASS"
        return out
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    finally:
        # Keep wav only on failure for diagnostics; delete on success to avoid clutter.
        if out.get("status") == "PASS" and wav_path and os.path.isfile(wav_path):
            try:
                os.remove(wav_path)
                out["wav"] = ""
            except OSError:
                pass


def run_simulated_voice_turn(
    text: str,
    *,
    live_tools: bool = False,
    debug: bool = True,
    fixture: str = "clean",
) -> Dict[str, Any]:
    """Piper→STT then same post-STT path as --inject-text."""
    from core.text_inject import _make_manager, run_one_turn
    from core.model_client import reset_model_client
    from core.tools import reset_registry_for_tests

    sim = simulate_utterance(text, fixture=fixture)
    if debug:
        print(
            f"[SIM_VOICE] label={sim.get('label')} fixture={sim.get('fixture')} "
            f"source={sim.get('source_text')!r} stt={sim.get('stt_text')!r} dry={sim.get('dry')}"
        )
    if sim.get("status") != "PASS":
        return {"sim": sim, "cm": None}

    mgr = _make_manager(live_tools=live_tools)
    try:
        stt_text = str(sim.get("stt_text") or "")
        # Match production wake strip before ConversationManager.handle.
        try:
            from WakeWord import extract_wake

            wake = extract_wake(stt_text)
            inject = (
                wake.raw_command
                if wake.wake_detected and str(wake.raw_command or "").strip()
                else stt_text
            )
            sim["wake_detected"] = bool(wake.wake_detected)
            sim["command_after_wake"] = inject
        except Exception:
            inject = stt_text
        if debug and inject != stt_text:
            print(f"[SIM_VOICE] wake_strip stt={stt_text!r} → command={inject!r}")
        row = run_one_turn(mgr, inject, debug=debug)
        return {"sim": sim, "cm": row}
    finally:
        try:
            mgr.deactivate(reason="simulated_voice_end")
        except Exception:
            pass
        reset_model_client()
        reset_registry_for_tests(None)
        tmp = getattr(mgr, "_text_inject_tmp", None)
        if tmp is not None:
            tmp.cleanup()


def run_from_argv(argv: List[str]) -> int:
    live = "--live-tools" in argv
    debug = "--quiet" not in argv
    fixture = "clean"
    if "--sim-voice-fixture" in argv:
        idx = argv.index("--sim-voice-fixture")
        fixture = argv[idx + 1] if idx + 1 < len(argv) else "clean"
    if "--simulated-voice" in argv:
        idx = argv.index("--simulated-voice")
        text = argv[idx + 1] if idx + 1 < len(argv) else ""
        if not text or text.startswith("--"):
            print(
                'Usage: python main.py --simulated-voice "ciemno tu" '
                "[--sim-voice-fixture no_diacritics]"
            )
            return 2
        payload = run_simulated_voice_turn(
            text, live_tools=live, debug=debug, fixture=fixture
        )
        sim = payload.get("sim") or {}
        print(json.dumps({"sim": sim, "cm": payload.get("cm")}, ensure_ascii=False)[:2000])
        if sim.get("status") == "SKIPPED":
            return 0
        return 0 if sim.get("status") == "PASS" else 1
    if "--sim-voice-script" in argv:
        idx = argv.index("--sim-voice-script")
        path = argv[idx + 1] if idx + 1 < len(argv) else ""
        if not path or not Path(path).is_file():
            print("Usage: python main.py --sim-voice-script path.json")
            return 2
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        turns = data.get("turns") if isinstance(data, dict) else data
        rows = []
        for item in turns or []:
            if isinstance(item, str):
                text = item
                turn_fixture = fixture
            else:
                text = str((item or {}).get("text") or (item or {}).get("user") or "")
                turn_fixture = str((item or {}).get("fixture") or fixture)
            rows.append(
                run_simulated_voice_turn(
                    text, live_tools=live, debug=debug, fixture=turn_fixture
                )
            )
        fails = sum(1 for r in rows if (r.get("sim") or {}).get("status") == "FAIL")
        skips = sum(1 for r in rows if (r.get("sim") or {}).get("status") == "SKIPPED")
        print(f"sim_voice_script turns={len(rows)} fail={fails} skipped={skips}")
        return 1 if fails else 0
    return 2
