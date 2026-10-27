# Memory P1.4 — Soft invalidation (nieważne) without durable wipe

**Date:** 2026-09-11 (UTC+1 / Etc/GMT-1)
**Owner:** Nexo (Memory 2.0 / WorldState / candidate / persistence)
**Machine:** Ziemniaczek (`a54f5f03-25ab-492d-b5ba-b9eca259a21f`)
**Root:** `C:\Users\kenar\Elian`

## Goal

Phrases like „dobra nieważne” / „w sumie nieważne” / „już nieważne” / „nieważne z tym” must invalidate the **session** goal / TemporaryIntention / CandidateMemory / short-term focus **without** wiping durable FACT / PREFERENCE / ROUTINE (unless explicit forget or subject-matched DISABLE_RULE).

## Before (bug)

Evidence chain from code + prior audit:

1. Soft „w sumie nieważne” already assessed as `CHANGE_OF_MIND` / `INVALIDATION` (`memory_worldstate_audit.md` A/A2_forget_soft **PASS**).
2. But `apply_assessment` on `INVALIDATE` called **`memory.forget(intention.text)`** after `invalidate_intention(...)`.
3. `MemoryManager.forget` → preferences + long_term + typed_store with **aggressive term-overlap** matching (`len(terms & blob_terms) >= max(1, (len(terms)+1)//2)`).
4. Soft shop intention text sharing tokens with a durable FACT (e.g. „sklep”) could delete durable entries.
5. `should_forget=True` on CHANGE_OF_MIND semantically invited durable wipe.
6. CM order: `collab.try_handle` (`cancel_idle`) could beat Cognitive Core on „jednak nieważne”, so session intention invalidation never ran on the live CM path.

**Not touched (by design):** Planner, ToolRegistry, GUI, voice, Semantic Executive internals. Single Memory 2.0 store (`elian_memory.json`) retained.

## After (fix)

### `core/cognitive_core.py`

- Expanded `_INVALIDATE` with transfer variants: `już nieważne`, `dobra nieważne`, `nieważne z tym`, `w sumie (to )?nieważne`.
- CHANGE_OF_MIND: `should_forget=False`, `persistence=NONE`.
- New `invalidate_session_intention(...)`:
  - marks TemporaryIntention invalidated
  - invalidates linked / same-subject session candidates
  - clears short-term **focus topic** when it mirrors the soft intent (never `short.clear()`)
  - `note_session({cognitive_invalidation: true, ...})`
  - **does not** call `memory.forget`
- Apply path: CHANGE_OF_MIND / INVALIDATE → session scope only. Durable wipe remains only on EXPLICIT_FORGET (CM) or DISABLE_RULE (subject-matched ROUTINE).

### `core/conversation_manager.py`

- When `looks_invalidation(text) && has_active_referent(cognitive)`, defer collab short-circuit so Cognitive Core owns soft invalidation (memory handoff). Observe still runs.

### Tests (`tests/test_cognitive_core.py`)

- `test_soft_invalidation_preserves_durable`
- `test_invalidation_transfer_variants` (≥3 beyond original)
- `test_juz_niewazne_does_not_wipe_unrelated_routine` (miałeś… / już nieważne)
- `test_explicit_disable_still_removes_matching_routine`

## AC2 transfer matrix

| Phrase | rationale | session intent cleared | durable intact | PASS |
|--------|-----------|------------------------|----------------|------|
| w sumie nieważne (original) | CHANGE_OF_MIND | yes | yes (4/4) | PASS |
| dobra nieważne | CHANGE_OF_MIND | yes | yes | PASS |
| już nieważne | CHANGE_OF_MIND | yes | yes | PASS |
| nieważne z tym | CHANGE_OF_MIND | yes | yes | PASS |
| jednak nieważne | CHANGE_OF_MIND | yes | yes | PASS |
| miałeś… / już nieważne | CHANGE_OF_MIND | yes | ROUTINE left=1 | PASS |

Probe JSON: `Data/diagnostics/memory_p1_invalidation_probe.json` (`all_pass: true`).

## Test results

```
python -m unittest tests.test_cognitive_core -v
→ Ran 28 tests in ~2.2s — OK

python -m unittest tests.test_memory_v2 tests.test_reminder_rehydrate -v
→ Ran 18 tests in ~0.3s — OK
```

Logs: `Data/diagnostics/_p14_unittest.txt`, `_p14_related_unittest.txt`.

## Changed files

- `core/cognitive_core.py` — session-scoped invalidation (main fix)
- `core/conversation_manager.py` — defer collab when cognitive referent active
- `tests/test_cognitive_core.py` — P1.4 regression tests
- `Data/diagnostics/memory_p1_invalidation_fix.md` — this report
- `Data/diagnostics/memory_p1_invalidation_probe.json` — probe evidence

## DEPENDENCY notes

- **Memik / Grocky:** none required for this fix. Soft invalidation is local Cognitive Core + MemoryManager. No second memory store introduced.
- **Semantic Executive:** unchanged; CM only gates collab when memory referent is active. If SE emits `cancel_goal` without active cognitive referent, collab path still applies (idle cancel).
- Explicit „zapomnij o X” durable path still owned by MemoryManager / CM parse_explicit (not weakened).
- DISABLE_RULE („już nie przypominaj mi o ładowarce”) still subject-matches and may forget matching ROUTINE — intentional.

## Success criteria

| Criterion | Status |
|-----------|--------|
| Invalidation clears session/candidate/intent | PASS |
| Durable intact unless explicit forget / matched DISABLE | PASS |
| No second memory store | PASS |
| No Planner / Tool edits | PASS |
| Transfer ≥3 phrases | PASS (5+ variants) |
