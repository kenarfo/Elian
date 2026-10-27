# ELIAN TOTAL AUDIT â€” FAZA 1 (ANALIZA ONLY)

| Field | Value |
|------|--------|
| **Date** | 2026-10-05 |
| **Scope** | Full project `C:\Users\kenar\Elian` + comparison repos in `C:\Users\kenar\ElianResearch` |
| **Mode** | **READ-ONLY** â€” no production edits, no config/TTS/GPU/routing/self-improve changes |
| **Goal** | Inventory â†’ runtime map â†’ known-failure root causes â†’ V2 plan â†’ STOP for human approval |
| **Prior diagnostics reused** | `fish_audio_ab_diagnostic_20261004_200145.md`, `jarvis_parity/*`, crash dumps `runtime_crash_20261004T1948*.json`, `SELF_IMPROVEMENT.md`, `ARCHITECTURE.md`, `ELIAN_MASTER_AUDIT.md` |

---

## 1. Executive Summary

Elian is a capable Polish/English Windows voice assistant with a real ToolRegistry, HA, media, Fish S2 TTS, and a large offline test suite â€” but the **runtime is over-layered**. A single turn can pass through Semantic Executive â†’ Cognitive Core â†’ ActionLoop â†’ FastPath gate â†’ Agent/Planner â†’ legacy Skills/Commands/IntentRouter â†’ Brain, while **background self-development** can run concurrently with the mic loop.

Measured pain is not â€śone bad parameterâ€ť:

| Symptom | Root cause (verified) |
|---------|------------------------|
| ~30 s first reply | Unmarked agent/LLM + Ollama unload + Fish worker cold (~9â€“14 s) + `tts_generateâ‰0` instrumentation lie |
| Weather EN ignored | Polish-only `looks_weather` / shortlist â†’ `TALK` / `needs_tools=false` â†’ Brain chat that can *promise* tools |
| PaError `-9996`/`-9985` | Nameâ†’numeric index + dual host APIs (WDM-KS capture vs MME/DS barge) + AudioGate races |
| HA `ConnectTimeout` | Sync `requests` timeout=5s + `maybe_refresh()` on turn path |
| Self-improve thrash | Runtime starts background self-dev by default; 1747 cycles / 0 success / repeated `retry_policy` proposals |
| Fish < cloud quality | Backend + voice profile + PL text on EN Jarvis conditioning â€” not â€śjust Q4 vs Q8â€ť |

**Verdict:** Remodel should **delete and merge**, not add layers. Target: fewer classifiers, one decision owner, tools when promised, warm TTS, non-blocking HA, self-improve offline-only.

---

## 2. Full file inventory

### 2.1 Counts (repo, excluding `.git` / `__pycache__` / self-improve sandbox noise where noted)

| Type | Approx count | Notes |
|------|-------------:|-------|
| `.py` | **598** | ~249 `core/`, ~124 `tests/`, ~78 under `Data/`, many root `_tmp*`/`_patch*` |
| `.json` | **~3949** | Dominated by `Data/` (diagnostics, self_improve, memory, campaigns) |
| `.yml` / `.yaml` / `.toml` | **0** | Config is JSON |
| `.md` | **~75** | Architecture, gates, diagnostics |
| `.ps1` | **1** | Dev script |
| `.bat` / `.cmd` | **0** in Elian root (build scripts live in ElianResearch) |

### 2.2 Top-level directories

| Dir | Role | Runtime? |
|-----|------|----------|
| `core/` | Orchestration, agent, tools, self-improve, multi-agent | **Yes** |
| `voice/` | Pipeline, Fish worker, TTS engine, wake, audio gate | **Yes** |
| `memory/` | MemoryManager layers | **Yes** |
| `integrations/` | HA / calendar / media / reminders tools | **Yes** |
| `vision/` | Screen / OCR / UIA | Optional |
| `watchers/` | Proactive ticks | **Yes** (runtime thread) |
| `Skills/` | Legacy skill plugins | Legacy fallback |
| `tests/` | unittest | Gate only |
| `scripts/` | Dev / Fish / VRAM benches | Diag |
| `Data/` | config, secrets, diagnostics, self_improve state | Runtime data |
| `docs/` | Design docs | Docs |
| `gui/` / `ui/` | HUD / control surfaces | Optional |
| `prompts/` | Prompt text | Runtime |
| `voices/` / `Sounds/` | Voice assets / generated WAV | Runtime assets |
| `.cursor/` | Cursor rules/skills | Dev |
| Root `*.py` | Entry + **legacy monoliths** + **~40 junk probes** | Mixed |

