# ==========================
# ELIAN — RC campaign persistence tests (Phase 9.6, offline)
# ==========================

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.rc_validation.campaign import (
    add_issue,
    all_test_ids,
    classify_campaign_release,
    create_campaign,
    delete_campaign,
    end_session,
    finish_campaign,
    get_or_resume_campaign,
    load_campaign,
    merge_session_metrics,
    record_test_result,
    save_campaign,
    start_session,
)
from core.rc_validation.collector import RCValidationCollector
from core.rc_validation.report import (
    build_campaign_report,
    classify_release,
    write_reports,
)
from core.rc_validation.tests import RC_TEST_CASES
from core.rc_validation.wake_policy import ACOUSTIC_STATUS_MISSING


class CampaignPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "rc_validation_campaign.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_pass_fail_survive_restart(self):
        camp = create_campaign()
        start_session(camp)
        record_test_result(camp, "wake_word", "PASS", note="ok")
        record_test_result(camp, "fastpath_volume", "FAIL", note="bad")
        end_session(camp, clean=True)
        save_campaign(camp, self.path)

        # Simulate restart
        loaded = load_campaign(self.path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["tests"]["wake_word"]["status"], "PASS")
        self.assertEqual(loaded["tests"]["fastpath_volume"]["status"], "FAIL")
        self.assertEqual(loaded["tests"]["wake_word"]["note"], "ok")

    def test_resume_does_not_reset_campaign(self):
        camp = create_campaign()
        sid = start_session(camp)
        record_test_result(camp, "stt_phrases", "PASS")
        end_session(camp, clean=True)
        save_campaign(camp, self.path)

        with patch(
            "core.rc_validation.campaign.campaign_path",
            return_value=self.path,
        ):
            resumed, is_resume, messages = get_or_resume_campaign(path=self.path)
        self.assertTrue(is_resume)
        self.assertEqual(resumed["campaign_id"], camp["campaign_id"])
        self.assertEqual(resumed["tests"]["stt_phrases"]["status"], "PASS")
        self.assertTrue(any("Resuming" in m for m in messages))
        # Starting a new session keeps prior results
        start_session(resumed)
        self.assertEqual(resumed["tests"]["stt_phrases"]["status"], "PASS")
        self.assertNotEqual(resumed["active_session_id"], sid)

    def test_unexpected_exit_detected_on_resume(self):
        camp = create_campaign()
        start_session(camp)
        # session_alive left True → crash
        save_campaign(camp, self.path)

        resumed, is_resume, messages = get_or_resume_campaign(path=self.path)
        self.assertTrue(is_resume)
        self.assertTrue(resumed["unexpected_exit_pending_review"])
        self.assertTrue(
            any(s.get("unexpected_exit") for s in resumed.get("sessions") or [])
        )
        self.assertTrue(any("unexpected_exit" in m for m in messages))
        high = [
            i for i in resumed.get("issues") or []
            if i.get("severity") == "HIGH"
        ]
        self.assertTrue(high)

    def test_reset_requires_confirmation_via_delete(self):
        camp = create_campaign()
        record_test_result(camp, "wake_word", "PASS")
        save_campaign(camp, self.path)
        self.assertTrue(self.path.exists())
        # reset = explicit delete (confirmation handled in runner)
        delete_campaign(self.path)
        self.assertFalse(self.path.exists())
        self.assertIsNone(load_campaign(self.path))


