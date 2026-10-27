# ==========================
# ELIAN — Self-Improvement / Experience Log
# ==========================
"""Interaction records the assistant reflects on.

One JSON row per interaction. Categories are the raw material for the
Reflection Engine: NO_TOOL_MATCH ("I couldn't find the right tool"),
VAGUE_ANSWER, USER_FRUSTRATION, TOOL_ERROR, TIMEOUT, WRONG_TOOL,
USER_CORRECTION, SUCCESS.

Storage: ``Data/self_improve/experience_log.json`` (schema-versioned,
corruption-tolerant via ``core.persistence.safe_read_json``).
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.persistence import safe_read_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "Data" / "self_improve"
DEFAULT_PATH = DATA_DIR / "experience_log.json"
SCHEMA_VERSION = 1


class FailureCategory(str, Enum):
    """Why an interaction did not go well. SUCCESS = it did."""

    SUCCESS = "SUCCESS"
    NO_TOOL_MATCH = "NO_TOOL_MATCH"      # "I couldn't find the right tool"
    WRONG_TOOL = "WRONG_TOOL"            # picked a tool that did not fit
    TOOL_ERROR = "TOOL_ERROR"            # tool ran and failed
    TIMEOUT = "TIMEOUT"                  # tool / provider timed out
    VAGUE_ANSWER = "VAGUE_ANSWER"        # answer was vague or unsourced
    USER_FRUSTRATION = "USER_FRUSTRATION"  # user was frustrated
    USER_CORRECTION = "USER_CORRECTION"  # user had to correct the assistant
    UNKNOWN = "UNKNOWN"


#: Failure categories only (SUCCESS excluded).
FAILURE_CATEGORIES = frozenset(
    category for category in FailureCategory if category is not FailureCategory.SUCCESS
)

#: Human wording used in reports / proposals.
CATEGORY_TEXT: Dict[FailureCategory, str] = {
    FailureCategory.SUCCESS: "handled without a problem",
    FailureCategory.NO_TOOL_MATCH: "I couldn't find the right tool",
    FailureCategory.WRONG_TOOL: "I picked a tool that didn't fit",
    FailureCategory.TOOL_ERROR: "the tool failed while running",
    FailureCategory.TIMEOUT: "the tool timed out",
    FailureCategory.VAGUE_ANSWER: "I gave a vague answer",
    FailureCategory.USER_FRUSTRATION: "the user was frustrated",
    FailureCategory.USER_CORRECTION: "the user had to correct me",
    FailureCategory.UNKNOWN: "uncategorised failure",
}

OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"

#: Evidence provenance — learning pipeline uses ``runtime`` only by default.
PROVENANCE_RUNTIME = "runtime"
PROVENANCE_SEED = "seed"
PROVENANCE_MANUAL = "manual"

#: Evidence scopes for Reflection / GapAnalyzer.
EVIDENCE_RUNTIME = "runtime"
EVIDENCE_ALL = "all"
EVIDENCE_SEED = "seed"  # seed + manual + legacy (everything non-runtime)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _coerce_category(value: Any) -> FailureCategory:
    try:
        return FailureCategory(str(value or "").strip().upper())
    except ValueError:
        return FailureCategory.UNKNOWN


def normalize_provenance(value: Any) -> str:
    """Map raw provenance to a known label. Unknown/empty → seed (never runtime)."""
    raw = str(value or "").strip().lower()
    if raw == PROVENANCE_RUNTIME:
        return PROVENANCE_RUNTIME
    if raw == PROVENANCE_MANUAL:
        return PROVENANCE_MANUAL
    if raw == PROVENANCE_SEED:
        return PROVENANCE_SEED
    # Legacy rows and typos must not silently enter the runtime learning set.
    return PROVENANCE_SEED


def normalize_repeat_count(value: Any) -> int:
    """Fail closed to 1 for missing/invalid counts."""
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 1
    return count if count >= 1 else 1


def select_evidence(
    entries: Optional[Iterable[ExperienceEntry]],
    evidence: str = EVIDENCE_RUNTIME,
) -> List[ExperienceEntry]:
    """Filter entries for analysis. Fail-closed: never fall back to all-on-error."""
    try:
        scope = str(evidence or EVIDENCE_RUNTIME).strip().lower()
        rows = list(entries or [])
        if scope == EVIDENCE_ALL:
            return rows
        if scope in (EVIDENCE_SEED, "non_runtime", "historical", "manual"):
            return [
                entry
                for entry in rows
                if normalize_provenance(getattr(entry, "provenance", ""))
                != PROVENANCE_RUNTIME
            ]
        # Default and any unknown scope → runtime ONLY (contamination-safe).
        return [
            entry
            for entry in rows
            if normalize_provenance(getattr(entry, "provenance", ""))
            == PROVENANCE_RUNTIME
        ]
    except Exception:
        return []


@dataclass
class ExperienceEntry:
    """A single logged interaction."""

    domain: str                                  # scheduling, web, smart_home, …
    category: FailureCategory
    summary: str                                 # what happened, one line
    outcome: str = ""                            # success / failure (derived if empty)
    id: str = ""
    ts: str = ""
    session_id: str = ""
    tool: str = ""                               # tool used, if any
    user_feedback: str = ""                      # verbatim frustration / correction
    latency_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    provenance: str = ""                         # runtime | seed | manual
    repeat_count: int = 1                        # equivalent occurrences represented

    def __post_init__(self) -> None:
        self.domain = str(self.domain or "").strip().lower() or "unknown"
        self.category = (
            self.category
            if isinstance(self.category, FailureCategory)
            else _coerce_category(self.category)
        )
        self.summary = str(self.summary or "").strip()
        self.id = str(self.id or "").strip() or f"exp_{uuid.uuid4().hex[:10]}"
        self.ts = str(self.ts or "").strip() or utc_now_iso()
        if not self.outcome:
            self.outcome = (
                OUTCOME_SUCCESS
                if self.category is FailureCategory.SUCCESS
                else OUTCOME_FAILURE
            )
        self.outcome = (
            OUTCOME_SUCCESS if self.outcome == OUTCOME_SUCCESS else OUTCOME_FAILURE
        )
        self.provenance = normalize_provenance(self.provenance)
        self.repeat_count = normalize_repeat_count(self.repeat_count)

    @property
    def is_failure(self) -> bool:
        return self.outcome == OUTCOME_FAILURE

    @property
    def is_runtime(self) -> bool:
        return self.provenance == PROVENANCE_RUNTIME

    @property
    def occurrence_weight(self) -> int:
        """How many equivalent runtime occurrences this row represents."""
        return normalize_repeat_count(self.repeat_count)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "session_id": self.session_id,
            "domain": self.domain,
            "category": self.category.value,
            "outcome": self.outcome,
            "summary": self.summary,
            "tool": self.tool,
            "user_feedback": self.user_feedback,
            "latency_ms": float(self.latency_ms or 0.0),
            "metadata": dict(self.metadata or {}),
            "provenance": self.provenance,
            "repeat_count": int(self.repeat_count),
        }

    @classmethod
    def from_dict(cls, row: Any) -> Optional["ExperienceEntry"]:
        """Tolerant parser: returns None for unusable rows instead of raising."""
        if not isinstance(row, dict):
            return None
        domain = str(row.get("domain") or "").strip()
        summary = str(row.get("summary") or "").strip()
        if not domain and not summary:
            return None
        try:
            return cls(
                domain=domain or "unknown",
                category=_coerce_category(row.get("category")),
                summary=summary,
                outcome=str(row.get("outcome") or ""),
                id=str(row.get("id") or ""),
                ts=str(row.get("ts") or ""),
                session_id=str(row.get("session_id") or ""),
                tool=str(row.get("tool") or ""),
                user_feedback=str(row.get("user_feedback") or ""),
                latency_ms=float(row.get("latency_ms") or 0.0),
                metadata=dict(row.get("metadata") or {}),
                provenance=str(row.get("provenance") or ""),
                repeat_count=row.get("repeat_count", 1),
            )
        except (TypeError, ValueError):
            return None


class ExperienceLogStore:
    """JSON-backed append-only-ish log. All reads tolerate corrupt files."""

    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)

    def load(self) -> List[ExperienceEntry]:
        raw, _recovered = safe_read_json(
            self.path, default={"schema": SCHEMA_VERSION, "entries": []}
        )
        rows: Iterable[Any]
        if isinstance(raw, dict):
            rows = raw.get("entries") or []
        elif isinstance(raw, list):
            rows = raw
        else:
            rows = []
        entries: List[ExperienceEntry] = []
        for row in rows:
            entry = ExperienceEntry.from_dict(row)
            if entry is not None:
                entries.append(entry)
        return entries

    def save(self, entries: List[ExperienceEntry]) -> None:
        payload = {
            "schema": SCHEMA_VERSION,
            "updated_at": utc_now_iso(),
            "entries": [entry.to_dict() for entry in entries],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        import json

        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self.path)

    def append(self, entry: ExperienceEntry | Dict[str, Any]) -> ExperienceEntry:
        if isinstance(entry, dict):
            entry = ExperienceEntry.from_dict(entry) or ExperienceEntry(
                domain=str(entry.get("domain") or "unknown"),
                category=_coerce_category(entry.get("category")),
                summary=str(entry.get("summary") or ""),
            )
        entries = self.load()
        entries.append(entry)
        self.save(entries)
        return entry

    def append_many(self, entries: List[ExperienceEntry]) -> List[ExperienceEntry]:
        existing = self.load()
        existing.extend(entries)
        self.save(existing)
        return entries

    def by_domain(self, domain: str) -> List[ExperienceEntry]:
        wanted = str(domain or "").strip().lower()
        return [entry for entry in self.load() if entry.domain == wanted]

    def domains(self) -> List[str]:
        return sorted({entry.domain for entry in self.load()})


def get_store(path: Path | str = DEFAULT_PATH) -> ExperienceLogStore:
    return ExperienceLogStore(path)
