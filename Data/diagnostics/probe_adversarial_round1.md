# Probe adversarial round 1 — Elian SOFTWARE QA

Date: 2026-09-10 (UTC+1 / Etc/GMT-1). Machine: `a54f5f03-25ab-492d-b5ba-b9eca259a21f` (Ziemniaczek).
Scope: **SOFTWARE only** (dry semantic / registry / CM / HA message heuristics / computer lifecycle unit).
**No production code patched.** Evidence under `Data/diagnostics/`.

Probe scripts used:
- `_probe_natural_pl.py` (existing)
- `Data/diagnostics/_probe_adversarial_round1.py` (this round)
- inline wyszukaj / CM repros → `qa_wyszukaj_repro_round1.json`, `qa_wyszukaj_cm_round1.json`
- Artifacts: `natural_pl_probe_after.json`, `probe_adversarial_round1.json`

Ownership legend: **@Grocky** semantic/follow-ups · **@Memik** Planner/ActiveGoal/shortlist · **@Toolik** ToolRegistry/Cap honesty · **@Nexo** Memory/WorldState · **@Probe** this report.

---

## Env notes (SOFTWARE vs HARDWARE)

| Check | Result | Class |
|-------|--------|-------|
| `faster_whisper` | Resolves to **stub** `Elian\_smoke_stubs\faster_whisper.py` (not real package) | SOFTWARE/env |
| `torch` | **2.14.0+cpu** present | SOFTWARE/env |
| `from core.agent_smoke import run_agent_smoke` | import OK | SOFTWARE |
| `python main.py --agent-smoke` | **11/11 PASS** (no longer blocked by missing torch; whisper stubbed) | SOFTWARE |
| HA `192.168.0.31:80` | ConnectTimeout (~5s) during smoke + probes — **HARDWARE/network unreachable** | HARDWARE |
| Mic / voice capture | Not exercised (dry) | HARDWARE (out of scope) |

Compare prior audit `Data/diagnostics/natural_pl_semantic_audit.md`: previously `main.py --agent-smoke` was **BLOCKED** on `faster_whisper`/`torch`. Now smoke runs via stub + torch install. Stub means Whisper quality is **not** validated — only import gate.

---

## 1. Slang + follow-ups (`_probe_natural_pl.py`)

**Result: 17/17 PASS — no regressions vs post-P0 audit.**

Prior audit AFTER baseline was also 17/17. Failing IDs: `[]`.

| Scenario id | Phrase(s) | Result |
|-------------|-----------|--------|
| `ciemno_tu` | ciemno tu | PASS |
| `wez_cos_z_tym` | weź coś z tym zrób | PASS |
| `jeszcze_jasniej` | jeszcze jaśniej | PASS |
| `nie_az_tak` | nie aż tak (after COMPLETED brighten) | PASS |
| `zgas_ja` | zgaś ją | PASS |
| `troche_ciszej` | trochę ciszej | PASS |
| `nie_to` | nie to | PASS |
| `no_to_zrob` | no to zrób | PASS |
| `dobra_niewazne` | dobra nieważne | PASS |
| `zrob_jak_wczesniej` | zrób tak jak wcześniej | PASS |
| `czemu_nie_zrobiles` | czemu nie zrobiłeś? | PASS |
| `sprobuj_inaczej` | spróbuj inaczej | PASS |
| (+ odpal Michaela / co teraz leci / wyszukaj coś o tym / retry / unresolved) | | PASS |

**Layer:** @Grocky (semantic / utterance_class / active_goal discourse) — green.
**Regression verdict:** none vs `natural_pl_semantic_audit.md` after-P0.

Cold vs seeded ActiveGoal: probe covers both (fresh store per scenario; multi-turn with `mark_ok` / `mark_failed` / topic seed). No FAIL to hand off.

---

## 2. „wyszukaj X” e2e

