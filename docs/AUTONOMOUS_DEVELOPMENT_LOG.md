# Autonomous Development Log — Elian 2.0.0-rc1

Operator: continuous autonomous agent
Workspace: `c:\Users\kenar\Elian`
North star: Jarvis-like understand → plan → execute → verify → repair → reply

---

## Session baseline (2026-09-12)

### Gates before work
| Gate | Result |
|------|--------|
| unittest discover | **OK** (prior ~1397; this session re-run OK) |
| agent-smoke | **11/11 PASS** |
| jarvis-behavioral --critical | **38/38 PASS** |

### Human / env inventory
| Item | Status | Mitigation |
|------|--------|------------|
| Mic / spoken RC | HARDWARE_UNVERIFIED | L1 text inject |
| Acoustic barge-in | HARDWARE_UNVERIFIED | `--barge-in` / `/barge` software event |
| HA token | CREDENTIAL_UNAVAILABLE | fake `light.vizzini` in text harness |
| Spotify / user apps | protected | `ELIAN_PROTECT_USER_APPS=1` |
| 30–60m soak | HARDWARE_UNVERIFIED | skip; keep software cycles |

---

## Cycle 1 — Official text harness unlock

| Field | Notes |
|-------|-------|
| **Problem** | No official post-STT text path for autonomous RC (`--inject-text` / REPL / script) |
| **Evidence** | CLI flags absent in `main.py`; mic RC BLOCKED without human |
| **Root cause** | Gap in operator tooling, not ConversationManager |
| **Change** | `core/text_inject.py` + `main.py` flags; dry tools + app protect; optional barge inject; fake HA when no token |
| **Tests before** | n/a |
| **Tests after** | `tests.test_summarize_web_and_wake_ack.TextInjectCliSmoke` PASS; script smoke PASS |
| **Novel** | multi-turn script `Data/diagnostics/scripts/p3_stresc_followup.json` |
| **Architecture** | Same `ConversationManager.handle` as voice after STT; no parallel Elian |
| **KEEP/REVERT** | **KEEP** |
| **Risks** | Dry FakeModelClient weak for live Ollama paths; use `--live-tools` carefully |

Commands:
```bash
python main.py --inject-text "..."
python main.py --text-input
python main.py --conversation-script path.json
```

---

## Cycle 2 — „streść to krótko” re-search / weak grounding

| Field | Notes |
|-------|-------|
| **Problem** | After web search, „streść to krótko” re-ran search / echoed tool ACK / WHO-fallback |
| **Evidence** | `docs/P3_LIVE_JARVIS_RC.md` OPEN; text script before fix |
| **Root cause** | No soft continuation for summarize-from-evidence; soft web spoke raw tool status; `synthesize_from_evidence` WHO-fallback for WHAT queries |
| **Change** | `soft_goals.summarize_web`; `action_loop` summarize + soft web synthesis; `web_synthesis` title brief for non-WHO; reject search ACK as „answer” |
| **Tests before** | FAIL/absent |
| **Tests after** | `tests.test_summarize_web_and_wake_ack` PASS; phase11 synthesis OK; script: soft_kind=summarize_web, **no second web_search** |
| **Novel paraphrases** | streść to / podsumuj to / skróć to / streść wyniki / podsumuj to jednym zdaniem → `summarize_web` |
| **Architecture** | Soft shortcut on GoalContext evidence; Planner still owns fresh search |
| **KEEP/REVERT** | **KEEP** |
| **Risks** | Title-only briefs when snippets thin; not false device SUCCESS |

---

## Cycle 3 — In-session wake ACK

| Field | Notes |
|-------|-------|
| **Problem** | Bare „Hej Elian” in active session → silent `ambient_continue` (live RC FAIL) |
| **Evidence** | `Data/diagnostics/p3_live_rc_notes.md` T2–T6; unittest previously expected silence |
| **Root cause** | Anti-re-greet policy suppressed all ACK; Jarvis needs soft listening ACK |
| **Change** | `ConversationManager`: bare wake → speak `listening_ack` (`wake_ack`) when config allows |
| **Tests before** | ambient test expected silent |
| **Tests after** | ambient + wake_ack tests PASS |
| **Novel** | `hej elian` mid-script → `Słucham.` |
| **Architecture** | Control path only; no new intent family |
| **KEEP/REVERT** | **KEEP** |
| **Risks** | Slightly more TTS on accidental wake re-say (desired) |

