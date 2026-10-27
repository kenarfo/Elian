# ==========================
# ELIAN — Improvement discovery (runtime + repository + tests)
# ==========================
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..gap_analysis import CapabilityGap, GapAnalyzer
from ..reflection import ReflectionEngine
from .types import ImprovementCandidate, ImprovementKind


# Comment-only markers (avoid matching scanner source / string constants)
_TODO_RE = re.compile(
    r"(?:#|//)\s*(TODO|FIXME|XXX|HACK)\b[:\s\-]*(.{0,80})",
    re.I,
)
_SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "Data",
    "checkpoints",
    ".cursor",
    "agent-transcripts",
    "self_development",  # do not treat autonomy engine internals as product TODOs
}


def _fp(*parts: str) -> str:
    raw = "|".join(str(p or "").strip().lower() for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class ImprovementDiscovery:
    """Multi-source candidate discovery for autonomous development."""

    def __init__(
        self,
        project_root: Path,
        *,
        max_todo_hits: int = 40,
        max_candidates: int = 40,
    ) -> None:
        self.root = Path(project_root).resolve()
        self.max_todo_hits = max_todo_hits
        self.max_candidates = max_candidates

    def discover(
        self,
        *,
        include_runtime: bool = True,
        include_repo: bool = True,
        include_tests: bool = True,
        synthetic: Optional[List[ImprovementCandidate]] = None,
        quarantine: Optional[Dict[str, Any]] = None,
    ) -> List[ImprovementCandidate]:
        found: List[ImprovementCandidate] = []
        if synthetic:
            found.extend(synthetic)
        if include_runtime:
            found.extend(self._from_runtime())
        if include_repo:
            found.extend(self._from_repository_markers())
            found.extend(self._from_architecture_gaps())
        if include_tests:
            found.extend(self._from_failing_tests())
        by_fp: Dict[str, ImprovementCandidate] = {}
        for c in found:
            if quarantine and c.fingerprint in quarantine:
                q = quarantine[c.fingerprint]
                if isinstance(q, dict) and int(q.get("failures") or 0) >= 3:
                    c.quarantined = True
                    c.attempt_count = int(q.get("failures") or 0)
                    c.score *= 0.05
            prev = by_fp.get(c.fingerprint)
            if prev is None or c.score > prev.score:
                by_fp[c.fingerprint] = c
        ranked = sorted(by_fp.values(), key=lambda x: (-x.score, x.id))
        return ranked[: self.max_candidates]

    def _from_runtime(self) -> List[ImprovementCandidate]:
        out: List[ImprovementCandidate] = []
        try:
            report = ReflectionEngine().reflect(evidence="runtime")
            gaps = GapAnalyzer().analyze(report, evidence="runtime")
        except Exception:
            return out
        for gap in gaps:
            if not isinstance(gap, CapabilityGap) or gap.addressed:
                continue
            dominant = ""
            if gap.failure_modes:
                dominant = max(gap.failure_modes.items(), key=lambda kv: kv[1])[0]
            score = self._score_gap(gap, dominant)
            kind = ImprovementKind.TOOLS.value
            if dominant in ("TOOL_ERROR", "EXECUTION_FAILED"):
                kind = ImprovementKind.RELIABILITY.value
            elif dominant in ("VAGUE_ANSWER",):
                kind = ImprovementKind.UX.value
            elif dominant in ("NO_TOOL_MATCH", "CLARIFICATION"):
                kind = ImprovementKind.CAPABILITY.value
            title = f"Close gap: {gap.domain} / {dominant or gap.kind}"
            if gap.observed_tool:
                title = f"Improve tool path: {gap.observed_tool} ({dominant or gap.kind})"
            # UNKNOWN with no tool/error is usually telemetry noise, not a product defect.
            if (
                dominant == "UNKNOWN"
                and not gap.observed_tool
                and not gap.error_code
            ):
                score = min(score, 12.0)
                kind = ImprovementKind.RELIABILITY.value
                title = f"Validate telemetry gap: {gap.domain} / UNKNOWN"
            out.append(
                ImprovementCandidate(
                    id=f"gap:{gap.id}",
                    title=title,
                    kind=kind,
                    reason=(
                        f"{gap.evidence_count} weighted {dominant or gap.kind} "
                        f"(rate={gap.failure_rate:.0%}, sev={gap.severity})"
                    ),
                    score=score,
                    fingerprint=_fp(
                        "gap",
                        gap.domain,
                        dominant,
                        gap.observed_tool,
                        gap.error_code,
                    ),
                    domain=gap.domain,
                    category=dominant or gap.kind,
                    observed_tool=gap.observed_tool or "",
                    error_code=gap.error_code or "",
                    gap_id=gap.id,
                    source="runtime",
                    metadata={"gap": gap.to_dict()},
                )
            )
        return out

    def _score_gap(self, gap: CapabilityGap, dominant: str) -> float:
        sev = {"high": 1.0, "medium": 0.65, "low": 0.35}.get(
            str(gap.severity or "medium").lower(), 0.5
        )
        freq = min(1.0, float(gap.evidence_count or 0) / 20.0)
        conf = min(1.0, float(gap.failure_rate or 0.0))
        testability = 0.8 if dominant in ("TOOL_ERROR", "NO_TOOL_MATCH") else 0.55
        complexity = 0.4 if dominant == "VAGUE_ANSWER" else 0.25
        if dominant == "UNKNOWN" and not gap.observed_tool and not gap.error_code:
            testability = 0.15
            conf *= 0.35
        return round(
            100.0
            * (
                0.30 * sev
                + 0.25 * freq
                + 0.20 * conf
                + 0.15 * testability
                - 0.10 * complexity
            ),
            3,
        )

    def _from_repository_markers(self) -> List[ImprovementCandidate]:
        out: List[ImprovementCandidate] = []
        hits = 0
        scan_roots = [
            self.root / "core",
            self.root / "voice",
            self.root / "integrations",
            self.root / "vision",
        ]
        for base in scan_roots:
            if not base.is_dir():
                continue
            for path in base.rglob("*.py"):
                if hits >= self.max_todo_hits:
                    break
                if any(part in _SKIP_DIRS for part in path.parts):
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for i, line in enumerate(text.splitlines(), start=1):
                    m = _TODO_RE.search(line)
                    if not m:
                        continue
                    hits += 1
                    rel = str(path.relative_to(self.root)).replace("\\", "/")
                    marker = m.group(1).upper()
                    note = (m.group(2) or "").strip()
                    severity = 0.85 if marker == "FIXME" else 0.55
                    out.append(
                        ImprovementCandidate(
                            id=f"todo:{rel}:{i}",
                            title=f"Resolve {marker} in {rel}:{i}",
                            kind=ImprovementKind.RELIABILITY.value
                            if marker == "FIXME"
                            else ImprovementKind.ARCHITECTURE.value,
                            reason=note or f"{marker} marker",
                            score=round(40.0 * severity, 3),
                            fingerprint=_fp("todo", rel, str(i), marker),
                            source="repository",
                            files_hint=[rel],
                            metadata={"line": i, "marker": marker, "note": note},
                        )
                    )
                    if hits >= self.max_todo_hits:
                        break
        return out

    def _from_architecture_gaps(self) -> List[ImprovementCandidate]:
        out: List[ImprovementCandidate] = []
        rem = self.root / "core" / "proactive_reminders.py"
        cm = self.root / "core" / "conversation_manager.py"
        rt = self.root / "core" / "runtime.py"
        watcher = self.root / "watchers" / "reminders.py"
        proactive = self.root / "core" / "proactive.py"

        rem_txt = ""
        cm_txt = ""
        watcher_txt = ""
        proactive_txt = ""
        if rem.exists():
            try:
                rem_txt = rem.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                rem_txt = ""
        if cm.exists():
            try:
                cm_txt = cm.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                cm_txt = ""

        # Scaffold-only helper still needs real ReminderStore integration
        if rem.exists() and "get_reminder_store" not in rem_txt and "ReminderStore" not in rem_txt:
            out.append(
                ImprovementCandidate(
                    id="cap:proactive_reminders_integrate",
                    title="Integrate proactive_reminders with ReminderStore",
                    kind=ImprovementKind.PROACTIVITY.value,
                    reason="proactive_reminders.py is still a scaffold without ReminderStore",
                    score=72.0,
                    fingerprint=_fp("cap", "proactive_reminders_integrate"),
                    source="repository",
                    files_hint=["core/proactive_reminders.py", "core/conversation_manager.py"],
                )
            )
        elif not rem.exists() and "proactive" in cm_txt.lower():
            out.append(
                ImprovementCandidate(
                    id="cap:proactive_reminders_module",
                    title="Add dedicated proactive reminders helper",
                    kind=ImprovementKind.PROACTIVITY.value,
                    reason="Proactive paths referenced without dedicated reminder module",
                    score=28.0,
                    fingerprint=_fp("cap", "proactive_reminders"),
                    source="repository",
                    files_hint=["core/proactive_reminders.py"],
                )
            )
        elif rem.exists() and "poll_proactive_reminders" not in cm_txt:
            out.append(
                ImprovementCandidate(
                    id="cap:proactive_reminders_cm_wire",
                    title="Wire proactive_reminders into ConversationManager",
                    kind=ImprovementKind.PROACTIVITY.value,
                    reason="Reminder facade exists but ConversationManager does not poll it",
                    score=68.0,
                    fingerprint=_fp("cap", "proactive_reminders_cm_wire"),
                    source="repository",
                    files_hint=["core/conversation_manager.py", "core/runtime.py"],
                )
            )

        # Reliability: ReminderWatcher marks delivered before speech succeeds
        if watcher.exists():
            try:
                watcher_txt = watcher.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                watcher_txt = ""
        if proactive.exists():
            try:
                proactive_txt = proactive.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                proactive_txt = ""
        if (
            "mark_delivered" in watcher_txt
            and "self.store.mark_delivered" in watcher_txt
            and "reminder_due" in proactive_txt
            and "mark_delivered" not in proactive_txt
        ):
            out.append(
                ImprovementCandidate(
                    id="fix:reminder_mark_after_speech",
                    title="Mark reminders delivered only after successful proactive speech",
                    kind=ImprovementKind.RELIABILITY.value,
                    reason=(
                        "ReminderWatcher marks delivered before ProactiveManager speaks; "
                        "quiet-hours/busy suppress can drop the reminder permanently"
                    ),
                    score=80.0,
                    fingerprint=_fp("fix", "reminder_mark_after_speech"),
                    source="repository",
                    files_hint=[
                        "watchers/reminders.py",
                        "core/proactive.py",
                    ],
                    metadata={"op": "fix_reminder_mark_after_speech"},
                )
            )

        # APP exclusion: "otwórz X nie Y" must not pick Y via longest-match
        intent_family = self.root / "core" / "intent_family.py"
        if intent_family.exists():
            try:
                if_txt = intent_family.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                if_txt = ""
            if (
                "def _find_app_name" in if_txt
                and r'\bnie\b' not in if_txt
                and "split" not in if_txt[if_txt.find("def _find_app_name") : if_txt.find("def _find_app_name") + 400]
            ):
                out.append(
                    ImprovementCandidate(
                        id="fix:app_nie_exclusion",
                        title="Honor 'nie' exclusion when selecting open_app target",
                        kind=ImprovementKind.BUG_FIX.value,
                        reason=(
                            "Agent-smoke: 'Otwórz Spotify nie YouTube Music' opens YouTube Music "
                            "because _find_app_name longest-matches the excluded app"
                        ),
                        score=88.0,
                        fingerprint=_fp("fix", "app_nie_exclusion"),
                        source="repository",
                        files_hint=["core/intent_family.py"],
                        metadata={"op": "fix_app_nie_exclusion"},
                    )
                )
        return out

    def _from_failing_tests(self) -> List[ImprovementCandidate]:
        out: List[ImprovementCandidate] = []
        marker = self.root / "Data" / "self_improve" / "last_test_failures.json"
        if not marker.exists():
            return out
        try:
            raw = json.loads(marker.read_text(encoding="utf-8"))
        except Exception:
            return out
        failures = raw.get("failures") if isinstance(raw, dict) else None
        if not isinstance(failures, list):
            return out
        for item in failures[:15]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("test") or item.get("id") or "").strip()
            if not name:
                continue
            out.append(
                ImprovementCandidate(
                    id=f"testfail:{name}",
                    title=f"Fix failing test: {name}",
                    kind=ImprovementKind.BUG_FIX.value,
                    reason=str(item.get("message") or "failing test"),
                    score=95.0,
                    fingerprint=_fp("testfail", name),
                    source="tests",
                    files_hint=[str(x) for x in item.get("files") or []],
                    metadata=item,
                )
            )
        return out

    def build_architecture_summary(self) -> Dict[str, Any]:
        key_paths = {
            "entry": "main.py",
            "conversation": "core/conversation_manager.py",
            "planner": "core/planner.py",
            "tools": "core/tools.py",
            "builtin_tools": "core/builtin_tools.py",
            "semantic_executive": "core/semantic_executive.py",
            "self_improve": "core/self_improve/",
            "self_development": "core/self_improve/self_development/",
            "config": "Data/config.json",
            "tests": "tests/",
            "learned_tools": "core/self_improve/learned/",
        }
        present = {}
        for k, rel in key_paths.items():
            p = self.root / rel
            present[k] = {
                "path": rel,
                "exists": p.exists(),
                "is_dir": p.is_dir() if p.exists() else False,
            }
        return {
            "entry_points": ["main.py", "core/self_improve/cli.py"],
            "paths": present,
            "routing_order": [
                "session",
                "memory",
                "utterance",
                "semantic_executive",
                "cognitive_core",
                "goal_action_loop",
                "agent_planner",
                "fastpath",
                "legacy",
                "brain",
            ],
        }


