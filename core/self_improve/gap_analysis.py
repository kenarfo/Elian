# ==========================
# ELIAN — Self-Improvement / Gap Analysis
# ==========================
"""Turns a ReflectionReport into named Capability Gaps + the Capability Map.

Example output: repeated scheduling failures become a
``Scheduling Capability Gap`` (kind ``scheduling``), persisted to
``Data/self_improve/capability_gaps.json`` and mirrored into
``Data/self_improve/capability_map.json`` (domain → HEALTHY/GAP).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.persistence import safe_read_json
from core.self_improve.experience_log import (
    EVIDENCE_RUNTIME,
    ExperienceLogStore,
    utc_now_iso,
)
from core.self_improve.reflection import (
    DEFAULT_MIN_FAILURE_RATE,
    DEFAULT_MIN_SAMPLES,
    DomainReflection,
    ImprovementCandidate,
    ReflectionEngine,
    ReflectionReport,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "Data" / "self_improve"
DEFAULT_GAPS_PATH = DATA_DIR / "capability_gaps.json"
DEFAULT_MAP_PATH = DATA_DIR / "capability_map.json"
SCHEMA_VERSION = 1

#: Failure category → what kind of capability is missing.
KIND_BY_CATEGORY: Dict[str, str] = {
    "NO_TOOL_MATCH": "tool_lookup",
    "WRONG_TOOL": "tool_lookup",
    "VAGUE_ANSWER": "answer_quality",
    "USER_FRUSTRATION": "answer_quality",
    "USER_CORRECTION": "answer_quality",
    "TOOL_ERROR": "reliability",
    "TIMEOUT": "reliability",
    "UNKNOWN": "general",
}

#: Domains whose "no tool" failures mean a scheduling/recurrence gap.
SCHEDULING_DOMAINS = frozenset({"scheduling", "calendar"})

SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_LOW = "LOW"

#: Capability Map status for a gap that already has an adopted remedy.
STATUS_ADDRESSED = "ADDRESSED"

#: Adoption record: module_name -> {gap_id, gap_name, tool_name, ...}.
DEFAULT_MANIFEST_PATH = DATA_DIR / "adopted.json"
LEARNED_DIR = PROJECT_ROOT / "core" / "self_improve" / "learned"


def adopted_remedies(
    manifest_path: Path | str = DEFAULT_MANIFEST_PATH,
    learned_dir: Path | str = LEARNED_DIR,
) -> Dict[str, List[str]]:
    """gap_id -> tool names adopted to answer it.

    Deliberately provenance-based: the link is recorded when a proposal is
    adopted, so nothing here depends on guessing from tool or domain names.
    A module whose file has since been removed (unlearned) no longer counts,
    and the gap falls back to an open GAP.
    """
    path = Path(manifest_path)
    learned = Path(learned_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    modules = raw.get("modules") if isinstance(raw, dict) else None
    if not isinstance(modules, dict):
        return {}
    remedies: Dict[str, List[str]] = {}
    for module_name, entry in modules.items():
        if not isinstance(entry, dict):
            continue
        if not (learned / f"{module_name}.py").exists():
            continue  # unlearned: the remedy is gone
        gap_id = str(entry.get("gap_id") or "")
        if not gap_id:
            continue
        tool_name = str(entry.get("tool_name") or module_name)
        names = remedies.setdefault(gap_id, [])
        if tool_name not in names:
            names.append(tool_name)
    return remedies


def _slug(value: str) -> str:
    keep = [ch if ch.isalnum() else "_" for ch in str(value or "").lower()]
    slug = "".join(keep)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_") or "unknown"


@dataclass
class CapabilityGap:
    id: str
    name: str                     # "Scheduling Capability Gap"
    domain: str
    kind: str                     # scheduling / tool_lookup / answer_quality / reliability / general
    description: str
    failure_modes: Dict[str, int]
    evidence_count: int
    failure_rate: float
    severity: str
    sample_evidence: List[str] = field(default_factory=list)
    candidate_id: str = ""
    detected_at: str = ""
    addressed: bool = False
    addressed_by: List[str] = field(default_factory=list)
    #: Dominant concrete runtime tool (empty → domain-level / template fallback).
    observed_tool: str = ""
    #: Representative error_code for observed_tool (occurrence-weighted).
    error_code: str = ""
    tool_from_runtime: bool = False
    tool_counts: Dict[str, int] = field(default_factory=dict)
    error_code_counts: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "domain": self.domain,
            "kind": self.kind,
            "description": self.description,
            "failure_modes": dict(self.failure_modes),
            "evidence_count": self.evidence_count,
            "failure_rate": self.failure_rate,
            "severity": self.severity,
            "sample_evidence": list(self.sample_evidence),
            "candidate_id": self.candidate_id,
            "detected_at": self.detected_at,
            "addressed": self.addressed,
            "addressed_by": list(self.addressed_by),
            "observed_tool": self.observed_tool,
            "error_code": self.error_code,
            "tool_from_runtime": self.tool_from_runtime,
            "tool_counts": dict(self.tool_counts),
            "error_code_counts": dict(self.error_code_counts),
        }

    @classmethod
    def from_dict(cls, row: Any) -> Optional["CapabilityGap"]:
        if not isinstance(row, dict) or not row.get("id"):
            return None
        return cls(
            id=str(row["id"]),
            name=str(row.get("name") or row["id"]),
            domain=str(row.get("domain") or "unknown"),
            kind=str(row.get("kind") or "general"),
            description=str(row.get("description") or ""),
            failure_modes={
                str(key): int(value)
                for key, value in dict(row.get("failure_modes") or {}).items()
            },
            evidence_count=int(row.get("evidence_count") or 0),
            failure_rate=float(row.get("failure_rate") or 0.0),
            severity=str(row.get("severity") or SEVERITY_LOW),
            sample_evidence=[str(item) for item in row.get("sample_evidence") or []],
            candidate_id=str(row.get("candidate_id") or ""),
            detected_at=str(row.get("detected_at") or ""),
            addressed=bool(row.get("addressed")),
            addressed_by=[str(item) for item in row.get("addressed_by") or []],
            observed_tool=str(row.get("observed_tool") or ""),
            error_code=str(row.get("error_code") or ""),
            tool_from_runtime=bool(row.get("tool_from_runtime")),
            tool_counts={
                str(key): int(value)
                for key, value in dict(row.get("tool_counts") or {}).items()
            },
            error_code_counts={
                str(key): int(value)
                for key, value in dict(row.get("error_code_counts") or {}).items()
            },
        )


def _severity(failure_rate: float, failures: int) -> str:
    if failure_rate >= 0.6 and failures >= 5:
        return SEVERITY_HIGH
    if failure_rate >= 0.45 and failures >= 3:
        return SEVERITY_MEDIUM
    return SEVERITY_LOW


def _kind_for(domain: str, dominant: str) -> str:
    if domain in SCHEDULING_DOMAINS and dominant in ("NO_TOOL_MATCH", "WRONG_TOOL"):
        return "scheduling"
    return KIND_BY_CATEGORY.get(dominant, "general")


class GapAnalyzer:
    """Pattern detector: repeated failures in one domain = Capability Gap."""

    def __init__(
        self,
        store: Optional[ExperienceLogStore] = None,
        *,
        gaps_path: Path | str = DEFAULT_GAPS_PATH,
        map_path: Path | str = DEFAULT_MAP_PATH,
        manifest_path: Path | str = DEFAULT_MANIFEST_PATH,
        learned_dir: Path | str = LEARNED_DIR,
    ):
        self.store = store or ExperienceLogStore()
        self.manifest_path = Path(manifest_path)
        self.learned_dir = Path(learned_dir)
        self.gaps_path = Path(gaps_path)
        self.map_path = Path(map_path)

    def analyze(
        self,
        report: Optional[ReflectionReport] = None,
        *,
        evidence: str = EVIDENCE_RUNTIME,
        min_samples: int = DEFAULT_MIN_SAMPLES,
        min_failure_rate: float = DEFAULT_MIN_FAILURE_RATE,
    ) -> List[CapabilityGap]:
        if report is None:
            report = ReflectionEngine(self.store).reflect(
                evidence=evidence,
                min_samples=min_samples,
                min_failure_rate=min_failure_rate,
            )
        gaps: List[CapabilityGap] = []
        for domain in report.domains:
            if domain.total < min_samples or domain.failures == 0:
                continue
            if domain.failure_rate < min_failure_rate:
                continue
            # Discourse-only / tool-less signals are not capability gaps.
            # Aligns with supervised dry-run D1: tool-less UNKNOWN must not
            # open a general gap that would propose structured_result.
            dominant = str(getattr(domain, "dominant_failure", "") or "")
            if dominant in (
                "USER_CORRECTION",
                "USER_FRUSTRATION",
                "UNKNOWN",
            ) and not getattr(domain, "observed_tool", ""):
                continue
            candidate = report.candidate_for(domain.domain)
            gaps.append(
                self._build_gap(
                    domain,
                    candidate.id if candidate else "",
                    candidate=candidate,
                )
            )
        # A gap with an adopted remedy is not closed yet, but it is no longer
        # an open problem: mark it so nobody re-proposes the same fix.
        remedies = adopted_remedies(self.manifest_path, self.learned_dir)
        for gap in gaps:
            tools = remedies.get(gap.id)
            if tools:
                gap.addressed = True
                gap.addressed_by = list(tools)
        return gaps

    def _build_gap(
        self,
        domain: DomainReflection,
        candidate_id: str,
        *,
        candidate: Optional[ImprovementCandidate] = None,
    ) -> CapabilityGap:
        dominant = domain.dominant_failure
        kind = _kind_for(domain.domain, dominant)
        name = f"{domain.domain.replace('_', ' ').title()} Capability Gap"
        modes = {
            key: value
            for key, value in sorted(
                domain.categories.items(), key=lambda item: (-item[1], item[0])
            )
            if key != "SUCCESS"
        }
        observed_tool = ""
        error_code = ""
        tool_from_runtime = False
        tool_counts = dict(domain.tool_counts)
        error_code_counts = dict(domain.error_code_counts)
        if candidate is not None:
            observed_tool = str(getattr(candidate, "observed_tool", "") or "")
            error_code = str(getattr(candidate, "error_code", "") or "")
            tool_from_runtime = bool(getattr(candidate, "tool_from_runtime", False))
            if getattr(candidate, "tool_counts", None):
                tool_counts = dict(candidate.tool_counts)
            if getattr(candidate, "error_code_counts", None):
                error_code_counts = dict(candidate.error_code_counts)
        if not observed_tool and domain.observed_tool:
            observed_tool = domain.observed_tool
            tool_from_runtime = True
        if not error_code and domain.representative_error_code:
            error_code = domain.representative_error_code
        description = (
            f"{domain.failures} of {domain.total} {domain.domain} interactions failed "
            f"({domain.failure_rate:.0%}); dominant failure mode: {dominant or 'UNKNOWN'}."
        )
        if observed_tool:
            description += f" Dominant observed tool: {observed_tool}."
        if error_code:
            description += f" Representative error_code: {error_code}."
        if domain.failure_examples:
            description += f' Example: "{domain.failure_examples[0]}"'
        # Gap id stays domain-scoped for adoption / historical compatibility.
        return CapabilityGap(
            id=f"gap_{_slug(domain.domain)}",
            name=name,
            domain=domain.domain,
            kind=kind,
            description=description,
            failure_modes=modes,
            evidence_count=int(domain.failures),
            failure_rate=domain.failure_rate,
            severity=_severity(domain.failure_rate, domain.failures),
            sample_evidence=list(domain.evidence_ids[:8]),
            candidate_id=candidate_id,
            detected_at=utc_now_iso(),
            observed_tool=observed_tool,
            error_code=error_code,
            tool_from_runtime=tool_from_runtime,
            tool_counts=tool_counts,
            error_code_counts=error_code_counts,
        )

    # ---- persistence -------------------------------------------------
    def save(self, gaps: List[CapabilityGap]) -> Path:
        payload = {
            "schema": SCHEMA_VERSION,
            "updated_at": utc_now_iso(),
            "gaps": [gap.to_dict() for gap in gaps],
        }
        self._write_json(self.gaps_path, payload)
        return self.gaps_path

    def load(self) -> List[CapabilityGap]:
        raw, _ = safe_read_json(self.gaps_path, default={"gaps": []})
        rows = raw.get("gaps") if isinstance(raw, dict) else raw
        gaps = [CapabilityGap.from_dict(row) for row in rows or []]
        return [gap for gap in gaps if gap is not None]

    def write_capability_map(
        self,
        gaps: Optional[List[CapabilityGap]] = None,
        *,
        evidence: str = EVIDENCE_RUNTIME,
    ) -> Dict[str, Any]:
        """Capability Map: every seen domain with HEALTHY/GAP/ADDRESSED status."""
        if gaps is None:
            gaps = self.load()
        reflections = ReflectionEngine(self.store).reflect(evidence=evidence)
        by_domain = {gap.domain: gap for gap in gaps}
        capabilities: Dict[str, Any] = {}
        for domain in reflections.domains:
            gap = by_domain.get(domain.domain)
            if gap is None:
                status = "HEALTHY"
            elif gap.addressed:
                status = STATUS_ADDRESSED
            else:
                status = "GAP"
            capabilities[domain.domain] = {
                "status": status,
                "gap_id": gap.id if gap else "",
                "kind": gap.kind if gap else "",
                "failure_rate": domain.failure_rate,
                "failures": domain.failures,
                "total": domain.total,
                "addressed_by": list(gap.addressed_by) if gap else [],
                "observed_tool": (
                    (gap.observed_tool if gap else "") or domain.observed_tool or ""
                ),
                "error_code": (
                    (gap.error_code if gap else "")
                    or domain.representative_error_code
                    or ""
                ),
            }
        payload = {
            "schema": SCHEMA_VERSION,
            "updated_at": utc_now_iso(),
            "capabilities": capabilities,
        }
        self._write_json(self.map_path, payload)
        return capabilities

    def load_capability_map(self) -> Dict[str, Any]:
        raw, _ = safe_read_json(self.map_path, default={"capabilities": {}})
        if isinstance(raw, dict):
            return dict(raw.get("capabilities") or {})
        return {}

    @staticmethod
    def _write_json(path: Path, payload: Dict[str, Any]) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def run_gap_analysis(
    store: Optional[ExperienceLogStore] = None,
    *,
    evidence: str = EVIDENCE_RUNTIME,
    gaps_path: Path | str = DEFAULT_GAPS_PATH,
    map_path: Path | str = DEFAULT_MAP_PATH,
) -> List[CapabilityGap]:
    """One call: reflect → gaps → persist gaps + capability map."""
    analyzer = GapAnalyzer(store, gaps_path=gaps_path, map_path=map_path)
    gaps = analyzer.analyze(evidence=evidence)
    analyzer.save(gaps)
    analyzer.write_capability_map(gaps, evidence=evidence)
    return gaps
