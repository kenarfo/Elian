# ==========================
# ELIAN — Self-improvement RC session harness tests
# ==========================
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.self_improve.experience_log import (
    ExperienceEntry,
    ExperienceLogStore,
    FailureCategory,
    PROVENANCE_RUNTIME,
    PROVENANCE_SEED,
)
from core.self_improve.rc_session import (
    RC_SESSION_ENV,
    entries_after,
    finalize_rc_session,
    find_suspicious_classifications,
    run_rc_session,
    snapshot_runtime_ids,
)
from core.self_improve.supervised_dry_run import audit_runtime_corpus


def _runtime(
    *,
    domain: str,
    category: FailureCategory,
    tool: str = "",
    error_code: str = "",
    repeat_count: int = 1,
    source: str = "agent",
    rc_session_id: str = "",
    intent: str = "",
    utterance_class: str = "",
    extra_meta: dict | None = None,
) -> ExperienceEntry:
    meta = {"source": source, "fingerprint": f"fp_{domain}_{tool}_{category.value}_{error_code}_{repeat_count}"}
    if error_code:
        meta["error_code"] = error_code
    if intent:
        meta["intent"] = intent
    if utterance_class:
        meta["utterance_class"] = utterance_class
    if rc_session_id:
        meta["rc_session_id"] = rc_session_id
    if extra_meta:
        meta.update(extra_meta)
    return ExperienceEntry(
        domain=domain,
        category=category,
        summary="rc",
        tool=tool,
        provenance=PROVENANCE_RUNTIME,
        repeat_count=repeat_count,
        metadata=meta,
    )


