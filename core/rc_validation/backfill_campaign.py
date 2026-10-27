# ==========================
# ELIAN — manual recovery backfill for RC campaign (Phase 9.6+)
# ==========================
# Appends manual_recovery history to existing rc_validation_campaign.json.
# Does NOT modify stamped rc_validation_<timestamp>.json files.

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from Config import DATA_DIR
from core.rc_validation.campaign import all_test_ids, campaign_path
from core.rc_validation.merge_campaign import (
    classify_merged_release,
    diagnostics_dir,
    render_campaign_markdown,
    write_merged_outputs,
)

MANUAL_RECOVERY_SESSION = "manual_recovery"

# Final statuses from real hardware testing (manual recovery).
MANUAL_RECOVERY_STATUSES: Dict[str, Tuple[str, str]] = {
    "wake_word": ("PARTIAL", "legacy STT wake; custom elian.onnx not installed"),
    "wake_false_positive": ("PASS", ""),
    "stt_phrases": ("PARTIAL", ""),
    "natural_speech": ("FAIL", ""),
    "fastpath_volume": ("PASS", ""),
    "agent_multi": ("FAIL", ""),
    "memory_preference": ("FAIL", ""),
    "context_pronoun": ("FAIL", ""),
    "wait_phrase": ("FAIL", ""),
    "correction": ("PASS", ""),
    "negation": ("PASS", ""),
    "hypothetical": ("FAIL", ""),
    "reminder_real": ("FAIL", ""),
    "calendar_local": ("FAIL", ""),
    "media_controls": ("FAIL", ""),
    "ha_basic": ("FAIL", ""),
    "ha_followup": ("FAIL", ""),
    "vision_screen": ("FAIL", ""),
    "vision_risk": ("FAIL", ""),
    "barge_in": ("PASS", ""),
    "session_timeout": ("PARTIAL", ""),
    "proactive_battery": ("SKIP", "desktop PC — no battery"),
    "idle_stability": ("PASS", ""),
    "gpu_coexistence": (
        "FAIL",
        "RC/Elian process self-terminated during test",
    ),
}

EXPECTED_COUNTS = {
    "PASS": 6,
    "PARTIAL": 3,
    "FAIL": 14,
    "SKIP": 1,
    "UNRESOLVED": 0,
    "TOTAL": 24,
}


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _manual_history_entry(
    *,
    status: str,
    note: str,
    attempt: int,
    timestamp: str,
) -> Dict[str, Any]:
    return {
        "source": MANUAL_RECOVERY_SESSION,
        "session_id": MANUAL_RECOVERY_SESSION,
        "timestamp": timestamp,
        "status": status,
        "note": note,
        "attempt": attempt,
        "marked_at": timestamp,
    }


def apply_manual_recovery(
    campaign: Dict[str, Any],
    *,
    statuses: Optional[Dict[str, Tuple[str, str]]] = None,
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """Backfill test statuses; preserve existing history; append manual_recovery rows."""
    statuses = statuses or MANUAL_RECOVERY_STATUSES
    now = timestamp or _iso_now()
    tests = campaign.setdefault("tests", {})

    for tid in all_test_ids():
        if tid not in statuses:
            raise KeyError(f"Missing manual recovery status for test: {tid}")

        target_status, target_note = statuses[tid]
        entry = tests.setdefault(tid, {
            "status": "UNRESOLVED",
            "note": "",
            "attempts": 0,
            "last_session": "",
            "last_report": "",
            "last_marked_at": "",
            "history": [],
        })
        history = list(entry.get("history") or [])
        current_status = str(entry.get("status") or "UNRESOLVED")

        needs_entry = (
            current_status != target_status
            or not history
            or current_status == "UNRESOLVED"
        )
        # Always append for gpu_coexistence PARTIAL→FAIL even if we'd skip otherwise
        if tid == "gpu_coexistence" and current_status == "PARTIAL":
            needs_entry = True

        if needs_entry:
            history.append(
                _manual_history_entry(
                    status=target_status,
                    note=target_note,
                    attempt=len(history) + 1,
                    timestamp=now,
                )
            )

        entry["history"] = history
        entry["status"] = target_status
        entry["note"] = target_note
        entry["attempts"] = len(history)
        entry["last_session"] = MANUAL_RECOVERY_SESSION if needs_entry else (
            entry.get("last_session") or MANUAL_RECOVERY_SESSION
        )
        if needs_entry:
            entry["last_report"] = ""
            entry["last_marked_at"] = now
        tests[tid] = entry

    counts = {"PASS": 0, "FAIL": 0, "PARTIAL": 0, "SKIP": 0, "UNRESOLVED": 0}
    for tid in all_test_ids():
        st = (tests.get(tid) or {}).get("status", "UNRESOLVED")
        counts[st] = counts.get(st, 0) + 1
    campaign["counts"] = counts

    campaign.setdefault("issues", []).append({
        "severity": "INFO",
        "text": (
            "Manual recovery backfill: hardware test results restored after "
            "process restarts (source=manual_recovery)"
        ),
        "ts": now,
        "session_id": MANUAL_RECOVERY_SESSION,
        "source": MANUAL_RECOVERY_SESSION,
    })

    verdict, release_notes = classify_merged_release(campaign)
    campaign["release_recommendation"] = verdict
    campaign["release_notes"] = release_notes
    campaign["blockers"] = list(release_notes) if verdict == "NOT READY" else []
    campaign["updated_at"] = now
    return campaign


def load_campaign_file(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or campaign_path()
    if not p.exists():
        raise FileNotFoundError(f"Campaign file not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid campaign JSON: {p}")
    return data


def verify_counts(campaign: Dict[str, Any]) -> None:
    counts = campaign.get("counts") or {}
    for key, expected in EXPECTED_COUNTS.items():
        if key == "TOTAL":
            actual = sum(
                counts.get(k, 0)
                for k in ("PASS", "PARTIAL", "FAIL", "SKIP", "UNRESOLVED")
            )
        else:
            actual = counts.get(key, 0)
        if actual != expected:
            raise ValueError(
                f"Count mismatch {key}: expected {expected}, got {actual}"
            )


def run_backfill(
    *,
    diag_dir: Optional[Path] = None,
    quiet: bool = False,
) -> int:
    diag = diagnostics_dir(diag_dir)
    campaign = load_campaign_file(diag / "rc_validation_campaign.json")
    campaign = apply_manual_recovery(campaign)
    verify_counts(campaign)
    if campaign.get("release_recommendation") != "NOT READY":
        raise ValueError(
            f"Expected NOT READY, got {campaign.get('release_recommendation')}"
        )

    c_path, latest, md = write_merged_outputs(campaign, diag_dir=diag)
    if not quiet:
        counts = campaign.get("counts") or {}
        print("=== RC CAMPAIGN MANUAL RECOVERY ===")
        print(f"Campaign ID: {campaign.get('campaign_id')} (unchanged)")
        print(
            f"PASS={counts.get('PASS')} PARTIAL={counts.get('PARTIAL')} "
            f"FAIL={counts.get('FAIL')} SKIP={counts.get('SKIP')} "
            f"UNRESOLVED={counts.get('UNRESOLVED')}"
        )
        print(f"Release: {campaign.get('release_recommendation')}")
        print(f"Wrote: {c_path}")
        print(f"Wrote: {latest}")
        print(f"Wrote: {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_backfill())