class ReleaseVerdictTests(unittest.TestCase):
    def _full_pass_campaign(self, *, missing_elian: bool = False):
        camp = create_campaign()
        for tid in all_test_ids():
            record_test_result(camp, tid, "PASS")
        camp["aggregated_metrics"] = {
            "wake_model": {
                "custom_elian_onnx_installed": not missing_elian,
                "acoustic_wake_status": (
                    ACOUSTIC_STATUS_MISSING if missing_elian else "READY"
                ),
                "wake_mode_used": "legacy_stt" if missing_elian else "acoustic",
            }
        }
        camp["custom_elian_onnx_installed"] = not missing_elian
        return camp

    def test_fail_never_ready(self):
        camp = create_campaign()
        record_test_result(camp, "natural_speech", "FAIL")
        verdict, notes = classify_campaign_release(camp)
        self.assertEqual(verdict, "NOT READY")
        self.assertTrue(notes)

        # session-only classifier
        v2, n2 = classify_release(
            {"crashes": 0, "wake_model": {"custom_elian_onnx_installed": True}},
            [{"result": "FAIL", "test_id": "natural_speech"}],
        )
        self.assertEqual(v2, "NOT READY")

    def test_partial_without_fail_is_ready_with_issues(self):
        camp = self._full_pass_campaign()
        record_test_result(camp, "media_controls", "PARTIAL", note="pause flaky")
        verdict, notes = classify_campaign_release(camp)
        self.assertEqual(verdict, "READY WITH ISSUES")
        self.assertTrue(any("PARTIAL" in n for n in notes))

    def test_complete_pass_is_ready(self):
        camp = self._full_pass_campaign(missing_elian=False)
        verdict, notes = classify_campaign_release(camp)
        self.assertEqual(verdict, "READY")
        self.assertEqual(notes, [])

    def test_missing_elian_caps_at_ready_with_issues(self):
        camp = self._full_pass_campaign(missing_elian=True)
        verdict, notes = classify_campaign_release(camp)
        self.assertEqual(verdict, "READY WITH ISSUES")
        self.assertTrue(any("CUSTOM MODEL MISSING" in n for n in notes))

    def test_unexpected_exit_forces_not_ready(self):
        camp = self._full_pass_campaign()
        camp["unexpected_exit_pending_review"] = True
        verdict, notes = classify_campaign_release(camp)
        self.assertEqual(verdict, "NOT READY")


class CampaignReportAggregationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_report_aggregates_multiple_sessions(self):
        camp = create_campaign()
        s1 = start_session(camp)
        record_test_result(camp, "wake_word", "PASS", session_id=s1)
        end_session(camp, clean=True)
        s2 = start_session(camp)
        record_test_result(camp, "natural_speech", "FAIL", session_id=s2)
        end_session(camp, clean=True)

        merge_session_metrics(camp, {
            "wake_count": 2,
            "routing_counts": {"fast_path": 3, "agent": 1, "brain": 0, "legacy": 0},
            "routable_interactions": 4,
            "interactions": 4,
            "wake_model": {
                "custom_elian_onnx_installed": False,
                "acoustic_wake_status": ACOUSTIC_STATUS_MISSING,
                "wake_mode_used": "legacy_stt",
            },
        })

        report = build_campaign_report(camp)
        self.assertEqual(report["scope"], "campaign")
        self.assertEqual(len(report["sessions"]), 2)
        self.assertEqual(report["summary"]["fail"], 1)
        self.assertEqual(report["summary"]["pass"], 1)
        self.assertEqual(report["summary"]["release_recommendation"], "NOT READY")
        session_ids = {s["session_id"] for s in report["sessions"]}
        self.assertEqual(session_ids, {s1, s2})

    def test_stamped_session_reports_remain(self):
        camp = create_campaign()
        start_session(camp)
        record_test_result(camp, "wake_word", "PASS")

        col = RCValidationCollector()
        col.active = True
        col.deactivate()

        with patch("core.rc_validation.report.DATA_DIR", self.dir):
            stamped1, _ = write_reports(col, campaign=camp)
            stamped2, latest = write_reports(col, campaign=camp)

        self.assertTrue(stamped1.exists())
        self.assertTrue(stamped2.exists())
        self.assertNotEqual(stamped1.name, stamped2.name)
        # both stamps preserved
        stamps = list((self.dir / "diagnostics").glob("rc_validation_20*.json"))
        self.assertGreaterEqual(len(stamps), 2)
        # latest is campaign aggregate
        payload = json.loads(
            (self.dir / "diagnostics" / "rc_validation_latest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(payload.get("scope"), "campaign")


class CatalogSizeTests(unittest.TestCase):
    def test_twenty_four_tests(self):
        self.assertEqual(len(RC_TEST_CASES), 24)
        self.assertEqual(len(all_test_ids()), 24)


if __name__ == "__main__":
    unittest.main()
