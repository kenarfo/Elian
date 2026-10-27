# GOAL Pending Retry Retention Fix (S11/S12)

**Date:** 2026-09-10 (UTC+1)
**Scope:** Memik / goal-execution layer only (pending + ConversationManager retry/alternate)
**Machine:** Elian local Windows (`C:\Users\kenar\Elian`)
**Out of scope:** Semantic Executive (Grocky), Memory (Nexo), Cap/ToolRegistry (Toolik), GUI, voice

## Problem (audit)

From `GOAL_ORIENTED_AUDIT_REPORT.md`:

- **S11** `spróbuj jeszcze raz` with seeded pending → `"Nie mam już tej czynności do ponowienia."`
- **S12** `spróbuj inaczej` → same; log showed `pending_alternate` then pending lost
- Envelope could still fall to TALK (SemanticLab) — **not fixed here**; retention fixed in goal/execution layer

## Root cause

Verified offline with harness-shaped pending:

```python
pending = {
  "plan_id": "p-audit",
  "goal": "Otwórz Spotify i daj trochę ciszej",  # alias only
  # missing: original_goal / original_user_text
  ...
}
```

`ConversationManager._retry_pending_executable_goal` resolved goal text only from:

- `original_goal`
- `original_user_text`

It ignored the `goal` alias that `status_summary_for_pending` already understood (why **S10** incomplete-status worked). On empty text it called `_set_pending_executable(None)` **before any re-execute**, producing the false "nie mam czynności" reply and wiping pending.

Production `pending_from_plan()` always sets `original_goal`, so many unit paths were fine; audit / partial stores that only had `goal` hit the bug.

## Fix

### `core/pending_goal.py`
- Added `goal_text_from_pending()` — resolves `original_goal` | `original_user_text` | `goal`
- Added `normalize_pending_goal_fields()` — keeps the three aliases aligned; fills `normalized_goal` when missing
- `persist_pending()` normalizes before store
- `status_summary_for_pending()` uses `goal_text_from_pending`

### `core/conversation_manager.py`
- `_set_pending_executable` / `_restore_pending_from_ttl` / alternate path normalize pending
- `_retry_pending_executable_goal`:
  1. Resolve goal via aliases, then ActiveGoal `objective` fallback
  2. If pending exists but still no goal text → **retain pending**, speak `status_summary_for_pending` (`status_fallback`) — never claim "nie mam czynności"
  3. Only say `"Nie mam już tej czynności do ponowienia."` when pending is truly empty
  4. Do **not** clear pending before re-execute; clear remains only via successful `_run_agent` completion path

### Tests (`tests/test_goal_execution.py`)
New `PendingRetryRetentionTests`:
- alias normalize helper
- S11-style retry re-executes (no "nie mam już…")
- S12-style alternate re-executes
- failed retry retains pending + goal text
- no-goal-text pending → status speech, pending kept

## Before / after

| Case | Before | After |
|---|---|---|
| S11 seeded pending (`goal` only) | reply "Nie mam już…"; pending `None` | re-execute with goal text; pending retained until successful complete |
| S12 alternate | `pending_alternate` then same wipe | alternate flag set; re-execute; pending retained until success |
| Pending with steps but no goal text | wipe + "nie mam" | `status_fallback` status speech; pending kept |
| Truly empty pending | "Nie mam już…" | unchanged |
| P0 ActiveGoal evidence gate | — | untouched (FalseSuccess tests still OK) |

## Tests run

```text
python -m unittest \
  tests.test_goal_execution.PendingRetryRetentionTests \
  tests.test_goal_execution.FalseSuccessGoalCompletionTests \
  tests.test_goal_execution.ResumedGoalTests \
  tests.test_goal_execution.InterruptCorrectionTests \
  tests.test_goal_execution.PlanStatusHelpersTests \
  tests.test_phase13_stage_a \
  -q
→ Ran 46 tests in ~3.2s  OK
```

Also offline repro: harness-shaped pending + `spróbuj jeszcze raz` / `spróbuj inaczej` no longer emit the wipe phrase; `goal_text_from_pending` stays populated.

## SemanticLab handoffs remaining

Still **not** fixed in this change (intent/envelope formation before planner):

1. **S08** — `co się stało?` with UNRESOLVED ActiveGoal → `TALK` / `general_conversation` (`needs_active_goal=False`)
2. **S11/S12 envelope** — even when CM pending path now re-executes correctly, Semantic Executive may still classify the bare retry utterance as `TALK`/`general_conversation` when pending is *absent*; mapping RETRY + unresolved/pending → ACT/`retry_goal` remains a SemanticLab item
3. Extra probe from audit: `co poszło nie tak?` under UNRESOLVED → same S08-class dependency

## Non-regressions

- Did not touch Semantic Executive, Memory, Cap/ToolRegistry honesty, GUI, voice
- Did not hardcode example phrases/plans
- Did not weaken P0 ActiveGoal evidence gate or Cap-AVAILABLE shortlist fixes

## Files touched

- `core/pending_goal.py`
- `core/conversation_manager.py`
- `tests/test_goal_execution.py`
- `GOAL_PENDING_RETRY_RETENTION_FIX.md` (this report)