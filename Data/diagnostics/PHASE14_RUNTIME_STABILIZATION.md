# Phase 14 — Runtime & Hardware Stabilization

Date: 2026-09-07
Version: 2.0.0-rc1

## Root cause (unexpected exit)

**PortAudio / sounddevice race during barge-in**, not intentional RC `sys.exit`.

While TTS played (`sd.play`), barge monitor opened a concurrent `InputStream`, then called `sd.stop()` from the barge thread before the input stream closed; main then opened listen InputStream. Native PortAudio abort → process returns to PowerShell with **no** `runtime_crash_*.json` (matches campaign HIGH notes for `gpu_coexistence` / `natural_speech`).

## Software fixes

- `voice/audio_gate.py` — serialize PortAudio accounting; max one input / one TTS
- `Voice.py` — signal-only interrupt from barge; TTS owner owns `sd.stop()`; Piper/STT failure classification
- `voice/pipeline.py` — barge barrier before listen; INTERRUPTED must leave SPEAKING
- `core/crash_log.py` — full Phase 14 snapshots, heartbeat, faulthandler, thread/asyncio hooks
- `core/rc_validation/runner.py` — install crash hooks in RC
- `core/clarification_policy.py` + `core/planner.py` + `core/fast_path.py` — natural APP/MEDIA routing

## Gates

| Gate | Result |
|------|--------|
| unittest | PASS (full discover) |
| agent-smoke | **11/11 PASS** |
| barge stress (offline) | **50 cycles PASS** — no abort, no duplicate streams |
| hardware 11 targeted | **BLOCKED** (Ollama + HA unavailable) |

## Campaign counts (honest)

- PASS: 9
- PARTIAL: 3
- SKIP: 1
- BLOCKED: 11
- FAIL: 0 (prior FAILs superseded by BLOCKED retest stamps — **not** hardware PASS)

`unexpected_exit_pending_review` = **True** (kept until live barge/gpu proof)

## Release

**NOT READY** for full 24-test hardware RC until Ollama/HA are up and the 11 BLOCKED tests are re-run on mic.
