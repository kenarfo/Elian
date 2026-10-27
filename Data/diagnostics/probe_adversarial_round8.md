# Probe adversarial round 8 — NEW Jarvis-gap SOFTWARE holes

Date: 2026-09-10 ~21:55 UTC+1 (Etc/GMT-1). Machine: `a54f5f03-25ab-492d-b5ba-b9eca259a21f` (Ziemniaczek).
Scope: **SOFTWARE only**. **No production patches from Probe.**
Evidence: `Data/diagnostics/probe_adversarial_round8.json`, `_probe_adversarial_round8.py`, `natural_pl_probe_round8.txt`, `agent_smoke_round8.txt`.

Ownership: **@Grocky** semantics / cancel / pronouns · **@Memik** compound plan / partial honesty / ActiveGoal · **@Toolik** Cap (not exercised) · **@Probe** this report.

Prior closed (not re-opened): slang, search, Memory ROUTINE, jeszcze raz, no i?, cousins, sticky, S08, Cap cold, volume verify (R7), C, S11/S12, Toolik A+B.

---

## Verdict matrix

| # | Owner | Case | Result | Notes |
|---|-------|------|--------|-------|
| 1 | **@Memik+@Grocky** | `zgaś światło i ciszej` both goals | **FAIL (NEW)** | families SMART_HOME+SYSTEM parsed, but light tool=`None`; domains SYSTEM-only |
| 1b | **@Memik** | CONTROL `wyłącz światło i ciszej` | **PASS** | `turn_off_device` + `volume_down` |
| 1c | **@Grocky+@Memik** | `włącz lampę i ciszej` | **FAIL (NEW)** | lamp clause → APP/`open_service` incomplete |
| 2a | **@Grocky** | `stop`/`stój`/`cisza` mid ActiveGoal | **FAIL (NEW)** | semantics TALK/general_conversation; goal stays ACTIVE |
| 2b | **@Grocky** | `poczekaj` | **PASS** | WAIT / wait |
| 2c | **@Grocky** | `nieważne` | **PASS** | cancel_goal |
| 2d | **@Grocky** | `anuluj to` mid ActiveGoal | **FAIL (NEW)** | soft_cancel + cancel_requested, but intent=`execute_discussed_goal` |
| 3a | **@Memik** | partial_success canonical mixed | **PASS** | honest PL, not Gotowe |
| 3b | **@Memik** | partial_success bad-shape rows | **FAIL (NEW)** | `success`/`verified` keys → falsely `Gotowe.` |
| 4a | **@Grocky** | `zrób ją ciemniejszą` (turn 2) | **FAIL (NEW)** | pronoun yes, action_hint empty → execute_discussed `włącz lampę` |
| 4b | **@Grocky** | `włącz ją znowu` (turn 5) | **FAIL (NEW)** | pronoun `turn_on` complete, semantics ASK/`app` |
| 4c | **@Grocky** | CONTROL `zgaś ją` | **PASS** | turn_off ACT |
| 4d | **@Grocky** | pronoun chain >2 turns | **FAIL (NEW)** | breaks at turns 2 and 5 |
| 5a | **@Memik** | volume S38/S42 residual | **PASS** | still CLOSED post-R7 |
| 5b | **@Memik** | false_success mark w/o tools | **PASS** | not COMPLETED |
| 5c | **@Memik** | S11 retry residual | **PASS** | not TALK/general_conversation |
| 6a | smoke | `_probe_natural_pl.py` | **PASS 17/17** | |
| 6b | smoke | `main.py --agent-smoke` | **PASS 11/11** | |

**NEW FAILs this round: 8** (1, 1c, 2a, 2d, 3b, 4a, 4b, 4d). Smokes green. R1–R7 closed items stay closed.

---

## 1. Compound / multi-intent — `zgaś światło i ciszej`

### Observed

| Layer | Result |
|-------|--------|
| `parse_compound_goal` families | `SMART_HOME` + `SYSTEM` |
| first `semantic_action` | **`smart_home`** (not `turn_off`) |
| `resolve_step_tool` | **`(None, …)`** for light; `volume_down` for ciszej |
| `_domains_from_text` | **`['SYSTEM']` only** — SMART_HOME dropped |
| `decide_semantics` | PLAN / `multi_action` / `allowed_domains=['SYSTEM']` |

### Root holes (software)

1. **`POWER_OFF_VERBS`** = `wyłącz|wylacz|zamknij|…` — **missing `zgaś`/`zgas`**. Clause falls through to generic `smart_home`.
2. **`resolve_step_tool`** has no handler for action `smart_home` → light step unusable.
3. **`_domains_from_text`** uses `\b(?:lamp|[sś]wiat[lł]|klima)\b` — **does not match `światło` / `lampę`** (boundary after stem). Confirmed: `'światło'→False`, `'lampę'→False`.

CONTROL: `wyłącz światło i ciszej` → `turn_off_device` + `volume_down` **PASS** (proves volume half + `wyłącz` path OK).