### 2.3 Root Python â€” classification

**KEEP (runtime entry / still imported):**
`main.py`, `Brain.py`, `Config.py`, `Voice.py`, `WakeWord.py`, `HomeAssistant.py`, `HomeAssistantMqttBridge.py`, `DeviceResolver.py`, `AppLauncher.py`, `AppRegistry.py`, `Integrations.py`, `TextCorrector.py`, `Diagnostics.py`, `Memory.py` (legacy parallel), `ConversationContext.py`, `SkillsLoader.py`, `Commands.py`, `CommandParser.py`, `IntentRouter.py`, `GoalRouter.py`, `ComputerControl.py`, `HaActions.py`, `WebActions.py`, `WebResearch.py`, `Contacts.py`, `BluetoothProbe.py` / `VizziniBluetooth.py` (niche), `ConversationFlow.py`, `VoiceEngine.py`, `VoiceListener.py`, `WakeListener.py`, `HomeAssistantCheck.py`.

**DELETE candidates (FAZA 2 â€” not now):** all root `_tmp*`, `_qa*`, `_patch*`, `_probe*`, `_repro*`, `_write_*`, `_goal_oriented_audit_harness.py` (~40+ one-off scripts). They inflate inventory and confuse â€śwhat is product.â€ť

### 2.4 `core/` (249 `.py`) â€” by role

| Bucket | Examples | Verdict |
|--------|----------|---------|
| Orchestration | `conversation_manager`, `runtime`, `proactive*` | KEEP â€” but CM is too fat |
| Understanding | `semantic_executive`, `read_intents`, `utterance*`, `intent_family`, `asr_repair` | MERGE toward one Understanding layer |
| Guidance | `cognitive_core`, `salience`, `context_selection`, `world_state` | KEEP slim OR fold into Understanding |
| Decision/exec | `agent`, `planner`, `plan_cycle`, `fast_path`, `action_loop`, `soft_goals` | **OVERLAP** â€” collapse |
| Tools | `tools`, `builtin_tools`, `tool_shortlist`, `tool_result`, integrations | KEEP |
| Safety | `action_firewall`, `agent_safety`, `action_claim`, `speech_guard`, `risk` | KEEP (claim enforcement must be hard) |
| Voice-adj | `latency`, `ollama_lifecycle`, `response_*`, `jarvis_response` | KEEP + fix latency marks |
| Self-improve | `core/self_improve/**` | KEEP offline; **disable** background on mic |
| Multi-agent | `core/multi_agent/**` | REDUCE â€” high complexity / low mic ROI |
| Smokes / diag | `*_smoke.py`, `doctor`, `crash_log`, `rc_validation` | KEEP as gates |
| Computer/browser | `computer_*`, `browser_*` | KEEP behind tools |

### 2.5 Voice / audio / TTS

| File | Purpose | Status |
|------|---------|--------|
| `voice/pipeline.py` | Wake â†’ listen â†’ CM â†’ speak | KEEP (core) |
| `voice/tts_engine.py` | RoutedTTS Fishâ†’Piper | KEEP |
| `voice/fish_s2_backend.py` | Synth + exclusive GPU handoff | REFACTOR (lifecycle) |
| `voice/fish_s2_worker.py` | Persistent s2 `--server` | KEEP |
| `voice/audio_gate.py` | Serialize PortAudio InputStreams | KEEP |
| `voice/wake.py` | Acoustic / legacy wake | KEEP |
| `voice/settings.py` | Voice config dataclass | KEEP |
| `voice/speech_chunker.py` | Clause flush for stream TTS | KEEP |
| `Voice.py` | Capture, playback, device resolve | REFACTOR (device model) |

### 2.6 Generated / runtime data (do not treat as source)

