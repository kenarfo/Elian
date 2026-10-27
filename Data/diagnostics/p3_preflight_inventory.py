# ELIAN — P3 preflight inventory (one-shot)
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
cfg = json.loads((ROOT / "Data" / "config.json").read_text(encoding="utf-8"))

print("=== PREFLIGHT INVENTORY ===")

# Audio
try:
    import sounddevice as sd

    print("--- AUDIO DEVICES ---")
    for i, d in enumerate(sd.query_devices()):
        kinds = []
        if d["max_input_channels"] > 0:
            kinds.append("IN")
        if d["max_output_channels"] > 0:
            kinds.append("OUT")
        name = str(d["name"])[:90]
        print(f"  [{i}] {'/'.join(kinds)} {name} sr={d['default_samplerate']}")
except Exception as e:
    print("sounddevice FAIL", e)

voice = cfg.get("voice", {})
inp = voice.get("input_device_name", "")
outp = voice.get("output_device_name", "")
print(f"config input_device_name={inp!r}")
print(f"config output_device_name={outp!r}")

piper = Path(voice.get("piper_path", ""))
model = Path(voice.get("model_path", ""))
print("Piper exe:", "AVAILABLE" if piper.exists() else "UNAVAILABLE", piper)
print("Piper model:", "AVAILABLE" if model.exists() else "UNAVAILABLE", model)

# Ollama
try:
    r = urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3)
    data = json.loads(r.read().decode())
    models = [m.get("name") for m in data.get("models", [])]
    print("Ollama: AVAILABLE", models[:10])
except Exception as e:
    print("Ollama: UNAVAILABLE", e)

# CUDA
try:
    import torch

    print(
        "torch",
        torch.__version__,
        "cuda_available",
        torch.cuda.is_available(),
    )
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        print(
            "  GPU",
            torch.cuda.get_device_name(0),
            "VRAM_GB",
            round(props.total_memory / 1e9, 1),
        )
except Exception as e:
    print("torch/cuda:", e)

try:
    import faster_whisper  # noqa: F401

    print("faster_whisper: AVAILABLE")
except Exception as e:
    print("faster_whisper:", e)

# HA
sm = cfg.get("smart_home") or {}
ha_url = (sm.get("url") or "").rstrip("/")
token_path = ROOT / "Data" / "ha_token.txt"
print("HA url:", ha_url or "(none)")
print("HA enabled:", sm.get("enabled"))
if ha_url:
    try:
        req = urllib.request.Request(ha_url + "/api/", method="GET")
        # may 401 without token — still proves reachability
        urllib.request.urlopen(req, timeout=3)
        print("HA: AVAILABLE (api reachable)")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            print("HA: AVAILABLE (auth required, host up)", e.code)
        else:
            print("HA: DEGRADED", e)
    except Exception as e:
        print("HA: UNAVAILABLE/BLOCKED", e)

# Spotify
try:
    out = subprocess.check_output(
        ["tasklist", "/FI", "IMAGENAME eq Spotify.exe"],
        text=True,
        errors="ignore",
    )
    print(
        "Spotify:",
        "USER_OWNED_RUNNING" if "Spotify.exe" in out else "NOT_RUNNING",
    )
except Exception as e:
    print("Spotify check", e)

# Playwright
try:
    from playwright.sync_api import sync_playwright  # noqa: F401

    print("Playwright: AVAILABLE")
except Exception as e:
    print("Playwright:", e)

print("HUD enabled:", (cfg.get("hud") or {}).get("enabled"))
print("companion cfg:", cfg.get("companion") or cfg.get("phone_companion") or "(absent)")
print("ELIAN_PROTECT_USER_APPS", os.environ.get("ELIAN_PROTECT_USER_APPS"))
print("whisper cfg:", cfg.get("whisper"))
