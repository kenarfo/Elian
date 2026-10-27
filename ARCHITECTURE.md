# Elian 2.0 — Architecture

Canonical map: [ELIAN_MASTER_AUDIT.md](ELIAN_MASTER_AUDIT.md). Phase 13 stages D–I: [PHASE13_ROADMAP.md](PHASE13_ROADMAP.md). Gates: [RELEASE_GATES.md](RELEASE_GATES.md).

One runtime. One Planner. Understand first → relevant context → FastPath or Planner → Policy → tools → observe → verify → Jarvis speech.

## Conversation routing (canonical order)

```text
ConversationManager.handle()
  1. Text wake fallback (standby only) → legacy.wake_text metric
  2. Session control (wait / forgot / end / cancel)
  3. Memory commands (MemoryManager)
  4. Proactive preferences
  5. Utterance analysis (fillers, correction, negation)
  6. AI Semantic Executive (DecisionEnvelope; utterance first)
  7. Cognitive Core (CognitiveAssessment — guide remember/forget/act; no tools)
  8. Salience / context selection (referents/continuation only)
  9. FastPath → ActionFirewall → ToolRegistry (no LLM; must not steal questions)
  10. Agent → Planner → ToolRegistry
  11. Legacy fallback (metrics per path):
       SkillsLoader → Commands → IntentRouter (+ GoalRouter)
  12. TextCorrector → Brain (chat only)
```

## Startup flow

```text
main.py
  ├─ validate_voice_config / validate_config
  ├─ initialize_voice (STT/TTS/wake stack)
  ├─ load_memory (legacy Memory.py list + MemoryManager)
  ├─ load_skills (optional legacy; settings.legacy_skills_startup)
  ├─ MQTT bridge + HA registry (integrations)
  ├─ ConversationManager(registry, memory, MemoryManager)
  ├─ VoicePipeline.start (acoustic wake primary)
  └─ ElianRuntime.start (EventBus, ProactiveManager, watchers)

Main loop:
  proactive.try_deliver
  → pipeline.await_wake (acoustic; STT fallback in voice layer)
  → ConversationManager.handle
  → VoicePipeline.speak
```

## Layering (target dependency direction)

```text
UI / Voice (voice/, main.py)
  ↓
Core orchestration (core/conversation_manager, runtime, proactive)
  ↓
Agent / FastPath / Planner
  ↓
ToolRegistry (core/tools, core/builtin_tools, integrations/*/tools)
  ↓
Integrations & providers (HA, calendar, media, messaging)
  ↓
Low-level (Whisper, Piper, Ollama, UIA, OCR)
```

Memory (`memory/`) and Events (`core/event_bus`) are shared services.

## Legacy remaining (intentional)

| Component | Role today | Replacement path |
|-----------|------------|------------------|
| SkillsLoader | Fallback after Agent | Tools + FastPath |
| Commands | Fallback | FastPath + tools |
| IntentRouter | Fallback monolith | Agent + tools |
| GoalRouter | Inside IntentRouter | Agent + media/prefs tools |
| Brain.py | Pure chat | Keep |
| Memory.py | Parallel record_action / search | MemoryManager |
| ConversationContext | HA TTL, pending goals | Partial MemoryManager |
| WakeWord text | Standby fallback | Acoustic wake |
| ComputerControl.take_screenshot | IntentRouter | Vision capture |

Track usage via `core.metrics` → `legacy.*` counters.

## Degraded mode

- **Ollama offline:** FastPath, HA tools, volume, reminders, calendar still work; Agent/Brain return friendly errors.
- **Vision LLM offline:** UIA/OCR fallback in `vision/session.py`.
- **HA offline:** Local calendar/reminders/media/volume unaffected.
- **Watcher crash:** Logged per-watcher; runtime poll loop continues.

## Phase 9 additions

- `core/version.py` — semver source (`2.0.0-rc1`)
- `core/health.py` — component health registry
- `core/doctor.py` — `--doctor`
- `core/startup.py` — startup timing marks
- `core/persistence.py` — corrupt JSON backup + safe read
- `core/performance_diagnostics.py` — `--performance-diagnostics`
- `core/trace.py` — debug ring buffer (optional)
- Lazy vision provider init in `vision/session.py`

## Threads (runtime)

| Thread | Owner | Notes |
|--------|-------|-------|
| `elian-runtime` | ElianRuntime | 1s poll; watchers sync |
| Voice barge-in | VoicePipeline | daemon |
| HA registry refresh | HomeAssistant | daemon |
| MQTT bridge worker | HomeAssistantMqttBridge | daemon |

Watchers (System, HA, Reminder, Calendar) run on runtime tick — not separate threads.

## Data on disk

| Data | File | Persistent |
|------|------|------------|
| Memory facts/prefs | `Data/elian_memory.json` | Yes |
| Legacy memory | `Data/memory.json` | Yes (migrated) |
| Reminders | `Data/reminders.json` | Yes |
| Calendar | `Data/calendar.json` | Yes |
| Config | `Data/config.json` | Yes |
| Secrets | `Data/secrets.json` | Yes |
| Performance diag | `Data/diagnostics/performance_latest.json` | Optional |
| TTS cache | temp WAV | No (ephemeral) |
| Vision snapshot | RAM | No |

Corrupt JSON → `.corrupt.*.bak` backup + safe defaults.