---

## Cycle 4 — Search query wrapper cleanup

| Field | Notes |
|-------|-------|
| **Problem** | Soft web summaries said „o informacje o piper tts” |
| **Evidence** | text inject RESULT after Cycle 2 |
| **Root cause** | `clean_web_query` stripped meta ACK noise but not Polish search verb shells |
| **Change** | `_SEARCH_WRAPPER` in `web_grounding.clean_web_query`; soft search slots + soft invoke call cleaner |
| **Tests before/after** | phase11 grounding/meta + summarize suite **OK**; script topic now `piper tts` |
| **Novel** | `Wyszukaj informacje o Piper TTS` → query `Piper TTS` |
| **KEEP/REVERT** | **KEEP** |
| **Risks** | Over-strip rare queries that literally start with „informacje o” without a search verb (acceptable) |

---

## Gates after Cycles 1–3
| Gate | Result |
|------|--------|
| focused unittest (summarize/wake/ambient/synthesis) | **OK** |
| full unittest discover | **OK** (exit 0) |
| agent-smoke | **11/11 PASS** |
| jarvis-behavioral --critical | **38/38 PASS** |

---

## Gates after Cycle 4
| Gate | Result |
|------|--------|
| phase11 + summarize focused | **35 OK** |
| conversation-script streść | PASS (no re-search; topic cleaned) |

---

## Session baseline (2026-09-15) — continuous autonomous directive

### Gates at session start
| Gate | Result |
|------|--------|
| unittest discover | **1403 OK** |
| agent-smoke | **11/11 PASS** (~35s; live HA timeouts on light path) |
| jarvis-behavioral --critical | **38/38 FULL PASS** |
| P3 Live | HARDWARE_UNVERIFIED (no mic operator) |

---

## Cycle 5 — Stateful Fake HA + dry schema parity (text harness)

| Field | Notes |
|-------|-------|
| **Problem** | `--inject-text "ciemno tu"` → `Niedozwolone argumenty: action, domain, target` — soft brightness blocked in dry harness |
| **Evidence** | L1 inject before fix; production `set_device_value` schema includes action/domain/target/value |
| **Root cause** | `text_inject._build_dry_registry` HA tools only allowed `entity_id`; no stateful world |
| **Change** | `core/fake_ha_backend.py` (stateful light.vizzini); wire into `text_inject`; production-matching schemas; richer RESULT trace; script `home_brightness_followup.json` |
| **Tests before** | FAIL (schema reject) |
| **Tests after** | `tests.test_text_inject_home_script` PASS — ciemno→jaśniej→nie aż tak→zgaś ją |
| **Novel** | multi-turn home script via `--conversation-script` |
| **Architecture** | Same CM.handle; CREDENTIAL_UNAVAILABLE continues with Fake HA (not HARDWARE PASS) |
| **KEEP/REVERT** | **KEEP** |
| **Risks** | Slim backend (no climate); live HA still HARDWARE_UNVERIFIED |

---

## Cycle 6 — HA registry fail-closed + smoke fixture (false-empty / latency)

| Field | Notes |
|-------|-------|
| **Problem** | Live HA timeout during dry smoke: `refresh()` wiped registry to 0 entities; ~10s stalls; soft speech “nie mogę potwierdzić” despite dry OK |
| **Evidence** | agent-smoke ConnectTimeout + `ha_registry: entities=0`; smoke ~35s |
| **Root cause** | Failed `/api/states` returned `[]` treated as success empty; fixtures overwritten; smoke never seeded fixture |
| **Change** | `HomeAssistant.refresh`: transport failure → keep previous + fixture_mode; fixture skips network unless `ELIAN_HA_LIVE_REFRESH=1`; agent-smoke seeds Fake HA; dry `set_device_value` carries `ha_verify=SUCCESS` |
| **Tests before/after** | `tests.test_ha_registry_refresh` PASS; agent-smoke **11/11 in ~3.9s** (was ~35s); clarification `Rozjaśniłem.` |
| **Architecture** | Fail-closed registry; no secrets touched |
| **KEEP/REVERT** | **KEEP** |
| **Risks** | Legitimate empty HA house still possible on successful `[]` response |

