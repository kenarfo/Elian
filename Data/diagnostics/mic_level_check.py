"""One-shot mic level check (not imported by runtime).

Speak into the Plantronics headset boom while this runs. Expect close-talk
peak_rms >> 0.01 on the chosen WDM-KS Plantronics device. LCS is the Windows
default room mic — compare both; Elian STT should use Plantronics.
"""
import sounddevice as sd
import numpy as np

devs = sd.query_devices()
print("=== INPUTS ===")
for i, d in enumerate(devs):
    if d["max_input_channels"] > 0:
        host = sd.query_hostapis(d["hostapi"])["name"]
        print(f"{i}: [{host}] {d['name'][:70]} sr={d['default_samplerate']}")

targets = []
for i, d in enumerate(devs):
    if d["max_input_channels"] <= 0:
        continue
    name = d["name"]
    low = name.lower()
    if "easeus" in low or "voicewave" in low:
        continue
    if "Plantronics" in name or "LCS" in name or "Blackwire" in name:
        host = sd.query_hostapis(d["hostapi"])["name"]
        # Prefer one entry per logical device: WDM-KS > MME > DS > WASAPI
        hu = host.upper()
        pref = 9
        for p, token in enumerate(("WDM-KS", "MME", "DIRECTSOUND", "WASAPI")):
            if token in hu:
                pref = p
                break
        targets.append((pref, i, name, host))

targets.sort()
# Dedupe by brand family keeping best host
picked = []
seen = set()
for pref, i, name, host in targets:
    key = "plantronics" if ("plantronics" in name.lower() or "blackwire" in name.lower()) else (
        "lcs" if "lcs" in name.lower() else name[:20]
    )
    if key in seen:
        continue
    seen.add(key)
    picked.append(i)

if not picked:
    picked = [sd.default.device[0]]

print()
print("Speak NOW for ~2s on each device (wear Plantronics for that entry)...")
for i in picked:
    try:
        sr = 16000
        try:
            sd.check_input_settings(device=i, samplerate=sr, channels=1)
        except Exception:
            sr = int(devs[i]["default_samplerate"]) or 48000
        rec = sd.rec(int(2.0 * sr), samplerate=sr, channels=1, dtype="float32", device=i)
        sd.wait()
        rms = float(np.sqrt(np.mean(rec**2)))
        peak = float(np.max(np.abs(rec)))
        host = sd.query_hostapis(devs[i]["hostapi"])["name"]
        print(
            f"#{i} [{host}] {devs[i]['name'][:50]} @{sr} | "
            f"rms={rms:.5f} peak={peak:.5f}"
        )
    except Exception as e:
        print(f"#{i} FAIL: {e}")
