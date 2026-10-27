# ==========================
# ELIAN — Reflection uses real runtime tool + error_code
# ==========================
"""Regression: concrete observed tools must survive Reflect → Gap → Proposal."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.self_improve.experience_log import (
    EVIDENCE_RUNTIME,
    ExperienceEntry,
    ExperienceLogStore,
    FailureCategory,
    PROVENANCE_RUNTIME,
    PROVENANCE_SEED,
)
from core.self_improve.gap_analysis import GapAnalyzer
from core.self_improve.proposal import ProposalGenerator
from core.self_improve.reflection import (
    DOMAIN_TOOL,
    ReflectionEngine,
    resolve_suggestion_tool,
    tool_for_domain,
)


def _runtime_failure(
    *,
    domain: str = "messaging",
    tool: str = "find_contact",
    category: FailureCategory = FailureCategory.TOOL_ERROR,
    error_code: str = "",
    repeat_count: int = 1,
    summary: str = "tool failed",
    entry_id: str = "",
) -> ExperienceEntry:
    meta = {}
    if error_code:
        meta["error_code"] = error_code
    return ExperienceEntry(
        domain=domain,
        category=category,
        summary=summary,
        tool=tool,
        provenance=PROVENANCE_RUNTIME,
        repeat_count=repeat_count,
        metadata=meta,
        id=entry_id or "",
    )


class ReflectionToolEvidenceTests(unittest.TestCase):
    def test_1_real_tool_survives_reflection(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            store.append(
                _runtime_failure(tool="find_contact", repeat_count=5, error_code="FAILURE")
            )
            report = ReflectionEngine(store).reflect(evidence=EVIDENCE_RUNTIME)
            cand = report.candidate_for("messaging")
            self.assertIsNotNone(cand)
            self.assertEqual(cand.observed_tool, "find_contact")
            self.assertTrue(cand.tool_from_runtime)
            self.assertIn("find_contact", cand.suggestion)
            self.assertNotIn("capability_helper", cand.suggestion)
            gaps = GapAnalyzer(store).analyze(report)
            gap = next(g for g in gaps if g.domain == "messaging")
            self.assertEqual(gap.observed_tool, "find_contact")
            self.assertNotIn("capability_helper", gap.description)

    def test_2_real_tool_beats_domain_fallback(self):
        # messaging has no DOMAIN_TOOL entry → capability_helper is the fallback.
        self.assertEqual(tool_for_domain("messaging"), "capability_helper")
        self.assertNotIn("messaging", DOMAIN_TOOL)
        tool, from_runtime = resolve_suggestion_tool(
            domain="messaging", tool_counts={"find_contact": 5}
        )
        self.assertEqual(tool, "find_contact")
        self.assertTrue(from_runtime)
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            store.append(_runtime_failure(tool="find_contact", repeat_count=5))
            report = ReflectionEngine(store).reflect(evidence=EVIDENCE_RUNTIME)
            cand = report.candidate_for("messaging")
            self.assertEqual(cand.observed_tool, "find_contact")
            self.assertNotEqual(cand.observed_tool, "capability_helper")
            self.assertNotIn("capability_helper", cand.suggestion)

    def test_3_occurrence_weighting_selects_dominant_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            store.append(
                _runtime_failure(
                    tool="find_contact",
                    repeat_count=5,
                    entry_id="exp_fc",
                    summary="find failed",
                )
            )
            store.append(
                _runtime_failure(
                    tool="send_message",
                    repeat_count=1,
                    entry_id="exp_sm",
                    summary="send failed",
                )
            )
            report = ReflectionEngine(store).reflect(evidence=EVIDENCE_RUNTIME)
            domain = next(d for d in report.domains if d.domain == "messaging")
            self.assertEqual(domain.tool_counts["find_contact"], 5)
            self.assertEqual(domain.tool_counts["send_message"], 1)
            self.assertEqual(domain.observed_tool, "find_contact")
            cand = report.candidate_for("messaging")
            self.assertEqual(cand.observed_tool, "find_contact")

    def test_4_error_code_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            store.append(
                _runtime_failure(
                    tool="find_contact",
                    error_code="SOME_ERROR",
                    repeat_count=4,
                )
            )
            report = ReflectionEngine(store).reflect(evidence=EVIDENCE_RUNTIME)
            cand = report.candidate_for("messaging")
            self.assertEqual(cand.error_code, "SOME_ERROR")
            gaps = GapAnalyzer(store).analyze(report)
            gap = next(g for g in gaps if g.domain == "messaging")
            self.assertEqual(gap.error_code, "SOME_ERROR")
            proposal = ProposalGenerator().for_gap(gap)
            self.assertIn("find_contact", proposal.requirement.statement)
            self.assertIn("SOME_ERROR", proposal.requirement.statement)

    def test_5_different_errors_remain_distinguishable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            store.append(
                _runtime_failure(
                    tool="find_contact",
                    error_code="TIMEOUT",
                    repeat_count=2,
                    entry_id="exp_to",
                )
            )
            store.append(
                _runtime_failure(
                    tool="find_contact",
                    error_code="FAILURE",
                    repeat_count=8,
                    entry_id="exp_fail",
                )
            )
            report = ReflectionEngine(store).reflect(evidence=EVIDENCE_RUNTIME)
            cand = report.candidate_for("messaging")
            self.assertEqual(cand.error_code, "FAILURE")
            self.assertEqual(cand.error_code_counts.get("TIMEOUT"), 2)
            self.assertEqual(cand.error_code_counts.get("FAILURE"), 8)
            # Distinct codes remain in the counts; representative is weighted.
            self.assertGreater(
                cand.error_code_counts["FAILURE"], cand.error_code_counts["TIMEOUT"]
            )

    def test_6_no_concrete_tool_keeps_domain_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            store.append(
                _runtime_failure(tool="", repeat_count=5, summary="messaging failed")
            )
            report = ReflectionEngine(store).reflect(evidence=EVIDENCE_RUNTIME)
            cand = report.candidate_for("messaging")
            self.assertIsNotNone(cand)
            self.assertEqual(cand.observed_tool, "")
            self.assertFalse(cand.tool_from_runtime)
            self.assertIn("capability_helper", cand.suggestion)
            gaps = GapAnalyzer(store).analyze(report)
            gap = next(g for g in gaps if g.domain == "messaging")
            self.assertEqual(gap.id, "gap_messaging")
            self.assertEqual(gap.observed_tool, "")

    def test_7_missing_metadata_error_code_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            store.append(
                ExperienceEntry(
                    domain="messaging",
                    category=FailureCategory.TOOL_ERROR,
                    summary="no error metadata",
                    tool="find_contact",
                    provenance=PROVENANCE_RUNTIME,
                    repeat_count=4,
                    metadata={},
                )
            )
            report = ReflectionEngine(store).reflect(evidence=EVIDENCE_RUNTIME)
            cand = report.candidate_for("messaging")
            self.assertEqual(cand.observed_tool, "find_contact")
            self.assertEqual(cand.error_code, "")
            GapAnalyzer(store).analyze(report)  # must not raise

    def test_8_mixed_tools_keep_dominant(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            store.append(
                _runtime_failure(tool="find_contact", repeat_count=8, entry_id="a")
            )
            store.append(
                _runtime_failure(tool="send_message", repeat_count=2, entry_id="b")
            )
            report = ReflectionEngine(store).reflect(evidence=EVIDENCE_RUNTIME)
            cand = report.candidate_for("messaging")
            self.assertEqual(cand.observed_tool, "find_contact")
            gaps = GapAnalyzer(store).analyze(report)
            gap = next(g for g in gaps if g.domain == "messaging")
            self.assertEqual(gap.observed_tool, "find_contact")
            self.assertEqual(gap.tool_counts.get("find_contact"), 8)
            self.assertEqual(gap.tool_counts.get("send_message"), 2)

    def test_9_seed_isolation_from_runtime_tool_aggregation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            # Seed noise with a different tool must not win under runtime evidence.
            for index in range(10):
                store.append(
                    ExperienceEntry(
                        domain="messaging",
                        category=FailureCategory.TOOL_ERROR,
                        summary=f"seed {index}",
                        tool="seed_only_tool",
                        provenance=PROVENANCE_SEED,
                        repeat_count=1,
                        metadata={"error_code": "SEED"},
                    )
                )
            store.append(
                _runtime_failure(
                    tool="find_contact",
                    error_code="FAILURE",
                    repeat_count=5,
                )
            )
            report = ReflectionEngine(store).reflect(evidence=EVIDENCE_RUNTIME)
            cand = report.candidate_for("messaging")
            self.assertIsNotNone(cand)
            self.assertEqual(cand.observed_tool, "find_contact")
            self.assertNotEqual(cand.observed_tool, "seed_only_tool")
            self.assertNotIn("seed_only_tool", cand.tool_counts)
            self.assertEqual(cand.error_code, "FAILURE")

    def test_10_gap_id_remains_domain_scoped(self):
        """Adoption compatibility: gap id stays gap_{domain}, tool is an attribute."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            store.append(
                _runtime_failure(tool="find_contact", error_code="FAILURE", repeat_count=5)
            )
            gaps = GapAnalyzer(store).analyze(
                ReflectionEngine(store).reflect(evidence=EVIDENCE_RUNTIME)
            )
            gap = next(g for g in gaps if g.domain == "messaging")
            self.assertEqual(gap.id, "gap_messaging")
            self.assertEqual(gap.observed_tool, "find_contact")


if __name__ == "__main__":
    unittest.main()