---

## Cycle 7 — Software barge-in state tests + memory replace retry

| Field | Notes |
|-------|-------|
| **Problem** | Parallel explore+unittest hit `PermissionError` on `memory.json` replace (WinError 5); barge-in lacked dedicated software SM coverage |
| **Change** | `Memory._write_store` retry on WinError 5/32; `tests/test_software_barge_in.py` |
| **Tests after** | barge-in 4 OK; full suite **1412 OK** (after retry) |
| **KEEP/REVERT** | **KEEP** |

---

## Cycle 8 — „nie, niższy” ordinal + poison quote FALSE_ACT

| Field | Notes |
|-------|-------|
| **Problem** | Explore seed 20260915: 23 FAIL — `nie, niższy` after open first → agent TALK; meta/roleplay/quote wrappers → soft/fast ACT (POISON_ACT) |
| **Evidence** | `explore_latest.json` failures; FALSE_ACT=19, POISON_ACT=15 |
| **Root cause** | `_OPEN_FIRST` treated `dolny` as first; no `niższy` ordinal; `looks_quoted_command` missed novel meta wrappers; soft_goals ignored quoted |
| **Change** | soft_goals ordinal (niższy/dolny→index 1); remove dolny from FIRST; expand `looks_quoted_command`; soft early-exit on quoted |
| **Tests** | `tests.test_lower_ordinal_and_poison` PASS; FastPath NOT_HANDLED on poison samples |
| **KEEP/REVERT** | **KEEP** |
| **Risks** | Quote detector is structural meta-markers, not a giant synonym map; still may miss novel phrasing |

---

## Explore re-run after Cycle 8 (seed 20260915b)
| Metric | Before (20260915) | After Cycle 8 |
|--------|-------------------|--------|
| novel overall | 96.167% | **99.5%** |
| verdict | PASS_GENERALIZATION | **PASS_GENERALIZATION** |
| residual FAILs | 23 | **3 BAD_REPAIR** (open_app retry) |

## Cycle 9 — soft retry „spróbuj ponownie” after open_app

| Field | Notes |
|-------|-------|
| **Problem** | 3 BAD_REPAIR — `spróbuj ponownie` po `open_app` bez tooli |
| **Change** | `_REPLAY_LAST` + soft retry z `previous_tool` / active app |
| **Tests** | `tests.test_soft_retry_open_app` PASS |
| **KEEP/REVERT** | **KEEP** |

## Explore after Cycle 9 (seed 20260915c)
| Metric | Result |
|--------|--------|
| novel overall | **100.0%** |
| verdict | **PASS_GENERALIZATION** |
| residual FAILs | **0** |

First explore attempt in this session (Tee-buffered) was force-killed as hung — superseded by unbuffered runs.

---

## Cycle 10 — L2 simulated voice harness (Piper → Whisper → CM)

| Field | Notes |
|-------|-------|
| **Problem** | No autonomous Level-2 voice path; mic RC blocked without human |
| **Change** | `core/simulated_voice.py` + `main.py --simulated-voice` / `--sim-voice-script`; dry mode `ELIAN_SIM_VOICE_DRY=1` |
| **Label** | **SIMULATED_VOICE_PASS** (never HARDWARE PASS) |
| **Tests** | `tests.test_simulated_voice` PASS (dry + Piper synth with mocked STT) |
| **Live note** | Piper→Whisper heard `czemnotu` for „ciemno tu” → added STT known correction |
| **KEEP/REVERT** | **KEEP** |

---

## Cycle 11 — STT returns corrected text (L2 live)

| Field | Notes |
|-------|-------|
| **Problem** | `apply_known_corrections` ran but `transcribe_audio` returned `envelope.raw_transcript`, discarding fixes (`Ciemnotu.` stayed raw) |
| **Evidence** | Live L2: Whisper `Ciemnotu.` / print PO KOREKCIE `Ciemno tu.` / CM still got raw until fix |
| **Root cause** | C.5.1 envelope discarded `corrected`; return path used raw only |
| **Change** | `envelope_from_whisper` stores corrected in normalized; `transcribe_audio` returns corrected (not `.lower()`); tests |
| **Live after** | `stt='Ciemno tu.'` → soft `brightness_up` → `Rozjaśniłem.` **SIMULATED_VOICE_PASS** |
| **KEEP/REVERT** | **KEEP** |

