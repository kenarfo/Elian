# ==========================
# ELIAN — RC validation campaign persistence (Phase 9.6)
# ==========================
# Survives process restarts. Does not replace stamped session JSON files.

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from Config import DATA_DIR
from core.rc_validation.tests import RC_TEST_CASES
from core.version import VERSION


CAMPAIGN_FILENAME = "rc_validation_campaign.json"
VALID_RESULTS = frozenset({"PASS", "FAIL", "PARTIAL", "SKIP", "PENDING"})


def campaign_path(base: Optional[Path] = None) -> Path:
    root = Path(base) if base else Path(DATA_DIR) / "diagnostics"
    root.mkdir(parents=True, exist_ok=True)
    return root / CAMPAIGN_FILENAME


def all_test_ids() -> List[str]:
    return [t.id for t in RC_TEST_CASES]


def optional_test_ids() -> frozenset:
    return frozenset(t.id for t in RC_TEST_CASES if t.skip_if)


def required_test_ids() -> frozenset:
    return frozenset(t.id for t in RC_TEST_CASES if not t.skip_if)


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{time.strftime('%Y%m%dT%H%M%S')}_{secrets.token_hex(3)}"


def _empty_test_entry() -> Dict[str, Any]:
    return {
        "status": "PENDING",
        "note": "",
        "attempts": 0,
        "updated_at": "",
        "session_id": "",
    }


def create_campaign(*, version: str = VERSION) -> Dict[str, Any]:
    tests = {tid: _empty_test_entry() for tid in all_test_ids()}
    return {
        "campaign_id": _new_id("campaign"),
        "status": "in_progress",
        "started_at": _iso_now(),
        "updated_at": _iso_now(),
        "elian_version": version,
        "tests": tests,
        "sessions": [],
        "active_session_id": "",
        "session_alive": False,
        "wake": {
            "detections": 0,
            "false_positives": 0,
            "missed": 0,
        },
        "aggregated_metrics": {},
        "issues": [],
        "blockers": [],
        "unexpected_exit_pending_review": False,
        "finished_at": "",
    }


