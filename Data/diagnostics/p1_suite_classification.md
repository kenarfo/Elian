# P1 Full Red Suite Classification (2.0.0-rc1)

**Baseline:** Ran 1377 tests in 66.202s — FAILED (failures=16, errors=4)
**agent-smoke:** 11/11 PASS (prior)
**Date:** 2026-09-11
**Note:** Earlier audit assumed Windows memory.json lock for 4 ERRORs — **disproven**. All 4 share `AttributeError: main.VoiceSettings`.

---

## ERRORS (4)

| TEST | SUBSYSTEM | EXPECTED | ACTUAL | ROOT CAUSE | CLASSIFICATION | OWNER FILES | SAFE TO FIX? |
|------|-----------|----------|--------|------------|----------------|-------------|--------------|
| `ConversationFlowTests.test_command_after_wake_word_runs_immediately` | main/voice bootstrap | main.start() under VoiceSettings patch | `AttributeError: main.VoiceSettings` | Tests patch `main.VoiceSettings.from_config`; `main.start()` uses `get_voice_settings()` from Voice — VoiceSettings not re-exported on main | INTEGRATION_MISMATCH | `tests/test_core.py`, `main.py`, `Voice.py` | YES — patch `main.get_voice_settings` (test aligns to architecture) |
| `ConversationFlowTests.test_natural_request_uses_ai_intent_before_chat` | same | same | same | same | INTEGRATION_MISMATCH | same | YES |
| `ConversationFlowTests.test_silence_returns_to_standby_after_idle_timeout` | same | same | same | same | INTEGRATION_MISMATCH | same | YES |
| `NaturalConversationTests.test_wait_phrase_keeps_conversation_active_in_main_loop` | same | same | same | same | INTEGRATION_MISMATCH | same | YES |

**Shared root cause:** YES — all 4 are VoiceSettings patch path, **not** memory.json lock / persistence.

---

## FAILURES (16)

