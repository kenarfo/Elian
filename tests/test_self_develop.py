# ==========================
# ELIAN — Autonomous self-development tests
# ==========================
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.self_improve.self_development.checkpoint import CheckpointManager
from core.self_improve.self_development.discovery import (
    ImprovementDiscovery,
    make_synthetic_candidates,
    prioritize,
)
from core.self_improve.self_development.engine import SelfDevelopmentEngine
from core.self_improve.self_development.history import DevelopmentHistoryStore
from core.self_improve.self_development.state import DevelopmentStateStore
from core.self_improve.self_development.types import (
    CycleResult,
    ImprovementCandidate,
    ImprovementKind,
    Phase,
)


class SelfDevelopUnitTests(unittest.TestCase):
    def test_startup_state_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DevelopmentStateStore(Path(tmp) / "state.json")
            st = store.load()
            self.assertEqual(st["mode"], "stopped")
            self.assertFalse(st["running"])
            self.assertEqual(st["current_phase"], Phase.STOPPED.value)

    def test_candidate_discovery_synthetic(self):
        cands = make_synthetic_candidates(
            mode="pass",
            sandbox_rel="Data/self_improve/dev_sandbox/cycle_target.py",
        )
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].source, "synthetic")

    def test_prioritization_picks_highest_score(self):
        a = ImprovementCandidate(
            id="a",
            title="low",
            kind=ImprovementKind.UX.value,
            reason="x",
            score=10.0,
            fingerprint="a",
        )
        b = ImprovementCandidate(
            id="b",
            title="high",
            kind=ImprovementKind.BUG_FIX.value,
            reason="y",
            score=90.0,
            fingerprint="b",
        )
        pick = prioritize([a, b])
        self.assertIsNotNone(pick)
        self.assertEqual(pick.id, "b")

    def test_checkpoint_rollback_restores_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "core" / "sample.py"
            target.parent.mkdir(parents=True)
            target.write_text("ORIGINAL = 1\n", encoding="utf-8")
            mgr = CheckpointManager(root, checkpoint_root=root / "cps")
            cp = mgr.create("c1", tracked_files=["core/sample.py"])
            mgr.snapshot_before_write(cp, "core/sample.py")
            target.write_text("BROKEN = 1\n", encoding="utf-8")
            restored = mgr.rollback(cp)
            self.assertTrue(any("sample.py" in r for r in restored))
            self.assertEqual(target.read_text(encoding="utf-8"), "ORIGINAL = 1\n")

    def test_checkpoint_refuses_forbidden_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mgr = CheckpointManager(root)
            self.assertTrue(mgr.is_forbidden("Data/config.json"))
            self.assertTrue(mgr.is_forbidden("Data/self_improve/experience_log.json"))
            self.assertTrue(mgr.is_forbidden(".env"))

    def test_persistence_survives_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = DevelopmentStateStore(path)
            store.update(mode="self-develop", current_cycle=3, current_phase=Phase.PLAN.value)
            other = DevelopmentStateStore(path)
            st = other.load()
            self.assertEqual(st["current_cycle"], 3)
            self.assertEqual(st["current_phase"], Phase.PLAN.value)

    def test_stop_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DevelopmentStateStore(Path(tmp) / "state.json")
            store.request_stop()
            self.assertTrue(store.load()["stop_requested"])

    def test_history_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            hist = DevelopmentHistoryStore(Path(tmp) / "h.json", max_entries=10)
            from core.self_improve.self_development.types import CycleRecord

            hist.append(
                CycleRecord(
                    cycle_id="c1",
                    timestamp="t",
                    phase="LEARN",
                    candidate="x",
                    reason="ok",
                    files_changed=["a.py"],
                    tests_run=["t"],
                    result="KEPT",
                )
            )
            rows = hist.load()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["result"], "KEPT")

    def test_repeated_failure_quarantine_deprioritizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            disc = ImprovementDiscovery(root)
            cands = make_synthetic_candidates(mode="pass", sandbox_rel="x.py")
            out = disc.discover(
                include_runtime=False,
                include_repo=False,
                include_tests=False,
                synthetic=cands,
                quarantine={cands[0].fingerprint: {"failures": 3}},
            )
            self.assertTrue(out[0].quarantined)
            self.assertLess(out[0].score, 100.0)