Cousin **FAIL**: `włącz lampę i ciszej` → first clause **APP / `open_service` incomplete** (`open_app` name=`''`), not SMART_HOME `turn_on`.

**Owner:** @Memik (compound_plan / compound_execute / domains) + @Grocky (multi_action domain envelope).

---

## 2. Cancel / wait mid-goal (after `otwórz spotify` ActiveGoal)

| Input | semantics | followup | ActiveGoal after observe | Result |
|-------|-----------|----------|--------------------------|--------|
| `stop` / `stój` / `cisza` / `wystarczy` | **TALK / general_conversation** | STOP (via `STOP_PHRASES`) | **ACTIVE** | **FAIL** |
| `poczekaj` / `czekaj` | WAIT / wait | — | — | **PASS** |
| `nieważne` | WAIT / cancel_goal | CANCEL | — | **PASS** |
| `anuluj to` | **ACT / execute_discussed_goal** + `cancel_requested=True` | CANCEL + soft_cancel | **ACTIVE** | **FAIL** |

Hole: CM clarification/`STOP_PHRASES` know STOP, but **`decide_semantics` does not map stop-family → CANCEL/WAIT**. Mid-agent / ActiveGoal path that trusts the envelope will chat instead of abort. `anuluj to` is worse: cancel flags set, but intent still **executes** the discussed goal.

**Owner:** @Grocky (semantic_executive cancel/wait; soft vs continuation precedence).

---

## 3. Partial success speech honesty

| Shape | Message | Result |
|-------|---------|--------|
| `status=SUCCEEDED` + `status=FAILED` | honest PL (`Nie udało mi się zmniejszyć głośności…`) | **PASS** |
| rows with only `success`/`verified` bools | **`Gotowe.`** | **FAIL (NEW)** |

Canonical path is honest. Trap: any caller feeding execute-row dicts without `status∈{SUCCEEDED,FAILED}` gets a vacuous success claim. Docstring says *never 'Gotowe.' on mixed outcomes* — contract not fail-closed on alt keys.

**Owner:** @Memik (`partial_success_message` / callers in compound reply path).

---

## 4. Pronoun chains longer than 2 turns

Seed device: `ActiveDevice(light.lampa)`. Chain length 6:

| # | Utterance | semantics | pronoun resolve | Result |
|---|-----------|-----------|-----------------|--------|
| 1 | `włącz lampę` | ACT smart_home | — | OK |
| 2 | `zrób ją ciemniejszą` | ACT **execute_discussed_goal** goal=`włącz lampę` | hint **empty**, resolve None | **FAIL** |
| 3 | `jeszcze trochę` | ACT brightness decrease | — | OK-ish |
| 4 | `zgaś ją` | ACT smart_home | turn_off complete | **PASS** |
| 5 | `włącz ją znowu` | **ASK / app** domains APP | turn_on **complete** | **FAIL** |
| 6 | `ustaw ją na 30 procent` | ACT smart_home | (percent path) | mixed |

Holes:
- `detect_device_action_hint` misses `zrób ją ciemniejszą` / `przygaś ją` (while `ściemnij ją` / `ją ciemniej` work).
- Semantics overrides a complete pronoun `turn_on` with ASK/app for `włącz ją znowu`.

**Owner:** @Grocky (device_pronoun hints + semantic vs pronoun precedence).

---

## 5. Residuals (GOAL / prior OPEN)

| Item | Result |
|------|--------|
| Volume S38/S42 `no_verify_spec` (R6 OPEN → R7 fix) | **CLOSED** (`get_volume`, claim=False) |
| `mark_result(success)` without tool evidence → COMPLETED | **not observed** (stays non-COMPLETED) |
| S11 `spróbuj jeszcze raz` → TALK | **CLOSED** (not general_conversation) |
| GOAL audit still documents historical false_success / zgubiony themes | informational only; not re-failed as NEW unless re-hit |

---

## 6. Regression smoke

| Suite | Result |
|-------|--------|
| `_probe_natural_pl.py` | **17/17 PASS** |
| `main.py --agent-smoke` | **11/11 PASS** |

---

## Handoff (Probe — no patches)

1. **@Memik** — compound `zgaś`+volume: add `zgaś` to POWER_OFF (or map `smart_home`+light+off verbs → `turn_off`); fix `_domains_from_text` stems for `światło`/`lampę`; stop APP hijack of `włącz lampę` in compounds; harden `partial_success_message` against non-`status` row shapes.
2. **@Grocky** — map `stop`/`stój`/`cisza`/`wystarczy` to CANCEL in `decide_semantics` when ActiveGoal/pending; fix `anuluj to` so cancel wins over `execute_discussed_goal`; dim pronoun hints (`ciemniejsz*`, `przygaś`); `włącz ją znowu` must stay SMART_HOME ACT when ActiveDevice light binds.
3. **@Toolik / @Nexo** — not newly failed this round.
4. Do **not** reopen R1–R7 closed items; volume/S11/S08/cousins/sticky remain green under smoke + residual checks.

Probe stops here.
