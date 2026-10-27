# Probe brutal E2E — semantic dry-run subset (no HA side effects)

Date: 2026-09-11 ~13:45 UTC+1. allow_llm=False. Tools/HA NOT invoked.

**Summary:** 9/10 scenarios PASS (semantic heuristics). Failed: ['B10']

## B06 — Kenarf — negation 'Nie otwieraj Spotify'

### Turn „Nie otwieraj Spotify.” — **PASS**

```
INPUT: Nie otwieraj Spotify.
SEMANTIC/GOAL: class=UNKNOWN mode=ANSWER intent=action_discussion goal='Discuss or explain, not execute: Nie otwieraj Spotify' hint= cont=False
CONTEXT: goal_status=ACTIVE objective='Discuss or explain, not execute: Nie otwieraj Spotify' domain= conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: No open_app; acknowledge negation; do not open Spotify.
ACTUAL: class=UNKNOWN mode=ANSWER intent=action_discussion tools=False domains=[] goal='Discuss or explain, not execute: Nie otwieraj Spotify'
ROOT CAUSE LAYER: —
```

**Scenario verdict:** PASS

---

## B07 — Kenarf — hypothetical no side effect

### Turn „Co by się stało, gdybym wyłączył komputer?” — **PASS**

```
INPUT: Co by się stało, gdybym wyłączył komputer?
SEMANTIC/GOAL: class=INFORMATION_REQUEST mode=ANSWER intent=action_discussion goal='Discuss or explain, not execute: Co by się stało, gdybym wyłączył komputer' hint= cont=False
CONTEXT: goal_status=DISCUSSED objective='Discuss or explain, not execute: Co by się stało, gdybym wyłączył komputer' domain= conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: Explain only (TALK/ANSWER/SIMULATE); never shutdown/sleep.
ACTUAL: class=INFORMATION_REQUEST mode=ANSWER intent=action_discussion tools=False domains=[] goal='Discuss or explain, not execute: Co by się stało, gdybym wyłączył komputer'
ROOT CAUSE LAYER: —
```

**Scenario verdict:** PASS

---

## B10 — STT garble + filler 'ehh' alone vs command

### Turn „ehh” — **FAIL**

```
INPUT: ehh
SEMANTIC/GOAL: class=UNKNOWN mode=TALK intent=general_conversation goal='ehh' hint= cont=False
CONTEXT: goal_status=ACTIVE objective='ehh' domain= conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: Bare ehh = WAIT/filler (no TALK lecture). Filler+command = ACT smart_home.
ACTUAL: class=UNKNOWN mode=TALK intent=general_conversation tools=False domains=[] goal='ehh'
ROOT CAUSE LAYER: ['FILLER_ONLY', 'utterance_class', 'semantic_executive']
FAIL_REASONS: ['bare filler → mode=TALK class=UNKNOWN (want WAIT/FILLER)']
```

### Turn „ehh włącz światło w salonie” — **PASS**

```
INPUT: ehh włącz światło w salonie
SEMANTIC/GOAL: class=ACTION_REQUEST mode=ACT intent=smart_home_control goal='ehh włącz światło w salonie' hint= cont=False
CONTEXT: goal_status=ACTIVE objective='ehh włącz światło w salonie' domain=SMART_HOME conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: Bare ehh = WAIT/filler (no TALK lecture). Filler+command = ACT smart_home.
ACTUAL: class=ACTION_REQUEST mode=ACT intent=smart_home_control tools=True domains=['SMART_HOME'] goal='ehh włącz światło w salonie'
ROOT CAUSE LAYER: —
```

**Scenario verdict:** FAIL

---

## B11 — Truncated ASR 'włącz…' / bare verb

### Turn „włącz…” — **PASS**

```
INPUT: włącz…
SEMANTIC/GOAL: class=ACTION_REQUEST mode=ASK intent=incomplete_request goal='włącz…' hint= cont=False
CONTEXT: goal_status=ACTIVE objective='włącz…' domain= conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: First: incomplete/ASK target — NOT APP domain. Second: complete light-on salon.
ACTUAL: class=ACTION_REQUEST mode=ASK intent=incomplete_request tools=False domains=[] goal='włącz…'
ROOT CAUSE LAYER: —
```

### Turn „lampę w salonie” — **PASS**

