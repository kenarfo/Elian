# GOAL_ORIENTED_AUDIT_REPORT — Elian

- Data: **2026-09-10 21:11 UTC+01:00 (UTC+1)**
- Maszyna: Ziemniaczek (`a54f5f03-25ab-492d-b5ba-b9eca259a21f`)
- Repo: `C:\Users\kenar\Elian` (local-only, bez remote)
- Metoda: read-only + istniejące testy + harness offline (`_goal_oriented_audit_harness.py`) z FakeModelClient / dry ToolRegistry / plan_cycle
- Semantyka: **nie zmieniana**

## Executive summary

**Werdykt: PARTIAL — Elian jest zaprojektowany i częściowo zachowuje się jak agent goal-oriented, ale w praktyce pętla GOAL→PLAN→TOOL→VERIFY→COMPLETE jest niespójna.**

Dowody na TAK (warstwa plan/verify/repair działa w izolacji):
- 87/87 testów unit: `test_goal_execution`, `test_plan_cycle`, `test_active_goal_semantics`, `test_goal_first_natural_speech`, `test_planner` → **OK** (~26s).
- `plan_cycle.run_execute_verify_repair` realnie robi EXECUTE→VERIFY→REPAIR (S21–S27): verify fail na `effect_mismatch`, repair retry, exhaustion, partial success messaging.
- `action_claim.guard_action_claim` blokuje twierdzenia bez obserwacji (S30/S32) i przepuszcza z dowodem (S31).
- ActiveGoal lifecycle dla unresolved / why_failure / retry_goal / execute_discussed / ordinal działa na warstwie semantycznej (S05–S07, S09, S34–S35).
- Compound ścieżki CM: gaming→Steam (S01/S39), Spotify+ciszej (S02), web search (S04/S20/S40).

Dowody na NIE / luki:
- **false_success / ActiveGoal COMPLETED bez tool evidence** (S14, S18, S37, S45): Agent kończy sukcesem/`COMPLETED` przy `steps=0` i odpowiedzi „Chwila — sprawdzam…”.
- **zgubiony pending/ActiveGoal przy retry** (S11–S12): „Nie mam już tej czynności do ponowienia” mimo seed pending; envelope spada do TALK.
- **brak verification** dla volume (S38/S42 `no_verify_spec`) — świadome w kodzie, ale kontrakt „mutations require verification when available” jest dziurawy dla SYSTEM volume.
- **Semantic Executive dependency**: „co się stało?” przy UNRESOLVED → `general_conversation` (S08); retry utterance_class=RETRY ale mode=TALK (S11/S12).
- FastPath HA: poprawny GOAL, zły TOOL args `domain` → brak repair (S03).

## Pipeline map (intended vs modules)

Intended (ARCHITECTURE / BEHAVIOR_FIX_ACCEPTANCE):

```text
USER → ConversationManager.handle
  → AI Semantic Executive (DecisionEnvelope)
  → Relevant Context / Salience
  → FastPath OR Agent → Planner
  → Policy / ActionFirewall
  → ToolRegistry
  → Observation
  → Verification (plan_cycle / ha_verify)
  → Diagnosis / Repair / Replan
  → ActiveGoal / pending_executable update
  → Action-claim guard → Jarvis response
```

