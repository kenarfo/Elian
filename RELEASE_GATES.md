# Release gates

Software gates are not hardware PASS.

## Phase 14 (2026-09-07) — Runtime & Hardware Stabilization

- `python -m unittest discover -s tests -p "test_*.py"` — **PASS** (includes Phase 14 barge/runtime suite)
- `python main.py --agent-smoke` — **11/11 PASS**
- Offline barge stress (≥50 cycles) — **PASS** (no process exit, no duplicate STT/TTS)
- Hardware 11-target retest — **BLOCKED** (Ollama + HA unavailable); campaign history preserved
- `unexpected_exit_pending_review` — **still True** until live barge/gpu proof
- See `Data/diagnostics/PHASE14_RUNTIME_STABILIZATION.md`

## Locked software baseline (C.5.5 + foreground-smoke fix)

- `python -m unittest discover -s tests -p "test_*.py"` — **840 OK** after Phase 1 (was 827); Phase 14 suite grows this count
- `python main.py --behavioral-eval` — **1200 / 1200**
- `python main.py --foreground-smoke --mock` — PASS
- `python main.py --ha-action-smoke` — required green
- `python main.py --agent-smoke` — required green

Never weaken an existing test to land a new feature.

## Master-build additions (Phase 0–1)

- WorldState observations: CURRENT / STALE / UNKNOWN / UNAVAILABLE
- Salience adapter metric: `irrelevant_context_injection_rate` (target 0 on deterministic talk fixtures)
- CapabilityMap honest UNAVAILABLE/DISABLED for Browser / Camera / MR
- DecisionEnvelope discourse flags (does not replace existing modes in the 1200 eval)

## Eval expansion (not this run)

C.5.5 lock remains **1200/1200**. Master-build targets are still open:

- 2000 deterministic semantic (gap vs today’s 1200 single-turn mix)
- 1000 poison (today **550** = 250 fixed + 300 random)
- 100 multi-turn scenarios (today **32** in `collaboration_eval.py`)

Do not inflate counts with duplicates. Do not weaken pass-1 contracts to hit the number.

## Hardware

Live `--foreground-smoke` is interactive and read-only. Do not mark global hardware PASS from mocks.

Camera, presence, and Playwright remain off until an explicit later phase with user opt-in.

**Phase 14:** full 24-test hardware RC is **NOT READY** while 11 targets remain BLOCKED and unexpected-exit review stays open.
