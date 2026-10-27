# Elian behavior-fix acceptance (C.5.4)

## ELIAN INTELLIGENCE CONTRACT

1. Understand first.
2. Context second.
3. Context resolves but does not invent intent.
4. Talk is a valid successful action.
5. Plan only if needed.
6. Tools only if needed.
7. FastPath is execution optimization, not intelligence.
8. AI semantics never bypass Policy.
9. Side effects require semantic evidence.
10. Current-world claims require fresh observation.
11. Mutations require verification when available.
12. No fake success.
13. No fake perception.
14. No blind retry after uncertain side effect.
15. Clarify real uncertainty.
16. Do not say "I don't understand" when normal conversation is possible.
17. Responses are semantic, not tool traces.
18. Follow-up questions are contextual, not automatic.
19. Proactivity requires user value.
20. One Elian, one runtime, one Planner.

## Semantic Executive

Layer name: **AI Semantic Executive** / **AI Semantic Decision** (not EIA).

Pipeline:

`ConversationManager.handle` â†’ Semantic Executive â†’ Relevant Context Selection â†’ Planner â†’ Policy â†’ ToolRegistry â†’ Observation â†’ Verification â†’ Jarvis response.

One Elian. No second chatbot, ConversationManager, Planner, memory, or autonomous loop.

The same Ollama `ModelClient` as the Planner may be used. High-confidence deterministic classification does **not** add an extra LLM call. A structured semantic LLM pass runs only on residual low-confidence utterances; TALK/ANSWER may carry `draft_reply` so Planner does not call the model again to â€śunderstandâ€ť.

### DecisionEnvelope

`mode`: `TALK | ANSWER | OBSERVE | ACT | PLAN | ASK | WAIT | RESUME | CANCEL | SIMULATE | PROACTIVE_DIALOGUE`

Plus: `intent`, `user_goal`, `confidence`, `needs_tools`, `allowed_domains`, `requires_fresh_observation`, `referents`, `continuation`, `needs_clarification`, `missing_information`, `response_style`, `safety_notes`, `negated`, `hypothetical`, `quoted`, `correction`, `wait_requested`, `cancel_requested`.

Optional Part X: `conversation_topic`, `task_reference`, `decision_reference`, `user_is_responding_to_elian`, `followup_kind`, `recommended_next_action`.

`intent` is a semantic label, not a keyword router.

### Confidence contract

| Range | Policy |
|-------|--------|
| `>= 0.90` (`CONFIDENCE_STRONG`) | Strong semantic result; FastPath allowed only for unambiguous ACT in SYSTEM/SMART_HOME/APP |
| `0.75â€“0.89` (`CONFIDENCE_USABLE`) | Usable for TALK / read-only, or ACT when deterministic evidence confirms |
| `< 0.75` | No side effect without additional evidence; ASK / conversation |

Destructive/sensitive actions still require the existing confirmation policy.

### Fallback when semantic AI is unavailable

1. Deterministic high-confidence safe known intents.
2. Otherwise conversation or clarification.
3. Side effects only with strong explicit evidence.

Never: timeout â†’ stale Spotify context â†’ `open_app`.

### TALK is first-class

Jokes, small talk, explanations, brainstorming, and opinions do not need tools. `"Nie rozumiem."` is last-resort ASR noise, not a command-miss.

### Mixed TALK + TOOL

The existing Planner compound path may include a non-tool `talk` / `creative_humor` step plus tool steps, ordered. No second planner.

### Trace

```
[AI SEMANTIC] mode= intent= confidence= goal= needs_tools= allowed_domains= continuation= referents= needs_clarification= context_requested=
```

No chain-of-thought.

## Behavioral eval

Minimum **650** cases: 250 deterministic + 250 context-poison + 150 semantic-natural-language.

Metric: `generic_callcenter_closer_rate` must be **0** on spoken output. Follow-up offers are allowed only when a useful next step is obvious.

## Part X â€” proactive collaboration

Elian may start a conversation only with **user value** (task blocked, requested analysis done, critical failure, reminder, user asked to be notified). Not for `ACTIVE_WINDOW_CHANGED`, vision ticks, or small successful commands.

`ActiveTaskContext` is observable state of the existing plan â€” not a second Planner. `OptionContext` holds a pending decision so short replies (`tak`, `pierwsza`, `czemu?`, `a inaczej?`) resolve safely.

Conversation topic and active task are **separate salience tracks**. A joke during a Notepad task is a joke; later â€śjak tam z tamtym?â€ť returns to the task.

New eval (additive): â‰Ą50 proactive/dialogue cases + â‰Ą30 multi-turn scenarios (3â€“8 turns). Existing 650 cases remain.

New metrics (target 0 unless noted): `irrelevant_proactive_speech_rate`, `internal_event_proactive_rate`, `wrong_pending_decision_resolution_rate`, `task_context_hijack_rate`, `conversation_context_hijack_rate`, `generic_callcenter_closer_rate`. `useful_followup_offer_rate` is informational.

Final software name remains **ELIAN 2.0.13.C.5.4**. Stage D and hardware PASS are out of scope.

## ELIAN 2.0.13.C.5.5 â€” FastPath + verified SMART_HOME

Additive on top of C.5.4. Stage D and hardware PASS remain out of scope.

### FASTPATH CONTRACT

1. FastPath is never a semantic authority.
2. Semantic Executive establishes user intent first.
3. Context may resolve but not invent an action.
4. FastPath still passes ToolPermission + ActionFirewall.
5. Side effects still require target resolution.
6. Mutations require postcondition verification when available.
7. ToolResult is not automatically spoken.
8. User-facing response is semantic.
9. FastPath cannot bypass negation/hypothetical/quotation.
10. AI failure does not expose broad mutations.

Pipeline:

`USER â†’ raw/normalized text â†’ AI Semantic Executive â†’ relevant context â†’ FastPath OR Planner â†’ Policy / ActionFirewall â†’ ToolRegistry â†’ Observation â†’ Verification â†’ Jarvis response`

FastPath may skip expensive general planning for an obvious deterministic ACT. It may not skip semantic intent, negation, hypothetical detection, quoted-command detection, context relevance, tool permission, action firewall, target resolution, verification, or response policy.

### HA mutation contract

COMMAND ACCEPTED is not SUCCESS. Outcomes: `SUCCESS`, `ALREADY_SATISFIED`, `FAILED`, `UNKNOWN`/`PARTIAL`, `UNAVAILABLE`. UNKNOWN is never spoken as success. Unverified side effects are not blindly retried.

Behavioral eval minimum **900** core cases (existing 650 + SMART_HOME 100 + hardening â‰Ą150) plus **300** randomized context-poison combinations (total â‰Ą1200). New metrics remain target 0, including `dictation_target_leak_rate`.

`--ha-action-smoke` and `--foreground-smoke --mock` are required software gates. Live `--foreground-smoke` is read-only hardware helper, not a PASS mark.
