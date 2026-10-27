# ==========================
# ELIAN — merge historical RC session reports into one campaign (Phase 9.6+)
# ==========================
# Reads Data/diagnostics/rc_validation_*.json (stamped sessions only).
# Does NOT modify or delete originals.

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from Config import DATA_DIR
from core.rc_validation.campaign import (
    all_test_ids,
    optional_test_ids,
    required_test_ids,
)
from core.rc_validation.tests import RC_TEST_CASES
from core.version import VERSION


STAMPED_PATTERN = re.compile(r"^rc_validation_(\d{8}T\d{6})(?:_[0-9a-f]+)?\.json$")
VALID_MANUAL = frozenset({"PASS", "FAIL", "PARTIAL", "SKIP"})


def diagnostics_dir(base: Optional[Path] = None) -> Path:
    return Path(base) if base else Path(DATA_DIR) / "diagnostics"


def discover_session_reports(diag: Path) -> List[Path]:
    """Stamped session reports only — never latest/campaign aggregates."""
    files = []
    for path in diag.glob("rc_validation_*.json"):
        name = path.name
        if name in (
            "rc_validation_latest.json",
            "rc_validation_campaign.json",
            "rc_validation_campaign_latest.json",
        ):
            continue
        if STAMPED_PATTERN.match(name):
            files.append(path)
    files.sort(key=lambda p: p.name)
    return files


def _safe_load(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[RC-MERGE] Skip unreadable {path.name}: {exc}")
        return None
    if not isinstance(data, dict):
        return None
    return data


def _timestamp_from_path(path: Path) -> str:
    match = STAMPED_PATTERN.match(path.name)
    if match:
        raw = match.group(1)  # 20260825T093540
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}T{raw[9:11]}:{raw[11:13]}:{raw[13:15]}"
    return ""


