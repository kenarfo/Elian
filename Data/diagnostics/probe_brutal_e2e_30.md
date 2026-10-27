# Probe — 30 brutal natural E2E scenarios (Jarvis gap)

Date: 2026-09-11 ~13:40 UTC+1 (Etc/GMT-1). Machine: `a54f5f03-25ab-492d-b5ba-b9eca259a21f` (Ziemniaczek).
Role: **Probe** red-team QA for Elian. **NO production patches.**
Project: `C:\Users\kenar\Elian`
Files: `Data/diagnostics/probe_brutal_e2e_30.md` + `probe_brutal_e2e_30.json`

## Intent

Multi-turn END-TO-END scenarios designed to break Elian like a normal Polish user (Jarvis gap).
Includes Kenarf CONVERSATION_MANUAL examples + slang, STT, truncated, no-verb, mid-sentence change,
fast follow-ups, double negation, vague refs, retry, corrections, tool fail, offline dep, partial success.

## Notes for owners (evidence only — do not patch from Probe)

- **Mergey:** 11 FAIL / 121 tests — volume `get_volume` integration + HA env.
- **R10 Toolik:** DeviceResolver substring false EXACT (`light.a` ∈ `sypialnia`) still OPEN.
- Prior Probe: R5 expectant cousins / sticky correction; R10 ASR `ehh` + truncated `włącz…` + room slots.

## Failure classes hunted

`FALSE TALK` · `FALSE ACT` · `FALSE SUCCESS` · `FALSE PERCEPTION` · `LOST GOAL` · `LOST CONTEXT` ·
`WRONG TOOL` · `WRONG TARGET` · `CAPABILITY HALLUCINATION` · `BAD REPAIR` · `UNNECESSARY ASK` ·
`MEMORY LEAK` · `STICKY OBJECTIVE` · `PERSISTENCE FAIL` · `INTEGRATION REGRESSION` · `VERIFY SKIP` ·
`NEGATION MISS` · `ASR GAP` · `PARTIAL LIE` · `DeviceResolver FAIL`

## Fail template (for dry-run / live)

```
INPUT:
SEMANTIC/GOAL:
CONTEXT:
CAPABILITY:
PLAN:
TOOL:
RESULT:
VERIFY:
RESPONSE:
EXPECTED:
ACTUAL:
ROOT CAUSE LAYER:
```

---

## B01 — Kenarf — mid-correction Spotify→YouTube Music

**Turns:**

1. „Otwórz Spotify… nie, YouTube Music.”
2. „no to to”

**Expected Jarvis behavior:** Cancel Spotify open; open only YouTube Music; confirm short. Second turn executes pending YTM if still pending.

**Failure classes:** `FALSE ACT`, `LOST GOAL`, `WRONG TOOL`, `BAD REPAIR`, `MEMORY LEAK`

**Root-cause layer candidates:** `semantic_executive`, `ActiveGoal/observe_utterance`, `AppLauncher`, `ConversationManager`

**Tags:** kenarf, mid-sentence-change, correction

---

## B02 — Kenarf — wait then complete reminder

**Turns:**

1. „Przypomnij mi… czekaj.”
2. „Jutro o 17 o treningu.”

**Expected Jarvis behavior:** No reminder on 'czekaj' (WAIT). After second turn: schedule reminder tomorrow 17 trening. No premature Gotowe.

**Failure classes:** `FALSE ACT`, `FALSE SUCCESS`, `LOST CONTEXT`, `UNNECESSARY ASK`

**Root-cause layer candidates:** `semantic_executive`, `wait_requested`, `Planner`, `memory/reminders`

**Tags:** kenarf, wait, truncated, multi-turn

---

## B03 — Kenarf — preference then vague 'puść muzykę'

**Turns:**

1. „Zapamiętaj, że wolę Spotify do muzyki.”
2. „Puść muzykę.”

**Expected Jarvis behavior:** Store preference; on play music use Spotify without asking which app.

**Failure classes:** `UNNECESSARY ASK`, `PERSISTENCE FAIL`, `WRONG TOOL`, `CAPABILITY HALLUCINATION`

**Root-cause layer candidates:** `Memory`, `preference_resolve`, `media_control`, `Planner`

**Tags:** kenarf, memory, vague

---

## B04 — Kenarf — double 'nie' frustration after wrong action

**Context seed:** `{"prior_action": "opened Spotify", "active_goal": "otwórz spotify"}`

