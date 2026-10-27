# Elian Jarvis stabilization — session report

| Field | Value |
|------|--------|
| **Date** | 2026-10-16 |
| **Scope** | Audit → top 5 → fixes → gates → Jarvis path proof |
| **Jarvis path** | Live wake STT + L2 Piper→Whisper `High LN, volume up.` → `volume_up` → TTS **Zwiększam głośność.** |
| **Doctor** | READY WITH OPTIONAL FEATURES UNAVAILABLE (HA/STT/TTS/Wake/Ollama READY) |

## ETAP 1 — Architektura (audyt)

Ścieżka: Wake (`legacy_stt` / opc. onnx) → STT (Whisper CUDA) → `ConversationManager.handle` (Semantic → Cognitive → ActionLoop soft → Agent/Planner → FastPath ≥0.90 → legacy → Brain) → RoutedTTS (Fish S2 → Piper).

Klucz: `core/conversation_manager.py`, `Voice.py` + `voice/*`, `WakeWord.py`, `core/tools.py`, `HomeAssistant.py`. Lokalnie-only (Ollama, HA LAN).

## ETAP 2 — Top 5 problemów

| # | Problem | Przyczyna | Kolejność |
|---|---------|-----------|-----------|
| 1 | PortAudio -9996/-9985 | stale device index / race na InputStream | 1 |
| 2 | TTS nie z RoutedTTS / barge stop | `main.speak` vs Fish; PortAudio stop z wątku barge | 2 |
| 3 | VRAM Fish↔Ollama | brak idle unload / keep_alive | 3 |
| 4 | Wake EN + ASR slips | brak `hi` / `higher lane` / **`High LN`** | 4 |
| 5 | HA sync thrash | empty registry refresh na hot path | 5 |

Dodatkowe (sesja): flood `barge_or_stop` JSON; barge blocking na WDM-KS → -9999; Windows capture mute; Fish multi-s2 hang.

## ETAP 3 — Naprawy

| Area | Change |
|------|--------|
| PortAudio | Re-resolve mic by name on -9996; no crash on -9985; AudioGate; wake under gate |
| TTS | `main.speak` → RoutedTTS; barge `force_portaudio_stop` forwarded |
| GPU | Fish idle 45s; Ollama `keep_alive=0` exclusive; end Fish before Ollama |
| HA | Hot-path cache + bg refresh; states timeout 2.5s; fail cooldown 30s |
| Wake EN | `hi` / `Hi Elian` / ASR slips `higher lane|lean` + **`High LN` / `hi ln`** / `Hey, Alien` |
| Soft goals EN | Room-dark → brightness; `turn up the volume` ≠ lights |
| Diagnostics | Rate-limit `barge*` / `recover_*` disk persist (30s); heartbeat always |
| Barge | Callback on primary STT mic first; fallback DirectSound/MME |
| Sim L2 | Wake strip before CM inject (parity z produkcją) |
| Degraded sticky | `clear_degraded_subsystem("tts")` after successful RoutedTTS / Piper speak |
| Mic mute | Detect Windows capture mute (pycaw); `auto_unmute_mic` on init/listen |
| Fish s2 | Kill orphans before bind (no port-bump stacking) |
| Speech gate | Energy fallback `max(0.012, thr*2)` for close-talk ~0.020 |
| Self-improve test | `AdoptionTests` → `EVIDENCE_SEED` (runtime log dilutes scheduling under `all`) |

## ETAP 4 — Dowody / testy

| Gate | Result |
|------|--------|
| `python -m unittest discover -s tests -p "test_*.py"` | **1770 OK** (~116s) |
| `python main.py --agent-smoke` | **11/11 PASS** |
| Live spoken wake | `spoken_wake_rc_20261016T174403.json`: STT `Hi, Elian.` → **Listening.** TTS OK |
| Replay live STT + tool | `replay_live_stt_wake_tool_latest.json`: wake + `volume_up` → **Głośniej.** |
| L2 simulated voice (not dry) | `spoken_l2_sim_latest.json`: Piper→Whisper `High LN, volume up.` → wake strip → `volume_up` → TTS **Zwiększam głośność.** (`L2_WAKE_PLUS_TOOL_TTS`) |
| PortAudio open stress | 5/5 #47 WDM-KS, 0× -9996/-9985 |
| `python main.py --doctor` | Overall READY; HA `http://192.168.0.31` READY; STT cuda; TTS READY |
| Acoustic / VoiceWave loopback | portaudio_open_ok; rms≈0 (brak sidetone / capture_too_quiet) — nie HARDWARE room PASS |

