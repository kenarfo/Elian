# GOAL — open_app verify skip ≠ success (Probe FAIL C)

**Date:** 2026-09-10 (UTC+1)
**Scope:** Memik / `core/plan_cycle.py` verify success gate + open_app verify registration
**Probe:** adversarial round1 OBSERVED C / round2 still-open → **GREEN**

## Root cause

Exact gate (was ~L1123, now gated):

`SKIPPED|UNKNOWN` + `execution.success` → `verified_success=True`

When `verify_spec_for(open_app)` needs `window_exists` but that tool is unregistered, `verify_effect` returned `reason=verify_tool_unregistered` and the cycle still claimed verified success. Smoke dry registry lacked `window_exists` / `get_now_playing`, so Probe saw false success without window proof.

## Fix

### `core/plan_cycle.py`
1. `verify_tool_unregistered` is now **`VerifyStatus.FAILED`** (not intentional SKIPPED).
2. `verification_allows_success_claim` — **default deny**; only allowlisted intentional skips may claim:
   - `no_verify_spec` (volume etc.)
   - `non_mutation`
3. `verify_tool_unregistered` never allowlisted; early fail-closed return → `verified_success=False`, no remutate.
4. Repair-loop path uses the same allowlist.

### `core/agent_smoke.py`
- Registers dry `window_exists` + `get_now_playing`.
- APP/MEDIA smoke PASS requires verify `status=passed` (not unregistered).

### `tests/test_plan_cycle.py`
- `_cycle_registry` includes `window_exists`.
- `VerifyToolUnregisteredTests` (4): unregistered → not verified; registered → passed; gate unit; verify_effect reason.

## Before / after (Probe)

| | Before | After |
|--|--------|-------|
| open_app, no `window_exists` | SKIPPED unregistered → **verified_success=True** | FAILED unregistered → **verified_success=False** |
| open_app + `window_exists` | — | PASSED matched → verified_success=True |
| Smoke Spotify / MJ | verify skipped unregistered, still PASS | verify **passed**, 11/11 PASS |

## Repro (green)

```text
status VerifyStatus.FAILED reason verify_tool_unregistered
verified_success False
PROBE_FAIL_C_REPRO: GREEN
```

## Tests (2026-09-10 UTC+1)

| Suite | Result |
|-------|--------|
| `VerifyToolUnregisteredTests` | **4/4 OK** |
| `tests.test_plan_cycle` | **23/23 OK** |
| `python -m core.agent_smoke` | **11/11 PASS** |
| Direct FAIL C repro | **GREEN** |

Smoke log: `Data/diagnostics/agent_smoke_verify_fix.txt`

## Untouched
Semantic, Memory, Cap honesty / Toolik HA/type_text, GUI, voice; ActiveGoal evidence gate; Cap AVAILABLE shortlist; no hardcoded phrase plans. Volume `no_verify_spec` still allowlisted intentional skip.
