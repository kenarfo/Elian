# Probe adversarial round 6 — retest R5 NEW FAILs (SOFTWARE)

Date: 2026-09-10 ~21:35 UTC+1 (Etc/GMT-1). Machine: `a54f5f03-25ab-492d-b5ba-b9eca259a21f` (Ziemniaczek).
Scope: **SOFTWARE only**. **No production patches from Probe.**
Evidence: `Data/diagnostics/probe_adversarial_round6.json`, `_probe_adversarial_round6.py`, `natural_pl_probe_round6.txt`, `agent_smoke_round6.txt`.

Ownership: **@Grocky** semantic/follow-ups · **@Memik** plan_cycle / ActiveGoal · **@Toolik** Cap/ToolResult · **@Probe** this report.

Prior closed (not re-opened): slang 17/17 path, wyszukaj X, Memory ROUTINE, jeszcze raz, Toolik A+B, Memik C+S11/S12, FastPath lamp domain (S03).

---

## Verdict matrix

| # | Owner | Case | Result | Notes |
|---|-------|------|--------|-------|
| 1 | **@Grocky** | Expectant cousins after ActiveGoal (`no i co?` / `i dalej?` / `no dalej?` / `a dalej?` / `no i??`) | **PASS** | All → CONTINUATION + fu + soft_expectant_continue + ACT `execute_discussed_goal` |
| 1a | **@Grocky** | R4 `no i?` regression | **PASS** | Still green |
| 2 | **@Grocky** | Sticky retarget: Spotify → `chodziło mi o discord` → `zrób to` | **PASS** | objective/discussed_action/target=`discord`; execute Discord not Spotify |
| 3 | **@Grocky** | S08 after fail: `co się stało?` | **PASS** | WHY_FAILURE / `why_failure` / ANSWER (not TALK/general_conversation) |
| 4 | **@Toolik** | Cap cold `_probing` + `_last_refresh=0` | **PASS** | spoken `Chwila — sprawdzam…`; tools kept; after refresh WEB_SEARCH=AVAILABLE |
| 5 | **@Memik** | volume `no_verify_spec` S38/S42 | **FAIL (OPEN)** | `verify_spec=None` + `verification_allows_success_claim=True` unchanged |
| 6a | smoke | `_probe_natural_pl.py` | **PASS 17/17** | |
| 6b | smoke | `main.py --agent-smoke` | **PASS 11/11** | |

**Round6 closed from R5 NEW:** items 1–4. **Still OPEN:** Memik volume S38/S42.

---

## 1. @Grocky — expectant cousins (R5 NEW → PASS)

Seed ActiveGoal: status=ACTIVE, objective=`otwórz spotify`.

| Input | class | follow | soft | mode | intent | Result |
|-------|-------|--------|------|------|--------|--------|
| `no i?` | CONTINUATION | True | soft_expectant_continue | ACT | execute_discussed_goal | PASS (R4) |
| `no i co?` | CONTINUATION | True | soft_expectant_continue | ACT | execute_discussed_goal | PASS |
| `i dalej?` | CONTINUATION | True | soft_expectant_continue | ACT | execute_discussed_goal | PASS |
| `no dalej?` | CONTINUATION | True | soft_expectant_continue | ACT | execute_discussed_goal | PASS |
| `a dalej?` | CONTINUATION | True | soft_expectant_continue | ACT | execute_discussed_goal | PASS |
| `no i??` | CONTINUATION | True | soft_expectant_continue | ACT | execute_discussed_goal | PASS |

R5 hole (UNKNOWN/TALK/general_conversation on these cousins) is **closed**.

---

## 2. @Grocky — sticky correction retarget (R5 NEW → PASS)

### Short chain

```text
otwórz spotify           → ACT app          objective=otwórz spotify  discussed=otwórz spotify
chodziło mi o discord    → ACT correction   objective=discord         discussed=discord  target=discord
zrób to                  → ACT execute_discussed_goal  objective=discord (not spotify)
```

### Full chain (with `nie to`)

```text
otwórz spotify → nie to → chodziło mi o discord → zrób to
objs: otwórz spotify → otwórz spotify → discord → discord
```

R5 hole (discord ignored; `zrób to` stuck on Spotify) is **closed**.

---

## 3. @Grocky — S08 `co się stało?` (R5 known open → PASS)

After `mark_result(success=False)` → UNRESOLVED, then:

| Field | Observed |
|-------|----------|
| class | `WHY_FAILURE` |
| mode | `ANSWER` |
| intent | `why_failure` |
| user_goal | Explain the previous failure from verified results |
| goal_status | UNRESOLVED |

Not TALK / `general_conversation`. **Closed.**

---

## 4. @Toolik — Cap cold probing (R5 NEW → PASS)

| State | WEB_SEARCH status | usable | probed | spoken_failure | tools |
|-------|-------------------|--------|--------|----------------|-------|
| `_probing=True`, `_last_refresh=0`, blank published | UNAVAILABLE* | False | **False** | **`Chwila — sprawdzam, czy mam do tego dostęp.`** | `["web_search"]` kept |
| after `refresh(force=True)` | **AVAILABLE** | True | True | (probed offline copy if deps down) | `["web_search"]` |

\*Enum remains `UNAVAILABLE` while `probed=False`, but spoken path is honest (`sprawdzam…`) — does **not** claim permanent offline. Tools not dropped mid-probe. R5 false-offline hole **closed** on product contract.

Note (non-fail): status enum is still `UNAVAILABLE` rather than `DEGRADED` for unprobed blank; honesty gate is spoken + `probed=False` + tools retained.

---

## 5. @Memik — volume `no_verify_spec` S38/S42 — **OPEN**

| Check | Value |
|-------|-------|
| `verify_spec_for(volume_up)` | `None` |
| `verify_spec_for(volume_down)` | `None` |
| SKIPPED `no_verify_spec` → `verification_allows_success_claim` | **True** |
| SKIPPED `verify_tool_unregistered` → allows claim | False (Memik C still green) |

**Still FAIL / OPEN** — false success / silent skip path unchanged from R5 / GOAL S38/S42. No claimed fix verified this round.

---

## 6. Regression smoke — green

| Suite | Result |
|-------|--------|
| `_probe_natural_pl.py` | **17/17 PASS** |
| `main.py --agent-smoke` | **11/11 PASS** |

---

## Handoff (Probe — no patches)

1. **@Grocky** — R5 NEW expectant cousins, sticky Discord retarget, S08 `co się stało?`: **CLOSED this round**. Keep R4 `no i?` green.
2. **@Toolik** — Cap cold `_probing` spoken honesty: **CLOSED**. Optional polish: unprobed blank → `DEGRADED` enum (spoken already OK).
3. **@Memik** — volume `no_verify_spec` success claim (S38/S42): **still OPEN** — only remaining Round6 fail from the retest list.
4. **@Nexo** — not exercised.

Probe stops here.