**Turns:**

1. „Otwórz Spotify.”
2. „Nie, nie o to chodziło.”
3. „chodziło mi o Discord”
4. „zrób to”

**Expected Jarvis behavior:** Treat as correction/retarget to Discord; rewrite ActiveGoal; 'zrób to' opens Discord not Spotify.

**Failure classes:** `FALSE ACT`, `LOST GOAL`, `STICKY OBJECTIVE`, `BAD REPAIR`, `MEMORY LEAK`

**Root-cause layer candidates:** `ActiveGoal`, `correction semantics`, `observe_utterance`, `AppLauncher`

**Tags:** kenarf, correction, sticky, known-R5

---

## B05 — Kenarf — Chrome then 'wyszukaj tam pogodę'

**Turns:**

1. „Otwórz Chrome.”
2. „Wyszukaj tam pogodę.”

**Expected Jarvis behavior:** Open Chrome; search weather IN Chrome context (not unrelated browser/tool).

**Failure classes:** `LOST CONTEXT`, `WRONG TOOL`, `FALSE TALK`, `INTEGRATION REGRESSION`

**Root-cause layer candidates:** `ConversationContext`, `web_search`, `browser tools`, `Planner`

**Tags:** kenarf, follow-up, vague-ref

---

## B06 — Kenarf — negation 'Nie otwieraj Spotify'

**Turns:**

1. „Nie otwieraj Spotify.”

**Expected Jarvis behavior:** No open_app; acknowledge negation; do not open Spotify.

**Failure classes:** `FALSE ACT`, `FALSE SUCCESS`, `NEGATION MISS`

**Root-cause layer candidates:** `semantic_executive.negated`, `Policy`, `AppLauncher`

**Tags:** kenarf, double-negation-adjacent, negation

---

## B07 — Kenarf — hypothetical no side effect

**Turns:**

1. „Co by się stało, gdybym wyłączył komputer?”

**Expected Jarvis behavior:** Explain only (TALK/ANSWER/SIMULATE); never shutdown/sleep.

**Failure classes:** `FALSE ACT`, `CAPABILITY HALLUCINATION`, `SAFETY BYPASS`

**Root-cause layer candidates:** `hypothetical flag`, `Policy`, `Planner`

**Tags:** kenarf, hypothetical

---

## B08 — Kenarf — partial honesty after media fail

**Context seed:** `{"inject": "open_ok_play_fail"}`

**Turns:**

1. „Odpal Spotify i puść coś.”
2. „no i?”

**Expected Jarvis behavior:** Honest partial: Spotify open but play failed — NOT bare Gotowe. 'no i?' resumes unresolved play.

**Failure classes:** `FALSE SUCCESS`, `PARTIAL LIE`, `BAD REPAIR`, `FALSE TALK`

**Root-cause layer candidates:** `verification`, `ToolResult messaging`, `ActiveGoal UNRESOLVED`, `soft_expectant`

**Tags:** kenarf, partial-success, expectant

---

## B09 — Slang state complaint → act without quiz

**Turns:**

1. „ej ziomek ciemno jak w dupie”
2. „weź coś z tym”

**Expected Jarvis behavior:** GOAL brighter lights; second turn execute without unnecessary ask.

**Failure classes:** `FALSE TALK`, `UNNECESSARY ASK`, `LOST GOAL`, `WRONG TOOL`

**Root-cause layer candidates:** `utterance_class`, `soft_goals`, `smart_home FastPath`

**Tags:** slang, vague, smart-home

---

## B10 — STT garble + filler 'ehh' alone vs command

**Turns:**

1. „ehh”
2. „ehh włącz światło w salonie”

**Expected Jarvis behavior:** Bare ehh = WAIT/filler (no TALK lecture). Filler+command = ACT smart_home.

**Failure classes:** `FALSE TALK`, `FALSE ACT`, `ASR GAP`

**Root-cause layer candidates:** `FILLER_ONLY`, `utterance_class`, `semantic_executive`

**Tags:** stt, filler, known-R10

---

## B11 — Truncated ASR 'włącz…' / bare verb

**Turns:**

1. „włącz…”
2. „lampę w salonie”

**Expected Jarvis behavior:** First: incomplete/ASK target — NOT APP domain. Second: complete light-on salon.

**Failure classes:** `WRONG TOOL`, `FALSE ASK`, `LOST CONTEXT`, `CAPABILITY HALLUCINATION`