---

## Cycle 12 — Web brief quality + false DEBUG_LEAK on „narzędzie”

| Field | Notes |
|-------|-------|
| **Problem** | Soft `web_search` spoke vacuous title briefs (`Wynik o: …; Drugi wynik`); natural Polish „narzędzie opisane…” was nuked by `_DEBUG_LEAK` → false „Nie udało mi się tego powiedzieć jasno.” |
| **Evidence** | Inject `poszukaj piper tts` → title-only; after snippet briefs, response wiped by polish_spoken |
| **Root cause** | (1) `synthesize_from_evidence` / soft path used titles only; dry fixtures were placeholder titles; (2) `_DEBUG_LEAK` matched `narzędzie\s+\w+` (any noun), not only tool ids |
| **Change** | `evidence_briefs` (prefer snippets over generic/thin titles); richer dry web fixtures; `_DEBUG_LEAK` only snake_case / known tool prefixes; regressions |
| **Tests before** | 1423 OK (prior cycle) |
| **Tests after** | unittest **1425 OK**; agent-smoke **11/11 PASS**; inject soft web returns snippet brief with „narzędzie” intact |
| **Novel** | targeted: generic-title → snippet; polish_spoken natural „narzędzie” kept |
| **KEEP/REVERT** | **KEEP** |
| **Architecture** | Consolidation of web brief paths via shared `evidence_briefs`; no new agent layer |

---

## Cycle 13 — „czym jest X” without Ollama → soft web

| Field | Notes |
|-------|-------|
| **Problem** | Definitional knowledge (`czym jest piper tts`) → ANSWER/knowledge_explanation → Ollama fail → „Lokalny model chwilowo nie odpowiada.” |
| **Evidence** | Inject before: soft_kind empty, chat fail; after Cycle 12 web briefs existed but path never reached them |
| **Root cause** | Soft `talk_locked` + action_loop `_MUTATING_SOFT` suppressed web under ANSWER; no soft define pattern |
| **Change** | Soft `_DEFINE_TOPIC` (+ talk-lock escape); action_loop allows `soft_define*` web_search under ANSWER; semantic stays ANSWER (behavioral corpus) |
| **Rejected approach** | Semantic ACT/web for all defines — broke 185 behavioral eval cases (`jak działa` / `czym jest AI?`) |
| **Tests after** | unittest green; smoke **11/11**; critical **38/38**; `tests.test_define_fact_web_soft` |
| **Inject** | `czym jest piper tts` → soft web_search → snippet brief (no Ollama) |
| **KEEP/REVERT** | **KEEP** |

---

## Biggest remaining gaps
1. Richer L2 voice fixtures (noise / fast / quiet) — P2
2. Multi-agent delegation for compound goals — P2/P3 design
3. Mic / acoustic barge / soak — HARDWARE_UNVERIFIED

---

## Cycle 14 — L2 voice fixture lab profiles

| Field | Notes |
|-------|-------|
| **Problem** | L2 only had clean dry echo / Piper path — no automated degradations (fast ASR, no diacritics, noise, truncate) |
| **Change** | `apply_stt_fixture` / `degrade_audio`; `--sim-voice-fixture`; per-turn fixture in scripts |
| **Profiles** | clean, quiet, fast, noise, truncated, repeat, partial, no_diacritics, delayed |
| **Tests** | `tests.test_simulated_voice` — dry fixtures + fast→`ciemnotu`→brightness soft **SIMULATED_VOICE_PASS** |
| **KEEP/REVERT** | **KEEP** |
| **Note** | Still not HARDWARE PASS |

---

## Gates snapshot (end Cycle 14 prep)
| Gate | Result |
|------|--------|
| unittest | **1430 OK** (Cycle 13) + sim-voice expansions |
| agent-smoke | **11/11 PASS** |
| critical behavioral | **38/38 FULL PASS** |
| HARDWARE mic/HA/barge | **HARDWARE_UNVERIFIED** |

---

## Cycle 15 — media calm follow-up under TALK lock

