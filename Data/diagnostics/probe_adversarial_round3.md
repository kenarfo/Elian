# Probe adversarial round 3 — Memik C + S11/S12 retest

Date: 2026-09-10 (UTC+1 / Etc/GMT-1). Machine: `a54f5f03-25ab-492d-b5ba-b9eca259a21f` (Ziemniaczek).
Scope: **SOFTWARE only**. **No production patches from Probe.**
Evidence: `Data/diagnostics/probe_adversarial_round3.json`, `agent_smoke_round3.txt`.

Ownership: **@Memik** plan_cycle / pending · **@Toolik** ToolResult honesty · **@Grocky** semantic/follow-ups · **@Probe** this report.

---

## Verdict matrix

| # | Owner | Case | Round2 | Round3 | Notes |
|---|-------|------|--------|--------|-------|
| 1 | **@Memik** | FAIL C — open_app when verify `verify_tool_unregistered` / SKIPPED ≠ verified_success | FAIL | **PASS (CLOSED)** | Unit 4/4 + cycle FAILED + gate deny; smoke verify **passed** |
| 2 | **@Memik** | S11/S12 pending retention (`goal` alias; clear only after success; retry/alternate) | (fix shipped) | **PASS** | `PendingRetryRetentionTests` 5/5 |
| 3 | **@Grocky** | bare „jeszcze raz” → RETRY + follow-up + soft_replay_bare | PASS | **PASS** | No regression |
| 4 | **@Toolik** | A — HA connect/timeout → success=False | PASS | **PASS** | 6/6 |
| 5 | **@Toolik** | B — type_text unknown_side_effect | PASS | **PASS** | unit green |
| 6 | smoke | `_probe_natural_pl.py` | 17/17 | **PASS 17/17** | |
| 7 | smoke | `main.py --agent-smoke` | 11/11 | **PASS 11/11** | verify matched (not unregistered) |
| 8 | **@Grocky** | optional „no i?” after active goal | — | **FAIL (hole)** | TALK/`general_conversation`; not follow-up |

---

## 1. @Memik C — open_app verify unregistered (CLOSED)

**Result: PASS**

### Unit (`tests.test_plan_cycle.VerifyToolUnregisteredTests`) — 4/4 OK

| Test | Outcome |
|------|---------|
| `test_open_app_unregistered_verify_not_verified_success` | execution.success=True; verify **FAILED** `verify_tool_unregistered`; **verified_success=False** |
| `test_open_app_with_window_exists_verified` | verify **passed** matched |
| `test_skipped_unregistered_never_verified_success_gate` | SKIPPED+FAILED unregistered → `verification_allows_success_claim` False; `no_verify_spec` still True |
| `test_verify_effect_unregistered_reason` | ok |

### Gate checks (Probe)

```
FAILED unregistered allows False
SKIPPED unregistered allows False
no_verify_spec allows True
```

### Agent-smoke evidence (was false COMPLETED in R2)

```
phase=verify tool=window_exists status=passed reason=matched   # Spotify open_app
phase=verify tool=get_now_playing status=passed reason=matched # MJ play_media
Result: 11/11 PASS
```

No `verify_tool_unregistered` / SKIPPED→success path in this smoke run.

**Layer:** @Memik `plan_cycle` (`verify_tool_unregistered`→FAILED + allowlist) + smoke registry dry `window_exists`/`get_now_playing` — **closed**.

---

## 2. @Memik S11/S12 — pending retention (PASS)

**Result: PASS** — `tests.test_goal_execution.PendingRetryRetentionTests` **5/5 OK**

| Test | Intent |
|------|--------|
| `test_goal_text_alias_and_normalize` | `goal` alias aligned with original_* |
| `test_retry_reexecutes_with_goal_alias` | S11 „spróbuj jeszcze raz” re-executes; no „Nie mam już…” |
| `test_alternate_reexecutes_with_goal_alias` | S12 „spróbuj inaczej” / alternate re-executes |
| `test_failed_retry_retains_pending` | clear only after success |
| `test_status_fallback_retains_when_no_goal_text` | status speech; pending kept |

**Note (not a fail):** GOAL doc still flags SemanticLab envelope when pending *absent* (RETRY→TALK) as out-of-scope for Memik retention; retention layer itself is green.

---

## 3. Regression smoke

| Suite | Result |
|-------|--------|
| `_probe_natural_pl.py` | **17/17 PASS** |
| bare „jeszcze raz” / `Jeszcze raz.` / `jeszcze raz!` | RETRY + follow=True + soft_replay_bare |
| Toolik A | **PASS** (6 error msgs fail-closed) |
| Toolik B | **PASS** (unknown_side_effect unit) |
| `main.py --agent-smoke` | **11/11 PASS** (HA ConnectTimeout on lamp clarify = HARDWARE/network; SOFTWARE gate still PASS) |

---

## 4. Optional light — „no i?” after active goal → @Grocky

**Result: FAIL (hole still open)** — Probe does **not** patch.

With `ConversationGoalStore.current` = ActiveGoal status=ACTIVE objective=`otwórz spotify`:

| Input | class | follow | soft | mode | intent |
|-------|-------|--------|------|------|--------|
| `no i?` | UNKNOWN | False | null | TALK | general_conversation |
| `no i` | UNKNOWN | False | null | TALK | general_conversation |
| `i co?` | UNKNOWN | False | null | TALK | general_conversation |
| `i co dalej?` | UNKNOWN | False | null | TALK | general_conversation |
| `co dalej?` | INFORMATION_REQUEST | False | null | TALK | general_conversation |

**Expected (product honesty):** after an active/unresolved goal, nudges like „no i?” should be follow-up / status / continuation (not cold TALK).
**Owner:** **@Grocky** (utterance_class / soft_goals / semantic_executive). Related to prior S08-class handoffs.

---

## Handoff (Probe — no patches)

1. **@Memik** — C and S11/S12 **closed** for this round; no further Probe action.
2. **@Toolik** — A+B still green; no action.
3. **@Grocky** — optional hole **„no i?”** (and close cousins) after active goal → still open; also prior SemanticLab items when pending absent remain theirs.
4. **@Nexo** — not exercised.

Probe stops here.