`Data/diagnostics/*`, `Data/self_improve/{sandbox,checkpoints,cycles,proposals}*`, `Sounds/*.wav`, `Data/reminders.json`, `Data/elian_memory.json`, heartbeat JSON â€” **generated**. Plan: rotate/archive; never â€śaudit as product modules.â€ť

---

## 3. Runtime architecture (actual)

```text
main.py:start
  â”śâ”€ validate config / initialize_voice (STT+TTS+wake)
  â”śâ”€ HA MQTT + registry
  â”śâ”€ ConversationManager + VoicePipeline + ElianRuntime
  â””â”€ loop:
       proactive.try_deliver
       â†’ VoicePipeline.await_wake (OpenWakeWord | STT wake)
       â†’ optional residual STT
       â†’ ConversationManager.activate
       â†’ VoicePipeline.execute_turn
            â†’ listen_active â†’ Voice.record_until_silence (AudioGate)
            â†’ Voice.transcribe_audio (Whisper)
            â†’ echo filter
            â†’ ConversationManager.handle  â† DECISION STACK
            â†’ RoutedTTS.speak (Fish) / FallbackTTS (Piper)
            â†’ Voice.speak_from_wav / speak (playback + barge)
```

### 3.1 Decision stack inside `ConversationManager.handle` (real order)

1. Session control (wait / end / cancel / ACK / pending confirm)
2. Memory / teach / proactive prefs
3. `intent_family` prefilter
4. **`semantic_executive.decide_semantics` â†’ DecisionEnvelope** (optional LLM)
5. `utterance_class` + `active_goal`
6. **`cognitive_core.assess_and_apply`** (guidance / short-circuit; no tools)
7. Meta-ACK / claim checks / knowledge repair
8. **`action_loop.run_soft_action_loop`** (may invoke tools)
9. Clarification / device pronoun / climate shortcuts
10. **FastPath** only if permitted + obvious (â‰Ą0.90)
11. **`agent.run_agent` â†’ Planner â†’ ToolRegistry** (default)
12. Legacy: Skills â†’ Commands â†’ IntentRouter
13. **Brain.ask_ai** chat fallthrough
14. Return text â†’ pipeline TTS

This matches `AGENTS.md` / `ARCHITECTURE.md` more closely than the older Master Audit diagram (which still showed FastPath *or* Planner as peers without ActionLoop/Cognitive).

---

## 4. Dependency graph (simplified)

```text
main
 â”śâ”€ voice.pipeline â”€â”€ Voice / WakeWord / tts_engine / fish_s2_* / audio_gate
 â”śâ”€ core.conversation_manager
 â”‚    â”śâ”€ semantic_executive â”€â”€ model_client (Ollama)
 â”‚    â”śâ”€ cognitive_core
 â”‚    â”śâ”€ action_loop / soft_goals / user_goal
 â”‚    â”śâ”€ fast_path â”€â”€ tools.ToolRegistry
 â”‚    â”śâ”€ agent â”€â”€ planner â”€â”€ tool_shortlist â”€â”€ tools
 â”‚    â”śâ”€ memory.manager (+ Memory.py legacy)
 â”‚    â”śâ”€ Brain (chat)
 â”‚    â””â”€ legacy SkillsLoader / Commands / IntentRouter
 â”śâ”€ core.runtime â”€â”€ watchers / proactive / multi_agent / self_improve thread
 â””â”€ integrations.* â”€â”€ HomeAssistant / media / calendar / reminders
```

**Hard dependency risk:** Fish exclusive policy â†’ `ollama_lifecycle.unload` on the speak path while Planner just used Ollama on the same turn.

---

## 5. Audio architecture

| Concern | Current behavior | Risk |
|---------|------------------|------|
| Device persistence | **Names** in config (`Plantronics Blackwire`) â†’ resolved to **numeric PortAudio index** at init | Index invalid after host reorder â†’ `-9996` |
| Capture host preference | WDM-KS > MME > DirectSound > WASAPI | Good levels; fragile Blocking API |
| Barge host preference | MME/DirectSound; **skip WDM** | Second endpoint for same headset |
| Serialization | `AudioGate` one InputStream | Correct idea; races still produce `-9985` |
| Crash evidence | Many `runtime_crash_20261004T1948*.json`: `-9985` then `-9996`; barge `-9999` | Dual-path + reopen |