| Field | Notes |
|-------|-------|
| **Problem** | Explore PARTIAL: after `puść X`, `coś łagodniejszego` → semantic TALK → no soft → dead chat |
| **Root cause** | Soft talk_lock returned before `_CALMER_MEDIA`; action_loop suppressed `media_calm` under TALK |
| **Change** | talk-lock escape `soft_media_mood_talk`; action_loop allow `soft_media_mood*` |
| **Tests** | `tests.test_media_calm_talk_cont`; inject multi-turn → soft_kind=media_calm |
| **Explore** | seed run: hard FAIL=0, PARTIAL still present elsewhere; critical counters clean |
| **KEEP/REVERT** | **KEEP** |

---

## Batch gates (Cycles 12–15)
| Gate | Result |
|------|--------|
| unittest | **1432+ OK** |
| agent-smoke | **11/11 PASS** |
| critical | **38/38 FULL PASS** |
| HARDWARE | **UNVERIFIED** |

---

## Cycle 16 — colloquial „gaś to” (not only „zgaś”)

| Field | Notes |
|-------|-------|
| **Problem** | Explore/inject: `gaś to` → TALK/REFERENCE → vacuous chat; `zgaś to` worked |
| **Evidence** | After light on: `gaś to` → „Jasne, słucham.” / web nonsense; novel corpus expects ACT for `gaś to w kuchni` |
| **Root cause** | Action/OFF patterns required `zgaś` stem; utterance class treated short `… to` as REFERENCE |
| **Change** | `z?ga[sś]` in device_pronoun, soft `_LIGHT_OFF`, utterance `_ACTION`, semantic `_ACTION_VERB` / light_cmd; soft talk-lock escape + entity bind |
| **Tests** | `tests.test_gas_to_light_off` — soft + semantic ACT + inject turn_off |
| **KEEP/REVERT** | **KEEP** |

---

## Cycle 17 — „wygaś” / „odetnij prąd” light_off paraphrases

| Field | Notes |
|-------|-------|
| **Problem** | `wygaś to` → TALK chat; `odetnij prąd od lampy` → agent `web_search` placeholder |
| **Change** | Extend OFF/ACTION/`HA_EXPLICIT`/`_LIGHT_OFF` with `wygaś` + `odetnij prąd` |
| **Tests** | `test_wygas_and_odetnij_prad`; unittest **1439 OK**; smoke **11/11**; critical **38/38** |
| **KEEP/REVERT** | **KEEP** |

## Remaining backlog
1. Explore PARTIAL soft gaps (further novel seeds)
2. Planner/FakeModel must not web_search on SMART_HOME when soft misses
3. Mic / physical HA — **HARDWARE_UNVERIFIED**

---

## Cycle 18 — SMART_HOME must not invent web_search

| Field | Notes |
|-------|-------|
| **Problem** | When soft missed HA paraphrase, dry FakeModel scripted `web_search(query=placeholder)` → false web answer |
| **Root cause** | (1) text_inject used cycling FakeModelClient; (2) tool_compat allowed web under SMART_HOME |
| **Change** | Family firewall in `tool_compat` (repair→HA tools); text_inject uses `AssistFakeModel` |
| **Tests** | `tests.test_smart_home_no_web_search`; full suite + smoke + critical |
| **KEEP/REVERT** | **KEEP** |

---

## Cycle 19 — compound APP+HA + odetnij zasilanie

| Field | Notes |
|-------|-------|
| **Problem** | `zamknij chrome i zgaś lampę` → false clarify; `odetnij zasilanie lampy` → vacuous agent |
| **Root cause** | Compound `_CLAUSE_VERB` missed `zgaś`; power-off dual-noun CLARIFY; soft only `odetnij prąd` |
| **Change** | Clause verbs include `z?gaś`/`wygaś`; compound skip-clarify; soft `odetnij zasilanie` |
| **Tests** | `tests.test_compound_app_ha`; unittest **1445 OK**; smoke **11/11**; critical **38/38** |
| **KEEP/REVERT** | **KEEP** |

---

## Cycle 20 — „ściemnij totalnie” → light_off

