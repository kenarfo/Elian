# Grocky P1 — situational „ciemno tu” (compositional lighting)

Date: 2026-09-11. Owner: **Grocky** (semantic). Sentry bar: no phrase whitelist sprawl.

## 1. Observed
Charter example „ciemno tu” already mapped ACT `smart_home_control` (SOFTWARE). Gaps were **rigid phrase-order** detectors: `nie widzę nic` / `ledwo widzę` → TALK; list-of-phrases architecture (Sentry NEEDS_REWORK risk).

## 2. TRACE (ciemno tu)
INPUT `ciemno tu`
→ SEMANTIC ACT smart_home_control SMART_HOME tools=True (implicit_light, desired=brighter)
→ ACTIVE GOAL soft lighting (incomplete target OK → clarify which light)
→ CONTEXT/CAP/PLAN/TOOL: downstream Memik/Toolik (not this fix)
→ RESPONSE: agent-smoke still asks „Które światło…” when no ActiveDevice — correct, not waiting for „włącz lampę”

## 3. Root cause (hypothesis → evidence)
**Evidence:** `IMPLICIT_LIGHT_*` was an alternation of full phrases (`nic nie widzę` order-locked).
**Hypothesis confirmed:** compositional cues (dark stem / negated|weak vision / want light) cover word-order variants without growing the list.

## 4. Owner / files
Grocky: `core/intent_family.py` — `looks_insufficient_light`, `looks_want_brighter`, rewritten `_implicit_lighting`; opinion guard (`nie lubię ciemności` ≠ goal).

## 5. Acceptance
- „ciemno tu” / „tu jest ciemno” / „nie widzę nic” / „ledwo widzę” → SMART_HOME ACT, not TALK
- „przyciemnij” → darker; „nie lubię ciemności” → not lighting goal
- „otwórz spotify” unchanged APP
- smoke 11/11 · active_goal_semantics OK
- Known gap (not patched with metaphor phrase): „tu jak w piwnicy” — needs broader situation model / LLM, not a new regex

## SOFTWARE vs UX
SOFTWARE semantic PASS for situational darkness. Full Jarvis UX still needs Cap P0 + target resolve (Toolik/Memik) + rematch rooms.