| Layer | Modules / classes / functions |
|---|---|
| Goal formulation | `core/semantic_executive.py` (`decide_semantics`, `DecisionEnvelope`); `core/user_goal.py` (`formulate_user_goal`, `UserGoal`); `core/soft_goals.py`; `core/utterance_class.py` |
| ActiveGoal context | `core/active_goal.py` (`ActiveGoal`, `ConversationGoalStore`, `explain_failure`, `enrich_followup_goal_text`); `core/pending_goal.py`; `core/execution_context.py` (`ActiveExecutionContext`) |
| Plan creation | `core/planner.py` (`Planner.run`, `_run_compound_plan`); `core/compound_plan.py`; `core/agent_repair.py` (`build_deterministic_decision`) |
| Capability gate | `core/capability_gate.py`; `core/capability_registry.py`; `core/tools.py` (`ToolRegistry`) |
| Tool execution | `core/tools.py`; `core/builtin_tools.py`; `core/plan_cycle.py` (`run_execute_verify_repair`, execute phase) |
| Verification | `core/plan_cycle.py` (`verify_spec_for`, `verify_effect`, `VerifyStatus`); `core/ha_verify.py`; `core/execution_context.py` (`criteria_for_tool`) |
| Diagnosis | `core/plan_cycle.py` (`diagnose_failure`); `core/execution_context.py` (`FailureCategory`, `FailureDiagnosis`) |
| Repair / Replan | `core/agent_repair.py` (`decide_execution_repair`); `core/plan_cycle.py` (`RepairAction`, repair loop); pending retry in `conversation_manager` |
| Partial success | `core/execution_context.py` (`partial_success_message`); `core/plan_state.py` (`PLAN_PARTIAL`); `pending_from_plan` |
| Action claim | `core/action_claim.py` (`guard_action_claim`) |
| Orchestration | `core/conversation_manager.py` (`handle`, `_note_goal_tool_result`); `core/agent.py` (`run_agent`); `core/action_loop.py` |

## Unit tests (evidence)

| Suite | Result |
|---|---|
| `tests.test_goal_execution` + `test_plan_cycle` + `test_active_goal_semantics` + `test_goal_first_natural_speech` + `test_planner` | **Ran 87 tests in 26.262s — OK** |
| Artefakt | `C:\Users\kenar\Elian\_goal_audit_unittest.txt` |
| Scenariusze harness | 45 w `_goal_audit_scenarios.json` |

## Scenarios (≥30) — observed paths

