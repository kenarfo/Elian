# ==========================
# ELIAN — Supervised dry-run tests
# ==========================
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from core.self_improve.experience_log import (
    ExperienceEntry,
    ExperienceLogStore,
    FailureCategory,
    PROVENANCE_RUNTIME,
    PROVENANCE_SEED,
)
from core.self_improve.supervised_dry_run import (
    STATUS_EMPTY,
    STATUS_SPARSE,
    audit_runtime_corpus,
    run_supervised_dry_run,
)
from core.self_improve.cli import main as cli_main


def _runtime(
    *,
    domain: str,
    category: FailureCategory,
    tool: str = "",
    error_code: str = "",
    repeat_count: int = 1,
    summary: str = "runtime",
    source: str = "agent",
    extra_meta: dict | None = None,
) -> ExperienceEntry:
    meta = {"source": source, "fingerprint": f"fp_{domain}_{tool}_{category.value}_{error_code}"}
    if error_code:
        meta["error_code"] = error_code
    if extra_meta:
        meta.update(extra_meta)
    return ExperienceEntry(
        domain=domain,
        category=category,
        summary=summary,
        tool=tool,
        provenance=PROVENANCE_RUNTIME,
        repeat_count=repeat_count,
        metadata=meta,
    )


class CorpusAuditTests(unittest.TestCase):
    def test_1_sparse_runtime_corpus(self):
        rows = [
            _runtime(
                domain="media",
                category=FailureCategory.SUCCESS,
                tool="play_media",
                repeat_count=1,
            ),
            _runtime(
                domain="media",
                category=FailureCategory.TOOL_ERROR,
                tool="play_media",
                error_code="EXEC_FAILED",
                repeat_count=1,
            ),
        ]
        audit = audit_runtime_corpus(rows)
        self.assertEqual(audit.status, STATUS_SPARSE)
        self.assertEqual(audit.physical_rows, 2)
        report = run_supervised_dry_run(entries=rows)
        self.assertEqual(report.evidence, "runtime")
        self.assertEqual(report.audit.seed_manual_excluded_rows, 0)
        text = report.render()
        self.assertIn("STATUS: SPARSE", text)
        self.assertIn("WARNING", text)

    def test_2_weighted_statistics(self):
        rows = [
            _runtime(
                domain="messaging",
                category=FailureCategory.TOOL_ERROR,
                tool="find_contact",
                error_code="FAILURE",
                repeat_count=5,
            ),
            _runtime(
                domain="messaging",
                category=FailureCategory.TOOL_ERROR,
                tool="find_contact",
                error_code="RATE_LIMIT",
                repeat_count=2,
            ),
        ]
        audit = audit_runtime_corpus(rows)
        self.assertEqual(audit.physical_rows, 2)
        self.assertEqual(audit.weighted_occurrences, 7)
        self.assertEqual(audit.failure_occurrences, 7)

    def test_3_seed_isolation(self):
        rows = [
            ExperienceEntry(
                domain="messaging",
                category=FailureCategory.TOOL_ERROR,
                summary="seed",
                tool="find_contact",
                provenance=PROVENANCE_SEED,
                repeat_count=1,
            )
            for _ in range(20)
        ]
        rows.append(
            _runtime(
                domain="media",
                category=FailureCategory.SUCCESS,
                tool="play_media",
                repeat_count=1,
            )
        )
        report = run_supervised_dry_run(entries=rows)
        self.assertEqual(report.audit.weighted_occurrences, 1)
        self.assertEqual(report.audit.seed_manual_excluded_rows, 20)
        self.assertNotIn("messaging", report.audit.domains)

    def test_4_failure_rate(self):
        rows = [
            _runtime(
                domain="web",
                category=FailureCategory.TOOL_ERROR,
                tool="web_search",
                error_code="FAILURE",
                repeat_count=5,
            ),
            _runtime(
                domain="web",
                category=FailureCategory.SUCCESS,
                tool="web_search",
                repeat_count=5,
            ),
        ]
        audit = audit_runtime_corpus(rows)
        self.assertEqual(audit.weighted_occurrences, 10)
        self.assertEqual(audit.failure_occurrences, 5)
        self.assertEqual(audit.success_occurrences, 5)
        self.assertEqual(audit.failure_rate, 0.5)

    def test_5_candidate_evidence_preserves_tool_error(self):
        rows = [
            _runtime(
                domain="messaging",
                category=FailureCategory.TOOL_ERROR,
                tool="find_contact",
                error_code="FAILURE",
                repeat_count=5,
            ),
            _runtime(
                domain="messaging",
                category=FailureCategory.TOOL_ERROR,
                tool="find_contact",
                error_code="RATE_LIMIT",
                repeat_count=2,
            ),
            _runtime(
                domain="messaging",
                category=FailureCategory.SUCCESS,
                tool="send_message",
                repeat_count=1,
            ),
        ]
        report = run_supervised_dry_run(entries=rows)
        cand = next(c for c in report.candidates if c.domain == "messaging")
        self.assertEqual(cand.observed_tool, "find_contact")
        self.assertEqual(cand.error_code, "FAILURE")
        self.assertEqual(cand.weighted_failures, 7)
        self.assertTrue(cand.qualified)
        self.assertEqual(cand.decision, "QUALIFIED")
        self.assertTrue(cand.evidence)
        self.assertTrue(any(e.error_code == "FAILURE" for e in cand.evidence))
        text = report.render()
        self.assertIn("find_contact", text)
        self.assertIn("FAILURE", text)
        self.assertIn("QUALIFIED", text)

    def test_6_vague_answer_distinct(self):
        rows = [
            _runtime(
                domain="app",
                category=FailureCategory.VAGUE_ANSWER,
                tool="",
                error_code="VACUOUS_REPLY",
                repeat_count=4,
                extra_meta={"answer_quality": "insufficient", "vacuous_reply": True},
            ),
            _runtime(
                domain="app",
                category=FailureCategory.SUCCESS,
                tool="open_app",
                repeat_count=1,
            ),
        ]
        report = run_supervised_dry_run(entries=rows)
        self.assertEqual(report.audit.category_count("VAGUE_ANSWER"), 4)
        cand = next(c for c in report.candidates if c.domain == "app")
        self.assertEqual(cand.category, FailureCategory.VAGUE_ANSWER.value)
        self.assertNotEqual(cand.category, FailureCategory.TOOL_ERROR.value)
        self.assertIn("(none)", report.render())  # no fabricated tool

    def test_7_no_mutation(self):
        project = Path(__file__).resolve().parents[1]
        learned = project / "core" / "self_improve" / "learned"
        proposals = project / "Data" / "self_improve" / "proposals.json"
        gaps_path = project / "Data" / "self_improve" / "capability_gaps.json"
        map_path = project / "Data" / "self_improve" / "capability_map.json"
        adopted = project / "Data" / "self_improve" / "adopted.json"

        before_learned = sorted(p.name for p in learned.glob("*.py")) if learned.exists() else []
        before_props = proposals.read_text(encoding="utf-8") if proposals.exists() else ""
        before_gaps = gaps_path.read_text(encoding="utf-8") if gaps_path.exists() else ""
        before_map = map_path.read_text(encoding="utf-8") if map_path.exists() else ""
        before_adopted = adopted.read_text(encoding="utf-8") if adopted.exists() else ""

        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            for _ in range(5):
                store.append(
                    _runtime(
                        domain="messaging",
                        category=FailureCategory.TOOL_ERROR,
                        tool="find_contact",
                        error_code="FAILURE",
                        repeat_count=1,
                    )
                )
            report = run_supervised_dry_run(store)
            self.assertFalse(report.mutated)
            # CLI path (stdout redirected — Windows consoles may use legacy code pages)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cli_main(["--log", str(Path(tmp) / "log.json"), "dry-run"])
            self.assertEqual(code, 0)
            self.assertIn("SUPERVISED DRY-RUN", buf.getvalue())

        after_learned = sorted(p.name for p in learned.glob("*.py")) if learned.exists() else []
        self.assertEqual(before_learned, after_learned)
        if proposals.exists():
            self.assertEqual(before_props, proposals.read_text(encoding="utf-8"))
        if gaps_path.exists():
            self.assertEqual(before_gaps, gaps_path.read_text(encoding="utf-8"))
        if map_path.exists():
            self.assertEqual(before_map, map_path.read_text(encoding="utf-8"))
        if adopted.exists():
            self.assertEqual(before_adopted, adopted.read_text(encoding="utf-8"))

    def test_8_empty_corpus(self):
        report = run_supervised_dry_run(entries=[])
        self.assertEqual(report.audit.status, STATUS_EMPTY)
        self.assertEqual(report.audit.weighted_occurrences, 0)
        text = report.render()
        self.assertIn("No runtime evidence", text)
        self.assertIn("impossible", text.lower())

    def test_9_legacy_missing_provenance_excluded(self):
        legacy = ExperienceEntry.from_dict(
            {
                "domain": "web",
                "category": "TOOL_ERROR",
                "summary": "legacy no provenance",
                "tool": "web_search",
                "metadata": {"error_code": "FAILURE"},
            }
        )
        self.assertEqual(legacy.provenance, PROVENANCE_SEED)
        rows = [
            legacy,
            _runtime(
                domain="media",
                category=FailureCategory.SUCCESS,
                tool="play_media",
                repeat_count=1,
            ),
        ]
        audit = audit_runtime_corpus(rows)
        self.assertEqual(audit.weighted_occurrences, 1)
        self.assertEqual(audit.seed_manual_excluded_rows, 1)
        self.assertNotIn("web", audit.domains)

    def test_coverage_indicators(self):
        rows = [
            _runtime(
                domain="app",
                category=FailureCategory.VAGUE_ANSWER,
                error_code="VACUOUS_REPLY",
                repeat_count=1,
                extra_meta={
                    "answer_quality": "insufficient",
                    "vacuous_reply": True,
                    "grounding_status": "NONE",
                    "grounded": False,
                },
            ),
            _runtime(
                domain="media",
                category=FailureCategory.SUCCESS,
                tool="play_media",
                source="fast_path",
                repeat_count=1,
            ),
        ]
        audit = audit_runtime_corpus(rows)
        self.assertEqual(audit.agent_path_rows, 1)
        self.assertEqual(audit.answer_quality_meta_rows, 1)
        self.assertEqual(audit.grounding_meta_rows, 1)


