# RC Regression Retest — Phase 10 Bugfix Pass #1 (+ 10.4)

After applying Agent / Planner / Routing / Conversation State fixes, repeat **only** these hardware RC tests (not all 24).

## Phase 14 (2026-09-07) — Runtime stabilization

| Area | Offline | Hardware |
|------|---------|----------|
| unexpected exit / barge | PortAudio race fixed; 50-cycle stress PASS | **BLOCKED** — needs live mic + Ollama |
| `natural_speech` routing | clarification_policy + FastPath APP/MEDIA PASS | **BLOCKED** |
| 11 targeted FAILs | not flipped to PASS from unittest | all stamped **BLOCKED** with timestamp |
| `unexpected_exit_pending_review` | root cause identified | **kept True** until live proof |

Retest when Ollama + HA are available: the same 11 IDs below — append history only, never wipe campaign.

## Phase 10.5 — AI-first (semantic Agent)

| Phrase | Expected |
|--------|----------|
| `Fajny masz głos.` | conversation / final_answer, zero tools |
| `Nie wiem co robić.` | conversation — **not** „Nie zrozumiałem” |
| `Przydałoby się więcej światła.` / media paraphrases | LLM may clarify or tool — not regex-bound |
| `Otwórz Discord.` / ACK | unchanged deterministic safety |

## Phase 10.4.1 hardware retest (brightness result correctness)

| Phrase | Expected |
|--------|----------|
| `Trochę tu ciemno.` → `Lampę.` | Clarification then `adjust_brightness` (+15%) on resolved light — **not** abstract `brightness_up`. Observation `success=True` only if HA accepted. |
| Unsupported action message `Nie obsługuję…` | `success=False`, `status=unsupported` (never success) |

## Phase 10.4 hardware retest (clarification slots + ACK)

| Phrase | Expected |
|--------|----------|
| `Trochę tu ciemno.` → `Lampę.` | First: light clarification with `missing_slots=["target"]`. Second: slot progress (resolved light or options) — **never** identical „Które światło mam rozjaśnić?” loop |
| `Dziękuję.` | ACK / noop, `tool_calls=0` (not UNKNOWN / play_media) |
| `Dziękuję, otwórz Discord.` | APP → `open_app` Discord (ACK prefix stripped) |

## Must retest (directly affected by fixes)

| RC test ID | Why retest | Expected improvement |
|------------|------------|----------------------|
| `natural_speech` | Clarification repair, pending leak, wake strip, stop/barge cleanup | No YouTube/open_app loop; no process exit after barge-in |
| `agent_multi` | FastPath partial-match guard | Planner multi-step (`open_app` + `volume_up`), not volume-only |
| `context_pronoun` | Canonical light context + follow-up routing | Follow-up targets same entity |
| `ha_followup` | Entity safety (`entity_id` + domain lock) | Brightness/temp on same HA device |
| `wait_phrase` | Pending clarification TTL + wait handling | Session preserved across wait |
| `hypothetical` | Agent contract — knowledge not open_app | Explanation only, zero shutdown |
| `reminder_real` | Agent routes to `create_reminder` | Reminder created, no app clarification |
| `calendar_local` | Text pipeline verb preservation + Agent routing | Full create intent reaches planner |
| `media_controls` | Agent routing + pending leak fix | Media tools, not open_app loop |
| `ha_basic` | Agent + context safety | HA tool calls without wrong-target |
| `vision_screen` | Clarification repair + new-intent during pending | Vision tools run |
| `vision_risk` | Same as vision_screen | NEEDS_CONFIRMATION path |
| `gpu_coexistence` | Vision routing + runtime crash logging | Vision actually runs; process stays alive |
| `barge_in` | Stop clears pending; barge stress test | TTS stops, session alive, no shell exit |

## Optional spot-check (lower priority)

| RC test ID | Notes |
|------------|-------|
| `stt_phrases` | Wake strip cleanup may improve transcripts slightly |
| `session_timeout` | Clarification TTL may interact — verify no regression |
| `idle_stability` | Confirm no new thread leaks after barge stress |

## Do NOT retest for Phase 10

| RC test ID | Reason |
|------------|--------|
| `wake_word` | Acoustic model still missing — unchanged scope |
| `proactive_battery` | Desktop SKIP — unchanged |
| `wake_false_positive` | Already PASS |
| `fastpath_volume` | Already PASS — verify no regression only if time permits |
| `correction` / `negation` | Already PASS |
| `idle_stability` | Already PASS unless gpu_coexistence retest shows issues |
| `memory_preference` | Requires restart persistence path — defer to Phase 10.5 unless quick check desired |

## Retest procedure

1. Use existing campaign: `Data/diagnostics/rc_validation_campaign.json` (do not reset).
2. Run `python main.py --rc-validation` and resume campaign.
3. For each test above, append result to campaign history (`rc pass/fail/...` commands).
4. If process exits unexpectedly, check `Data/diagnostics/runtime_crash_*.json`.
5. After retest, run `python -m core.rc_validation.backfill_campaign` only if manual recovery needed.

## Success signals

- No clarification containing „Czy chcesz otworzyć aplikację?” for non-open intents
- `agent_multi` reaches Agent with both steps
- Light follow-up „ustaw na 50%” never drives climate
- Barge-in / Stop never returns to PowerShell prompt
- `gpu_coexistence` reaches vision model at least once

## Still expected PARTIAL/SKIP after Phase 10

- `wake_word` → PARTIAL (legacy STT, no `elian.onnx`)
- `proactive_battery` → SKIP (desktop)
- `memory_preference` → may remain FAIL until restart-persistence pass
