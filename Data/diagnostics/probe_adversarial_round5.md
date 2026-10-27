# Probe adversarial round 5 — hunt NEW holes (SOFTWARE)

Date: 2026-09-10 ~21:25 UTC+1 (Etc/GMT-1). Machine: `a54f5f03-25ab-492d-b5ba-b9eca259a21f` (Ziemniaczek).
Scope: **SOFTWARE only**. **No production patches from Probe.**
Evidence: `Data/diagnostics/probe_adversarial_round5.json`, `_probe_adversarial_round5.py`, `natural_pl_probe_round5.txt`, `agent_smoke_round5.txt`, `probe_round5_units.txt`.

Ownership: **@Grocky** semantic/follow-ups · **@Memik** plan_cycle / ActiveGoal · **@Toolik** Cap/ToolResult · **@Probe** this report.

Prior closed (not re-opened): slang 17/17 path, wyszukaj X, Memory ROUTINE, jeszcze raz, no i?, Toolik A+B, Memik C+S11/S12.

---

## Verdict matrix

| # | Owner | Case | Result | Notes |
|---|-------|------|--------|-------|
| 1 | **@Grocky** | NEW expectant cousins after ActiveGoal | **FAIL (NEW)** | `no i co?`, `i dalej?`, `no dalej?`, `a dalej?` → TALK |
| 2 | **@Grocky** (+@Memik) | Correction retarget sticky objective | **FAIL (NEW)** | discord correction ignored; zrób to → spotify |
| 3 | **@Toolik** | Cap cold `_probing` without refresh | **FAIL (NEW)** | get(WEB_SEARCH)→UNAVAILABLE mid-probe |
| 4 | **@Memik** | volume `no_verify_spec` success claim | **FAIL (known open)** | S38/S42 reconfirmed |
| 5 | **@Grocky** | `co się stało?` after fail (S08) | **FAIL (known open)** | TALK/general_conversation |
| 6 | **@Toolik** | FastPath lamp `domain` schema (S03) | **PASS/CLOSED** | `domain` now in turn_on_device params |
| 7 | **@Toolik** | Internet vs WEB_SEARCH dual (tool present) | **PASS** | gate allows when tool registered |
| 8 | **@Grocky** | R4 `no i?` family + dalej/kontynuuj/no to?/i? | **PASS** | no regression; partial cousins green |
| 9 | smoke | `_probe_natural_pl.py` | **PASS 17/17** | |
| 10 | smoke | `main.py --agent-smoke` | **PASS 11/11** | verify matched |
| 11 | **@Memik** | C + S11/S12 units | **PASS 9/9** | |
| 12 | **@Toolik** | A `_message_success` | **PASS 6/6** | |

---

## 1. NEW — @Grocky expectant cousins

Seed ActiveGoal: status=ACTIVE, objective=`otwórz spotify`.

### Still FAIL (TALK / not expectant)

| Input | class | follow | soft | mode | intent |
|-------|-------|--------|------|------|--------|
| `no i co?` | UNKNOWN | False | null | TALK | general_conversation |
| `no i co` | UNKNOWN | False | null | TALK | general_conversation |
| `i dalej?` | UNKNOWN | False | null | TALK | general_conversation |
| `i dalej` | UNKNOWN | False | null | TALK | general_conversation |
| `no dalej` | UNKNOWN | False | null | TALK | general_conversation |
| `no dalej?` | UNKNOWN | False | null | TALK | general_conversation |
| `a dalej?` | UNKNOWN | True | null | TALK | general_conversation |

**Expected:** same contract as closed `no i?` → CONTINUATION + fu + `soft_expectant_continue` + ACT `execute_discussed_goal`.
**Layer:** `_EXPECTANT_CONTINUE` / soft_goals / semantic_executive.

### PASS cousins (already handled / acceptable)

