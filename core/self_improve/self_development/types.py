# ==========================
# ELIAN — Autonomous self-development types
# ==========================
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Phase(str, Enum):
    OBSERVE = "OBSERVE"
    ANALYZE = "ANALYZE"
    PRIORITIZE = "PRIORITIZE"
    PLAN = "PLAN"
    IMPLEMENT = "IMPLEMENT"
    TEST = "TEST"
    EVALUATE = "EVALUATE"
    KEEP = "KEEP"
    ROLLBACK = "ROLLBACK"
    LEARN = "LEARN"
    NEXT = "NEXT"
    STOPPED = "STOPPED"
    UNRECOVERABLE = "UNRECOVERABLE"


class ImprovementKind(str, Enum):
    BUG_FIX = "BUG_FIX"
    RELIABILITY = "RELIABILITY"
    CAPABILITY = "CAPABILITY"
    PLANNING = "PLANNING"
    MEMORY = "MEMORY"
    TOOLS = "TOOLS"
    INTEGRATION = "INTEGRATION"
    PERFORMANCE = "PERFORMANCE"
    UX = "UX"
    PROACTIVITY = "PROACTIVITY"
    CONTEXT = "CONTEXT"
    ERROR_RECOVERY = "ERROR_RECOVERY"
    TESTING = "TESTING"
    ARCHITECTURE = "ARCHITECTURE"
    SELF_IMPROVEMENT = "SELF_IMPROVEMENT"
    SYNTHETIC = "SYNTHETIC"


class CycleResult(str, Enum):
    KEPT = "KEPT"
    ROLLED_BACK = "ROLLED_BACK"
    NO_PROGRESS = "NO_PROGRESS"
    SKIPPED = "SKIPPED"
    UNRECOVERABLE = "UNRECOVERABLE"
    STOPPED = "STOPPED"


@dataclass
class ImprovementCandidate:
    id: str
    title: str
    kind: str
    reason: str
    score: float
    fingerprint: str
    domain: str = ""
    category: str = ""
    observed_tool: str = ""
    error_code: str = ""
    gap_id: str = ""
    source: str = ""  # runtime | repository | tests | synthetic
    files_hint: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    attempt_count: int = 0
    quarantined: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "reason": self.reason,
            "score": self.score,
            "fingerprint": self.fingerprint,
            "domain": self.domain,
            "category": self.category,
            "observed_tool": self.observed_tool,
            "error_code": self.error_code,
            "gap_id": self.gap_id,
            "source": self.source,
            "files_hint": list(self.files_hint),
            "metadata": dict(self.metadata),
            "attempt_count": self.attempt_count,
            "quarantined": self.quarantined,
        }

    @classmethod
    def from_dict(cls, row: Any) -> Optional["ImprovementCandidate"]:
        if not isinstance(row, dict) or not row.get("id"):
            return None
        return cls(
            id=str(row["id"]),
            title=str(row.get("title") or row["id"]),
            kind=str(row.get("kind") or ImprovementKind.CAPABILITY.value),
            reason=str(row.get("reason") or ""),
            score=float(row.get("score") or 0.0),
            fingerprint=str(row.get("fingerprint") or row["id"]),
            domain=str(row.get("domain") or ""),
            category=str(row.get("category") or ""),
            observed_tool=str(row.get("observed_tool") or ""),
            error_code=str(row.get("error_code") or ""),
            gap_id=str(row.get("gap_id") or ""),
            source=str(row.get("source") or ""),
            files_hint=[str(x) for x in row.get("files_hint") or []],
            metadata=dict(row.get("metadata") or {}),
            attempt_count=int(row.get("attempt_count") or 0),
            quarantined=bool(row.get("quarantined")),
        )


@dataclass
class ImplementationPlan:
    candidate_id: str
    summary: str
    actions: List[Dict[str, Any]] = field(default_factory=list)
    test_targets: List[str] = field(default_factory=list)
    high_risk_self_dev: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "summary": self.summary,
            "actions": list(self.actions),
            "test_targets": list(self.test_targets),
            "high_risk_self_dev": self.high_risk_self_dev,
        }


@dataclass
class TestRunResult:
    passed: bool
    command: str
    exit_code: int
    summary: str
    duration_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "command": self.command,
            "exit_code": self.exit_code,
            "summary": self.summary,
            "duration_s": self.duration_s,
        }


@dataclass
class CycleRecord:
    cycle_id: str
    timestamp: str
    phase: str
    candidate: str
    reason: str
    files_changed: List[str]
    tests_run: List[str]
    result: str
    failure_reason: str = ""
    checkpoint_id: str = ""
    attempts: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "timestamp": self.timestamp,
            "phase": self.phase,
            "candidate": self.candidate,
            "reason": self.reason,
            "files_changed": list(self.files_changed),
            "tests_run": list(self.tests_run),
            "result": self.result,
            "failure_reason": self.failure_reason,
            "checkpoint_id": self.checkpoint_id,
            "attempts": self.attempts,
        }