## ETAP 5 — Następny etap

1. Opcjonalny RC na micu (gdy użytkownik przy boomie):
   `python Data/diagnostics/spoken_wake_rc.py --wait 30` → **Hi Elian, volume up** → `WAKE_PLUS_TOOL`.
2. Po zgodzie: wyczyść stare `runtime_snapshot_*barge*` (~3.5k).
3. Opcjonalnie: custom `elian.onnx` (dziś legacy STT wake).

## Completion audit (vs objective)

| Requirement | Evidence | Status |
|-------------|----------|--------|
| ETAP 1–5 + raport | this file | OK |
| PortAudio -9996/-9985 | recovery + open stress 5/5 | OK |
| Wake→STT→TTS live | `spoken_wake_rc_*` Hi Elian → Listening | OK |
| Wake→STT→tools→TTS (full phrase) | L2 `spoken_l2_sim_latest.json` High LN→volume_up→TTS | OK |
| Live STT + session tool | replay `LIVE_STT_WAKE_PLUS_SESSION_TOOL` | OK |
| HA / local-only | doctor HA READY; no cloud APIs added | OK |
| Unittest / smoke | 1770 OK; agent-smoke 11/11 | OK |
| One continuous human spoken take | optional residual (mic silent in later windows) | residual |

## Still residual (not blockers for stages 1–5)

- Optional: one continuous spoken take on boom mic
- Config doctor: non-blocking DEGRADED warnings
- ~3.5k legacy barge snapshot files (clean only with user consent)

## Changed files (session)

`Voice.py`, `voice/audio_gate.py`, `voice/wake.py`, `voice/tts_engine.py`, `voice/pipeline.py`, `voice/fish_s2_backend.py`, `voice/fish_s2_worker.py`, `voice/mic_mute.py`, `main.py`, `WakeWord.py`, `HomeAssistant.py`, `Config.py`, `Data/config.json`, `core/model_client.py`, `core/ollama_lifecycle.py`, `core/text_inject.py`, `core/intent_family.py`, `core/utterance_class.py`, `core/soft_goals.py`, `core/tool_compat.py`, `core/crash_log.py`, `core/simulated_voice.py`, `tests/test_self_improve.py`, tests (`test_mic_mute.py`, `test_speech_gate_energy.py`, `test_fish_s2_worker.py`, `test_portaudio_*`, `test_phase14_*`, `test_core.py`, …), `Data/diagnostics/spoken_wake_rc.py`, `live_wake_tool_probe.py`, `replay_live_stt_wake_tool.py`, `acoustic_loopback_jarvis.py`, `virtual_loopback_tool_rc.py`

## Final summary

### Checked
Architecture (CM → Agent/FastPath → tools → RoutedTTS); PortAudio devices/streams; Fish/Piper GPU; HA; wake ASR slips; Windows mic mute; Fish s2 orphans; diagnostics flood; live mic wake; L2 full phrase; unittest/smoke.

### Fixed
PortAudio -9996/-9985 recovery; AudioGate; RoutedTTS wiring; Fish idle/Ollama handoff; HA non-blocking refresh; EN wake + ASR slips; mic auto-unmute; speech-gate close-talk; barge callback; Fish s2 orphan sweep; snapshot rate-limit; sticky TTS degraded clear; AdoptionTests seed isolation.

### Tests
`unittest` **1770 OK**; `agent-smoke` **11/11**; doctor **READY**; live wake→Listening; L2 + replay → `volume_up` → short Polish TTS.

### Still not verified (optional)
Single continuous human spoken take on boom mic (headset loopback too quiet for acoustic HARDWARE PASS).

### Next step
```bash
python Data/diagnostics/spoken_wake_rc.py --wait 30
```
Boom mic: **Hi Elian, volume up** → expect `pass_level=WAKE_PLUS_TOOL`.
