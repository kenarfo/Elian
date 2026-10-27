# P3 Live RC — collaborative notes

Protocol: user speaks; grader reads console; verdict after each turn; fixes AFTER full campaign.

Statuses: PASS | PARTIAL | FAIL | BLOCKED | NOT_TESTED

---

## Session start (pre-notes)

- RC running: `python main.py --rc-validation` (terminal 11)
- Piper TTS: OK (first reply heard)
- BARGE_MONITOR: **FAIL/DEGRADED** — PaErrorCode -9999 WDM-KS Blocking API
- Vizzini MQTT: spam „Nie znaleziono lampy Vizzini przez Bluetooth” (noise, not graded as each test)

### T0 — cold TTS hello
- Spoken (system): intro TTS
- Result: SPEAKING → ACTIVE_LISTENING
- Verdict: **PASS** (TTS path)

### T1 — ambient after hello
- STT: `Dziękuje za oglądanie.`
- Route: `ambient_bleed` silent
- Verdict: **PASS** (correct ambient discard) — unless user intended something else

### T2–T6 — wake attempts while session / idle
| # | STT | Route | Notes |
|---|-----|-------|-------|
| T2 | `Eliam.` | ambient_continue, no TTS | FAIL wake recognition |
| T3 | `Hej Eljan` → corr `Hej Elian` | ambient_continue, no TTS | FAIL — wake not acted |
| T4 | `Hej, Eljan!` → `Hej, Elian!` | ambient_continue | FAIL same |
| T5 | `Hej Elian!` | ambient_continue | FAIL — correct STT, no wake/session ACK |
| T6 | `Hej Elian!` | ambient_continue | FAIL same |
| T7 | empty / idle | idle_timeout → IDLE | session ended |
| T8 | wake IDLE: `Happy birthday...` | no wake | FAIL / ambient |
| T9 | `KONIEC!` | no wake | — |
| T10 | empty STT | — | — |

**WAKE layer so far: FAIL** — even exact `Hej Elian!` does not activate session reply when already listening; after idle, STT wake also not engaging on clear phrases (user may have been testing).

Current state when notes started: WAKE_LISTENING / IDLE after timeout.

---

## Live turns (fill as we go)

### L1 — casual after wake
- STT: `Co tam`
- Semantic: TALK/social_smalltalk, agent
- TTS: `Spokojnie. Jestem, słucham.`
- Latency total ~2.9s
- Verdict: **PASS** (CASUAL)

### L2 — chat signal bleed / accidental
- STT: `G. Elian` (user typed `g` to grader; mic also heard)
- TTS: `Jestem, słucham.`
- Verdict: **N/A** (nie test — sygnał do mnie, nie komenda)
### L3 — ambient after L2
- STT: `Dzięki za oglądanie!`
- Route: ambient_bleed silent
- Verdict: **PASS** (bleed discard)
- Then: idle_timeout → IDLE / WAKE_LISTENING

### L4 — light → knowledge context switch (FAIL chain)
Prior theme in agent goal: `ale dalej tu ciemno` / clarify light.

| Turn | STT | Expected | Actual | Verdict |
|------|-----|----------|--------|---------|
| L4a | (prior) ciemno / light | ACT or bind light | clarify `Które światło…` / earlier `Już jest tak ustawione` | **PARTIAL/FAIL** — UNNECESSARY_ASK or false settle (HA?) |
| L4b | `Co to znaczy mieć arachnofobię?` | ANSWER knowledge | semantic OK INFORMATION_REQUEST but agent **SMART_HOME clarify** again `Które światło…` | **FAIL** — CONTEXT / ACTIVE_GOAL / LOST_GOAL |
| L4c | `Co to jest?` | ANSWER | `clarify_limit` → `Nie udało mi się ustalić. Spróbuj od nowa.` | **FAIL** — clarify_limit ate knowledge Q |

### L21 — fotosynteza knowledge
- STT: `Wyjaśni co to fotosynteza.`
- Agent crash: `IntentFamily` unbound → chat fallback
- TTS: correct photosynthesis explanation (no MJ)
- Verdict: **PARTIAL** — answer OK; AGENT crash smell

### L22 — media now playing
- STT: `Co teraz leci?`
- soft media_now / get_now_playing
- Honest: brak sesji SMTC (no false track; no Spotify close)
- Verdict: **PASS** (honest OBSERVE; Spotify protected)

### L23 — włącz lampę
- STT: `Łącz lampę` → ASR repair włącz
- turn_on_device light.vizzini VERIFY SUCCESS
- Then agent extra set_device_value {} ×2 → fail → TTS `Nie udało mi się przejść dalej.`
- Verdict: **FAIL** — BAD_REPAIR / false failure after verified success (light IS on)

## Collaborative short finish: DONE
Next: user says stop OR barge optional; then fix batch from notes.

---

## Fix batch landed (2026-09-12, offline) — NOT a mic FULL PASS

| Item | Status |
|------|--------|
| Sticky MJ / topic poison | FIXED offline |
| Stop plan after verified HA success | FIXED offline |
| IntentFamily unbound | FIXED |
| Clarify eats knowledge | FIXED offline |
| Barge PaError -9999 | PARTIAL (MME barge device + log backoff) |
| wyłącz ją SMART_HOME | FIXED offline |
| tiszej → volume | FIXED offline |
| Idle timeout | PARTIAL (default 90s) |
| TTS awkward / web promise | PARTIAL |
| HA latency / Piper reload | SKIPPED |
| no to zrób after cancel → ASK | FIXED offline |
| Vizzini BLE spam backoff | FIXED |
| Caps HA DEGRADED speech | SKIPPED |
| Live mic retest | REMAINING |

Gates: unittest **1397 OK**, agent-smoke **11/11**. Restart RC to retest.

<!-- continue -->
