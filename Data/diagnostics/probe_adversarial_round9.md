# Probe adversarial round 9 — rematch after Grocky R8 DONE

Date: 2026-09-10 ~22:10 UTC+1 (Etc/GMT-1). Machine: `a54f5f03-25ab-492d-b5ba-b9eca259a21f` (Ziemniaczek).
Scope: **SOFTWARE rematch** of R8 Grocky FAILs + controls + Memik #3b + smokes. **No production patches from Probe.**
Evidence: `Data/diagnostics/probe_adversarial_round9.json`, `_probe_adversarial_round9.py`, `GROCKY_R8_SEMANTIC_FIX.md`, `natural_pl_probe_round9.txt`, `active_goal_semantics_round9.txt`, `agent_smoke_round9.txt`.

Ownership: **@Grocky** rematch (compound / cancel / pronouns) · **@Memik** #3b still OPEN · **@Probe** this report.

Prior closed (not re-opened): slang, search, Memory ROUTINE, jeszcze raz, no i?, cousins, sticky, S08, Cap cold, volume verify (R7), C, S11/S12, Toolik A+B.

---

## Verdict matrix

| # | Owner | Case | Result | Notes |
|---|-------|------|--------|-------|
| 1 | **@Grocky+@Memik** | `zgaś światło i ciszej` both goals | **PASS** | `turn_off_device` + `volume_down`; domains SYSTEM+SMART_HOME |
| 1b | **@Memik** | CONTROL `wyłącz światło i ciszej` | **PASS** | still both tools |
| 1c | **@Grocky+@Memik** | `włącz lampę i ciszej` | **PASS** | SMART_HOME `turn_on_device` + SYSTEM `volume_down` (not APP/incomplete) |
| 2a | **@Grocky** | `stop`/`stój`/`cisza` mid ActiveGoal | **PASS** | WAIT / `cancel_goal` / `cancel_requested` (was TALK) |
| 2b | **@Grocky** | CONTROL `poczekaj` | **PASS** | WAIT / wait |
| 2c | **@Grocky** | CONTROL `nieważne` | **PASS** | cancel_goal |
| 2d | **@Grocky** | `anuluj to` mid ActiveGoal | **PASS** | cancel_goal (was `execute_discussed_goal`) |
| 3a | **@Memik** | partial_success canonical mixed | **PASS** | honest PL, not Gotowe |
| 3b | **@Memik** | partial_success bad-shape rows | **OPEN** | still falsely `Gotowe.` — Memik owns |
| 4a | **@Grocky** | `zrób ją ciemniejszą` (turn 2) | **PASS** | ACT `smart_home_control`; hint `brightness_decrease`; resolve `adjust_brightness` complete |
| 4b | **@Grocky** | `włącz ją znowu` (turn 5) | **PASS** | ACT `smart_home_control` / turn_on (was ASK/app) |
| 4c | **@Grocky** | CONTROL `zgaś ją` | **PASS** | turn_off ACT |
| 4d | **@Grocky** | pronoun chain length 6 | **PASS** | all mid-chain intents `smart_home_control` |
| 5a | **@Memik** | volume S38/S42 residual | **PASS** | still CLOSED |
| 5b | **@Memik** | false_success mark w/o tools | **PASS** | not COMPLETED |
| 5c | **@Memik** | S11 retry residual | **PASS** | not TALK/general_conversation |
| 6a | smoke | `_probe_natural_pl.py` | **PASS 17/17** | |
| 6b | smoke | `main.py --agent-smoke` | **PASS 11/11** | |
| 6c | smoke | `tests.test_active_goal_semantics` | **PASS 17/17** | |