**Verdict:** Not two unrelated products â€” but **two host-API bindings** for one logical mic. Prefer single resolved `(name, hostAPI)` pair shared by capture+barge, or barge on same stream with non-blocking read.

---

## 6. TTS analysis

### 6.1 Path

`RoutedTTS` â†’ `FishAudioTTS.synthesize_to_file` â†’ `fish_s2_backend.synthesize_s2_wav` (unload Ollama if `exclusive`) â†’ persistent `s2.exe --server` `/generate` â†’ WAV @ 44100 â†’ `Voice.speak_from_wav` â†’ PortAudio play.

Fallback: Piper (`en_GB-alan-medium` when `language=en-US`).

### 6.2 Current local stack (verified)

| Item | Value |
|------|--------|
| Engine | s2.cpp / `s2.exe` |
| Model (prod config) | `s2-pro-q4_k_m.gguf` |
| Voice | `elian_jarvis.s2voice` |
| Sampler (hardcoded worker) | temp **0.8**, top_p **0.8**, top_k **30**, max_new_tokens **256** |
| speed / seed / trim | **not sent** by Elian |
| Streaming from Fish | Full WAV response (no true PCM stream into playback) |
| Cloud parity | Cloud uses different backend + voice id `7c1a7dc3â€¦` â€” **not 1:1** with local |

### 6.3 Quality findings (prior A/B â€” do not re-tune in FAZA 1)

- Cloud Fish sounds better than local â€” expected (BACKEND+MODEL+voice encode mismatch).
- Q8 vs Q4: Q8 costs more VRAM/init (~15 s vs ~9.5 s); **no proven quality win** without listening + fresh `.s2voice` encode on Q8.
- Polish text through English Jarvis conditioning sounds weak â€” **language/voice mismatch**, not only quant.
- Text normalization / prosody controls are thin; chunking exists for LLM stream TTS, not for Fish sentence streaming into ear.

### 6.4 Telemetry lie

`speak_from_wav` marks `tts_gen_start`/`tts_gen_end` back-to-back â†’ **`tts_generation_ms â‰ 0`** while real synth happened earlier. First-audio metrics under-attribute Fish.

---

## 7. STT analysis

| Item | Behavior |
|------|----------|
| Engine | Whisper via voice stack (`whisper` config) |
| Capture | `record_until_silence` + VAD thresholds from config |
| Repair | `asr_repair` whitelist; `TextCorrector` often off on listen |
| Language | Config `language=en-US` while many classifiers are **Polish-regex-first** |
| Wake | Acoustic OpenWakeWord preferred; STT wake legacy fallback |

**Gap:** Product claims bilingual; **intent surface is PL-heavy**. English utterances fall into `general_conversation` more often than they should.

---

## 8. LLM analysis

| Call site | Role |
|-----------|------|
| `planner` via `model_client` | Tool vs final_answer loop |
| `Brain.ask_ai` / stream | Chat fallthrough + TTS stream |
| `semantic_executive` LLM pass | Weak-confidence envelope |
| `TextCorrector` / `IntentRouter` | Optional / legacy |
| Vision provider | Screen describe |
| Self-improve coding model | Offline engineering |

**Problem:** Multiple LLM calls per turn possible (semantics + planner + brain) with **incomplete latency marks** (`route_*` / `planner_*` largely unused).

GPU: Ollama and Fish compete under `fish_gpu_policy=exclusive` â†’ unload waits (~6 s) + Fish cold (~9â€“14 s).

---

## 9. Tool routing analysis

### 9.1 Why weather EN failed

User: `Okay, so can you check the weather for me?`

Observed: `mode=TALK`, `intent=general_conversation`, `confidence=0.80`, `needs_tools=false`, shortlist none â€” then chat that can say it will check.

**Exact miss:**

1. `read_intents.looks_weather` â€” **Polish-only** regex (`jaka â€¦ pogoda`, etc.).
2. `semantic_executive` weather branch never fires â†’ fallthrough to TALK / `needs_tools=False`.
3. `tool_shortlist` weather keyword â€” **Polish-only** (`pogoda`).
4. Planner `talk_only` when `needs_tools` false â†’ no tools.
5. Brain may still **narrate** tool intent (`action_claim` exists but does not always stop promise language).

