# Legacy deprecation notes (Phase 9)

Migration path: **legacy → deprecated → no callers → tests → remove**

## Metrics (measure before remove)

`core.metrics.ConversationMetrics.record_legacy(kind)` increments:

| Metric key | Source |
|------------|--------|
| `legacy.skills` | `SkillsLoader.run_skills` |
| `legacy.commands` | `Commands.run_command` |
| `legacy.intent_router` | `IntentRouter.run_intent` |
| `legacy.goal_router` | (reserved; IntentRouter internal) |
| `legacy.memory` | (reserved; Memory.py direct) |
| `legacy.wake_text` | Text wake in ConversationManager standby |
| `legacy.screenshot` | (reserved; ComputerControl) |

Aggregate: `legacy_fallback_used`, `legacy_fallback_rate`.

## Coverage map

| Component | Current use | Replacement | Test coverage | Safe to remove? |
|-----------|-------------|-------------|---------------|-----------------|
| SkillsLoader | CM fallback + optional startup | ToolRegistry / FastPath / Agent | Phase 8 scenarios | **No** |
| Commands | CM fallback | FastPath + tools | test_core, FastPath | **No** |
| IntentRouter | CM fallback | Agent + tools | test_core | **No** — unique paths remain |
| GoalRouter | IntentRouter only | Agent + media tools | indirect | **No** |
| Brain.py | Last-resort chat | Keep as chat layer | test_conversation_phase8 | **Keep** |
| Memory.py | Brain, IR, tools record_action | MemoryManager | test_memory | **No** — dual-write |
| ConversationContext | IR, HA TTL | Partial MemoryManager | test_core | **No** |
| WakeWord text | Standby + voice fallback | Acoustic wake | test_voice_pipeline | **Keep fallback** |
| ComputerControl.take_screenshot | IntentRouter | Vision tools | test_vision | Deprecate after IR migration |
| Skills/Music | Skills + IR overlap | integrations/media | test_integrations | Partial overlap |

## IntentRouter — capabilities still unique

Review before removal:

- Some game/web/music goal flows with ConversationContext pending state
- Legacy screenshot + describe paths
- Confidence-based routing not fully duplicated in Agent

**Action:** migrate remaining flows to ToolRegistry; then soft-disable via config.

## Normal path target

Covered scenarios (volume, open app, HA, reminder, calendar, media) should show:

```text
legacy_fallback_used = 0
```

Verified in `tests/test_hardening.py::FastPathZeroHeavyTests`.

## Startup

`settings.legacy_skills_startup=false` skips `load_skills()` at boot (skills still reachable via CM fallback until removed).

Track `legacy.*` in production-like sessions before deleting modules.