| TEST | SUBSYSTEM | EXPECTED | ACTUAL | ROOT CAUSE | CLASSIFICATION | OWNER FILES | SAFE TO FIX? |
|------|-----------|----------|--------|------------|----------------|-------------|--------------|
| `BrowserPlannerIntegrationTests.test_planner_invokes_browser_tools_not_a_second_loop` | Browser / CapGate | `browser_open_url` success via FakeAgent | `available=False` / UNAVAILABLE | CapRegistry DISABLED (opt_in=false) marks tools unavailable before handler; FakeAgent never runs | ARCHITECTURE_CONFLICT | `capability_registry.py`, `tools.py`, `builtin_tools.py` | YES — DISABLED must still allow invoke for honest handler messaging / test FakeAgent; or force AVAILABLE when agent injected |
| `BrowserPlannerIntegrationTests.test_youtube_search_stays_unavailable_without_opt_in` | Browser speech | `"nie umiem"` + unavailable | `"youtube music otwarte, ale reszta nie wyszła."` | Compound partial speech sanitizes browser unavailable away from contract phrase | REAL_CODE_REGRESSION | `jarvis_response.py`, `browser_agent.py`, `response_builder.py` | YES — compound must preserve browser-unavailable honesty |
| `StageBFoundationTests.test_youtube_search_browser_unavailable` | Browser speech | `"nie umiem"` | same partial template | same as above | REAL_CODE_REGRESSION | same | YES |
| `ScenarioTests.test_preference_no_clarification` | Memory → media | `play_media` app=Spotify | app=None | Soft ActionLoop `media_play` bypasses Planner preference inject | REAL_CODE_REGRESSION | `action_loop.py`, `soft_goals.py` | YES — inject `music.default_app` in soft media_play |
| `HardwareRegressionIntegrationTests.test_memory_pref_play_no_set_preference` | Memory → media | app=Spotify | app=None | same soft bypass | REAL_CODE_REGRESSION | same | YES |
| `HardwareRegressionIntegrationTests.test_ciemno_zero_open_app` | Soft brightness | source clarify/agent/deterministic OR “światło” | source=`action_loop`, “rozjaśnienie…” | Soft brightness_up is correct post-P0 path; assertion omits `action_loop` | STALE_TEST_EXPECTATION | `tests/test_natural_language_safety.py` | YES — accept `action_loop` / rozjaśnienie (behavior objectively better; zero open_app already holds) |
| `StaleAckGuardTests.test_meta_then_web_not_okej` | Soft replay / web | web_search + “starmer” | soft `replay_last` steals turn | `_REPLAY_LAST` matches `jeszcze raz` even with new web question after | REAL_CODE_REGRESSION | `soft_goals.py` | YES — replay only when no new contentful question |
| `AgentMultiContractTests.test_partial_spotify_fail_volume_ok` | Compound / verify | fail speech for open + volume OK | `"chwila — sprawdzam…"` | Unmocked `get_volume` → volume verify fail → vacuous finalize | INTEGRATION_MISMATCH | tests + optionally finalize | YES — mock get_volume in test; keep P0 verify |
| `CompoundCloseTests.test_planner_close_then_volume_legacy_zero` | Compound / verify | COMPLETED | PARTIAL | same volume verify without mock | INTEGRATION_MISMATCH | `tests/test_phase13_stage_a.py` | YES — mock get_volume |
| `CompoundCloseTests.test_retry_failed_only_skips_successful_close` | Compound retry | second.success | False | volume verify fail on retry | INTEGRATION_MISMATCH | same | YES |
| `HardwareFix2Tests.test_10_retry_only_failed_close` | Compound retry | volume_calls==1 | 2 | first volume marked failed by verify → retried | INTEGRATION_MISMATCH | same | YES |
| `ClimateControlTests.test_pronoun_climate_off` | Soft / pronoun | climate turn_off | soft light_off clarify | Soft `_LIGHT_OFF` matches “Wyłącz ją” before climate pronoun execute | REAL_CODE_REGRESSION | `soft_goals.py`, `action_loop.py`, `conversation_manager.py` | YES — defer soft light when climate pronoun bound |
| `Phase13C54…test_behavioral_eval_minimum_650` | Semantic | ASK / unclear | ACT / smart_home | `looks_insufficient_light` (`nie`+`widzisz`) before garbled STT ASK | REAL_CODE_REGRESSION | `intent_family.py`, `semantic_executive.py` | YES — garbled STT before implicit light |
| `Phase13C54PartX…test_behavioral_eval_reports_split_counts` | Semantic | same | same | same | REAL_CODE_REGRESSION | same | YES |
| `Phase13C55…test_behavioral_eval_minimum_750` | Semantic | same | same | same | REAL_CODE_REGRESSION | same | YES |
| `InboxHandleTests.test_phone_status_handle` | Phone / CapGate | fast_path + Twilio copy | agent fallthrough | PHONE=DISABLED marks `get_phone_status` unavailable; FastPath can't invoke | ARCHITECTURE_CONFLICT | `capability_registry.py`, `tools.py`, `fast_path.py` | YES — status/read tools must speak when DISABLED |

---

## Cross-cutting

| Theme | Failures | Fix strategy |
|-------|----------|--------------|
| VoiceSettings patch | 4E | Test: patch `get_voice_settings` |
| CapGate blocks handlers | browser invoke, phone status | Prod: DISABLED → invoke for messaging / status tools |
| Soft ActionLoop steals | pref×2, web replay, climate | Prod: preference inject; tighter soft matchers |
| P0 volume verify | compound×4 | Test: mock `get_volume` (do **not** weaken verify) |
| Implicit light FP | eval×3 | Prod: garbled before implicit light |
| Compound browser speech | youtube×2 | Prod: preserve “nie umiem” / disabled copy |
| Spotify user protection | (critical requirement) | Add test-only protected apps + regression |

---

## Explicit non-goals

- Do not weaken P0 volume verify / false-Gotowe / cancel soft-resume.
- Do not restore command-router.
- Do not remove or greenwash tests.
- Do not close user's real Spotify during suite.

---

## FINAL (post-fix)

- unittest: **1384** Ran — **OK** (0 FAIL / 0 ERROR) — +7 Spotify protection tests
- agent-smoke: **11/11 PASS**
- P0 (`test_p0_*.py`): **26 OK**
- Classification file: this doc (pre-fix inventory retained above)
