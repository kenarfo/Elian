# Elian — Self-Improvement System

A closed loop where the assistant notices its own failures, turns them into a
concrete technical requirement, generates the code that fixes it, proves the
code works in a sandbox, and only then — **after you approve it** — adds the new
capability to its own toolkit.

```text
Experience Log ──▶ Reflection Engine ──▶ Gap Analysis ──▶ Proposal Generator
 (what happened)    (patterns + one        (named gaps +     (requirement +
                     improvement)           capability map)    module + tests)
                                                                      │
                                                                      ▼
Adopt ◀── Approval Gate ◀── Verification Loop ◀── Execution Sandbox
 (hot-swap into    (human sees the      (fail-closed checks)   (isolated run,
  ToolRegistry)      code + says yes)                           timeout)
```

## Components

| Component | Module | Purpose |
|-----------|--------|---------|
| Experience Log | `core/self_improve/experience_log.py` | schema + failure categories + JSON store |
| Runtime Experience Collector | `core/self_improve/runtime_collector.py` | fail-open turn logging from ConversationManager → Experience Log |
| Reflection Engine | `core/self_improve/reflection.py` | aggregates failures per domain → improvement candidates |
| Gap Analysis | `core/self_improve/gap_analysis.py` | named `Capability Gap`s + the Capability Map |
| Proposal Generator | `core/self_improve/proposal.py` | gap → requirement → deterministic module + unittest |
| Execution Sandbox | `core/self_improve/sandbox.py` | AST safety scan + scrubbed subprocess with hard timeout |
| Verification Loop | `core/self_improve/verification.py` | ordered fail-closed checks; stores the verdict |
| Approval Gate | `core/self_improve/approval.py` | human-in-the-loop: review → approve / reject |
| Tool hot-swap | `core/self_improve/tool_hot_swap.py` | adopt → `learned/` → live ToolRegistry, no restart |
| CLI | `core/self_improve/cli.py` | drives the whole pipeline |

## Data (local JSON)

| File | Written by | Contents |
|------|-----------|----------|
| `Data/self_improve/experience_log.json` | CLI `log` + runtime collector | Experience Log (schema v1; seeded dummy rows preserved; runtime appends sanitized turns) |
| `Data/self_improve/capability_gaps.json` | `gaps` | detected Capability Gaps |
| `Data/self_improve/capability_map.json` | `gaps` | domain → `HEALTHY` / `GAP` + failure rate |
| `Data/self_improve/proposals.json` | `propose`/`verify`/`approve`/`adopt` | proposals incl. generated code + verdicts |
| `Data/self_improve/adopted.json` | `adopt` | manifest of learned modules |
| `Data/self_improve/sandbox/<id>/` | sandbox | throwaway copies of module + tests (git-ignored) |
| `core/self_improve/learned/*.py` | `adopt` | the only place generated code ever lands |

All reads go through `core.persistence.safe_read_json` (corrupt file → backup +
defaults, never a crash).

## Runtime observation (Phase 1)

`ConversationManager.handle` records **one** sanitized `ExperienceEntry` per
turn via `core.self_improve.runtime_collector` when logging is enabled.

**Observed today**

- turn `source` (fast_path / agent / chat / clarify / correction / …)
- DecisionEnvelope signals when present (`intent`, `utterance_class`, `confidence`, domains)
- compact tool outcomes buffered from `ToolRegistry._finalize` (name, status, success, error_code)
- latency (`time.monotonic` around the turn)
- session id (`ConversationManager.activate` → `_experience_session_id`)
- conservative `FailureCategory` classification

**Not observed / not stored**

- raw user utterance or assistant speech
- tool argument values
- secrets (token / password / api_key / …)
- full `ToolResult.data`
- audio
- HA entity display names / filesystem paths as free text

Wake / ack / wait / ambient / noise turns are skipped. Successful tool turns are
sampled (`success_sample_rate`). Failures are kept (with fingerprint dedupe).
The log is capped at `max_entries` (default 2000). Seeded/manual rows are not
rewritten.