def _extract_manual_results(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Compatible with session schema (metrics.manual_results) and campaign schema (tests)."""
    metrics = report.get("metrics") or {}
    results = metrics.get("manual_results")
    if isinstance(results, list) and results:
        out = []
        for item in results:
            if not isinstance(item, dict):
                continue
            status = str(item.get("result") or item.get("status") or "").upper()
            if status not in VALID_MANUAL:
                continue
            out.append({
                "test_id": str(item.get("test_id") or item.get("id") or ""),
                "result": status,
                "note": str(item.get("note") or ""),
                "ts": str(item.get("ts") or item.get("updated_at") or ""),
            })
        return [r for r in out if r["test_id"]]

    # Campaign-style tests list
    rows = report.get("tests")
    if isinstance(rows, list):
        out = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            status = str(item.get("result") or item.get("status") or "").upper()
            if status not in VALID_MANUAL:
                continue
            out.append({
                "test_id": str(item.get("test_id") or ""),
                "result": status,
                "note": str(item.get("note") or ""),
                "ts": str(item.get("updated_at") or item.get("ts") or ""),
            })
        return [r for r in out if r["test_id"]]
    return []


def _extract_issues(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = report.get("issue_log")
    if raw is None:
        raw = (report.get("metrics") or {}).get("issue_log")
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, str):
            text = item.strip()
            if not text:
                continue
            sev = "HIGH" if text.upper().startswith("HIGH") or "HIGH BUG" in text.upper() else "INFO"
            out.append({"severity": sev, "text": text, "ts": report.get("generated_at") or ""})
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("note") or "").strip()
            if not text:
                continue
            sev = str(item.get("severity") or "INFO").upper()
            if "HIGH" in text.upper() and sev == "INFO":
                sev = "HIGH"
            out.append({
                "severity": sev,
                "text": text,
                "ts": str(item.get("ts") or report.get("generated_at") or ""),
            })
    return out


def _percentiles(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"p50": None, "p95": None, "count": 0, "reliable_p95": False}
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    p50 = sorted_vals[n // 2]
    p95 = sorted_vals[min(n - 1, int(n * 0.95))]
    return {
        "p50": round(p50, 1),
        "p95": round(p95, 1),
        "count": n,
        "reliable_p95": n >= 5,
    }


def _collect_latency_samples(report: Dict[str, Any]) -> Dict[str, List[float]]:
    """Pull raw ms samples from interaction_traces; fall back to session percentile points."""
    buckets: Dict[str, List[float]] = {
        "stt": [],
        "agent": [],
        "tool": [],
        "tts": [],
        "fast_path": [],
        "end_to_end": [],
        "wake": [],
    }
    metrics = report.get("metrics") or {}
    for trace in metrics.get("interaction_traces") or []:
        if not isinstance(trace, dict):
            continue
        lat = trace.get("latency_ms") or {}
        if not isinstance(lat, dict):
            continue
        if lat.get("stt_ms") is not None:
            buckets["stt"].append(float(lat["stt_ms"]))
        if lat.get("planner_ms") is not None:
            buckets["agent"].append(float(lat["planner_ms"]))
        if lat.get("tool_ms") is not None:
            buckets["tool"].append(float(lat["tool_ms"]))
        if lat.get("tts_generation_ms") is not None:
            buckets["tts"].append(float(lat["tts_generation_ms"]))
        if lat.get("total_response_ms") is not None:
            buckets["end_to_end"].append(float(lat["total_response_ms"]))
            if (trace.get("route_bucket") or trace.get("route")) == "fast_path":
                buckets["fast_path"].append(float(lat["total_response_ms"]))

    # Wake latency: only aggregated in older reports — keep p50 as one observation per wake count
    wake_lat = metrics.get("wake_latency") or {}
    if wake_lat.get("p50") is not None and int(wake_lat.get("count") or 0) > 0:
        # Approximate: repeat p50 count times (no raw samples stored)
        for _ in range(int(wake_lat["count"])):
            buckets["wake"].append(float(wake_lat["p50"]))

    return buckets


def classify_merged_release(campaign: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Same rules as Phase 9.6 campaign, with UNRESOLVED instead of PENDING."""
    notes: List[str] = []
    blockers: List[str] = []
    tests = campaign.get("tests") or {}
    required = required_test_ids()
    optional = optional_test_ids()

    fails = [
        tid for tid in all_test_ids()
        if (tests.get(tid) or {}).get("status") == "FAIL"
    ]
    if fails:
        blockers.append(f"FAIL tests: {', '.join(fails)}")

    if campaign.get("unexpected_exit_pending_review"):
        blockers.append("Unexpected process exit — pending manual review")

    for issue in campaign.get("issues") or []:
        text = str(issue.get("text") or "")
        sev = str(issue.get("severity") or "").upper()
        low = text.lower()
        if sev == "HIGH":
            blockers.append(text)
            continue
        # Non-HIGH: only explicit crash / unexpected-exit language (not "bez OOM")
        if any(
            tok in low
            for tok in (
                "samoczynnie",
                "unexpected exit",
                "zakończył proces",
                "zakonczyl proces",
                "wrong-target",
                "wrong target",
            )
        ):
            blockers.append(text)
        elif re.search(r"(cuda\s*oom|out of memory|\boom\b)", low) and "bez oom" not in low:
            blockers.append(text)

    crashes = int((campaign.get("aggregated_metrics") or {}).get("crashes") or 0)
    if crashes > 0:
        blockers.append(f"Runtime crashes: {crashes}")

    if blockers or fails:
        # Deduplicate blockers while preserving order
        seen = set()
        uniq = []
        for b in blockers:
            if b not in seen:
                seen.add(b)
                uniq.append(b)
        return "NOT READY", uniq

    counts = campaign.get("counts") or {}
    unresolved_required = [
        tid for tid in required
        if (tests.get(tid) or {}).get("status") in ("UNRESOLVED", "PENDING", "")
    ]
    non_optional_skips = [
        tid for tid in required
        if (tests.get(tid) or {}).get("status") == "SKIP"
    ]
    partials = int(counts.get("PARTIAL") or 0)

    wake_model = (campaign.get("aggregated_metrics") or {}).get("wake_model") or {}
    missing_elian = (
        not wake_model.get("custom_elian_onnx_installed")
        or wake_model.get("acoustic_wake_status")
        == "MANUAL TEST REQUIRED / CUSTOM MODEL MISSING"
        or campaign.get("custom_elian_onnx_installed") is False
    )

    issues_notes: List[str] = []
    if partials:
        issues_notes.append(f"{partials} PARTIAL test(s)")
    if unresolved_required:
        issues_notes.append(
            f"{len(unresolved_required)} required test(s) unresolved"
        )
    if non_optional_skips:
        issues_notes.append(
            f"Required tests marked SKIP: {', '.join(non_optional_skips)}"
        )
    if missing_elian:
        issues_notes.append(
            "Acoustic wake: MANUAL TEST REQUIRED / CUSTOM MODEL MISSING"
        )

    optional_unresolved = [
        tid for tid in optional
        if (tests.get(tid) or {}).get("status") in ("UNRESOLVED", "PENDING", "", "SKIP")
    ]
    if optional_unresolved and not issues_notes:
        issues_notes.append("Optional tests unresolved/skipped")

    if (
        not fails
        and partials == 0
        and not unresolved_required
        and not non_optional_skips
        and not missing_elian
        and not campaign.get("unexpected_exit_pending_review")
    ):
        return "READY", []

    if not fails:
        if not issues_notes:
            issues_notes.append("Campaign incomplete")
        return "READY WITH ISSUES", issues_notes

    return "NOT READY", blockers


def merge_session_reports(
    *,
    diag_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    diag = diagnostics_dir(diag_dir)
    paths = discover_session_reports(diag)
    if not paths:
        raise FileNotFoundError(f"No stamped rc_validation_*.json in {diag}")

    sessions_meta: List[Dict[str, Any]] = []
    attempts_by_test: Dict[str, List[Dict[str, Any]]] = {
        tid: [] for tid in all_test_ids()
    }
    issues: List[Dict[str, Any]] = []
    sum_counters = {
        "interactions": 0,
        "routable_interactions": 0,
        "fast_path": 0,
        "agent": 0,
        "brain": 0,
        "legacy": 0,
        "other": 0,
        "wake_detections": 0,
        "wake_fp": 0,
        "wake_miss": 0,
        "legacy_fallback_used": 0,
        "crashes": 0,
        "tool_calls": 0,
        "tool_success": 0,
        "tool_failure": 0,
        "agent_success": 0,
        "agent_failure": 0,
        "stt_failures": 0,
        "tts_failures": 0,
        "vision_calls": 0,
        "vision_failures": 0,
        "ha_calls": 0,
        "ha_failures": 0,
    }
    legacy_breakdown: Dict[str, int] = {}
    latency_samples: Dict[str, List[float]] = {
        "stt": [], "agent": [], "tool": [], "tts": [],
        "fast_path": [], "end_to_end": [], "wake": [],
    }
    wake_model: Dict[str, Any] = {}
    unexpected_exit = False
    source_files: List[str] = []

    for path in paths:
        report = _safe_load(path)
        if report is None:
            continue
        # Skip campaign-scoped files if somehow matched
        if report.get("scope") == "campaign":
            continue

        source_files.append(path.name)
        generated = str(report.get("generated_at") or _timestamp_from_path(path))
        session_id = path.stem
        metrics = report.get("metrics") or {}

        sessions_meta.append({
            "session_id": session_id,
            "file": path.name,
            "generated_at": generated,
            "summary": {
                "pass": (report.get("summary") or {}).get("pass"),
                "fail": (report.get("summary") or {}).get("fail"),
                "partial": (report.get("summary") or {}).get("partial"),
                "skip": (report.get("summary") or {}).get("skip"),
            },
        })

        for mr in _extract_manual_results(report):
            tid = mr["test_id"]
            if tid not in attempts_by_test:
                attempts_by_test[tid] = []
            attempt_n = len(attempts_by_test[tid]) + 1
            attempts_by_test[tid].append({
                "session_id": session_id,
                "report_file": path.name,
                "report_timestamp": generated,
                "status": mr["result"],
                "note": mr["note"],
                "attempt": attempt_n,
                "marked_at": mr["ts"] or generated,
            })

        for issue in _extract_issues(report):
            issue = dict(issue)
            issue["session_id"] = session_id
            issue["report_file"] = path.name
            issues.append(issue)
            text_l = issue["text"].lower()
            if (
                issue["severity"] == "HIGH"
                or "samoczynnie" in text_l
                or "zakończył proces" in text_l
                or "zakonczyl proces" in text_l
                or "unexpected" in text_l
            ):
                unexpected_exit = True

        # Counters — sum raw, never sum percentages
        sum_counters["interactions"] += int(metrics.get("interactions") or 0)
        sum_counters["routable_interactions"] += int(
            metrics.get("routable_interactions") or 0
        )
        routes = metrics.get("routing_counts") or {}
        for key in ("fast_path", "agent", "brain", "legacy", "other"):
            sum_counters[key] += int(routes.get(key) or 0)
        sum_counters["wake_detections"] += int(metrics.get("wake_count") or 0)
        sum_counters["wake_fp"] += int(metrics.get("wake_false_positive_manual") or 0)
        sum_counters["wake_miss"] += int(metrics.get("wake_missed_manual") or 0)
        sum_counters["legacy_fallback_used"] += int(
            metrics.get("legacy_fallback_used") or 0
        )
        sum_counters["crashes"] += int(metrics.get("crashes") or 0)
        sum_counters["tool_calls"] += int(metrics.get("tool_calls") or 0)
        sum_counters["tool_success"] += int(metrics.get("tool_success") or 0)
        sum_counters["tool_failure"] += int(metrics.get("tool_failure") or 0)
        sum_counters["agent_success"] += int(metrics.get("agent_success") or 0)
        sum_counters["agent_failure"] += int(metrics.get("agent_failure") or 0)
        sum_counters["stt_failures"] += int(metrics.get("stt_failures") or 0)
        sum_counters["tts_failures"] += int(metrics.get("tts_failures") or 0)
        sum_counters["vision_calls"] += int(metrics.get("vision_calls") or 0)
        sum_counters["vision_failures"] += int(metrics.get("vision_failures") or 0)
        sum_counters["ha_calls"] += int(metrics.get("ha_calls") or 0)
        sum_counters["ha_failures"] += int(metrics.get("ha_failures") or 0)

        for k, v in (metrics.get("legacy_breakdown") or {}).items():
            legacy_breakdown[k] = int(legacy_breakdown.get(k) or 0) + int(v or 0)

        for key, samples in _collect_latency_samples(report).items():
            latency_samples[key].extend(samples)

        wm = metrics.get("wake_model") or {}
        if wm:
            # Prefer most recent non-empty wake_mode
            wake_model = {**wake_model, **wm}

    # Final status per test = last explicit manual mark (chronological sessions)
    tests_out: Dict[str, Any] = {}
    for tid in all_test_ids():
        attempts = attempts_by_test.get(tid) or []
        if attempts:
            last = attempts[-1]
            tests_out[tid] = {
                "status": last["status"],
                "note": last.get("note") or "",
                "attempts": len(attempts),
                "last_session": last.get("session_id") or "",
                "last_report": last.get("report_file") or "",
                "last_marked_at": last.get("marked_at") or "",
                "history": attempts,
            }
        else:
            tests_out[tid] = {
                "status": "UNRESOLVED",
                "note": "",
                "attempts": 0,
                "last_session": "",
                "last_report": "",
                "last_marked_at": "",
                "history": [],
            }

    # Also keep any unknown test IDs that appeared in reports
    for tid, attempts in attempts_by_test.items():
        if tid in tests_out or not attempts:
            continue
        last = attempts[-1]
        tests_out[tid] = {
            "status": last["status"],
            "note": last.get("note") or "",
            "attempts": len(attempts),
            "last_session": last.get("session_id") or "",
            "last_report": last.get("report_file") or "",
            "last_marked_at": last.get("marked_at") or "",
            "history": attempts,
        }

    counts = {"PASS": 0, "FAIL": 0, "PARTIAL": 0, "SKIP": 0, "UNRESOLVED": 0}
    for tid in all_test_ids():
        st = tests_out[tid]["status"]
        counts[st] = counts.get(st, 0) + 1

    routable = max(1, sum_counters["routable_interactions"])
    routing_pct = {
        k: round(100.0 * sum_counters[k] / routable, 1)
        for k in ("fast_path", "agent", "brain", "legacy")
    }
    legacy_rate = (
        round(100.0 * sum_counters["legacy"] / routable, 2)
        if sum_counters["routable_interactions"]
        else 0.0
    )

    if not wake_model.get("wake_mode_used"):
        wake_model["wake_mode_used"] = "legacy_stt"
    if not wake_model.get("custom_elian_onnx_installed"):
        wake_model["custom_elian_onnx_installed"] = False
        wake_model["acoustic_wake_status"] = (
            "MANUAL TEST REQUIRED / CUSTOM MODEL MISSING"
        )
        wake_model["warning"] = "CUSTOM ELIAN WAKE MODEL NOT INSTALLED"

    latency = {k: _percentiles(v) for k, v in latency_samples.items()}

    campaign: Dict[str, Any] = {
        "campaign_id": f"merged_{time.strftime('%Y%m%dT%H%M%S')}",
        "status": "merged_from_sessions",
        "started_at": sessions_meta[0]["generated_at"] if sessions_meta else "",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elian_version": VERSION,
        "source_reports": source_files,
        "sessions": sessions_meta,
        "tests": tests_out,
        "counts": counts,
        "issues": issues,
        "unexpected_exit_pending_review": unexpected_exit,
        "custom_elian_onnx_installed": bool(
            wake_model.get("custom_elian_onnx_installed")
        ),
        "wake": {
            "detections": sum_counters["wake_detections"],
            "false_positives": sum_counters["wake_fp"],
            "missed": sum_counters["wake_miss"],
            "mode": wake_model.get("wake_mode_used"),
            "acoustic_wake_status": wake_model.get("acoustic_wake_status"),
            "warning": wake_model.get("warning"),
        },
        "aggregated_metrics": {
            "interactions": sum_counters["interactions"],
            "routable_interactions": sum_counters["routable_interactions"],
            "routing_counts": {
                k: sum_counters[k]
                for k in ("fast_path", "agent", "brain", "legacy", "other")
            },
            "routing_distribution_pct": routing_pct,
            "legacy_fallback_rate_pct": legacy_rate,
            "legacy_breakdown": legacy_breakdown,
            "legacy_fallback_used": sum_counters["legacy_fallback_used"],
            "crashes": sum_counters["crashes"],
            "tool_calls": sum_counters["tool_calls"],
            "tool_success": sum_counters["tool_success"],
            "tool_failure": sum_counters["tool_failure"],
            "agent_success": sum_counters["agent_success"],
            "agent_failure": sum_counters["agent_failure"],
            "stt_failures": sum_counters["stt_failures"],
            "tts_failures": sum_counters["tts_failures"],
            "vision_calls": sum_counters["vision_calls"],
            "vision_failures": sum_counters["vision_failures"],
            "ha_calls": sum_counters["ha_calls"],
            "ha_failures": sum_counters["ha_failures"],
            "latency": latency,
            "wake_model": wake_model,
        },
    }

    verdict, release_notes = classify_merged_release(campaign)
    campaign["release_recommendation"] = verdict
    campaign["release_notes"] = release_notes
    campaign["blockers"] = list(release_notes) if verdict == "NOT READY" else []
    return campaign


def render_campaign_markdown(campaign: Dict[str, Any]) -> str:
    counts = campaign.get("counts") or {}
    wake = campaign.get("wake") or {}
    agg = campaign.get("aggregated_metrics") or {}
    latency = agg.get("latency") or {}
    dist = agg.get("routing_distribution_pct") or {}
    lines = [
        "# ELIAN 2.0 RC VALIDATION — MERGED CAMPAIGN",
        "",
        f"Generated: {campaign.get('updated_at')}",
        f"Campaign ID: `{campaign.get('campaign_id')}`",
        f"Source reports: {len(campaign.get('source_reports') or [])}",
        f"Elian version: {campaign.get('elian_version')}",
        "",
        "## Counts",
        "",
        f"- PASS: {counts.get('PASS', 0)}",
        f"- PARTIAL: {counts.get('PARTIAL', 0)}",
        f"- FAIL: {counts.get('FAIL', 0)}",
        f"- SKIP: {counts.get('SKIP', 0)}",
        f"- UNRESOLVED: {counts.get('UNRESOLVED', 0)}",
        f"- Total catalog: {len(all_test_ids())}",
        "",
        "## Test matrix",
        "",
        "| ID | Status | Attempts | Last session | Notes |",
        "|----|--------|----------|--------------|-------|",
    ]
    for tid in all_test_ids():
        row = (campaign.get("tests") or {}).get(tid) or {}
        note = (row.get("note") or "").replace("|", "/")
        if len(note) > 80:
            note = note[:77] + "..."
        lines.append(
            f"| `{tid}` | **{row.get('status', 'UNRESOLVED')}** | "
            f"{row.get('attempts', 0)} | `{row.get('last_session') or '—'}` | {note} |"
        )

    lines.extend([
        "",
        "## Sessions",
        "",
    ])
    for sess in campaign.get("sessions") or []:
        s = sess.get("summary") or {}
        lines.append(
            f"- `{sess.get('file')}` @ {sess.get('generated_at')} "
            f"(P={s.get('pass')} F={s.get('fail')} Part={s.get('partial')} S={s.get('skip')})"
        )

    lines.extend([
        "",
        "## Wake",
        "",
        f"- Mode: **{wake.get('mode')}**",
        f"- Acoustic: **{wake.get('acoustic_wake_status')}**",
        f"- Detections: {wake.get('detections')}",
        f"- False positives: {wake.get('false_positives')}",
        f"- Missed: {wake.get('missed')}",
    ])
    if wake.get("warning"):
        lines.append(f"- **{wake['warning']}**")

    lines.extend([
        "",
        "## Routing (from summed counters)",
        "",
        f"- FastPath: {dist.get('fast_path', 0)}%",
        f"- Agent: {dist.get('agent', 0)}%",
        f"- Brain: {dist.get('brain', 0)}%",
        f"- Legacy: {dist.get('legacy', 0)}%",
        f"- Legacy fallback rate: {agg.get('legacy_fallback_rate_pct', 0)}%",
        f"- Interactions: {agg.get('interactions')} "
        f"(routable {agg.get('routable_interactions')})",
        "",
        "## Latency p50 / p95 (ms, merged samples)",
        "",
    ])
    for name in ("stt", "agent", "fast_path", "tts", "end_to_end", "wake"):
        block = latency.get(name) or {}
        if not block.get("count"):
            lines.append(f"- {name}: no samples")
        else:
            rel = "" if block.get("reliable_p95") else f" (n={block['count']}, p95 not reliable)"
            lines.append(
                f"- {name}: p50={block.get('p50')}, p95={block.get('p95')}{rel}"
            )

    lines.extend([
        "",
        f"**Crashes (summed):** {agg.get('crashes', 0)}",
        f"**Unexpected exit pending review:** "
        f"{campaign.get('unexpected_exit_pending_review')}",
        "",
        "## Blockers",
        "",
    ])
    blockers = campaign.get("blockers") or []
    if not blockers:
        lines.append("- (none)")
    else:
        for b in blockers:
            lines.append(f"- {b}")

    lines.extend(["", "## Issues (all sessions)", ""])
    for issue in campaign.get("issues") or []:
        lines.append(
            f"- [{issue.get('severity')}] {issue.get('text')} "
            f"({issue.get('report_file')})"
        )
    if not campaign.get("issues"):
        lines.append("- (none)")

    lines.extend([
        "",
        "## Release recommendation",
        "",
        f"**{campaign.get('release_recommendation')}**",
        "",
    ])
    for note in campaign.get("release_notes") or []:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def write_merged_outputs(
    campaign: Dict[str, Any],
    *,
    diag_dir: Optional[Path] = None,
) -> Tuple[Path, Path, Path]:
    diag = diagnostics_dir(diag_dir)
    diag.mkdir(parents=True, exist_ok=True)
    campaign_path = diag / "rc_validation_campaign.json"
    latest_path = diag / "rc_validation_campaign_latest.json"
    md_path = diag / "RC_VALIDATION_CAMPAIGN_REPORT.md"

    payload = json.dumps(campaign, ensure_ascii=False, indent=2)
    campaign_path.write_text(payload, encoding="utf-8")
    latest_path.write_text(payload, encoding="utf-8")
    md_path.write_text(render_campaign_markdown(campaign), encoding="utf-8")
    return campaign_path, latest_path, md_path


def run_merge(*, diag_dir: Optional[Path] = None, quiet: bool = False) -> int:
    campaign = merge_session_reports(diag_dir=diag_dir)
    c_path, latest, md = write_merged_outputs(campaign, diag_dir=diag_dir)
    if not quiet:
        counts = campaign.get("counts") or {}
        print("=== RC CAMPAIGN MERGE ===")
        print(f"Sources: {len(campaign.get('source_reports') or [])} session reports")
        for name in campaign.get("source_reports") or []:
            print(f"  - {name}")
        print()
        print(
            f"PASS={counts.get('PASS')} PARTIAL={counts.get('PARTIAL')} "
            f"FAIL={counts.get('FAIL')} SKIP={counts.get('SKIP')} "
            f"UNRESOLVED={counts.get('UNRESOLVED')}"
        )
        print(f"Release: {campaign.get('release_recommendation')}")
        for note in (campaign.get("release_notes") or [])[:8]:
            print(f"  - {note}")
        print()
        print(f"Wrote: {c_path}")
        print(f"Wrote: {latest}")
        print(f"Wrote: {md}")
        print()
        print("--- Test matrix ---")
        for tid in all_test_ids():
            row = (campaign.get("tests") or {}).get(tid) or {}
            print(
                f"  {tid:<22} {row.get('status'):<12} "
                f"attempts={row.get('attempts')}  "
                f"last={row.get('last_session') or '-'}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(run_merge())
