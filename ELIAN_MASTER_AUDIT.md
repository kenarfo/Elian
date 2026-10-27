# ELIAN MASTER AUDIT

Jarvis-class target vs the locked C.5.5 runtime. Software only. Hardware campaign is **not** globally PASS.

## Baseline (locked before this run)

| Gate | Value |
|------|--------|
| unittest discover | **840 OK** (was 827 before Phase 1) |
| behavioral eval | 1200 / 1200 |
| `--foreground-smoke --mock` | green |
| `--ha-action-smoke` | required green |
| `--agent-smoke` | required green |
| Global hardware | NOT PASS |

Do not weaken tests to land new work.

## Current architecture map

```text
USER → STT / text
  → ASR repair (verb whitelist only)
  → raw + normalized transcript
  → session / memory / proactive prefs
  → AI Semantic Executive (DecisionEnvelope)
  → Salience (ContextSelector adapter)
  → FastPath + ActionFirewall    OR   Planner + ToolRegistry
  → Observation / HA verify / ComputerAgent verify
  → Jarvis response + SpeechGuard + TTS
```

One `ConversationManager`. One Planner. Legacy Skills/Commands/IntentRouter remain fallbacks with metrics.

Logical “brains” are modules, not agents:

| Master name | Existing module | Status |
|-------------|-----------------|--------|
| UnderstandingBrain | `core/semantic_executive.py` | ALREADY_IMPLEMENTED |
| ConversationBrain | `core/semantic_talk.py`, `jarvis_response.py` | PARTIAL |
| ContextBrain / SalienceEngine | `core/context_selection.py`, `core/salience.py` | PARTIAL |
| MemorySystem | `memory/manager.py` + `memory/layers.py` | PARTIAL |
| WorldState | `core/world_state.py` | PARTIAL |
| Planner | `core/planner.py` | ALREADY_IMPLEMENTED |
| PolicyEngine | `core/action_firewall.py`, `core/agent_safety.py`, `core/risk.py` | CONFLICTING |
| CapabilityRouter | `core/capability_map.py`, `core/health.py` | PARTIAL |
| PerceptionEngine | `core/perception/*` | PARTIAL |
| TaskBrain | `core/collaboration.py` ActiveTaskContext | PARTIAL |
| CollaborationEngine | `core/collaboration.py` | PARTIAL |
| ProcedureEngine | `core/procedure_contract.py` | MISSING (honest unavailable) |
| ProactiveEngine | `core/proactive.py` | PARTIAL |
| PersonalityEngine | `core/jarvis_response.py`, `behavior.py` | PARTIAL |
| ResponseComposer | `core/jarvis_response.py`, `speech_guard.py` | ALREADY_IMPLEMENTED |

## Already implemented

- C.5.5 AI-first Semantic Executive, FastPath after understanding, ActionFirewall
- DecisionEnvelope schema validation (`validate_envelope`)
- TALK as first-class; jokes/small talk skip tools
- Continuation / dictation ACTION-TARGET-PAYLOAD
- ComputerAgent observe → find → focus → verify → act
- Foreground provider chain + interactive `--foreground-smoke`
- Screen truth for “co widzisz?” (fresh observation)
- HA before/after verify foundation; no fake success on unknown
- Jarvis speech guard; no call-center closer by default
- Proactive IGNORE vs TELL filtering for internal vision/window events
- Local calendar CRUD (`integrations/calendar/`) — external Google sync not present
- Media tools + ActiveServiceContext
- Collaboration OptionContext / “dobra, rób” / task vs joke split (C.5.4)

## Partial

- WorldState: session dict + observation freshness (Phase 1). Missing fused CURRENT/STALE for HA, media, calendar, network.
- Salience: talk/joke excludes tools context. Missing full entity/project salience scoring.
- Memory: short/long/prefs exist. Named layers are a facade. No project persistence, no procedural memory, no semantic compression of long chats.
- Envelope flags `hypothetical` / `quoted` / `correction` now stamped. Modes `SIMULATE`/`RESUME`/`CANCEL` are schema-valid; classifiers still emit WAIT/ANSWER for compatibility with the 1200 eval.
- Model router: single Ollama client. No local-vs-cloud ladder.
- Offline: degraded FastPath/HA/calendar documented, not a first-class OfflineMode with one-shot network speech.
- ActiveTaskContext fields expanded; resume/pause/“jak idzie?” coverage is incomplete.
- PersonalVocabulary store exists; not wired into ASR repair yet.

## Missing (do not fake)