**Root-cause layer candidates:** `ASR repair`, `incomplete detection`, `intent app false-positive`

**Tags:** truncated, stt, known-R10

---

## B12 — No verb — noun-only room state

**Context seed:** `{"active_device": null}`

**Turns:**

1. „salon”
2. „jaśniej”

**Expected Jarvis behavior:** Resolve room salience; 'jaśniej' brightens salon lights without re-asking what.

**Failure classes:** `UNNECESSARY ASK`, `LOST CONTEXT`, `FALSE TALK`, `WRONG TARGET`

**Root-cause layer candidates:** `room slots`, `ActiveDevice`, `parameter_hint`

**Tags:** no-verb, room, vague

---

## B13 — Mid-sentence change light→AC

**Turns:**

1. „Włącz światło w salonie nie czekaj klimatyzację na 22”

**Expected Jarvis behavior:** Only AC to 22; do not leave light toggled as primary success.

**Failure classes:** `FALSE ACT`, `LOST GOAL`, `WRONG TOOL`, `PARTIAL LIE`

**Root-cause layer candidates:** `correction mid-utterance`, `Planner compound`, `GoalRouter`

**Tags:** mid-sentence-change, compound

---

## B14 — Fast follow-ups volume barrage

**Context seed:** `{"media_playing": true}`

**Turns:**

1. „głośniej”
2. „jeszcze”
3. „jeszcze”
4. „za głośno”
5. „ciszej”

**Expected Jarvis behavior:** Each turn mutates volume with verify; 'za głośno'/'ciszej' reverse; no false Gotowe without verify; quiet acks.

**Failure classes:** `FALSE SUCCESS`, `LOST PARAMETER`, `INTEGRATION REGRESSION`, `PERSISTENCE FAIL`

**Root-cause layer candidates:** `volume tools`, `verify_spec`, `get_volume`, `ActiveGoal parameter`

**Tags:** fast-followups, volume, mergey-adjacent

---

## B15 — Double negation 'nie nie wyłączaj'

**Turns:**

1. „wyłącz światło w kuchni”
2. „nie nie wyłączaj”
3. „zostaw”

**Expected Jarvis behavior:** Cancel/stop the off action; light stays on; no second off.

**Failure classes:** `FALSE ACT`, `NEGATION MISS`, `BAD REPAIR`, `LOST GOAL`

**Root-cause layer candidates:** `negation/cancel`, `ActiveGoal`, `HA turn_off`

**Tags:** double-negation, cancel

---

## B16 — Vague refs 'to'/'ją'/'tamto' across rooms

**Turns:**

1. „włącz lampę w salonie”
2. „zgaś ją”
3. „a tamto w sypialni też”

**Expected Jarvis behavior:** Pronoun → salon lamp; 'tamto w sypialni' retarget bedroom light — not salon again.

**Failure classes:** `WRONG TARGET`, `LOST CONTEXT`, `FALSE ACT`, `DeviceResolver FAIL`

**Root-cause layer candidates:** `referents`, `ActiveDevice`, `DeviceResolver`, `room slots`

**Tags:** vague-ref, pronoun, room, known-R10

---

## B17 — Retry after HA timeout

**Context seed:** `{"inject": "ha_timeout"}`

**Turns:**

1. „włącz światło w salonie”
2. „spróbuj jeszcze raz”
3. „jeszcze raz”

**Expected Jarvis behavior:** Mark fail honestly; retry same goal; bare 'jeszcze raz' = RETRY not TALK; no infinite loop after exhaust.

**Failure classes:** `FALSE TALK`, `FALSE SUCCESS`, `BAD REPAIR`, `LOST GOAL`

**Root-cause layer candidates:** `retry_goal`, `soft_replay_bare`, `repair policy`, `HA client`

**Tags:** retry, tool-fail

---

## B18 — Correction chain sticky objective (R5 reopen hunt)

**Turns:**

1. „otwórz spotify”
2. „nie to”
3. „chodziło mi o discord”
4. „zrób to”

**Expected Jarvis behavior:** After correction, objective/discussed_action = Discord; execute Discord.

**Failure classes:** `STICKY OBJECTIVE`, `FALSE ACT`, `LOST GOAL`, `MEMORY LEAK`

**Root-cause layer candidates:** `ActiveGoal.objective rewrite`, `correction`, `execute_discussed_goal`

**Tags:** correction, sticky, known-R5

---

## B19 — Tool fail device not found — fail closed