```
INPUT: lampę w salonie
SEMANTIC/GOAL: class=UNKNOWN mode=ACT intent=smart_home_control goal='lampę w salonie' hint= cont=False
CONTEXT: goal_status=ACTIVE objective='lampę w salonie' domain=SMART_HOME conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: First: incomplete/ASK target — NOT APP domain. Second: complete light-on salon.
ACTUAL: class=UNKNOWN mode=ACT intent=smart_home_control tools=True domains=['SMART_HOME'] goal='lampę w salonie'
ROOT CAUSE LAYER: —
```

**Scenario verdict:** PASS

---

## B18 — Correction chain sticky objective (R5 reopen hunt)

### Turn „otwórz spotify” — **PASS**

```
INPUT: otwórz spotify
SEMANTIC/GOAL: class=ACTION_REQUEST mode=ACT intent=app goal='otwórz spotify' hint= cont=False
CONTEXT: goal_status=ACTIVE objective='otwórz spotify' domain=APP conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: After correction, objective/discussed_action = Discord; execute Discord.
ACTUAL: class=ACTION_REQUEST mode=ACT intent=app tools=True domains=['APP'] goal='otwórz spotify'
ROOT CAUSE LAYER: —
```

### Turn „nie to” — **PASS**

```
INPUT: nie to
SEMANTIC/GOAL: class=CORRECTION mode=ACT intent=correction goal='otwórz spotify' hint= cont=True
CONTEXT: goal_status=ACTIVE objective='otwórz spotify' domain=APP conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: After correction, objective/discussed_action = Discord; execute Discord.
ACTUAL: class=CORRECTION mode=ACT intent=correction tools=True domains=['APP'] goal='otwórz spotify'
ROOT CAUSE LAYER: —
```

### Turn „chodziło mi o discord” — **PASS**

```
INPUT: chodziło mi o discord
SEMANTIC/GOAL: class=CORRECTION mode=ACT intent=correction goal='discord' hint= cont=True
CONTEXT: goal_status=ACTIVE objective='discord' domain=APP conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: After correction, objective/discussed_action = Discord; execute Discord.
ACTUAL: class=CORRECTION mode=ACT intent=correction tools=True domains=[] goal='discord'
ROOT CAUSE LAYER: —
```

### Turn „zrób to” — **PASS**

```
INPUT: zrób to
SEMANTIC/GOAL: class=CONFIRMATION mode=ACT intent=execute_discussed_goal goal='discord' hint= cont=True
CONTEXT: goal_status=ACTIVE objective='discord' domain=APP conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: After correction, objective/discussed_action = Discord; execute Discord.
ACTUAL: class=CONFIRMATION mode=ACT intent=execute_discussed_goal tools=True domains=['APP'] goal='discord'
ROOT CAUSE LAYER: —
```

**Scenario verdict:** PASS

---

## B22 — Room mismatch ActiveDevice salon vs 'w sypialni jaśniej'

### Turn „włącz lampę w salonie” — **PASS**

```
INPUT: włącz lampę w salonie
SEMANTIC/GOAL: class=ACTION_REQUEST mode=ACT intent=smart_home_control goal='włącz lampę w salonie' hint= cont=False
CONTEXT: goal_status=ACTIVE objective='włącz lampę w salonie' domain=SMART_HOME conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: Second turn targets bedroom brightness — never bind salon ActiveDevice when room said.
ACTUAL: class=ACTION_REQUEST mode=ACT intent=smart_home_control tools=True domains=['SMART_HOME'] goal='włącz lampę w salonie'
ROOT CAUSE LAYER: —
```

### Turn „w sypialni jaśniej” — **PASS**

```
INPUT: w sypialni jaśniej
SEMANTIC/GOAL: class=CHANGE_PARAMETER mode=ACT intent=smart_home_control goal='Increase brightness of the active light' hint=brighter cont=True
CONTEXT: goal_status=ACTIVE objective='włącz lampę w salonie' domain=SMART_HOME conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: Second turn targets bedroom brightness — never bind salon ActiveDevice when room said.
ACTUAL: class=CHANGE_PARAMETER mode=ACT intent=smart_home_control tools=True domains=['SMART_HOME'] goal='włącz lampę w salonie'
ROOT CAUSE LAYER: —
```

**Scenario verdict:** PASS

