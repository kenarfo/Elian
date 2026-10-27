# ==========================
# ELIAN — Self-Improvement / Human-in-the-loop approval
# ==========================
"""The assistant never edits its own core logic without a human saying yes.

Flow: propose → verify (sandbox) → **review + approve (this module)** → adopt.
A proposal in DRAFT/FAILED_VERIFICATION cannot be approved, and adoption
refuses anything that is not APPROVED — the gate cannot be skipped.
"""

from __future__ import annotations

from typing import Callable, Optional

from core.self_improve.experience_log import utc_now_iso
from core.self_improve.proposal import Proposal, ProposalStatus, ProposalStore
from core.self_improve.verification import VerificationResult


class ApprovalError(Exception):
    """Raised when the human gate refuses (or a precondition is missing)."""


def render_proposal(proposal: Proposal) -> str:
    """Full human-readable diff-style view of the proposed change."""
    verification = VerificationResult.from_dict(proposal.verification or {})
    lines = [
        "=" * 72,
        f"PROPOSAL {proposal.id}   [{proposal.status}]",
        f"Gap:      {proposal.gap_name} ({proposal.kind}, domain={proposal.domain})",
        f"Target:   {proposal.target_path}  (additive — no existing file is modified)",
        "",
        f"REQUIREMENT: {proposal.requirement.title}",
        f"  {proposal.requirement.statement}",
        "  Acceptance criteria:",
    ]
    lines += [f"    [ ] {item}" for item in proposal.requirement.acceptance]
    if proposal.evidence_ids:
        lines.append(
            f"  Evidence: {len(proposal.evidence_ids)} experience log entries "
            f"[{', '.join(proposal.evidence_ids[:6])}]"
        )
    if verification is not None:
        lines.append("")
        lines.append(f"VERIFICATION: {'PASSED' if verification.passed else 'FAILED'}")
        for check in verification.checks:
            flag = "SKIP" if check.skipped else ("ok" if check.passed else "FAIL")
            suffix = "" if check.required else " (advisory)"
            lines.append(f"    [{flag}] {check.name}{suffix}: {check.detail}")
    else:
        lines.append("")
        lines.append("VERIFICATION: not run yet")
    lines += ["", f"--- NEW FILE {proposal.target_path.name} " + "-" * 40]
    lines += [f"  {line}" for line in proposal.code.rstrip().splitlines()]
    lines += ["", f"--- NEW TEST {proposal.test_module}.py " + "-" * 38]
    lines += [f"  {line}" for line in proposal.tests.rstrip().splitlines()[:80]]
    lines.append("=" * 72)
    return "\n".join(lines)


class ApprovalGate:
    """Explicit human confirmation before anything is applied to core logic."""

    def __init__(self, store: Optional[ProposalStore] = None):
        self.store = store or ProposalStore()

    def review(self, proposal_id: str) -> str:
        return render_proposal(self.store.get(proposal_id))

    def approve(
        self,
        proposal_id: str,
        *,
        approved_by: str = "user",
        confirm_fn: Optional[Callable[[str], str]] = None,
        note: str = "",
    ) -> Proposal:
        """Record human consent. ``confirm_fn`` = interactive y/N prompt."""
        proposal = self.store.get(proposal_id)
        if proposal.status != ProposalStatus.VERIFIED.value:
            raise ApprovalError(
                f"Proposal {proposal.id} is {proposal.status}; only VERIFIED "
                "proposals can be approved (run `verify` first)."
            )
        verification = VerificationResult.from_dict(proposal.verification or {})
        if verification is None or not verification.passed:
            raise ApprovalError(f"Proposal {proposal.id} has no passing verification.")
        if confirm_fn is not None:
            try:
                answer = str(
                    confirm_fn("Apply this change to your own toolkit? [y/N] ") or ""
                )
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer.strip().lower() not in ("y", "yes"):
                raise ApprovalError("Human declined the change at the prompt.")
        proposal.status = ProposalStatus.APPROVED.value
        proposal.approval = {
            "approved_by": str(approved_by or "user"),
            "approved_at": utc_now_iso(),
            "note": str(note or ""),
        }
        return self.store.put(proposal)

    def reject(
        self, proposal_id: str, reason: str = "", *, rejected_by: str = "user"
    ) -> Proposal:
        proposal = self.store.get(proposal_id)
        if proposal.status == ProposalStatus.ADOPTED.value:
            raise ApprovalError(f"Proposal {proposal.id} is already adopted; unlearn it first.")
        proposal.status = ProposalStatus.REJECTED.value
        proposal.approval = {
            "approved_by": str(rejected_by or "user"),
            "approved_at": utc_now_iso(),
            "rejected": True,
            "note": str(reason or "rejected"),
        }
        return self.store.put(proposal)
