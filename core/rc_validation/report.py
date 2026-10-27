# ==========================
# ELIAN — RC validation report (Phase 9.5 + 9.6)
# ==========================

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from Config import DATA_DIR
from core.rc_validation.campaign import (
    all_test_ids,
    campaign_counts,
    classify_campaign_release,
    unresolved_tests,
)
from core.rc_validation.collector import RCValidationCollector


def _optional_gpu_snapshot() -> Dict[str, Any]:
    try:
        import subprocess

        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split("\n")[0].split(",")
            if len(parts) >= 3:
                return {
                    "gpu_util_pct": float(parts[0].strip()),
                    "vram_used_mb": float(parts[1].strip()),
                    "vram_total_mb": float(parts[2].strip()),
                }
    except Exception:
        pass
    return {}


def _legacy_recommendations(metrics: Dict[str, Any]) -> List[Dict[str, str]]:
    legacy = metrics.get("legacy_breakdown") or {}
    rows = []
    for component, calls in (
        ("Skills", legacy.get("skills", 0)),
        ("Commands", legacy.get("commands", 0)),
        ("IntentRouter", legacy.get("intent_router", 0)),
        ("GoalRouter", legacy.get("goal_router", 0)),
        ("Memory (legacy)", legacy.get("memory", 0)),
        ("Wake text", legacy.get("wake_text", 0)),
        ("Screenshot", legacy.get("screenshot", 0)),
    ):
        if calls == 0:
            action = "REMOVE (no calls this session)"
        elif calls <= 2:
            action = "MIGRATE FIRST"
        else:
            action = "KEEP (still used)"
        rows.append({
            "component": component,
            "calls": str(calls),
            "recommendation": action,
        })
    return rows


def classify_release(
    metrics: Dict[str, Any],
    manual_results: List[Dict[str, Any]],
    *,
    campaign: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[str]]:
    """
    Deterministic release rules.

    Prefer campaign-wide classification when campaign is provided.
    Session-only fallback still must never return READY when FAIL>0.
    """
    if campaign is not None:
        return classify_campaign_release(campaign)

    fails = sum(1 for r in manual_results if r.get("result") == "FAIL")
    partials = sum(1 for r in manual_results if r.get("result") == "PARTIAL")
    blockers: List[str] = []

    if fails > 0:
        blockers.append(f"{fails} manual test(s) marked FAIL")

    if metrics.get("crashes", 0) > 0:
        blockers.append(f"Crashes during session: {metrics['crashes']}")

    if metrics.get("unexpected_exit"):
        blockers.append("Unexpected process exit")

    wake = metrics.get("wake_model") or {}
    missing_elian = (
        not wake.get("custom_elian_onnx_installed")
        or wake.get("acoustic_wake_status")
        == "MANUAL TEST REQUIRED / CUSTOM MODEL MISSING"
    )

    if blockers:
        return "NOT READY", blockers

    notes: List[str] = []
    if partials > 0:
        notes.append(f"{partials} manual tests PARTIAL")
    if missing_elian:
        notes.append("Acoustic wake: MANUAL TEST REQUIRED / CUSTOM MODEL MISSING")

    if fails == 0 and partials == 0 and not missing_elian and manual_results:
        # Only READY if we have at least some PASS and no FAIL/PARTIAL
        passes = sum(1 for r in manual_results if r.get("result") == "PASS")
        if passes > 0:
            return "READY", []

    if fails == 0:
        if not notes:
            notes.append("Incomplete validation session")
        return "READY WITH ISSUES", notes

    return "NOT READY", blockers