def load_campaign(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    if path is None:
        p = campaign_path()
    elif path.is_dir():
        p = path / CAMPAIGN_FILENAME
    else:
        p = path

    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return _ensure_all_tests(data)


def _ensure_all_tests(data: Dict[str, Any]) -> Dict[str, Any]:
    tests = data.setdefault("tests", {})
    for tid in all_test_ids():
        if tid not in tests:
            tests[tid] = _empty_test_entry()
    return data


def save_campaign(campaign: Dict[str, Any], path: Optional[Path] = None) -> Path:
    p = path or campaign_path()
    if p.is_dir():
        p = p / CAMPAIGN_FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    campaign["updated_at"] = _iso_now()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(campaign, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(p)
    return p


def delete_campaign(path: Optional[Path] = None) -> None:
    p = path or campaign_path()
    if p.is_dir():
        p = p / CAMPAIGN_FILENAME
    if p.exists():
        p.unlink()


def count_completed(campaign: Dict[str, Any]) -> Tuple[int, int]:
    tests = campaign.get("tests") or {}
    total = len(all_test_ids())
    done = sum(
        1
        for tid in all_test_ids()
        if (tests.get(tid) or {}).get("status", "PENDING") != "PENDING"
    )
    return done, total


def get_or_resume_campaign(
    *,
    path: Optional[Path] = None,
    version: str = VERSION,
) -> Tuple[Dict[str, Any], bool, List[str]]:
    """
    Returns (campaign, resumed, messages).
    Detects unexpected exit of previous session.
    """
    messages: List[str] = []
    existing = load_campaign(path)
    if existing and existing.get("status") != "finished":
        done, total = count_completed(existing)
        messages.append("[RC] Existing validation campaign found")
        messages.append(f"[RC] Completed: {done}/{total}")
        messages.append("[RC] Resuming campaign")

        if existing.get("session_alive"):
            # Previous process died without clean shutdown
            sessions = existing.setdefault("sessions", [])
            active = existing.get("active_session_id") or ""
            for sess in sessions:
                if sess.get("session_id") == active:
                    sess["unexpected_exit"] = True
                    sess["clean_shutdown"] = False
                    sess["ended_at"] = sess.get("ended_at") or _iso_now()
            existing["unexpected_exit_pending_review"] = True
            issue = {
                "severity": "HIGH",
                "text": (
                    f"Previous RC session ended unexpectedly "
                    f"(session_id={active or 'unknown'}). Manual review required."
                ),
                "ts": _iso_now(),
            }
            existing.setdefault("issues", []).append(issue)
            messages.append("[RC] Previous session: unexpected_exit=true (HIGH)")

        return existing, True, messages

    campaign = create_campaign(version=version)
    messages.append(f"[RC] New validation campaign: {campaign['campaign_id']}")
    return campaign, False, messages


def start_session(campaign: Dict[str, Any]) -> str:
    session_id = _new_id("session")
    campaign.setdefault("sessions", []).append({
        "session_id": session_id,
        "started_at": _iso_now(),
        "ended_at": "",
        "unexpected_exit": False,
        "clean_shutdown": False,
    })
    campaign["active_session_id"] = session_id
    campaign["session_alive"] = True
    campaign["status"] = "in_progress"
    return session_id


def end_session(
    campaign: Dict[str, Any],
    *,
    clean: bool = True,
    unexpected: bool = False,
) -> None:
    active = campaign.get("active_session_id") or ""
    for sess in campaign.get("sessions") or []:
        if sess.get("session_id") == active:
            sess["ended_at"] = _iso_now()
            sess["clean_shutdown"] = bool(clean) and not unexpected
            sess["unexpected_exit"] = bool(unexpected)
            break
    campaign["session_alive"] = False
    if unexpected:
        campaign["unexpected_exit_pending_review"] = True


def finish_campaign(campaign: Dict[str, Any]) -> None:
    campaign["status"] = "finished"
    campaign["finished_at"] = _iso_now()
    campaign["session_alive"] = False


def record_test_result(
    campaign: Dict[str, Any],
    test_id: str,
    result: str,
    *,
    note: str = "",
    session_id: str = "",
) -> bool:
    result = result.upper()
    if result not in ("PASS", "FAIL", "PARTIAL", "SKIP", "BLOCKED", "UNAVAILABLE"):
        return False
    tests = campaign.setdefault("tests", {})
    if test_id not in tests and test_id not in all_test_ids():
        # Allow unknown IDs but track them
        tests[test_id] = _empty_test_entry()
    entry = tests.setdefault(test_id, _empty_test_entry())
    entry["status"] = result
    entry["note"] = note
    entry["attempts"] = int(entry.get("attempts") or 0) + 1
    entry["updated_at"] = _iso_now()
    entry["session_id"] = session_id or campaign.get("active_session_id") or ""
    history = entry.setdefault("history", [])
    history.append(
        {
            "session_id": entry["session_id"] or "phase14_retest",
            "timestamp": _iso_now(),
            "status": result,
            "note": note,
            "attempt": entry["attempts"],
            "marked_at": _iso_now(),
            "source": "phase14_runtime_stabilization",
        }
    )
    return True


def add_issue(
    campaign: Dict[str, Any],
    text: str,
    *,
    severity: str = "INFO",
) -> None:
    campaign.setdefault("issues", []).append({
        "severity": severity.upper(),
        "text": text,
        "ts": _iso_now(),
    })


def merge_session_metrics(campaign: Dict[str, Any], metrics: Dict[str, Any]) -> None:
    """Accumulate wake counters and keep latest routing snapshot."""
    wake = campaign.setdefault("wake", {})
    wake["detections"] = int(wake.get("detections") or 0) + int(
        metrics.get("wake_count") or 0
    )
    wake["false_positives"] = int(wake.get("false_positives") or 0) + int(
        metrics.get("wake_false_positive_manual") or 0
    )
    wake["missed"] = int(wake.get("missed") or 0) + int(
        metrics.get("wake_missed_manual") or 0
    )

    agg = campaign.setdefault("aggregated_metrics", {})
    # Sum counters across sessions
    for key in (
        "interactions",
        "routable_interactions",
        "fast_path_count",
        "agent_count",
        "brain_count",
        "legacy_route_count",
        "agent_success",
        "agent_failure",
        "tool_calls",
        "tool_success",
        "tool_failure",
        "crashes",
        "stt_failures",
        "tts_failures",
        "vision_calls",
        "vision_failures",
        "ha_calls",
        "ha_failures",
        "legacy_fallback_used",
    ):
        agg[key] = int(agg.get(key) or 0) + int(metrics.get(key) or 0)

    # Merge legacy breakdown
    legacy = agg.setdefault("legacy_breakdown", {})
    for k, v in (metrics.get("legacy_breakdown") or {}).items():
        legacy[k] = int(legacy.get(k) or 0) + int(v or 0)

    # Routing counts
    routes = agg.setdefault("routing_counts", {})
    for k, v in (metrics.get("routing_counts") or {}).items():
        routes[k] = int(routes.get(k) or 0) + int(v or 0)

    # Latest wake model meta
    if metrics.get("wake_model"):
        agg["wake_model"] = metrics["wake_model"]

    # Recompute distribution
    routable = max(1, int(agg.get("routable_interactions") or 0))
    rc = routes
    agg["routing_distribution_pct"] = {
        k: round(100.0 * int(rc.get(k) or 0) / routable, 1)
        for k in ("fast_path", "agent", "brain", "legacy")
    }
    legacy_count = int(rc.get("legacy") or 0)
    agg["legacy_fallback_rate_pct"] = round(
        100.0 * legacy_count / routable, 2
    ) if agg.get("routable_interactions") else 0.0


def campaign_counts(campaign: Dict[str, Any]) -> Dict[str, int]:
    tests = campaign.get("tests") or {}
    counts = {
        "PASS": 0,
        "FAIL": 0,
        "PARTIAL": 0,
        "SKIP": 0,
        "BLOCKED": 0,
        "UNAVAILABLE": 0,
        "PENDING": 0,
    }
    for tid in all_test_ids():
        status = (tests.get(tid) or {}).get("status", "PENDING")
        if status not in counts:
            status = "PENDING"
        counts[status] += 1
    return counts


def unresolved_tests(campaign: Dict[str, Any]) -> List[str]:
    tests = campaign.get("tests") or {}
    return [
        tid
        for tid in all_test_ids()
        if (tests.get(tid) or {}).get("status", "PENDING") == "PENDING"
    ]


def classify_campaign_release(campaign: Dict[str, Any]) -> Tuple[str, List[str]]:
    """
    Deterministic release rules for the whole campaign.

    NOT READY:
      - any required test FAIL
      - runtime crash / unexpected exit pending review
      - unsafe wrong-target / CUDA/OOM blockers in issues

    READY WITH ISSUES:
      - FAIL=0
      - PARTIAL or justified optional SKIP
      - missing custom elian.onnx (max this level)

    READY:
      - FAIL=0, PARTIAL=0
      - all required PASS
      - only optional SKIP allowed
      - no unexpected_exit pending
      - custom acoustic model installed (else capped to READY WITH ISSUES)
    """
    notes: List[str] = []
    blockers: List[str] = []
    counts = campaign_counts(campaign)
    tests = campaign.get("tests") or {}
    optional = optional_test_ids()
    required = required_test_ids()

    # Required FAIL → NOT READY
    required_fails = [
        tid for tid in required
        if (tests.get(tid) or {}).get("status") == "FAIL"
    ]
    # Any FAIL on catalog tests also blocks READY
    any_fails = [
        tid for tid in all_test_ids()
        if (tests.get(tid) or {}).get("status") == "FAIL"
    ]
    if any_fails:
        blockers.append(f"FAIL tests: {', '.join(any_fails)}")

    if campaign.get("unexpected_exit_pending_review"):
        blockers.append("Unexpected process exit — pending manual review")

    # Crash / high severity issues
    for issue in campaign.get("issues") or []:
        text = str(issue.get("text") or "").lower()
        sev = str(issue.get("severity") or "").upper()
        if sev == "HIGH":
            blockers.append(issue.get("text") or "HIGH severity issue")
        if any(
            token in text
            for token in (
                "cuda",
                "oom",
                "out of memory",
                "wrong-target",
                "wrong target",
                "unsafe",
                "crash",
            )
        ):
            blockers.append(issue.get("text") or text)

    crashes = int((campaign.get("aggregated_metrics") or {}).get("crashes") or 0)
    if crashes > 0:
        blockers.append(f"Runtime crashes: {crashes}")

    if blockers or counts["FAIL"] > 0:
        return "NOT READY", blockers or notes

    pending_required = [
        tid for tid in required
        if (tests.get(tid) or {}).get("status", "PENDING") == "PENDING"
    ]
    wake_model = (campaign.get("aggregated_metrics") or {}).get("wake_model") or {}
    missing_elian = not bool(wake_model.get("custom_elian_onnx_installed"))
    # Also check campaign-level flag if set
    if campaign.get("custom_elian_onnx_installed") is False:
        missing_elian = True
    if wake_model.get("acoustic_wake_status") == (
        "MANUAL TEST REQUIRED / CUSTOM MODEL MISSING"
    ):
        missing_elian = True

    partials = counts["PARTIAL"]
    # Non-optional SKIP counts as incomplete for READY
    non_optional_skips = [
        tid for tid in required
        if (tests.get(tid) or {}).get("status") == "SKIP"
    ]

    issues_notes: List[str] = []
    if partials:
        issues_notes.append(f"{partials} PARTIAL test(s)")
    if pending_required:
        issues_notes.append(f"{len(pending_required)} required test(s) unresolved")
    if non_optional_skips:
        issues_notes.append(
            f"Required tests marked SKIP: {', '.join(non_optional_skips)}"
        )
    if missing_elian:
        issues_notes.append(
            "Acoustic wake: MANUAL TEST REQUIRED / CUSTOM MODEL MISSING"
        )

    if (
        counts["FAIL"] == 0
        and counts["PARTIAL"] == 0
        and not pending_required
        and not non_optional_skips
        and not missing_elian
        and not campaign.get("unexpected_exit_pending_review")
    ):
        return "READY", []

    # FAIL=0 but incomplete / partial / optional gaps / missing model
    if counts["FAIL"] == 0:
        if not issues_notes:
            issues_notes.append("Campaign incomplete or optional gaps")
        return "READY WITH ISSUES", issues_notes

    return "NOT READY", blockers


def print_campaign_status(campaign: Dict[str, Any]) -> None:
    done, total = count_completed(campaign)
    counts = campaign_counts(campaign)
    print("[RC] --- campaign status ---")
    print(f"  id: {campaign.get('campaign_id')}")
    print(f"  status: {campaign.get('status')}")
    print(f"  completed: {done}/{total}")
    print(
        f"  PASS={counts['PASS']} PARTIAL={counts['PARTIAL']} "
        f"FAIL={counts['FAIL']} SKIP={counts['SKIP']} PENDING={counts['PENDING']}"
    )
    print(f"  sessions: {len(campaign.get('sessions') or [])}")
    print(f"  unexpected_exit_pending_review: "
          f"{campaign.get('unexpected_exit_pending_review')}")
    unresolved = unresolved_tests(campaign)
    if unresolved:
        print(f"  unresolved: {', '.join(unresolved[:8])}"
              + ("..." if len(unresolved) > 8 else ""))
    verdict, notes = classify_campaign_release(campaign)
    print(f"  current verdict: {verdict}")
    for note in notes[:5]:
        print(f"    - {note}")
