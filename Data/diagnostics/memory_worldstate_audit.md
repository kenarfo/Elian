# Audyt pamięci / WorldState / kontekstu — Elian

Data (UTC+1): 2026-09-10 ~20:04+

## 1. Executive summary

**Werdykt remember/forget: `partial`**

Częściowo: rdzeń remember/forget soft+candidate działa, ale wykryto luki w restart/rehydrate lub w macierzy referencji. Szczegóły w tabeli scenariuszy.

- Scenariusze PASS/FAIL: **22 PASS / 1 FAIL** (A 8/0, B 4/0, C 3/0, D 7/1)
- Unit testy powiązane: 76 tests ran earlier: 75 OK, 1 FAIL test_agent_receives_preference_in_context (source action_loop!=agent) — DEPENDENCY routing, not memory store
- DEPENDENCY (Planner/Tool/Semantic): 1

## 2. Layer map (5 warstw)

| Layer | Classes / files | Lifetime | Persistent? |
|-------|-----------------|----------|-------------|
| EPHEMERAL CONTEXT | `memory/short_term.py ShortTermMemory.turns/focus; ConversationContext.py; core/pending_context.py` | in-process / TTL; cleared on session clear; not written to elian_memory.json as durable facts | no (turns may be projected to conversation[] but not promoted as knowledge) |
| SESSION MEMORY | `core/cognitive_core.py TemporaryIntention + CognitiveState; memory/types.py MemoryType.SESSION; ShortTermMemory.entities` | session / process; TemporaryIntention until invalidated; SESSION typed entries dropped on clear_session/restart | no (session-only) |
| CANDIDATE MEMORY | `core/cognitive_core.py CandidateMemory + upsert_candidate / promote_candidate_to_memory` | session scratchpad until reinforced/promoted/invalidated; promote → Memory 2.0 | no until promotion |
| PERSISTENT MEMORY | `memory/manager.py MemoryManager; memory/typed_store.py; Data/elian_memory.json; ReminderRule mirrored via ROUTINE; integrations/reminders separate` | disk; FACT/PREFERENCE/RELATION/ROUTINE/VOCABULARY/TEMPORARY(until expires) | yes (atomic JSON) |
| WORLD STATE | `core/world_state.py Observation/Fact/WorldSnapshot; active_referent, smart_home, media, …` | live snapshot with TTL; STALE/UNKNOWN/UNAVAILABLE; NOT merged into elian_memory.json | no (process memory; note_* writers) |

Mapowanie nazewnictwa docs ↔ audit:
- Docs `memory/layers.py` (working/conversation/semantic/…) = fasada odczytu, nie zastępuje 5 warstw audytu.
- `CandidateMemory` / `TemporaryIntention` / `ReminderRule` żyją w `CognitiveState` (sesja).
- Durable knowledge = `MemoryManager` → `Data/elian_memory.json` (schema 4 `entries[]`).
- Live HA/media/PC = `WorldState` tylko.

## 3. Scenario results

