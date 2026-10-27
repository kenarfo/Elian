# ==========================
# ELIAN — Supervised dry-run (read-only)
# ==========================
"""Runtime corpus audit + Reflect → Gap → Proposal report. No adoption.

Does not write capability maps, proposals, learned tools, or approvals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from core.self_improve.experience_log import (
    EVIDENCE_RUNTIME,
    ExperienceEntry,
    ExperienceLogStore,
    FailureCategory,
    select_evidence,
)
from core.self_improve.gap_analysis import GapAnalyzer
from core.self_improve.proposal import Proposal, ProposalGenerator
from core.self_improve.reflection import (
    DEFAULT_MIN_FAILURE_RATE,
    DEFAULT_MIN_SAMPLES,
    ImprovementCandidate,
    ReflectionEngine,
)

# Transparent corpus-quality thresholds (documented in SELF_IMPROVEMENT.md).
SPARSE_MAX_OCCURRENCES = 9
LIMITED_MAX_OCCURRENCES = 29
USABLE_MIN_DOMAINS = 2
USABLE_MIN_FAILURES = 5
LIMITED_MIN_FAILURES = 3

STATUS_EMPTY = "EMPTY"
STATUS_SPARSE = "SPARSE"
STATUS_LIMITED = "LIMITED"
STATUS_USABLE = "USABLE"

_ANSWER_QUALITY_KEYS = frozenset(
    {
        "answer_quality",
        "vacuous_reply",
        "false_success_blocked",
        "agent_success",
        "response_empty",
    }
)
_GROUNDING_KEYS = frozenset({"grounded", "grounded_in_web", "grounding_status"})

#: Discourse-only failure categories that must not drive structured_result
#: proposals when no runtime tool was observed (align dry-run with GapAnalyzer).
_DISCOURSE_NOISE_CATEGORIES = frozenset(
    {
        FailureCategory.USER_CORRECTION.value,
        FailureCategory.UNKNOWN.value,
    }
)


def _is_tool_less_discourse_noise(*, category: str, observed_tool: str) -> bool:
    """True for tool-less USER_CORRECTION / UNKNOWN — not a capability gap."""
    if str(observed_tool or "").strip():
        return False
    return str(category or "").strip().upper() in _DISCOURSE_NOISE_CATEGORIES


def _weight(entry: ExperienceEntry) -> int:
    try:
        return int(getattr(entry, "occurrence_weight", None) or 1)
    except (TypeError, ValueError):
        return 1


def _meta(entry: ExperienceEntry) -> Dict[str, Any]:
    raw = getattr(entry, "metadata", None) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _error_code(entry: ExperienceEntry) -> str:
    meta = _meta(entry)
    code = str(meta.get("error_code") or "").strip()
    if code:
        return code[:64]
    tools = meta.get("tools")
    if isinstance(tools, list):
        for item in reversed(tools):
            if isinstance(item, dict):
                nested = str(item.get("error_code") or "").strip()
                if nested:
                    return nested[:64]
    return ""


def corpus_status(
    *,
    occurrences: int,
    failure_occurrences: int,
    distinct_domains: int,
) -> str:
    if occurrences <= 0:
        return STATUS_EMPTY
    if (
        occurrences <= SPARSE_MAX_OCCURRENCES
        or failure_occurrences < LIMITED_MIN_FAILURES
        or distinct_domains < 1
    ):
        return STATUS_SPARSE
    if (
        occurrences <= LIMITED_MAX_OCCURRENCES
        or distinct_domains < USABLE_MIN_DOMAINS
        or failure_occurrences < USABLE_MIN_FAILURES
    ):
        return STATUS_LIMITED
    return STATUS_USABLE


@dataclass
class CorpusAudit:
    physical_rows: int = 0
    weighted_occurrences: int = 0
    success_occurrences: int = 0
    failure_occurrences: int = 0
    failure_rate: float = 0.0
    domains: Dict[str, int] = field(default_factory=dict)
    categories: Dict[str, int] = field(default_factory=dict)
    top_tools: Dict[str, int] = field(default_factory=dict)
    top_error_codes: Dict[str, int] = field(default_factory=dict)
    seed_manual_excluded_rows: int = 0
    status: str = STATUS_EMPTY
    # Coverage among runtime physical rows (not weighted).
    agent_path_rows: int = 0
    answer_quality_meta_rows: int = 0
    grounding_meta_rows: int = 0

    @property
    def distinct_domains(self) -> int:
        return len(self.domains)

    @property
    def distinct_categories(self) -> int:
        return len(self.categories)

    @property
    def distinct_tools(self) -> int:
        return len(self.top_tools)

    def category_count(self, name: str) -> int:
        return int(self.categories.get(str(name), 0))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "physical_rows": self.physical_rows,
            "weighted_occurrences": self.weighted_occurrences,
            "success_occurrences": self.success_occurrences,
            "failure_occurrences": self.failure_occurrences,
            "failure_rate": self.failure_rate,
            "domains": dict(self.domains),
            "categories": dict(self.categories),
            "top_tools": dict(self.top_tools),
            "top_error_codes": dict(self.top_error_codes),
            "seed_manual_excluded_rows": self.seed_manual_excluded_rows,
            "status": self.status,
            "distinct_domains": self.distinct_domains,
            "distinct_categories": self.distinct_categories,
            "distinct_tools": self.distinct_tools,
            "coverage": {
                "agent_path_rows": self.agent_path_rows,
                "answer_quality_meta_rows": self.answer_quality_meta_rows,
                "grounding_meta_rows": self.grounding_meta_rows,
                "answer_quality_coverage": (
                    round(self.answer_quality_meta_rows / self.physical_rows, 3)
                    if self.physical_rows
                    else 0.0
                ),
                "grounding_coverage": (
                    round(self.grounding_meta_rows / self.physical_rows, 3)
                    if self.physical_rows
                    else 0.0
                ),
                "agent_path_coverage": (
                    round(self.agent_path_rows / self.physical_rows, 3)
                    if self.physical_rows
                    else 0.0
                ),
            },
            "thresholds": {
                "sparse_max_occurrences": SPARSE_MAX_OCCURRENCES,
                "limited_max_occurrences": LIMITED_MAX_OCCURRENCES,
                "usable_min_domains": USABLE_MIN_DOMAINS,
                "usable_min_failures": USABLE_MIN_FAILURES,
            },
        }


def audit_runtime_corpus(
    entries: Sequence[ExperienceEntry],
    *,
    evidence: str = EVIDENCE_RUNTIME,
) -> CorpusAudit:
    """Weighted runtime corpus statistics. Never mixes in seed/manual by default."""
    all_rows = list(entries or [])
    runtime = select_evidence(all_rows, evidence)
    excluded = len(all_rows) - len(runtime) if evidence == EVIDENCE_RUNTIME else 0

    domains: Dict[str, int] = {}
    categories: Dict[str, int] = {}
    tools: Dict[str, int] = {}
    errors: Dict[str, int] = {}
    success = 0
    failure = 0
    total = 0
    agent_rows = 0
    aq_rows = 0
    ground_rows = 0

    for entry in runtime:
        weight = _weight(entry)
        total += weight
        domain = str(getattr(entry, "domain", "") or "unknown")
        domains[domain] = domains.get(domain, 0) + weight
        cat = (
            entry.category.value
            if isinstance(getattr(entry, "category", None), FailureCategory)
            else str(getattr(entry, "category", "") or "UNKNOWN")
        )
        categories[cat] = categories.get(cat, 0) + weight
        if cat == FailureCategory.SUCCESS.value:
            success += weight
        else:
            failure += weight

        tool = str(getattr(entry, "tool", "") or "").strip()
        if tool and cat != FailureCategory.SUCCESS.value:
            tools[tool] = tools.get(tool, 0) + weight
        code = _error_code(entry)
        if code and cat != FailureCategory.SUCCESS.value:
            errors[code] = errors.get(code, 0) + weight

        meta = _meta(entry)
        if str(meta.get("source") or "").strip().lower() == "agent":
            agent_rows += 1
        if _ANSWER_QUALITY_KEYS.intersection(meta.keys()):
            aq_rows += 1
        if _GROUNDING_KEYS.intersection(meta.keys()):
            ground_rows += 1

    rate = round(failure / total, 3) if total else 0.0
    status = corpus_status(
        occurrences=total,
        failure_occurrences=failure,
        distinct_domains=len(domains),
    )
    return CorpusAudit(
        physical_rows=len(runtime),
        weighted_occurrences=total,
        success_occurrences=success,
        failure_occurrences=failure,
        failure_rate=rate,
        domains=dict(sorted(domains.items(), key=lambda item: (-item[1], item[0]))),
        categories=dict(sorted(categories.items(), key=lambda item: (-item[1], item[0]))),
        top_tools=dict(sorted(tools.items(), key=lambda item: (-item[1], item[0]))),
        top_error_codes=dict(sorted(errors.items(), key=lambda item: (-item[1], item[0]))),
        seed_manual_excluded_rows=max(0, excluded),
        status=status,
        agent_path_rows=agent_rows,
        answer_quality_meta_rows=aq_rows,
        grounding_meta_rows=ground_rows,
    )


@dataclass
class EvidenceSnippet:
    tool: str
    error_code: str
    category: str
    repeat_count: int
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "error_code": self.error_code,
            "category": self.category,
            "repeat_count": self.repeat_count,
            "summary": self.summary,
        }


@dataclass
class DryRunCandidate:
    domain: str
    category: str
    observed_tool: str
    error_code: str
    weighted_samples: int
    weighted_failures: int
    failure_rate: float
    qualified: bool
    suggestion: str = ""
    gap_id: str = ""
    gap_kind: str = ""
    gap_description: str = ""
    proposal_requirement: str = ""
    proposal_tool_name: str = ""
    proposal_module_name: str = ""
    evidence: List[EvidenceSnippet] = field(default_factory=list)
    min_samples: int = DEFAULT_MIN_SAMPLES
    min_failure_rate: float = DEFAULT_MIN_FAILURE_RATE

    @property
    def decision(self) -> str:
        return "QUALIFIED" if self.qualified else "BELOW THRESHOLD"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "category": self.category,
            "observed_tool": self.observed_tool,
            "error_code": self.error_code,
            "weighted_samples": self.weighted_samples,
            "weighted_failures": self.weighted_failures,
            "failure_rate": self.failure_rate,
            "qualified": self.qualified,
            "decision": self.decision,
            "suggestion": self.suggestion,
            "gap_id": self.gap_id,
            "gap_kind": self.gap_kind,
            "gap_description": self.gap_description,
            "proposal_requirement": self.proposal_requirement,
            "proposal_tool_name": self.proposal_tool_name,
            "proposal_module_name": self.proposal_module_name,
            "evidence": [item.to_dict() for item in self.evidence],
            "min_samples": self.min_samples,
            "min_failure_rate": self.min_failure_rate,
        }


@dataclass
class SupervisedDryRunReport:
    evidence: str
    audit: CorpusAudit
    candidates: List[DryRunCandidate] = field(default_factory=list)
    min_samples: int = DEFAULT_MIN_SAMPLES
    min_failure_rate: float = DEFAULT_MIN_FAILURE_RATE
    mutated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence": self.evidence,
            "audit": self.audit.to_dict(),
            "candidates": [item.to_dict() for item in self.candidates],
            "min_samples": self.min_samples,
            "min_failure_rate": self.min_failure_rate,
            "mutated": self.mutated,
            "read_only": True,
        }

    def render(self) -> str:
        lines: List[str] = []
        audit = self.audit
        lines.append("=== SUPERVISED DRY-RUN (read-only) ===")
        lines.append(f"evidence={self.evidence}")
        lines.append("")
        lines.append("RUNTIME CORPUS")
        lines.append(f"  Physical rows:        {audit.physical_rows}")
        lines.append(f"  Weighted occurrences: {audit.weighted_occurrences}")
        lines.append(f"  Success occurrences:  {audit.success_occurrences}")
        lines.append(f"  Failure occurrences:  {audit.failure_occurrences}")
        lines.append(f"  Failure rate:         {audit.failure_rate:.0%}")
        lines.append(f"  Distinct domains:     {audit.distinct_domains}")
        lines.append(f"  Distinct categories:  {audit.distinct_categories}")
        lines.append(f"  Distinct tools:       {audit.distinct_tools}")
        lines.append(
            f"  Seed/manual excluded: {audit.seed_manual_excluded_rows} physical rows"
        )
        lines.append(f"  STATUS: {audit.status}")
        lines.append("")
        if audit.status in (STATUS_EMPTY, STATUS_SPARSE):
            lines.append("WARNING:")
            lines.append(
                "  Current runtime evidence is insufficient for strong "
                "autonomous-learning conclusions."
            )
            lines.append("  Results below are informational only.")
            lines.append("")
        elif audit.status == STATUS_LIMITED:
            lines.append("WARNING:")
            lines.append(
                "  Runtime corpus is LIMITED - useful for supervised review, "
                "not for autonomous adoption."
            )
            lines.append("")

        lines.append("Coverage (physical runtime rows):")
        lines.append(
            f"  agent-path: {audit.agent_path_rows}/{audit.physical_rows}"
        )
        lines.append(
            f"  answer-quality metadata: "
            f"{audit.answer_quality_meta_rows}/{audit.physical_rows}"
        )
        lines.append(
            f"  grounding metadata: "
            f"{audit.grounding_meta_rows}/{audit.physical_rows}"
        )
        lines.append("")

        lines.append("Domains (weighted):")
        if not audit.domains:
            lines.append("  (none)")
        for name, count in audit.domains.items():
            lines.append(f"  {name:<16} {count}")
        lines.append("")
        lines.append("Categories (weighted):")
        if not audit.categories:
            lines.append("  (none)")
        for name, count in audit.categories.items():
            lines.append(f"  {name:<16} {count}")
        if audit.top_tools:
            lines.append("")
            lines.append("Top tools among failures (weighted):")
            for name, count in list(audit.top_tools.items())[:12]:
                lines.append(f"  {name:<24} {count}")
        if audit.top_error_codes:
            lines.append("")
            lines.append("Top error codes among failures (weighted):")
            for name, count in list(audit.top_error_codes.items())[:12]:
                lines.append(f"  {name:<24} {count}")

        lines.append("")
        lines.append(
            f"Reflection thresholds: min_samples={self.min_samples} "
            f"min_failure_rate={self.min_failure_rate}"
        )
        lines.append("")

        if audit.weighted_occurrences == 0:
            lines.append("No runtime evidence - learning conclusions are impossible.")
            lines.append("STOP (no mutation, no adoption).")
            return "\n".join(lines)

        if not self.candidates:
            lines.append("No domains with failures to review.")
            lines.append("STOP (no mutation, no adoption).")
            return "\n".join(lines)

        lines.append(f"CANDIDATES ({len(self.candidates)})")
        for index, cand in enumerate(self.candidates, start=1):
            tool_disp = cand.observed_tool or "(none)"
            err_disp = cand.error_code or "(none)"
            lines.append("")
            lines.append(f"{index}. Domain: {cand.domain}")
            lines.append(f"   Category: {cand.category}")
            lines.append(f"   Observed tool: {tool_disp}")
            lines.append(f"   Observed error: {err_disp}")
            lines.append(f"   Weighted samples: {cand.weighted_samples}")
            lines.append(f"   Weighted failures: {cand.weighted_failures}")
            lines.append(f"   Failure rate: {cand.failure_rate:.0%}")
            lines.append(
                f"   Threshold check: samples>={cand.min_samples} "
                f"rate>={cand.min_failure_rate} -> {cand.decision}"
            )
            if cand.suggestion:
                lines.append(f"   Suggestion: {cand.suggestion}")
            if cand.gap_id:
                lines.append(f"   Gap: {cand.gap_id} kind={cand.gap_kind}")
                if cand.gap_description:
                    lines.append(f"   Gap detail: {cand.gap_description}")
            if cand.proposal_requirement:
                lines.append(
                    f"   Would-propose module: {cand.proposal_module_name or '-'} "
                    f"tool_name={cand.proposal_tool_name or '-'}"
                )
                lines.append(f"   Requirement: {cand.proposal_requirement}")
            if cand.evidence:
                lines.append("   Representative evidence:")
                for snip in cand.evidence[:8]:
                    tool = snip.tool or "(no tool)"
                    code = snip.error_code or "(no error_code)"
                    lines.append(
                        f"   - {tool} / {snip.category} / {code} / "
                        f"repeat_count={snip.repeat_count}"
                    )
        lines.append("")
        lines.append("STOP - dry-run does not write proposals, learned tools, or approvals.")
        return "\n".join(lines)


def _evidence_for_domain(
    runtime: Sequence[ExperienceEntry],
    domain: str,
    *,
    limit: int = 8,
) -> List[EvidenceSnippet]:
    rows = [
        entry
        for entry in runtime
        if str(getattr(entry, "domain", "") or "") == domain
        and getattr(entry, "is_failure", False)
    ]
    rows.sort(key=lambda entry: (-_weight(entry), str(getattr(entry, "tool", "") or "")))
    snippets: List[EvidenceSnippet] = []
    for entry in rows[:limit]:
        cat = (
            entry.category.value
            if isinstance(entry.category, FailureCategory)
            else str(entry.category)
        )
        snippets.append(
            EvidenceSnippet(
                tool=str(getattr(entry, "tool", "") or ""),
                error_code=_error_code(entry),
                category=cat,
                repeat_count=_weight(entry),
                summary=str(getattr(entry, "summary", "") or "")[:120],
            )
        )
    return snippets


def run_supervised_dry_run(
    store: Optional[ExperienceLogStore] = None,
    *,
    entries: Optional[Sequence[ExperienceEntry]] = None,
    evidence: str = EVIDENCE_RUNTIME,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    min_failure_rate: float = DEFAULT_MIN_FAILURE_RATE,
) -> SupervisedDryRunReport:
    """Reflect → Gap → Proposal *in memory*. Never persists learning artifacts.

    Always analyzes runtime evidence only — never falls back to seed/manual.
    """
    _ = evidence  # API accepts the flag; dry-run ignores non-runtime scopes.
    source_store = store or ExperienceLogStore()
    try:
        all_entries = list(entries) if entries is not None else source_store.load()
    except Exception:
        all_entries = []

    audit = audit_runtime_corpus(all_entries, evidence=EVIDENCE_RUNTIME)
    runtime = select_evidence(all_entries, EVIDENCE_RUNTIME)

    engine = ReflectionEngine(source_store)
    report = engine.reflect(
        runtime,
        evidence=EVIDENCE_RUNTIME,
        min_samples=min_samples,
        min_failure_rate=min_failure_rate,
    )

    # Analyze without save/write_capability_map.
    analyzer = GapAnalyzer(source_store)
    gaps = analyzer.analyze(
        report,
        evidence=EVIDENCE_RUNTIME,
        min_samples=min_samples,
        min_failure_rate=min_failure_rate,
    )
    gaps_by_domain = {gap.domain: gap for gap in gaps}

    generator = ProposalGenerator(analyzer=analyzer)
    candidates: List[DryRunCandidate] = []

    # Include every domain that had failures (qualified or not) for auditability.
    domains = [domain for domain in report.domains if domain.failures > 0]
    domains.sort(key=lambda item: (-item.failure_rate, -item.failures, item.domain))

    for domain in domains:
        cand: Optional[ImprovementCandidate] = report.candidate_for(domain.domain)
        qualified = cand is not None
        category = (
            cand.category
            if cand is not None
            else (domain.dominant_failure or FailureCategory.UNKNOWN.value)
        )
        observed_tool = ""
        error_code = ""
        suggestion = ""
        if cand is not None:
            observed_tool = str(cand.observed_tool or "")
            error_code = str(cand.error_code or "")
            suggestion = str(cand.suggestion or "")
        else:
            observed_tool = str(domain.observed_tool or "")
            error_code = str(domain.representative_error_code or "")

        gap = gaps_by_domain.get(domain.domain)
        gap_id = gap.id if gap else ""
        gap_kind = gap.kind if gap else ""
        gap_description = gap.description if gap else ""
        proposal_requirement = ""
        proposal_tool_name = ""
        proposal_module_name = ""

        # D1: tool-less USER_CORRECTION / UNKNOWN must not QUALIFY or propose
        # structured_result (GapAnalyzer already skips discourse-only corrections).
        discourse_noise = _is_tool_less_discourse_noise(
            category=category,
            observed_tool=observed_tool,
        )
        if discourse_noise:
            qualified = False

        if gap is not None and not gap.addressed and not discourse_noise:
            try:
                # for_gap builds an in-memory Proposal — does not call store.put.
                proposal: Proposal = generator.for_gap(gap)
                proposal_requirement = str(proposal.requirement.statement or "")
                proposal_tool_name = str(proposal.tool_name or "")
                proposal_module_name = str(proposal.module_name or "")
                if gap.observed_tool and not observed_tool:
                    observed_tool = gap.observed_tool
                if gap.error_code and not error_code:
                    error_code = gap.error_code
            except Exception:
                proposal_requirement = "(proposal generation failed - dry-run continues)"

        # Safety net: never surface structured_result for tool-less discourse.
        if discourse_noise or (
            proposal_module_name == "structured_result"
            and _is_tool_less_discourse_noise(
                category=category,
                observed_tool=observed_tool,
            )
        ):
            proposal_requirement = ""
            proposal_tool_name = ""
            proposal_module_name = ""
            qualified = False

        candidates.append(
            DryRunCandidate(
                domain=domain.domain,
                category=category,
                observed_tool=observed_tool,
                error_code=error_code,
                weighted_samples=int(domain.total),
                weighted_failures=int(domain.failures),
                failure_rate=float(domain.failure_rate),
                qualified=qualified,
                suggestion=suggestion,
                gap_id=gap_id,
                gap_kind=gap_kind,
                gap_description=gap_description,
                proposal_requirement=proposal_requirement,
                proposal_tool_name=proposal_tool_name,
                proposal_module_name=proposal_module_name,
                evidence=_evidence_for_domain(runtime, domain.domain),
                min_samples=min_samples,
                min_failure_rate=min_failure_rate,
            )
        )

    return SupervisedDryRunReport(
        evidence=EVIDENCE_RUNTIME,
        audit=audit,
        candidates=candidates,
        min_samples=min_samples,
        min_failure_rate=min_failure_rate,
        mutated=False,
    )