| Gate | Expected | Actual | Layer | Verdict |
|------|----------|--------|-------|---------|
| Cap `WEB_SEARCH` after cold get/refresh | AVAILABLE, tools⊇`web_search` | AVAILABLE, `provider ready`, tools=`[web_search]` | @Toolik | **PASS** |
| Tool in registry | `web_search` present | present (109 tools; `build_default_registry`) | @Toolik | **PASS** |
| Shortlist nonempty + web | web tools when Cap AVAILABLE | `['web_search','fetch_web_page','open_website']` for `wyszukaj X` / seeded | @Memik | **PASS** |
| DecisionEnvelope `needs_tools` | True, intent web, domain WEB | cold+seeded: mode=ACT intent=web needs_tools=True domains=[WEB] conf=0.92 | @Grocky | **PASS** |
| CM handle does **not** claim cannot search | search / attempt; no „nie mogę wyszukać” | After `cm.activate()`: real DuckDuckGo hits; spoken „Mam wyniki z sieci…”; `nie_moge=false` | @Memik (+ @Toolik honesty) | **PASS** |
| `never_claim_missing_web_if_available` | rewrite false claim | „Nie mogę wyszukać w internecie.” → „Mogę to wyszukać — powiedz czego szukamy.” | @Toolik | **PASS** |

**Note:** CM without `activate()` returns `source=standby` empty — expected wake gate, not a search failure.

Evidence: `Data/diagnostics/qa_wyszukaj_repro_round1.json`, `qa_wyszukaj_cm_round1.json`.

---

## 3. FALSE SUCCESS findings (SOFTWARE)

### FAIL A — HA Polish/English error strings → `ToolResult.success=True`

- **Input:** message `"Błąd połączenia z Home Assistant."` or `"Connection to 192.168.0.31 timed out."` passed through `_tool_result_from_ha_message`
- **Expected:** `success=False` (connection/timeout error)
- **Actual:** `_message_success` → True → `ToolResult.ok` / `ToolStatus.SUCCESS`
- **Layer owner:** **@Toolik** (`core/builtin_tools.py` `_message_success` ~L103–131, `_tool_result_from_ha_message` ~L165–174)
- **Repro:**
  `from core.builtin_tools import _tool_result_from_ha_message`
  `r = _tool_result_from_ha_message("Błąd połączenia z Home Assistant.", tool_name="probe"); assert r.success` → True (should be False)
- **Why:** failure_markers include „nie mogę teraz połączyć” but **not** „błąd połączenia” / English `timed out` / `timeout` / `error`.
- **Live invoke caveat (PASS path):** `turn_on_device` / `set_device_value` against unreachable HA returned `success=False` with „Nie znalazłem takiego urządzenia…” (resolve miss before service). Heuristic hole is still real for any path that surfaces the connect-error string into `_tool_result_from_ha_message` without `ha_verify` UNKNOWN/FAILED.

### FAIL B — `type_text` unknown_side_effect returns `success=True`

- **Input:** unit `StageCHardwareFix3Tests.test_unknown_side_effect_is_partial_and_idle` — after type, clear editor + `signal_deadline()`
- **Expected:** `success=False`, `ToolStatus.PARTIAL`, `error=unknown_side_effect`
- **Actual:** `AssertionError: True is not false` (`result.success` is True)
- **Layer owner:** **@Toolik** (primary: `core/computer_agent.py` `_act_type` / deadline) · **@Memik** if planner completes goals on this ToolResult
- **Repro:**
  `python -m unittest tests.test_phase13_stage_c.StageCHardwareFix3Tests.test_unknown_side_effect_is_partial_and_idle`
- **Evidence (OBSERVED):**
  1. `signal_deadline` sets `_deadline = time.time()`; `deadline_exceeded` uses **`time.time() > _deadline`** (strict `>`), so immediately after signal the deadline branch often **does not fire** (`computer_agent.py` L327–331, L2198–2225).
  2. Fall-through sets `ok=bool(typed and target_ok)` even when content verify failed / `observed=''` / `verification_level=BASIC` (L2237–2245) → **success=True** + lifecycle SUCCEEDED via `act()` L1236–1237.
  3. Probe log: `[VERIFY] expected='XYZ' observed='' status=BASIC` then test asserts success False → fails.
- **Class:** SOFTWARE false success (not mic/HA hardware).