- Stage D BrowserAgent / Playwright DOM
- Stage E ProcedureEngine execution
- Undo brain / ActionHistory inverses
- Timeline / “co mnie ominęło?”
- HUD / overlay
- Camera / presence / speaker ID (must stay DISABLED)
- Spatial / MR
- Correction learning that persists (policy-safe)
- 2000 semantic + 1000 poison + 100 multi-turn scenarios (today: **1200** single-turn, **550** poison, **32** collab scenarios / 100 turns)

## Conflicts (do not add a third layer)

Deeper read of `core/` after Phase 1. These are the highest-risk forks. Phase 2 should **unify**, not replace.

| Conflict | Modules | Risk |
|----------|---------|------|
| Dual mutation policy | `action_firewall.allow_tool` (FastPath + envelope) vs `agent_safety.side_effect_blocked_for_intent` (Planner + intent_family) | Same utterance can pass one gate and fail the other. `MUTATION_TOOLS` ≠ `SIDE_EFFECT_TOOLS`. |
| Dual classifiers | `semantic_executive` vs `intent_family.classify_intent_family` | Mode/intent mismatch on edge cases. Envelope is canonical; family is Planner binding. |
| Triple “what is active” | `world_state`, `ConversationContext.py`, `MemoryManager.short` | Stale referents. Writes should converge on WorldState + salience fragments. |
| Four pending mechanisms | clarification, tool confirm, `pending_executable_goal`, `OptionContext` | Wrong merge/clear. |
| Browser catalog vs runtime | `builtin_tools` registers `browser_*`; `browser_contract` always UNAVAILABLE | Planner can name a capability that cannot run. Keep honest unavailable until Stage D. |
| Dual memory write | `Memory.py.record_action` vs `memory/manager.py` | Duplicate facts. |
| Dual unprompted-speech policy | `proactive.py` vs `collaboration.initiate` | Overlap is OK if both require user value; do not add a third. |

Production `ConversationManager` still calls `decide_semantics(..., allow_llm=False)`. FastPath does not go through Salience; Agent context does. That split is intentional — do not put an LLM in FastPath.

Local calendar is implemented. BrowserAgent, ProcedureEngine, timeline, HUD, simulation-as-user-mode, and experience replay are still missing.

## Dangerous duplication risks

| Risk | Do this instead |
|------|-----------------|
| Second Planner / second ConversationManager | Forbidden |
| Second WorldState store | Extend `core/world_state.py` |
| Second health system | `capability_map` wraps `health.py` |
| Second context selector | `salience.select_salient` wraps `select_context` |
| Dumping WorldState into Planner prompts | ContextSelector/Salience only |
| Rebuilding ComputerAgent | Preserve verified worker |
| FastAPI/React “brains” | Not this product |
| Silent Playwright / camera / multi-GB models | User decision required |

## Technical debt affecting the roadmap

- Legacy Skills/Commands/IntentRouter still reachable after Agent
- Dual memory: `Memory.py` + `MemoryManager`
- WorldState started as Stage B “minimal store”
- Browser tools return honest UNAVAILABLE (good) but remain in the planner catalog — Stage D must implement, not invent a second catalog
- Eval still regex-heavy pass-1; residual LLM pass is optional; CM mic path is deterministic-first (`allow_llm=False`)
- `irrelevant_context_injection_rate` now measured; ~15 master §78 metrics still have no runtime counter
- `episodic_hooks` exists in schema and stays empty
- `plan_state.py` is IDs/idempotency, not a full FSM

## Recommended phase boundaries

| Phase | Scope | This run |
|-------|--------|----------|
| 0 | Audit, docs, baseline lock | SOFTWARE COMPLETE |
| 1 | WorldState freshness, Salience adapter, memory layers, CapabilityMap | SOFTWARE COMPLETE |
| 2 | Unify FastPath/Agent policy behind envelope; repair/correction learning; no third firewall | NOT STARTED |
| 3 | Offline mode + model routing | NOT STARTED |
| 4 | Stage D BrowserAgent | NOT STARTED — hardware later |
| 5 | Procedures + undo + simulate-as-mode | NOT STARTED |
| 6 | Timeline + “co mnie ominęło?” | NOT STARTED |
| 7 | Calendar intelligence / focus | NOT STARTED (adapter exists) |
| 8–11 | Trace hardening, camera, HUD, MR | NOT STARTED |

Stop a phase on any unittest / 1200 eval / mock-smoke regression.

## Test baseline (this document’s lock)

See [RELEASE_GATES.md](RELEASE_GATES.md). Hardware validation remains separate.