| id | turns / tag | expected | observed (skrót) | PASS/FAIL | failure class |
|----|-------------|----------|------------------|-----------|---------------|
| A/A1_soft | jutro chyba pójdę do sklepu | {"rationale": "TEMPORARY_FUTURE_INTENT", "rationale_alts": ["UNCERTAIN_FUTURE"], | rationale=TEMPORARY_FUTURE_INTENT persist=SESSION intent=POSSIBLE_INTENTION | PASS |  |
| A/A2_forget_soft | w sumie nieważne | {"rationale": "CHANGE_OF_MIND", "rationale_alts": ["NO_ACTION_REQUIRED"], "activ | rationale=CHANGE_OF_MIND persist=NONE intent=INVALIDATION | PASS |  |
| A/A2b_no_resurrect_retrieve | (retrieve sklep) | {} |  | PASS |  |
| A/A3_rule | przypominaj mi rano o ładowarce | {"rationale": "RECURRING_REMINDER_RULE", "persistence": "PERSISTENT", "enabled_r | rationale=RECURRING_REMINDER_RULE persist=PERSISTENT intent=REMINDER_REQUEST | PASS |  |
| A/A4_restart_rule_must_exist | A4_restart_rule_must_exist | {} | persist_rule=True session_rule=False | PASS |  |
| A/A5a_disable_after_cold_restart | już nie przypominaj mi o ładowarce | {} | rationale=DISABLE_REMINDER_RULE persist=PERSISTENT intent=INVALIDATION; routines_left=0 | PASS |  |
| A/A5b_disable_same_session | już nie przypominaj mi o ładowarce | {} | rationale=DISABLE_REMINDER_RULE persist=PERSISTENT intent=INVALIDATION | PASS |  |
| A/A6_restart_after_delete | A6_restart_after_delete | {} | resurrect=0 | PASS |  |
| B/B0_seed | B0_seed | device chain resolution | soft=None | PASS |  |
| B/B_brighter | jeszcze jaśniej | device chain resolution | soft=brightness_up | PASS |  |
| B/B_less | nie aż tak | device chain resolution | soft=brightness_down | PASS |  |
| B/B_turn_off_pronoun | zgaś ją | device chain resolution | soft=None world=device | PASS |  |
| C/C1_search | wyszukaj Michaela Jacksona | web chain | kind=web_search | PASS |  |
| C/C2_open_first | otwórz pierwszy wynik | web chain | kind=open_result | PASS |  |
| C/C3_more_about_him | a powiedz coś więcej o nim | web chain | kind=None | PASS |  |

## 4. Resolution matrix

| token | utterance | path | result summary | PASS/FAIL |
|-------|-----------|------|----------------|-----------|
| to | zrób to | replay/device/app | {"kind": "replay_last", "intent_family": "SYSTEM", "objective": "Powtórz ostatnią czynność: web_sear | PASS |
| ją | zgaś ją | device pronoun | {"kind": "device", "entity_id": "light.vizzini", "name": "light.vizzini", "domain": "light", "confid | PASS |
| go | zamknij go | app/device pronoun | {"type": "application", "name": "Spotify", "entity_id": "", "domain": "", "canonical_name": "Spotify | PASS |
| nim | powiedz coś o nim | person focus | [false, ["Michael Jackson", "Michaela Jacksona"]] | PASS |
| pierwszy | otwórz pierwszy wynik | web first | {"kind": "open_result", "intent_family": "WEB", "objective": "Otwórz pierwszy wynik: MJ Wiki", "targ | PASS |
| tamto | wyłącz tamto | world pronoun | {"kind": "device", "entity_id": "light.vizzini", "name": "light.vizzini", "domain": "light", "confid | PASS |
| jak wcześniej | zrób jak wcześniej | replay | {"kind": "replay_last", "intent_family": "SYSTEM", "objective": "Powtórz ostatnią czynność: web_sear | PASS |
| jeszcze raz | jeszcze raz | replay/reexplain | [null, false] | FAIL |

## 5. Bugs ranked by severity

1. **[stale_refs]** `D:jeszcze raz` — ['jeszcze raz not replay/followup: soft=None fu=False']

### Znane ryzyka z mapowania kodu (nawet gdy warm-path PASS)

1. **ReminderRule nie jest rehydratowane** z `ROUTINE` przy starcie procesu (`ConversationManager` tworzy pusty `CognitiveState()`).
2. Disable reguły po restarcie zależy od obecności reguły w sesji — cold path może zostawić ROUTINE na dysku.
3. WorldState nie wolno mylić z Persistent Memory (docs OK; `note_session` zapisuje tylko live cognitive hint).

## 6. DEPENDENCY list (Planner / Tool / Semantic) — nie naprawiane

- DEPENDENCY: test_memory.test_agent_receives_preference_in_context expects source=agent, got action_loop (Planner/routing path)

## 7. Recommendations (tylko memory / WorldState / context)

1. Dodać **rehydrate ReminderRule** z aktywnych `MemoryType.ROUTINE` (provenance cognitive_core) przy `ConversationManager.activate()` / starcie MemoryManager.
2. Przy `DISABLE_REMINDER_RULE` bez sesyjnej reguły: **fallback `memory.forget` / deactivate ROUTINE** po podmiocie (ładowarka), nie tylko short-circuit „nie mam”.
3. Trzymać `CandidateMemory` / `TemporaryIntention` ściśle sesyjnie; nie promować soft „chyba” do ROUTINE/reminder (obecnie OK).
4. Dla referencji osoby (`nim`/`go` ludzki): wzmocnić focus `referenced_people` w ścieżce web→knowledge (obecnie częściowo przez `update_focus`).
5. Nie mieszać `integrations/reminders` (ToolRegistry create_reminder) z `ReminderRule` kognitywnym bez jawnego mostu — dokumentować dwa store’y.
6. Dodać regression test: create rule → new MemoryManager+CognitiveState → assert ROUTINE widoczny **oraz** enabled_rules po rehydrate → disable → restart → brak ROUTINE.

## Appendix — unit tests

```
FAILED (failures=1) — test_memory.MemoryCoreTests.test_agent_receives_preference_in_context
```

Evidence JSON: `C:\Users\kenar\Elian\Data\diagnostics\memory_worldstate_audit_evidence.json`
