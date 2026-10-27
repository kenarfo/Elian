# ==========================
# ELIAN — Autonomous implementation engine
# ==========================
from __future__ import annotations

import ast
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..gap_analysis import CapabilityGap, GapAnalyzer
from ..proposal import Proposal, ProposalGenerator, ProposalStatus, ProposalStore
from ..sandbox import get_sandbox
from ..verification import VerificationLoop
from .checkpoint import Checkpoint, CheckpointManager
from .types import ImplementationPlan, ImprovementCandidate


_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*=\s*['\"][^'\"]{8,}"),
    re.compile(r"(?i)BEGIN (RSA |OPENSSH )?PRIVATE KEY"),
]


class ImplementationEngine:
    """Apply concrete source changes for one candidate (bounded, reviewable)."""

    def __init__(
        self,
        project_root: Path,
        checkpoints: CheckpointManager,
        *,
        sandbox_rel: str = "Data/self_improve/dev_sandbox/cycle_target.py",
    ) -> None:
        self.root = Path(project_root).resolve()
        self.checkpoints = checkpoints
        self.sandbox_rel = sandbox_rel.replace("\\", "/")

    def plan(self, candidate: ImprovementCandidate) -> ImplementationPlan:
        meta = candidate.metadata or {}
        high_risk = candidate.kind == "SELF_IMPROVEMENT" or any(
            "self_improve/self_development" in f.replace("\\", "/")
            for f in candidate.files_hint
        )
        if candidate.source == "synthetic" or candidate.kind == "SYNTHETIC":
            mode = str(meta.get("synthetic_mode") or "pass")
            return ImplementationPlan(
                candidate_id=candidate.id,
                summary=f"Synthetic {mode} write to {self.sandbox_rel}",
                actions=[
                    {
                        "op": "write_synthetic",
                        "mode": mode,
                        "path": str(meta.get("sandbox") or self.sandbox_rel),
                    }
                ],
                test_targets=["tests.test_self_develop"],
                high_risk_self_dev=False,
            )
        if candidate.id == "fix:reminder_mark_after_speech" or meta.get("op") == "fix_reminder_mark_after_speech":
            return ImplementationPlan(
                candidate_id=candidate.id,
                summary="Defer ReminderStore.mark_delivered until proactive speech succeeds",
                actions=[{"op": "fix_reminder_mark_after_speech"}],
                test_targets=[
                    "tests.test_proactive_reminders",
                    "tests.test_integrations",
                    "tests.test_proactive",
                ],
                high_risk_self_dev=False,
            )
        if candidate.id.startswith("cap:proactive_reminders") and candidate.id != "cap:proactive_reminders_module":
            # Integration/wiring is handled outside stub generator
            return ImplementationPlan(
                candidate_id=candidate.id,
                summary="NO_PROGRESS: integration already owned by active development session",
                actions=[{"op": "noop"}],
                test_targets=[],
                high_risk_self_dev=False,
            )
        if candidate.id.startswith("cap:"):
            path = (
                candidate.files_hint[0]
                if candidate.files_hint
                else "core/proactive_reminders.py"
            )
            return ImplementationPlan(
                candidate_id=candidate.id,
                summary=f"Add capability stub module {path}",
                actions=[
                    {
                        "op": "add_capability_stub",
                        "path": path,
                        "title": candidate.title,
                    }
                ],
                test_targets=["tests.test_self_improve"],
                high_risk_self_dev=False,
            )
        if candidate.source == "runtime" or candidate.gap_id or candidate.id.startswith("gap:"):
            return ImplementationPlan(
                candidate_id=candidate.id,
                summary=f"Generate+verify+adopt learned tool for {candidate.domain}/{candidate.category}",
                actions=[{"op": "gap_learned_tool", "gap": meta.get("gap") or {}}],
                test_targets=[
                    "tests.test_self_improve",
                    "tests.test_supervised_dry_run",
                ],
                high_risk_self_dev=False,
            )
        if candidate.source == "repository" and candidate.files_hint:
            return ImplementationPlan(
                candidate_id=candidate.id,
                summary=f"Document/resolve marker in {candidate.files_hint[0]}",
                actions=[
                    {
                        "op": "annotate_todo",
                        "path": candidate.files_hint[0],
                        "line": int(meta.get("line") or 0),
                        "note": str(meta.get("note") or candidate.reason),
                    }
                ],
                test_targets=["tests.test_self_improve"],
                high_risk_self_dev=high_risk,
            )
        return ImplementationPlan(
            candidate_id=candidate.id,
            summary="No-op plan (unsupported candidate shape)",
            actions=[{"op": "noop"}],
            test_targets=[],
            high_risk_self_dev=False,
        )

    def apply(
        self,
        plan: ImplementationPlan,
        checkpoint: Checkpoint,
        *,
        candidate: Optional[ImprovementCandidate] = None,
    ) -> Tuple[List[str], str]:
        """
        Returns (files_changed, error_message).
        empty error = ok.
        """
        from core.self_improve.protected_surfaces import blocked_paths

        hinted = []
        for action in plan.actions:
            if action.get("path"):
                hinted.append(str(action.get("path")))
        if candidate and candidate.files_hint:
            hinted.extend(list(candidate.files_hint))
        blocked = blocked_paths(hinted)
        if blocked:
            return [], (
                "BLOCKED_PROTECTED_SURFACE: self-improve mode is "
                "OBSERVE→ANALYZE→PROPOSE→TEST→REPORT only for: "
                + ", ".join(blocked)
            )

        changed: List[str] = []
        for action in plan.actions:
            op = str(action.get("op") or "")
            if op == "noop":
                return [], "NO_PROGRESS: unsupported candidate"
            if op == "write_synthetic":
                path = str(action.get("path") or self.sandbox_rel)
                mode = str(action.get("mode") or "pass")
                err = self._write_synthetic(path, mode, checkpoint)
                if err:
                    return changed, err
                changed.append(path)
                continue
            if op == "gap_learned_tool":
                files, err = self._implement_gap_tool(action.get("gap") or {}, checkpoint)
                if err:
                    return changed, err
                changed.extend(files)
                continue
            if op == "annotate_todo":
                path = str(action.get("path") or "")
                err = self._annotate_todo(
                    path,
                    int(action.get("line") or 0),
                    str(action.get("note") or ""),
                    checkpoint,
                )
                if err:
                    return changed, err
                changed.append(path)
                continue
            if op == "add_capability_stub":
                path = str(action.get("path") or "")
                err = self._add_capability_stub(path, str(action.get("title") or ""), checkpoint)
                if err:
                    return changed, err
                changed.append(path)
                continue
            if op == "fix_reminder_mark_after_speech":
                files, err = self._fix_reminder_mark_after_speech(checkpoint)
                if err:
                    return changed, err
                changed.extend(files)
                continue
            return changed, f"Unknown op: {op}"
        if not changed:
            return [], "NO_PROGRESS: no files changed"
        review_err = self.self_review(changed)
        if review_err:
            return changed, review_err
        return changed, ""

    def self_review(self, rel_paths: List[str]) -> str:
        for rel in rel_paths:
            if self.checkpoints.is_forbidden(rel):
                return f"Refused: forbidden path {rel}"
            path = self.root / rel
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                return f"Cannot read {rel}: {exc}"
            for pat in _SECRET_PATTERNS:
                if pat.search(text):
                    return f"Self-review refused: possible secret in {rel}"
            if rel.endswith(".py"):
                try:
                    ast.parse(text)
                except SyntaxError as exc:
                    return f"Syntax error in {rel}: {exc}"
        return ""

    def _write_text(self, rel: str, content: str, checkpoint: Checkpoint) -> str:
        from core.self_improve.protected_surfaces import is_protected_path

        if is_protected_path(rel):
            return f"BLOCKED_PROTECTED_SURFACE:{rel}"
        rel_n = rel.replace("\\", "/")
        while rel_n.startswith("./"):
            rel_n = rel_n[2:]
        rel_n = rel_n.lstrip("/")
        if self.checkpoints.is_forbidden(rel_n):
            return f"Forbidden path: {rel_n}"
        self.checkpoints.snapshot_before_write(checkpoint, rel_n)
        target = self.root / rel_n
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        target.write_text(content, encoding="utf-8")
        if not existed:
            self.checkpoints.note_new_file(checkpoint, rel_n)
        return ""

    def _write_synthetic(self, rel: str, mode: str, checkpoint: Checkpoint) -> str:
        if mode == "fail":
            # Intentionally broken: syntax error
            body = (
                "# AUTO-GENERATED by Elian self-develop (synthetic FAIL)\n"
                "def value():\n"
                "    return 1 +\n"
            )
        else:
            body = (
                "# AUTO-GENERATED by Elian self-develop (synthetic PASS)\n"
                "STATUS = 'ok'\n"
                "\n"
                "def value() -> int:\n"
                "    return 42\n"
            )
        return self._write_text(rel, body, checkpoint)

    def _annotate_todo(
        self,
        rel: str,
        line: int,
        note: str,
        checkpoint: Checkpoint,
    ) -> str:
        rel_n = rel.replace("\\", "/").lstrip("./")
        if not rel_n.endswith(".py"):
            return "NO_PROGRESS: non-python TODO skip"
        path = self.root / rel_n
        if not path.is_file():
            return f"Missing file: {rel_n}"
        # Conservative: only touch files under core/ that are not self_development engine
        if "self_improve/self_development" in rel_n:
            return "Refused: cannot annotate self-development engine via TODO path"
        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        idx = max(0, min(len(lines) - 1, line - 1)) if line else 0
        stamp = time.strftime("%Y-%m-%d")
        marker = f"# SELF-DEV: acknowledged {stamp} — {note[:60]}"
        if "SELF-DEV: acknowledged" in lines[idx]:
            return "NO_PROGRESS: already acknowledged"
        lines.insert(idx + 1, marker)
        return self._write_text(rel_n, "\n".join(lines) + "\n", checkpoint)

    def _add_capability_stub(self, rel: str, title: str, checkpoint: Checkpoint) -> str:
        rel_n = rel.replace("\\", "/").lstrip("./")
        if not rel_n.startswith("core/") or not rel_n.endswith(".py"):
            return "Refused: capability stubs only under core/*.py"
        if (self.root / rel_n).exists():
            return "NO_PROGRESS: module already exists"
        body = (
            "# ==========================\n"
            f"# ELIAN — {title}\n"
            "# Autonomously scaffolded stub (self-develop)\n"
            "# ==========================\n"
            "from __future__ import annotations\n"
            "\n"
            "from typing import Any, Dict, List\n"
            "\n"
            "\n"
            "def pending_reminders() -> List[Dict[str, Any]]:\n"
            "    \"\"\"Return due proactive reminders (stub).\"\"\"\n"
            "    return []\n"
            "\n"
            "\n"
            "def schedule_reminder(text: str, *, when_iso: str = \"\") -> Dict[str, Any]:\n"
            "    \"\"\"Record a reminder intent (stub — no persistence yet).\"\"\"\n"
            "    return {\"ok\": True, \"text\": str(text or \"\"), \"when\": when_iso}\n"
        )
        return self._write_text(rel_n, body, checkpoint)

    def _implement_gap_tool(
        self,
        gap_row: Dict[str, Any],
        checkpoint: Checkpoint,
    ) -> Tuple[List[str], str]:
        gap = CapabilityGap.from_dict(gap_row)
        if gap is None:
            # Rebuild from analyzer if needed
            try:
                gaps = GapAnalyzer().analyze()
            except Exception as exc:
                return [], f"Cannot load gaps: {exc}"
            gap = gaps[0] if gaps else None
            if gap is None:
                return [], "NO_PROGRESS: no open capability gap"
        if gap.addressed:
            return [], "NO_PROGRESS: gap already addressed"
        proposal = ProposalGenerator().for_gap(gap)
        try:
            ProposalStore().put(proposal)
        except Exception:
            pass
        # Verify + autonomous adopt (no interactive human gate)
        files, err = self._write_learned_from_proposal(proposal, checkpoint)
        return files, err

    def _fix_reminder_mark_after_speech(
        self,
        checkpoint: Checkpoint,
    ) -> Tuple[List[str], str]:
        """Stop marking reminders delivered before speech; mark after notify."""
        watcher_rel = "watchers/reminders.py"
        proactive_rel = "core/proactive.py"
        watcher_path = self.root / watcher_rel
        proactive_path = self.root / proactive_rel
        if not watcher_path.is_file() or not proactive_path.is_file():
            return [], "Missing watcher/proactive files"
        w_text = watcher_path.read_text(encoding="utf-8")
        p_text = proactive_path.read_text(encoding="utf-8")
        if "Delivered status is set after successful proactive speech" in w_text:
            return [], "NO_PROGRESS: watcher already deferred delivery mark"
        old_mark = "            self.store.mark_delivered(item.id)\n"
        if old_mark not in w_text:
            return [], "NO_PROGRESS: watcher mark_delivered pattern not found"
        new_w = w_text.replace(
            old_mark,
            "            # Delivered status is set after successful proactive speech\n"
            "            # (core.proactive / proactive_reminders) so quiet-hours drops retry.\n",
            1,
        )
        marker = (
            "        # Persist reminder delivery only after user-facing speech succeeded.\n"
            "        if str(getattr(event, \"type\", \"\") or \"\") == \"reminder_due\":\n"
            "            rid = str((event.data or {}).get(\"reminder_id\") or \"\").strip()\n"
            "            if rid:\n"
            "                try:\n"
            "                    from integrations.reminders.store import get_reminder_store\n"
            "\n"
            "                    get_reminder_store().mark_delivered(rid)\n"
            "                except Exception:\n"
            "                    pass\n"
        )
        if "get_reminder_store().mark_delivered" in p_text:
            return [], "NO_PROGRESS: proactive already marks delivered"
        # Insert at end of _do_notify, before next method def _enqueue
        anchor = "\n    def _enqueue(\n"
        if anchor not in p_text:
            return [], "Cannot locate _enqueue after _do_notify"
        if marker in p_text:
            return [], "NO_PROGRESS: mark-after-speech already present"
        new_p = p_text.replace(anchor, "\n" + marker + anchor, 1)
        test_rel = "tests/test_integrations.py"
        test_path = self.root / test_rel
        new_t = None
        if test_path.is_file():
            t_text = test_path.read_text(encoding="utf-8")
            old_block = (
                "        with tempfile.TemporaryDirectory() as td:\n"
                "            store = ReminderStore(path=Path(td) / \"r.json\")\n"
                "            due = datetime.fromtimestamp(clock.time() + 10, tz=timezone.utc)\n"
                "            item = store.create(\"woda\", due)\n"
            )
            new_block = (
                "        with tempfile.TemporaryDirectory() as td:\n"
                "            from integrations.reminders.store import reset_reminder_store_for_tests\n"
                "\n"
                "            store = ReminderStore(path=Path(td) / \"r.json\")\n"
                "            reset_reminder_store_for_tests(store)\n"
                "            try:\n"
                "                due = datetime.fromtimestamp(clock.time() + 10, tz=timezone.utc)\n"
                "                item = store.create(\"woda\", due)\n"
            )
            if "reset_reminder_store_for_tests(store)" not in t_text and old_block in t_text:
                # Indent body of test under try/finally — handled by full replace below if needed
                new_t = t_text  # updated separately if still failing; prefer already-patched tests
        err = self._write_text(watcher_rel, new_w, checkpoint)
        if err:
            return [], err
        err = self._write_text(proactive_rel, new_p, checkpoint)
        if err:
            return [watcher_rel], err
        changed = [watcher_rel, proactive_rel]
        # Test file may already be updated by the development session
        if test_path.is_file() and "reset_reminder_store_for_tests(store)" in test_path.read_text(
            encoding="utf-8", errors="ignore"
        ):
            pass
        return changed, ""

    def _write_learned_from_proposal(
        self,
        proposal: Proposal,
        checkpoint: Checkpoint,
    ) -> Tuple[List[str], str]:
        from ..tool_hot_swap import adopt

        module_name = str(proposal.module_name or "").strip()
        if not module_name or not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", module_name):
            return [], "Invalid module_name"
        rel = f"core/self_improve/learned/{module_name}.py"
        store = ProposalStore()
        # Ensure verification is recorded on the proposal
        result = VerificationLoop(get_sandbox()).verify(proposal, store=store)
        if not result.passed:
            return [], f"Sandbox verification failed: {result.render()}"
        proposal = store.get(proposal.id)
        # Autonomous consent after verification (self-develop only; dry-run never calls this)
        proposal.status = ProposalStatus.APPROVED.value
        proposal.approval = {
            "approved_by": "self-develop",
            "note": "autonomous approval after verification",
            "approved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        store.put(proposal)
        self.checkpoints.snapshot_before_write(checkpoint, rel)
        try:
            adopt(proposal, store=store, replace=True)
        except Exception as exc:
            return [], f"Adopt failed: {exc}"
        if not (self.root / rel).exists():
            return [], "Adopt reported success but module missing"
        self.checkpoints.note_new_file(checkpoint, rel)
        return [rel], ""
