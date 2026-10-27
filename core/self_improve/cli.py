# ==========================
# ELIAN — Self-Improvement CLI
# ==========================
"""Pipeline driver: ``python -m core.self_improve.cli <command>``.

    reflect                 read the Experience Log, suggest improvements
    gaps                    detect Capability Gaps + write the Capability Map
    dry-run                 supervised runtime audit + Reflect→Gap→Proposal report
                            (read-only — never adopts or writes learned tools)
    rc-session              guided mic RC: plan → real voice → audit → dry-run
                            (read-only — never adopts or writes learned tools)
    self-develop            autonomous continuous development loop (writes code)
    self-develop-status     inspect autonomous loop state
    self-develop-stop       request stop after the current safe operation
    propose GAP_ID          generate requirement + code + tests for a gap
    list [--status S]       show proposals
    verify PROPOSAL_ID      run the Execution Sandbox + Verification Loop
    review PROPOSAL_ID      show the proposed code (human review step)
    approve PROPOSAL_ID     record human consent (y/N or --yes)
    reject PROPOSAL_ID      decline a proposal
    adopt PROPOSAL_ID       write the approved module into learned/
    unlearn MODULE          remove an adopted module + its tool
    status                  capability map + proposals + learned tools
    log                     append an experience entry by hand
    demo                    reflect → gaps → propose → verify → review
                            (stops before approval — the human gate)

Exit code 0 = ok, 1 = refused/failed, 2 = unrecoverable (self-develop).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from core.self_improve.approval import ApprovalError, ApprovalGate
from core.self_improve.experience_log import (
    EVIDENCE_RUNTIME,
    PROVENANCE_MANUAL,
    ExperienceEntry,
    ExperienceLogStore,
    FailureCategory,
)
from core.self_improve.gap_analysis import GapAnalyzer, run_gap_analysis
from core.self_improve.proposal import ProposalGenerator, ProposalStore, ProposalStatus
from core.self_improve.reflection import ReflectionEngine
from core.self_improve.sandbox import get_sandbox
from core.self_improve.rc_session import run_rc_session
from core.self_improve.supervised_dry_run import run_supervised_dry_run
from core.self_improve.self_development import (
    DevelopmentStateStore,
    SelfDevelopmentEngine,
    default_state_path,
    load_config,
)
from core.self_improve.tool_hot_swap import (
    AdoptionError,
    adopt,
    learned_sources,
    unlearn,
)
from core.self_improve.verification import VerificationLoop


def _log_store(args) -> ExperienceLogStore:
    return ExperienceLogStore(Path(args.log)) if getattr(args, "log", None) else ExperienceLogStore()


def _fail(message: str) -> int:
    print(f"[SELF_IMPROVE] {message}")
    return 1


def next_open_gap(gaps):
    """The first gap that still needs work, or None.

    Single source of truth for 'what should Elian work on next'. `gaps` and
    `demo` must agree here: when they each picked independently, `demo` kept
    re-proposing a remedy that had already been adopted.
    """
    for gap in gaps:
        if not gap.addressed:
            return gap
    return None


def addressed_note(gaps) -> str:
    """One-line summary of the gaps that already have an adopted remedy."""
    parts = [
        f"{gap.id} (by {', '.join(gap.addressed_by)})"
        for gap in gaps if gap.addressed
    ]
    return "; ".join(parts)


# ---- commands ----------------------------------------------------------
def _evidence_scope(args) -> str:
    return str(getattr(args, "evidence", None) or EVIDENCE_RUNTIME).strip().lower()


def cmd_reflect(args) -> int:
    evidence = _evidence_scope(args)
    report = ReflectionEngine(_log_store(args)).reflect(evidence=evidence)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print(f"(evidence={evidence})")
    print(report.render())
    top = report.top()
    if top is not None:
        print(f"\nSuggested improvement: {top.suggestion}")
    return 0


def cmd_gaps(args) -> int:
    evidence = _evidence_scope(args)
    gaps = run_gap_analysis(_log_store(args), evidence=evidence)
    if args.json:
        print(json.dumps([gap.to_dict() for gap in gaps], ensure_ascii=False, indent=2))
        return 0
    if not gaps:
        print(f"No capability gaps (evidence={evidence}): nothing is failing repeatedly.")
        return 0
    print(f"(evidence={evidence})")
    for gap in gaps:
        flag = "  ALREADY ADDRESSED by " + ", ".join(gap.addressed_by) if gap.addressed else ""
        print(f"  {gap.name}  [{gap.id}] kind={gap.kind} severity={gap.severity}{flag}")
        print(f"    {gap.description}")
        print(f"    failure modes: {gap.failure_modes}")
    print("\nCapability Map written to Data/self_improve/capability_map.json")
    target = next_open_gap(gaps)
    if target is None:
        print("Every detected gap already has an adopted fix.")
        if addressed_note(gaps):
            print(f"  {addressed_note(gaps)}")
    else:
        print("Next: python -m core.self_improve.cli propose " + target.id)
        for gap in gaps:
            if gap.addressed:
                print("(skipped " + gap.id + ": already addressed by "
                      + ", ".join(gap.addressed_by) + ")")
    return 0


def cmd_propose(args) -> int:
    generator = ProposalGenerator()
    proposal = generator.propose(args.gap_id)
    print(f"Proposal {proposal.id} created for {proposal.gap_name}")
    print(f"  Requirement: {proposal.requirement.statement}")
    print(f"  Generates:   {proposal.module_name}.py + {proposal.test_module}.py")
    print(f"  Next:        verify {proposal.id}")
    return 0


def cmd_list(args) -> int:
    proposals = ProposalStore().list(status=args.status)
    if args.json:
        print(json.dumps([item.to_dict() for item in proposals],
                         ensure_ascii=False, indent=2, default=str))
        return 0
    if not proposals:
        print("No proposals yet.")
        return 0
    for proposal in proposals:
        verify = "verified" if (proposal.verification or {}).get("passed") else "unverified"
        print(f"  {proposal.id:<40} {proposal.status:<20} {verify:<10} {proposal.gap_name}")
    return 0


def cmd_verify(args) -> int:
    store = ProposalStore()
    try:
        proposal = store.get(args.proposal_id)
    except KeyError as exc:
        return _fail(str(exc))
    result = VerificationLoop(get_sandbox()).verify(proposal, store=store)
    print(result.render())
    return 0 if result.passed else 1


def cmd_review(args) -> int:
    try:
        print(ApprovalGate().review(args.proposal_id))
    except KeyError as exc:
        return _fail(str(exc))
    print("\nApprove with: python -m core.self_improve.cli approve " + args.proposal_id)
    return 0


def cmd_approve(args) -> int:
    gate = ApprovalGate()
    try:
        if args.yes:
            proposal = gate.approve(args.proposal_id, note=args.note or "")
        else:
            print(gate.review(args.proposal_id))
            if not sys.stdin.isatty():
                return _fail(
                    "non-interactive session: read `review`, then re-run with --yes "
                    "to record your consent."
                )
            proposal = gate.approve(args.proposal_id, confirm_fn=input, note=args.note or "")
    except (KeyError, ApprovalError) as exc:
        return _fail(str(exc))
    print(f"APPROVED {proposal.id} by {proposal.approval.get('approved_by')}")
    print(f"Next: python -m core.self_improve.cli adopt {proposal.id}")
    return 0


def cmd_reject(args) -> int:
    try:
        proposal = ApprovalGate().reject(args.proposal_id, args.reason or "rejected")
    except (KeyError, ApprovalError) as exc:
        return _fail(str(exc))
    print(f"REJECTED {proposal.id}: {proposal.approval.get('note')}")
    return 0


def cmd_adopt(args) -> int:
    store = ProposalStore()
    try:
        proposal = store.get(args.proposal_id)
    except KeyError as exc:
        return _fail(str(exc))
    try:
        target = adopt(proposal, store=store, replace=args.replace)
    except (AdoptionError, OSError) as exc:
        return _fail(str(exc))
    print(f"ADOPTED {proposal.id} -> {target}")
    print(f"  Tool '{proposal.tool_name}' is now hot-swappable:")
    print("    from core.self_improve.tool_hot_swap import load_learned_tools")
    print("    load_learned_tools(get_registry())   # no restart needed")
    return 0


def cmd_unlearn(args) -> int:
    if not unlearn(args.module):
        return _fail(f"nothing to remove for module '{args.module}'")
    print(f"Removed learned module '{args.module}'.")
    return 0


def cmd_status(args) -> int:
    analyzer = GapAnalyzer(_log_store(args))
    capability_map = analyzer.load_capability_map()
    proposals = ProposalStore().list()
    learned = learned_sources()
    if args.json:
        print(json.dumps(
            {
                "capability_map": capability_map,
                "proposals": [item.to_dict() for item in proposals],
                "learned_modules": sorted(learned),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ))
        return 0
    print("Capability Map:")
    if not capability_map:
        print("  (empty — run `gaps` first)")
    for domain, item in sorted(capability_map.items()):
        print(f"  {domain:<14} {item.get('status'):<7} failure_rate={item.get('failure_rate')}")
    print("Proposals:")
    if not proposals:
        print("  (none — run `propose <gap_id>`)")
    for proposal in proposals:
        print(f"  {proposal.id:<40} {proposal.status}")
    print("Learned tools (hot-swapped):")
    if not learned:
        print("  (none adopted yet)")
    for module_name in sorted(learned):
        print(f"  {module_name}")
    return 0


def cmd_self_develop(args) -> int:
    """Continuous autonomous development — no per-cycle approval."""
    cfg = load_config()
    max_cycles = getattr(args, "max_cycles", None)
    if max_cycles is not None:
        max_cycles = int(max_cycles)
    synthetic = getattr(args, "synthetic", None) or None
    engine = SelfDevelopmentEngine(
        max_repair_attempts=int(
            getattr(args, "max_repair_attempts", None)
            or cfg.get("max_repair_attempts")
            or 3
        ),
        test_timeout=float(
            getattr(args, "test_timeout", None) or cfg.get("test_timeout") or 180.0
        ),
        max_cycles=max_cycles,
        synthetic_mode=synthetic,
        skip_agent_smoke=bool(getattr(args, "skip_agent_smoke", False)),
        include_runtime=not bool(getattr(args, "no_runtime", False)),
        include_repo=not bool(getattr(args, "no_repo", False)),
        sleep_between_cycles_s=float(
            cfg.get("sleep_between_cycles_s")
            if max_cycles is None
            else 0.0
        ),
        state_path=Path(cfg["development_state_path"])
        if cfg.get("development_state_path")
        else None,
        history_path=Path(cfg["history_path"]) if cfg.get("history_path") else None,
    )
    return engine.run()


def cmd_self_develop_status(args) -> int:
    cfg = load_config()
    path = Path(cfg["development_state_path"]) if cfg.get("development_state_path") else default_state_path()
    summary = DevelopmentStateStore(path).status_summary()
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    running = "running" if summary.get("running") else "stopped"
    print(f"mode: {summary.get('mode')} ({running})")
    print(f"current_cycle: {summary.get('current_cycle')}")
    print(f"current_phase: {summary.get('current_phase')}")
    print(f"current_candidate: {summary.get('current_candidate') or '(none)'}")
    print(f"successful_cycles: {summary.get('successful_cycles')}")
    print(f"failed_cycles: {summary.get('failed_cycles')}")
    print(f"rollback_count: {summary.get('rollback_count')}")
    print(f"last_successful_improvement: {summary.get('last_successful_improvement') or '(none)'}")
    print(f"last_failed_improvement: {summary.get('last_failed_improvement') or '(none)'}")
    print(f"stop_requested: {summary.get('stop_requested')}")
    if summary.get("unrecoverable_reason"):
        print(f"unrecoverable_reason: {summary.get('unrecoverable_reason')}")
    return 0


def cmd_self_develop_stop(args) -> int:
    cfg = load_config()
    path = Path(cfg["development_state_path"]) if cfg.get("development_state_path") else default_state_path()
    store = DevelopmentStateStore(path)
    store.request_stop()
    print("[SELF-DEV] stop requested — engine will halt before the next cycle")
    return 0


def cmd_log(args) -> int:
    entry = _log_store(args).append(
        ExperienceEntry(
            domain=args.domain,
            category=FailureCategory(args.category),
            summary=args.summary,
            tool=args.tool or "",
            user_feedback=args.feedback or "",
            provenance=PROVENANCE_MANUAL,
        )
    )
    print(
        f"Logged {entry.id} [{entry.category.value}] {entry.domain} "
        f"provenance={entry.provenance}: {entry.summary}"
    )
    return 0


def cmd_dry_run(args) -> int:
    """Supervised runtime-only dry-run: corpus audit + Reflect→Gap→Proposal report.

    Read-only for the learning system: does not write capability maps, proposals,
    learned tools, approvals, or adoption state.
    """
    report = run_supervised_dry_run(_log_store(args), evidence=EVIDENCE_RUNTIME)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print(report.render())
    return 0


def cmd_rc_session(args) -> int:
    """Guided mic RC around the real Elian voice path + supervised dry-run.

    Read-only for learning: no proposal persistence, no adoption, no learned tools.
    """
    report = run_rc_session(
        _log_store(args),
        launch_voice=not bool(getattr(args, "no_launch", False)),
        analyze_only=bool(getattr(args, "analyze_only", False)),
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print(report.render())
    return 0


def cmd_demo(args) -> int:
    """First step: experience log → reflection → gap → proposal → verify → review."""
    store = _log_store(args)
    evidence = _evidence_scope(args)
    report = ReflectionEngine(store).reflect(evidence=evidence)
    print(f"(evidence={evidence})")
    print(report.render())
    top = report.top()
    if top is None:
        print("\nNothing to improve: no domain is failing repeatedly.")
        return 0
    print(f"\nSuggested improvement: {top.suggestion}")

    gaps = run_gap_analysis(store, evidence=evidence)
    if not gaps:
        return 0
    # Never re-propose a remedy that has already been adopted: that is how
    # the cycle ends up regenerating the same tool for ever.
    target = next_open_gap(gaps)
    if target is None:
        print("\nEvery detected gap already has an adopted fix:")
        print(f"  {addressed_note(gaps)}")
        print("Nothing new to propose. Record fresh outcomes, or work on a new gap.")
        return 0
    generator = ProposalGenerator()
    proposal = generator.propose(target.id)
    print(f"\nGenerated proposal {proposal.id} ({proposal.gap_name})")
    for gap in gaps:
        if gap.addressed:
            print(f"  (skipped {gap.id}: already addressed by {', '.join(gap.addressed_by)})")

    result = VerificationLoop(get_sandbox()).verify(proposal, store=ProposalStore())
    print(result.render())
    if not result.passed:
        return 1

    print("\n" + ApprovalGate().review(proposal.id))
    print("\nSTOPPED BEFORE APPLYING. The change is yours to approve:")
    print(f"  python -m core.self_improve.cli approve {proposal.id}")
    print(f"  python -m core.self_improve.cli adopt  {proposal.id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m core.self_improve.cli",
        description="Elian Self-Improvement System (reflection → self-upgrade with a human gate).",
    )
    parser.add_argument("--log", help="experience log path (default Data/self_improve/experience_log.json)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--evidence",
        choices=["runtime", "all", "seed"],
        default="runtime",
        help="evidence population for reflect/gaps/demo (default: runtime)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("reflect", help="read the experience log and suggest improvements")
    sub.add_parser("gaps", help="detect capability gaps + write the capability map")
    sub.add_parser(
        "dry-run",
        help="supervised runtime corpus audit + Reflect→Gap→Proposal (read-only)",
    )
    rc = sub.add_parser(
        "rc-session",
        help="guided mic RC session + corpus audit + dry-run (read-only)",
    )
    rc.add_argument(
        "--no-launch",
        action="store_true",
        help="do not spawn python main.py; wait for Enter after you run Elian yourself",
    )
    rc.add_argument(
        "--analyze-only",
        action="store_true",
        help="skip interaction wait; summarize any new runtime rows since process start snapshot",
    )

    propose = sub.add_parser("propose", help="generate a proposal for a gap")
    propose.add_argument("gap_id")

    listing = sub.add_parser("list", help="list proposals")
    listing.add_argument("--status", help=ProposalStatus.__doc__ or "filter by status")

    verify = sub.add_parser("verify", help="run sandbox + verification loop")
    verify.add_argument("proposal_id")

    review = sub.add_parser("review", help="show proposed code for human review")
    review.add_argument("proposal_id")

    approve = sub.add_parser("approve", help="human approval (y/N prompt, or --yes)")
    approve.add_argument("proposal_id")
    approve.add_argument("--yes", action="store_true", help="record consent non-interactively")
    approve.add_argument("--note", default="", help="optional note")

    reject = sub.add_parser("reject", help="decline a proposal")
    reject.add_argument("proposal_id")
    reject.add_argument("--reason", default="", help="why it is rejected")

    adopt_cmd = sub.add_parser("adopt", help="apply an approved proposal to the toolkit")
    adopt_cmd.add_argument("proposal_id")
    adopt_cmd.add_argument("--replace", action="store_true", help="overwrite existing learned module")

    unlearn_cmd = sub.add_parser("unlearn", help="remove an adopted module")
    unlearn_cmd.add_argument("module")

    sub.add_parser("status", help="capability map + proposals + learned tools")

    sd = sub.add_parser(
        "self-develop",
        help="autonomous continuous development loop (writes/tests/keeps or rolls back)",
    )
    sd.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="optional cap for tests; omit for continuous unattended run",
    )
    sd.add_argument(
        "--synthetic",
        choices=["pass", "fail"],
        default=None,
        help="deterministic sandbox harness (do not use for production autonomy)",
    )
    sd.add_argument(
        "--skip-agent-smoke",
        action="store_true",
        help="skip python main.py --agent-smoke (tests / constrained hosts)",
    )
    sd.add_argument("--no-runtime", action="store_true", help="skip runtime gap discovery")
    sd.add_argument("--no-repo", action="store_true", help="skip repository marker discovery")
    sd.add_argument("--max-repair-attempts", type=int, default=None)
    sd.add_argument("--test-timeout", type=float, default=None)

    sub.add_parser("self-develop-status", help="inspect autonomous development state")
    sub.add_parser(
        "self-develop-stop",
        help="request stop after the current safe operation (no mid-git kill)",
    )

    log_cmd = sub.add_parser("log", help="append an experience entry by hand")
    log_cmd.add_argument("--domain", required=True)
    log_cmd.add_argument(
        "--category",
        required=True,
        choices=sorted(category.value for category in FailureCategory),
    )
    log_cmd.add_argument("--summary", required=True)
    log_cmd.add_argument("--tool", default="")
    log_cmd.add_argument("--feedback", default="")

    sub.add_parser("demo", help="reflect → gaps → propose → verify → review (stops before approval)")
    return parser


COMMANDS = {
    "reflect": cmd_reflect,
    "gaps": cmd_gaps,
    "dry-run": cmd_dry_run,
    "rc-session": cmd_rc_session,
    "self-develop": cmd_self_develop,
    "self-develop-status": cmd_self_develop_status,
    "self-develop-stop": cmd_self_develop_stop,
    "propose": cmd_propose,
    "list": cmd_list,
    "verify": cmd_verify,
    "review": cmd_review,
    "approve": cmd_approve,
    "reject": cmd_reject,
    "adopt": cmd_adopt,
    "unlearn": cmd_unlearn,
    "status": cmd_status,
    "log": cmd_log,
    "demo": cmd_demo,
}


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
