# ==========================
# ELIAN — Self-Improvement / Reflection Engine
# ==========================
"""Turns Experience Log rows into concrete improvement candidates.

Deterministic (no LLM): aggregates failures per domain, picks the dominant
failure category and applies a category→suggestion template. Output is a
:class:`ReflectionReport` with at least one :class:`ImprovementCandidate`
whenever a domain is clearly struggling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.self_improve.experience_log import (
    CATEGORY_TEXT,
    EVIDENCE_RUNTIME,
    FAILURE_CATEGORIES,
    ExperienceEntry,
    ExperienceLogStore,
    FailureCategory,
    select_evidence,
    utc_now_iso,
)

DEFAULT_MIN_SAMPLES = 3
DEFAULT_MIN_FAILURE_RATE = 0.34

#: Best "one small code improvement" per failure category.
SUGGESTION_TEMPLATES: Dict[FailureCategory, str] = {
    FailureCategory.NO_TOOL_MATCH: (
        "Add a dedicated {tool} tool to the ToolRegistry for {domain} requests "
        "so the planner stops falling back to generic replies."
    ),
    FailureCategory.WRONG_TOOL: (
        "Add {domain}-specific gating to tool shortlisting so the planner stops "
        "choosing {tool} for {domain} requests."
    ),
    FailureCategory.TOOL_ERROR: (
        "Wrap {domain} tools with structured error handling in {tool}: classify "
        "permanent vs retryable failures and surface a degraded status instead "
        "of a silent failure."
    ),
    FailureCategory.TIMEOUT: (
        "Add a hard timeout + one retry with backoff to {domain} tool calls "
        "({tool}) and report 'degraded' when the provider does not answer."
    ),
    FailureCategory.VAGUE_ANSWER: (
        "Require sources and a confidence score for {domain} answers: reject "
        "answers below the confidence floor and ask a clarifying question."
    ),
    FailureCategory.USER_FRUSTRATION: (
        "Add a clarification-first repair step for {domain}: detect frustration "
        "in the reply loop and ask one targeted question before acting again."
    ),
    FailureCategory.USER_CORRECTION: (
        "Add a correction handler for {domain}: when the user corrects us, "
        "re-plan from the corrected constraint instead of repeating the old plan."
    ),
    FailureCategory.UNKNOWN: (
        "Add structured failure codes to {domain} tool calls so future "
        "reflections can pinpoint the cause."
    ),
}

#: Suggested module/tool name per domain (used when no concrete runtime tool).
DOMAIN_TOOL: Dict[str, str] = {
    "scheduling": "recurring_schedule",
    "calendar": "recurring_schedule",
    "web": "answer_confidence",
    "research": "answer_confidence",
    "smart_home": "device_targeting",
    "media": "media_targeting",
}


def tool_for_domain(domain: str) -> str:
    return DOMAIN_TOOL.get(str(domain or "").strip().lower(), "capability_helper")


def _occurrence_weight(entry: ExperienceEntry) -> int:
    try:
        weight = int(getattr(entry, "occurrence_weight", None) or 1)
        return weight if weight >= 1 else 1
    except (TypeError, ValueError):
        return 1


def error_code_from_entry(entry: ExperienceEntry) -> str:
    """Read representative error_code from entry metadata (fail-soft)."""
    try:
        meta = getattr(entry, "metadata", None) or {}
        if not isinstance(meta, dict):
            return ""
        code = str(meta.get("error_code") or "").strip()
        if code:
            return code[:64]
        tools = meta.get("tools")
        if isinstance(tools, list):
            for item in reversed(tools):
                if not isinstance(item, dict):
                    continue
                nested = str(item.get("error_code") or "").strip()
                if nested:
                    return nested[:64]
    except Exception:
        return ""
    return ""


def pick_dominant_key(counts: Dict[str, int]) -> str:
    """Highest weight wins; alphabetical key breaks ties (deterministic)."""
    best_key = ""
    best_count = -1
    for key, count in (counts or {}).items():
        name = str(key or "").strip()
        if not name:
            continue
        try:
            n = int(count)
        except (TypeError, ValueError):
            continue
        if n < 1:
            continue
        if n > best_count or (n == best_count and (not best_key or name < best_key)):
            best_key = name
            best_count = n
    return best_key


def resolve_suggestion_tool(
    *,
    domain: str,
    tool_counts: Optional[Dict[str, int]] = None,
) -> Tuple[str, bool]:
    """Return (tool_name, from_runtime_evidence).

    Priority: dominant observed runtime tool → DOMAIN_TOOL → capability_helper.
    """
    observed = pick_dominant_key(dict(tool_counts or {}))
    if observed:
        return observed, True
    return tool_for_domain(domain), False


@dataclass
class DomainReflection:
    domain: str
    total: int
    failures: int
    categories: Dict[str, int] = field(default_factory=dict)
    failure_examples: List[str] = field(default_factory=list)
    evidence_ids: List[str] = field(default_factory=list)
    #: failure-only: tool name → occurrence weight (all failure categories)
    tool_counts: Dict[str, int] = field(default_factory=dict)
    #: failure-only: category → (tool → weight); avoids cross-category bleed
    category_tool_counts: Dict[str, Dict[str, int]] = field(default_factory=dict)
    #: failure-only: error_code → occurrence weight (all tools)
    error_code_counts: Dict[str, int] = field(default_factory=dict)
    #: failure-only: tool → (error_code → weight)
    tool_error_counts: Dict[str, Dict[str, int]] = field(default_factory=dict)
    #: failure-only: category → tool → (error_code → weight)
    category_tool_error_counts: Dict[str, Dict[str, Dict[str, int]]] = field(
        default_factory=dict
    )

    @property
    def failure_rate(self) -> float:
        return round(self.failures / self.total, 3) if self.total else 0.0

    @property
    def dominant_failure(self) -> str:
        best, best_count = "", 0
        for name, count in self.categories.items():
            if name == FailureCategory.SUCCESS.value:
                continue
            if count > best_count:
                best, best_count = name, count
        return best

    def tools_for_category(self, category: str) -> Dict[str, int]:
        return dict(self.category_tool_counts.get(str(category or ""), {}) or {})

    @property
    def observed_tool(self) -> str:
        """Dominant tool for the dominant failure category (not other categories)."""
        dominant = self.dominant_failure
        if dominant:
            scoped = pick_dominant_key(self.tools_for_category(dominant))
            if scoped:
                return scoped
        return ""

    @property
    def representative_error_code(self) -> str:
        tool = self.observed_tool
        dominant = self.dominant_failure
        if tool and dominant:
            per_cat = self.category_tool_error_counts.get(dominant) or {}
            code = pick_dominant_key(per_cat.get(tool) or {})
            if code:
                return code
        if tool:
            per_tool = self.tool_error_counts.get(tool) or {}
            code = pick_dominant_key(per_tool)
            if code:
                return code
        return pick_dominant_key(self.error_code_counts)

    @property
    def struggling(self) -> bool:
        return (
            self.total >= DEFAULT_MIN_SAMPLES
            and self.failures > 0
            and self.failure_rate >= DEFAULT_MIN_FAILURE_RATE
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "total": self.total,
            "failures": self.failures,
            "failure_rate": self.failure_rate,
            "categories": dict(self.categories),
            "dominant_failure": self.dominant_failure,
            "struggling": self.struggling,
            "observed_tool": self.observed_tool,
            "representative_error_code": self.representative_error_code,
            "tool_counts": dict(self.tool_counts),
            "error_code_counts": dict(self.error_code_counts),
        }


@dataclass
class ImprovementCandidate:
    id: str
    domain: str
    category: str
    title: str
    suggestion: str
    evidence_ids: List[str]
    priority: float
    examples: List[str] = field(default_factory=list)
    observed_tool: str = ""
    error_code: str = ""
    tool_from_runtime: bool = False
    tool_counts: Dict[str, int] = field(default_factory=dict)
    error_code_counts: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "domain": self.domain,
            "category": self.category,
            "title": self.title,
            "suggestion": self.suggestion,
            "evidence_ids": list(self.evidence_ids),
            "priority": self.priority,
            "examples": list(self.examples),
            "observed_tool": self.observed_tool,
            "error_code": self.error_code,
            "tool_from_runtime": self.tool_from_runtime,
            "tool_counts": dict(self.tool_counts),
            "error_code_counts": dict(self.error_code_counts),
        }


@dataclass
class ReflectionReport:
    generated_at: str
    entry_count: int
    domains: List[DomainReflection]
    candidates: List[ImprovementCandidate]

    def top(self) -> Optional[ImprovementCandidate]:
        return self.candidates[0] if self.candidates else None

    def candidate_for(self, domain: str) -> Optional[ImprovementCandidate]:
        wanted = str(domain or "").strip().lower()
        for candidate in self.candidates:
            if candidate.domain == wanted:
                return candidate
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "entry_count": self.entry_count,
            "domains": [domain.to_dict() for domain in self.domains],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }

    def render(self) -> str:
        lines = [
            f"Reflection @ {self.generated_at} — {self.entry_count} experiences",
            "",
        ]
        for domain in self.domains:
            flag = "GAP " if domain.struggling else "ok  "
            tool_bit = domain.observed_tool or "-"
            err_bit = domain.representative_error_code or "-"
            lines.append(
                f"  [{flag}] {domain.domain:<14} {domain.failures}/{domain.total} failed "
                f"({domain.failure_rate:.0%}) dominant={domain.dominant_failure or '-'} "
                f"tool={tool_bit} error={err_bit}"
            )
        lines.append("")
        if not self.candidates:
            lines.append("  No improvement candidates: nothing is failing repeatedly.")
        for index, candidate in enumerate(self.candidates, start=1):
            lines.append(f"  {index}. {candidate.title}  (priority {candidate.priority})")
            lines.append(f"     {candidate.suggestion}")
            if candidate.observed_tool or candidate.error_code:
                lines.append(
                    f"     observed_tool={candidate.observed_tool or '-'} "
                    f"error_code={candidate.error_code or '-'} "
                    f"runtime_tool={candidate.tool_from_runtime}"
                )
            lines.append(
                f"     evidence: {len(candidate.evidence_ids)} entries "
                f"[{', '.join(candidate.evidence_ids[:5])}]"
            )
        return "\n".join(lines)


class ReflectionEngine:
    """Reads the Experience Log and produces improvement candidates."""

    def __init__(self, store: Optional[ExperienceLogStore] = None):
        self.store = store or ExperienceLogStore()

    def reflect(
        self,
        entries: Optional[List[ExperienceEntry]] = None,
        *,
        evidence: str = EVIDENCE_RUNTIME,
        min_samples: int = DEFAULT_MIN_SAMPLES,
        min_failure_rate: float = DEFAULT_MIN_FAILURE_RATE,
    ) -> ReflectionReport:
        """Aggregate failures. Default evidence scope is runtime-only."""
        try:
            source = list(entries) if entries is not None else self.store.load()
            rows = select_evidence(source, evidence)
        except Exception:
            # Fail closed: never analyze the unfiltered population by accident.
            rows = []

        per_domain: Dict[str, DomainReflection] = {}
        occurrence_total = 0
        for entry in rows:
            weight = _occurrence_weight(entry)
            occurrence_total += weight
            bucket = per_domain.setdefault(
                entry.domain,
                DomainReflection(domain=entry.domain, total=0, failures=0),
            )
            bucket.total += weight
            bucket.categories[entry.category.value] = (
                bucket.categories.get(entry.category.value, 0) + weight
            )
            if entry.is_failure and entry.category in FAILURE_CATEGORIES:
                bucket.failures += weight
                if entry.id not in bucket.evidence_ids:
                    bucket.evidence_ids.append(entry.id)
                if entry.summary and len(bucket.failure_examples) < 5:
                    bucket.failure_examples.append(entry.summary)
                tool_name = str(getattr(entry, "tool", "") or "").strip()
                cat_key = entry.category.value
                if tool_name:
                    bucket.tool_counts[tool_name] = (
                        bucket.tool_counts.get(tool_name, 0) + weight
                    )
                    per_cat_tools = bucket.category_tool_counts.setdefault(cat_key, {})
                    per_cat_tools[tool_name] = per_cat_tools.get(tool_name, 0) + weight
                code = error_code_from_entry(entry)
                if code:
                    bucket.error_code_counts[code] = (
                        bucket.error_code_counts.get(code, 0) + weight
                    )
                    if tool_name:
                        per_tool = bucket.tool_error_counts.setdefault(tool_name, {})
                        per_tool[code] = per_tool.get(code, 0) + weight
                        per_cat = bucket.category_tool_error_counts.setdefault(
                            cat_key, {}
                        )
                        per_tool_codes = per_cat.setdefault(tool_name, {})
                        per_tool_codes[code] = per_tool_codes.get(code, 0) + weight

        domains = sorted(
            per_domain.values(), key=lambda item: (-item.failure_rate, item.domain)
        )
        candidates = [
            self._candidate(domain, min_samples, min_failure_rate)
            for domain in domains
        ]
        candidates = [candidate for candidate in candidates if candidate is not None]
        candidates.sort(key=lambda item: (-item.priority, item.domain))

        return ReflectionReport(
            generated_at=utc_now_iso(),
            entry_count=occurrence_total,
            domains=domains,
            candidates=candidates,
        )

    def _candidate(
        self, domain: DomainReflection, min_samples: int, min_failure_rate: float
    ) -> Optional[ImprovementCandidate]:
        if domain.total < min_samples or domain.failures == 0:
            return None
        if domain.failure_rate < min_failure_rate:
            return None
        try:
            category = FailureCategory(domain.dominant_failure)
        except ValueError:
            category = FailureCategory.UNKNOWN
        # Tools are scoped to the dominant failure category so a TOOL_ERROR
        # row's tool cannot rewrite a NO_TOOL_MATCH suggestion template.
        scoped_tools = domain.tools_for_category(category.value)
        tool, from_runtime = resolve_suggestion_tool(
            domain=domain.domain, tool_counts=scoped_tools
        )
        error_code = ""
        if from_runtime and tool:
            per_cat = domain.category_tool_error_counts.get(category.value) or {}
            error_code = pick_dominant_key(per_cat.get(tool) or {})
            if not error_code:
                error_code = pick_dominant_key(domain.tool_error_counts.get(tool) or {})
        template = SUGGESTION_TEMPLATES.get(
            category, SUGGESTION_TEMPLATES[FailureCategory.UNKNOWN]
        )
        suggestion = template.format(tool=tool, domain=domain.domain)
        if from_runtime and error_code:
            suggestion = f"{suggestion} Observed error_code={error_code}."
        if from_runtime:
            title = (
                f"Improve {tool} for {domain.domain}: {CATEGORY_TEXT[category]}"
            )
        elif category in (FailureCategory.NO_TOOL_MATCH, FailureCategory.WRONG_TOOL):
            title = f"Add {tool} tool for {domain.domain}"
        else:
            title = (
                f"Improve {domain.domain} handling of: {CATEGORY_TEXT[category]}"
            )
        priority = round(domain.failure_rate * domain.failures, 2)
        # Expose category-scoped tool weights; fall back to domain-wide view.
        tool_view = scoped_tools or dict(domain.tool_counts)
        per_tool_errors: Dict[str, int] = {}
        if tool:
            per_cat_err = domain.category_tool_error_counts.get(category.value) or {}
            per_tool_errors = dict(per_cat_err.get(tool) or {})
            if not per_tool_errors:
                per_tool_errors = dict(domain.tool_error_counts.get(tool) or {})
        error_view = per_tool_errors or dict(domain.error_code_counts)
        return ImprovementCandidate(
            id=f"cand_{domain.domain}_{category.value.lower()}",
            domain=domain.domain,
            category=category.value,
            title=title,
            suggestion=suggestion,
            evidence_ids=list(domain.evidence_ids),
            priority=priority,
            examples=list(domain.failure_examples),
            observed_tool=tool if from_runtime else "",
            error_code=error_code if from_runtime else "",
            tool_from_runtime=from_runtime,
            tool_counts=tool_view,
            error_code_counts=error_view,
        )
