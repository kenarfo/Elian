# Natural Polish semantic audit — Elian

Date: 2026-09-10 (UTC+1). Machine: Kenarf Craft Windows (`a54f5f03-25ab-492d-b5ba-b9eca259a21f`).
Scope: semantic/context only. Probe: `_probe_natural_pl.py`.

## Summary

| Gate | Result |
|------|--------|
| Natural PL probe (before) | **8/17** passed |
| Natural PL probe (after) | **17/17** passed |
| `tests.test_active_goal_semantics` + `test_goal_first_natural_speech` + behavioral eval subset | **OK** (34 tests) |
| Full `unittest discover` | 1156 tests; **env-limited**: 17× `faster_whisper` import errors; 3 residual fails unrelated to this fix (see below) |
| `python -c "from core.agent_smoke import run_agent_smoke; ..."` | **11/11 PASS** |
| `python main.py --agent-smoke` | **BLOCKED** — `ModuleNotFoundError: faster_whisper` / `torch` in this Python runtime |

## Method (OBSERVED)

1. Read AGENTS.md, ARCHITECTURE.md, semantic/active_goal/utterance_class/context wiring, existing tests.
2. Wrote dry probe driving `classify_utterance` → `decide_semantics(allow_llm=False)` → `ConversationGoalStore.observe_utterance` with multi-turn context.
3. Collected failing paths; fixed architecture (discourse continuity, learned-routine shadowing, state goals, pronoun completeness).
4. Re-probed; ran unit tests + agent smoke.

Python used: `C:\Users\kenar\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` (3.12.13). No project clone.

---

## Failing transcripts (BEFORE) — input → expected → actual

### 1. `ciemno tu` (and cascades in weź/no to/dobra scenarios)

- **Expected:** ambient state GOAL → `smart_home_control` / brighter / SMART_HOME
- **Actual:** `CHANGE_PARAMETER` + `param_bare` (intent already ACT smart_home_control)
- **Evidence (OBSERVED):** state-goal regex required `za ciemno` / `jest … ciemn` / `trochę tu ciemn`; bare `ciemno tu` only hit `_param_hint` → CHANGE_PARAMETER.
- **Root cause:** incomplete ambient-state discourse shape (architecture), not missing phrase list.
- **Fix:** broaden state-goal / `_param_hint` for location deixis `ciemno tu` / `tu ciemno` (and jasno twin).

### 2. `nie aż tak` (after brighten → COMPLETED goal)

- **Expected:** CHANGE_PARAMETER darker / smart_home_control
- **Actual:** UNKNOWN + **learned_routine** (`set_device_value` recipe in `Data/learned_routines.json`, count=19)
- **Evidence:** `_RELATIVE_DAMPEN` required `has_active_goal`; COMPLETED goals set `has_active_goal=False`. `decide_semantics` early-returned learned before discourse.
- **Root cause:** (a) parameter continuity after COMPLETED not treated as discourse context; (b) learned recipes shadowing relative dampen.
- **Fix:** dampen gates on `has_param`/`last_parameter`; map less→darker on brightness; revive COMPLETED goals on param follow-ups; `_discourse_blocks_learned`.

### 3. `zgaś ją` (after light on)

- **Expected:** ACTION_REQUEST ACT smart_home_control needs_tools
- **Actual:** ASK smart_home_control needs_tools=False confidence 0.7 (`intent_family` incomplete pronoun)
- **Evidence:** pass1 marks SMART_HOME incomplete without entity; weak-confidence gate strips tools.
- **Root cause:** pronoun device acts not completed from recent SMART_HOME / parameter context.
- **Fix:** in `classify_pass1`, pronoun+device action + recent SMART_HOME/parameter → ACT continuation 0.92.

### 4. `trochę ciszej` (after media)

- **Expected:** system_volume / CHANGE_PARAMETER quieter
- **Actual:** learned_routine (`volume_down`, count=21) via early decide_semantics shadow
- **Evidence:** `_VOLUME_BARE` matched bare `ciszej` only; intent_family already SYSTEM complete but never reached.
- **Fix:** `_VOLUME_BARE` allows optional `trochę`; discourse/volume blocks learned.

### 5. `nie to` (after media pick)

- **Expected:** CORRECTION/REJECTION of last choice
- **Actual:** REFERENCE / general_conversation (or CONTINUATION execute_discussed when goal open)
- **Evidence:** `_CORRECTION` had `nie, nie to` / `nie ten` but not bare `nie to`.
- **Fix:** bare `nie to` → CORRECTION; CORRECTION+MEDIA → play_media reject/retry; preserve pass-1 ACT for explicit retargets (`chodziło mi o Spotify`).

### 6. `zrób tak jak wcześniej`

- **Expected:** resume prior goal (CONTINUATION / execute_discussed_goal)
- **Actual:** UNKNOWN + unclassified_request
- **Root cause:** no discourse shape for “repeat prior”; COMPLETED goals not exposed as `has_recent_goal`.
- **Fix:** `_REPEAT_PRIOR` → CONTINUATION `repeat_prior`; `semantic_conversation` adds `has_recent_goal` / `recent_domain` / `recent_goal`; CONTINUATION uses recent goal.

### Scenarios that already passed (OBSERVED before fixes)

