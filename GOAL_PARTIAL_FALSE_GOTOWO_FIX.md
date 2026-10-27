# GOAL - partial / bad-shape false „Gotowe.” (Probe r8 #3b → r9 GREEN)

**Date:** 2026-09-10 (UTC+1)
**Owner:** Memik
**Scope:** `partial_success_message`, `PLAN_PARTIAL`, response finalize path, ActiveGoal COMPLETED gate, `plan_status_from_rows`
**Probe:** adversarial round8 FAIL NEW (3b) → rematch r9 **GREEN**

## Root cause

`partial_success_message` only counted rows with canonical `status ∈ {SUCCEEDED,FAILED}`.
Callers feeding execute-row dicts with only `success` / `verified` bools produced **empty** ok/bad sets → fall-through returned **`Gotowe.`** on mixed/partial outcomes.

Same shape hole in `plan_status_from_rows`: missing `status` + no `observation.success` still fell through to **`PLAN_COMPLETED`**.

## Fix (fail-closed)

### `core/execution_context.py`
- **`step_row_outcome(row)`** — normalize `status`, aliases, top-level `success`/`verified`/`verified_success`, and `observation.success` → `STEP_*` or `''` (unknown/incomplete).
- **`partial_success_message`** — classify via outcomes; any `FAILED` → `honest_failed_message`; any unknown → honest incomplete (`Nie udało mi się tego dokończyć.`); **`Gotowe.` only when every row is clear SUCCEEDED/SKIPPED**.
- **`rows_indicate_partial_or_incomplete`** — shared gate for mixed/failed/unknown steps or mixed observations.
- **`ActiveExecutionContext.sync_from_steps`** — uses outcomes; unknown rows → `PLAN_PARTIAL` / `PLAN_RUNNING`, never auto-`COMPLETED`.

### `core/plan_cycle.py`
- **`plan_status_from_rows`** — uses `step_row_outcome`; mixed → `PLAN_PARTIAL`; unknown → `PARTIAL`/`RUNNING`; **removed** vacuous `if rows: return PLAN_COMPLETED`.

### `core/planner.py`
- Compound finish uses **`plan_status_from_rows(executed_rows)`**; `PLAN_PARTIAL` still swaps vacuous Gotowe/Ok via `partial_success_message` before `never_fake_success` / `finalize_response`.

### `core/active_goal.py`
- **`mark_result`**: even with evidence, mixed/failed/incomplete steps or observations **block COMPLETED** (`partial_blocked_completion` → UNRESOLVED/IN_PROGRESS path).

## Before / after (Probe 3b)

| Shape | Before | After |
|-------|--------|-------|
| `status=SUCCEEDED` + `FAILED` | honest PL | honest PL (unchanged) |
| rows with only `success`/`verified` mixed | **`Gotowe.`** | honest partial (not Gotowe) |
| unknown rows (no signals) | **`Gotowe.`** / COMPLETED | incomplete / PARTIAL\|RUNNING |
| ActiveGoal `mark_result(success=True)` + mixed steps | could COMPLETED | **not COMPLETED** |

## Repro (GREEN)

```text
3a PASS
3b PASS
plan_status PARTIAL
PROBE_R9_3B_REPRO: GREEN
```

## Tests (2026-09-10 UTC+1)

| Suite | Result |
|-------|--------|
| `BadShapePartialGotoweTests` | **5/5 OK** |
| `PartialSuccessTests` (incl. probe_r8) | **3/3 OK** |
| `PlanStatusHelpersTests` | **3/3 OK** |
| `FalseSuccessGoalCompletionTests` | **3/3 OK** |
| Direct Probe 3a/3b repro | **GREEN** |

## Untouched
Semantic (Grocky), Memory (Nexo), Cap/HA ToolResult honesty (Toolik), GUI, voice.
No regression intended on: ActiveGoal evidence gate, Cap shortlist, pending retention, `verify_tool_unregistered`, volume `get_volume` verify, Toolik Cap cold.
No hardcoded example phrase plans.