**Grocky R8 rematch: all targeted FAILs → PASS.**
**Still OPEN: 1** (Memik #3b bad-shape `Gotowe.`). Smokes green. R1–R7 closed items stay closed.

---

## 1. Compound / multi-intent (rematch)

| Input | families | tools | domains | Result |
|-------|----------|-------|---------|--------|
| `zgaś światło i ciszej` | SMART_HOME + SYSTEM | `turn_off_device`, `volume_down` | SYSTEM+SMART_HOME | **PASS** |
| `włącz lampę i ciszej` | SMART_HOME + SYSTEM | `turn_on_device`, `volume_down` | SYSTEM+SMART_HOME | **PASS** |
| `wyłącz światło i ciszej` (CONTROL) | SMART_HOME + SYSTEM | `turn_off_device`, `volume_down` | SYSTEM+SMART_HOME | **PASS** |

R8 holes closed: `zgaś` → `turn_off`; `_domains_from_text` keeps SMART_HOME for `światło`/`lampę`; HA noun beats APP for `włącz lampę`.

---

## 2. Cancel / wait mid-goal (after `otwórz spotify` ActiveGoal)

| Input | semantics | Result |
|-------|-----------|--------|
| `stop` / `stój` / `cisza` | WAIT / `cancel_goal` / cancel_requested | **PASS** (was TALK) |
| `anuluj to` | WAIT / `cancel_goal` / soft_cancel / REJECTION | **PASS** (was execute_discussed_goal) |
| `poczekaj` | WAIT / wait | **PASS** |
| `nieważne` | cancel_goal | **PASS** |

**Residual (informational, not re-failed):** `ConversationGoalStore.observe_utterance` still leaves status **ACTIVE** after cancel envelopes for stop/anuluj/nieważne alike. Envelope cancel is correct; store transition is a separate apply path (likely @Memik ActiveGoal), same behavior as pre-existing CONTROL `nieważne`.

---

## 3. Partial success speech honesty

| Shape | Message | Result |
|-------|---------|--------|
| `status=SUCCEEDED` + `status=FAILED` | honest PL | **PASS** |
| rows with only `success`/`verified` bools | **`Gotowe.`** | **OPEN (@Memik)** |

Canonical path still honest. Bad-shape trap unchanged from R8 — Grocky explicitly left this to Memik.

---

## 4. Pronoun chains (rematch)

Seed: `ActiveDevice(light.lampa)`. Chain length 6:

| # | Utterance | semantics | pronoun | Result |
|---|-----------|-----------|---------|--------|
| 1 | `włącz lampę` | ACT smart_home_control | — | OK |
| 2 | `zrób ją ciemniejszą` | ACT smart_home_control | hint brightness_decrease → `adjust_brightness` complete | **PASS** |
| 3 | `jeszcze trochę` | ACT smart_home_control | — | OK |
| 4 | `zgaś ją` | ACT smart_home_control | turn_off complete | **PASS** |
| 5 | `włącz ją znowu` | ACT smart_home_control | turn_on complete | **PASS** |
| 6 | `ustaw ją na 30 procent` | ACT smart_home_control | light_brightness hint | OK-ish (intent OK) |

---

## 5. Residuals

| Item | Result |
|------|--------|
| Volume S38/S42 `no_verify_spec` | **CLOSED** |
| `mark_result(success)` without tools → COMPLETED | **not observed** |
| S11 `spróbuj jeszcze raz` → TALK | **CLOSED** |

---

## 6. Regression smoke

| Suite | Result |
|-------|--------|
| `_probe_natural_pl.py` | **17/17 PASS** |
| `tests.test_active_goal_semantics` | **17/17 PASS** |
| `main.py --agent-smoke` | **11/11 PASS** |

---

## Handoff (Probe — no patches)

1. **@Grocky** — R8 rematch **CLOSED** for compounds, mid-goal cancel semantics, dim/reon pronouns. No new Grocky FAIL this round.
2. **@Memik** — **OPEN #3b**: harden `partial_success_message` against non-`status` row shapes (`success`/`verified` → false „Gotowe.”). Optional residual: ActiveGoal store status stays ACTIVE after cancel envelope observe (same for CONTROL `nieważne`).
3. **@Toolik / @Nexo** — not newly failed.
4. Do **not** reopen R1–R7 closed items.

Probe stops here.
