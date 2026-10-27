# Probe — Memory / ROUTINE regression round 1

Date: 2026-09-10 ~20:45 UTC+1 (Etc/GMT-1).
Machine: `a54f5f03-25ab-492d-b5ba-b9eca259a21f` (Ziemniaczek).
Project: `C:\Users\kenar\Elian`.
Python: `C:\Users\kenar\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`.
Scope: **SOFTWARE only**. **No production patches** (Probe).

Upstream: `@Nexo` `Data/diagnostics/memory_p0_fix_report.md` (A5a claimed PASS; bare „jeszcze raz” DEPENDENCY → soft_goals).

Harnesses:
- `tests/test_reminder_rehydrate.py` → **3/3 OK**
- `_probe_memory_audit.py` → **PASS 22 / FAIL 1** (A 8/0; D „jeszcze raz” FAIL)
- Inline adversarial smoke (create→rehydrate→disable→restart2 + soft + jeszcze raz)

---

## Summary verdict

| # | Scenario | Result | Layer owner |
|---|----------|--------|-------------|
| 1 | Cold ROUTINE create → restart/rehydrate → „już nie…” disable → restart again — **no resurrect** | **PASS** | `@Nexo` (Memory / cognitive_core) — fix holds |
| 2 | Soft „jutro chyba…” must **not** create reminder/ROUTINE | **PASS** | `@Nexo` (cognitive assess soft path) |
| 3 | Bare „jeszcze raz” → soft/follow-up | **FAIL** | **`@Grocky` (soft_goals / follow-up)** — **not** `@Nexo` |

**Memory P0 / ReminderRule rehydrate: PASS.**
**Handoff:** bare „jeszcze raz” → **`@Grocky`** (`core/soft_goals.py` / follow-up policy). Do **not** bounce to Nexo.

---

## 1. Cold ROUTINE no-resurrect — **PASS** (`@Nexo`)

Exact adversarial path:

1. `przypominaj mi o ładowarce` / `przypominaj mi rano o ładowarce` → `RECURRING_REMINDER_RULE`, `enabled_rules=1`, active `ROUTINE=1`
2. `mem.save()` → new `MemoryManager` + empty `CognitiveState` → `rehydrate_reminder_rules_from_memory` → rules≥1, ROUTINE still present
3. `już nie przypominaj mi o ładowarce` → `DISABLE_REMINDER_RULE`, honest ack (not „Nie mam takiego…”), `enabled_rules=0`, active ROUTINE=0
4. Second restart + rehydrate → `enabled_rules=0`, active ROUTINE=0 → **NO_RESURRECT**

Evidence:

| Source | Check | Result |
|--------|-------|--------|
| `test_rehydrate_then_cold_disable_clears_routine` | full path | ok |
| `test_cold_disable_without_prior_rehydrate_clears_routine` | cold disable clears disk ROUTINE | ok |
| `_probe_memory_audit` A5a | disable after cold restart | **PASS** |
| `_probe_memory_audit` A6 | restart after delete | **PASS** |
| Inline smoke | CREATE→REHYDRATE→DISABLE→RESTART2 | `NO_RESURRECT True` |

Inline DISABLE reply sample: *„Dobrze, już nie będę o tym przypominać.”* (not ghost „nie mam”).

Nexo P0 claim for A5a confirmed by Probe.

---

## 2. Soft „jutro chyba…” — **PASS** (`@Nexo`)

| Source | Utterance | Observed | Result |
|--------|-----------|----------|--------|
| unit `test_soft_intent_not_auto_reminder` | `jutro chyba pójdę do sklepu` | `TEMPORARY_FUTURE_INTENT`, rules=0, ROUTINE=0 | ok |
| probe A1_soft | same | not auto-reminder / TEMPORARY | **PASS** |
| Inline smoke | `jutro chyba pojde do sklepu` | `TEMPORARY_FUTURE_INTENT`, rules=0, routines=0 | `NO_REMINDER True` |

Soft path does **not** mint `ReminderRule` or `MemoryType.ROUTINE`.

---

## 3. Bare „jeszcze raz” — **FAIL** → handoff **`@Grocky`**

| Check | Expected | Actual |
|-------|----------|--------|
| `detect_soft_goal("jeszcze raz")` | replay/reexplain soft goal | **`None`** |
| `is_followup_utterance("jeszcze raz")` | True | **`False`** |
| probe D matrix | PASS | **FAIL** (`soft=None fu=False`) |

Owner: **`@Grocky`** — `core/soft_goals.py` (`_REPLAY_LAST` covers „jak wcześniej” / „powtórz…” / „zrób to” but **not** bare „jeszcze raz”). Architectural follow-up / soft_goals policy; **not** Memory 2.0 / ReminderRule.
**Do not assign to `@Nexo`.** Phrase-list sprawl out of Probe scope; Grocky owns soft_goals / follow-up.

Related probe D controls still green: `to`, `ją`, `go`, `nim`, `pierwszy`, `tamto`, `jak wcześniej` = PASS.

---

## Audit rollup (`_probe_memory_audit.py`)

```
VERDICT partial
PASS 22 FAIL 1
A: A1_soft PASS | A2_forget_soft PASS | A2b PASS | A3_rule PASS |
   A4_restart_rule_must_exist PASS | A5a_disable_after_cold_restart PASS |
   A5b_disable_same_session PASS | A6_restart_after_delete PASS
D: jeszcze raz FAIL (only)
```

Unit: `python -m unittest tests.test_reminder_rehydrate -v` → Ran 3 … **OK**.

Artifacts refreshed by this run:
- `Data/diagnostics/memory_worldstate_audit.md`
- `Data/diagnostics/memory_worldstate_audit_evidence.json`
- `Data/diagnostics/probe_memory_routine_round1_console.txt`
- this file

---

## DEPENDENCY / handoff list

1. **`@Grocky`** — bare „jeszcze raz” not recognized as replay/follow-up (`soft_goals` / follow-up architecture). Probe D FAIL. Not Memory P0.
2. Pre-existing (from Nexo report, not re-litigated here): `test_cm_multi_turn_shop_scenario` collab vs cognitive; preference-in-context source mismatch — out of this round’s ROUTINE scope.

**Probe action:** no production code changed.