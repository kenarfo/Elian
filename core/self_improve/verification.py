# ==========================
# ELIAN — Self-Improvement / Verification Loop
# ==========================
"""Gate between 'generated code' and 'code the assistant may adopt'.

Checks (ordered, fail-closed — a required failure blocks adoption):
1. ``safety``         — static AST scan of module + tests
2. ``module_name``    — identifier hygiene / target stays in learned/
3. ``import``         — module imports in a scrubbed subprocess
4. ``unit_tests``     — generated unittest file, hard timeout
5. ``tool_contract``  — TOOL_SPEC shape, ``handle({})`` and ``handle(sample)``
6. ``action_contract``— every declared action reachable via its own schema,
                       one name per concept, returned dates honour the
                       requested weekdays (catches silent wrong data)
7. ``message_polish`` — no raw enum/abbreviation leakage in user-facing text
8. ``name_collision`` — tool name must not shadow an existing registry tool
9. ``core_smoke``     — advisory: ``import core.tools`` still works (non-blocking)

Result is stored on the proposal; only ``passed`` proposals can be approved.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

from core.self_improve.experience_log import utc_now_iso
from core.self_improve.proposal import Proposal, ProposalStatus, ProposalStore
from core.self_improve.sandbox import PROJECT_ROOT, ExecutionSandbox, get_sandbox

MODULE_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{1,63}$")

#: Names a generated module must never take (stdlib shadowing / project packages).
_STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()))
_FORBIDDEN_MODULE_NAMES = _STDLIB | {
    "core", "self_improve", "learned", "integrations", "memory", "modules", "tests",
}


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    required: bool = True
    skipped: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "required": self.required,
            "skipped": self.skipped,
        }

    @classmethod
    def from_dict(cls, row: Any) -> "Check":
        row = dict(row or {})
        return cls(
            name=str(row.get("name") or ""),
            passed=bool(row.get("passed")),
            detail=str(row.get("detail") or ""),
            required=bool(row.get("required", True)),
            skipped=bool(row.get("skipped")),
        )


@dataclass
class VerificationResult:
    proposal_id: str
    passed: bool
    checks: List[Check] = field(default_factory=list)
    started_at: str = ""
    duration_s: float = 0.0

    def check(self, name: str) -> Optional[Check]:
        for item in self.checks:
            if item.name == name:
                return item
        return None

    @property
    def failures(self) -> List[Check]:
        return [
            item
            for item in self.checks
            if item.required and not item.passed and not item.skipped
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "passed": self.passed,
            "checks": [item.to_dict() for item in self.checks],
            "started_at": self.started_at,
            "duration_s": self.duration_s,
        }

    @classmethod
    def from_dict(cls, row: Any) -> Optional["VerificationResult"]:
        if not isinstance(row, dict) or not row.get("proposal_id"):
            return None
        return cls(
            proposal_id=str(row["proposal_id"]),
            passed=bool(row.get("passed")),
            checks=[Check.from_dict(item) for item in row.get("checks") or []],
            started_at=str(row.get("started_at") or ""),
            duration_s=float(row.get("duration_s") or 0.0),
        )

    def render(self) -> str:
        lines = [f"Verification {self.proposal_id}: "
                 f"{'PASSED' if self.passed else 'FAILED'} ({self.duration_s}s)"]
        for item in self.checks:
            if item.skipped:
                flag = "SKIP"
            else:
                flag = "ok  " if item.passed else "FAIL"
            suffix = "" if item.required else " (advisory)"
            lines.append(f"  [{flag}] {item.name}{suffix}: {item.detail}")
        return "\n".join(lines)


class VerificationLoop:
    """Runs the ordered checks against a proposal inside the sandbox."""

    def __init__(
        self,
        sandbox: Optional[ExecutionSandbox] = None,
        *,
        reserved_names: Optional[Iterable[str]] = None,
        core_smoke: bool = True,
    ):
        self.sandbox = sandbox or get_sandbox()
        self._reserved: Optional[Set[str]] = (
            set(reserved_names) if reserved_names is not None else None
        )
        self.core_smoke = bool(core_smoke)

    # ---- public API --------------------------------------------------
    def verify(
        self, proposal: Proposal, *, store: Optional[ProposalStore] = None
    ) -> VerificationResult:
        started = utc_now_iso()
        clock = time.monotonic()
        checks: List[Check] = []

        safety = self.sandbox.safety_scan(proposal)
        checks.append(
            Check(
                "safety",
                safety.passed,
                "clean" if safety.passed else "; ".join(safety.findings[:5]),
            )
        )

        name_ok = bool(MODULE_NAME_RE.match(proposal.module_name or ""))
        if name_ok and proposal.module_name in _FORBIDDEN_MODULE_NAMES:
            name_ok = False
        if name_ok:
            try:
                resolved = proposal.target_path.resolve()
                learned_dir = resolved.parent
                name_ok = learned_dir.name == "learned" and resolved.suffix == ".py"
            except OSError:
                name_ok = False
        checks.append(
            Check(
                "module_name",
                name_ok,
                f"target {proposal.target_path.name}"
                if name_ok
                else f"illegal module name: {proposal.module_name!r}",
            )
        )

        if not safety.passed or not name_ok:
            blocked = "skipped: blocked by an earlier failure"
            checks.append(Check("import", False, blocked, skipped=True))
            checks.append(Check("unit_tests", False, blocked, skipped=True))
            checks.append(Check("tool_contract", False, blocked, skipped=True))
            checks.append(Check("action_contract", False, blocked, skipped=True))
            checks.append(Check("message_polish", False, blocked, skipped=True))
            checks.append(Check("name_collision", False, blocked, skipped=True))
        else:
            import_result = self.sandbox.import_check(proposal)
            checks.append(
                Check(
                    "import",
                    import_result.passed,
                    f"{import_result.duration_s}s"
                    + ("" if import_result.passed else f" :: {import_result.output.strip()[:200]}"),
                )
            )

            test_result = self.sandbox.run_tests(proposal)
            detail = f"{test_result.duration_s}s"
            if test_result.timed_out:
                detail = f"timed out after {self.sandbox.timeout_s}s"
            elif not test_result.passed:
                tail = [line for line in test_result.output.strip().splitlines() if line.strip()]
                detail = " :: ".join(tail[-4:])[:400]
            checks.append(Check("unit_tests", test_result.passed, detail))

            contract = self.sandbox.run_contract(proposal)
            errors = contract.get("errors") or []
            checks.append(
                Check(
                    "tool_contract",
                    bool(contract.get("passed")),
                    "; ".join(str(item) for item in errors)[:400]
                    or f"spec/handle ok ({contract.get('duration_s', 0)}s)",
                )
            )

            interface = self.sandbox.run_interface(proposal)
            actions_tested = interface.get("actions_tested") or []
            probe_skipped = not actions_tested
            contract_errors = [
                "duplicate parameter names for %s" % item
                for item in interface.get("alias_params") or []
            ] + [
                "unreachable action — %s" % item
                for item in interface.get("action_failures") or []
            ] + [
                "wrong weekday in result — %s" % item
                for item in interface.get("weekday_mismatch") or []
            ] + [
                str(item) for item in interface.get("errors") or []
            ]
            checks.append(
                Check(
                    "action_contract",
                    not contract_errors,
                    "; ".join(contract_errors)[:400]
                    or interface.get("skipped")
                    or "actions %s consistent" % ", ".join(actions_tested),
                    skipped=probe_skipped,
                )
            )

            polish = list(interface.get("message_polish") or [])
            checks.append(
                Check(
                    "message_polish",
                    not polish,
                    "; ".join(polish)[:400] if polish else "user-facing text clean",
                    skipped=probe_skipped,
                )
            )

            existing = self._tool_names()
            re_adopting = proposal.target_path.exists()
            if proposal.tool_name in existing and not re_adopting:
                checks.append(
                    Check(
                        "name_collision",
                        False,
                        f"tool '{proposal.tool_name}' is already registered",
                    )
                )
            else:
                checks.append(
                    Check(
                        "name_collision",
                        True,
                        "free"
                        if proposal.tool_name not in existing
                        else "re-adoption of same learned module",
                    )
                )

        if self.core_smoke:
            smoke = self.sandbox.run_python(
                ["-c", "import core.tools"], cwd=PROJECT_ROOT
            )
            checks.append(
                Check(
                    "core_smoke",
                    smoke.passed,
                    "core.tools imports"
                    if smoke.passed
                    else f"core import broken: {smoke.output.strip()[:200]}",
                    required=False,
                )
            )
        else:
            checks.append(Check("core_smoke", True, "disabled", required=False, skipped=True))

        result = VerificationResult(
            proposal_id=proposal.id,
            passed=not any(
                item.required and not item.passed and not item.skipped
                for item in checks
            ),
            checks=checks,
            started_at=started,
            duration_s=round(time.monotonic() - clock, 3),
        )

        if store is not None:
            proposal.verification = result.to_dict()
            if result.passed:
                if proposal.status in (
                    ProposalStatus.DRAFT.value,
                    ProposalStatus.FAILED_VERIFICATION.value,
                ):
                    proposal.status = ProposalStatus.VERIFIED.value
            else:
                proposal.status = ProposalStatus.FAILED_VERIFICATION.value
            store.put(proposal)
        return result

    # ---- helpers -----------------------------------------------------
    def _tool_names(self) -> Set[str]:
        if self._reserved is not None:
            return set(self._reserved)
        try:
            from core.tools import get_registry

            return {tool.name for tool in get_registry().list_tools()}
        except Exception:
            return set()