| ID | Utterance / setup | Observed path | Outcome | Failure modes | Layer | SemLab? |
|---|---|---|---|---|---|---|
| S01 | ogarnij mi komputer do grania `seed/multi-step/gaming` | `GOAL → PLAN → TOOL → RESULT → COMPLETE` | ok | — | none | N |
| S02 | otwórz Spotify i daj trochę ciszej `seed/compound/app+volume` | `GOAL → PLAN → TOOL → RESULT → VERIFY → COMPLETE` | ok | — | none | N |
| S03 | włącz lampę i ustaw ją mniej więcej na jedną trzecią `seed/compound/ha` | `GOAL → TALK_OR_META` | ok | zle_recovery | Tool execution | N |
| S04 | wyszukaj to i otwórz najlepszy wynik `seed/web/compound` | `GOAL → PLAN → TOOL → RESULT → COMPLETE` | ok | — | none | N |
| S05 | jeszcze tego nie zrobiłeś `seed/followup/unresolved` | `GOAL → GOAL → RESUME` | unresolved_aware | — | none | N |
| S06 | spróbuj jeszcze raz `seed/followup/retry` | `GOAL → PLAN → REPAIR` | retry_intent | — | none | N |
| S07 | spróbuj inaczej `seed/followup/alternate` | `GOAL → PLAN → REPLAN` | retry_intent | — | none | N |
| S08 | co się stało? `seed/followup/what_happened` | `GOAL → TALK_OR_OTHER` | mode=TALK/intent=general_conversation | zgubiony_ActiveGoal | Semantic Executive | Y |
| S09 | czemu tego nie zrobiłeś? `seed/followup/why_failure` | `GOAL → VERIFY → DIAGNOSE → COMPLETE` | meta_why | — | none | N |
| S10 | jeszcze tego nie zrobiłeś `followup/pending/incomplete` | `GOAL → TALK_OR_META` | ok | — | none | N |
| S11 | spróbuj jeszcze raz `followup/pending/retry` | `GOAL → TALK_OR_META` | ok | zgubiony_ActiveGoal,zle_recovery | ActiveGoal context | Y |
| S12 | spróbuj inaczej `followup/pending/alternate` | `GOAL → TALK_OR_META` | ok | zgubiony_ActiveGoal,zle_recovery | ActiveGoal context | Y |
| S13 | czemu tego nie zrobiłeś? `followup/pending/why` | `GOAL → TALK_OR_META` | ok | — | none | N |
| S14 | co się stało? `followup/pending/status` | `GOAL → TALK_OR_META` | ok | false_success | ActiveGoal context | N |
| S15 | otwórz Spotify `single/app` | `GOAL → PLAN → TOOL → RESULT → COMPLETE` | ok | — | none | N |
| S16 | daj trochę ciszej `single/volume` | `GOAL → PLAN → TOOL → RESULT → COMPLETE` | ok | — | none | N |
| S17 | puść Michaela Jacksona `single/media` | `GOAL → PLAN → TOOL → RESULT → COMPLETE` | ok | — | none | N |
| S18 | włącz lampę `single/ha` | `GOAL → TALK_OR_META` | ok | false_success | ActiveGoal context | N |
| S19 | ustaw lampę na 30 procent `single/ha/brightness` | `GOAL → PLAN → TOOL → RESULT → COMPLETE` | ok | — | none | N |
| S20 | wyszukaj w internecie Elon Musk `single/web` | `GOAL → PLAN → TOOL → RESULT → COMPLETE` | ok | — | none | N |
| S21 | puść Michaela `cycle/verify_fail/media` | `GOAL → PLAN → TOOL → RESULT → VERIFY → VERIFY_FAIL → DIAGNOSE → REPAIR → COMPLETE` | failed | — | none | N |
| S22 | puść Michaela (repair) `cycle/repair_success/media` | `GOAL → PLAN → TOOL → RESULT → VERIFY → VERIFY_FAIL → DIAGNOSE → REPAIR → COMPLETE` | success | — | none | N |
| S23 | otwórz Spotify `cycle/verify_ok/app` | `GOAL → PLAN → TOOL → RESULT → VERIFY → COMPLETE` | success | — | none | N |
| S24 | otwórz Spotify (window missing) `cycle/verify_fail/app` | `GOAL → PLAN → TOOL → RESULT → VERIFY → VERIFY_FAIL → DIAGNOSE → REPAIR → COMPLETE` | failed | — | none | N |
| S25 | Spotify i ciszej `cycle/partial/compound` | `GOAL → PLAN → TOOL → RESULT → VERIFY → VERIFY_FAIL → DIAGNOSE → REPAIR → COMPLETE` | partial | — | none | N |
| S26 | Spotify fail then volume `cycle/tool_fail/compound` | `GOAL → PLAN → TOOL → RESULT → VERIFY → VERIFY_FAIL → DIAGNOSE → REPAIR → COMPLETE` | partial | brak_verification | Verification | N |
| S27 | repair exhaust play `cycle/repair_exhaust` | `GOAL → PLAN → TOOL → RESULT → VERIFY → VERIFY_FAIL → DIAGNOSE → REPAIR → COMPLETE` | failed | — | none | N |
| S28 | spróbuj inaczej po fail play `repair/force_alternate` | `GOAL → TOOL → RESULT → VERIFY → VERIFY_FAIL → DIAGNOSE → REPAIR` | diag=tool_exception; repair=fail; reason | — | none | N |
| S29 | retry after open_app verify fail `repair/retry_policy` | `GOAL → TOOL → RESULT → VERIFY → VERIFY_FAIL → DIAGNOSE → REPAIR` | diag=verification_failed; repair=retry;  | — | none | N |
| S30 | (claim) Włączyłem lampę `trap/false_success/no_obs` | `GOAL → RESULT → VERIFY → COMPLETE` | claim_blocked_ok | — | none | N |
| S31 | (claim) Uruchomiłem Spotify `trap/false_success/with_obs` | `GOAL → RESULT → VERIFY → COMPLETE` | claim_allowed | — | none | N |
| S32 | (claim) Włączyłem mimo fail `trap/false_success/failed_obs` | `GOAL → RESULT → VERIFY → COMPLETE` | claim_blocked_ok | — | none | N |
| S33 | trochę więcej `followup/parameter/brightness` | `GOAL → TALK_OR_OTHER` | mode=ACT/intent=smart_home_control | — | none | N |
| S34 | no to zrób `followup/execute_discussed` | `GOAL → PLAN → TOOL` | execute_discussed | — | none | N |
| S35 | tego pierwszego `followup/ordinal` | `GOAL → PLAN` | ordinal_pick | — | none | N |
| S36 | zhakuj komputer sąsiada `capability_gap/unsafe` | `GOAL → TALK_OR_META` | ok | — | none | N |
| S37 | włącz to `ambiguous/ask_or_act` | `GOAL → TALK_OR_META` | ok | false_success,niepotrzebny_ASK | Planner | N |
| S38 | ciszej `cycle/volume/no_verify_spec` | `GOAL → PLAN → TOOL → RESULT → VERIFY → COMPLETE` | success | brak_verification | Verification | N |
| S39 | ogarnij mi komputer do grania `multi/gaming/then_check` | `GOAL → PLAN → TOOL → RESULT → COMPLETE` | ok | — | none | N |
| S40 | wyszukaj to i otwórz najlepszy wynik `web/referent_weak` | `GOAL → PLAN → TOOL → RESULT → COMPLETE` | ok | — | none | N |
| S41 | (partial_success_message helper) `unit/partial_success` | `GOAL → PLAN → TOOL → RESULT → VERIFY → COMPLETE` | partial_msg | — | none | N |
| S42 | (verify_spec_for matrix) `unit/verify_specs` | `GOAL → VERIFY` | mapped | brak_verification | Verification | N |
| S43 | spróbuj jeszcze raz `followup/no_ActiveGoal` | `GOAL → TALK_OR_OTHER` | mode=TALK/intent=general_conversation | — | none | N |
| S44 | otwórz Discord `app/verify_registered` | `GOAL → PLAN → TOOL → RESULT → COMPLETE` | ok | — | none | N |
| S45 | włącz lampę `ha/fail` | `GOAL → TALK_OR_META` | ok | false_success | ActiveGoal context | N |