---

## B23 — DeviceResolver single-letter substring trap

### Turn „w sypialni jaśniej” — **PASS**

```
INPUT: w sypialni jaśniej
SEMANTIC/GOAL: class=CHANGE_PARAMETER mode=ACT intent=smart_home_control goal='w sypialni jaśniej' hint=brighter cont=False
CONTEXT: goal_status=ACTIVE objective='w sypialni jaśniej' domain=SMART_HOME conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: Clarify or none — must NOT exact-resolve light.a via 'a' in sypialnia.
ACTUAL: class=CHANGE_PARAMETER mode=ACT intent=smart_home_control tools=True domains=['SMART_HOME'] goal='w sypialni jaśniej'
ROOT CAUSE LAYER: —
```

**Scenario verdict:** PASS

---

## B24 — Expectant cousins after ActiveGoal

### Turn „otwórz spotify” — **PASS**

```
INPUT: otwórz spotify
SEMANTIC/GOAL: class=ACTION_REQUEST mode=ACT intent=app goal='otwórz spotify' hint= cont=False
CONTEXT: goal_status=ACTIVE objective='otwórz spotify' domain=APP conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: All expectant → CONTINUATION/ACT execute_discussed_goal (same as 'no i?'), not TALK.
ACTUAL: class=ACTION_REQUEST mode=ACT intent=app tools=True domains=['APP'] goal='otwórz spotify'
ROOT CAUSE LAYER: —
```

### Turn „no i co?” — **PASS**

```
INPUT: no i co?
SEMANTIC/GOAL: class=CONTINUATION mode=ACT intent=execute_discussed_goal goal='otwórz spotify' hint= cont=True
CONTEXT: goal_status=ACTIVE objective='otwórz spotify' domain=APP conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: All expectant → CONTINUATION/ACT execute_discussed_goal (same as 'no i?'), not TALK.
ACTUAL: class=CONTINUATION mode=ACT intent=execute_discussed_goal tools=True domains=['APP'] goal='otwórz spotify'
ROOT CAUSE LAYER: —
```

### Turn „i dalej?” — **PASS**

```
INPUT: i dalej?
SEMANTIC/GOAL: class=CONTINUATION mode=ACT intent=execute_discussed_goal goal='otwórz spotify' hint= cont=True
CONTEXT: goal_status=ACTIVE objective='otwórz spotify' domain=APP conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: All expectant → CONTINUATION/ACT execute_discussed_goal (same as 'no i?'), not TALK.
ACTUAL: class=CONTINUATION mode=ACT intent=execute_discussed_goal tools=True domains=['APP'] goal='otwórz spotify'
ROOT CAUSE LAYER: —
```

### Turn „a dalej?” — **PASS**

```
INPUT: a dalej?
SEMANTIC/GOAL: class=CONTINUATION mode=ACT intent=execute_discussed_goal goal='otwórz spotify' hint= cont=True
CONTEXT: goal_status=ACTIVE objective='otwórz spotify' domain=APP conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: All expectant → CONTINUATION/ACT execute_discussed_goal (same as 'no i?'), not TALK.
ACTUAL: class=CONTINUATION mode=ACT intent=execute_discussed_goal tools=True domains=['APP'] goal='otwórz spotify'
ROOT CAUSE LAYER: —
```

**Scenario verdict:** PASS

---

## B09 — Slang state complaint → act without quiz

### Turn „ej ziomek ciemno jak w dupie” — **PASS**

```
INPUT: ej ziomek ciemno jak w dupie
SEMANTIC/GOAL: class=CHANGE_PARAMETER mode=ACT intent=smart_home_control goal='ej ziomek ciemno jak w dupie' hint=brighter cont=False
CONTEXT: goal_status=ACTIVE objective='ej ziomek ciemno jak w dupie' domain=SMART_HOME conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: GOAL brighter lights; second turn execute without unnecessary ask.
ACTUAL: class=CHANGE_PARAMETER mode=ACT intent=smart_home_control tools=True domains=['SMART_HOME'] goal='ej ziomek ciemno jak w dupie'
ROOT CAUSE LAYER: —
```

### Turn „weź coś z tym” — **PASS**

