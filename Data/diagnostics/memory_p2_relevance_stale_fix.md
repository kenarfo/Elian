# Memory P2.2 — Candidate session-only / STALE resolve / relevance filter

**Date:** 2026-09-11 14:35 UTC+1 (Etc/GMT-1)
**Owner:** Nexo (Memory 2.0 / WorldState / candidate / relevance)
**Machine:** Ziemniaczek (`a54f5f03-25ab-492d-b5ba-b9eca259a21f`)
**Root:** `C:\Users\kenar\Elian`
**Prior run:** died with ZERO report — this run completes Goals 1–4.

## Scope (kept)

- Memory 2.0 / WorldState / CandidateMemory / retrieval relevance only.
- **Not touched:** Planner, ToolRegistry, GUI, voice, Semantic Executive internals.
- Single WorldState (`core/world_state.py`) and single Memory store retained.
- Phrase regex lists **not** expanded.

## Gaps found (before)

| # | Gap | Evidence |
|---|-----|----------|
| G1 | `promote_candidate_to_memory(..., explicit=False)` promoted `status=candidate` / `kind=intention` into persistent `TEMPORARY` | Probe: weak intention → `temporary_*` written |
| G2 | `resolve_pronoun_referent` / `active_device_from_world` treated `STALE` like live (`status not in (UNKNOWN, UNAVAILABLE)`) | After TTL+400s still returned `light.vizzini` with `status=STALE`; mentioned fallback returned `status=STALE` as driver |
| G3 | Soft-cancelled TEMPORARY (if present) could still hit `typed.retrieve` / `select_compact` — no status relevance gate | `retrieve` only skipped `expired`; P1.4 invalidated session intention but not typed TEMPORARY rows |

P1.4 soft invalidation of session TemporaryIntention/CandidateMemory already landed; this pass adds the retrieval/WorldState architectural seals.

## Fixes

### 1. `core/cognitive_core.py` — Candidate session-only until reinforced
- `promote_candidate_to_memory`: refuse unless `explicit` **or** `status == "reinforced"`; refuse `kind in (intention, temporary, session, note)` unless explicit.
- Soft uncertain future intents remain CognitiveState-only (STORE_INTENTION unchanged — no auto-promote).
- `invalidate_session_intention`: mark matching typed **TEMPORARY** rows `status=invalidated` (by linked id / provenance.candidate_id / entity-text overlap); **never** wipe FACT/PREFERENCE/ROUTINE; `memory.save()` when marks applied.

### 2. `core/world_state.py` — STALE must not drive resolve
- Added `_is_live_status` → KNOWN/CURRENT only.
- `resolve_pronoun_referent`: active_referent / smart_home / app require live status; removed mentioned-object STALE driving fallback.
- `active_device_from_world(*, now=)`: same live gate.
- Volume speech-act still resolves as `kind=volume` (not a stale device drive).

### 3. `memory/typed_store.py` + `memory/retrieval.py` — relevance filter
- `TypedStore.mark_status(id, status)`.
- `retrieve` skips `invalidated|cancelled|forgotten|expired|cleanup_candidate`.
- `is_retrieval_relevant()` + applied in `compact_entries` / `select_compact` merge.

### 4. `core/context_selection.py`
- Attach `active_referent` / media / volume fragments only when status is `KNOWN`.

## Tests

New: `tests/test_memory_p2_relevance_stale.py`

```
python -m unittest tests.test_memory_p2_relevance_stale \
  tests.test_world_state_canonical tests.test_cognitive_core tests.test_memory_v2 -v
→ Ran 62 tests in ~12.9s — OK
```

Log: `Data/diagnostics/_p22_unittest.txt`

## AC matrix

| Goal | Result |
|------|--------|
| 1 CandidateMemory session-only until reinforced; soft uncertain ≠ persistent | **PASS** |
| 2 STALE WorldState does not drive pronoun/device resolve | **PASS** |
| 3 After CHANGE_OF_MIND, retrieval does not resurface soft TEMPORARY; durable FACT survives | **PASS** |
| 4 Tests + this report (both paths) | **PASS** |

## Changed files

- `core/cognitive_core.py`
- `core/world_state.py`
- `core/context_selection.py`
- `memory/typed_store.py`
- `memory/retrieval.py`
- `tests/test_memory_p2_relevance_stale.py` (new)
- `Data/diagnostics/memory_p2_relevance_stale_fix.md` (this report)
- `/workspace/memory_p2_relevance_stale_fix.md` (mirror)

## Not modified

Planner, ToolRegistry, GUI, voice, SE, phrase regex lists, second Memory/WorldState store.