**Not** a missing weather tool â€” a **classification/language** failure plus **promiseâ‰ execute** gap.

### 9.2 Multiple decision owners

Semantic `needs_tools` Ă— Cognitive short-circuit Ă— ActionLoop soft tools Ă— FastPath Ă— Agent Ă— legacy Ă— Brain = **six+ owners**. Any layer can deny tools after another layerâ€™s language implies action.

**Simplification (proposed):** One Understanding output (`intent`, `needs_tools`, slots) â†’ one Executor (FastPath **or** Planner) â†’ ResponseComposer that **cannot claim tools without ToolResult**.

---

## 10. Memory analysis

| Layer | Location | Notes |
|-------|----------|-------|
| MemoryManager | `memory/manager.py` + layers | Canonical |
| Legacy `Memory.py` | Root | Parallel record/search â€” MERGE/retire |
| ActiveGoal / WorldState / Focus | `core/*` | Session cognition â€” keep but slim |
| Self-improve experience log | `core/self_improve` | Observation OK; writes must stay offline |
| PersonalJarvis-style wiki | N/A | Do **not** add to hot path |

---

## 11. Self-improve analysis

| Question | Answer |
|----------|--------|
| Who starts it? | `ElianRuntime` â†’ multi-agent orchestrator background thread **by default** (`background_self_development` true). CLI also. **Not** directly from `main.py`. |
| During mic? | **Yes.** Mic path does not call `note_interactive`; pause only on orchestrator text handle. |
| Can modify prod? | Classic adopt â†’ `learned/` additive. Autonomous engine can attempt code paths; **protected_surfaces** block GPU/Fish/Ollama/Voice. Historical `retry_policy` proposals show appetite for reliability modules. |
| Cycle limits | Background `max_cycles=0` â†’ **unlimited** outer loop; repair attempts=3; quarantine after 3 fingerprint fails. |
| Dedupe | Fingerprint + addressed gaps â€” **weak** for same module new proposal IDs. |
| Evidence | State: **1747** cycles, **0** success, **9** rollbacks, **1735** NO_PROGRESS; many `retry_policy` / `structured_result` proposals; `autonomous.enabled=false` in config **does not stop** runtime thread start. |

**CEL violated today:** self-improve can compete with mic/GPU/tests without explicit human â€śengineering mode.â€ť

**Required FAZA 2 policy (design only):** default **OFF** in runtime; CLI/supervised only; hard stop on repeated rejected module fingerprint; never touch hot path modules without human approve.

---

## 12. Error handling

| Area | Behavior | Assessment |
|------|----------|------------|
| Mic open fail | Retry default device | Partial â€” still crashes in dumps |
| HA timeout | 5s; keep previous entities | Good keep_previous; bad sync block |
| Fish fail | Piper fallback | Good |
| Planner fail | Repair / clarify / Brain | Can over-talk |
| Self-improve fail | Rollback + quarantine | Exists but loops still burn cycles |
| Crash | `crash_log` snapshots | Good diagnostics |

---

## 13. Latency analysis (~30 s first vs ~5 s second)

### 13.1 Do not trust `tts_generate` alone

Tracker pairs include `tts_generation_ms` and `total_response_ms`, but Fish cost is **outside** `tts_gen_*` marks. `route_*` / `planner_*` / `tool_*` rarely stamped â†’ **agent time invisible**.

### 13.2 Plausible reconstruction (cold first turn)

| Phase | Order of magnitude | Marked? |
|-------|--------------------|---------|
| Capture | ~1.5â€“3 s | Yes |
| STT | ~0.3 s | Yes |
| Semantic + Agent/LLM | **several secondsâ€“tens** | **Mostly no** |
| Ollama unload (exclusive) | ~3â€“6 s | No (inside Fish backend) |
| Fish worker cold | ~9â€“14 s (diag 9609 / 14369 wall) | No as `tts_generate` |
| Fish generate warm-ish | ~3.5â€“8 s | Hidden |
| Playback first audio | small | Partial |

Second turn: warm worker + no unload â†’ total ~5 s matches â€śFish already up / Ollama already out.â€ť