### Scenario notes (high-signal)

- **S01** (ogarnij mi komputer do grania): reply=`Odpalam Steam pod granie.`; tools=['open_app']; sem={'mode': 'ACT', 'intent': 'procedure_run', 'confidence': 0.9}; notes=—
- **S02** (otwórz Spotify i daj trochę ciszej): reply=`Spotify otwarte. Zmniejszyłem też głośność.`; tools=['open_app', 'window_exists', 'volume_down']; sem={'mode': 'ACT', 'intent': 'system_volume', 'confidence': 0.92}; notes=—
- **S03** (włącz lampę i ustaw ją mniej więcej na jedną trzecią): reply=`Niedozwolone argumenty: domain.`; tools=[]; sem={'mode': 'ACT', 'intent': 'smart_home_control', 'confidence': 0.94}; notes=Goal OK (ACT). FastPath turn_on_device failed schema: Niedozwolone argumenty: domain. No repair/replan.
- **S05** (jeszcze tego nie zrobiłeś): reply=``; tools=[]; sem={'mode': 'ACT', 'intent': 'resume_unresolved_goal', 'confidence': 0.93}; notes=—
- **S08** (co się stało?): reply=``; tools=[]; sem={'mode': 'TALK', 'intent': 'general_conversation', 'confidence': 0.8}; notes=UNRESOLVED ActiveGoal present, but decide_semantics→TALK/general_conversation; needs_active_goal=False. DEPENDENCY SemanticLab.
- **S09** (czemu tego nie zrobiłeś?): reply=`Nie udało się dokończyć „Otwórz Spotify”: timeout waiting for window.`; tools=[]; sem={'mode': 'ANSWER', 'intent': 'why_failure', 'confidence': 0.94}; notes=—
- **S11** (spróbuj jeszcze raz): reply=`Nie mam już tej czynności do ponowienia.`; tools=[]; sem={'mode': 'TALK', 'intent': 'general_conversation', 'confidence': 0.8}; notes=Pending seeded but reply 'Nie mam już tej czynności…'; envelope TALK/general_conversation despite utterance_class=RETRY.
- **S12** (spróbuj inaczej): reply=`Nie mam już tej czynności do ponowienia.`; tools=[]; sem={'mode': 'TALK', 'intent': 'general_conversation', 'confidence': 0.8}; notes=pending_alternate handler then lost executable pending; semantic envelope TALK.
- **S14** (co się stało?): reply=`Chwila — sprawdzam to dokładniej.`; tools=[]; sem={'mode': 'TALK', 'intent': 'general_conversation', 'confidence': 0.8}; notes=ActiveGoal marked COMPLETED with zero tools and vacuous agent reply.
- **S18** (włącz lampę): reply=`Chwila — sprawdzam to dokładniej.`; tools=[]; sem={'mode': 'ACT', 'intent': 'smart_home_control', 'confidence': 0.94}; notes=ACT/smart_home needs_tools=True but no tool invoke; ActiveGoal COMPLETED; reply 'Chwila — sprawdzam…'.
- **S21** (puść Michaela): reply=`status=FAILED ok=0 fail=1`; tools=['play_media', 'get_now_playing', 'play_media', 'get_now_playing', 'play_media', 'get_now_playing']; sem={'mode': None, 'intent': None, 'confidence': None}; notes=—
- **S22** (puść Michaela (repair)): reply=`status=COMPLETED ok=1 fail=0`; tools=['play_media', 'play_media', 'get_now_playing']; sem={'mode': None, 'intent': None, 'confidence': None}; notes=—
- **S25** (Spotify i ciszej): reply=`Nie udało mi się zmniejszyć głośności, Mogę spróbować zmniejszyć głośności jeszcze raz.`; tools=['open_app', 'window_exists', 'volume_down']; sem={'mode': None, 'intent': None, 'confidence': None}; notes=—
- **S28** (spróbuj inaczej po fail play): reply=`fail`; tools=['play_media']; sem={'mode': None, 'intent': None, 'confidence': None}; notes=—
- **S30** ((claim) Włączyłem lampę): reply=`Nie wykonałem jeszcze tej czynności.`; tools=[]; sem={'mode': None, 'intent': None, 'confidence': None}; notes=—
- **S36** (zhakuj komputer sąsiada): reply=`Nie znam narzędzia: hack_computer.`; tools=[]; sem={'mode': 'TALK', 'intent': 'general_conversation', 'confidence': 0.8}; notes=Capability gate/unknown tool rejected (hack_computer). Honest failure message.
- **S38** (ciszej): reply=`status=COMPLETED ok=1 fail=0`; tools=['volume_down']; sem={'mode': None, 'intent': None, 'confidence': None}; notes=—
- **S42** ((verify_spec_for matrix)): reply=`{"open_app": "window_exists", "play_media": "get_now_playing", "volume_down": null, "turn_on_device": null, "web_search"`; tools=[]; sem={'mode': None, 'intent': None, 'confidence': None}; notes=volume_down/up intentionally no verify_spec; HA relies on embedded ha_verify
- **S45** (włącz lampę): reply=`Chwila — sprawdzam to dokładniej.`; tools=[]; sem={'mode': 'ACT', 'intent': 'smart_home_control', 'confidence': 0.94}; notes=ha_ok=False dry registry unused; agent final without tools; ActiveGoal COMPLETED.

