# Memory architecture

Canonical manager: `memory/manager.py` (`Data/elian_memory.json`).

## Memory 2.0 schema (`schema_version`: 4)

Typed long-term knowledge — **not** WorldState (live HA/media/PC snapshot) and **not** ProcedureEngine (`Data/procedures.json`).

Each entry: `id`, `type`, `key`/`subject`, `value`, `source`, `confidence`, `created_at`, `updated_at`, `last_used_at`, optional `expires_at`, `importance`, `provenance`.

| Type | Persistence | Notes |
|------|-------------|--------|
| `FACT` | persistent | Explicit user facts |
| `PREFERENCE` | persistent | Outranks a weak observation. Existing `set_preference` / `get_preference` API unchanged |
| `RELATION` | persistent | Aliases (`moja lampa to light.vizzini`) |
| `ROUTINE` | persistent pointer | Remembered user phrase → action. Does **not** replace `Data/procedures.json` |
| `VOCABULARY` | persistent pointer | Dual-writes `memory/vocabulary.py` (`personal_vocabulary.json`) |
| `TEMPORARY` | persistent until `expires_at` | Short-term notes. Not a second calendar |
| `SESSION` | in-memory only | Dropped on `clear_session` / process restart |

### Migration (3 → 4)

Existing `facts[]` and `preferences[]` are **kept** as projections. On first load, they are copied into `entries[]` with provenance `migrated_from`. User memory is not wiped. `memory_preference` (`music.default_app` via `set_preference` / `get_preference`) stays the same keys and overwrite-on-explicit-set behavior.

### Conflict / confidence

- Explicit user preference (`confidence` ≥ 0.8) is not overwritten by a weak observation.
- Same key + same value → merge (confirmations++, `last_used_at`).
- Same key + different value: stronger/better-confirmed wins; two strong explicit facts **ask** before overwrite.
- Retrieval and `get_preference` update `last_used_at`.
- Rarely used low-value rows are **cleanup candidates** only — important prefs/relations are never auto-deleted. Expired `TEMPORARY`/`SESSION` may be purged.

### Compact retrieval

`MemoryManager.select_compact_memory(goal)` / `build_context()` send Planner and Semantic Executive a **goal-scoped slice** (type + meaning + recency + confidence), never the whole file. `compact_memory` is also attached on the agent context.

### Coexistence

| Store | Role |
|-------|------|
| `set_preference` / `get_preference` | Canonical preference API (`memory_preference`) |
| `memory/vocabulary.py` | Phrase aliases; Memory 2.0 dual-writes `VOCABULARY` |
| `core/learned_routines.py` | “when I say X, do Y” tool recipes; teach path unchanged |
| Calendar tools | Source of truth for events; TEMPORARY notes set `provenance.calendar=false` |
| WorldState | Live snapshot — not merged into this file |

## Layers (Phase 1 facade)

`memory/layers.py` names existing stores. It does not replace persistence. `salient_layers()` still does **not** dump SEMANTIC/PROJECT into talk/jokes.

## Privacy

`memory/sensitive.py` refuses passwords/tokens. No auto-save of casual chat. User “zapomnij” remains a MemoryManager command.