**Config** (`Data/config.json`):

```json
"self_improve": {
  "runtime_logging": {
    "enabled": true,
    "store_utterance_hashes_only": true,
    "max_entries": 2000,
    "success_sample_rate": 0.1
  }
}
```

Set `enabled` to `false` to disable runtime logging without code changes.
`store_utterance_hashes_only` documents the policy; raw utterances are never
persisted either way.

Runtime entries use the same schema as CLI `log` rows and set
`provenance="runtime"`. CLI `log` writes `provenance="manual"`. Legacy/seed
rows without the field are treated as `seed` at load time.

`reflect` / `gaps` / `demo` analyze **runtime evidence only** by default
(`--evidence runtime`). Use `--evidence all` to inspect seed/manual history.
Phase 1 only **collects** at runtime; it does not auto-run reflection or proposals.

## Supervised dry-run (recommended next step)

After real microphone / conversation sessions have appended `provenance=runtime`
rows, inspect what Elian *would* learn — without changing anything:

```bash
python -m core.self_improve.cli dry-run
```

The command is **read-only for the learning system**. It:

1. audits the runtime corpus (physical rows vs weighted occurrences)
2. runs Reflection → Gap Analysis → Proposal generation **in memory**
3. prints each candidate with observed tool / error_code / evidence
4. **stops** — no proposal store write, no capability-map write, no sandbox
   adoption, no `learned/` mutation, no approval

### Corpus status

Dry-run reports a transparent status from weighted **runtime** occurrences:

| Status | Meaning |
|--------|---------|
| `EMPTY` | zero runtime occurrences |
| `SPARSE` | ≤9 occurrences, or &lt;3 failure occurrences, or no domains |
| `LIMITED` | ≤29 occurrences, or &lt;2 domains, or &lt;5 failure occurrences |
| `USABLE` | above those floors — suitable for supervised human review |

Sparse/limited corpora still produce an informational report. They are **not**
permission to enable autonomous Reflect → Gap → Propose.

Seed and manual rows are counted as **excluded** and never enter the dry-run
evidence set. Do not “fix” a sparse corpus by switching to `--evidence all`
for this command (`dry-run` is hard-wired to runtime).

## Microphone RC session (collect real evidence)

When the runtime corpus is `SPARSE`, collect genuine interactions with the
**real** wake → STT → ConversationManager path:

```bash
python -m core.self_improve.cli rc-session
```

What it does:

1. Snapshots the current runtime corpus (rows / weighted occurrences / status)
2. Prints a balanced scenario plan (messaging, app, smart home, knowledge, media,
   general conversation as a negative control)
3. Optionally launches `python main.py` with `ELIAN_RC_SESSION_ID` set so new
   runtime rows can carry a compact `metadata.rc_session_id`
4. Waits until you finish the voice session (process exit or Enter with `--no-launch`)
5. Reports **new** runtime rows / weighted occurrences / domains / categories / tools
6. Runs classification-health checks (e.g. clarify→TOOL_ERROR, chat→VAGUE_ANSWER)
7. Runs the existing supervised corpus audit + **read-only** `dry-run`
8. Stops — **no auto-adopt, no learned tools, no proposal persistence**

Useful flags:

```bash
python -m core.self_improve.cli rc-session --no-launch      # you start Elian yourself
python -m core.self_improve.cli rc-session --analyze-only   # summarize only (tests / CI)
```

Only genuine production turns count as runtime evidence. The harness never
inserts synthetic failures into `experience_log.json`.

Approximate target for a useful supervised review: ~20–40 physical runtime rows
and ~30–60+ weighted occurrences across several domains. Diversity matters more
than manufacturing failures.

### What the runtime corpus represents

Each runtime row is one sanitized turn fingerprint (often with
`repeat_count` &gt; 1 after dedup). Weighted statistics use
`entry.occurrence_weight` (= normalized `repeat_count`), so a single physical
row with `repeat_count=20` counts as twenty occurrences.