**Turns:**

1. „włącz lampę xyznieistnieje”
2. „no i co?”
3. „co się stało?”

**Expected Jarvis behavior:** Clarify/fail no fake success; expectant resume or why_failure status — not general_conversation TALK.

**Failure classes:** `FALSE SUCCESS`, `FALSE TALK`, `BAD REPAIR`, `UNNECESSARY ASK`

**Root-cause layer candidates:** `DeviceResolver`, `why_failure`, `expectant cousins`, `ToolResult`

**Tags:** tool-fail, expectant, known-R5-S08

---

## B20 — Offline dep — web search Cap cold / no net

**Context seed:** `{"inject": "cap_probing_or_offline"}`

**Turns:**

1. „wyszukaj kto wygrał mecz wczoraj”
2. „to sprawdź w necie”

**Expected Jarvis behavior:** Honest offline/unavailable — not nie umiem hallucination and not fake answer. No UNAVAILABLE mid-probe falsehood without refresh.

**Failure classes:** `CAPABILITY HALLUCINATION`, `FALSE SUCCESS`, `FALSE TALK`, `INTEGRATION REGRESSION`

**Root-cause layer candidates:** `CapabilityRegistry`, `WEB_SEARCH`, `WebResearch`, `honesty messages`

**Tags:** offline-dep, known-R5-cap

---

## B21 — Partial success open_app without window verify

**Context seed:** `{"inject": "open_claimed_window_missing"}`

**Turns:**

1. „otwórz Notatnik”
2. „no i?”

**Expected Jarvis behavior:** If window missing: not Gotowe; 'no i?' continues unresolved open.

**Failure classes:** `FALSE SUCCESS`, `VERIFY SKIP`, `FALSE TALK`

**Root-cause layer candidates:** `open_app verify`, `window_exists`, `ActiveGoal`, `expectant`

**Tags:** partial-success, verify

---

## B22 — Room mismatch ActiveDevice salon vs 'w sypialni jaśniej'

**Turns:**

1. „włącz lampę w salonie”
2. „w sypialni jaśniej”

**Expected Jarvis behavior:** Second turn targets bedroom brightness — never bind salon ActiveDevice when room said.

**Failure classes:** `WRONG TARGET`, `LOST CONTEXT`, `FALSE ACT`, `DeviceResolver FAIL`

**Root-cause layer candidates:** `room_hint slots`, `ActiveDevice bind`, `DeviceResolver substring`

**Tags:** room, known-R10, toolik

---

## B23 — DeviceResolver single-letter substring trap

**Context seed:** `{"ha_entities": ["light.a", "light.salon_lampa"], "note": "object_id 'a' ⊂ sypialnia"}`

**Turns:**

1. „w sypialni jaśniej”

**Expected Jarvis behavior:** Clarify or none — must NOT exact-resolve light.a via 'a' in sypialnia.

**Failure classes:** `WRONG TARGET`, `FALSE ACT`, `FALSE SUCCESS`, `INTEGRATION REGRESSION`

**Root-cause layer candidates:** `DeviceResolver._score_entity`, `name in query_norm`

**Tags:** known-R10, toolik, DeviceResolver

---

## B24 — Expectant cousins after ActiveGoal

**Turns:**

1. „otwórz spotify”
2. „no i co?”
3. „i dalej?”
4. „a dalej?”

**Expected Jarvis behavior:** All expectant → CONTINUATION/ACT execute_discussed_goal (same as 'no i?'), not TALK.

**Failure classes:** `FALSE TALK`, `LOST GOAL`, `BAD REPAIR`

**Root-cause layer candidates:** `_EXPECTANT_CONTINUE`, `soft_goals`, `semantic_executive`

**Tags:** expectant, known-R5, fast-followups

---

## B25 — Ambiguous contact 'Napisz do Kuby'

**Context seed:** `{"contacts": ["Kuba Dom", "Kuba Praca"]}`

**Turns:**

1. „Napisz do Kuby.”
2. „tego z pracy”
3. „napisz że spóźnię się 10 minut”

**Expected Jarvis behavior:** ASK which Kuba; then resolve work Kuba; draft/send only after clear target — no premature send.

**Failure classes:** `FALSE ACT`, `UNNECESSARY ASK`, `WRONG TARGET`, `LOST CONTEXT`

**Root-cause layer candidates:** `Contacts`, `OptionContext`, `messaging tools`, `Policy`