**Verdict:** ~30 s is real wall time; telemetry **misattributes** it. Fix instrumentation first, then lifecycle (warm Fish, smarter exclusivity).

---

## 14. GPU / resource lifecycle

```text
Turn N: Ollama loaded for Planner
Speak: exclusive â†’ unload Ollama (wait) â†’ ensure Fish worker â†’ generate â†’ play
Turn N+1 listen: may reload Ollama for next Planner
Self-dev thread: may also want models/CPU
```

Compared to **Jarvis (research):** explicit Sleep mode with `keep_alive=0` and unload STT/TTS â€” clearer than Elianâ€™s per-speak exclusive handoff.

ElianResearch `s2.cpp` benches: spawn-per-request ~10 s vs persistent server â€” Elian already prefers persistent worker; cold still hurts after unload/restart.

---

## 15. Comparison with external Jarvis repositories

Repos under `C:\Users\kenar\ElianResearch`: `Jarvis/`, `PersonalJarvis/`, `local-voice-ai/`, `s2.cpp/`, models/voices, CUDA tooling.

| Dimension | **Elian** | **Jarvis** | **PersonalJarvis** | **local-voice-ai** | **s2.cpp** |
|-----------|-----------|------------|--------------------|--------------------|------------|
| Style | Voice OS, many layers | Tray + SM + hybrid router | Ecosystem FastAPI/React | LiveKit demo | TTS engine only |
| Simplicity | Low | **Higher** | Lowest | Highest (demo) | N/A |
| Reliability story | Rich tests; complex races | Spec + ModeĂ—State | Huge surface risk | Thin | Component |
| Latency strategy | Exclusive GPU, partial marks | Sleep/VRAM eviction | Ack-Brain / workers off-path | Dual VAD, 1-chunk TTS prefetch | Persistent server |
| Voice quality path | Fish S2 + Piper | Piper EN | Multi TTS cloud | Piper pipeline | Fish S2 |
| Tools | ToolRegistry + HA/media | PC + MCP | Risk tiers + missions | math only | â€” |
| Memory | MemoryManager | Notes files | Wiki/awareness | Session | â€” |
| Barge-in | AudioGate + barge device | Silero during SPEAKING | Echo-aware docs | Dual VAD ~0.2s | â€” |
| Local-first | Yes | Yes | Cloud-leaning | Yes | Yes |

### What is actually better

| Borrow | From | Why |
|--------|------|-----|
| Mode Ă— ConversationalState clarity | Jarvis | Debugability |
| Sleep / VRAM eviction policy | Jarvis | Predictable GPU |
| Dual VAD (endpoint â‰  barge) + 1-ahead TTS | local-voice-ai | Latency + barge |
| Persistent s2 server (already) + streaming knobs | s2.cpp | TTFB |
| scrub_for_voice; awareness off hot path | PersonalJarvis (ideas) | Clean speech |
| **Avoid** FastAPI/React/missions/marketplace | PersonalJarvis | Complexity |
| **Avoid** English-only Piper as primary | Jarvis | PL quality |

**Elian better than peers at:** HA depth, Polish soft goals, ToolRegistry breadth, offline agent-smoke gates.
**Elian worse at:** simplicity, latency honesty, language-agnostic routing, self-improve isolation.

---

## 16. Dead / duplicate / legacy code

| Item | Assessment |
|------|------------|
| Root `_tmp*` / `_patch*` / `_qa*` | **Dead clutter** â†’ DELETE in FAZA 2 |
| Skills / Commands / IntentRouter | **Legacy live fallback** â†’ metrics then retire |
| `Memory.py` vs `memory/` | Duplicate â†’ MERGE to MemoryManager |
| Cognitive + Semantic + ActionLoop + Agent | Overlapping decision | MERGE |
| Multi-agent + self-dev default ON | Overbuilt for mic product | GATE / reduce |
| Multiple `*_smoke.py` in core | OK as gates; donâ€™t grow product logic there |
| Master Audit â€śbrainsâ€ť naming | Aspirational map â‰  runtime | Treat as backlog, not architecture |

---

## 17. Security / reliability risks

