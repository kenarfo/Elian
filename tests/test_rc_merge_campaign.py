# ==========================
# ELIAN — RC campaign merge tests (offline)
# ==========================

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.rc_validation.merge_campaign import (
    discover_session_reports,
    merge_session_reports,
    run_merge,
    write_merged_outputs,
)
from core.rc_validation.tests import RC_TEST_CASES


def _session(
    generated_at: str,
    *,
    manual=None,
    issues=None,
    routing=None,
    wake_count=0,
    interactions=0,
    routable=0,
    traces=None,
    wake_model=None,
):
    return {
        "generated_at": generated_at,
        "title": "ELIAN 2.0 RC VALIDATION",
        "metrics": {
            "interactions": interactions,
            "routable_interactions": routable,
            "routing_counts": routing or {
                "fast_path": 0, "agent": 0, "brain": 0, "legacy": 0, "other": 0
            },
            "legacy_breakdown": {"skills": 0},
            "legacy_fallback_used": 0,
            "wake_count": wake_count,
            "wake_false_positive_manual": 0,
            "wake_missed_manual": 0,
            "crashes": 0,
            "tool_calls": 0,
            "tool_success": 0,
            "tool_failure": 0,
            "agent_success": 0,
            "agent_failure": 0,
            "manual_results": manual or [],
            "interaction_traces": traces or [],
            "wake_model": wake_model or {
                "custom_elian_onnx_installed": False,
                "wake_mode_used": "legacy_stt",
                "acoustic_wake_status": "MANUAL TEST REQUIRED / CUSTOM MODEL MISSING",
                "warning": "CUSTOM ELIAN WAKE MODEL NOT INSTALLED",
            },
            "latency": {},
        },
        "summary": {
            "pass": sum(1 for m in (manual or []) if m.get("result") == "PASS"),
            "fail": sum(1 for m in (manual or []) if m.get("result") == "FAIL"),
            "partial": sum(1 for m in (manual or []) if m.get("result") == "PARTIAL"),
            "skip": sum(1 for m in (manual or []) if m.get("result") == "SKIP"),
        },
        "issue_log": issues or [],
    }


class MergeCampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.diag = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name: str, payload: dict):
        path = self.diag / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def test_discovers_stamped_only(self):
        self._write("rc_validation_20260825T091941.json", _session("2026-08-25T09:19:41"))
        self._write("rc_validation_latest.json", _session("2026-08-25T10:00:00"))
        self._write("rc_validation_campaign.json", {"scope": "campaign"})
        found = discover_session_reports(self.diag)
        self.assertEqual([p.name for p in found], ["rc_validation_20260825T091941.json"])

    def test_last_manual_status_wins(self):
        self._write(
            "rc_validation_20260825T090000.json",
            _session(
                "2026-08-25T09:00:00",
                manual=[{"test_id": "wake_word", "result": "PARTIAL", "note": "first", "ts": "a"}],
            ),
        )
        self._write(
            "rc_validation_20260825T100000.json",
            _session(
                "2026-08-25T10:00:00",
                manual=[{"test_id": "wake_word", "result": "PASS", "note": "later", "ts": "b"}],
            ),
        )
        camp = merge_session_reports(diag_dir=self.diag)
        self.assertEqual(camp["tests"]["wake_word"]["status"], "PASS")
        self.assertEqual(camp["tests"]["wake_word"]["attempts"], 2)
        self.assertEqual(camp["tests"]["wake_word"]["note"], "later")

    def test_absent_test_stays_fail_not_pass(self):
        self._write(
            "rc_validation_20260825T090000.json",
            _session(
                "2026-08-25T09:00:00",
                manual=[{"test_id": "agent_multi", "result": "FAIL", "note": "fastpath", "ts": "a"}],
            ),
        )
        self._write(
            "rc_validation_20260825T100000.json",
            _session(
                "2026-08-25T10:00:00",
                manual=[{"test_id": "natural_speech", "result": "PASS", "note": "", "ts": "b"}],
            ),
        )
        camp = merge_session_reports(diag_dir=self.diag)
        self.assertEqual(camp["tests"]["agent_multi"]["status"], "FAIL")
        self.assertEqual(camp["tests"]["natural_speech"]["status"], "PASS")
        self.assertEqual(camp["tests"]["wake_word"]["status"], "UNRESOLVED")

    def test_fail_never_ready(self):
        self._write(
            "rc_validation_20260825T090000.json",
            _session(
                "2026-08-25T09:00:00",
                manual=[{"test_id": "natural_speech", "result": "FAIL", "note": "", "ts": "a"}],
                issues=["HIGH BUG: process died"],
            ),
        )
        camp = merge_session_reports(diag_dir=self.diag)
        self.assertEqual(camp["release_recommendation"], "NOT READY")
        self.assertNotEqual(camp["release_recommendation"], "READY")

    def test_sum_counters_then_percent(self):
        self._write(
            "rc_validation_20260825T090000.json",
            _session(
                "2026-08-25T09:00:00",
                routing={"fast_path": 3, "agent": 1, "brain": 0, "legacy": 0, "other": 0},
                routable=4,
                interactions=4,
                wake_count=2,
            ),
        )
        self._write(
            "rc_validation_20260825T100000.json",
            _session(
                "2026-08-25T10:00:00",
                routing={"fast_path": 1, "agent": 3, "brain": 0, "legacy": 0, "other": 0},
                routable=4,
                interactions=4,
                wake_count=1,
            ),
        )
        camp = merge_session_reports(diag_dir=self.diag)
        agg = camp["aggregated_metrics"]
        self.assertEqual(agg["routing_counts"]["fast_path"], 4)
        self.assertEqual(agg["routing_counts"]["agent"], 4)
        self.assertEqual(agg["routable_interactions"], 8)
        self.assertEqual(agg["routing_distribution_pct"]["fast_path"], 50.0)
        self.assertEqual(camp["wake"]["detections"], 3)

    def test_latency_from_traces(self):
        traces = [
            {"route": "fast_path", "route_bucket": "fast_path",
             "latency_ms": {"stt_ms": 100.0, "total_response_ms": 200.0}},
            {"route": "agent", "route_bucket": "agent",
             "latency_ms": {"stt_ms": 300.0, "planner_ms": 500.0}},
        ]
        self._write(
            "rc_validation_20260825T090000.json",
            _session("2026-08-25T09:00:00", traces=traces),
        )
        camp = merge_session_reports(diag_dir=self.diag)
        lat = camp["aggregated_metrics"]["latency"]
        self.assertEqual(lat["stt"]["count"], 2)
        self.assertEqual(lat["stt"]["p50"], 300.0)

    def test_does_not_modify_originals(self):
        original = _session(
            "2026-08-25T09:00:00",
            manual=[{"test_id": "wake_word", "result": "PASS", "note": "x", "ts": "a"}],
        )
        path = self._write("rc_validation_20260825T090000.json", original)
        before = path.read_text(encoding="utf-8")
        run_merge(diag_dir=self.diag, quiet=True)
        after = path.read_text(encoding="utf-8")
        self.assertEqual(before, after)
        self.assertTrue((self.diag / "rc_validation_campaign.json").exists())
        self.assertTrue((self.diag / "RC_VALIDATION_CAMPAIGN_REPORT.md").exists())

    def test_catalog_size(self):
        self.assertEqual(len(RC_TEST_CASES), 24)


if __name__ == "__main__":
    unittest.main()