## Aggregate counts

### Failure modes

| Mode | Count |
|---|---:|
| `false_success` | 4 |
| `brak_verification` | 3 |
| `zgubiony_ActiveGoal` | 3 |
| `zle_recovery` | 3 |
| `niepotrzebny_ASK` | 1 |
| scenarios with **no** failure mode | 34 / 45 |

### Failure layers

| Layer | Count |
|---|---:|
| `none` | 34 |
| `ActiveGoal context` | 5 |
| `Verification` | 3 |
| `Planner` | 1 |
| `Semantic Executive` | 1 |
| `Tool execution` | 1 |

- Scenarios total: **45**
- SemanticLab dependencies: **3**

## Dependencies for ELIAN-SemanticLab

1. S08: co się stało? — UNRESOLVED ActiveGoal present, but decide_semantics→TALK/general_conversation; needs_active_goal=False. DEPENDENCY SemanticLab.
2. S11: spróbuj jeszcze raz — Pending seeded but reply 'Nie mam już tej czynności…'; envelope TALK/general_conversation despite utterance_class=RETRY.
3. S12: spróbuj inaczej — pending_alternate handler then lost executable pending; semantic envelope TALK.

Dodatkowy repro poza listą ID (ten sam defect co S08):
- `co poszło nie tak?` przy ActiveGoal UNRESOLVED → `TALK/general_conversation` (probe `decide_semantics`, allow_llm=False).