class SelfDevelopCycleTests(unittest.TestCase):
    def _engine(self, root: Path, *, synthetic: str, max_repair: int = 2) -> SelfDevelopmentEngine:
        return SelfDevelopmentEngine(
            project_root=root,
            state_path=root / "Data" / "self_improve" / "self_develop_state.json",
            history_path=root / "Data" / "self_improve" / "development_history.json",
            max_repair_attempts=max_repair,
            test_timeout=60.0,
            max_cycles=1,
            synthetic_mode=synthetic,
            skip_agent_smoke=True,
            include_runtime=False,
            include_repo=False,
            sleep_between_cycles_s=0.0,
        )

    def test_successful_synthetic_cycle_keeps_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Minimal tree so relative sandbox path works
            (root / "Data" / "self_improve").mkdir(parents=True)
            (root / "main.py").write_text("# stub\n", encoding="utf-8")
            eng = self._engine(root, synthetic="pass")
            result = eng.run_one_cycle()
            self.assertEqual(result, CycleResult.KEPT)
            target = root / "Data" / "self_improve" / "dev_sandbox" / "cycle_target.py"
            self.assertTrue(target.is_file())
            self.assertIn("STATUS", target.read_text(encoding="utf-8"))
            st = eng.state.load()
            self.assertEqual(st["last_result"], "KEPT")
            self.assertGreaterEqual(st["statistics"]["successful_cycles"], 1)

    def test_failed_synthetic_cycle_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Data" / "self_improve").mkdir(parents=True)
            (root / "main.py").write_text("# stub\n", encoding="utf-8")
            # Pre-create clean file that should be restored / removed if new
            eng = self._engine(root, synthetic="fail", max_repair=1)
            result = eng.run_one_cycle()
            self.assertEqual(result, CycleResult.ROLLED_BACK)
            target = root / "Data" / "self_improve" / "dev_sandbox" / "cycle_target.py"
            # New broken file should be removed on rollback
            self.assertFalse(target.exists())
            st = eng.state.load()
            self.assertEqual(st["last_result"], "ROLLED_BACK")
            self.assertGreaterEqual(st["statistics"]["rollback_count"], 1)

    def test_repair_attempts_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Data" / "self_improve").mkdir(parents=True)
            (root / "main.py").write_text("# stub\n", encoding="utf-8")
            eng = self._engine(root, synthetic="fail", max_repair=3)
            with mock.patch.object(
                eng.implementer,
                "apply",
                side_effect=[
                    (["Data/self_improve/dev_sandbox/cycle_target.py"], "review fail"),
                    (["Data/self_improve/dev_sandbox/cycle_target.py"], "review fail"),
                    (["Data/self_improve/dev_sandbox/cycle_target.py"], "review fail"),
                ],
            ) as apply_mock:
                result = eng.run_one_cycle()
            self.assertEqual(result, CycleResult.ROLLED_BACK)
            self.assertEqual(apply_mock.call_count, 3)

    def test_stop_prevents_next_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Data" / "self_improve").mkdir(parents=True)
            (root / "main.py").write_text("# stub\n", encoding="utf-8")
            eng = SelfDevelopmentEngine(
                project_root=root,
                state_path=root / "Data" / "self_improve" / "self_develop_state.json",
                history_path=root / "Data" / "self_improve" / "development_history.json",
                max_cycles=5,
                synthetic_mode="pass",
                skip_agent_smoke=True,
                include_runtime=False,
                include_repo=False,
                sleep_between_cycles_s=0.0,
            )
            calls = {"n": 0}
            real = eng.run_one_cycle

            def wrapped():
                calls["n"] += 1
                result = real()
                eng.state.request_stop()
                return result

            eng.run_one_cycle = wrapped  # type: ignore[method-assign]
            code = eng.run()
            self.assertEqual(code, 0)
            st = eng.state.load()
            self.assertFalse(st["running"])
            self.assertEqual(calls["n"], 1)
            self.assertEqual(st["statistics"]["cycles_completed"], 1)

    def test_self_dev_cannot_disable_critical_gates_constant(self):
        from core.self_improve.self_development import evaluate as ev

        # Safety: critical suite list must remain non-empty and include self_improve
        self.assertTrue(ev.CRITICAL_SELF_IMPROVE)
        self.assertIn("tests.test_self_improve", ev.CRITICAL_SELF_IMPROVE)
        self.assertIn("tests.test_supervised_dry_run", ev.CRITICAL_SELF_IMPROVE)


class DryRunUnchangedTests(unittest.TestCase):
    def test_dry_run_command_still_registered(self):
        from core.self_improve.cli import COMMANDS, build_parser

        self.assertIn("dry-run", COMMANDS)
        self.assertIn("self-develop", COMMANDS)
        self.assertIn("self-develop-status", COMMANDS)
        self.assertIn("self-develop-stop", COMMANDS)
        parser = build_parser()
        args = parser.parse_args(["self-develop-status"])
        self.assertEqual(args.command, "self-develop-status")


if __name__ == "__main__":
    unittest.main()