1. Background self-dev mutating/testing while mic runs (CPU, flaky adopts, user interruption required historically).
2. Sync HA on voice thread (timeout stalls).
3. PortAudio crashes (hard process fail).
4. Exclusive GPU thrash under concurrent Ollama/Fish/self-dev.
5. Secrets in `Data/secrets.json` â€” ensure never committed; audit does not open values here.
6. Agent speech promising actions without tools (trust breakage).
7. Unlimited self-dev outer cycles (`max_cycles=0`).

---

## 18. Proposed ELIAN V2 architecture

```text
Audio          â†’ Understanding â†’ Decision â†’ Tools â†’ Response â†’ Voice
(capture/STT)    (one envelope)  (one owner) (registry) (compose) (TTS/play)
```

| Layer | Responsibility | In | Out | Modules (target) | Deps | Errors | Timeout | Lifecycle |
|-------|----------------|----|-----|------------------|------|--------|---------|-----------|
| **Audio** | Wake, capture, VAD, barge, device | PCM | transcript + audio events | `voice/pipeline`, `Voice`, `audio_gate`, wake | PortAudio, Whisper | device fail â†’ recover/name re-resolve | capture/VAD caps | start/stop streams; one host binding |
| **Understanding** | Language-agnostic intent + slots + needs_tools | transcript + session | `TurnUnderstanding` | merge semantic + read_intents + utterance_class (+ optional light LLM) | Ollama optional | low conf â†’ clarify | â‰¤1 LLM | stateless per turn |
| **Decision** | Choose FastPath vs Planner vs Talk; **no second classifier** | understanding + caps | `TurnDecision` | slim CM + FastPath gate + Agent | caps registry | deny â†’ honest speech | planner budget | one owner |
| **Tools** | Execute + verify | decision | `ToolResult[]` | ToolRegistry, integrations | HA/media/â€¦ | fail â†’ structured error | per-tool; HA async | background HA refresh |
| **Response** | Compose user text; **forbid unearned claims** | results + mode | speakable text | jarvis_response, speech_guard, action_claim | â€” | scrub junk | â€” | â€” |
| **Voice** | TTS + play + barge | text | audio out | RoutedTTS, fish worker, Piper | GPU policy | Fish fail â†’ Piper | synth budget | warm worker; Sleep/idle unload |

**Removed from hot path:** Cognitive-as-second-brain, ActionLoop soft parallel, legacy routers (metrics-only quarantine), multi-agent spawn, self-improve.

**Cognitive remnant:** optional async â€śremember/forgetâ€ť hints **after** speak, never blocking tools.

---

## 19. File-by-file migration plan (sample; FAZA 2+)

| Current file | Status | Reason | Target |
|--------------|--------|--------|--------|
| `core/conversation_manager.py` | REFACTOR | God-object turn brain | Thin orchestrator of V2 layers |
| `core/semantic_executive.py` | MERGE | Primary understanding | `understanding/` |
| `core/cognitive_core.py` | MERGE/MOVE | Guidance only | Async post-turn or delete from hot path |
| `core/action_loop.py` | MERGE | Soft tools overlap Planner | Decision shortcuts table |
| `core/fast_path.py` | KEEP | Deterministic tools | Decision FastPath |
| `core/agent.py` / `planner.py` | KEEP | Default executor | Decision Planner |
| `core/tool_shortlist.py` | REFACTOR | PL-only keywords | Lang-agnostic + caps |
| `core/read_intents.py` | REFACTOR | PL-only weather etc. | EN+PL (+ locale) |
| `core/runtime.py` | REFACTOR | Starts self-dev default ON | Default OFF; engineering mode |
| `core/self_improve/**` | KEEP | Valuable offline | CLI/supervised only |
| `core/multi_agent/**` | REDUCE | Complexity | Optional experiment package |
| `voice/fish_s2_*.py` | REFACTOR | Lifecycle + marks | Warm + honest latency |
| `Voice.py` | REFACTOR | Dual host devices | Single device binding |
| `HomeAssistant.py` | REFACTOR | Sync block | Async/non-blocking registry |
| `Brain.py` | KEEP | Chat only | Talk mode only |
| `Skills/*` / `Commands.py` / `IntentRouter.py` | MOVE | Legacy | `legacy/` then DELETE after metrics=0 |
| `Memory.py` | MERGE | Duplicate | `memory/` |
| Root `_tmp*` `_patch*` `_qa*` | DELETE | Clutter | trash / archive |
| `Data/diagnostics/ELIAN_TOTAL_AUDIT.md` | KEEP | This report | diagnostics |
| `gui/` `ui/` `control_center` | KEEP optional | Not mic-critical | side packages |

