# GOAL — Shortlist empty when Cap AVAILABLE fix

**Date:** 2026-09-10 (UTC+1)
**Owner:** Memik (planner / tool_shortlist)
**Scope:** Cap AVAILABLE + registered tool ⇒ Planner shortlist includes the tool when intent/goal needs it
**Machine:** Elian local Windows (`C:\Users\kenar\Elian`)

## Root cause (evidence)

Two cooperating bugs in the **shortlist/planner path** (not Cap honesty):

### 1) `talk_only` forced empty shortlist from stale `needs_tools=False`

In `Planner._build_messages`:

```python
if envelope is not None and not bool(getattr(envelope, "needs_tools", True)):
    talk_only = True
```

Then `shortlist_tools(..., talk_only=True)` returned `[]`.

Offline repro (FakeModelClient / dry Cap):

- Cap `WEB_SEARCH=AVAILABLE`, tool `web_search` in catalog
- Envelope `mode=ACT`, `intent=web`, **`needs_tools=False`** (stale / mismatched)
- Classification `IntentFamily.WEB` for `"wyszukaj Elon Musk"` / `"wyszukaj X"`
- **Before:** `talk_only=True` → `shortlist=[]` while compact Caps still printed `WEB_SEARCH=AVAILABLE`
- Matches the symptom: Cap says AVAILABLE, tool exists, shortlist empty

Grocky already fixed deterministic semantic `needs_tools` for `"wyszukaj X"` (`allow_llm=False` → `needs_tools=True`). Residual risk remained: any other path that sets `needs_tools=False` while mode/family/Cap still imply action still blanked the shortlist.

### 2) Stale `Tool.available=False` after Cap cold refresh

`apply_tool_availability` runs mainly at registry build. After Toolik cold auto-refresh Cap → AVAILABLE, catalog rows can still carry `available=False`.

`shortlist_tools` previously did:

```python
available = [item for item in catalog if item.get("available", True)]
```

So Cap-usable `web_search` was dropped **before** `filter_catalog_by_capabilities`. Compact Caps still showed AVAILABLE.

Offline: Cap AVAILABLE + catalog `web_search.available=False` → shortlist omitted `web_search` (or only alternatives).

**Not Cap lying** — Cap status was correct; shortlist ignored it. No Toolik handoff required for honesty.

## Fix (architecture-level)

Invariant: **Cap AVAILABLE + registered tool + intent/goal needs it ⇒ non-empty shortlist including that tool.**

### `core/tool_shortlist.py`
- `_eligible_catalog`: Cap AVAILABLE/DEGRADED wins over stale `Tool.available=False` for shortlist membership
- `goal_needs_shortlist_tools`: narrow override signal (actionable families, ACT/PLAN, allowed domains, explicit web keywords) — does **not** treat media chit-chat like `"Spotify jest fajne"` as needing tools
- `shortlist_tools`: honor `talk_only` only when `goal_needs_shortlist_tools` is false; accept optional `envelope=`

### `core/planner.py`
- Re-sync Cap → `Tool.available` via `apply_tool_availability` before `descriptions_for_planner`
- Clear `talk_only` when `goal_needs_shortlist_tools(...)` is true (even if envelope `needs_tools=False`)
- Pass `envelope` into `shortlist_tools`

### Untouched (as required)
- Semantic executive, Memory/WorldState, Cap honesty / capability_registry probes (Toolik), GUI, voice
- No hardcoded plans for example phrases
- ToolRegistry + safety remain mandatory boundaries
- ActiveGoal P0 evidence gate (`GOAL_P0_FALSE_SUCCESS_FIX.md`) not modified

## Before / after

| Scenario | Before | After |
|---|---|---|
| `"wyszukaj Elon Musk"`, Cap WEB_SEARCH=AVAILABLE, `needs_tools=False`, family=WEB | `shortlist=[]`, Caps still AVAILABLE | `shortlist=[web_search, fetch_web_page, open_website]`, `talk_only=False` |
| Cap AVAILABLE, catalog `web_search.available=False` | `web_search` omitted | `web_search` included |
| Pure talk `"Fajny masz głos"` / `"Spotify jest fajne"` | empty shortlist | empty shortlist (unchanged) |

## Tests

Added in `tests/test_capability_registry.py`:
- `test_shortlist_includes_web_when_cap_available_despite_stale_flag`
- `test_shortlist_not_empty_when_needs_tools_false_but_cap_web_available`
- `PlannerShortlistCapAvailableTests.test_build_messages_shortlist_web_search_when_cap_available`

Scoped runs (pass):
- New Cap/shortlist regressions above
- `tests.test_phase11_1_grounding` (incl. `test_talk_shortlist_empty`)
- `tests.test_phase11_conversation`
- `tests.test_active_goal_semantics` (P0 evidence path not regressed)

Note: `test_ha_disabled_message` can fail on this machine when HA is actually reachable — pre-existing env flake, unrelated.

## Remaining / handoffs

- **No Toolik handoff** for Cap honesty on this bug — Cap was AVAILABLE correctly.
- Optional follow-up (Toolik/Memik): call `apply_tool_availability` on Cap cold refresh globally (planner now self-heals per turn).
- Semantic still owns true `needs_tools`; planner only refuses to blank Cap-backed actionable shortlists when the envelope is stale.
