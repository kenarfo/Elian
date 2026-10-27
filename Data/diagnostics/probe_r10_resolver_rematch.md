# Probe rematch — R10 DeviceResolver after Toolik fix (AC2 transfer)

Date: 2026-09-11 ~13:45 UTC+1 (Etc/GMT-1). Machine: `a54f5f03-25ab-492d-b5ba-b9eca259a21f` (Ziemniaczek).
Owner: **@Probe** (no production patches). Evidence JSON: `Data/diagnostics/probe_r10_resolver_rematch.json`, harness: `_probe_r10_resolver_rematch.py`.
Python: `C:\Users\kenar\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`.

## Verdict

| Gate | Result |
|------|--------|
| **Toolik R10 2d/2e rematch (AC2 transfer phrases)** | **PASS** |
| Unit `tests.test_device_resolver_substring` | **PASS 4/4** |
| Mergey live transfer notes (resolver) | **PASS** (see M1–M5) |
| Invented transfer (≠ unit strings) | **PASS 6/6** |
| Short object_id ⊆ room-name battery | **PASS 5/5** |
| **`lampe a` E2E delta** | **DOCUMENTED** (EXPECTED exact vs ACTUAL ambiguous) — not a reopen of 2d substring |
| **B10 bare `ehh` → TALK** | **SEMANTIC residual @Grocky** — **not DeviceResolver** |
| **R10 REAL_UX_FAIL closed?** | **NOT CLOSED** — no full production AC1 chain (CM → tool → HA RESULT → spoken VERIFY). Live catalog has only `light.vizzini` (no `light.a`). |

**Return: PASS** (Toolik DeviceResolver rematch / AC2). **Do not close R10 REAL_UX_FAIL** until live/spoken E2E exists.

Prior R10 fails under rematch: `2d_resolver_substring_false_exact`, `2e_room_ignored_false_resolve` (@Toolik). Grocky room_hint / urwane rechecked quickly below.

---

## Live catalog

```
ha_registry entities=20; lights=1
light.vizzini | Inspire Vizzini D56 Vizzini | object_id=vizzini
```

Rematch of `light.a` false-EXACT uses **synthetic pool** matching R10 failure (`light.a` / `light.b`), not live HA mutate. Alias config still maps `lampa`/`światło` → `light.vizzini`.

---

## Mergey live transfer notes (EXACT)

| # | INPUT | EXPECTED | ACTUAL | PASS |
|---|-------|----------|--------|------|
| M1 | `w sypialni jasniej` | resolve **none**, NOT false `light.a` | `none` (score light.a ≪ 0.78); slot not resolved | **PASS** |
| M2 | `sypialnia` | resolve **none**, NOT false `light.a` | `none`; slot not resolved | **PASS** |
| M3 | `jaśniej w sypialni` | resolve **none**, NOT false `light.a` | `none`; slot not resolved | **PASS** |
| M4 | bare `a` | **exact** `light.a` (query=object_id) | `exact` `light.a` | **PASS** |
| M5 | `lampe a` | **ambiguous** (catch E2E delta) | `ambiguous` candidates `[light.a, light.b]` msg `Które urządzenie: Lampa A czy Lampa B?` | **PASS vs Mergey expect**; see delta chain |

Scores on room phrases (post Toolik `_token_span_in`): e.g. `w sypialni jaśniej` → light.a **0.21** (was **0.97** pre-fix).

---

## Invented transfer phrases (≠ unit `"w sypialni jaśniej"`)

| # | INPUT | EXPECTED | ACTUAL resolve | PASS |
|---|-------|----------|----------------|------|
| T1 | `przygas w sypialni` | none / no false `light.a` | `none` | **PASS** |
| T2 | `zgaś to w pokoju dzieci` | none / no false `light.a` | `none` | **PASS** |
| T3 | `jaśniej w łazience` | none / no false `light.a` | `none` | **PASS** |
| T4 | `przyciemnij w salonie` | none / no false `light.a` | `none` | **PASS** |
| T5 | `zgaś w kuchni` | none / no false `light.a` | `none` | **PASS** |
| T6 | `rozjaśnij w biurze` | none / no false `light.a` | `none` | **PASS** |

Controls: two bedroom lights → `ambiguous` **PASS**; `przygas w salonie` + salon entity → `exact light.salon_main` **PASS**; bare `a` still exact **PASS**.

Short-id ⊆ room battery (`light.a`/`light.i`/`light.bi` vs sypialnia/salon/kuchnia/biuro): all **none**, scores ≤ 0.30 **PASS**.

---

## AC1 evidence chains

### Chain A — room utterance, no matching ents (M1 / 2e rematch) — **PASS**

| Step | Value |
|------|-------|
| **INPUT** | `w sypialni jasniej` |
| **SEMANTIC/GOAL** | mode=`ACT` intent=`smart_home_control` goal=`w sypialni jasniej` conf=0.9 domains=`[SMART_HOME]` |
| **CONTEXT** | `room_hint=sypialnia`; slots include room on related `jaśniej w sypialni` |
| **CAPABILITY** | SMART_HOME / light via DeviceResolver |
| **PLAN** | `resolve_device(domain=light, entities=[light.a, light.b])` |
| **TOOL** | `DeviceResolver.resolve_device` (+ `resolve_light_target_answer`) |
| **RESULT** | status=`none` message=`Nie znalazłem urządzenia pasującego do: w sypialni jasniej.` |
| **VERIFY** | entity_id is None; slot status ≠ `resolved`; no `light.a` |
| **RESPONSE** | clarify/none path message (resolver); no false “Wybrałem Lampa A” |
| **EXPECTED** | none / not false EXACT `light.a` |
| **ACTUAL** | none |
| **ROOT CAUSE LAYER** | N/A (fixed) — was `_score_entity` substring `a` ∈ `sypialnia` → 0.97; now `_token_span_in` + room hard-gate |