| Field | Notes |
|-------|-------|
| **Problem** | Novel corpus treats `ściemnij totalnie` as off; soft did brightness_down |
| **Change** | Soft/`_OFF`: dim + totalnie/zupełnie/całkowicie → `light_off` |
| **Tests** | `test_sciemnij_totalnie_is_off`; suite green with smoke |
| **KEEP/REVERT** | **KEEP** |

## Remaining backlog
1. Explore PARTIAL soft gaps (further novel seeds)
2. Mic / physical HA / barge — **HARDWARE_UNVERIFIED**

---

## Cycle 21 — dry COMPUTER tools + summarize ≠ „Otwieram”

| Field | Notes |
|-------|-------|
| **Explore** | seed 20260915e: **PASS_GENERALIZATION**, novel 100%, hard FAIL=0 (PARTIAL residual OK) |
| **Problem** | L1 dry: `wpisz`/`zaznacz` → unknown tool; `streść to` after open → echoed „Otwieram.” |
| **Change** | Dry registry: type_text/hotkey/close_app/…; summarize rejects open/ack assistants |
| **Tests** | `tests.test_dry_computer_and_summarize`; suite/smoke/critical |
| **KEEP/REVERT** | **KEEP** |

---

## Cycle 22 — AssistFakeModel false DISCUSSION + bare media_calm

| Field | Notes |
|-------|-------|
| **Problem** | Harness AssistFakeModel matched `gdybym` / `nie otwieraj` inside **system/repair prompt**, so almost every agent turn became „To pytanie — nic nie robię.” Bare „coś łagodniejszego” also missed soft MEDIA. |
| **Evidence** | Probe: `HIT gdybym` on planner blob; inject → vacuous final; after partial fix critical FALSE_ACT on `co tam piszą?` (history `wyszukaj` in JSON payload). |
| **Root cause** | Discussion/tool heuristics scanned full messages + instruction/history JSON, not the current goal. Soft calmer required prior media. |
| **Change** | `AssistFakeModel._user_goal_blob` → JSON `goal`/`raw_text` only; soft `_CALMER_MEDIA` bare + talk unlock; regression tests. |
| **Tests before** | critical briefly 36/38 (FALSE_ACT×2) mid-fix |
| **Tests after** | unittest **1452 OK**; smoke **11/11**; critical **38/38**; explore seed `20260915f` **PASS_GENERALIZATION** novel 100% |
| **Architecture** | Harness-only AssistFakeModel; production soft media_calm widened intentionally |
| **KEEP/REVERT** | **KEEP** |

---

## Cycle 23 — set_preference ≠ „Urządzenie włączone.”

| Field | Notes |
|-------|-------|
| **Problem** | „zapamiętaj że wolę Spotify” → tool OK but TTS „Urządzenie włączone.” |
| **Evidence** | L1 jarvis_session probe; `speak_ha_result(set_preference)` → HA on-speech |
| **Root cause** | After ack-like tool message (`ok`), planner always called `speak_ha_result` for any tool. |
| **Change** | `speak_ha_result` no-ops for non-HA tools; planner preference phrase; dry set_preference message; `tests.test_preference_ack_not_device` |
| **KEEP/REVERT** | **KEEP** (gates pending this cycle close) |

---

## Cycle 24 — home overview + „przygotuj mnie na jutro”

| Field | Notes |
|-------|-------|
| **Problem** | „co słychać w domu” / „przygotuj mnie na jutro” → vacuous „Jasne, słucham.” despite existing FastPath tools |
| **Root cause** | Detectors too narrow (`_HOME_OVERVIEW`, `_BRIEFING`); dry harness missing `get_home_summary` / `get_situation_briefing` |
| **Change** | Extend `read_intents` + `situation_briefing` phrasing; dry read tools; `tests.test_home_briefing_routing` |
| **Tests after** | unittest **1457 OK**; smoke **11/11**; critical **38/38 FULL PASS** |
| **KEEP/REVERT** | **KEEP** |

---

## Cycle 25 — dry hotkey + read-tool speech (no Chwila)