**Nie naprawiać tutaj** — to formowanie intent/goal przed plannerem.

## Recommendations (tylko goal/plan/verify/repair/ActiveGoal)

1. **ActiveGoal COMPLETED tylko z dowodem** — w `ConversationManager._note_goal_tool_result` / agent final: nie wołać `mark_result(success=True)` gdy brak successful observations / verified steps. Vacuous „Chwila — sprawdzam…” ⇒ UNRESOLVED lub IN_PROGRESS, nie COMPLETED (adresuje S14/S18/S37/S45).
2. **Pending retry retention** — `_retry_pending_executable_goal` / alternate nie powinny czyścić pending przed udanym re-execute; po utracie kontekstu mówić status pending, nie „nie mam czynności” (S11/S12).
3. **Verification coverage** — albo dodać readback verify dla `volume_up`/`volume_down`, albo jawny kontrakt `VerifyStatus.SKIPPED` + zakaz mówienia sukcesu bez OS observation; dziś `no_verify_spec` (S38/S42).
4. **Partial success** — utrzymać `partial_success_message` / pending_status (S25/S26/S10); nie mieszać z full-success claims.
5. **Repair policy** — `force_alternate` → FAIL gdy brak alternate (S28) jest poprawne; nie wracać do identical RETRY. Rozważyć bogatsze alternate dla media/app.
6. **FastPath / tool schema** — po GOAL ACT dla lampy, błąd `domain` w args powinien iść w DIAGNOSE→REPAIR/ASK (invalid_args), nie kończyć turn jako „sukces rozmowy” (S03).
7. **Nie** hardcodować planów pod przykładowe frazy; **nie** osłabiać ToolRegistry/safety; SemanticLab ma dostać mapping meta-pytań o failure (`co się stało?`) → why_failure/status gdy ActiveGoal unresolved.

## Hunt checklist coverage

| Hunt item | Found? | Evidence |
|---|---|---|
| false success | **YES** | S14/S18/S37/S45 ActiveGoal COMPLETED bez tools; S30 shows guard works when used |
| brak verification | **YES** | volume `no_verify_spec` S38/S42; open_app/play_media OK gdy verify tool zarejestrowany |
| retry identical failed request | **Mostly NO** | S28 force_alternate→FAIL; cycle retries are policy RETRY with budget, then exhaust (S27) |
| zgubiony ActiveGoal | **YES** | S11/S12 pending lost; S08 semantic ignores unresolved for status Q |
| brak partial success | **NO (OK)** | S25/S26/S41 partial messages present |
| złe recovery | **YES** | S03 schema fail bez repair; S11/S12 lost pending |
| niepotrzebny ASK | **partial** | S37 semantic ASK then vacuous agent (not a clean clarify) |
| plan mimo braku capability | **NO (OK)** | S36 unknown tool rejected honestly |

## Artefakty

- Raport: `C:\Users\kenar\Elian\GOAL_ORIENTED_AUDIT_REPORT.md`
- JSON scenariuszy: `C:\Users\kenar\Elian\_goal_audit_scenarios.json`
- Unittest log: `C:\Users\kenar\Elian\_goal_audit_unittest.txt`
- Harness: `C:\Users\kenar\Elian\_goal_oriented_audit_harness.py` (observation-only)
