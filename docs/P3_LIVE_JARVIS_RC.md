# P3 — LIVE JARVIS RC

Elian **2.0.0-rc1** · date **2026-09-12** · operator: automated agent (no interactive human mic speaker)

Honest rule: offline green ≠ live PASS. Mic / barge-in / soak / novel spoken dialogues that require a human at the headset are **BLOCKED / NOT_TESTED**, not mock PASS.

---

## SOFTWARE BASELINE

| Gate | Result |
|------|--------|
| unittest | **1397 OK** (0F/0E) — +9 live P0 regressions |
| agent-smoke | **11/11 PASS** |
| jarvis-behavioral --critical | prior **38/38** (not re-run this fix batch) |
| P2 regression | prior FULL PASS; live soft/topic/verify fixes added |

---

## ENVIRONMENT

| Component | Status | Notes |
|-----------|--------|-------|
| mic | **AVAILABLE** | Plantronics Blackwire 3220 (config + WDM-KS #44). No human operator for spoken RC. |
| Whisper | **AVAILABLE** | `large-v3-turbo` / CUDA — loaded during voice init (“Whisper gotowy”) |
| Piper | **AVAILABLE** | Live TTS spoke “Test P3. Elian mówi.” on Plantronics output |
| Ollama | **AVAILABLE** | `llama3.1:8b` (+ others); live ask_ai ~0.55s |
| HA | **DEGRADED / BLOCKED for live control** | Host `http://192.168.0.31` up (HTTP 401). **`smart_home.token` missing** → no verified on/off |
| Spotify/media | **NOT_RUNNING** | Safe; `ELIAN_PROTECT_USER_APPS=1` used. No destructive media actions. |
| Browser / Playwright | **AVAILABLE** | Live Chromium steps PASS; capability label asserts FAIL (see BROWSER) |
| HUD | **AVAILABLE** | `--hud-smoke` PASS (offscreen); visual RC not done |
| Companion | **AVAILABLE (opt-in smoke)** | `--companion-smoke` 11/11; default `companion.enabled` stays false |
| GPU | **AVAILABLE** | RTX 5070 12.8 GB, CUDA; Whisper on CUDA |

Artifacts: `Data/diagnostics/p3_preflight_inventory.py`, `p3_live_probes.json`.

---

## FULL RC (`python main.py --rc-validation`)

| | |
|--|--|
| Status | **BLOCKED** |
| Reason | Interactive mic campaign requires a human speaking wake/phrases; automated agent cannot honestly complete the 24-test hardware matrix. Legacy campaign (`merged_20260825T104829`) remains **in_progress**: PASS 9 / PARTIAL 3 / BLOCKED 11 / SKIP 1 — stale (last update 2026-09-07). |
| PASS | _(not re-run live this session)_ |
| PARTIAL | _(legacy campaign only)_ |
| FAIL | _(not newly marked)_ |
| BLOCKED | Full catalog mic RC |
| NOT TESTED | Fresh per-test traces (STT/semantic/goal/TTS/session/latency) |

Do **not** treat `rc inject` as mic PASS (bypasses wake/VAD/STT).

---

## LIVE NATURAL SPEECH

**BLOCKED / NOT_TESTED** — no human Polish utterances into mic.

Text-pipeline substitutes (not mic PASS):

- Soft web search + open first (when DDG returns hits)
- TTS Piper playback PASS
- Whisper model load PASS (no live utterance scored)

---

## LIVE MULTI-TURN

**PARTIAL** (text CM, not mic):

| Trace | Result | Layer |
|-------|--------|-------|
| search → otwórz pierwszy | PASS when DDG has results | WEB / soft open_result |
| streść to krótko | PARTIAL — often re-searches polluted query; answer may be ungrounded (`grounding_status=INSUFFICIENT`) | PLANNER / WEB |
| a drugi (before fix) | FAIL → `click_ui_element` / polluted web_search | CONTEXT / soft_goals / talk-lock |
| a drugi (after fix, injected results) | PASS → `open_website` second URL | soft_goals + action_loop |

≥20 spoken dialogues: **NOT_TESTED**.

---

## BARGE-IN

**BLOCKED / NOT_TESTED** — requires live TTS interrupt with mic. Prior campaign claimed barge_in PASS (stale); not re-proven.

---

## GPU COEXISTENCE

**PARTIAL / NOT_TESTED full**

- Whisper CUDA + Piper + Ollama observed coexisting in short probes without crash.
- 15–20 turn mic GPU soak: **NOT_TESTED**.
- Legacy campaign `gpu_coexistence`: BLOCKED / unexpected-exit history — not cleared this session.

---

## HOME ASSISTANT

**BLOCKED** — dependency: missing `smart_home.token`. Host reachable; no live on/off/brightness/pronoun with state verify.

---

## MEDIA

**NOT_TESTED** (destructive) — Spotify not running; no play/pause/skip performed. Status-only preference respected. User voice “zamknij Spotify” outside automated mode not mic-tested.

---

## WEB

**PARTIAL**

- Live DuckDuckGo `web_search` **PASS** when provider returns hits (Home Assistant / Piper queries).
- Soft reply sometimes FALLBACK (“Mam wyniki… streścił”) instead of immediate synthesis.
- Multi-turn “a drugi” fixed when `last_web_results` present.
- DDG intermittently returns 0 results → honest retryable failure (not false success).
- WEB_SEARCH ≠ BROWSER_AUTOMATION confirmed (`BROWSER_AUTOMATION=DISABLED` by default).

---

## BROWSER

**PARTIAL**

- `--browser-agent-smoke`: **26/28** — all live Playwright Chromium steps PASS; clean shutdown / no orphans.
- FAIL ×2: `capability_experimental_when_opt_in` (got READY), `capability_unavailable_by_default` (got DISABLED/Playwright ready overlay) — registry overlay vs map EXPERIMENTAL/UNAVAILABLE labels. Functional automation OK; status vocabulary drift.
- FakeBackend ≠ live PASS; live Chromium exercised.

---

## HUD

**PARTIAL**

- `--hud-smoke`: **PASS (offscreen)**
- Visual IDLE/LISTENING/… states: **MANUAL_VISUAL_REQUIRED / NOT_TESTED**

---

## COMPANION

**PASS** (smoke) — temporary opt-in: 11/11; default remains disabled.

---

## MEMORY RESTART

**PARTIAL**

- Fact add + cleanup: PASS (`add_memory` / store scrub).
- Preference set (`p3_rc_test_pref=herbata`) + forget API: exercised; full process restart remember/forget cycle: **NOT_TESTED**.
- Temp context wrongly persistent: not fully audited.

---

## REMINDERS/CALENDAR

**NOT_TESTED** live create/fire/cancel/restart (avoid touching real user data without isolated store harness this session). Offline unit coverage remains in suite.

---

## PROACTIVITY

**PASS** (dry transport)

- Injected `BATTERY_LOW` 15% + calendar 25 min → SituationEngine→Attention→Policy → one SUGGEST notify (dry `speak_fn`).
- Immediate re-inject → `situation_cooldown` suppressed (no spam).
- No live TTS spam.

---

## SOAK

| | |
|--|--|
| duration | **NOT_TESTED** (30–60 min not run) |
| crashes | none in short probes |
| deadlocks | listen() can block indefinitely without speech timeout — risk noted (probe killed) |
| resource issues | short process snapshot only (not meaningful full-session) |

---

## FALSE SUCCESS FOUND

- Soft web success with FALLBACK spoken line (“Mam wyniki…”) while tool already had 5 results — weak UX, not claiming device success without verify.
- Summarize path answered Piper facts with `grounding_status=INSUFFICIENT` — answer may be model prior, not web-grounded (**FALSE_SUCCESS risk for knowledge claims**).

---

## FALSE "I CAN'T" FOUND

- When DDG empty: honest “Nie udało mi się teraz znaleźć wyników” — OK.
- Before fix: “a drugi” → missing `element_id` / false can’t-find — **FALSE can't** for ordinal web follow-up (**fixed**).

---

## NEW LIVE REGRESSIONS

| ID | Layer | Status |
|----|-------|--------|
| WEB ordinal “a drugi” under TALK lock | CONTEXT / soft_goals / action_loop talk-lock | **FIXED** (minimal) |
| Sticky MJ / topic poison after cancel + knowledge | FOCUS / enrich_goal / cancel clear | **FIXED** (offline) — mic retest needed |
| False failure after HA VERIFY SUCCESS (L23) | PLANNER stop_after_verified | **FIXED** (offline) |
| IntentFamily unbound on KNOWLEDGE deterministic | PLANNER local import shadow | **FIXED** |
| Clarify light eats INFORMATION_REQUEST (L4) | CM pending / clarify_limit | **FIXED** (offline) |
| Barge PaError -9999 WDM-KS Blocking | BARGE MME sibling + rate-limit spam | **PARTIAL** — needs live barge retest |
| “streść to krótko” re-searches | PLANNER / WEB | **OPEN** |
| Browser capability EXPERIMENTAL/UNAVAILABLE labels | CAPABILITY | **OPEN** (smoke 2 fails) |
| HA token missing | INTEGRATION | **BLOCKED** / env |
| Mic RC / barge-in / soak / novel speech | WAKE/STT/TTS/BARGE_IN | **BLOCKED** until human retest |

---

## FILES CHANGED (Jarvis live fix batch 2026-09-12)

- `core/conversation_focus.py` — new-topic knowledge vs comprehension repair; clear topic poison; enrich skip Osoby
- `core/conversation_manager.py` — cancel/knowledge clear focus; clarify vs INFORMATION_REQUEST
- `core/planner.py` — IntentFamily unbound fix; stop after verified success; no false “Nie udało…”
- `core/semantic_executive.py` — `wyłącz ją` → SMART_HOME not APP close
- `core/soft_goals.py` — `tiszej` volume; ask_goal after cancel
- `core/action_loop.py` / `core/user_goal.py` — ask_goal soft
- `voice/pipeline.py` / `Voice.py` — barge Blocking API device + spam backoff
- `Config.py` — idle timeout 90s
- `core/ha_speech.py` / `core/response_finalize.py` — less awkward TTS / honest web fallback
- `HomeAssistantMqttBridge.py` — BLE not-found backoff
- `tests/test_p3_live_jarvis_fixes.py` — live P0 regressions
- `docs/P3_LIVE_JARVIS_RC.md` — this update
- `Data/diagnostics/p3_live_rc_notes.md` — fix batch note

No Planner-core rewrite. No Spotify/user-app closes. No FULL LIVE PASS claim.

---

## P2 / OFFLINE GATES AFTER LIVE FIXES

- unittest: **1397 OK**
- agent-smoke: **11/11**
- `--jarvis-behavioral --critical`: not re-run this batch (optional)

---

## FINAL VERDICT

# **PARTIAL** (fixes landed offline; mic retest required)

### Can the user sit down and use Elian as Jarvis for a long session?

**Not re-proven live.** Collaborative mic RC earlier found real FAILs; this batch addresses those layers in software. Restart `python main.py --rc-validation` with a human at the headset before claiming LIVE PASS.

**Remaining (out of scope this pass):** full goal-store merge, Memory 2.0, BrowserAgent rewrite, hour soak, acoustic wake model, full capability consolidation, HA DEGRADED speech polish when turn_on works, Piper reload latency.