| Field | Notes |
|-------|-------|
| **Problem** | `zaznacz wszystko` → unknown `hotkey`; `wpisz` / pogoda / kalendarz / przypomnienia → „Chwila…” lub „nie mogę potwierdzić” mimo success |
| **Root cause** | Dry registry missing `hotkey`; tools returned bare `ok`; `_speak` finalized tool ACK as knowledge `answer` → Chwila theater |
| **Change** | Dry hotkey + spoken read/computer messages; `_speak` agent/fast_path ACK → `kind=ack`; planner computer/read speech; `tests.test_dry_computer_read_speech` |
| **Tests after** | unittest **1459 OK**; smoke **11/11**; critical **38/38**; explore `20260915g` **PASS_GENERALIZATION** novel 100% |
| **KEEP/REVERT** | **KEEP** |

---

## Cycle 26 — retry after fail: named app + journal isolation

| Field | Notes |
|-------|-------|
| **Problem** | After fail→why→retry, soft replay could run stale global `set_preference` / empty `open_app` name („aplikacja otwarte”) |
| **Root cause** | `last_journal_action` process-global preferred over session `previous_tool`; harness did not reset journal |
| **Change** | Soft replay prefers session observations; retry_goal recovers app from objective; `jarvis_session` calls `reset_action_history`; `tests.test_retry_open_app_name` |
| **Tests after** | unittest **1460 OK**; smoke **11/11**; critical **38/38 FULL PASS** |
| **KEEP/REVERT** | **KEEP** |

---

## Cycle 27 — compound „ścisz” + cancel→kontynuuj ASK

| Field | Notes |
|-------|-------|
| **Problem** | `zgaś lampę i ścisz` → only light (soft stole); after `anuluj` + `kontynuuj` → revive cancelled Chrome |
| **Root cause** | `SECOND_ACTION` missing `ścisz`; CONTINUATION ignored CANCELLED goal status |
| **Change** | Compound verb; semantic ask_after_cancel; soft/CM cancel flags; kontynuuj expectant; tests |
| **Tests after** | unittest **1464 OK**; smoke **11/11**; critical **38/38** |
| **KEEP/REVERT** | **KEEP** |

---

## Cycle 28 — preference no invent Spotify + „ale inaczej” after fail

| Field | Notes |
|-------|-------|
| **Problem** | `zapamiętaj że wolę ciemny motyw` → `music.default_app=Spotify`; after fail `ale inaczej` → vacuous „Jasne, słucham.” |
| **Root cause** | PREFERENCE matched bare `zapamiętaj…wolę` and defaulted value to Spotify; alternate hint missed `ale inaczej` |
| **Change** | Tighten PREFERENCE patterns; no default Spotify; soft ask alternate after fail; tests |
| **Tests after** | unittest **1467 OK**; smoke **11/11**; critical **38/38 FULL PASS** |
| **KEEP/REVERT** | **KEEP** |

## Cycle 29 � compound przycisz + reverse �nie a� tak�

| Field | Notes |
|-------|-------|
| **Problem** | `wy��cz �wiat�o i przycisz` � tylko volume; `za jasno` � `nie a� tak` � ponownie przyciemnia |
| **Evidence** | Probe: compound undetected without `przycisz` in clause verbs; soft relative always `brightness_down`; `_RELATIVE_DAMPEN` mapped less�darker then enrich rewritten to Przyciemnij |
| **Root cause** | (1) `compound_plan` missing `przycisz`; (2) relative dampen ignored last `parameter_direction`; (3) soft absolute `za jasno` mixed into relative cue |
| **Change** | `przycisz` in compound verbs/volume; soft `_TOO_BRIGHT` + reverse soft; expose `parameter_direction` in `semantic_conversation`; invert reverse cues in `utterance_class`; tests |
| **Tests before** | compound przycisz undetected; session reverse FAIL |
| **Tests after** | unittest **1472 OK**; smoke **11/11**; critical **38/38 FULL PASS**; explore `20260915i` **PASS_GENERALIZATION** novel 100% |
| **Novel** | `wy��cz �wiat�o i przycisz` both tools; `za jasno`�`nie a� tak` brightens |
| **Architecture** | Reverse at utterance layer (first wrong layer); soft defense-in-depth |
| **KEEP/REVERT** | **KEEP** |
| **Risks** | Soft volume-up still missing (`podg�o�nij` � AssistFakeModel vacuous) � next cycle |


## Cycle 30 � soft volume-up + RC bucket for ActionLoop