def make_synthetic_candidates(
    *,
    mode: str,
    sandbox_rel: str,
) -> List[ImprovementCandidate]:
    if mode == "pass":
        return [
            ImprovementCandidate(
                id="synthetic:pass",
                title="Synthetic successful improvement",
                kind=ImprovementKind.SYNTHETIC.value,
                reason="Controlled harness: write passing sandbox module",
                score=999.0,
                fingerprint=_fp("synthetic", "pass"),
                source="synthetic",
                files_hint=[sandbox_rel],
                metadata={"synthetic_mode": "pass", "sandbox": sandbox_rel},
            )
        ]
    if mode == "fail":
        return [
            ImprovementCandidate(
                id="synthetic:fail",
                title="Synthetic failing improvement",
                kind=ImprovementKind.SYNTHETIC.value,
                reason="Controlled harness: write broken sandbox module",
                score=999.0,
                fingerprint=_fp("synthetic", "fail"),
                source="synthetic",
                files_hint=[sandbox_rel],
                metadata={"synthetic_mode": "fail", "sandbox": sandbox_rel},
            )
        ]
    return []


def prioritize(candidates: List[ImprovementCandidate]) -> Optional[ImprovementCandidate]:
    """Pick ONE primary improvement (highest score). Skip quarantined entirely."""
    if not candidates:
        return None
    active = [c for c in candidates if not c.quarantined and c.score >= 1.0]
    if not active:
        return None
    return sorted(active, key=lambda c: (-c.score, c.id))[0]