def build_report(
    collector: RCValidationCollector,
    *,
    campaign: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    metrics = collector.snapshot_metrics()
    metrics["gpu_optional"] = _optional_gpu_snapshot()

    if campaign is not None:
        return build_campaign_report(campaign, session_metrics=metrics)

    manual = metrics.get("manual_results") or []
    release, release_notes = classify_release(metrics, manual)

    pass_n = sum(1 for r in manual if r.get("result") == "PASS")
    fail_n = sum(1 for r in manual if r.get("result") == "FAIL")
    partial_n = sum(1 for r in manual if r.get("result") == "PARTIAL")
    skip_n = sum(1 for r in manual if r.get("result") == "SKIP")

    tool_calls = int(metrics.get("tool_calls") or 0)
    tool_fail = int(metrics.get("tool_failure") or 0)
    tool_rate = (
        round(100.0 * (tool_calls - tool_fail) / max(1, tool_calls), 1)
        if tool_calls
        else None
    )

    agent_ok = int(metrics.get("agent_success") or 0)
    agent_fail = int(metrics.get("agent_failure") or 0)
    agent_total = agent_ok + agent_fail
    agent_rate = (
        round(100.0 * agent_ok / agent_total, 1) if agent_total else None
    )

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "title": "ELIAN 2.0 RC VALIDATION",
        "scope": "session",
        "metrics": metrics,
        "summary": {
            "tests_completed": len(manual),
            "pass": pass_n,
            "fail": fail_n,
            "partial": partial_n,
            "skip": skip_n,
            "routing_distribution_pct": metrics.get("routing_distribution_pct"),
            "legacy_fallback_rate_pct": metrics.get("legacy_fallback_rate_pct"),
            "legacy_breakdown": metrics.get("legacy_breakdown"),
            "tool_success_rate_pct": tool_rate,
            "agent_success_rate_pct": agent_rate,
            "release_recommendation": release,
            "release_notes": release_notes,
        },
        "legacy_recommendations": _legacy_recommendations(metrics),
        "issue_log": metrics.get("issue_log") or [],
    }


def build_campaign_report(
    campaign: Dict[str, Any],
    *,
    session_metrics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    counts = campaign_counts(campaign)
    unresolved = unresolved_tests(campaign)
    release, release_notes = classify_campaign_release(campaign)
    agg = dict(campaign.get("aggregated_metrics") or {})
    if session_metrics:
        # Prefer wake model from latest session for display
        if session_metrics.get("wake_model"):
            agg.setdefault("wake_model", session_metrics["wake_model"])

    wake_model = agg.get("wake_model") or {}
    tests = campaign.get("tests") or {}
    test_rows = []
    for tid in all_test_ids():
        entry = tests.get(tid) or {}
        test_rows.append({
            "test_id": tid,
            "result": entry.get("status", "PENDING"),
            "note": entry.get("note") or "",
            "attempts": entry.get("attempts") or 0,
            "session_id": entry.get("session_id") or "",
            "updated_at": entry.get("updated_at") or "",
        })

    high_issues = [
        i for i in (campaign.get("issues") or [])
        if str(i.get("severity") or "").upper() == "HIGH"
    ]

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "title": "ELIAN 2.0 RC VALIDATION",
        "scope": "campaign",
        "campaign": {
            "campaign_id": campaign.get("campaign_id"),
            "status": campaign.get("status"),
            "started_at": campaign.get("started_at"),
            "updated_at": campaign.get("updated_at"),
            "elian_version": campaign.get("elian_version"),
            "sessions": list(campaign.get("sessions") or []),
            "unexpected_exit_pending_review": bool(
                campaign.get("unexpected_exit_pending_review")
            ),
        },
        "metrics": {
            **agg,
            "wake": campaign.get("wake") or {},
            "wake_model": wake_model,
            "version": campaign.get("elian_version"),
            "routing_distribution_pct": agg.get("routing_distribution_pct") or {},
            "legacy_fallback_rate_pct": agg.get("legacy_fallback_rate_pct") or 0.0,
            "legacy_breakdown": agg.get("legacy_breakdown") or {},
        },
        "summary": {
            "tests_total": len(all_test_ids()),
            "tests_completed": counts["PASS"] + counts["FAIL"] + counts["PARTIAL"] + counts["SKIP"],
            "pass": counts["PASS"],
            "fail": counts["FAIL"],
            "partial": counts["PARTIAL"],
            "skip": counts["SKIP"],
            "pending": counts["PENDING"],
            "unresolved": unresolved,
            "routing_distribution_pct": agg.get("routing_distribution_pct"),
            "legacy_fallback_rate_pct": agg.get("legacy_fallback_rate_pct"),
            "legacy_breakdown": agg.get("legacy_breakdown"),
            "release_recommendation": release,
            "release_notes": release_notes,
            "blockers": list(release_notes) if release == "NOT READY" else [],
        },
        "tests": test_rows,
        "sessions": list(campaign.get("sessions") or []),
        "issue_log": list(campaign.get("issues") or []),
        "high_severity_issues": high_issues,
        "legacy_recommendations": _legacy_recommendations(agg),
        "session_metrics_latest": session_metrics,
    }


