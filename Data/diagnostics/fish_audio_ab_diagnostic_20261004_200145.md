# Fish Audio A/B diagnostic — Elian local TTS

| Field | Value |
|------|--------|
| **When (local)** | 2026-10-04 ~20:01 |
| **Mode** | Read-only diagnosis (no production / config / Fish param changes) |
| **Control text** | `Dzień dobry. To jest test głosu Elian.` |
| **Raw result JSON** | `Data/diagnostics/fish_ab_diag_tmp_result.json` |
| **WAVs** | `fish_ab_diag_cold.wav`, `fish_ab_diag_warm.wav` |

Note: first attempt hit `ELIAN_FISH_FORCE_UNAVAILABLE` left in the shell environment (not in `Data/config.json`). Cleared for the process only; config files untouched.

---

## A. Fish configuration (from live config + code)

| Item | Value (verified) |
|------|------------------|
| Backend | Local `s2.exe` (s2.cpp), persistent `--server` on `127.0.0.1:3037` |
| Exe | `C:\Users\kenar\ElianResearch\s2.cpp\build\Release\s2.exe` (mtime 2026-10-04 18:25:51, 945152 bytes) |
| s2.cpp project | `project(s2cpp …)` in CMakeLists — **no semantic version string** in help/binary |
| Model file | **`s2-pro-q4_k_m.gguf`** (confirmed) |
| Model path | `C:\Users\kenar\ElianResearch\models\s2-pro\s2-pro-q4_k_m.gguf` (~3.32 GiB) |
| Quant config | `fish_quant=q4_k_m` |
| Tokenizer | `C:\Users\kenar\ElianResearch\models\s2-pro\tokenizer.json` |
| Voice profile | **`elian_jarvis`** |
| `.s2voice` path | `C:\Users\kenar\ElianResearch\voices\elian_jarvis.s2voice` (13614 bytes) |
| GPU policy | `exclusive` (Ollama unload on Fish session) |
| CUDA device | `0` |
| Cloud `fish_voice_id` | empty (local profile only) |
| Elian `prefer_fish_tts` | `true` |
| Config language / Piper | `language=en-US`, Piper `en_GB-alan-medium` (fallback) |

### Parameters passed to Fish (current code — not modified)

**Persistent `/generate` (what Elian uses):**

| Param | Value | Tempo/prosody impact? |
|-------|-------|------------------------|
| `max_new_tokens` | 256 | Cap length; can truncate long text, not slow speech by itself |
| `temperature` | **0.8** (hardcoded in `fish_s2_worker.py`) | Yes — variability / pacing |
| `top_p` | **0.8** | Mild |
| `top_k` | **30** | Mild |
| `voice` / `voice_dir` | `elian_jarvis` + voices dir | **Strong** — conditioning of pace/timbre |
| `speed` | **not sent** | N/A — no speed control in Elian path |
| `seed` | **not sent** | N/A |
| silence trim / normalize | **not set** in multipart params | s2.exe defaults apply (CLI has `--trim-silence` / `--normalize` but Elian does not pass them) |
| streaming | HTTP response body = full WAV | No chunked playback from Fish |
| sample-rate conversion in Fish | none observed (native WAV @ 44100) | — |

**One-shot CLI fallback** (`_synthesize_oneshot`): `--model --tokenizer --text --output --cuda --gpu-layers -1 --max-tokens --voice --voice-dir` — **does not pass** `--temperature/--top-p/--top-k/--speed`.

---

## B. Audio properties (control text)

| Property | Cold | Warm |
|----------|-----:|-----:|
| Sample rate | **44100 Hz** | **44100 Hz** |
| Channels | **1** | **1** |
| Format | WAV FLOAT | WAV FLOAT |
| Frames | 129024 | 126976 |
| Duration | **2.926 s** | **2.879 s** |
| Peak | ~0.63 | ~0.66 |

Resampling in Elian playback path for this run: **none** (`output_rate=44100`, `resampled=false`, device `#16`).

---

## C. Generation

| Metric | Cold (session start + 1st gen) | Warm (2nd gen) |
|--------|-------------------------------:|---------------:|
| Worker cold-start | **9609 ms** | (reused) |
| Worker `last_generate_ms` | 3723 ms | **3531 ms** |
| API wall (`synthesize_s2_wav`) | **14369 ms** | **3545 ms** |
| Audio duration | 2.926 s | 2.879 s |
| **RTF** (wall / audio) | **4.91** | **1.23** |
| RTF generate-only (warm) | — | ≈ 3.53 / 2.88 ≈ **1.23** |