Same shape **PASS** for M2 `sypialnia`, M3 `jaśniej w sypialni`, T1–T6.

### Chain B — bare object_id (M4) — **PASS**

| Step | Value |
|------|-------|
| **INPUT** | `a` |
| **SEMANTIC/GOAL** | mode=`TALK` (bare letter; semantic not SMART_HOME — OK for resolver unit) |
| **CONTEXT** | no room |
| **CAPABILITY** | DeviceResolver token-span exact name |
| **PLAN** | resolve_device light pool_ab |
| **TOOL** | `resolve_device` |
| **RESULT** | `exact` entity=`light.a` |
| **VERIFY** | status exact + entity_id light.a |
| **RESPONSE** | (resolver exact; no ambiguity) |
| **EXPECTED** | exact light.a |
| **ACTUAL** | exact light.a |
| **ROOT CAUSE LAYER** | N/A — standalone token still matches |

### Chain C — `lampe a` E2E delta (M5) — **DOCUMENTED DELTA**

| Step | Value |
|------|-------|
| **INPUT** | `lampe a` |
| **SEMANTIC/GOAL** | mode=`ACT` intent=`smart_home_control` goal=`lampe a` conf=0.94 domains=`[SMART_HOME]` needs_tools=True |
| **CONTEXT** | no room_hint |
| **CAPABILITY** | SMART_HOME light target resolution |
| **PLAN** | resolve_device → expect single lamp A |
| **TOOL** | `DeviceResolver.resolve_device` |
| **RESULT** | status=`ambiguous` candidates=`[light.a, light.b]` message=`Które urządzenie: Lampa A czy Lampa B?` |
| **VERIFY** | **DELTA** vs ideal UX |
| **RESPONSE** | clarification question (A vs B), not silent pick of A |
| **EXPECTED** | **exact `light.a`** (user named lamp + token `a`) — *or* Mergey transfer note: treat **ambiguous** as the observed catch |
| **ACTUAL** | **ambiguous** (scores: light.a **0.97**, light.b **0.95**; margin=0.08) |
| **ROOT CAUSE LAYER** | **DeviceResolver stem-overlap** — `_stem("lampe")`≡`_stem("lampa")`→`lamp` boosts **all** `Lampa *` to ≥0.9 + domain 0.05; token `a` lifts only A to 0.97 but B stays inside ambiguous_margin. **Not** the R10 substring-in-`sypialnia` bug. |

Probe does **not** patch. Handoff residual for Toolik if product wants `lampe a` → exact when object_id token present.

### Chain D — B10 bare `ehh` (SEMANTIC, not resolver) — **NOTE ONLY**

| Step | Value |
|------|-------|
| **INPUT** | `ehh` |
| **SEMANTIC/GOAL** | utterance: `FILLER_ONLY=True`, `filler_only=True`, `intent_hint=wait` — but `decide_semantics` → mode=**`TALK`** intent=`general_conversation` conf=0.8 |
| **CONTEXT** | n/a |
| **CAPABILITY** | n/a (no SMART_HOME) |
| **PLAN** | none |
| **TOOL** | none (resolver would return none if forced — irrelevant) |
| **RESULT** | TALK path |
| **VERIFY** | filler detected at utterance layer; executive still TALK |
| **RESPONSE** | conversation / TALK (not WAIT silence) |
| **EXPECTED** | WAIT / filler (Grocky R10 intent) |
| **ACTUAL** | **TALK** |
| **ROOT CAUSE LAYER** | **SEMANTIC / Semantic Executive (@Grocky)** — **not DeviceResolver**. Mergey: note separately. |

Related quick Grocky transfer (not closing those tickets here):

| INPUT | Actual |
|-------|--------|
| `ehh włącz lampę` | ACT smart_home_control **PASS** (filler+command) |
| `włącz…` / `włącz` / `otwórz…` | ASK `incomplete_request` domains=[] **PASS** |
| `jaśniej w sypialni` | ACT + slots `room`/`room_hint=sypialnia` **PASS** |

Extra semantic observations (out of Toolik scope): `przygas w sypialni` → TALK (verb not in lighting lexicon); `jaśniej w łazience` → ACT but **`room_hint=""`** (`łazienka` missing from `ROOM_HINTS`).

---

## What was fixed (Toolik) — still holding

1. `_token_span_in` — name match is contiguous **token span**, not character substring (`a` no longer hits inside `sypialnia` / `jasniej`).
2. Room hard constraint in `resolve_device` — if `extract_room_hint` set and no room-matching viable entity → **`none`**, not false EXACT.

Unit regression: `tests.test_device_resolver_substring` **4/4 OK**.

---

## Handoff

1. **@Toolik** — R10 **2d/2e rematch PASS** on AC2 transfer + Mergey notes. Optional residual: **`lampe a` → ambiguous** stem/margin delta (Chain C).
2. **@Grocky** — B10 bare **`ehh` still TALK** despite `filler_only`/hint wait (Chain D); optional: `przygas` lexicon; `łazienka` in `ROOM_HINTS`.
3. **@Probe / Sentry** — **R10 REAL_UX_FAIL remains OPEN** until AC1 E2E on a path that includes spoken RESPONSE after tool RESULT (live pool lacks `light.a`).
4. No production patches from this Probe run.

**PASS** (resolver rematch). **R10 not closed.**
