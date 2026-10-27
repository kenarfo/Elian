# Probe adversarial round 10 — hunt NEW Jarvis SOFTWARE holes

Date: 2026-09-10 ~22:25 UTC+1 (Etc/GMT-1). Machine: `a54f5f03-25ab-492d-b5ba-b9eca259a21f` (Ziemniaczek).
Scope: **NEW SOFTWARE holes** after R1–R9 closed. Targets: ASR/urwane, multi-entity/room, browser honesty, repair loops, OPEN residuals, smokes. **No production patches from Probe.**
Evidence: `Data/diagnostics/probe_adversarial_round10.json`, `_probe_adversarial_round10.py`, `natural_pl_probe_round10.txt`, `agent_smoke_round10.txt`.

Ownership: **@Grocky** ASR + room slots · **@Memik** ActiveDevice bind / repair · **@Toolik** DeviceResolver · **@Nexo** browser · **@Probe** this report.

Prior closed (not re-opened): slang, search, Memory ROUTINE, jeszcze raz, no i?, cousins, sticky, S08, Cap cold, volume verify (R7), C, S11/S12, Toolik A+B, compounds/cancel/pronouns (R8/R9), Memik #3b partial (R9 dogrywka).

---

## Verdict matrix

| # | Owner | Case | Result | Notes |
|---|-------|------|--------|-------|
| 1a | **@Grocky** | ASR `ehh` filler/wait | **FAIL (NEW)** | FILLER_ONLY misses ehh/eh/ahh → TALK act |
| 1b | **@Grocky** | filler+command still ACT | **PASS** | yyy/ehh/eee + action OK |
| 1c | **@Grocky** | urwane `włącz…` / bare `włącz` | **FAIL (NEW)** | false APP intent; incomplete=False |
| 2a | **@Grocky** | room not in slots | **FAIL (NEW)** | w sypialni jaśniej loses room_hint |
| 2b | **@Grocky+@Memik** | ActiveDevice ignores room | **FAIL (NEW)** | binds salon while user said sypialnia |
| 2c | **@Toolik** | ambiguous bedroom clarify | **PASS** | 2 bedroom lights → ASK |
| 2d | **@Toolik** | resolver substring false EXACT | **FAIL (NEW)** | object_id a ∈ sypialnia → 0.97 |
| 2e | **@Toolik+@Grocky** | room ignored → false resolve | **FAIL (NEW)** | resolves light.a with no room match |
| 3a | **@Nexo** | browser DISABLED honesty | **PASS** | wyłączone, not nie umiem |
| 3b | **@Nexo** | browser DEGRADED honesty | **PASS** | method-level ograniczony; registry clamps when opt-out |
| 3c | **@Nexo** | browser path vs nie umiem | **PASS** | tool path uses DISABLED_MSG |
| 4a | **@Memik** | timeout → RETRY | **PASS** | ConnectTimeout categorized |
| 4b | **@Memik** | idempotent repair exhaust | **PASS** | RETRY×2 then FAIL |
| 4c | **@Memik+@Toolik** | not-found fail-closed | **PASS** | no fake success |
| 4d | **@Memik** | relative no remutate | **PASS** | volume_down |
| 5a | **@Memik** | R9 #3b residual | **PASS** | still not Gotowe |
| 5b | **@Memik** | cancel store ACTIVE | **OPEN (INFO)** | R9 informational |
| 6a | smoke | natural_pl | **PASS 17/17** | |
| 6b | smoke | agent-smoke | **PASS 11/11** | |

**NEW FAILs: 6** (`1a_asr_ehh_filler, 1c_asr_truncated_wlacz, 2a_room_not_in_slots, 2b_activedevice_ignores_room, 2d_resolver_substring_false_exact, 2e_room_ignored_false_resolve`). **OPEN INFO: 1**. Smokes green. R1–R9 closed items stay closed.

---

## 1. ASR-ish / urwane

### 1a `ehh` not filler (**NEW FAIL @Grocky**)

`FILLER_ONLY` matches `y+|e+|yyy+|eee+|uhm+|hmm+|…` but **not** `ehh`/`eh`/`ahh`/`aaa`/`mm` → `filler_only=False`, `intent_hint=act`, semantics TALK/general_conversation.

Real ASR often emits `ehh`. Expected: wait/filler like `yyy`/`eee`.

### 1b filler+command (**PASS**)

`yyy włącz lampę`, `ehh włącz światło`, `eee otwórz spotify` → ACT with correct intents.