## Failure categories

`SUCCESS`, `NO_TOOL_MATCH` ("I couldn't find the right tool"), `WRONG_TOOL`,
`TOOL_ERROR`, `TIMEOUT`, `VAGUE_ANSWER` ("I gave a vague answer"),
`USER_FRUSTRATION`, `USER_CORRECTION`, `UNKNOWN`.

Log one by hand:

```bash
python -m core.self_improve.cli log --domain scheduling \
  --category NO_TOOL_MATCH --summary "no tool for 'every other Friday'"
```

## First step: from a dummy log to one suggested improvement

```bash
python -m core.self_improve.cli reflect     # reads Data/self_improve/experience_log.json
```

prints per-domain failure rates and the single best improvement, e.g.

```text
[GAP ] scheduling     8/11 failed (73%) dominant=NO_TOOL_MATCH
 1. Add recurring_schedule tool for scheduling  (priority 5.82)
    Add a dedicated recurring_schedule tool to the ToolRegistry for scheduling
    requests so the planner stops falling back to generic replies.
```

## Full loop

```bash
python -m core.self_improve.cli gaps                 # -> "Scheduling Capability Gap"
python -m core.self_improve.cli propose gap_scheduling
python -m core.self_improve.cli verify  <proposal>   # sandbox + verification
python -m core.self_improve.cli review  <proposal>   # SEE the code change
python -m core.self_improve.cli approve <proposal>   # y/N prompt (or --yes)
python -m core.self_improve.cli adopt   <proposal>   # write learned/<module>.py
```

`demo` runs everything up to `review` and deliberately stops before approval.

Once adopted, the running assistant picks the tool up without a restart —
`core.tools.get_registry()` calls `load_learned_tools()` on every access, and
unchanged files are skipped via an mtime cache:

```python
from core.tools import get_registry
from core.self_improve.tool_hot_swap import load_learned_tools, unlearn

load_learned_tools(get_registry())     # hot-swap in / refresh
get_registry().invoke("recurring_schedule", {"action": "expand",
                                             "start": "2026-01-05", "count": 3})
unlearn("recurring_schedule", registry=get_registry())   # and back out
```

## Safety model

1. **Human-in-the-loop** — `adopt` refuses anything that is not `APPROVED`, and
   `approve` refuses anything whose verification did not pass. The CLI shows the
   full generated code before asking (interactive `y/N`, `--yes` for scripted use).
2. **Static scan first** — generated module *and* tests are AST-scanned: import
   allowlist only (`datetime`, `re`, `unittest`, …), no `eval`/`exec`/`open`/
   `getattr`/`__import__`, no dunder attribute access, no `__`-looking string
   literals. Anything else fails closed and nothing is executed.
3. **Isolation** — code runs only in `Data/self_improve/sandbox/<id>/` in child
   processes with a scrubbed environment (no project `PYTHONPATH`) and a hard
   timeout (30s default).
4. **Additive only** — adopted code may never modify an existing file; it is
   written to `core/self_improve/learned/<module>.py` under a validated module
   name (no stdlib/project package names, target must stay inside `learned/`).
5. **No shadowing** — a learned tool whose name is already owned by a builtin is
   skipped at load time, not silently overridden.
6. **Fail-closed verification** — safety, module name, import, generated unit
   tests, tool contract (`TOOL_SPEC` + `handle({})` + `handle(sample)`), name
   collision; `core.tools` import smoke is reported as advisory.
7. **Reversible** — `unlearn` deletes the module and unregisters its tool.

## Verification checks

| Check | Required | What it proves |
|-------|----------|----------------|
| `safety` | yes | no dangerous imports/calls/dunders in module or tests |
| `module_name` | yes | legal identifier, target stays in `learned/` |
| `import` | yes | module imports in a scrubbed subprocess |
| `unit_tests` | yes | the generated unittest file passes within the timeout |
| `tool_contract` | yes | `TOOL_SPEC` shape, `handle({})` fails gracefully, `handle(sample)` succeeds |
| `name_collision` | yes | tool name does not shadow a registered tool |
| `core_smoke` | advisory | `import core.tools` still works |

