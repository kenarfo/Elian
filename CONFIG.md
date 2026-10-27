# Elian 2.0 — Configuration

Config file: `Data/config.json` (created from defaults on first run).

Schema version: `settings.config_version` (current: **2**).

Unknown top-level keys trigger developer warnings via `validate_config()` (use `--debug` at startup).

## Core

| Key | Meaning | Default |
|-----|---------|---------|
| `name` | Assistant name | `"Elian"` |
| `language` | UI/voice language | `"pl"` |
| `settings.config_version` | Config schema version | `2` |
| `settings.legacy_skills_startup` | Load Skills at boot | `true` |
| `settings.conversation_idle_timeout` | Session idle (s) | `30` |

## Voice (`voice.*`)

| Key | Default | Valid / notes |
|-----|---------|---------------|
| `wake_enabled` | `true` | Acoustic wake |
| `wake_threshold` | `0.5` | **0.0–1.0** |
| `wake_legacy_stt_fallback` | `true` | Text/STT wake fallback |
| `session_idle_timeout` | `30` | Overrides settings if set |
| `tts_enabled` | `true` | |
| `tts_cache_size` | `16` | Piper phrase cache |
| `vad_silence_ms` | `700` | End-of-utterance |
| `max_recording_seconds` | `20` | STT cap |

## AI / Agent (`ai.*`)

| Key | Default | Valid / notes |
|-----|---------|---------------|
| `agent_enabled` | `true` | Multi-step planner |
| `agent_max_steps` | `6` | **1–20** |
| `max_same_tool_repeats` | `2` | No-progress guard |
| `ollama_model` | `llama3.1:8b` | Text LLM |
| `vision_model` | `""` | Empty = UIA/OCR only |
| `vision_enabled` | `true` | |
| `request_timeout` | `30` | Seconds **1–600** |

## Whisper (`whisper.*`)

| Key | Default |
|-----|---------|
| `model` | `large-v3-turbo` |
| `device` | `cuda` |
| `compute_type` | `float16` |
| `fallback_to_cpu` | `true` |

## Smart home (`smart_home.*`)

| Key | Default |
|-----|---------|
| `enabled` | `false` |
| `url` | HA URL |
| `refresh_interval` | `60` |

## Proactive (`proactive.*`)

| Key | Default |
|-----|---------|
| `enabled` | `true` |
| `system_poll_seconds` | `10` |
| `ha_poll_seconds` | `60` |
| `cpu_threshold` | `95` |

## Integrations (`integrations.*`)

Each subsection (`calendar`, `reminders`, `media`, `messaging`, `home_assistant`, `contacts`, `browser`) has `enabled: true/false`.

### Browser (`integrations.browser.*`) — Stage D BrowserAgent

Off by default. Playwright tools stay **UNAVAILABLE** until both are true:

1. `integrations.browser.enabled` = `true` in `Data/config.json` (keep `false` unless you intentionally experiment)
2. Playwright + Chromium installed:

```bash
pip install playwright
python -m playwright install chromium
```

With opt-in + Playwright, CapabilityMap reports **EXPERIMENTAL** (not full READY until hardware RC). Default config remains `enabled: false` → UNAVAILABLE.

Then: `python main.py --browser-agent-smoke` (uses real Playwright against local fixtures; does not permanently flip `enabled`).

Attached/CDP browser control is not in this stage. ComputerAgent remains the Windows UI fallback. Screenshot is diagnostic only. The planner will not type passwords/tokens from memory.

## Personality (`personality.*`)

| Key | Default |
|-----|---------|
| `profile` | `elian_jarvis` |
| `response_style` | `concise` |
| `formality` | `neutral` |
| `humor` / `dry_humor` | `low` |
| `concise_execution` | `true` |
| `no_service_closing_phrase` | `true` |

## Screen perception (`screen_perception.*`)

| Key | Default | Notes |
|-----|---------|-------|
| `enabled` | `true` | Sensor runtime, not a second agent |
| `mode` | `ACTIVE_LIGHT` | Capture/change on; vision on-demand |
| `provider` | `auto` | WGC if live, else DXGI, else memory |
| `capture_fps` | `20` | Target only; not a correctness gate |
| `store_frames` / `store_video` | `false` | No PNG/video spam |

## Vision (`vision.*`)

| Key | Default |
|-----|---------|
| `enabled` | `true` |
| `provider` | `ollama` |
| `model` | `qwen3-vl:8b` |
| `local_only` | `true` |
| `action_confidence` | `0.90` |
| `referent_confidence` | `0.80` |
| `max_concurrent_requests` | `1` |

Model is **not** auto-downloaded. If Ollama/qwen3-vl is missing, ComputerAgent still runs on UIA.

## HUD (`hud.*`)

Desktop observer GUI (PySide6) — Jarvis-like status surface, **not** a chat app.
Default **off** — voice works without it. Crash of GUI must not kill the runtime.

| Key | Default | Notes |
|-----|---------|-------|
| `enabled` | `false` | Also `--hud` / `--no-hud` |
| `always_on_top` | `true` | |
| `compact` | `false` | Orb + status only |
| `mini` | `false` | Always-on-top orb; click expands full GUI |
| `hide_to_tray` | `true` | Window close hides; tray closes GUI without quitting Elian |
| `theme` | `dark` | Dark default (no big settings panel yet) |
| `opacity` | `0.94` | Panel opacity 0.5–1.0 |
| `animations` | `true` | Orb pulse; off = static |
| `dev_view` | `false` | Semantic family, tool, latency, session, planner phase |
| `proactive_enabled` | `true` | Situation cards on/off (tray toggle) |
| `world_update_ms` | `500` | Throttle WorldState → GUI updates |

Event bridge (`core/hud_events.py`): `state_changed`, `transcript_received`, `response_created`,
`goal_started`, `plan_updated`, `tool_started`/`finished`, `world_state_updated`,
`situation_created`, `proactive_message`, `memory_candidate_created`, `memory_saved`,
`integration_status_changed`, `error_occurred`. Widgets must not call ToolRegistry.

Cognitive Core / SituationEngine hooks: use `emit_goal_started`, `emit_world_state_updated`,
`emit_situation`, `emit_memory_*` when those modules land — smoke can simulate until then.

Optional: `python main.py --hud-smoke` (offscreen). Missing PySide6 → UNAVAILABLE, exit 0.
Visual / live hardware HUD = **NIE TESTOWANE** until manual RC with `--hud`.
Default stays `hud.enabled=false`.

## CLI diagnostics

```bash
python main.py --version
python main.py --doctor
python main.py --performance-diagnostics
python main.py --conversation-diagnostics
python main.py --hud-smoke     # optional PySide6 offscreen HUD
python main.py --hud           # enable HUD (default off)
python main.py --debug          # verbose logs + config warnings
```

## Computer control (`computer_control.*`)

| Key | Default | Notes |
|-----|---------|-------|
| `enabled` | `true` | Local computer tools |
| `computer_action_timeout` | `12` | Bounded timeout for type/hotkey/click (seconds, **4–30**) |
| `computer_observation_timeout` | `15` | Inspect / list / find (seconds, **6–30**). Wait tools use at least 18s. |

Store tokens in `Data/secrets.json` or environment variables (see `Config.get_secret`). Never commit secrets.