| Input | class | mode | intent | note |
|-------|-------|------|--------|------|
| `no i?` / `no i` / `i co?` / `i co dalej?` / `co dalej?` | CONTINUATION | ACT | execute_discussed_goal | R4 regression green |
| `i?` | CONTINUATION | ACT | execute_discussed_goal | soft_expectant_continue |
| `no to?` / `dalej` / `kontynuuj` / `to dalej` | CONTINUATION | ACT | execute_discussed_goal | soft may be None — OK |
| `ehh zgaś` / `eh zgaś` / `ech zgaś` | ACTION_REQUEST | ACT | smart_home_control | filler+zgaś OK |
| `nie…` / `nie...` / `nie` | REJECTION | WAIT | cancel_goal | OK |

### Optional edge (not a fail)

`no i??` / `no i???` → UNKNOWN/TALK (regex allows one `?`) — same note as Round4.

---

## 2. NEW — correction chain sticky ActiveGoal

```text
otwórz spotify → ACTIVE objective=otwórz spotify
nie to         → CORRECTION ACT correction (objective unchanged)
chodziło mi o discord → ACT correction, user_goal=Correct or resolve the previous choice
                     → objective/discussed_action STILL otwórz spotify
zrób to        → ACT execute_discussed_goal → still spotify
```

**Hole:** explicit retarget does not rewrite `ActiveGoal.objective` / `discussed_action` / `target`. Pending/execute path will open the wrong app.
**Owners:** **@Grocky** (correction semantics / observe_utterance) · **@Memik** if store update belongs in ActiveGoal apply path.

---

## 3. NEW — @Toolik Cap cold probing

| State | WEB_SEARCH status | usable |
|-------|-------------------|--------|
| `get()` after reset (auto-refresh) | AVAILABLE | True |
| `_probing=True`, `_last_refresh=0` | **UNAVAILABLE** | **False** (+ spoken offline) |
| `refresh(force=True)` | AVAILABLE | True |

**Hole:** mid-refresh callers can see false offline. Dual Internet vs WEB_SEARCH otherwise OK when `web_search` is in the tool registry (gate allows); Cap consulted when tool missing.

---

## 4. Known open reconfirmed

### @Memik — volume `no_verify_spec`

- `verify_spec_for(volume_up/down) = None`
- SKIPPED reason `no_verify_spec` → `verification_allows_success_claim=True`
- Compound unit path: `phase=verify tool=volume_down status=skipped reason=no_verify_spec` then success observation
- GOAL S38/S42 still open (false success / silent skip)

Memik C still green: `verify_tool_unregistered` → claim denied.

### @Grocky — S08 status question

After `mark_result(success=False)` → UNRESOLVED, then `co się stało?` → `INFORMATION_REQUEST` / `TALK` / `general_conversation` (not why_failure/status).
Note: `no i?` after fail correctly → ACT `resume_unresolved_goal` (good).

---

## 5. FastPath lamp domain (S03) — CLOSED at schema

`turn_on_device.parameters.properties` includes `domain`. Invoke with `domain` no longer returns `Niedozwolone argumenty: domain` (HA resolve/timeout instead — HARDWARE). Schema reject hole from GOAL S03 is **closed**; leftover HA false-success S18/S45 not fully re-litigated this round beyond smoke clarify path.

---

## 6. Regression smoke — all green

| Suite | Result |
|-------|--------|
| `_probe_natural_pl.py` | **17/17 PASS** |
| `main.py --agent-smoke` | **11/11 PASS** (`window_exists`/`get_now_playing` reason=matched) |
| `VerifyToolUnregisteredTests` + `PendingRetryRetentionTests` | **9/9 PASS** |
| Toolik A `_message_success` | **6/6** fail-closed |
| bare „jeszcze raz” | RETRY + follow + soft_replay_bare |
| R4 „no i?” family | CONTINUATION + ACT execute_discussed |

---

## Handoff (Probe — no patches)

1. **@Grocky** — NEW: expand expectant cousins (`no i co?`, `i dalej?`, `no dalej?`, `a dalej?`); NEW: correction retarget must update ActiveGoal; known: S08 `co się stało?`.
2. **@Memik** — NEW co-own correction sticky objective; known: volume `no_verify_spec` success claim (S38/S42). C + S11/S12 still green.
3. **@Toolik** — NEW: Cap `_probing` cold UNAVAILABLE edge; dual Internet/WEB_SEARCH tool-present path OK; S03 domain schema CLOSED; A still green.
4. **@Nexo** — not exercised.

Probe stops here.
