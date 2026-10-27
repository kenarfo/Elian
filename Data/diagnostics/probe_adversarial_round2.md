# Probe adversarial round 2 — retests after team fixes

Date: 2026-09-10 (UTC+1 / Etc/GMT-1). Machine: `a54f5f03-25ab-492d-b5ba-b9eca259a21f` (Ziemniaczek).
Scope: **SOFTWARE only**. **No production patches from Probe.**
Evidence: `Data/diagnostics/probe_adversarial_round2.json`, `_probe_adversarial_round2.py`.

Ownership: **@Grocky** semantic/follow-ups · **@Toolik** ToolResult honesty · **@Memik** plan_cycle / verify · **@Probe** this report.

---

## Verdict matrix

| # | Owner | Case | Round1 | Round2 | Notes |
|---|-------|------|--------|--------|-------|
| 1 | **@Grocky** | bare „jeszcze raz” → soft_replay_bare + follow-up True + RETRY | (handoff) | **PASS** | Also related soft follow-ups green |
| 2 | **@Toolik** | FAIL A — HA connect/timeout msg → success=False via `_message_success` | FAIL | **PASS (CLOSED)** | Markers cover błąd połączenia / timed out / timeout / ConnectTimeout / connection error |
| 3 | **@Toolik** | FAIL B — type_text deadline+empty observe → not success=True | FAIL | **PASS (CLOSED)** | `test_unknown_side_effect_is_partial_and_idle` green; deadline `>=` + unknown_side_effect path |
| 4 | **@Memik** | FAIL C — open_app success when verify skipped (`verify_tool_unregistered`) | OBSERVED | **FAIL (STILL OPEN)** | Smoke + plan_cycle still set `verified_success=True` on SKIPPED |
| 5 | **@Grocky** | `_probe_natural_pl.py` | 17/17 | **PASS 17/17** | No regression |
| 6 | smoke | `main.py --agent-smoke` | 11/11 | **PASS 11/11** | Still emits Memik C skip lines |

---

## 1. @Grocky — bare „jeszcze raz”

**Result: PASS**

| Input | utterance_class | is_followup | soft_source |
|-------|-----------------|-------------|-------------|
| `jeszcze raz` | RETRY | True | soft_replay_bare |
| `Jeszcze raz.` | RETRY | True | soft_replay_bare |
| `jeszcze raz!` | RETRY | True | soft_replay_bare |

With journal (`last_journal_action.tool=turn_on_device`): source upgrades to **`soft_replay`** (not bare) — expected.

### Related soft follow-ups (easy)

| Input | class | follow-up | soft |
|-------|-------|-----------|------|
| spróbuj jeszcze raz | RETRY | True | soft_replay_bare |
| powtórz | RETRY | True | (none — class+follow-up enough) |
| zrób tak jak wcześniej | UNKNOWN* | True | soft_replay_bare |
| ponów | RETRY | True | (none) |

\*„zrób tak jak wcześniej” is follow-up True via soft/`_REPLAY_LAST` / discourse patch even when cold class is UNKNOWN — acceptable for this retest.

**Layer:** @Grocky — closed.

---

## 2. @Toolik — FALSE SUCCESS A (CLOSED)

**Result: PASS**

| Message | `_message_success` | ToolResult.success |
|---------|--------------------|--------------------|
| `Błąd połączenia z Home Assistant.` | False | False |
| `Blad polaczenia z Home Assistant.` | False | False |
| `Connection to 192.168.0.31 timed out.` | False | False |
| `ConnectTimeout` | False | False |
| `connection error` | False | False |
| `Nie mogę teraz połączyć z Home Assistant.` | False | False |

**Layer:** @Toolik `core/builtin_tools.py` `_message_success` — closed.

---

## 3. @Toolik — FALSE SUCCESS B (CLOSED)

**Result: PASS**

- Unit: `StageCHardwareFix3Tests.test_unknown_side_effect_is_partial_and_idle` → **was_successful=True**
- Observed path: `[VERIFY] expected='XYZ' observed='' status=BASIC` then returns **unknown_side_effect** (not success=True)
- `deadline_exceeded` now inclusive (`time.time() >= _deadline`); `_act_type` honesty branch returns `ToolResult.unknown_side_effect` when typed+target without content proof

**Layer:** @Toolik `core/computer_agent.py` — closed.

---

## 4. @Memik — FAIL C (STILL OPEN)

**Result: FAIL** — reproduced on agent-smoke.

### Smoke evidence (`możesz odpalić mi spotify`)

```
tool=open_app status=success success=True message=Uruchomiłem aplikację.
phase=execute tool=open_app success=True
phase=verify tool=window_exists status=skipped reason=verify_tool_unregistered
```

Also: `get_now_playing` verify → same `verify_tool_unregistered`.

### Code root

`core/plan_cycle.py` `run_execute_verify_repair` ~L1123–1128:

```python
if verification.status in (VerifyStatus.SKIPPED, VerifyStatus.UNKNOWN) and execution.success:
    return StepCycleOutcome(..., verified_success=True, ...)
```

Same pattern again ~L1272–1275 after repair.
`verify_effect` still returns SKIPPED + `reason=verify_tool_unregistered` when `registry.get(verify_tool) is None` (~L565–567).

So: **execute success + unregistered verify tool ⇒ verified_success=True** — COMPLETE-without-window-proof class remains.

**Expected (honesty):** register `window_exists` in smoke/dry registry, **or** treat `verify_tool_unregistered` as non-verified (not `verified_success=True`) for mutation tools like `open_app`.

**Layer owner:** **@Memik** (`plan_cycle.verify_effect` + SKIPPED→verified_success gate). Secondary: tool registration for computer verify tools in smoke path (@Toolik if registry wiring).

---

## 5. Quick smoke

| Suite | Result |
|-------|--------|
| `_probe_natural_pl.py` | **17/17 PASS** (`natural_pl_probe_before.json` / probe stdout) |
| `main.py --agent-smoke` | **11/11 PASS** (SOFTWARE gate; HA host still ConnectTimeout = HARDWARE/network) |

---

## Handoff (Probe — no patches)

1. **@Memik** — FAIL C still open: do not set `verified_success=True` when verify reason is `verify_tool_unregistered` for effectful tools (`open_app`, media verify); and/or ensure `window_exists` / `get_now_playing` are registered wherever smoke/agent runs.
2. **@Grocky** — no further action (bare jeszcze raz + 17/17 green).
3. **@Toolik** — A+B closed; no further action on those two.
4. **@Nexo** — not exercised this round.

Probe stops here.
