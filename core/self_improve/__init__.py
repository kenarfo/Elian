# ==========================
# ELIAN — Self-Improvement System
# ==========================
"""Reflection → Gap Analysis → Proposal → Sandbox → Verification → Approval → Adopt.

Light exports only; heavier stages stay submodules so that logging an
experience never drags in the ToolRegistry:

- ``core.self_improve.experience_log``   — Experience Log storage (schema + categories)
- ``core.self_improve.runtime_collector`` — fail-open runtime turn → ExperienceEntry
- ``core.self_improve.reflection``       — Reflection Engine (log → improvement candidates)
- ``core.self_improve.gap_analysis``     — Gap Analysis + Capability Map
- ``core.self_improve.proposal``         — Proposal Generator (gap → requirement + code)
- ``core.self_improve.sandbox``          — Execution Sandbox (isolated run + AST safety scan)
- ``core.self_improve.verification``     — Verification Loop (ordered fail-closed checks)
- ``core.self_improve.approval``         — human-in-the-loop gate
- ``core.self_improve.tool_hot_swap``    — adoption into the ToolRegistry (no restart)
- ``core.self_improve.cli``              — ``python -m core.self_improve.cli``
"""

from __future__ import annotations

from core.self_improve.experience_log import (
    CATEGORY_TEXT,
    EVIDENCE_ALL,
    EVIDENCE_RUNTIME,
    PROVENANCE_MANUAL,
    PROVENANCE_RUNTIME,
    PROVENANCE_SEED,
    ExperienceEntry,
    ExperienceLogStore,
    FailureCategory,
    get_store,
    normalize_repeat_count,
    select_evidence,
)
from core.self_improve.reflection import (
    ImprovementCandidate,
    ReflectionEngine,
    ReflectionReport,
)

__all__ = [
    "CATEGORY_TEXT",
    "EVIDENCE_ALL",
    "EVIDENCE_RUNTIME",
    "PROVENANCE_MANUAL",
    "PROVENANCE_RUNTIME",
    "PROVENANCE_SEED",
    "ExperienceEntry",
    "ExperienceLogStore",
    "FailureCategory",
    "ImprovementCandidate",
    "ReflectionEngine",
    "ReflectionReport",
    "get_store",
    "normalize_repeat_count",
    "select_evidence",
]
