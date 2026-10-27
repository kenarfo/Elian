# ELIAN 2.0 RC VALIDATION — MERGED CAMPAIGN

Updated: 2026-09-07 (Phase 14 runtime stabilization)
Campaign ID: `merged_20260825T104829`
Elian version: 2.0.0-rc1

## Counts

- PASS: 9
- PARTIAL: 3
- FAIL: 0
- SKIP: 1
- BLOCKED: 11
- UNAVAILABLE: 0
- PENDING: 0
- Total catalog: 24

## Phase 14 note

- Root cause unexpected exit: PortAudio barge race (software fixed).
- 11 prior FAILs retested as BLOCKED (Ollama/HA unavailable) — not fake PASS.
- unexpected_exit_pending_review: True
- Release: **NOT READY**

## Test matrix (current status)

| ID | Status | Note |
|----|--------|------|
| `wake_word` | **PARTIAL** | legacy STT wake; custom elian.onnx not installed |
| `wake_false_positive` | **PASS** |  |
| `stt_phrases` | **PARTIAL** |  |
| `natural_speech` | **BLOCKED** | Phase14 retest 2026-09-07: BLOCKED — mic RC not run (Ollama down). Offline contr |
| `fastpath_volume` | **PASS** |  |
| `agent_multi` | **PASS** |  |
| `memory_preference` | **BLOCKED** | Phase14 retest 2026-09-07: BLOCKED — deferred persistence/restart path; Ollama u |
| `context_pronoun` | **PASS** |  |
| `wait_phrase` | **BLOCKED** | Phase14 retest 2026-09-07: Ollama UNAVAILABLE + HA url/token UNAVAILABLE — hardw |
| `correction` | **PASS** |  |
| `negation` | **PASS** |  |
| `hypothetical` | **BLOCKED** | Phase14 retest 2026-09-07: Ollama UNAVAILABLE + HA url/token UNAVAILABLE — hardw |
| `reminder_real` | **BLOCKED** | Phase14 retest 2026-09-07: Ollama UNAVAILABLE + HA url/token UNAVAILABLE — hardw |
| `calendar_local` | **BLOCKED** | Phase14 retest 2026-09-07: Ollama UNAVAILABLE + HA url/token UNAVAILABLE — hardw |
| `media_controls` | **BLOCKED** | Phase14 retest 2026-09-07: Ollama UNAVAILABLE + HA url/token UNAVAILABLE — hardw |
| `ha_basic` | **BLOCKED** | Phase14 retest 2026-09-07: Ollama UNAVAILABLE + HA url/token UNAVAILABLE — hardw |
| `ha_followup` | **PASS** |  |
| `vision_screen` | **BLOCKED** | Phase14 retest 2026-09-07: BLOCKED — vision needs Ollama vision model. Offline o |
| `vision_risk` | **BLOCKED** | Phase14 retest 2026-09-07: BLOCKED — vision needs Ollama vision model. Offline o |
| `barge_in` | **PASS** |  |
| `session_timeout` | **PARTIAL** |  |
| `proactive_battery` | **SKIP** | desktop PC — no battery |
| `idle_stability` | **PASS** |  |
| `gpu_coexistence` | **BLOCKED** | Phase14 retest 2026-09-07: BLOCKED — requires live Whisper+Ollama(+vision) GPU c |

## Blockers

- Unexpected process exit — pending manual review
- gpu_coexistence PARTIAL: Whisper + text model działały bez OOM, ale vision request nie uruchomił modelu vision przez błędny routing/pending clarification, więc współistnienie Whisper + text + vision nie zostało realnie zweryfikowane
- HIGH BUG: Elian/RC zakończył proces podczas gpu_coexistence bez zamierzonego Ctrl+C
- HIGH BUG: RC/Elian samoczynnie kończy proces po barge-in; wystąpiło co najmniej podczas gpu_coexistence i natural_speech
- Previous RC session ended unexpectedly (session_id=session_20260825T112717_d7996f). Manual review required.
- Previous RC session ended unexpectedly (session_id=session_20260825T115531_9440c4). Manual review required.
- Previous RC session ended unexpectedly (session_id=session_20260825T122450_ec0ae2). Manual review required.
- Previous RC session ended unexpectedly (session_id=session_20260825T172259_997c5c). Manual review required.
- Previous RC session ended unexpectedly (session_id=session_20260825T172455_01ece7). Manual review required.
- Previous RC session ended unexpectedly (session_id=session_20260825T172753_d6e548). Manual review required.
- Previous RC session ended unexpectedly (session_id=session_20260825T180344_e8b00b). Manual review required.
- Previous RC session ended unexpectedly (session_id=session_20260825T225611_49ea92). Manual review required.
- Previous RC session ended unexpectedly (session_id=session_20260827T124257_7e3151). Manual review required.
- Previous RC session ended unexpectedly (session_id=session_20260827T165400_007e3e). Manual review required.
- Previous RC session ended unexpectedly (session_id=session_20260901T092332_b4ac1b). Manual review required.
