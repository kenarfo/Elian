# GOAL P0 — False Success Fix

**Date:** 2026-09-10 (UTC+1)
**Scope:** Memik / goal-pipeline only (ActiveGoal COMPLETED evidence gate)
**Machine:** Elian local Windows (`C:\Users\kenar\Elian`)

## Root cause (verified)

Hypothesis from `GOAL_ORIENTED_AUDIT_REPORT.md` was **confirmed**:

1. `Planner` `final_answer` path returns `PlannerResult(success=True)` with **zero tools / observations** (often after `finalize_response` turns ACK-like text into the vacuous stall *"Chwila — sprawdzam to dokładniej."*).
2. `ConversationManager._run_agent` then called `_note_goal_tool_result(success=True)` → `ConversationGoalStore.mark_result(success=True)` → **ActiveGoal `COMPLETED`** with no successful observations / verified steps.
3. Soft `action_loop` path also used `success=ok if loop.observations else True` (no observations ⇒ forced success).

This matches S14 / S18 / S37 / S45 (ACT or follow-up, `tools=[]`, vacuous reply, `COMPLETED`).

## What changed

### `core/active_goal.py`
- Added `has_goal_completion_evidence(...)` — COMPLETED requires successful tool observations and/or verified / succeeded steps, or a real tool result (`tool` + `success=True`). Non-tool actions like `agent` / `talk` alone are **not** evidence.
- Added `is_vacuous_goal_reply(...)` for empty / ACK-theater / finalize stall replies.
- `mark_result(success=True)` without evidence **never** sets `COMPLETED`:
  - vacuous / no-tool claim → `IN_PROGRESS`
  - otherwise → `UNRESOLVED`
- Optional `observations=` / `steps=` kwargs on `mark_result`.

### `core/conversation_manager.py`
- `_note_goal_tool_result` prefers `ToolResult.to_observation()`, passes observations/steps into `mark_result` (evidence gate lives in ActiveGoal).
- Soft action_loop: `success=bool(ok)` only (no longer `else True` when observations empty).
- `_run_agent`: builds payload with observations/steps; skips goal completion note for clarification / confirmation / knowledge (keeps `IN_PROGRESS` via `mark_in_progress`).

### `core/agent.py`
- `_gate_agent_success_evidence`: for ACT/PLAN/`needs_tools` or vacuous finals **without** evidence, downgrade `PlannerResult.success` to `False` (clarify/confirm/knowledge excluded).

### Untouched (as required)
- Semantic parsing, Memory/WorldState, ToolRegistry/capability, GUI, voice.
- No hardcoded plans for example phrases.
- `execution_context.py` / `action_claim.py` / `pending_goal.py` not required for this P0.

## Tests

### Added
`tests/test_goal_execution.py` → `FalseSuccessGoalCompletionTests`
- `test_act_final_without_tools_not_completed` — FakeModelClient final-only ACT path must **not** COMPLETED
- `test_verified_tool_success_may_complete` — successful tool observation **may** COMPLETED
- `test_helper_rejects_vacuous_claim` — unit evidence helper

### Updated
`tests/test_active_goal_semantics.py` → `test_mark_result_unresolved_lifecycle`
- success path now passes tool evidence; asserts vacuous/`agent` claim does **not** COMPLETED.

### Results (2026-09-10)
- `FalseSuccessGoalCompletionTests`: **3/3 OK**
- `tests.test_active_goal_semantics`: **OK** (incl. lifecycle)
- `tests.test_goal_execution` (full): **OK** on earlier run (25 tests); one later flaky `PermissionError` on `Data/memory.json` replace in `RepairExhaustionTests` — **environmental**, unrelated to this gate (Memory write while file locked).

## What remains broken (out of this P0)

From the audit — **not** fixed here:
- Pending retry retention / lost ActiveGoal on retry (S11/S12)
- Volume `no_verify_spec` verification gap (S38/S42)
- Semantic Executive dependency for status/retry envelopes (S08/S11)
- Planner still emits `success=True` on bare `final_answer`; gated downstream (agent + ActiveGoal). A future cleanup could set planner final success from evidence directly.

## Success criteria

- S14/S18/S37/S45 class: ActiveGoal no longer COMPLETED on vacuous zero-tool finals.
- Unit goal-path tests green; semantics untouched.
