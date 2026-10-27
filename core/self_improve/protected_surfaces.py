# ==========================
# ELIAN — Self-improve protected surfaces (observe/propose only)
# ==========================
"""Critical runtime paths that autonomous self-improve must NOT modify.

Mode for Fish/GPU workstream:
  OBSERVE → ANALYZE → PROPOSE → TEST → REPORT
Auto-apply / adopt against these surfaces is blocked until human approval
explicitly opts in via a future gate (not enabled here).
"""
from __future__ import annotations

from typing import Iterable, List, Tuple

# Relative to project root, forward slashes.
PROTECTED_PATH_PREFIXES: Tuple[str, ...] = (
    "core/multi_agent/resource_manager.py",
    "core/multi_agent/hardware.py",
    "core/ollama_lifecycle.py",
    "voice/fish_s2_backend.py",
    "voice/fish_s2_worker.py",
    "voice/tts_engine.py",
    "Voice.py",
)

PROTECTED_TOPICS: Tuple[str, ...] = (
    "gpu_resource_manager",
    "ollama_lifecycle",
    "fish_worker_lifecycle",
    "fish_cancel",
    "piper_fallback",
    "max_gpu_slots",
)

SELF_IMPROVE_MODE = "OBSERVE_ANALYZE_PROPOSE_TEST_REPORT"


def normalize_rel(path: str) -> str:
    return str(path or "").replace("\\", "/").lstrip("./")


def is_protected_path(path: str) -> bool:
    rel = normalize_rel(path)
    for pref in PROTECTED_PATH_PREFIXES:
        if rel == pref or rel.endswith("/" + pref) or rel.endswith(pref):
            return True
        if pref in rel:
            return True
    return False


def blocked_paths(paths: Iterable[str]) -> List[str]:
    return [normalize_rel(p) for p in paths if is_protected_path(p)]


def assert_writable(paths: Iterable[str]) -> None:
    bad = blocked_paths(paths)
    if bad:
        raise PermissionError(
            "self-improve blocked from modifying protected surfaces: " + ", ".join(bad)
        )


def policy_snapshot() -> dict:
    return {
        "mode": SELF_IMPROVE_MODE,
        "auto_deploy": False,
        "requires_test_gate": True,
        "requires_human_approval": True,
        "rollback_required": True,
        "protected_paths": list(PROTECTED_PATH_PREFIXES),
        "protected_topics": list(PROTECTED_TOPICS),
        "allowed_actions": [
            "observe_metrics",
            "analyze_logs",
            "propose_patches",
            "run_sandbox_tests_on_non_protected",
            "report",
        ],
        "forbidden_actions": [
            "auto_edit_protected_paths",
            "auto_adopt_without_approval",
            "bypass_unittest_or_agent_smoke",
        ],
    }
