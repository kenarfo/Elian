# Probe adversarial round9 dogrywka — Memik #3b PARTIAL DONE rematch

**Verdict: PASS**

Date: 2026-09-10 ~22:15 UTC+1 (Etc/GMT-1). Machine: `a54f5f03-25ab-492d-b5ba-b9eca259a21f` (Ziemniaczek).
Scope: **SOFTWARE rematch** of Memik #3b (bad-shape partial → false „Gotowe.”) after `GOAL_PARTIAL_FALSE_GOTOWO_FIX.md`. **No production patches from Probe.**
Prior: `probe_adversarial_round9.md` left #3b **OPEN**; this file is the dogrywka / close-out.

Ownership: **@Memik** fix under test · **@Probe** this rematch.

---

## Expectation (fail-closed)

- `step_row_outcome` normalizes `status`, aliases, top-level `success`/`verified`/`verified_success`, and `observation.success`.
- `partial_success_message`: any FAILED → honest failed; any unknown → honest incomplete; **„Gotowe.” only when every row is clear SUCCEEDED/SKIPPED**.
- `plan_status_from_rows`: mixed bad-shape → `PLAN_PARTIAL` (not vacuous COMPLETED).
- ActiveGoal: mixed/incomplete steps block COMPLETED even if `success=True` + message „Gotowe.”.

---

## Verdict matrix

| # | Case | Result | Notes |
|---|------|--------|-------|
| 3a | canonical mixed `SUCCEEDED`+`FAILED` | **PASS** | honest PL, no Gotowe |
| 3b | bad-shape `success`/`verified` mixed | **PASS** (was OPEN/FAIL) | honest partial, not Gotowe |
| plan | `plan_status_from_rows` bad-shape | **PASS** | `PARTIAL` |
| AG | `mark_result(success=True)` + mixed steps | **PASS** | not COMPLETED |
| smoke | `main.py --agent-smoke` | **PASS 11/11** | no regression |

**Memik #3b: CLOSED (GREEN).** R9 Grocky rematch items stay PASS; R1–R7 closed items stay closed.

---

## Suites run

| Suite | Result |
|-------|--------|
| `BadShapePartialGotoweTests` | **5/5 OK** |
| `PartialSuccessTests` (incl. probe_r8 bad-shape) | **3/3 OK** |
| `PlanStatusHelpersTests` | **3/3 OK** |
| Direct `PROBE_R9_3B_REPRO` | **GREEN** |
| `main.py --agent-smoke` | **11/11 PASS** (log: `Data/diagnostics/agent_smoke_round9_partial.txt`) |

Python: `C:\Users\kenar\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`

---

## Direct repro (PROBE_R9_3B_REPRO)

`	ext
3a PASS 'Nie udało mi się zmniejszyć głośności, Mogę spróbować zmniejszyć głośności jeszcze raz.'
step_row_outcome SUCCEEDED FAILED
3b PASS 'Nie udało mi się zmniejszyć głośności, Mogę spróbować zmniejszyć głośności jeszcze raz.'
plan_status PARTIAL PASS
rows_indicate_partial True
PROBE_R9_3B_REPRO: GREEN
`

Bad-shape rows used:

`python
[
  {"tool": "open_app", "success": True, "verified": True},
  {"tool": "volume_down", "success": False, "verified": False},
]
`

Before fix (R8/R9): `Gotowe.`
After Memik: honest failed/partial PL; `plan_status=PARTIAL`; ActiveGoal not COMPLETED.

---

## Notes

- HA connect timeout in agent-smoke slot path (`192.168.0.31`) is environmental; smoke still **11/11 PASS**.
- Probe did **not** patch production code.
- Optional residual from R9 (ActiveGoal store stays ACTIVE after cancel envelope) **not re-opened** here — out of #3b scope.

---

## Handoff

1. **@Memik** — #3b dogrywka **PASS / CLOSED**. Fail-closed `step_row_outcome` / `partial_success_message` / `plan_status_from_rows` / ActiveGoal gate verified.
2. **@Grocky / @Toolik / @Nexo** — not newly failed this dogrywka.
3. Do **not** reopen R1–R8 closed items beyond this #3b close.

Probe stops here. **PASS**
