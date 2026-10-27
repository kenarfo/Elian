# Probe adversarial round 7 — volume dogrywka (S38/S42) after Memik VOLUME DONE

Date: 2026-09-10 ~21:45 UTC+1 (Etc/GMT-1). Machine: `a54f5f03-25ab-492d-b5ba-b9eca259a21f` (Ziemniaczek).
Scope: **SOFTWARE only**. **No production patches from Probe.**
Prior: round5 FAIL / round6 FAIL OPEN on Memik volume `no_verify_spec` → Memik `GOAL_VOLUME_NO_VERIFY_FIX.md` claims GREEN → this dogrywka.

Ownership: **@Memik** plan_cycle / volume verify · **@Probe** this report.

Evidence:
- `GOAL_VOLUME_NO_VERIFY_FIX.md` (Memik)
- `Data/diagnostics/probe_round7_volume_repro.txt`
- `Data/diagnostics/probe_round7_volume_units.txt`
- `Data/diagnostics/agent_smoke_round7.txt`
- `Data/diagnostics/natural_pl_probe_round7.txt`
- `_repro_volume_s38.py`

---

## Verdict

| Gate | Result |
|------|--------|
| **Volume S38/S42 (Memik dogrywka)** | **PASS** |
| **agent-smoke** | **PASS 11/11** |
| **VolumeNoVerifyFixTests** | **PASS 5/5** |
| **natural_pl** | **PASS 17/17** |

**Round6 OPEN item #5 (Memik volume `no_verify_spec` S38/S42) is CLOSED.**

---

## Acceptance checks (adversarial)

| Check | Expected | Observed | Result |
|-------|----------|----------|--------|
| `verify_spec_for(volume_down)` | `("get_volume", {})` | `('get_volume', {})` | PASS |
| `verify_spec_for(volume_up)` | `("get_volume", {})` | `('get_volume', {})` | PASS |
| `verification_allows_success_claim(SKIPPED/no_verify_spec)` | **False** | `False` | PASS |
| S38 execute-ok `volume_down` **without** readback / `get_volume` | `verified_success=False` | FAILED `verify_tool_unregistered`, `verified_success=False` | PASS |
| S38 with `get_volume` + before/after | `verified_success=True` | PASSED `matched`, `verified_success=True` | PASS |
| S42 mirror `volume_up` no-readback | `verified_success=False` | FAILED `verify_tool_unregistered`, `verified_success=False` | PASS |
| S42 mirror `volume_up` with readback | `verified_success=True` | PASSED `matched`, `verified_success=True` | PASS |

Repro line: `PROBE_S38_S42_REPRO: GREEN` (+ Probe mirror `PROBE_S42_MIRROR: GREEN`).

### Fail-closed contract (dogrywka intent)

- `no_verify_spec` must **NOT** allow success claim without readback → confirmed False.
- Without readback → `verified_success=False` (fail-closed via `verify_tool_unregistered` FAILED, not silent SKIPPED success).
- `volume_up` / `volume_down` verify via `get_volume` when registered.

---

## Unit / smoke regression

| Suite | Result | Notes |
|-------|--------|-------|
| `tests.test_plan_cycle.VolumeNoVerifyFixTests` | **5/5 OK** | present; all green |
| `python -m core.agent_smoke` | **11/11 PASS** | dry tools |
| `_probe_natural_pl.py` | **17/17** | quick regression |

VolumeNoVerifyFixTests covered:
1. `test_verify_spec_maps_volume_to_get_volume`
2. `test_no_verify_spec_never_allows_success_claim`
3. `test_volume_down_with_get_volume_verified_success`
4. `test_volume_without_get_volume_not_verified_even_if_execute_ok`
5. `test_volume_embedded_before_after_passes_without_get_volume` (ha_embedded_verify)

---

## Compared to Memik report

Memik `GOAL_VOLUME_NO_VERIFY_FIX.md` claims match Probe observation. No residual hole found on the stated S38/S42 contracts in this dogrywka.

---

## Untouched / out of scope

No production patches. Semantic (Grocky), Memory (Nexo), Cap (Toolik), GUI, voice not re-audited beyond smoke/natural_pl. Hardware volume OS behavior not live-tested (dry registry + embedded observation only).

---

## Summary for parent

```text
VOLUME S38/S42: PASS
agent-smoke:    PASS 11/11
VolumeNoVerify: PASS 5/5
natural_pl:     PASS 17/17
ROUND7 VOLUME DOGRYWKA: PASS (Memik #5 CLOSED)
```