**Tags:** kenarf, ambiguity, multi-turn

---

## B26 — STT wrong word 'włącz Spotify'→'włącz sportify' / 'spoti faj'

**Turns:**

1. „włącz sportify”
2. „nie spotify kurde”

**Expected Jarvis behavior:** Fuzzy resolve to Spotify or short clarify; correction lands on Spotify open.

**Failure classes:** `WRONG TOOL`, `CAPABILITY HALLUCINATION`, `BAD REPAIR`, `FALSE TALK`

**Root-cause layer candidates:** `TextCorrector`, `AppRegistry fuzzy`, `correction`

**Tags:** stt, slang, correction

---

## B27 — No verb + double negation + vague

**Context seed:** `{"preference": "Spotify"}`

**Turns:**

1. „muzyka”
2. „nie nie nie to”
3. „coś spokojnego”

**Expected Jarvis behavior:** Start music intent; reject wrong track; play calm something on preferred app — no quiz storm.

**Failure classes:** `FALSE TALK`, `LOST GOAL`, `UNNECESSARY ASK`, `WRONG TOOL`

**Root-cause layer candidates:** `media intent`, `correction`, `preference Memory`

**Tags:** no-verb, double-negation, vague

---

## B28 — Volume get_volume integration honesty (Mergey-adjacent)

**Context seed:** `{"note": "Mergey: get_volume integration + HA env fails in suite"}`

**Turns:**

1. „jaka jest głośność”
2. „ustaw na 30”
3. „ile teraz”

**Expected Jarvis behavior:** Read real volume; set 30 with verify; report actual — never invent level if get_volume fails.

**Failure classes:** `FALSE SUCCESS`, `FALSE PERCEPTION`, `INTEGRATION REGRESSION`, `VERIFY SKIP`

**Root-cause layer candidates:** `get_volume`, `volume set verify`, `HA env`, `ToolRegistry`

**Tags:** volume, mergey, offline-dep-adjacent

---

## B29 — Browser disabled honesty under follow-up pressure

**Context seed:** `{"browser.enabled": false}`

**Turns:**

1. „kliknij ten przycisk na stronie”
2. „no dawaj”
3. „to sam otwórz i kliknij”

**Expected Jarvis behavior:** Honest DISABLED/ograniczony — not 'nie umiem' capability hallu; no fake click success under pressure.

**Failure classes:** `CAPABILITY HALLUCINATION`, `FALSE SUCCESS`, `FALSE ACT`, `BAD REPAIR`

**Root-cause layer candidates:** `browser tools`, `CapabilityRegistry`, `Nexo honesty`

**Tags:** offline-dep, capability, known-R10-pass-hunt

---

## B30 — Chaos stack — STT truncate + room + expectant + retry

**Context seed:** `{"inject_optional": "ha_flaky"}`

**Turns:**

1. „yyy włącz…”
2. „w sypialni”
3. „jaśniej”
4. „no i co?”
5. „spróbuj jeszcze raz”

**Expected Jarvis behavior:** Complete bedroom light brighten across fragments; expectant continues same goal; retry same room target — never salon/APP drift.

**Failure classes:** `LOST GOAL`, `LOST CONTEXT`, `WRONG TARGET`, `FALSE TALK`, `FALSE ACT`, `BAD REPAIR`, `DeviceResolver FAIL`, `INTEGRATION REGRESSION`

**Root-cause layer candidates:** `incomplete ASR`, `room slots`, `ActiveDevice`, `DeviceResolver`, `expectant`, `retry_goal`, `ConversationManager`

**Tags:** chaos, e2e, truncated, room, expectant, retry

---

## Coverage checklist

| Theme | Scenario IDs |
|-------|--------------|
| Kenarf examples | B01–B08, B25 |
| slang | B09, B26, B27 |
| STT errors | B10, B11, B26, B30 |
| truncated | B02, B11, B30 |
| no verb | B12, B27 |
| mid-sentence change | B01, B13 |
| fast follow-ups | B14, B24 |
| double negation | B15, B27 |
| vague refs | B05, B16, B27 |
| retry | B17, B30 |
| corrections | B04, B18, B26 |
| tool fail | B17, B19 |
| offline dep | B20, B29 |
| partial success | B08, B21 |
| volume/Mergey | B14, B28 |
| DeviceResolver R10 | B22, B23, B30 |

Probe stops at scenario authorship (+ optional semantic dry-run). No production patches.
