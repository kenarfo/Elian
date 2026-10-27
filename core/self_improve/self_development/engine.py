# ==========================
# ELIAN — Autonomous Self-Development Engine
# ==========================
from __future__ import annotations

import os
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from .checkpoint import CheckpointManager
from .discovery import ImprovementDiscovery, make_synthetic_candidates, prioritize
from .evaluate import TestGate
from .history import DevelopmentHistoryStore, default_history_path
from .implement import ImplementationEngine
from .state import DevelopmentStateStore, default_state_path
from .types import (
    CycleRecord,
    CycleResult,
    ImprovementCandidate,
    Phase,
)


def _log(cycle: int, msg: str) -> None:
    # ASCII-only for Windows consoles
    print(f"[SELF-DEV][CYCLE {cycle}] {msg}", flush=True)


class SelfDevelopmentEngine:
    """
    Continuous autonomous engineering loop:

    OBSERVE -> ANALYZE -> PRIORITIZE -> PLAN -> IMPLEMENT -> TEST
    -> EVALUATE -> KEEP/ROLLBACK -> LEARN -> NEXT
    """

    def __init__(
        self,
        project_root: Optional[Path] = None,
        *,
        state_path: Optional[Path] = None,
        history_path: Optional[Path] = None,
        max_repair_attempts: int = 3,
        test_timeout: float = 180.0,
        max_cycles: Optional[int] = None,
        synthetic_mode: Optional[str] = None,
        skip_agent_smoke: bool = False,
        include_runtime: bool = True,
        include_repo: bool = True,
        sleep_between_cycles_s: float = 0.0,
    ) -> None:
        self.root = Path(project_root or Path(__file__).resolve().parents[3]).resolve()
        self.state = DevelopmentStateStore(state_path or default_state_path(self.root))
        self.history = DevelopmentHistoryStore(
            history_path or default_history_path(self.root)
        )
        self.checkpoints = CheckpointManager(self.root)
        self.discovery = ImprovementDiscovery(self.root)
        self.implementer = ImplementationEngine(self.root, self.checkpoints)
        self.tests = TestGate(self.root, timeout_s=test_timeout)
        self.max_repair_attempts = max(1, int(max_repair_attempts))
        self.max_cycles = max_cycles
        self.synthetic_mode = synthetic_mode
        self.skip_agent_smoke = bool(skip_agent_smoke)
        self.include_runtime = include_runtime
        self.include_repo = include_repo
        self.sleep_between_cycles_s = float(sleep_between_cycles_s)

    # ---- public API -------------------------------------------------
    def run(self) -> int:
        """Enter continuous loop until stop / unrecoverable / max_cycles."""
        st = self.state.load()
        if st.get("incomplete_cycle") and st.get("last_checkpoint"):
            self._recover_interrupted(st)

        self.state.update(
            mode="self-develop",
            running=True,
            stop_requested=False,
            pid=os.getpid(),
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            unrecoverable_reason="",
            current_phase=Phase.OBSERVE.value,
        )
        _log(0, "Autonomous self-development started")
        cycles_this_run = 0
        try:
            while True:
                st = self.state.load()
                if st.get("stop_requested"):
                    self._set_phase(Phase.STOPPED)
                    _log(int(st.get("current_cycle") or 0), "Stop requested - finishing")
                    break
                if self.max_cycles is not None and cycles_this_run >= self.max_cycles:
                    _log(cycles_this_run, f"max_cycles={self.max_cycles} reached")
                    break
                try:
                    self.run_one_cycle()
                except UnrecoverableError as exc:
                    self.state.update(
                        running=False,
                        mode="unrecoverable",
                        current_phase=Phase.UNRECOVERABLE.value,
                        unrecoverable_reason=str(exc)[:500],
                        incomplete_cycle=False,
                    )
                    _log(0, f"UNRECOVERABLE: {exc}")
                    return 2
                except Exception as exc:
                    _log(0, f"Cycle error (continuing): {exc}")
                    traceback.print_exc()
                    self._record_stats(success=False, rolled_back=False, duration_s=0.0)
                cycles_this_run += 1
                if self.sleep_between_cycles_s > 0:
                    time.sleep(self.sleep_between_cycles_s)
        finally:
            self.state.update(
                running=False,
                mode="stopped",
                current_phase=Phase.STOPPED.value,
                pid=None,
            )
            _log(cycles_this_run, "Autonomous self-development stopped")
        return 0

    def run_one_cycle(self) -> CycleResult:
        t0 = time.monotonic()
        st = self.state.load()
        cycle_num = int(st.get("last_completed_cycle") or 0) + 1
        cycle_id = f"c{cycle_num}"
        self.state.update(
            current_cycle=cycle_num,
            incomplete_cycle=True,
            current_phase=Phase.OBSERVE.value,
            current_candidate="",
        )

        # OBSERVE
        self._set_phase(Phase.OBSERVE, cycle=cycle_num)
        arch = st.get("architecture_summary") or {}
        if not arch:
            arch = self.discovery.build_architecture_summary()
            self.state.update(architecture_summary=arch)
        _log(cycle_num, "OBSERVE: repository + architecture summary ready")

        # ANALYZE
        self._set_phase(Phase.ANALYZE, cycle=cycle_num)
        synthetic = None
        if self.synthetic_mode:
            synthetic = make_synthetic_candidates(
                mode=self.synthetic_mode,
                sandbox_rel=self.implementer.sandbox_rel,
            )
        candidates = self.discovery.discover(
            include_runtime=self.include_runtime and not self.synthetic_mode,
            include_repo=self.include_repo and not self.synthetic_mode,
            include_tests=not self.synthetic_mode,
            synthetic=synthetic,
            quarantine=st.get("quarantine") or {},
        )
        _log(cycle_num, f"ANALYZE: {len(candidates)} candidate(s)")

        # PRIORITIZE
        self._set_phase(Phase.PRIORITIZE, cycle=cycle_num)
        candidate = prioritize(candidates)
        if candidate is None:
            _log(cycle_num, "NO_PROGRESS: no candidates")
            self._finish_cycle(
                cycle_id,
                cycle_num,
                candidate=None,
                result=CycleResult.NO_PROGRESS,
                reason="no candidates",
                files=[],
                tests=[],
                duration_s=time.monotonic() - t0,
            )
            return CycleResult.NO_PROGRESS

        self.state.update(
            current_candidate=candidate.title,
            current_candidate_fingerprint=candidate.fingerprint,
        )
        _log(cycle_num, f"Candidate: {candidate.title}")
        _log(cycle_num, f"Reason: {candidate.reason}")

        # PLAN
        self._set_phase(Phase.PLAN, cycle=cycle_num)
        plan = self.implementer.plan(candidate)
        if plan.actions and plan.actions[0].get("op") == "noop":
            self._finish_cycle(
                cycle_id,
                cycle_num,
                candidate=candidate,
                result=CycleResult.NO_PROGRESS,
                reason="noop plan",
                files=[],
                tests=[],
                duration_s=time.monotonic() - t0,
            )
            return CycleResult.NO_PROGRESS

        # Checkpoint
        try:
            checkpoint = self.checkpoints.create(
                cycle_id,
                tracked_files=list(candidate.files_hint),
            )
        except Exception as exc:
            raise UnrecoverableError(f"Cannot create checkpoint: {exc}") from exc
        self.state.update(last_checkpoint=checkpoint.checkpoint_id)
        _log(cycle_num, f"Checkpoint: {checkpoint.checkpoint_id} ({checkpoint.strategy})")

        files_changed: List[str] = []
        tests_run: List[str] = []
        last_err = ""
        accepted = False
        accept_reason = ""

        for attempt in range(1, self.max_repair_attempts + 1):
            # IMPLEMENT
            self._set_phase(Phase.IMPLEMENT, cycle=cycle_num)
            _log(cycle_num, f"IMPLEMENT attempt {attempt}/{self.max_repair_attempts}")
            # Rollback previous attempt residue before retry
            if attempt > 1:
                self.checkpoints.rollback(checkpoint)
                files_changed = []
            changed, err = self.implementer.apply(plan, checkpoint, candidate=candidate)
            files_changed = list(changed)
            if err.startswith("NO_PROGRESS"):
                self.checkpoints.rollback(checkpoint)
                self._finish_cycle(
                    cycle_id,
                    cycle_num,
                    candidate=candidate,
                    result=CycleResult.NO_PROGRESS,
                    reason=err,
                    files=[],
                    tests=[],
                    duration_s=time.monotonic() - t0,
                    checkpoint_id=checkpoint.checkpoint_id,
                    attempts=attempt,
                )
                return CycleResult.NO_PROGRESS
            if err:
                last_err = err
                _log(cycle_num, f"Implement/review failed: {err}")
                continue
            if files_changed:
                _log(cycle_num, f"Action: modify {', '.join(files_changed)}")

            # TEST + EVALUATE
            self._set_phase(Phase.TEST, cycle=cycle_num)
            syn_mode = None
            if candidate.source == "synthetic":
                syn_mode = str((candidate.metadata or {}).get("synthetic_mode") or "")
            accepted, runs, accept_reason = self.tests.evaluate_cycle(
                files_changed=files_changed,
                test_targets=plan.test_targets,
                high_risk_self_dev=plan.high_risk_self_dev,
                skip_agent_smoke=self.skip_agent_smoke or bool(syn_mode),
                synthetic_mode=syn_mode,
            )
            tests_run = [r.command for r in runs]
            for r in runs:
                _log(cycle_num, f"Tests: {r.summary}")
            self._set_phase(Phase.EVALUATE, cycle=cycle_num)
            if accepted:
                break
            last_err = accept_reason
            _log(cycle_num, f"EVALUATE: reject ({accept_reason})")

        if not accepted:
            self._set_phase(Phase.ROLLBACK, cycle=cycle_num)
            restored = self.checkpoints.rollback(checkpoint)
            _log(cycle_num, f"Decision: ROLLBACK ({len(restored)} path(s))")
            self._quarantine_bump(candidate.fingerprint, last_err)
            self._finish_cycle(
                cycle_id,
                cycle_num,
                candidate=candidate,
                result=CycleResult.ROLLED_BACK,
                reason=f"{last_err}; fingerprint={candidate.fingerprint}",
                files=files_changed,
                tests=tests_run,
                duration_s=time.monotonic() - t0,
                checkpoint_id=checkpoint.checkpoint_id,
                attempts=self.max_repair_attempts,
                rolled_back=True,
            )
            return CycleResult.ROLLED_BACK

        # KEEP
        self._set_phase(Phase.KEEP, cycle=cycle_num)
        commit = self.checkpoints.keep(
            checkpoint,
            message=f"self-dev/{cycle_id}: {candidate.title[:60]}",
        )
        _log(cycle_num, f"Decision: KEEP{(' Commit: ' + commit) if commit else ''}")
        self._finish_cycle(
            cycle_id,
            cycle_num,
            candidate=candidate,
            result=CycleResult.KEPT,
            reason=accept_reason or "accepted",
            files=files_changed,
            tests=tests_run,
            duration_s=time.monotonic() - t0,
            checkpoint_id=checkpoint.checkpoint_id,
            attempts=1,
            success=True,
        )
        # LEARN
        self._set_phase(Phase.LEARN, cycle=cycle_num)
        self._set_phase(Phase.NEXT, cycle=cycle_num)
        _log(cycle_num, "Next cycle...")
        return CycleResult.KEPT

    # ---- helpers ----------------------------------------------------
    def _set_phase(self, phase: Phase, *, cycle: Optional[int] = None) -> None:
        kw: Dict[str, Any] = {"current_phase": phase.value}
        if cycle is not None:
            kw["current_cycle"] = cycle
        self.state.update(**kw)

    def _finish_cycle(
        self,
        cycle_id: str,
        cycle_num: int,
        *,
        candidate: Optional[ImprovementCandidate],
        result: CycleResult,
        reason: str,
        files: List[str],
        tests: List[str],
        duration_s: float,
        checkpoint_id: str = "",
        attempts: int = 1,
        success: bool = False,
        rolled_back: bool = False,
    ) -> None:
        title = candidate.title if candidate else ""
        self.history.append(
            CycleRecord(
                cycle_id=cycle_id,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                phase=Phase.LEARN.value,
                candidate=title,
                reason=reason[:500],
                files_changed=files,
                tests_run=tests,
                result=result.value,
                failure_reason=reason[:500] if result != CycleResult.KEPT else "",
                checkpoint_id=checkpoint_id,
                attempts=attempts,
            )
        )
        updates: Dict[str, Any] = {
            "last_completed_cycle": cycle_num,
            "last_result": result.value,
            "incomplete_cycle": False,
            "last_checkpoint": checkpoint_id,
            "current_candidate": title,
        }
        if success:
            updates["last_success"] = title
        if result == CycleResult.ROLLED_BACK:
            updates["last_failure"] = title
        self.state.update(**updates)
        self._record_stats(
            success=success,
            rolled_back=rolled_back or result == CycleResult.ROLLED_BACK,
            no_progress=result == CycleResult.NO_PROGRESS,
            duration_s=duration_s,
            files_n=len(files),
        )

    def _record_stats(
        self,
        *,
        success: bool,
        rolled_back: bool,
        duration_s: float,
        no_progress: bool = False,
        files_n: int = 0,
    ) -> None:
        st = self.state.load()
        stats = dict(st.get("statistics") or {})
        stats["cycles_completed"] = int(stats.get("cycles_completed") or 0) + 1
        if success:
            stats["successful_cycles"] = int(stats.get("successful_cycles") or 0) + 1
            stats["files_changed"] = int(stats.get("files_changed") or 0) + files_n
            stats["features_added"] = int(stats.get("features_added") or 0) + 1
        if rolled_back:
            stats["failed_cycles"] = int(stats.get("failed_cycles") or 0) + 1
            stats["rollback_count"] = int(stats.get("rollback_count") or 0) + 1
        if no_progress:
            stats["no_progress_count"] = int(stats.get("no_progress_count") or 0) + 1
        total_d = float(stats.get("total_cycle_duration_s") or 0.0) + float(duration_s)
        stats["total_cycle_duration_s"] = round(total_d, 3)
        done = max(1, int(stats["cycles_completed"]))
        stats["average_cycle_duration_s"] = round(total_d / done, 3)
        succ = int(stats.get("successful_cycles") or 0)
        stats["success_rate"] = round(succ / done, 3)
        # quarantine list
        q = st.get("quarantine") or {}
        repeated = [
            fp
            for fp, row in q.items()
            if isinstance(row, dict) and int(row.get("failures") or 0) >= 3
        ]
        stats["repeated_failure_candidates"] = repeated[:20]
        self.state.update(statistics=stats)

    def _quarantine_bump(self, fingerprint: str, reason: str) -> None:
        st = self.state.load()
        q = dict(st.get("quarantine") or {})
        row = dict(q.get(fingerprint) or {})
        row["failures"] = int(row.get("failures") or 0) + 1
        row["last_result"] = reason[:300]
        row["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        q[fingerprint] = row
        self.state.update(quarantine=q)

    def _recover_interrupted(self, st: Dict[str, Any]) -> None:
        """Rollback unfinished mutation if checkpoint exists."""
        cid = str(st.get("last_checkpoint") or "")
        if not cid:
            self.state.update(incomplete_cycle=False)
            return
        meta_path = (
            self.root
            / "Data"
            / "self_improve"
            / "checkpoints"
            / cid
            / "checkpoint.json"
        )
        if not meta_path.exists():
            self.state.update(incomplete_cycle=False)
            return
        try:
            import json
            from .checkpoint import Checkpoint

            cp = Checkpoint.from_dict(json.loads(meta_path.read_text(encoding="utf-8")))
            if cp and not (cp.to_dict().get("kept")):
                restored = self.checkpoints.rollback(cp)
                _log(0, f"Recovered interrupted cycle; restored {len(restored)} path(s)")
        except Exception as exc:
            _log(0, f"Recovery warning: {exc}")
        self.state.update(incomplete_cycle=False, last_result="INTERRUPTED_RECOVERED")


class UnrecoverableError(RuntimeError):
    """Stop the loop — do not continue blindly."""


def load_config(project_root: Optional[Path] = None) -> Dict[str, Any]:
    """Read Data/config.json self_improve.autonomous block with defaults."""
    root = Path(project_root or Path(__file__).resolve().parents[3])
    defaults = {
        "enabled": False,
        "max_repair_attempts": 3,
        "test_timeout": 180.0,
        "checkpoint_strategy": "file",
        "development_state_path": str(default_state_path(root)),
        "history_path": str(default_history_path(root)),
        "sleep_between_cycles_s": 1.0,
    }
    block: Dict[str, Any] = {}
    try:
        from Config import get_config_value

        raw_block = get_config_value("self_improve.autonomous", None)
        if isinstance(raw_block, dict):
            block = dict(raw_block)
    except Exception:
        cfg_path = root / "Data" / "config.json"
        if cfg_path.exists():
            try:
                import json

                raw = json.loads(cfg_path.read_text(encoding="utf-8"))
                raw_block = ((raw.get("self_improve") or {}).get("autonomous")) or {}
                if isinstance(raw_block, dict):
                    block = dict(raw_block)
            except Exception:
                pass
    merged = dict(defaults)
    merged.update(block)
    # Resolve relative paths against project root
    for key in ("development_state_path", "history_path"):
        p = Path(str(merged[key]))
        if not p.is_absolute():
            merged[key] = str((root / p).resolve())
    return merged
