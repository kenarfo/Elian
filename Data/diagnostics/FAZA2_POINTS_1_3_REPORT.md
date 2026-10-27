# FAZA 2 — Punkty 1–3 COMPLETE (STOP before 4)

| Field | Value |
|------|--------|
| **Date** | 2026-10-05 |
| **Scope** | Self-improve isolation + honest latency + EN/PL weather + hard action_claim |
| **Next** | Do **not** start point 4 until human approval |

## Gates

| Gate | Result |
|------|--------|
| `unittest discover` | **1741 OK** |
| `python main.py --agent-smoke` | **11/11 PASS** |
| `tests.test_fish_s2_tts` + `test_fish_s2_worker` | **15 OK** |
| New regressions (isolation / latency / weather+claim) | **OK** |

## Sample turn telemetry (mocked Fish wall, honest marks)

```
speech_capture_ms ≈ 10
stt_ms ≈ 5
llm_ms = 420
gpu_lease_wait_ms = 3100
fish_worker_startup_ms = 9600
fish_synthesis_ms = 3500
tts_generation_ms = 16200   ← NOT 0
```

## Protected surfaces

Touched **only for latency marks** (no lifecycle/policy/Q4/Q8/voice changes):

- `voice/fish_s2_backend.py` — record `gpu_lease_wait_ms`, `fish_worker_startup_ms`, `fish_synthesis_ms`
- `voice/tts_engine.py` — wrap Fish synth with `tts_gen_*` + record generation
- `Voice.py` — `speak_from_wav` no longer zero-width `tts_gen_*`; playback marks only

Untouched topics: `max_gpu_slots`, exclusive policy semantics, Piper fallback policy, cancel/barge-in, Q4/Q8, `.s2voice`.

## Files changed

See chat report.
