# ==========================
# ELIAN — plan identifiers / state (Phase 13 Stage A)
# ==========================
"""plan_id / step_id / attempt_id / idempotency. Full state machine later stages."""

from __future__ import annotations

import uuid

PLAN_CREATED = "CREATED"
PLAN_RUNNING = "RUNNING"
PLAN_PARTIAL = "PARTIAL"
PLAN_FAILED = "FAILED"
PLAN_COMPLETED = "COMPLETED"

STEP_PENDING = "PENDING"
STEP_RUNNING = "RUNNING"
STEP_SUCCEEDED = "SUCCEEDED"
STEP_FAILED = "FAILED"
STEP_SKIPPED = "SKIPPED"


def new_plan_id() -> str:
    return uuid.uuid4().hex[:4]


def make_step_id(plan_id: str, index: int) -> str:
    return f"{plan_id}.{int(index)}"


def make_idempotency_key(
    plan_id: str,
    step_id: str,
    semantic_action: str,
    canonical_target: str = "",
    normalized_value: str = "",
) -> str:
    target = str(canonical_target or "").strip().lower()
    value = str(normalized_value or "").strip().lower()
    action = str(semantic_action or "").strip().lower()
    return f"{plan_id}|{step_id}|{action}|{target}|{value}"