Full 598-file table is unnecessary for approval; FAZA 2 should generate machine inventory CSV from this taxonomy.

---

## 20. Test strategy (post-remont gates)

| Critical path | Gate type |
|---------------|-----------|
| Microphone open / name resolve | unit + hardware optional |
| STT PL / EN | offline fixtures + optional live |
| Interruption / barge-in | `test_software_barge_in` expand |
| TTS Fish worker warm/cold | existing fish tests + latency marks assert |
| Piper fallback | unit |
| Ollama lifecycle exclusivity | scripted handoff (already in scripts) |
| Weather EN+PL â†’ `get_weather` | **new regression** (current miss) |
| HA timeout non-blocking | `test_ha_registry_refresh` strengthen |
| Spotify / media | existing agent-smoke |
| Tools honesty / action_claim | unit: no â€śIâ€™ll checkâ€ť without tool |
| Memory prefs | existing |
| Multi-turn | conversation scenarios |
| Shutdown / crash recovery | phase14 stability |
| **Self-improve isolation** | assert mic runtime does **not** start write loops |

Keep: `unittest discover`, `python main.py --agent-smoke` before any mic RC.

---

## 21. Migration phases (FAZA 2+ â€” not started)

| Phase | Focus | Exit |
|-------|-------|------|
| **2.0 Freeze** | Disable background self-dev default; archive root junk (after approve) | Mic path quiet |
| **2.1 Observe** | Fix latency marks (Fish + planner) | Honest totals |
| **2.2 Routing** | EN+PL weather/tools; hard action_claim | Weather EN PASS |
| **2.3 Audio** | Single device binding; barge on shared policy | No -9985 storm |
| **2.4 HA** | Non-blocking refresh | Turn never waits 5s sync |
| **2.5 Collapse** | Merge Understanding/Decision; quarantine legacy | One decision owner |
| **2.6 TTS** | Warm Fish; lifecycle Sleep; optional voice encode experiments | TTFB predictable |
| **2.7 Delete** | Legacy routers + dead root scripts when metrics=0 | Smaller tree |

---

## 22. Risks of remodel

| Risk | Mitigation |
|------|------------|
| Regress Polish soft goals while collapsing ActionLoop | Port soft cases as FastPath table + tests first |
| Breaking Fish exclusive â†’ VRAM OOM | Keep policy flag; change default only after bench |
| Deleting legacy too early | Metrics counters must hit zero |
| Over-scoping multi-agent removal | Feature-flag first |
| Touching wake onnx / memory_preference | Explicitly out of scope unless named |

---

## 23. Recommended order of implementation

1. **Stop the bleeding:** self-improve off mic; latency instrumentation; weather EN+claim honesty.
2. **Stabilize I/O:** audio device model; HA non-blocking.
3. **Simplify brain:** one Understanding + one Decision.
4. **TTS quality/latency:** warm server + lifecycle Sleep; only then voice/encode experiments.
5. **Delete legacy/junk** when covered by tests.

---

# STOP â€” FAZA 1 COMPLETE

No production remodel performed. Awaiting human acceptance of TOP 10 lists before FAZA 2.

---

## Appendix A â€” Inventory snapshot commands

Counts generated 2026-10-05 from workspace scan (py/json/md; core/tests/voice/memory/integrations layout). ElianResearch used for comparative architecture only.

## Appendix B â€” Prior diagnostic anchors

- `Data/diagnostics/fish_audio_ab_diagnostic_20261004_200145.md`
- `Data/diagnostics/jarvis_parity/q4_vs_q8_ab_20261005_151514.md`
- `Data/diagnostics/jarvis_parity/jarvis_voice_parity_20261005_155829.md`
- Crash dumps `Data/diagnostics/runtime_crash_20261004T1948*.json`
- `Data/self_improve/self_develop_state.json` (cycle stats)