def write_reports(
    collector: RCValidationCollector,
    *,
    campaign: Optional[Dict[str, Any]] = None,
) -> Tuple[Path, Path]:
    """
    Write stamped session file (always) + campaign aggregate latest/MD.
    Old stamped session reports are never overwritten.
    """
    out_dir = Path(DATA_DIR) / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y%m%dT%H%M%S")
    import secrets

    stamped = out_dir / f"rc_validation_{ts}_{secrets.token_hex(2)}.json"

    # Session stamp (preserves history of each process)
    session_report = build_report(collector, campaign=None)
    session_report["campaign_id"] = (
        (campaign or {}).get("campaign_id") if campaign else None
    )
    stamped.write_text(
        json.dumps(session_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Campaign aggregate → latest + MD
    if campaign is not None:
        report = build_campaign_report(
            campaign,
            session_metrics=collector.snapshot_metrics(),
        )
    else:
        report = session_report

    latest = out_dir / "rc_validation_latest.json"
    md_path = out_dir / "RC_VALIDATION_REPORT.md"
    latest.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")

    return stamped, md_path


def render_markdown(report: Dict[str, Any]) -> str:
    m = report.get("metrics") or {}
    s = report.get("summary") or {}
    camp = report.get("campaign") or {}
    lines = [
        "# ELIAN 2.0 RC VALIDATION",
        "",
        f"Generated: {report.get('generated_at', '')}",
        f"Scope: {report.get('scope', 'session')}",
        "",
    ]

    if camp:
        lines.extend([
            "## Campaign",
            "",
            f"- ID: `{camp.get('campaign_id')}`",
            f"- Status: {camp.get('status')}",
            f"- Started: {camp.get('started_at')}",
            f"- Version: {camp.get('elian_version')}",
            f"- Unexpected exit pending review: "
            f"{camp.get('unexpected_exit_pending_review')}",
            "",
        ])

    total = s.get("tests_total") or (
        int(s.get("pass") or 0)
        + int(s.get("fail") or 0)
        + int(s.get("partial") or 0)
        + int(s.get("skip") or 0)
        + int(s.get("pending") or 0)
    )
    completed = s.get("tests_completed")
    lines.extend([
        "## Tests completed",
        "",
    ])
    if completed is not None and total:
        lines.append(f"- Progress: {completed}/{total}")
    lines.extend([
        f"- PASS: {s.get('pass', 0)}",
        f"- PARTIAL: {s.get('partial', 0)}",
        f"- FAIL: {s.get('fail', 0)}",
        f"- SKIP: {s.get('skip', 0)}",
        f"- PENDING: {s.get('pending', 0)}",
        "",
    ])

    unresolved = s.get("unresolved") or []
    if unresolved:
        lines.extend([
            "",
            "### Unresolved tests",
            "",
        ])
        for tid in unresolved:
            lines.append(f"- `{tid}`")

    lines.extend(["", "## Sessions / restarts", ""])
    sessions = report.get("sessions") or camp.get("sessions") or []
    if not sessions:
        lines.append("- (single session / no campaign sessions recorded)")
    for sess in sessions:
        flag = ""
        if sess.get("unexpected_exit"):
            flag = " **unexpected_exit**"
        elif sess.get("clean_shutdown"):
            flag = " clean"
        lines.append(
            f"- `{sess.get('session_id')}` "
            f"{sess.get('started_at')} → {sess.get('ended_at') or '…'}{flag}"
        )

    lines.extend(["", "## Routing", ""])
    dist = s.get("routing_distribution_pct") or m.get("routing_distribution_pct") or {}
    for key in ("fast_path", "agent", "brain", "legacy"):
        label = {
            "fast_path": "FastPath",
            "agent": "Agent",
            "brain": "Brain",
            "legacy": "Legacy",
        }[key]
        lines.append(f"- {label}: {dist.get(key, 0)}%")

    lines.extend([
        "",
        f"**Legacy fallback rate:** {s.get('legacy_fallback_rate_pct', m.get('legacy_fallback_rate_pct', 0))}%",
        "",
        "### Legacy breakdown",
        "",
    ])
    legacy = s.get("legacy_breakdown") or m.get("legacy_breakdown") or {}
    for k, v in legacy.items():
        lines.append(f"- {k}: {v}")

    wake = m.get("wake") or {}
    wake_model = m.get("wake_model") or {}
    lines.extend([
        "",
        "## Voice",
        "",
        f"- Wake detections: {wake.get('detections', m.get('wake_count', 0))}",
        f"- False positives (manual): "
        f"{wake.get('false_positives', m.get('wake_false_positive_manual', 0))}",
        f"- Missed (manual): {wake.get('missed', m.get('wake_missed_manual', 0))}",
        f"- Acoustic wake: **{wake_model.get('acoustic_wake_status', 'UNKNOWN')}**",
        f"- Wake mode used: {wake_model.get('wake_mode_used', 'unknown')}",
    ])
    if wake_model.get("warning"):
        lines.append(f"- **{wake_model['warning']}**")

    lines.extend(["", "## Test matrix (24)", ""])
    for row in report.get("tests") or []:
        note = f" — {row['note']}" if row.get("note") else ""
        lines.append(
            f"- `{row.get('test_id')}`: **{row.get('result')}**{note}"
        )
    if not report.get("tests"):
        for item in (m.get("manual_results") or []):
            note = f" — {item['note']}" if item.get("note") else ""
            lines.append(f"- `{item.get('test_id')}`: **{item.get('result')}**{note}")

    lines.extend([
        "",
        "## Legacy recommendations",
        "",
        "| Component | Calls | Recommendation |",
        "|-----------|-------|----------------|",
    ])
    for row in report.get("legacy_recommendations") or []:
        lines.append(
            f"| {row['component']} | {row['calls']} | {row['recommendation']} |"
        )

    if report.get("high_severity_issues") or report.get("issue_log"):
        lines.extend(["", "## Issue log", ""])
        for issue in report.get("high_severity_issues") or []:
            lines.append(
                f"- **HIGH**: {issue.get('text')} ({issue.get('ts', '')})"
            )
        for issue in report.get("issue_log") or []:
            if isinstance(issue, dict):
                if str(issue.get("severity") or "").upper() == "HIGH":
                    continue  # already listed
                lines.append(
                    f"- [{issue.get('severity', 'INFO')}] {issue.get('text')}"
                )
            else:
                lines.append(f"- {issue}")

    blockers = s.get("blockers") or (
        s.get("release_notes") if s.get("release_recommendation") == "NOT READY" else []
    )
    if blockers:
        lines.extend(["", "## Blockers", ""])
        for b in blockers:
            lines.append(f"- {b}")

    lines.extend([
        "",
        "## Release decision",
        "",
        f"**{s.get('release_recommendation', 'UNKNOWN')}**",
        "",
    ])
    for note in s.get("release_notes") or []:
        lines.append(f"- {note}")

    lines.append("")
    return "\n".join(lines)


def print_summary(report: Dict[str, Any]) -> None:
    s = report.get("summary") or {}
    m = report.get("metrics") or {}
    camp = report.get("campaign") or {}
    print()
    print("=" * 50)
    print("ELIAN 2.0 RC VALIDATION — CAMPAIGN SUMMARY")
    print("=" * 50)
    print()
    if camp.get("campaign_id"):
        print(f"Campaign: {camp.get('campaign_id')} ({camp.get('status')})")
    total = s.get("tests_total", 24)
    print(
        f"Tests: PASS={s.get('pass')} PARTIAL={s.get('partial')} "
        f"FAIL={s.get('fail')} SKIP={s.get('skip')} PENDING={s.get('pending', 0)} "
        f"({s.get('tests_completed', 0)}/{total})"
    )
    print()
    dist = s.get("routing_distribution_pct") or m.get("routing_distribution_pct") or {}
    print("Routing:")
    print(f"  FastPath: {dist.get('fast_path', 0)}%")
    print(f"  Agent:    {dist.get('agent', 0)}%")
    print(f"  Brain:    {dist.get('brain', 0)}%")
    print(f"  Legacy:   {dist.get('legacy', 0)}%")
    print()
    print(f"Legacy fallback rate: {s.get('legacy_fallback_rate_pct', 0)}%")
    wake = m.get("wake_model") or {}
    print()
    print(f"Acoustic wake: {wake.get('acoustic_wake_status', 'UNKNOWN')}")
    print(f"Wake mode used: {wake.get('wake_mode_used', 'unknown')}")
    print()
    print(f"Release recommendation: {s.get('release_recommendation')}")
    for note in (s.get("release_notes") or [])[:5]:
        print(f"  - {note}")
    print("=" * 50)