## Extending

Add a template in `core/self_improve/proposal.py`:

1. write `_template_<kind>(gap) -> GeneratedArtifact` (pure module + unittest),
2. register it in `TEMPLATES["<kind>"]`,
3. add requirement text to `REQUIREMENTS["<kind>"]`,
4. map it from a failure category in `gap_analysis.KIND_BY_CATEGORY`.

Gap kinds today: `scheduling`, `tool_lookup`, `answer_quality`, `reliability`,
`general`.

## Tests

```bash
python -m unittest tests.test_self_improve -v
python -m unittest tests.test_self_develop -v
```

covers log schema/tolerance, reflection output, gap naming, the safety scanner,
all five templates through the sandbox, approval refusals, and the full
gap → proposal → verify → approve → adopt → hot-swap → unlearn pipeline, plus
autonomous loop mechanics (checkpoint/rollback, synthetic keep/fail, stop).

---

## Operating modes

### Normal Elian

Voice assistant runtime. May append sanitized runtime `ExperienceEntry` rows.
Does **not** start autonomous code modification.

### `dry-run` (read-only)

```bash
python -m core.self_improve.cli dry-run
```

Supervised Reflect → Gap → Proposal audit. Never adopts, never writes learned
tools, never mutates source.

### Supervised evaluation (human gate)

```bash
python -m core.self_improve.cli demo
python -m core.self_improve.cli approve <id> --yes
python -m core.self_improve.cli adopt <id>
```

Proposal is the endpoint until a human approves.

### Autonomous self-development

```text
repository-inspecting · code-writing · test-gated · checkpointed
rollback-capable · continuous · no per-cycle approval
```

```bash
# start (continuous until stop / unrecoverable)
python -m core.self_improve.cli self-develop

# status
python -m core.self_improve.cli self-develop-status

# stop after current safe operation
python -m core.self_improve.cli self-develop-stop
```

Loop state machine (`core/self_improve/self_development/`):

```text
OBSERVE → ANALYZE → PRIORITIZE → PLAN → IMPLEMENT → TEST
→ EVALUATE → KEEP/ROLLBACK → LEARN → NEXT
```

| Concern | Location |
|---------|----------|
| Engine | `self_development/engine.py` (`SelfDevelopmentEngine`) |
| Discovery / prioritize | `self_development/discovery.py` |
| Implement + self-review | `self_development/implement.py` |
| Test gates | `self_development/evaluate.py` |
| File checkpoints | `self_development/checkpoint.py` |
| State / history | `Data/self_improve/self_develop_state.json`, `development_history.json` |

**Checkpoint / rollback:** before each mutation a file snapshot is taken under
`Data/self_improve/checkpoints/`. Failed cycles restore only cycle-owned files.
Never `git reset --hard` against unrelated dirty work. Optional git commit on
KEEP when a git repo + identity exist.

**Resume after interrupt:** on start, if `incomplete_cycle` is set, the engine
rolls back the last unfinished checkpoint before observing again.

**Config** (`Data/config.json` → `self_improve.autonomous`):

```json
"autonomous": {
  "enabled": false,
  "max_repair_attempts": 3,
  "test_timeout": 180.0,
  "checkpoint_strategy": "file",
  "development_state_path": "Data/self_improve/self_develop_state.json",
  "history_path": "Data/self_improve/development_history.json",
  "sleep_between_cycles_s": 1.0
}
```

Normal Elian startup does **not** launch this mode. Start it explicitly.

**Safety:** secrets/credentials/`.env`/`Data/config.json`/`experience_log.json`
are forbidden paths. Telemetry is never fabricated. Changes under
`self_development/` require elevated gates (full self-improve suite + smoke).