class DiscourseNoiseFilterTests(unittest.TestCase):
    """D1: tool-less USER_CORRECTION / UNKNOWN must not propose structured_result."""

    def test_tool_less_user_correction_no_structured_result(self):
        rows = [
            _runtime(
                domain="system",
                category=FailureCategory.USER_CORRECTION,
                tool="",
                repeat_count=5,
            ),
        ]
        report = run_supervised_dry_run(entries=rows)
        cand = next(c for c in report.candidates if c.domain == "system")
        self.assertEqual(cand.category, FailureCategory.USER_CORRECTION.value)
        self.assertFalse(cand.observed_tool)
        self.assertFalse(cand.qualified)
        self.assertEqual(cand.decision, "BELOW THRESHOLD")
        self.assertNotEqual(cand.proposal_module_name, "structured_result")
        self.assertEqual(cand.proposal_module_name, "")
        self.assertEqual(cand.proposal_tool_name, "")

    def test_tool_less_unknown_no_structured_result(self):
        rows = [
            _runtime(
                domain="knowledge",
                category=FailureCategory.UNKNOWN,
                tool="",
                repeat_count=6,
            ),
        ]
        report = run_supervised_dry_run(entries=rows)
        cand = next(c for c in report.candidates if c.domain == "knowledge")
        self.assertEqual(cand.category, FailureCategory.UNKNOWN.value)
        self.assertFalse(cand.observed_tool)
        self.assertFalse(cand.qualified)
        self.assertEqual(cand.decision, "BELOW THRESHOLD")
        self.assertNotEqual(cand.proposal_module_name, "structured_result")
        self.assertEqual(cand.proposal_module_name, "")
        self.assertEqual(cand.proposal_tool_name, "")

    def test_unknown_with_runtime_tool_still_proposes_structured_result(self):
        rows = [
            _runtime(
                domain="knowledge",
                category=FailureCategory.UNKNOWN,
                tool="web_search",
                error_code="PROVIDER_EMPTY",
                repeat_count=5,
            ),
            _runtime(
                domain="knowledge",
                category=FailureCategory.SUCCESS,
                tool="web_search",
                repeat_count=1,
            ),
        ]
        report = run_supervised_dry_run(entries=rows)
        cand = next(c for c in report.candidates if c.domain == "knowledge")
        self.assertEqual(cand.category, FailureCategory.UNKNOWN.value)
        self.assertEqual(cand.observed_tool, "web_search")
        self.assertTrue(cand.qualified)
        self.assertEqual(cand.proposal_module_name, "structured_result")

    def test_user_correction_with_runtime_tool_keeps_proposal_path(self):
        rows = [
            _runtime(
                domain="system",
                category=FailureCategory.USER_CORRECTION,
                tool="set_volume",
                error_code="WRONG_TARGET",
                repeat_count=5,
            ),
        ]
        report = run_supervised_dry_run(entries=rows)
        cand = next(c for c in report.candidates if c.domain == "system")
        self.assertEqual(cand.category, FailureCategory.USER_CORRECTION.value)
        self.assertEqual(cand.observed_tool, "set_volume")
        self.assertTrue(cand.qualified)
        # With a runtime tool, dry-run may still attach a proposal (answer_quality).
        self.assertTrue(cand.proposal_module_name)
        self.assertNotEqual(cand.proposal_module_name, "")

    def test_tool_error_still_qualifies_as_before(self):
        rows = [
            _runtime(
                domain="messaging",
                category=FailureCategory.TOOL_ERROR,
                tool="find_contact",
                error_code="FAILURE",
                repeat_count=5,
            ),
            _runtime(
                domain="messaging",
                category=FailureCategory.SUCCESS,
                tool="send_message",
                repeat_count=1,
            ),
        ]
        report = run_supervised_dry_run(entries=rows)
        cand = next(c for c in report.candidates if c.domain == "messaging")
        self.assertTrue(cand.qualified)
        self.assertEqual(cand.observed_tool, "find_contact")
        self.assertEqual(cand.error_code, "FAILURE")
        self.assertTrue(cand.proposal_module_name)


class CliDryRunSmokeTests(unittest.TestCase):
    def test_cli_dry_run_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.json"
            store = ExperienceLogStore(path)
            store.append(
                _runtime(
                    domain="media",
                    category=FailureCategory.SUCCESS,
                    tool="play_media",
                )
            )
            # Capture via json path through the API used by CLI.
            report = run_supervised_dry_run(store)
            payload = report.to_dict()
            self.assertTrue(payload["read_only"])
            self.assertFalse(payload["mutated"])
            self.assertEqual(payload["evidence"], "runtime")
            json.dumps(payload)  # serializable


if __name__ == "__main__":
    unittest.main()