### 1c truncated / bare verb → false APP (**NEW FAIL @Grocky**)

| Input | incomplete | mode | intent | domains |
|-------|------------|------|--------|---------|
| `włącz…` | False | ASK | app | ['APP'] |
| `włącz...` | False | ASK | app | ['APP'] |
| `yyy włącz…` | False | ASK | app | ['APP'] |
| `otwórz…` | False | ASK | app | ['APP'] |
| `włącz` | True | ASK | app | ['APP'] |

ASR repair strips ellipsis → `włącz` then APP ASK. Bare `włącz` also APP. Expected: incomplete / clarify target, not APP domain.

---

## 2. Multi-entity / room

### 2a room not extracted to slots (**NEW FAIL @Grocky**)

`w sypialni jaśniej` slots = `{'implicit_light': True, 'desired': 'brighter', 'domain': 'light'}` — room present in text (`['sypialnia']`) but **no room/room_hint slot**.

### 2b ActiveDevice bind ignores room (**NEW FAIL @Grocky+@Memik**)

CM path ~L2034–2056: `implicit_light` + ActiveDevice(light) binds `entity_id` **without checking room in utterance**. Salon ActiveDevice + `w sypialni jaśniej` → wrong target.

### 2c ambiguous bedroom (**PASS @Toolik**)

Two sypialnia lights → `ambiguous` + option question (good).

### 2d DeviceResolver substring false EXACT (**NEW FAIL @Toolik**)

- `_entity_names(light.a)` includes bare `['lampa a', 'a']`
- `_score_entity(..., 'sypialnia')` = **0.97** (cutoff 0.78)
- `resolve_device('w sypialni jaśniej')` → **exact light.a**

Root: `_score_entity` does `name in query_norm` — single-letter object_id `a` matches inside `sypialnia`/`jaśniej` → 0.92 + domain 0.05 = 0.97.

### 2e room ignored → false resolve (**NEW FAIL @Toolik+@Grocky**)

No room-matching entities + utterance `w sypialni jaśniej` → `{'status': 'resolved', 'target': 'light.a', 'message': 'Wybrałem Lampa A.'}` — **resolved** instead of clarify/none.

---

## 3. Browser DEGRADED/DISABLED honesty

| Case | Result |
|------|--------|
| DISABLED click message | **PASS** — `Sterowanie przeglądarką jest wyłączone w konfiguracji.` |
| DEGRADED spoken (probed) | **PASS** — limited-mode PL, not nie umiem |
| vs unknown_tool nie umiem | **PASS** — browser tools use DISABLED_MSG |

Note: live registry clamps DEGRADED→DISABLED when `integrations.browser.enabled=false` (still honest). Method-level DEGRADED+probed speaks trybie ograniczonym.

---

## 4. Repair / replan after deliberate tool fail

| Case | Result |
|------|--------|
| ConnectTimeout | **PASS** — TIMEOUT + RETRY |
| Idempotent verify-fail loop | **PASS** — RETRY then repair_exhausted |
| device not found | **PASS** — FAIL no_alternate:family_fallback |
| volume_down relative | **PASS** — no overshoot remutate |

No infinite replan / false success on deliberate fails observed.

---

## 5. OPEN residuals scan

| Item | Result |
|------|--------|
| R9 #3b bad-shape Gotowe | **PASS** (CLOSED stays closed) |
| ActiveGoal ACTIVE after cancel envelope | **OPEN (INFO)** — informational, not expanded |

---

## 6. Regression smoke

| Suite | Result |
|-------|--------|
| `_probe_natural_pl.py` | **PASS 17/17** |
| `main.py --agent-smoke` | **PASS 11/11** |

---

## Handoff (Probe — no patches)

1. **@Grocky** — **NEW FAILs**: `ehh` filler gap; truncated/bare `włącz…` → false APP; room not slotted on `w sypialni jaśniej`; (shared) ActiveDevice room-ignore.
2. **@Memik** — **NEW FAIL** (shared): CM ActiveDevice bind without room check. Repair loops **PASS**. R9 #3b stays closed; cancel-store residual remains INFO OPEN.
3. **@Toolik** — **NEW FAILs**: DeviceResolver short object_id substring → false EXACT; room utterance + unmatched pool → false resolve. Ambiguous multi-light clarify **PASS**.
4. **@Nexo** — Browser DISABLED/DEGRADED honesty **PASS** (not bare nie umiem).
5. Do **not** reopen R1–R9 closed SOFTWARE items.

Probe stops here.
