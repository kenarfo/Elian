# GOAL - volume no_verify_spec success-without-readback (S38/S42)

**Date:** 2026-09-10 (UTC+1)
**Scope:** Memik / `core/plan_cycle.py` volume verify + OS readback
**Probe:** adversarial round5 FAIL (known open) → round6 reconfirm FAIL OPEN → **this fix GREEN**

## Root cause

`verify_spec_for(volume_up|volume_down)` returned `None` → `verify_effect` reason=`no_verify_spec` (SKIPPED).
`verification_allows_success_claim` **allowlisted** `no_verify_spec`, so execute `success=True` became `verified_success=True` / COMPLETED **without any OS volume observation**.

Audit S38/S42; Probe round5 #4 Memik; round6 still FAIL OPEN.

## Fix

### Prefer real readback
1. **`get_volume`** read-only tool (pycaw `EndpointVolume`) registered in `core/builtin_tools.py`.
2. `volume_up` / `volume_down` capture `volume_before` / `volume_after` in ToolResult.data.
3. `verify_spec_for(volume_*)` → `("get_volume", {})`.
4. `volume_direction_observed` + `success_criteria_met_from_observation` / `_matches_expected` prove relative change (or floor/ceiling already satisfied).
5. Missing `get_volume` + no before/after → `verify_tool_unregistered` **FAILED**, `verified_success=False` (fail-closed, same as FAIL C).
6. Embedded before/after without `get_volume` → PASSED `ha_embedded_verify` (observation evidence).

### Fail-closed SKIPPED contract
- Removed `no_verify_spec` from `_SKIP_SUCCESS_CLAIM_REASONS`.
- SKIPPED `no_verify_spec` **cannot** claim success without observation.

### Also
- `execution_context.criteria_for_tool` volume → `get_volume` criterion.
- `VERIFY_TOOLS`, capability_registry, action_firewall include `get_volume`.
- Dry registries: `tests/test_plan_cycle.py`, `core/agent_smoke.py`.

## Before / after (Probe)

| Case | Before | After |
|------|--------|-------|
| `volume_down` execute OK, no readback | SKIPPED `no_verify_spec` → **verified_success=True** | FAILED `verify_tool_unregistered` → **verified_success=False** |
| `volume_down` + `get_volume` + before/after | — | PASSED `matched` → **verified_success=True** |
| `verify_spec_for(volume_down)` | `None` | `("get_volume", {})` |
| `verification_allows_success_claim(no_verify_spec)` | True | **False** |

## Repro (green)

```text
spec volume_down ('get_volume', {})
no_verify allow? False
S38 no-readback: FAILED verify_tool_unregistered verified_success False
S38 with readback: PASSED matched verified_success True
PROBE_S38_S42_REPRO: GREEN
```

Script: `_repro_volume_s38.py`

## Tests (2026-09-10 UTC+1)

| Suite | Result |
|-------|--------|
| `VolumeNoVerifyFixTests` | **5/5 OK** |
| `VerifyToolUnregisteredTests` | **4/4 OK** |
| `tests.test_plan_cycle` | **28/28 OK** |
| `python -m core.agent_smoke` | **11/11 PASS** |
| Direct S38/S42 repro | **GREEN** |

## Untouched
Semantic (Grocky), Memory (Nexo), Cap (Toolik), GUI, voice; ActiveGoal evidence gate; Cap shortlist; pending retention; verify_tool_unregistered→FAILED; Toolik Cap cold; no hardcoded phrase plans.
