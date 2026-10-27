# ==========================
# ELIAN — Test gates for autonomous development
# ==========================
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import List, Optional, Sequence

from .types import TestRunResult


# Critical self-improvement suite — must not be disabled by the engine
CRITICAL_SELF_IMPROVE = [
    "tests.test_runtime_experience_log",
    "tests.test_vague_answer_experience",
    "tests.test_reflection_tool_evidence",
    "tests.test_supervised_dry_run",
    "tests.test_self_improve",
    "tests.test_conversation_phase8",
]


class TestGate:
    """Run targeted + regression + smoke gates with timeouts."""

    def __init__(
        self,
        project_root: Path,
        *,
        timeout_s: float = 180.0,
        python_exe: str = "python",
    ) -> None:
        self.root = Path(project_root).resolve()
        self.timeout_s = float(timeout_s)
        self.python = python_exe

    def run_unittest(self, modules: Sequence[str], *, label: str = "unittest") -> TestRunResult:
        mods = [m for m in modules if m]
        if not mods:
            return TestRunResult(
                passed=True,
                command="(none)",
                exit_code=0,
                summary="no targeted tests",
            )
        # Prefer -q quiet; fall back per-module if discover-style list fails
        cmd = [self.python, "-m", "unittest", *mods, "-q"]
        return self._run(cmd, label=label)

    def run_agent_smoke(self) -> TestRunResult:
        cmd = [self.python, "main.py", "--agent-smoke"]
        return self._run(cmd, label="agent-smoke", timeout_s=max(self.timeout_s, 240.0))

    def run_critical_self_improve(self) -> TestRunResult:
        return self.run_unittest(CRITICAL_SELF_IMPROVE, label="self-improve-critical")

    def run_syntax_check(self, rel_paths: Sequence[str]) -> TestRunResult:
        failed: List[str] = []
        for rel in rel_paths:
            path = self.root / rel
            if not path.is_file() or not str(rel).endswith(".py"):
                continue
            cmd = [self.python, "-m", "py_compile", str(path)]
            r = self._run(cmd, label=f"py_compile:{rel}", timeout_s=30.0)
            if not r.passed:
                failed.append(rel)
        return TestRunResult(
            passed=not failed,
            command="py_compile",
            exit_code=0 if not failed else 1,
            summary="ok" if not failed else f"syntax fail: {', '.join(failed)}",
        )

    def evaluate_cycle(
        self,
        *,
        files_changed: Sequence[str],
        test_targets: Sequence[str],
        high_risk_self_dev: bool,
        skip_agent_smoke: bool = False,
        synthetic_mode: Optional[str] = None,
    ) -> tuple[bool, List[TestRunResult], str]:
        """
        Returns (accepted, runs, reason).

        Policy:
        - targeted / syntax fail -> rollback
        - critical self-improve fail -> rollback
        - agent-smoke fail -> rollback (unless skipped for synthetic harness)
        - synthetic fail mode: treat as failed if sandbox file has syntax error
        """
        runs: List[TestRunResult] = []

        if synthetic_mode == "fail":
            # Harness: broken file must fail compile → rollback
            syn = self.run_syntax_check(files_changed)
            runs.append(syn)
            if not syn.passed:
                return False, runs, "synthetic failure: syntax gate failed (expected)"
            return False, runs, "synthetic failure mode without syntax break"

        if synthetic_mode == "pass":
            syn = self.run_syntax_check(files_changed)
            runs.append(syn)
            if not syn.passed:
                return False, runs, syn.summary
            # Lightweight: also ensure sandbox imports
            for rel in files_changed:
                if not str(rel).endswith(".py"):
                    continue
                mod = str(rel).replace("\\", "/").replace("/", ".").removesuffix(".py")
                # Data/... is not a package — compile-only is enough
                break
            return True, runs, "synthetic pass"

        syn = self.run_syntax_check(files_changed)
        runs.append(syn)
        if not syn.passed:
            return False, runs, syn.summary

        if test_targets:
            targeted = self.run_unittest(list(test_targets), label="targeted")
            runs.append(targeted)
            if not targeted.passed:
                return False, runs, targeted.summary

        if high_risk_self_dev or any(
            "self_improve/self_development" in f.replace("\\", "/") for f in files_changed
        ):
            crit = self.run_critical_self_improve()
            runs.append(crit)
            if not crit.passed:
                return False, runs, "critical self-improve suite failed"
            if not skip_agent_smoke:
                smoke = self.run_agent_smoke()
                runs.append(smoke)
                if not smoke.passed:
                    return False, runs, "agent-smoke failed (high-risk self-dev)"
            return True, runs, "high-risk gates passed"

        # Standard real change: self-improve subset + smoke
        crit = self.run_unittest(
            [
                "tests.test_self_improve",
                "tests.test_supervised_dry_run",
            ],
            label="regression-lite",
        )
        runs.append(crit)
        if not crit.passed:
            return False, runs, crit.summary

        if not skip_agent_smoke:
            smoke = self.run_agent_smoke()
            runs.append(smoke)
            if not smoke.passed:
                return False, runs, "agent-smoke failed"

        return True, runs, "accepted"

    def _run(
        self,
        cmd: List[str],
        *,
        label: str,
        timeout_s: Optional[float] = None,
    ) -> TestRunResult:
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=timeout_s or self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return TestRunResult(
                passed=False,
                command=" ".join(cmd),
                exit_code=124,
                summary=f"{label}: timed out",
                duration_s=round(time.monotonic() - t0, 3),
            )
        except Exception as exc:
            return TestRunResult(
                passed=False,
                command=" ".join(cmd),
                exit_code=1,
                summary=f"{label}: {exc}",
                duration_s=round(time.monotonic() - t0, 3),
            )
        out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        tail = " | ".join(line.strip() for line in out.splitlines()[-4:] if line.strip())
        summary = f"{label}: exit={proc.returncode}"
        if tail:
            summary = f"{summary} :: {tail[:300]}"
        return TestRunResult(
            passed=proc.returncode == 0,
            command=" ".join(cmd),
            exit_code=int(proc.returncode),
            summary=summary,
            duration_s=round(time.monotonic() - t0, 3),
        )
