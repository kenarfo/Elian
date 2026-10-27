# ==========================
# ELIAN — Autonomous development persistent state
# ==========================
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .types import Phase


def _default_state() -> Dict[str, Any]:
    return {
        "mode": "stopped",
        "running": False,
        "stop_requested": False,
        "current_cycle": 0,
        "last_completed_cycle": 0,
        "current_phase": Phase.STOPPED.value,
        "current_candidate": "",
        "current_candidate_fingerprint": "",
        "last_checkpoint": "",
        "last_result": "",
        "last_success": "",
        "last_failure": "",
        "pid": None,
        "started_at": "",
        "updated_at": "",
        "unrecoverable_reason": "",
        "incomplete_cycle": False,
        "statistics": {
            "cycles_completed": 0,
            "successful_cycles": 0,
            "failed_cycles": 0,
            "rollback_count": 0,
            "no_progress_count": 0,
            "tests_passed": 0,
            "tests_failed": 0,
            "files_changed": 0,
            "features_added": 0,
            "bugs_fixed": 0,
            "success_rate": 0.0,
            "average_cycle_duration_s": 0.0,
            "total_cycle_duration_s": 0.0,
            "repeated_failure_candidates": [],
        },
        "quarantine": {},  # fingerprint -> {failures, last_result, until}
        "architecture_summary": {},
    }


class DevelopmentStateStore:
    """Atomic JSON state for autonomous self-development."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return _default_state()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return _default_state()
        if not isinstance(raw, dict):
            return _default_state()
        base = _default_state()
        base.update(raw)
        stats = _default_state()["statistics"]
        if isinstance(raw.get("statistics"), dict):
            stats.update(raw["statistics"])
        base["statistics"] = stats
        if not isinstance(base.get("quarantine"), dict):
            base["quarantine"] = {}
        return base

    def save(self, state: Dict[str, Any]) -> None:
        state = dict(state)
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            prefix=".self_develop_state_",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def update(self, **kwargs: Any) -> Dict[str, Any]:
        state = self.load()
        state.update(kwargs)
        self.save(state)
        return state

    def request_stop(self) -> Dict[str, Any]:
        return self.update(stop_requested=True)

    def clear_stop(self) -> Dict[str, Any]:
        return self.update(stop_requested=False)

    def status_summary(self) -> Dict[str, Any]:
        s = self.load()
        stats = s.get("statistics") or {}
        return {
            "running": bool(s.get("running")),
            "mode": s.get("mode") or "stopped",
            "stop_requested": bool(s.get("stop_requested")),
            "current_cycle": int(s.get("current_cycle") or 0),
            "current_phase": s.get("current_phase") or Phase.STOPPED.value,
            "current_candidate": s.get("current_candidate") or "",
            "successful_cycles": int(stats.get("successful_cycles") or 0),
            "failed_cycles": int(stats.get("failed_cycles") or 0),
            "rollback_count": int(stats.get("rollback_count") or 0),
            "no_progress_count": int(stats.get("no_progress_count") or 0),
            "last_successful_improvement": s.get("last_success") or "",
            "last_failed_improvement": s.get("last_failure") or "",
            "last_result": s.get("last_result") or "",
            "incomplete_cycle": bool(s.get("incomplete_cycle")),
            "unrecoverable_reason": s.get("unrecoverable_reason") or "",
            "pid": s.get("pid"),
            "updated_at": s.get("updated_at") or "",
        }


def default_state_path(project_root: Optional[Path] = None) -> Path:
    root = Path(project_root) if project_root else Path(__file__).resolve().parents[3]
    return root / "Data" / "self_improve" / "self_develop_state.json"
