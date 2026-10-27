# Capability router

`core/capability_map.py` wraps `core/health.py`. It is not a second health registry and not a second planner.

| Capability | Phase 1 status |
|------------|----------------|
| LocalAI | from health (ollama/ai) |
| CloudAI | DISABLED (opt-in later) |
| Internet | from health network if present |
| ComputerControl | READY (existing ComputerAgent) |
| Browser | UNAVAILABLE (Stage D not built) |
| ScreenPerception | READY |
| HomeAssistant | from health |
| Calendar | adapter; often NOT_CONFIGURED |
| Camera | DISABLED |
| MR | DISABLED |

If a capability is UNAVAILABLE, tools must say so. Do not pretend Playwright or camera works.

Model routing remains one Ollama `ModelClient`. No silent multi-GB downloads.
