# ==========================
# ELIAN — Self-improvement RC session harness
# ==========================
"""Guided microphone RC around the real Elian voice runtime.

Collects genuine runtime evidence, then runs the existing corpus audit +
supervised dry-run. Never fabricates telemetry, never adopts proposals.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from core.self_improve.experience_log import (
    EVIDENCE_RUNTIME,
    ExperienceEntry,
    ExperienceLogStore,
    FailureCategory,
    select_evidence,
)
from core.self_improve.supervised_dry_run import (
    CorpusAudit,
    SupervisedDryRunReport,
    audit_runtime_corpus,
    run_supervised_dry_run,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RC_SESSION_ENV = "ELIAN_RC_SESSION_ID"

#: Scenario guide — real ToolRegistry / domain capabilities (not a fake assistant).
RC_SCENARIOS: List[Dict[str, Any]] = [
    {
        "id": "messaging",
        "title": "Messaging",
        "domain": "messaging",
        "tools": ["find_contact", "send_message"],
        "prompts": [
            "Normal: ask Elian to find a contact / message someone (SUCCESS path).",
            "Ambiguous: use a name that matches several people (expect clarify, NOT TOOL_ERROR).",
            "Optional failure: unavailable contact channel if safely reproducible.",
        ],
        "notes": "Clarification/AMBIGUOUS must not become TOOL_ERROR in the log.",
    },
    {
        "id": "app",
        "title": "App control",
        "domain": "app",
        "tools": ["open_app", "close_app", "check_app_installed", "focus_or_open"],
        "prompts": [
            "Success: open a real installed app (e.g. Spotify / Notepad).",
            "Optional: ask for an app that is not installed (unavailable / TOOL_ERROR).",
        ],
        "notes": "Do not invent tool names; use open_app family only.",
    },
    {
        "id": "smart_home",
        "title": "Smart Home",
        "domain": "smart_home",
        "tools": [
            "turn_on_device",
            "turn_off_device",
            "set_device_value",
            "get_device_state",
            "list_devices",
        ],
        "prompts": [
            "Success: adjust a known light/device if HA is available.",
            "If HA/device unavailable: say so - do not fabricate a failure row.",
        ],
        "notes": "Skip destructive device actions. Unavailable environment is OK.",
    },
    {
        "id": "knowledge",
        "title": "Knowledge / information",
        "domain": "knowledge",
        "tools": ["web_search", "get_weather"],
        "prompts": [
            "Factual question that should use knowledge/web when available.",
            "A request where grounding/evidence is expected (current events / lookup).",
            "Observe whether answer-quality metadata appears; do not sabotage the model.",
        ],
        "notes": "VAGUE_ANSWER only if the real runtime marks insufficient resolution.",
    },
    {
        "id": "media",
        "title": "Media (optional)",
        "domain": "media",
        "tools": ["play_media", "pause_media", "resume_media"],
        "prompts": [
            "Optional: play/pause media if a player is available.",
        ],
        "notes": "Skip if no media backend is ready.",
    },
    {
        "id": "chat",
        "title": "General conversation (negative control)",
        "domain": "knowledge",
        "tools": [],
        "prompts": [
            "Casual: 'hej', 'co tam', short chat - must NOT become VAGUE_ANSWER.",
        ],
        "notes": "These are false-positive controls for answer-quality classification.",
    },
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_rc_session_id() -> str:
    return f"rc_{uuid.uuid4().hex[:12]}"


def _weight(entry: ExperienceEntry) -> int:
    try:
        return int(getattr(entry, "occurrence_weight", None) or 1)
    except (TypeError, ValueError):
        return 1


def _meta(entry: ExperienceEntry) -> Dict[str, Any]:
    raw = getattr(entry, "metadata", None) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def snapshot_runtime_ids(store: ExperienceLogStore) -> Set[str]:
    rows = select_evidence(store.load(), EVIDENCE_RUNTIME)
    return {str(entry.id) for entry in rows if getattr(entry, "id", None)}


def entries_after(
    store: ExperienceLogStore,
    before_ids: Set[str],
    *,
    rc_session_id: str = "",
) -> List[ExperienceEntry]:
    """Runtime rows new since the snapshot (optional rc_session_id filter)."""
    runtime = select_evidence(store.load(), EVIDENCE_RUNTIME)
    fresh: List[ExperienceEntry] = []
    for entry in runtime:
        eid = str(getattr(entry, "id", "") or "")
        if eid and eid in before_ids:
            continue
        if rc_session_id:
            meta = _meta(entry)
            tagged = str(meta.get("rc_session_id") or "").strip()
            # Prefer tagged rows when present; still include untagged new ids
            # so sessions work even if collector env was unset mid-run.
            if tagged and tagged != rc_session_id:
                continue
        fresh.append(entry)
    return fresh


def diff_audit(entries: Sequence[ExperienceEntry]) -> CorpusAudit:
    return audit_runtime_corpus(list(entries), evidence=EVIDENCE_RUNTIME)


@dataclass
class SuspiciousHit:
    entry_id: str
    reason: str
    category: str
    source: str
    tool: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "reason": self.reason,
            "category": self.category,
            "source": self.source,
            "tool": self.tool,
        }


def find_suspicious_classifications(
    entries: Sequence[ExperienceEntry],
) -> List[SuspiciousHit]:
    """Heuristic health checks on collected runtime rows (report only)."""
    hits: List[SuspiciousHit] = []
    for entry in entries:
        cat = (
            entry.category.value
            if isinstance(entry.category, FailureCategory)
            else str(entry.category or "")
        )
        meta = _meta(entry)
        source = str(meta.get("source") or "").strip().lower()
        intent = str(meta.get("intent") or "").strip().lower()
        utt = str(meta.get("utterance_class") or "").strip().upper()
        code = str(meta.get("error_code") or "").strip().upper()
        tool = str(getattr(entry, "tool", "") or "")
        eid = str(getattr(entry, "id", "") or "")

        if cat == FailureCategory.TOOL_ERROR.value and source == "clarify":
            hits.append(
                SuspiciousHit(
                    eid, "clarification classified as TOOL_ERROR", cat, source, tool
                )
            )
        if cat == FailureCategory.TOOL_ERROR.value and code in (
            "AMBIGUOUS",
            "NEEDS_CONFIRMATION",
        ):
            hits.append(
                SuspiciousHit(
                    eid,
                    f"ambiguity/confirmation error_code={code} as TOOL_ERROR",
                    cat,
                    source,
                    tool,
                )
            )
        if cat == FailureCategory.VAGUE_ANSWER.value and source == "confirm":
            hits.append(
                SuspiciousHit(
                    eid, "confirmation classified as VAGUE_ANSWER", cat, source, tool
                )
            )
        if cat == FailureCategory.VAGUE_ANSWER.value and (
            intent in ("general_conversation", "unclear_utterance", "smalltalk")
            or utt in ("CASUAL_TALK", "CONFIRMATION")
        ):
            hits.append(
                SuspiciousHit(
                    eid, "normal/casual chat classified as VAGUE_ANSWER", cat, source, tool
                )
            )
        if cat == FailureCategory.TOOL_ERROR.value and meta.get("agent_success") is True:
            hits.append(
                SuspiciousHit(
                    eid,
                    "TOOL_ERROR while agent_success=True",
                    cat,
                    source,
                    tool,
                )
            )
        tools = meta.get("tools")
        if (
            cat == FailureCategory.TOOL_ERROR.value
            and isinstance(tools, list)
            and tools
            and all(isinstance(t, dict) and t.get("success") for t in tools)
        ):
            hits.append(
                SuspiciousHit(
                    eid,
                    "TOOL_ERROR but all buffered tools marked success",
                    cat,
                    source,
                    tool,
                )
            )
    return hits


@dataclass
class RcSessionPlan:
    scenarios: List[Dict[str, Any]] = field(default_factory=lambda: list(RC_SCENARIOS))
    target_rows: str = "20-40 physical runtime rows"
    target_occurrences: str = "30-60+ weighted occurrences"

    def render(self) -> str:
        lines = [
            "Scenarios (use the normal Elian microphone / wake interface):",
            "",
        ]
        for index, scenario in enumerate(self.scenarios, start=1):
            tools = ", ".join(scenario.get("tools") or []) or "(no specific tool)"
            lines.append(f"[{index}] {scenario['title']}  (domain={scenario['domain']})")
            lines.append(f"    tools: {tools}")
            for prompt in scenario.get("prompts") or []:
                lines.append(f"    - {prompt}")
            note = scenario.get("notes") or ""
            if note:
                lines.append(f"    note: {note}")
            lines.append("")
        lines.append(f"Target (approximate): {self.target_rows}; {self.target_occurrences}")
        lines.append("Diversity matters more than forcing failures.")
        return "\n".join(lines)


@dataclass
class RcSessionReport:
    rc_session_id: str
    started_at: str
    finished_at: str
    before: CorpusAudit
    after: CorpusAudit
    new_entries: List[ExperienceEntry] = field(default_factory=list)
    new_audit: CorpusAudit = field(default_factory=CorpusAudit)
    suspicious: List[SuspiciousHit] = field(default_factory=list)
    dry_run: Optional[SupervisedDryRunReport] = None
    voice_launched: bool = False
    voice_launch_error: str = ""
    mutated: bool = False

    @property
    def new_physical_rows(self) -> int:
        return len(self.new_entries)

    @property
    def new_weighted_occurrences(self) -> int:
        return int(self.new_audit.weighted_occurrences)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rc_session_id": self.rc_session_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "new_physical_rows": self.new_physical_rows,
            "new_weighted_occurrences": self.new_weighted_occurrences,
            "new_audit": self.new_audit.to_dict(),
            "suspicious": [hit.to_dict() for hit in self.suspicious],
            "dry_run": self.dry_run.to_dict() if self.dry_run else None,
            "voice_launched": self.voice_launched,
            "voice_launch_error": self.voice_launch_error,
            "mutated": self.mutated,
            "read_only": True,
            "no_auto_adopt": True,
        }

    def render(self) -> str:
        lines: List[str] = []
        lines.append("=== ELIAN SELF-IMPROVEMENT RC SESSION RESULT ===")
        lines.append(f"rc_session_id: {self.rc_session_id}")
        lines.append(f"started:  {self.started_at}")
        lines.append(f"finished: {self.finished_at}")
        lines.append("")
        lines.append("BEFORE (runtime)")
        lines.append(
            f"  rows={self.before.physical_rows}  "
            f"occurrences={self.before.weighted_occurrences}  "
            f"status={self.before.status}"
        )
        lines.append("")
        lines.append("SESSION RESULT (new runtime evidence only)")
        lines.append(f"  new physical rows:        {self.new_physical_rows}")
        lines.append(f"  new weighted occurrences: {self.new_weighted_occurrences}")
        lines.append(
            f"  new domains: {', '.join(self.new_audit.domains) or '(none)'}"
        )
        lines.append(
            f"  new categories: {', '.join(self.new_audit.categories) or '(none)'}"
        )
        lines.append(
            f"  new tools: {', '.join(self.new_audit.top_tools) or '(none)'}"
        )
        lines.append(
            f"  new error codes: {', '.join(self.new_audit.top_error_codes) or '(none)'}"
        )
        lines.append("")
        lines.append("NEW ENTRY COVERAGE (physical)")
        lines.append(
            f"  answer-quality metadata: "
            f"{self.new_audit.answer_quality_meta_rows}/{self.new_audit.physical_rows}"
        )
        lines.append(
            f"  grounding metadata: "
            f"{self.new_audit.grounding_meta_rows}/{self.new_audit.physical_rows}"
        )
        lines.append(
            f"  agent-path: "
            f"{self.new_audit.agent_path_rows}/{self.new_audit.physical_rows}"
        )
        lines.append("")
        lines.append("AFTER (full runtime corpus)")
        lines.append(
            f"  rows={self.after.physical_rows}  "
            f"occurrences={self.after.weighted_occurrences}  "
            f"success={self.after.success_occurrences}  "
            f"failure={self.after.failure_occurrences}  "
            f"failure_rate={self.after.failure_rate:.0%}  "
            f"status={self.after.status}"
        )
        lines.append(
            f"  distinct domains={self.after.distinct_domains}  "
            f"categories={self.after.distinct_categories}  "
            f"tools={self.after.distinct_tools}"
        )
        lines.append(
            f"  answer-quality coverage: "
            f"{self.after.answer_quality_meta_rows}/{self.after.physical_rows}"
        )
        lines.append(
            f"  grounding coverage: "
            f"{self.after.grounding_meta_rows}/{self.after.physical_rows}"
        )
        lines.append(
            f"  agent-path coverage: "
            f"{self.after.agent_path_rows}/{self.after.physical_rows}"
        )
        if self.voice_launch_error:
            lines.append("")
            lines.append(f"VOICE LAUNCH: {self.voice_launch_error}")
        lines.append("")
        lines.append("CLASSIFICATION HEALTH")
        if not self.suspicious:
            lines.append("  No suspicious classifications detected in new runtime rows.")
        else:
            lines.append(f"  FLAGGED: {len(self.suspicious)} suspicious classification(s)")
            for hit in self.suspicious[:20]:
                lines.append(
                    f"  - {hit.entry_id}: {hit.reason} "
                    f"(category={hit.category} source={hit.source or '-'} "
                    f"tool={hit.tool or '-'})"
                )
        lines.append("")
        lines.append("SAFETY")
        lines.append("  NO AUTO-ADOPT")
        lines.append("  NO SOURCE MUTATION")
        lines.append("  NO LEARNED TOOL MUTATION")
        lines.append("  NO PROPOSAL PERSISTENCE FROM THIS COMMAND")
        if self.dry_run is not None:
            lines.append("")
            lines.append("--- supervised dry-run (read-only) ---")
            lines.append(self.dry_run.render())
        return "\n".join(lines)


def render_session_banner(before: CorpusAudit, plan: RcSessionPlan) -> str:
    lines = [
        "=== ELIAN SELF-IMPROVEMENT RC SESSION ===",
        "",
        "Purpose:",
        "  Collect real runtime evidence for supervised self-improvement evaluation.",
        "",
        "Current corpus (runtime):",
        f"  {before.physical_rows} row(s) / {before.weighted_occurrences} occurrence(s) / "
        f"{before.status}",
        "",
        plan.render(),
        "",
        "Run the scenarios through the normal Elian microphone interface.",
        "When finished, return here and complete the session.",
        "No code changes or automatic adoption will occur.",
        "",
    ]
    return "\n".join(lines)


def launch_production_voice(
    *,
    rc_session_id: str,
    project_root: Path = PROJECT_ROOT,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.Popen:
    """Start ``python main.py`` (real wake/STT/CM path). Caller waits on the process."""
    run_env = dict(os.environ if env is None else env)
    run_env[RC_SESSION_ENV] = rc_session_id
    main_py = project_root / "main.py"
    return subprocess.Popen(
        [sys.executable, str(main_py)],
        cwd=str(project_root),
        env=run_env,
    )


def wait_for_session_end(
    *,
    proc: Optional[subprocess.Popen] = None,
    input_fn: Callable[[str], str] = input,
) -> None:
    """Block until the voice process exits or the operator confirms finish."""
    if proc is not None:
        print()
        print("Production voice launched (python main.py).")
        print("Use wake + microphone as usual. Stop Elian (Ctrl+C) when scenarios are done.")
        print("Waiting for the voice process to exit...")
        try:
            proc.wait()
        except KeyboardInterrupt:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        return
    print()
    print("Voice was not launched by this harness.")
    print("Run Elian yourself (python main.py), complete the scenarios, then press Enter here.")
    try:
        input_fn("")
    except EOFError:
        pass


def finalize_rc_session(
    store: ExperienceLogStore,
    *,
    before_ids: Set[str],
    before_audit: CorpusAudit,
    rc_session_id: str,
    started_at: str,
    voice_launched: bool = False,
    voice_launch_error: str = "",
) -> RcSessionReport:
    """Compute delta, suspicious hits, and read-only dry-run. No mutations."""
    finished_at = utc_now_iso()
    after_audit = audit_runtime_corpus(store.load(), evidence=EVIDENCE_RUNTIME)
    new_rows = entries_after(store, before_ids, rc_session_id=rc_session_id)
    new_audit = diff_audit(new_rows)
    # Seed isolation sanity: new_audit only from runtime rows already filtered.
    suspicious = find_suspicious_classifications(new_rows)
    dry = run_supervised_dry_run(store, evidence=EVIDENCE_RUNTIME)
    return RcSessionReport(
        rc_session_id=rc_session_id,
        started_at=started_at,
        finished_at=finished_at,
        before=before_audit,
        after=after_audit,
        new_entries=new_rows,
        new_audit=new_audit,
        suspicious=suspicious,
        dry_run=dry,
        voice_launched=voice_launched,
        voice_launch_error=voice_launch_error,
        mutated=False,
    )


def run_rc_session(
    store: Optional[ExperienceLogStore] = None,
    *,
    launch_voice: bool = True,
    analyze_only: bool = False,
    rc_session_id: str = "",
    input_fn: Callable[[str], str] = input,
    launch_fn: Optional[Callable[..., subprocess.Popen]] = None,
    project_root: Path = PROJECT_ROOT,
) -> RcSessionReport:
    """Orchestrate one supervised RC session around the real runtime log."""
    source = store or ExperienceLogStore()
    session_id = str(rc_session_id or "").strip() or new_rc_session_id()
    started_at = utc_now_iso()
    before_ids = snapshot_runtime_ids(source)
    before_audit = audit_runtime_corpus(source.load(), evidence=EVIDENCE_RUNTIME)
    plan = RcSessionPlan()

    print(render_session_banner(before_audit, plan))
    print(f"rc_session_id={session_id}")
    print(f"(export {RC_SESSION_ENV}={session_id} if launching Elian yourself)")
    print()

    voice_launched = False
    voice_error = ""
    proc = None

    if analyze_only:
        print("ANALYZE-ONLY: skipping live interaction wait.")
    else:
        if launch_voice:
            try:
                starter = launch_fn or launch_production_voice
                proc = starter(rc_session_id=session_id, project_root=project_root)
                voice_launched = True
            except Exception as exc:
                voice_error = f"could not launch voice ({exc}); continuing in wait-for-Enter mode"
                print(f"[RC] {voice_error}")
                proc = None
        wait_for_session_end(proc=proc, input_fn=input_fn)

    return finalize_rc_session(
        source,
        before_ids=before_ids,
        before_audit=before_audit,
        rc_session_id=session_id,
        started_at=started_at,
        voice_launched=voice_launched,
        voice_launch_error=voice_error,
    )
