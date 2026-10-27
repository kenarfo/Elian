# ==========================
# ELIAN — Self-Improvement System tests
# ==========================
"""Experience Log -> Reflection -> Gap -> Proposal -> Sandbox -> Verify -> Approve -> Adopt."""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from core.self_improve import tool_hot_swap
from core.self_improve.approval import ApprovalError, ApprovalGate
from core.self_improve.experience_log import (
    EVIDENCE_ALL,
    EVIDENCE_SEED,  # fixture-stable gap tests (runtime noise excluded)
    ExperienceEntry,
    ExperienceLogStore,
    FailureCategory,
)
from core.self_improve.gap_analysis import CapabilityGap, GapAnalyzer
from core.self_improve.proposal import (
    Proposal,
    ProposalGenerator,
    ProposalStatus,
    ProposalStore,
    Requirement,
)
from core.self_improve.reflection import ReflectionEngine
from core.self_improve.sandbox import ExecutionSandbox, scan_source
from core.self_improve.verification import VerificationLoop
from core.tools import Tool, ToolRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DUMMY_LOG = PROJECT_ROOT / "Data" / "self_improve" / "experience_log.json"


class ExperienceLogTests(unittest.TestCase):
    def test_dummy_log_schema(self):
        entries = ExperienceLogStore(DUMMY_LOG).load()
        self.assertGreaterEqual(len(entries), 20)
        for entry in entries:
            self.assertTrue(entry.id)
            self.assertTrue(entry.ts)
            self.assertTrue(entry.domain)
            self.assertIsInstance(entry.category, FailureCategory)
            self.assertIn(entry.outcome, ("success", "failure"))
            self.assertEqual(
                entry.outcome == "success",
                entry.category is FailureCategory.SUCCESS,
            )

    def test_dummy_log_has_scheduling_pattern(self):
        scheduling = ExperienceLogStore(DUMMY_LOG).by_domain("scheduling")
        self.assertGreaterEqual(len(scheduling), 8)
        self.assertGreaterEqual(
            sum(1 for entry in scheduling if entry.category is FailureCategory.NO_TOOL_MATCH),
            3,
        )

    def test_append_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            entry = store.append(
                ExperienceEntry(
                    domain="scheduling",
                    category=FailureCategory.NO_TOOL_MATCH,
                    summary="no tool for that",
                )
            )
            loaded = store.load()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].id, entry.id)
            self.assertEqual(loaded[0].outcome, "failure")

    def test_corrupt_log_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(ExperienceLogStore(path).load(), [])

    def test_invalid_rows_are_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.json"
            path.write_text(
                json.dumps({"schema": 1, "entries": [42, {}, {"domain": "web", "category": "SUCCESS", "summary": "ok"}]}),
                encoding="utf-8",
            )
            entries = ExperienceLogStore(path).load()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].domain, "web")


class ReflectionTests(unittest.TestCase):
    def test_reflection_suggests_one_code_improvement(self):
        # Seed fixture exercises the algorithm; learning CLI defaults to runtime.
        # Use seed evidence so live runtime noise cannot steal the top candidate.
        report = ReflectionEngine(ExperienceLogStore(DUMMY_LOG)).reflect(
            evidence=EVIDENCE_SEED
        )
        top = report.top()
        self.assertIsNotNone(top)
        self.assertEqual(top.domain, "scheduling")
        self.assertEqual(top.category, FailureCategory.NO_TOOL_MATCH.value)
        self.assertIn("recurring_schedule", top.suggestion)
        self.assertTrue(top.evidence_ids)

    def test_clean_domains_produce_no_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            for index in range(5):
                store.append(
                    ExperienceEntry(
                        domain="media",
                        category=FailureCategory.SUCCESS,
                        summary=f"played item {index}",
                    )
                )
            report = ReflectionEngine(store).reflect(evidence=EVIDENCE_ALL)
            self.assertEqual(report.candidates, [])
            self.assertIsNone(report.top())

    def test_report_render_mentions_domains(self):
        report = ReflectionEngine(ExperienceLogStore(DUMMY_LOG)).reflect(
            evidence=EVIDENCE_ALL
        )
        text = report.render()
        self.assertIn("scheduling", text)
        self.assertIn("priority", text)
        self.assertIn("dominant=NO_TOOL_MATCH", text)


class GapAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # Isolate the adoption manifest: the real one records what this
        # machine has actually adopted, which would make these assertions
        # depend on the developer's own self-improvement history.
        self.manifest = self.tmp / "adopted.json"
        self.learned = self.tmp / "learned"
        self.learned.mkdir(parents=True, exist_ok=True)
        self.analyzer = GapAnalyzer(
            ExperienceLogStore(DUMMY_LOG),
            gaps_path=self.tmp / "gaps.json",
            map_path=self.tmp / "map.json",
            manifest_path=self.manifest,
            learned_dir=self.learned,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_manifest(self, modules):
        self.manifest.write_text(
            json.dumps({"schema": 1, "modules": modules}), encoding="utf-8"
        )

    def _make_learned_module(self, name="recurring_schedule"):
        """A real file, because adopted_remedies() checks the module exists.

        Written into this test's own tmp learned/ dir: never the project's,
        so a cleanup can never delete a genuinely adopted module.
        """
        path = self.learned / f"{name}.py"
        path.write_text(
            "TOOL_SPEC = {'name': %r, 'description': 'x', "
            "'parameters': {'properties': {}}}\n"
            "def handle(arguments):\n"
            "    return {'success': True, 'message': 'ok', 'data': {}}\n" % name,
            encoding="utf-8",
        )
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path

    def test_user_correction_only_is_not_capability_gap(self):
        """Correction discourse alone must not invent a system capability gap."""
        from core.self_improve.experience_log import PROVENANCE_RUNTIME

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.json"
            store = ExperienceLogStore(path)
            for index in range(5):
                store.append(
                    ExperienceEntry(
                        domain="system",
                        category=FailureCategory.USER_CORRECTION,
                        summary=f"correction:{index}",
                        provenance=PROVENANCE_RUNTIME,
                    )
                )
            analyzer = GapAnalyzer(
                store,
                gaps_path=Path(tmp) / "gaps.json",
                map_path=Path(tmp) / "map.json",
                manifest_path=Path(tmp) / "adopted.json",
                learned_dir=Path(tmp) / "learned",
            )
            (Path(tmp) / "learned").mkdir(parents=True, exist_ok=True)
            gaps = analyzer.analyze(evidence="runtime")
            self.assertEqual(gaps, [])

    def test_tool_less_unknown_is_not_capability_gap(self):
        """E1: tool-less UNKNOWN must not open a general/structured_result gap."""
        from core.self_improve.experience_log import PROVENANCE_RUNTIME

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.json"
            store = ExperienceLogStore(path)
            for index in range(5):
                store.append(
                    ExperienceEntry(
                        domain="knowledge",
                        category=FailureCategory.UNKNOWN,
                        summary=f"unknown:{index}",
                        tool="",
                        provenance=PROVENANCE_RUNTIME,
                    )
                )
            analyzer = GapAnalyzer(
                store,
                gaps_path=Path(tmp) / "gaps.json",
                map_path=Path(tmp) / "map.json",
                manifest_path=Path(tmp) / "adopted.json",
                learned_dir=Path(tmp) / "learned",
            )
            (Path(tmp) / "learned").mkdir(parents=True, exist_ok=True)
            gaps = analyzer.analyze(evidence="runtime")
            self.assertEqual(gaps, [])
            # Propose path must also stay quiet (no structured_result).
            generator = ProposalGenerator(analyzer=analyzer)
            for gap in gaps:
                proposal = generator.for_gap(gap)
                self.assertNotEqual(proposal.module_name, "structured_result")

    def test_unknown_with_runtime_tool_still_proposes_structured_result(self):
        """E1: UNKNOWN with a real runtime tool keeps general/structured_result."""
        from core.self_improve.experience_log import PROVENANCE_RUNTIME

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.json"
            store = ExperienceLogStore(path)
            for index in range(5):
                store.append(
                    ExperienceEntry(
                        domain="knowledge",
                        category=FailureCategory.UNKNOWN,
                        summary=f"unknown_tool:{index}",
                        tool="web_search",
                        provenance=PROVENANCE_RUNTIME,
                        metadata={"error_code": "PROVIDER_EMPTY"},
                    )
                )
            analyzer = GapAnalyzer(
                store,
                gaps_path=Path(tmp) / "gaps.json",
                map_path=Path(tmp) / "map.json",
                manifest_path=Path(tmp) / "adopted.json",
                learned_dir=Path(tmp) / "learned",
            )
            (Path(tmp) / "learned").mkdir(parents=True, exist_ok=True)
            gaps = analyzer.analyze(evidence="runtime")
            by_domain = {gap.domain: gap for gap in gaps}
            self.assertIn("knowledge", by_domain)
            gap = by_domain["knowledge"]
            self.assertEqual(gap.kind, "general")
            self.assertEqual(gap.observed_tool, "web_search")
            proposal = ProposalGenerator(analyzer=analyzer).for_gap(gap)
            self.assertEqual(proposal.module_name, "structured_result")

    def test_scheduling_capability_gap_detected(self):
        # Seed fixture only — runtime SUCCESS noise must not dilute severity.
        gaps = self.analyzer.analyze(evidence=EVIDENCE_SEED)
        by_id = {gap.id: gap for gap in gaps}
        self.assertIn("gap_scheduling", by_id)
        gap = by_id["gap_scheduling"]
        self.assertEqual(gap.name, "Scheduling Capability Gap")
        self.assertEqual(gap.kind, "scheduling")
        self.assertEqual(gap.severity, "HIGH")
        self.assertEqual(gap.failure_modes.get("NO_TOOL_MATCH"), 4)

    def test_capability_map_statuses(self):
        gaps = self.analyzer.analyze(evidence=EVIDENCE_SEED)
        self.analyzer.save(gaps)
        capability_map = self.analyzer.write_capability_map(
            gaps, evidence=EVIDENCE_SEED
        )
        self.assertEqual(capability_map["scheduling"]["status"], "GAP")
        self.assertEqual(capability_map["media"]["status"], "HEALTHY")
        self.assertEqual(capability_map["scheduling"]["gap_id"], "gap_scheduling")
        reloaded = self.analyzer.load_capability_map()
        self.assertEqual(reloaded, capability_map)
        self.assertEqual([gap.id for gap in self.analyzer.load()], [gap.id for gap in gaps])

    def test_gap_is_addressed_when_a_remedy_was_adopted(self):
        self._make_learned_module("recurring_schedule")
        self._write_manifest({
            "recurring_schedule": {
                "gap_id": "gap_scheduling",
                "gap_name": "Scheduling Capability Gap",
                "tool_name": "recurring_schedule",
            }
        })
        gaps = self.analyzer.analyze(evidence=EVIDENCE_SEED)
        by_id = {gap.id: gap for gap in gaps}
        self.assertTrue(by_id["gap_scheduling"].addressed)
        self.assertEqual(by_id["gap_scheduling"].addressed_by, ["recurring_schedule"])
        # The web gap has no remedy and must stay open.
        self.assertFalse(by_id["gap_web"].addressed)

    def test_addressed_gap_shows_in_the_capability_map(self):
        self._make_learned_module("recurring_schedule")
        self._write_manifest({
            "recurring_schedule": {
                "gap_id": "gap_scheduling",
                "tool_name": "recurring_schedule",
            }
        })
        gaps = self.analyzer.analyze(evidence=EVIDENCE_SEED)
        self.analyzer.save(gaps)
        capability_map = self.analyzer.write_capability_map(
            gaps, evidence=EVIDENCE_SEED
        )
        self.assertEqual(capability_map["scheduling"]["status"], "ADDRESSED")
        self.assertEqual(capability_map["scheduling"]["addressed_by"], ["recurring_schedule"])
        # The real failure numbers stay visible - ADDRESSED is not HEALTHY.
        self.assertEqual(capability_map["web"]["status"], "GAP")
        self.assertGreater(capability_map["scheduling"]["failure_rate"], 0)

    def test_unlearned_module_reopens_the_gap(self):
        # Manifest says a remedy exists, but the file is gone (unlearned):
        # the gap must go back to open rather than claim to be fixed.
        self._write_manifest({
            "recurring_schedule": {
                "gap_id": "gap_scheduling",
                "tool_name": "recurring_schedule",
            }
        })
        gaps = self.analyzer.analyze(evidence=EVIDENCE_SEED)
        by_id = {gap.id: gap for gap in gaps}
        self.assertFalse(by_id["gap_scheduling"].addressed)
        capability_map = self.analyzer.write_capability_map(
            gaps, evidence=EVIDENCE_SEED
        )
        self.assertEqual(capability_map["scheduling"]["status"], "GAP")


class NextGapSelectionTests(unittest.TestCase):
    """`gaps` and `demo` must agree on what to work on next.

    Regression guard: `demo` once picked gaps[0] unconditionally and kept
    re-proposing a remedy that had already been adopted.
    """

    @staticmethod
    def _gap(gap_id, addressed=False, tools=()):
        return CapabilityGap(
            id=gap_id, name=gap_id, domain="x", kind="general", description="",
            failure_modes={}, evidence_count=1, failure_rate=0.9,
            severity="HIGH", addressed=addressed, addressed_by=list(tools),
        )

    def test_picks_the_first_unaddressed_gap(self):
        from core.self_improve.cli import next_open_gap

        gaps = [
            self._gap("gap_a", addressed=True, tools=["t1"]),
            self._gap("gap_b"),
            self._gap("gap_c"),
        ]
        self.assertEqual(next_open_gap(gaps).id, "gap_b")

    def test_returns_none_when_everything_is_addressed(self):
        from core.self_improve.cli import next_open_gap

        gaps = [self._gap("gap_a", addressed=True, tools=["t1"])]
        self.assertIsNone(next_open_gap(gaps))

    def test_addressed_note_names_the_remedy(self):
        from core.self_improve.cli import addressed_note

        gaps = [self._gap("gap_a", addressed=True, tools=["t1", "t2"])]
        self.assertIn("gap_a", addressed_note(gaps))
        self.assertIn("t1", addressed_note(gaps))


class SandboxSafetyTests(unittest.TestCase):
    def test_clean_source_passes(self):
        report = scan_source(
            "from __future__ import annotations\nimport unittest\nimport re\n\nX = 1\n"
        )
        self.assertTrue(report.passed, report.findings)

    def test_dangerous_imports_rejected(self):
        for source in ("import os\n", "import subprocess\n", "from pathlib import Path\n"):
            report = scan_source(source)
            self.assertFalse(report.passed, source)

    def test_dangerous_calls_rejected(self):
        for source in ("eval('1')\n", "open('x')\n", "__import__('os')\n"):
            report = scan_source(source)
            self.assertFalse(report.passed, source)

    def test_dunder_attribute_rejected(self):
        report = scan_source("class A:\n    pass\n\nA().__class__\n")
        self.assertFalse(report.passed)

    def test_syntax_error_fails_closed(self):
        report = scan_source("def broken(:\n")
        self.assertFalse(report.passed)


class VerificationTests(unittest.TestCase):
    """Sandbox runs are subprocess-based: ~1-2s per verify()."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = ProposalStore(self.tmp / "proposals.json")
        self.sandbox = ExecutionSandbox(root=self.tmp / "sandbox", timeout_s=60.0)
        self.generator = ProposalGenerator(
            GapAnalyzer(
                ExperienceLogStore(DUMMY_LOG),
                gaps_path=self.tmp / "gaps.json",
                map_path=self.tmp / "map.json",
            ),
            self.store,
        )
        self.loop = VerificationLoop(
            self.sandbox, reserved_names=set(), core_smoke=False
        )
        self.generator.analyzer.save(self.generator.analyzer.analyze(evidence=EVIDENCE_ALL))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _proposal(self, kind: str = "scheduling") -> Proposal:
        gaps = self.generator.analyzer.analyze(evidence=EVIDENCE_ALL)
        gap = next((gap for gap in gaps if gap.kind == kind), None)
        if gap is None:
            # Not every dummy-log domain maps to every kind: borrow a real gap
            # and retarget it so each template is exercised.
            gap = gaps[0]
            gap.kind = kind
        return self.generator.for_gap(gap)

    def test_generated_proposal_passes_verification(self):
        proposal = self._proposal()
        self.assertEqual(proposal.status, ProposalStatus.DRAFT.value)
        result = self.loop.verify(proposal, store=self.store)
        self.assertTrue(result.passed, result.render())
        self.assertTrue(result.check("safety").passed)
        self.assertTrue(result.check("unit_tests").passed)
        self.assertTrue(result.check("tool_contract").passed)
        self.assertEqual(
            self.store.get(proposal.id).status, ProposalStatus.VERIFIED.value
        )

    def test_all_template_kinds_verify(self):
        for kind in ("scheduling", "tool_lookup", "answer_quality", "reliability", "general"):
            with self.subTest(kind=kind):
                proposal = self._proposal(kind)
                result = self.loop.verify(proposal)
                self.assertTrue(result.passed, result.render())

    def test_failing_generated_test_blocks_verification(self):
        proposal = self._proposal("general")
        proposal.tests += (
            "\nclass DeliberateFailure(unittest.TestCase):\n"
            "    def test_boom(self):\n"
            "        self.fail('deliberate failure')\n"
        )
        result = self.loop.verify(proposal, store=self.store)
        self.assertFalse(result.passed)
        self.assertFalse(result.check("unit_tests").passed)
        self.assertEqual(
            self.store.get(proposal.id).status,
            ProposalStatus.FAILED_VERIFICATION.value,
        )

    def test_unsafe_code_blocks_verification_before_execution(self):
        proposal = self._proposal("general")
        proposal.code = "import os\n\nTOOL_SPEC = {}\n"
        result = self.loop.verify(proposal, store=self.store)
        self.assertFalse(result.passed)
        self.assertFalse(result.check("safety").passed)
        self.assertTrue(result.check("unit_tests").skipped)
        self.assertEqual(
            self.store.get(proposal.id).status,
            ProposalStatus.FAILED_VERIFICATION.value,
        )

    def test_name_collision_blocks_verification(self):
        proposal = self._proposal("general")
        loop = VerificationLoop(
            self.sandbox, reserved_names={proposal.tool_name}, core_smoke=False
        )
        result = loop.verify(proposal, store=self.store)
        self.assertFalse(result.passed)
        self.assertFalse(result.check("name_collision").passed)


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = ProposalStore(self.tmp / "proposals.json")
        self.gate = ApprovalGate(self.store)
        self.proposal = self._draft()
        self.store.put(self.proposal)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _draft(self) -> Proposal:
        return Proposal(
            id="prop_test_1",
            gap_id="gap_scheduling",
            gap_name="Scheduling Capability Gap",
            domain="scheduling",
            kind="scheduling",
            requirement=Requirement(
                id="req_1", title="T", statement="S", acceptance=["a"]
            ),
            module_name="recurring_schedule",
            tool_name="recurring_schedule",
            code="TOOL_SPEC = {}\n",
            tests="",
            test_module="test_recurring_schedule",
            status=ProposalStatus.DRAFT.value,
        )

    def _mark_verified(self, proposal: Proposal) -> Proposal:
        proposal.status = ProposalStatus.VERIFIED.value
        proposal.verification = {
            "proposal_id": proposal.id,
            "passed": True,
            "checks": [],
            "started_at": "",
            "duration_s": 0.1,
        }
        return self.store.put(proposal)

    def test_review_shows_code_and_requirement(self):
        text = self.gate.review("prop_test_1")
        self.assertIn("Scheduling Capability Gap", text)
        self.assertIn("TOOL_SPEC = {}", text)
        self.assertIn("VERIFICATION: not run yet", text)

    def test_approve_refuses_unverified(self):
        with self.assertRaises(ApprovalError):
            self.gate.approve("prop_test_1")

    def test_approve_requires_human_yes(self):
        self._mark_verified(self.store.get("prop_test_1"))
        with self.assertRaises(ApprovalError):
            self.gate.approve("prop_test_1", confirm_fn=lambda _prompt: "")
        with self.assertRaises(ApprovalError):
            self.gate.approve("prop_test_1", confirm_fn=lambda _prompt: "n")
        proposal = self.gate.approve(
            "prop_test_1", confirm_fn=lambda _prompt: "y", note="looks good"
        )
        self.assertEqual(proposal.status, ProposalStatus.APPROVED.value)
        self.assertEqual(proposal.approval["approved_by"], "user")

    def test_reject_marks_rejected(self):
        self._mark_verified(self.store.get("prop_test_1"))
        proposal = self.gate.reject("prop_test_1", "not now")
        self.assertEqual(proposal.status, ProposalStatus.REJECTED.value)


class AdoptionTests(unittest.TestCase):
    """Full pipeline: gap -> proposal -> verify -> approve -> adopt -> hot-swap."""

    MODULE = "recurring_schedule"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # Adoption deliberately writes into the real learned/ package (the module
        # has to be importable from there), so snapshot whatever is already
        # there and put it back in tearDown instead of destroying it.
        self._saved_module = None
        self._real_module_path = tool_hot_swap.LEARNED_DIR / f"{self.MODULE}.py"
        if self._real_module_path.exists():
            self._saved_module = self._real_module_path.read_bytes()
            # Move it aside so the test adopts onto a clean slate; tearDown
            # puts the original bytes back untouched.
            self._real_module_path.unlink()
        self._saved_manifest = None
        if tool_hot_swap.MANIFEST_PATH.exists():
            self._saved_manifest = tool_hot_swap.MANIFEST_PATH.read_bytes()
        self.store = ProposalStore(self.tmp / "proposals.json")
        self.sandbox = ExecutionSandbox(root=self.tmp / "sandbox", timeout_s=60.0)
        self.generator = ProposalGenerator(
            GapAnalyzer(
                ExperienceLogStore(DUMMY_LOG),
                gaps_path=self.tmp / "gaps.json",
                map_path=self.tmp / "map.json",
            ),
            self.store,
        )
        self.loop = VerificationLoop(self.sandbox, reserved_names=set(), core_smoke=False)
        self.registry = None
        # Seed-only: runtime successes in experience_log dilute scheduling
        # below DEFAULT_MIN_FAILURE_RATE when evidence=all.
        self.generator.analyzer.save(
            self.generator.analyzer.analyze(evidence=EVIDENCE_SEED)
        )

    def tearDown(self):
        # Pass this test's own store: with store=None, unlearn() falls back to
        # the project's real proposals.json and resets genuinely adopted
        # proposals back to APPROVED.
        tool_hot_swap.unlearn(self.MODULE, registry=self.registry, store=self.store)
        tool_hot_swap._LOADED.pop(self.MODULE, None)
        # Restore exactly the state that existed before this test ran.
        if self._saved_module is not None:
            self._real_module_path.write_bytes(self._saved_module)
        elif self._real_module_path.exists():
            self._real_module_path.unlink()
        if self._saved_manifest is not None:
            tool_hot_swap.MANIFEST_PATH.write_bytes(self._saved_manifest)
        elif tool_hot_swap.MANIFEST_PATH.exists():
            tool_hot_swap.MANIFEST_PATH.unlink()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_pipeline_hot_swaps_a_new_tool(self):
        gap = next(
            g
            for g in self.generator.analyzer.analyze(evidence=EVIDENCE_SEED)
            if g.kind == "scheduling"
        )
        proposal = self.generator.propose(gap.id)

        # 1. adoption without verification/approval is refused
        with self.assertRaises(tool_hot_swap.AdoptionError):
            tool_hot_swap.adopt(proposal, store=self.store)
        with self.assertRaises(ApprovalError):
            ApprovalGate(self.store).approve(proposal.id)

        # 2. verification passes
        result = self.loop.verify(proposal, store=self.store)
        self.assertTrue(result.passed, result.render())

        # 3. still refused until a human approves
        with self.assertRaises(tool_hot_swap.AdoptionError):
            tool_hot_swap.adopt(self.store.get(proposal.id), store=self.store)

        # 4. human approves, then adoption writes the module
        ApprovalGate(self.store).approve(proposal.id, confirm_fn=lambda _p: "yes")
        target = tool_hot_swap.adopt(self.store.get(proposal.id), store=self.store)
        self.assertTrue(target.exists())
        self.assertEqual(target.parent, tool_hot_swap.LEARNED_DIR)
        self.assertEqual(
            self.store.get(proposal.id).status, ProposalStatus.ADOPTED.value
        )

        # 5. hot-swap into a registry (no restart)
        registry = self.registry = ToolRegistry()
        loaded = tool_hot_swap.load_learned_tools(registry)
        self.assertEqual(loaded, ["recurring_schedule"])
        tool = registry.get("recurring_schedule")
        self.assertIsNotNone(tool)
        self.assertEqual(getattr(tool, "learned_module", ""), self.MODULE)

        # 6. the learned tool actually works through the wrapped handler
        outcome = tool.handler(
            {"action": "expand", "start": "2026-01-05", "count": 3}
        )
        self.assertTrue(outcome.success, outcome.message)
        self.assertEqual(
            outcome.data["occurrences"],
            ["2026-01-05", "2026-01-12", "2026-01-19"],
        )

        # 7. reload is idempotent (no duplicate registration)
        self.assertEqual(tool_hot_swap.load_learned_tools(registry), [])
        self.assertEqual(len(registry.list_tools()), 1)

        # 8. unlearn removes file + tool and puts the proposal back to APPROVED
        self.assertTrue(
            tool_hot_swap.unlearn(self.MODULE, registry=registry, store=self.store)
        )
        self.assertIsNone(registry.get("recurring_schedule"))
        self.assertFalse((tool_hot_swap.LEARNED_DIR / f"{self.MODULE}.py").exists())
        rolled_back = self.store.get(proposal.id)
        self.assertEqual(rolled_back.status, ProposalStatus.APPROVED.value)
        self.assertIsNone(rolled_back.adoption)
        self.assertEqual(rolled_back.approval["approved_by"], "user")

        # and it can be adopted again afterwards
        target = tool_hot_swap.adopt(rolled_back, store=self.store)
        self.assertTrue(target.exists())

    def test_learned_tool_never_shadows_a_builtin(self):
        from core.risk import RiskLevel
        from core.tool_result import ToolResult

        (tool_hot_swap.LEARNED_DIR / f"{self.MODULE}.py").write_text(
            "TOOL_SPEC = {'name': 'recurring_schedule', 'description': 'x', "
            "'parameters': {'properties': {}}}\n"
            "def handle(arguments):\n"
            "    return {'success': True, 'message': 'learned', 'data': {}}\n",
            encoding="utf-8",
        )
        registry = self.registry = ToolRegistry()
        registry.register(
            Tool(
                name="recurring_schedule",
                description="builtin",
                handler=lambda _args: ToolResult.ok("builtin"),
                risk_level=RiskLevel.SAFE,
            )
        )
        self.assertEqual(tool_hot_swap.load_learned_tools(registry), [])
        tool = registry.get("recurring_schedule")
        self.assertEqual(tool.description, "builtin")
        self.assertFalse(getattr(tool, "learned_module", ""))


class CliTests(unittest.TestCase):
    def test_reflect_command_exits_zero(self):
        from core.self_improve.cli import main

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["--json", "reflect"])
        self.assertEqual(code, 0)
        self.assertIn("candidates", buffer.getvalue())

    def test_list_command_runs(self):
        from core.self_improve.cli import main

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["list"])
        self.assertEqual(code, 0)
        self.assertIn("prop", buffer.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
