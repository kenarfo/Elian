# Probe adversarial round 4 — @Grocky „no i?” fix + regression

Date: 2026-09-10 (UTC+1 / Etc/GMT-1). Machine: `a54f5f03-25ab-492d-b5ba-b9eca259a21f` (Ziemniaczek).
Scope: **SOFTWARE only**. **No production patches from Probe.**
Evidence: `Data/diagnostics/probe_adversarial_round4.json`, `agent_smoke_round4.txt`, `probe_round4_units.txt`.

Ownership: **@Grocky** semantic/follow-ups · **@Memik** plan_cycle / pending · **@Toolik** ToolResult honesty · **@Probe** this report.

---

## Verdict matrix

| # | Owner | Case | Round3 | Round4 | Notes |
|---|-------|------|--------|--------|-------|
| 1 | **@Grocky** | „no i?” / cousins after ActiveGoal → CONTINUATION + fu + soft_expectant_continue + ACT execute_discussed_goal | FAIL (hole) | **PASS (CLOSED)** | Seed objective=`otwórz spotify` ACTIVE |
| 2 | **@Grocky** | bare „jeszcze raz” → RETRY + follow-up + soft_replay_bare | PASS | **PASS** | No regression |
| 3 | **@Memik** | C — open_app verify unregistered ≠ verified_success | PASS | **PASS** | Unit 4/4; smoke verify matched |
| 4 | **@Memik** | S11/S12 pending retention | PASS | **PASS** | `PendingRetryRetentionTests` 5/5 |
| 5 | **@Toolik** | A — HA connect/timeout → success=False | PASS | **PASS** | 6/6 |
| 6 | **@Toolik** | B — type_text unknown_side_effect | PASS | **PASS** | unit green |
| 7 | smoke | `_probe_natural_pl.py` | 17/17 | **PASS 17/17** | |
| 8 | smoke | `main.py --agent-smoke` | 11/11 | **PASS 11/11** | verify matched (not unregistered) |

---

## 1. @Grocky — „no i?” after ActiveGoal (PASS)

**Expected:** after ActiveGoal e.g. `otwórz spotify`, utterances `no i?` / `no i` / `i co?` / `i co dalej?` / `co dalej?` → class=CONTINUATION, fu=True, soft=`soft_expectant_continue`, mode=ACT, intent=`execute_discussed_goal` (or equivalent). Cold without goal may stay TALK — OK.

### With ActiveGoal (status=ACTIVE, objective=`otwórz spotify`)

| Input | class | follow | soft | mode | intent | result |
|-------|-------|--------|------|------|--------|--------|
| `no i?` | CONTINUATION | True | soft_expectant_continue | ACT | execute_discussed_goal | PASS |
| `no i` | CONTINUATION | True | soft_expectant_continue | ACT | execute_discussed_goal | PASS |
| `i co?` | CONTINUATION | True | soft_expectant_continue | ACT | execute_discussed_goal | PASS |
| `i co dalej?` | CONTINUATION | True | soft_expectant_continue | ACT | execute_discussed_goal | PASS |
| `co dalej?` | CONTINUATION | True | soft_expectant_continue | ACT | execute_discussed_goal | PASS |

### Cold without goal (allowed TALK)

| Input | class | follow | soft | mode | intent |
|-------|-------|--------|------|------|--------|
| `no i?` | UNKNOWN | True | soft_expectant_continue | TALK | general_conversation |
| `no i` | UNKNOWN | True | soft_expectant_continue | TALK | general_conversation |
| `i co?` | UNKNOWN | True | soft_expectant_continue | TALK | general_conversation |

Cold soft may still fire `soft_expectant_continue` / fu=True, but envelope stays **TALK** / `general_conversation` — accepted per brief.

**Optional edge (not required):** `no i??` → still UNKNOWN/TALK (regex allows one `?` only). Not a fail.

**Layer:** @Grocky `utterance_class._EXPECTANT_CONTINUE` + `soft_goals.soft_expectant_continue` + `semantic_executive` CONTINUATION→ACT `execute_discussed_goal` — **closed**.

---

## 2. Regression smoke

| Suite | Result |
|-------|--------|
| bare „jeszcze raz” / `Jeszcze raz.` / `jeszcze raz!` | RETRY + follow=True + soft_replay_bare |
| Memik C `VerifyToolUnregisteredTests` | **4/4 PASS** |
| Memik S11/S12 `PendingRetryRetentionTests` | **5/5 PASS** |
| Toolik A | **PASS** (6/6 fail-closed) |
| Toolik B | **PASS** (unknown_side_effect unit) |
| `_probe_natural_pl.py` | **17/17 PASS** |
| `main.py --agent-smoke` | **11/11 PASS** (HA ConnectTimeout on lamp clarify = HARDWARE/network; SOFTWARE gate PASS) |

### Agent-smoke verify evidence

```
phase=verify tool=window_exists status=passed reason=matched
phase=verify tool=get_now_playing status=passed reason=matched
Result: 11/11 PASS
```

No `verify_tool_unregistered` / SKIPPED→success path in this smoke run.

---

## Handoff (Probe — no patches)

1. **@Grocky** — „no i?” hole from Round3 **CLOSED**; bare „jeszcze raz” still green.
2. **@Memik** — C and S11/S12 still green; no further Probe action.
3. **@Toolik** — A+B still green; no action.
4. **@Nexo** — not exercised.

Probe stops here.
