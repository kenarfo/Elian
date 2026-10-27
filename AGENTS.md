# Elian — agent map

Polish Windows **voice assistant** (wake → STT → conversation → TTS). Local Ollama agent, ToolRegistry, Home Assistant, vision, memory, RC validation. Not a web app.

## Routing order (`ConversationManager.handle`)

1. Session control (wait / end / cancel / ACK)
2. Memory / proactive prefs
3. Utterance analysis (filler, negation, correction)
4. **AI Semantic Executive** (DecisionEnvelope — meaning signals)
5. **Cognitive Core** (importance / intentions — guidance)
6. Relevant context (referents / continuation)
7. **Goal formulation + ActionLoop** (UserGoal; soft shortcuts only when rock-solid)
8. **Agent → Planner → ToolRegistry** (**default intelligence** — goal → plan → execute → verify → repair)
9. **FastPath** only for obvious deterministic ops (volume / clear APP / briefings) at confidence ≥ 0.90
10. Legacy Skills/Commands/IntentRouter — metrics / last resort only
11. Brain chat (`ask_ai`)

Devices: any Home Assistant entity (any brand) via DeviceResolver + `smart_home.aliases`. BLE/MQTT bridges publish into HA; voice does not talk brand APIs.

## Key paths

| Area | Location |
|------|----------|
| Orchestration | `core/conversation_manager.py`, `core/runtime.py` |
| Agent contract | `core/planner.py`, `intent_family.py`, `agent_repair.py`, `agent_safety.py`, `semantic_executive.py` |
| Cognitive Core | `core/cognitive_core.py` (after DecisionEnvelope; no tool execution) |
| Tools | `core/tools.py`, `core/builtin_tools.py`, `integrations/*/tools.py` |
| Voice | `voice/`, `WakeWord.py`, `Voice.py` |
| Vision | `vision/` |
| RC | `core/rc_validation/`, `Data/diagnostics/` |
| Self-improvement | `core/self_improve/` (log → gap → proposal → sandbox → verify → approve → adopt), `SELF_IMPROVEMENT.md` |
| Docs | `ARCHITECTURE.md`, `ELIAN_MASTER_AUDIT.md`, `RELEASE_GATES.md` |

## CLI gates (before mic RC)

```bash
python -m unittest discover -s tests -p "test_*.py"
python main.py --agent-smoke
# optional: python main.py --agent-smoke-real
# optional Phone Companion (offline): python main.py --companion-smoke
```

## Cursor guidance

- Project rules: `.cursor/rules/*.mdc`
- Project skills: `.cursor/skills/*/SKILL.md` (`agent-smoke-gate`, `rc-bugfix-pass`, `elian-regression-test`)
- Prefer `core/` + ToolRegistry over new legacy paths
- Do not touch `memory_preference` / custom wake onnx unless the phase explicitly asks
