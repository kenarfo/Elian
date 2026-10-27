# Phase 13 — Elian as one agent

The Jarvis-class master build ([ELIAN_MASTER_AUDIT.md](ELIAN_MASTER_AUDIT.md)) supersedes stage *order* where they conflict: WorldState/Salience/Memory (master Phase 1) land **before** Stage D BrowserAgent.

Pipeline stays:

`ConversationManager.handle` → Semantic Executive → Salience → FastPath or Planner → Policy → ToolRegistry → Observation → response

No second execution engine. UI, procedures, and computer/browser control attach to this path.

## Stage A — HARDWARE PASS (closed)

APP+SYSTEM compound, plan ids, retry failed-only, NP veto, APP close vs HA power-off, volume direction guard.

## Stage B — HARDWARE PASS (closed)

Ordered semantic steps across APP, SYSTEM, SMART_HOME, WEB, MEDIA, VISION, PROCEDURE.

## Stage C — full ComputerAgent (this stage)

Facade over Windows API, UI Automation, existing vision tools, and ComputerControl.
Same pipeline: ConversationManager.handle → Planner → ToolRegistry → ComputerAgent.

Preference order: native OS/API → Windows UI Automation → application API → (Stage D DOM) → screen vision + verified bounds. Blind coordinates forbidden.

`--computer-agent-smoke` 20/20. `--jarvis-core-smoke` remains 15/15.

**Not in Stage C:** BrowserAgent, ProcedureEngine, ambient listening, full WorldState G, camera, Elian UI.

## Stage D — full BrowserAgent + music control (not implemented)

Backend: MANAGED Playwright now; ATTACHED later.

Navigate, inspect DOM, find/click/fill/type/select/scroll, tabs (close exact tab, not the whole browser), back/forward, wait, download/upload, extract, read state.

If `ActiveServiceContext.resolved_kind=WEBSITE`:

`"Zamknij Spotify."` → `close_browser_tab(tab_id)` for that Spotify tab.

Music: search → inspect results → choose matching track → click → verify playback when observable. `"Puść drugi wynik."` uses `last_results[1]`. No fake media claims.

## Stage E — ProcedureEngine (not implemented)

Named procedures: sleep, morning, gaming, study, movie, stream, leave_home, home.

Natural triggers, not exact commands. Mention is not trigger (`"Co oznacza dzień dobry?"` ≠ morning).

Voice editing is versioned (`procedure_version`, `updated_at`, `previous_version`). Undo restores previous version.

Execution still goes through ToolRegistry.

## Stage F — continuous conversation (not implemented)

SalienceEngine, active conversation, optional ambient listening, barge-in, background filtering, corrections, interrupt plan. No constant `"W czym mogę pomóc?"`.

## Stage G — WorldState / trace / proactive (not implemented)

Full WorldState, ActionPolicyEngine, ActionVerifier, audit trace, replay dry-run, proactive events, behavioral eval ≥ 50.

Stage B only prepared a minimal store. Do not dump WorldState into the prompt.

## Stage H — camera / presence (not implemented)

Provider APIs only at first. No automatic capture.

## Stage I — Elian UI (not implemented)

There is **no** `ui/` folder in this repo today and no GUI framework in the Python stack (no PySide/Qt/tkinter app).

**Proposal (do not install in Stage B):** PySide6 on Windows — light enough, native, one process with the existing runtime.

UI is a view/control surface for ConversationManager, JarvisRuntime, WorldState, ProcedureEngine, and tool observations. Zero separate execution engine.

Planned surfaces:

- Main: ELIAN, status IDLE/LISTENING/PROCESSING/SPEAKING/EXECUTING, orb, mic, wake/ambient/session
- Live conversation with subtle plan/step/tool activity
- Computer/service status (active app/service, `resolved_kind`, tab, media, device, procedure)
- Procedure visual editor (still executes via backend tools)
- Smart home quick controls that call canonical Elian tools (no safety bypass)
- Optional debug drawer (`plan_id` / `step_id` / observation / latency)
- Always-visible privacy indicators (MIC / AMBIENT / CAMERA / SCREEN INSPECTION)

Later optional: overlay, tray, push-to-talk, mini orb, notifications. Not Stage B.