Interpretation:

- Cold is dominated by **model load + exclusive GPU handoff**, not speech length.
- Warm generate is **slightly slower than realtime** (RTF≈1.23) — noticeable as “thinking then speaking”, but not a 2× playback bug.
- Prior e2e (`fish_persistent_e2e_benchmark.json`): warm ~1.6–2.2 s for short **English** lines — same order of magnitude.

---

## D. Playback

| Metric | Value |
|--------|------:|
| Output rate | 44100 (native) |
| Resample | **no** |
| Time-to-first-audio (after `sd.play`) | ~0.062 s |
| Theoretical WAV duration | 2.879 s |
| Measured `sd.wait()` wall | **3.31 s** |
| **playback_ratio** | **1.15** |

Interpretation:

- **Not** a 0.5× / 0.75× / 2× clock bug.
- Ratio ≈1.15 ≈ WAV duration + PortAudio drain / scheduling; Elian also adds `tts_cooldown=0.35 s` after playback in production (`Config.py`), which extends perceived turn end further but does not slow the voice itself.
- `_fit_playback_rate` only resamples when the device rejects the native rate; this device accepted 44100.

---

## E. Speech speed / prosody

| Metric | Cold | Warm |
|--------|-----:|-----:|
| Leading silence | 0.001 s | 0.0 s |
| Trailing silence | 0.12 s | 0.15 s |
| Voiced span | 2.81 s | 2.73 s |
| Speech ratio | 0.61 | 0.58 |
| Long internal gaps (≥250 ms) | 1× **477 ms** | 1× **562 ms** |
| Approx chars / voiced-s | ~11.4 | ~11.7 |

For a short 2-clause Polish sentence, **~2.9 s total audio** is **somewhat leisurely** vs a typical ~1.5–2.5 s human delivery, but **not** a 5–6 s stretched disaster.

The internal ~0.5 s gap aligns with the period between sentences — normal pause, not pathological stretch.

**Likely perceptual “slower than browser” factors (generation-side, not playback clock):**

1. English Jarvis `.s2voice` conditioning speaking **Polish** text (config `language=en-US`).
2. Sampling defaults `temperature=0.8` / `top_p=0.8` / `top_k=30` with **no speed knob**.
3. Q4_K_M local stack vs whatever the browser stack used (unknown — see G).

Reference: existing English probe `e2e_q4_jarvis_profile_probe.wav` (“Elian is online.”) = **1.53 s @ 44100** — natural for short English.

---

## F. Elian latency (where ~20–30 s goes)

### Metric semantics (`core/latency.py`)

| Logged label | Definition |
|--------------|------------|
| `tts_generate` | `tts_gen_start` → `tts_gen_end` |
| `time_to_first_audio` | `tts_gen_start` → `tts_first_audio` |
| `total` | **`turn_start` → `tts_first_audio`** (not end of playback) |

### Critical instrumentation gap (Fish path)

`FishAudioTTS.speak` → `synthesize_s2_wav` (GPU unload + cold start + generate) → `Voice.speak_from_wav`.

In `speak_from_wav`:

```text
tracker.mark("tts_gen_start")
tracker.mark("tts_gen_end")   # immediately — generation already finished outside tracker
...
sd.play(...)
tracker.mark("tts_first_audio")
```

So for Fish:

- **`tts_generate` is often ~0 ms** (matches your second sample).
- Cold start / exclusive unload / generate wall time sit in the **unlabeled gap** inside `total` (between STT/agent end and `tts_first_audio`), **not** in `tts_generate`.
- Piper `Voice.speak` marks gen around real Piper synth — that can show hundreds of ms (`~753` may be Piper fallback or a non-Fish path).

### Breakdown of a ~30 s `total` (best evidence-based reconstruction)

Components that **can** sum to ~25–30 s before first audio:

| Stage | Typical measured here / in prior e2e |
|-------|--------------------------------------|
| Capture | ~1.5–3.0 s (user speech — not Fish) |
| STT | ~0.3 s |
| Agent / Ollama / tools | **seconds–tens of seconds** (unmarked in your snippet) |
| Exclusive GPU: Ollama unload | ~3–4 s (`fish_persistent_e2e` handoff ~4.3 s) |
| Fish worker cold start | **~8–10 s** (this run **9.6 s**) |
| Fish generate (warm) | ~1.6–3.5 s |
| Fish generate wall on cold request | **~14 s** this run (includes cold) |

**Fish alone does not explain 30 s if `tts_generate=753` and `ttfa=808`** — those marks are inconsistent with a full cold Fish synth unless the long work happened **before** `tts_gen_start` (agent + GPU handoff + Fish synth already done, then `speak_from_wav` only plays).

Most probable for sample 1 (`total≈30741`, `tts_generate≈753`, `ttfa≈808`):

1. Long **agent/LLM/tool** time after STT, and/or
2. **Fish/GPU work not attributed** to `tts_generate` because of `speak_from_wav` zero-width marks, and
3. `total` stops at **first audio**, so it still excludes full playback (~3 s) + cooldown (0.35 s).

Sample 2 (`total≈4971`, `tts_generate≈0`, `ttfa≈32`): warm Fish or short/cached path; generate hidden; total still includes agent+capture.

**PIPELINE_LATENCY: PROBLEM** (instrumentation + exclusive cold handoff + agent), not primarily “Fish plays at half speed”.

---

## G. Web comparison

Searched project + `ElianResearch` docs/logs for a recorded browser/playground A/B (model, voice, speed, sample rate, params).

**Result:**

```text
BRAK DANYCH DO PEŁNEGO A/B Z WERSJĄ WEB
```

Confirmed only: Fish Audio website mentioned in s2.cpp LICENSE; no saved browser run with matching `elian_jarvis` / q4 / sampling / speed settings in Elian diagnostics.

Cannot honestly claim local vs web param parity.

---

## H. ROOT CAUSE (ranked)

1. **Most likely — perceptual “slow voice” is mostly prosody/conditioning, not a broken playback clock.**
   Local stack produces ~2.9 s of leisurely Polish audio from an English Jarvis `.s2voice`, with temp/top_p hardcoded and **no speed parameter**. Playback ratio ≈1.15 @ native 44100 rules out 0.5× resampling.

2. **Second — turn latency (~20–30 s) is dominated by exclusive GPU handoff + Fish cold start + agent/LLM, and Fish time is mis-attributed.**
   Cold session wall ~14 s for this sentence; worker cold ~9.6 s; `speak_from_wav` zeros `tts_generate` so logs under-report Fish cost.

3. **Third — warm generate RTF≈1.23 (Q4 local) makes speech start feel late even when audio length is only ~3 s.**
   Browser/cloud may have been faster or used different speed/quality settings — **unverified**.

---

## I. NEXT ACTION (recommendations only — not applied)

1. **Instrument Fish properly:** mark `tts_gen_start` before `synthesize_s2_wav` and `tts_gen_end` after it (in `FishAudioTTS.speak` / RoutedTTS), and log `fish_cold_ms` / `fish_generate_ms` / `ollama_unload_ms` separately.
2. **A/B prosody (future, explicit approval):** compare same text with (a) current params, (b) lower temperature, (c) any s2.cpp speed-related flag if documented — plus English vs Polish text on `elian_jarvis`. Do **not** change defaults until measured.
3. **Capture a true web baseline** (model id, voice id/profile, speed, sample rate, WAV) before claiming parity.
4. Optional UX: keep persistent Fish session warmer across turns (trade VRAM vs cold 9–10 s) — protected/lifecycle surface; needs explicit design, not a silent tweak.

---

## Chain map (verified)

```text
ConversationManager / pipeline
  → voice.tts_engine.get_tts_engine() → RoutedTTS
    → FishAudioTTS.speak
      → fish_s2_backend.synthesize_s2_wav
        → begin_fish_tts_session (exclusive unload Ollama + s2 --server cold)
        → FishS2ServerWorker.generate_to_file (multipart /generate)
        → WAV @ 44100 mono float
      → Voice.speak_from_wav
        → audio_gate.begin_tts / wait_inputs_idle
        → sf.read → _fit_playback_rate (may resample; not this run)
        → sounddevice.play @ output rate → sd.wait
        → TTS_COOLDOWN 0.35 s
```

---

## Terminal summary fields

See following assistant message.
