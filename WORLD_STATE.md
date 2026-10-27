# WorldState

Canonical store: `core/world_state.py`. Live session snapshot only — not long-term memory (`memory/` keeps `remember` / `prefer`).

## Shape

Each slot is a fact with `value`, `timestamp`, `ttl_s`, `source`, freshness, and status:

- `KNOWN` — live (freshness `CURRENT` for Stage B tests)
- `STALE` — TTL expired
- `UNKNOWN` — never observed; do not invent
- `UNAVAILABLE` — integration down

Planner receives `WorldSnapshot.compact_for_llm()` (short lines). Salience/ContextSelector picks fragments. Never dump the whole store.

`get_world_state()` still returns Stage B keys (`services`, `conversation`, `active_plan`, `smart_home`, `computer`, `screen`) so older tests stay valid. Full state is `take_snapshot()`.

## Slots

Session, active referent, recently mentioned objects, Home Assistant (including `light.vizzini` when observed), media, volume, active app/window, PC, calendar, reminders, last weather, integration availability, STT/TTS (read-only from health if present), last actions, procedure.

## Updates

- ToolRegistry `_finalize` → `apply_tool_result` (no extra I/O)
- EventBus → `ingest_event`
- Existing `note_*` writers (HA/media/screen/computer)

Weaker sources (`MODEL_KNOWLEDGE`) cannot overwrite stronger live facts (`LOCAL_HOME_ASSISTANT`).

## Source of truth (preferred, by question type)

LOCAL_RUNTIME > LOCAL_COMPUTER/UIA > CURRENT_SCREEN_PIXELS > LOCAL_VISION > LOCAL_HOME_ASSISTANT > LOCAL_FILE > BROWSER_DOM > WEB > MEMORY > MODEL_KNOWLEDGE

## Honest gaps

STT/TTS are health flags, not barge-in state (ambient/voice loop is owned elsewhere). Procedure execution beyond morning/sleep remains Stage E. Calendar/weather/HA stay UNKNOWN until a tool or event observes them.