### OBSERVED C — open_app verify skipped still `success=True` (smoke)

- **Input:** agent-smoke `możesz odpalić mi spotify`
- **Actual:** tool `open_app` `success=True`; verify `window_exists` **skipped** `reason=verify_tool_unregistered`
- **Risk:** plan/goal can COMPLETE without window proof
- **Layer:** **@Memik** (`core/plan_cycle.py` verify skip ~L567) + open_app registration
- **Severity:** honesty gap / incomplete verify wiring — not a timeout-error→success lie, but related COMPLETED-without-effect class. Smoke still marks PASS.

### PASS (honesty) — ActiveGoal mark_result

- `mark_result(success=True, result={"ok": False})` → goal status **UNRESOLVED** (not COMPLETED). Aspiration alone insufficient. **@Memik** / `active_goal.py` OK.

### PASS — live HA tools when entity unresolved

- Unreachable HA → `turn_on_device` / `set_device_value` → `success=False` (device not found). Good fail-closed on that path.

---

## PASS summary (brief)

1. Natural PL slang/follow-ups: **17/17**, no P0 regression (@Grocky).
2. WEB_SEARCH Cap AVAILABLE + auto health (@Toolik).
3. `web_search` in registry; shortlist nonempty (@Toolik/@Memik).
4. Envelope `needs_tools=True` for wyszukaj cold+seeded (@Grocky).
5. CM activated handle searches and does not claim cannot search (@Memik/@Toolik).
6. Cap honesty rewrite for false „nie mogę wyszukać” (@Toolik).
7. `main.py --agent-smoke` **11/11** (SOFTWARE gate).
8. Goal store does not COMPLETE on aspirational success alone (@Memik).
9. HA invoke fail-closed when device unresolved (@Toolik).

---

## Failing cases table (handoff)

| # | Input → Expected → Actual | Layer |
|---|---------------------------|-------|
| 1 | HA msg `"Błąd połączenia z Home Assistant."` → success=False → **success=True** | **@Toolik** `builtin_tools._message_success` |
| 2 | HA msg `"Connection … timed out."` → success=False → **success=True** | **@Toolik** same |
| 3 | `test_unknown_side_effect_is_partial_and_idle` / deadline after type with empty observe → PARTIAL success=False → **success=True** | **@Toolik** `computer_agent._act_type` (+ deadline `>` vs `>=`); **@Memik** if goals complete on it |
| 4 | (obs) open_app success with verify unregistered → prefer verify or non-COMPLETE | **@Memik** plan_cycle / tool verify registration |

No slang/wyszukaj semantic fails this round.

---

## SOFTWARE vs HARDWARE

| Item | Bucket |
|------|--------|
| Natural PL, Cap, shortlist, CM search, `_message_success`, computer unknown_side_effect unit, agent-smoke | **SOFTWARE** |
| HA host unreachable / ConnectTimeout to 192.168.0.31 | **HARDWARE/network** (environment) |
| Real mic / Whisper ASR quality | **HARDWARE** / stubbed SOFTWARE — not probed |
| Real light actuation | **HARDWARE** — not claimed; tools fail-closed on missing entities |

---

## Handoff (no patches from Probe)

1. **@Toolik** — widen `_message_success` failure markers (błąd połączenia / timeout / timed out / error) or stop routing raw connect errors through optimistic message heuristic; prefer Cap/HA UNAVAILABLE ToolResult.
2. **@Toolik** — fix `deadline_exceeded` vs `signal_deadline` (`>=` or subtract epsilon); ensure `_act_type` returns `unknown_side_effect` when deadline fires and content unverified; do not `ok=typed and target_ok` when `verified=False` and deadline/uncertain.
3. **@Memik** — confirm planner/ActiveGoal never COMPLETE on PARTIAL/unknown_side_effect or verify-skipped open_app; register `window_exists` in smoke/dry registry if verify is required.
4. **@Grocky** — no action (17/17 green).
5. **@Nexo** — no memory/context fails in this round.

Probe stops here. Evidence JSON/MD under `Data/diagnostics/`.