class RcSessionTests(unittest.TestCase):
    def test_1_empty_rc_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            store.append(
                _runtime(
                    domain="media",
                    category=FailureCategory.SUCCESS,
                    tool="play_media",
                )
            )
            before_ids = snapshot_runtime_ids(store)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                report = run_rc_session(
                    store,
                    launch_voice=False,
                    analyze_only=True,
                    rc_session_id="rc_empty_test",
                )
            self.assertEqual(report.new_physical_rows, 0)
            self.assertEqual(report.new_weighted_occurrences, 0)
            self.assertEqual(snapshot_runtime_ids(store), before_ids)
            self.assertIsNotNone(report.dry_run)
            self.assertFalse(report.mutated)
            self.assertIn("NO AUTO-ADOPT", report.render())

    def test_2_before_after_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            store.append(
                _runtime(
                    domain="media",
                    category=FailureCategory.SUCCESS,
                    tool="play_media",
                    repeat_count=1,
                )
            )
            before_ids = snapshot_runtime_ids(store)
            before_audit = audit_runtime_corpus(store.load())
            store.append(
                _runtime(
                    domain="messaging",
                    category=FailureCategory.TOOL_ERROR,
                    tool="find_contact",
                    error_code="FAILURE",
                    repeat_count=5,
                )
            )
            store.append(
                _runtime(
                    domain="app",
                    category=FailureCategory.VAGUE_ANSWER,
                    error_code="VACUOUS_REPLY",
                    repeat_count=2,
                    extra_meta={"answer_quality": "insufficient"},
                )
            )
            report = finalize_rc_session(
                store,
                before_ids=before_ids,
                before_audit=before_audit,
                rc_session_id="rc_acct",
                started_at="2026-01-01T00:00:00+00:00",
            )
            self.assertEqual(report.new_physical_rows, 2)
            self.assertEqual(report.new_weighted_occurrences, 7)
            self.assertIn("messaging", report.new_audit.domains)
            self.assertIn("app", report.new_audit.domains)
            self.assertEqual(report.new_audit.category_count("TOOL_ERROR"), 5)
            self.assertEqual(report.new_audit.category_count("VAGUE_ANSWER"), 2)

    def test_3_provenance_seed_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            before_ids = snapshot_runtime_ids(store)
            before_audit = audit_runtime_corpus(store.load())
            for _ in range(5):
                store.append(
                    ExperienceEntry(
                        domain="messaging",
                        category=FailureCategory.TOOL_ERROR,
                        summary="seed",
                        tool="find_contact",
                        provenance=PROVENANCE_SEED,
                        repeat_count=1,
                    )
                )
            store.append(
                _runtime(
                    domain="media",
                    category=FailureCategory.SUCCESS,
                    tool="play_media",
                    repeat_count=3,
                )
            )
            report = finalize_rc_session(
                store,
                before_ids=before_ids,
                before_audit=before_audit,
                rc_session_id="rc_prov",
                started_at="t0",
            )
            self.assertEqual(report.new_physical_rows, 1)
            self.assertEqual(report.new_weighted_occurrences, 3)
            self.assertNotIn("messaging", report.new_audit.domains)

    def test_4_no_mutation(self):
        project = Path(__file__).resolve().parents[1]
        learned = project / "core" / "self_improve" / "learned"
        proposals = project / "Data" / "self_improve" / "proposals.json"
        gaps_path = project / "Data" / "self_improve" / "capability_gaps.json"
        adopted = project / "Data" / "self_improve" / "adopted.json"
        before_learned = sorted(p.name for p in learned.glob("*.py")) if learned.exists() else []
        before_props = proposals.read_text(encoding="utf-8") if proposals.exists() else ""
        before_gaps = gaps_path.read_text(encoding="utf-8") if gaps_path.exists() else ""
        before_adopted = adopted.read_text(encoding="utf-8") if adopted.exists() else ""

        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            with contextlib.redirect_stdout(io.StringIO()):
                report = run_rc_session(
                    store, launch_voice=False, analyze_only=True, rc_session_id="rc_nomut"
                )
            self.assertFalse(report.mutated)

        after_learned = sorted(p.name for p in learned.glob("*.py")) if learned.exists() else []
        self.assertEqual(before_learned, after_learned)
        if proposals.exists():
            self.assertEqual(before_props, proposals.read_text(encoding="utf-8"))
        if gaps_path.exists():
            self.assertEqual(before_gaps, gaps_path.read_text(encoding="utf-8"))
        if adopted.exists():
            self.assertEqual(before_adopted, adopted.read_text(encoding="utf-8"))

    def test_5_suspicious_classification_report(self):
        rows = [
            _runtime(
                domain="messaging",
                category=FailureCategory.TOOL_ERROR,
                tool="find_contact",
                error_code="AMBIGUOUS",
                source="clarify",
            ),
            _runtime(
                domain="app",
                category=FailureCategory.VAGUE_ANSWER,
                source="chat",
                intent="general_conversation",
                utterance_class="CASUAL_TALK",
                error_code="VACUOUS_REPLY",
            ),
        ]
        hits = find_suspicious_classifications(rows)
        reasons = " ".join(h.reason for h in hits)
        self.assertIn("clarification", reasons)
        self.assertIn("casual chat", reasons)

        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            before_ids = snapshot_runtime_ids(store)
            before_audit = audit_runtime_corpus([])
            for row in rows:
                store.append(row)
            report = finalize_rc_session(
                store,
                before_ids=before_ids,
                before_audit=before_audit,
                rc_session_id="rc_sus",
                started_at="t0",
            )
            self.assertGreaterEqual(len(report.suspicious), 2)
            self.assertIn("FLAGGED", report.render())

    def test_6_dry_run_integration(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            before_ids = snapshot_runtime_ids(store)
            before_audit = audit_runtime_corpus([])
            for _ in range(4):
                store.append(
                    _runtime(
                        domain="messaging",
                        category=FailureCategory.TOOL_ERROR,
                        tool="find_contact",
                        error_code="FAILURE",
                    )
                )
            with patch(
                "core.self_improve.rc_session.run_supervised_dry_run"
            ) as mocked:
                from core.self_improve.supervised_dry_run import run_supervised_dry_run as real

                # Call through finalize but assert the supervised helper is used.
                mocked.side_effect = real
                report = finalize_rc_session(
                    store,
                    before_ids=before_ids,
                    before_audit=before_audit,
                    rc_session_id="rc_dry",
                    started_at="t0",
                )
                mocked.assert_called()
                self.assertIsNotNone(report.dry_run)
                self.assertEqual(report.dry_run.evidence, "runtime")

    def test_7_privacy_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            before_ids = snapshot_runtime_ids(store)
            before_audit = audit_runtime_corpus([])
            store.append(
                _runtime(
                    domain="messaging",
                    category=FailureCategory.TOOL_ERROR,
                    tool="find_contact",
                    error_code="FAILURE",
                    repeat_count=2,
                )
            )
            report = finalize_rc_session(
                store,
                before_ids=before_ids,
                before_audit=before_audit,
                rc_session_id="rc_priv",
                started_at="t0",
            )
            text = report.render()
            blob = json.dumps(report.to_dict())
            for banned in (
                "password",
                "api_key",
                "SECRET",
                "napisz do",
                "transcript",
                "wav",
            ):
                self.assertNotIn(banned, text.lower())
                self.assertNotIn(banned, blob.lower())
            self.assertIn("find_contact", text)
            self.assertIn("FAILURE", text)

    def test_entries_after_ignores_other_rc_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            before = snapshot_runtime_ids(store)
            store.append(
                _runtime(
                    domain="media",
                    category=FailureCategory.SUCCESS,
                    tool="play_media",
                    rc_session_id="rc_other",
                )
            )
            store.append(
                _runtime(
                    domain="app",
                    category=FailureCategory.SUCCESS,
                    tool="open_app",
                    rc_session_id="rc_mine",
                )
            )
            # Untagged new row still counts (collector env may be unset).
            store.append(
                _runtime(
                    domain="web",
                    category=FailureCategory.SUCCESS,
                    tool="web_search",
                )
            )
            fresh = entries_after(store, before, rc_session_id="rc_mine")
            domains = {e.domain for e in fresh}
            self.assertIn("app", domains)
            self.assertIn("web", domains)
            self.assertNotIn("media", domains)

    def test_rc_env_constant(self):
        self.assertEqual(RC_SESSION_ENV, "ELIAN_RC_SESSION_ID")


if __name__ == "__main__":
    unittest.main()