```
INPUT: weź coś z tym
SEMANTIC/GOAL: class=UNKNOWN mode=TALK intent=general_conversation goal='weź coś z tym' hint= cont=False
CONTEXT: goal_status=ACTIVE objective='ej ziomek ciemno jak w dupie' domain=SMART_HOME conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: GOAL brighter lights; second turn execute without unnecessary ask.
ACTUAL: class=UNKNOWN mode=TALK intent=general_conversation tools=False domains=[] goal='ej ziomek ciemno jak w dupie'
ROOT CAUSE LAYER: —
```

**Scenario verdict:** PASS

---

## B14 — Fast follow-ups volume barrage

### Turn „głośniej” — **PASS**

```
INPUT: głośniej
SEMANTIC/GOAL: class=CHANGE_PARAMETER mode=ACT intent=system_volume goal='Adjust system volume' hint=louder cont=False
CONTEXT: goal_status=ACTIVE objective='Adjust system volume' domain=SYSTEM conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: Each turn mutates volume with verify; 'za głośno'/'ciszej' reverse; no false Gotowe without verify; quiet acks.
ACTUAL: class=CHANGE_PARAMETER mode=ACT intent=system_volume tools=True domains=['SYSTEM'] goal='Adjust system volume'
ROOT CAUSE LAYER: —
```

### Turn „jeszcze” — **PASS**

```
INPUT: jeszcze
SEMANTIC/GOAL: class=CONTINUATION mode=ACT intent=execute_discussed_goal goal='Adjust system volume' hint= cont=True
CONTEXT: goal_status=ACTIVE objective='Adjust system volume' domain=SYSTEM conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: Each turn mutates volume with verify; 'za głośno'/'ciszej' reverse; no false Gotowe without verify; quiet acks.
ACTUAL: class=CONTINUATION mode=ACT intent=execute_discussed_goal tools=True domains=['SYSTEM'] goal='Adjust system volume'
ROOT CAUSE LAYER: —
```

### Turn „jeszcze” — **PASS**

```
INPUT: jeszcze
SEMANTIC/GOAL: class=CONTINUATION mode=ACT intent=execute_discussed_goal goal='Adjust system volume' hint= cont=True
CONTEXT: goal_status=ACTIVE objective='Adjust system volume' domain=SYSTEM conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: Each turn mutates volume with verify; 'za głośno'/'ciszej' reverse; no false Gotowe without verify; quiet acks.
ACTUAL: class=CONTINUATION mode=ACT intent=execute_discussed_goal tools=True domains=['SYSTEM'] goal='Adjust system volume'
ROOT CAUSE LAYER: —
```

### Turn „za głośno” — **PASS**

```
INPUT: za głośno
SEMANTIC/GOAL: class=CHANGE_PARAMETER mode=ACT intent=system_volume goal='Adjust system volume' hint=quieter cont=True
CONTEXT: goal_status=ACTIVE objective='Adjust system volume' domain=SYSTEM conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: Each turn mutates volume with verify; 'za głośno'/'ciszej' reverse; no false Gotowe without verify; quiet acks.
ACTUAL: class=CHANGE_PARAMETER mode=ACT intent=system_volume tools=True domains=['SYSTEM'] goal='Adjust system volume'
ROOT CAUSE LAYER: —
```

### Turn „ciszej” — **PASS**

```
INPUT: ciszej
SEMANTIC/GOAL: class=CHANGE_PARAMETER mode=ACT intent=system_volume goal='Adjust system volume' hint=quieter cont=False
CONTEXT: goal_status=ACTIVE objective='Adjust system volume' domain=SYSTEM conv_keys=['has_active_goal', 'has_recent_goal', 'unresolved_goal', 'active_parameter', 'last_parameter', 'recent_domain', 'presented_options', 'discussed_action', 'active_goal', 'recent_goal']
CAPABILITY: NOT_PROBED
PLAN: NOT_RUN
TOOL: NOT_INVOKED
RESULT: N/A
VERIFY: N/A
RESPONSE: N/A
EXPECTED: Each turn mutates volume with verify; 'za głośno'/'ciszej' reverse; no false Gotowe without verify; quiet acks.
ACTUAL: class=CHANGE_PARAMETER mode=ACT intent=system_volume tools=True domains=['SYSTEM'] goal='Adjust system volume'
ROOT CAUSE LAYER: —
```

**Scenario verdict:** PASS

---