| Field | Notes |
|-------|-------|
| **Problem** | `podg�o�nij` / `g�o�niej` � AssistFakeModel vacuous �Jasne, s�ucham.�; soft only had volume-down phrases |
| **Evidence** | Probe soft None for g�o�niej; agent final_answer; gates then failed expecting FastPath |
| **Root cause** | Asymmetric soft volume; completed volume goals lacked parameter_direction so �nie a� tak� fell to TALK |
| **Change** | `_SOFT_VOLUME_UP`; talk-locked relative reverse; last_action�parameter direction; RC `action_loop`�fast_path bucket; perf diag accepts action_loop |
| **Tests after** | unittest **1474 OK**; smoke **11/11**; critical **38/38 FULL PASS** |
| **KEEP/REVERT** | **KEEP** |
| **Next** | Continue exploration for next Jarvis gap |


---

## Cycle 31 - metaphor darkness + decibel volume under TALK lock

| Field | Notes |
|-------|-------|
| **Problem** | Fresh explore trace showed natural phrases `w tarasie jest jak w jaskini`, `w sypialni jak w piwnicy`, and `podkrec decybele troche` falling through to TALK / `Jasne, slucham.` instead of acting |
| **Severity** | P1/P2 natural-language generalization gap; not a crash, but very visible Jarvis-behavior failure |
| **Root cause** | Soft goals understood literal darkness/volume phrases but not compositional darkness metaphors or colloquial decibel phrasing; ActionLoop TALK lock allowed only a narrow set of soft escapes |
| **Change** | Added darkness metaphor recognition in `intent_family`; routed metaphor darkness through `soft_goals`; added decibel volume-up soft match; allowed trusted soft light/volume actions through TALK lock; added regression tests |
| **Files** | `core/intent_family.py`; `core/soft_goals.py`; `core/action_loop.py`; `tests/test_soft_volume_up.py`; `tests/test_soft_light_metaphors.py` |
| **Tests before** | Baseline green, but novel trace exposed vacuous TALK on the target phrases |
| **Tests after** | targeted soft tests **7 OK**; unittest **1479 OK**; smoke **11/11**; critical **38/38 FULL PASS**; explore `20260916T172941Z` **PASS_GENERALIZATION**, novel overall **99.167%** |
| **Novel** | `w tarasie jest jak w jaskini` and `w sypialni jak w piwnicy` now call `set_device_value` / `Rozjasnilem.`; `podkrec decybele troche` now calls `volume_up` / `Podglosnilem.` |
| **KEEP/REVERT** | **KEEP** |
| **Risks** | Remaining trace gaps: `pingnij mnie za kwadrans o herbacie` and `potrzebuje notion na juz` still fall to TALK; live mic/HA hardware remains unverified |

---

## Cycle 32 - slang reminder: pingnij za kwadrans

| Field | Notes |
|-------|-------|
| **Problem** | Novel trace still showed `pingnij mnie za kwadrans o herbacie` falling to TALK / `Jasne, slucham.` instead of creating a reminder |
| **Severity** | P2 Jarvis natural-language gap; safe to execute only when an explicit time is present |
| **Root cause** | `REMINDER_PATTERNS` only covered `przypomnij/przypomnienie`; parser did not understand `za kwadrans`; deterministic reminder decision did not pass `when` to `create_reminder`; dry explore harness also only recognized `przypomnij` |
| **Change** | Added `pingnij/pingnac` reminder signal, reminder time-slot extraction, `za kwadrans` parsing, deterministic `when` propagation, and dry harness support |
| **Files** | `core/intent_family.py`; `integrations/datetime_parse.py`; `core/agent_repair.py`; `core/jarvis_behavioral/harness.py`; `tests/test_agent_intent_contract.py`; `tests/test_pingnij_reminder.py` |
| **Tests after** | focused reminder tests **13 OK**; smoke **11/11**; critical **38/38 FULL PASS**; unittest **1482 OK**; explore `20260916T173508Z` **PASS_GENERALIZATION**, novel overall **99.167%** |
| **Novel** | `pingnij mnie za kwadrans o herbacie` now calls `create_reminder` with `text=herbacie`, `when=za kwadrans` |
| **KEEP/REVERT** | **KEEP** |
| **Risks** | Remaining trace gap: `potrzebuje notion na juz` still falls to TALK; live mic/HA hardware remains unverified |