`jeszcze jaśniej`, `odpal coś Michaela`, `co teraz leci`, `wyszukaj coś o tym`, `jeszcze tego nie zrobiłeś`, `czemu nie zrobiłeś?`, `spróbuj jeszcze raz`, `spróbuj inaczej` — green on probe when context seeded correctly.

---

## Changed files (conceptual)

| File | Change |
|------|--------|
| `core/utterance_class.py` | State-goal for `ciemno tu`; relative dampen with `has_param` + dim mapping; bare `nie to` correction; `_REPEAT_PRIOR`; REJECTION before soft-correction; `jednak nieważne` reject |
| `core/active_goal.py` | `has_recent_goal` / `recent_domain` / `recent_goal` / last_parameter continuity; revive COMPLETED on param/continuation/correction |
| `core/semantic_executive.py` | Broader `_VOLUME_BARE`; `_discourse_blocks_learned`; pronoun SMART_HOME completeness; CORRECTION overlay (preserve pass-1 ACT / WAIT cancel); CONTINUATION+CHANGE_PARAMETER use recent goal |
| `_probe_natural_pl.py` | Temporary multi-turn dry probe (artifacts under `Data/diagnostics/`) |

**Not modified:** Planner, ToolRegistry, GUI, Phone Companion.

---

## Before / after probe

```
BEFORE: 8/17 passed; failing:
  ciemno_tu, wez_cos_z_tym, nie_az_tak, zgas_ja,
  troche_ciszej, nie_to, no_to_zrob, dobra_niewazne, zrob_jak_wczesniej

AFTER:  17/17 passed; failing: []
```

Artifacts: `natural_pl_probe_before.json`, `natural_pl_probe_after.json`, `*_failures.txt`.

---

## Remaining keyword / regex dependencies (semantic path)

Discourse detectors remain regex **shape** matchers (allowed by design — not phrase→tool maps):

| Module | `re.compile` count (approx) |
|--------|-----------------------------|
| `semantic_executive.py` | 24 |
| `utterance_class.py` | 25 |
| `soft_goals.py` | 17 |
| `device_pronoun.py` | 8 |
| `role_semantics.py` | 7 |
| `semantic_media.py` | 5 |
| `context_selection.py` | 3 |
| `semantic_talk.py` | 3 |
| `conversation_focus.py` | 3 |
| `followup_policy.py` | 1 |

**Additional non-regex keyword dependency (OBSERVED):** `Data/learned_routines.json` exact-phrase recipes can still steal intents unless blocked by `_discourse_blocks_learned` / `_VOLUME_BARE` / intent_family early returns. Example pollution: `puść muzykę` → `open_app` with empty args (breaks preference→Spotify tests).

---

## Unittest + agent-smoke

### Full discover (this runtime)

```
Ran 1156 tests in ~131s
FAILED (failures≈3 after re-fixes of behavioral, errors=17, skipped=6)
```

**Errors (17):** `ModuleNotFoundError: faster_whisper` — environment, not semantic regressions.

**Residual failures (OBSERVED, not introduced by discourse fixes):**

1. `test_preference_no_clarification` / `test_memory_pref_play_no_set_preference` — `Puść muzykę` → `learned_routine` open_app with `app=None` because learned store owns the phrase. HYPOTHESIS: pre-existing local `learned_routines.json` pollution.
2. `test_unknown_side_effect_is_partial_and_idle` — computer verify path asserts `success=False`; got True. Outside semantic/context scope.

**Semantic / goal-first / behavioral:** green after fixes.

### Agent smoke

- Direct `core.agent_smoke.run_agent_smoke()`: **11/11 PASS**
- `main.py --agent-smoke`: blocked on missing `faster_whisper`/`torch` before smoke body (import of `Voice.py`). Stubbing `faster_whisper` alone then hits `torch`.

---

## Blocked dependencies outside semantic/context

1. **Voice stack packages** (`faster_whisper`, `torch`, …) missing in the Codex Python used for gates → cannot run `main.py --agent-smoke` without stubs/full install.
2. **Learned routines store** on disk can still override media preference routing for exact phrases (execution path / learned layer, not fixed here beyond discourse blocking).
3. HA unreachable during smoke clarification (`192.168.0.31`) — smoke still PASS via clarify path.

---

## Unittest green vs real conversational failure

| Case | Unittest | Real conversation (probe) |
|------|----------|---------------------------|
| `za ciemno tu` | often GOAL already | bare `ciemno tu` failed until state-shape fix |
| `nie aż tak` | often patched via mocked `decide_semantics` in goal-first test | failed via learned_routine + COMPLETED goal |
| `zgaś ją` | CM test mocks envelope ACT | real pass1 incomplete → ASK |
| `trochę ciszej` | intent_family SYSTEM OK | learned early-return hid it |

**Conclusion:** several failures were **real conversational** gaps masked by unittest mocks / paraphrase banks that used `za ciemno` not `ciemno tu`.

---

## Evidence vs hypothesis legend

- **OBSERVED:** probe dumps, classify/decide outputs, learned_routines lookup, unittest traces above.
- **HYPOTHESIS:** Spotify preference fails are solely from learned store pollution (strong OBSERVED lookup; not re-verified against clean empty store).
