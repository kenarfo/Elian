# ==========================
# ELIAN — self-improve must not start with product/mic runtime (Faza 2.1)
# ==========================
from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.multi_agent.orchestrator import ElianOrchestrator
from core.multi_agent.self_model import SelfModel
from core.multi_agent.memory_scopes import ScopedMemoryStore


class ProductRuntimeNoSelfImproveTests(unittest.TestCase):
    def test_runtime_source_forces_start_sd_false(self) -> None:
        from core import runtime as runtime_mod

        src = Path(runtime_mod.__file__).read_text(encoding="utf-8")
        self.assertIn("start_sd = False", src)
        self.assertIn("refused by product runtime", src)
        # Must not reinstate the old default-ON path.
        self.assertNotRegex(
            src,
            r"start_sd\s*=\s*True\s*\n\s*if\s+[\"']background_self_development",
        )

    def test_autonomous_disabled_is_respected_in_config(self) -> None:
        from Config import config

        auto = (config.get("self_improve") or {}).get("autonomous") or {}
        self.assertFalse(bool(auto.get("enabled")))
        ma = config.get("multi_agent") or {}
        self.assertFalse(bool(ma.get("background_self_development")))

    def test_explicit_orchestrator_self_dev_still_callable(self) -> None:
        """CLI / supervised path: start_self_development=True still works."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            orch = ElianOrchestrator(
                language="en-US",
                state_path=root / "o.json",
                max_agents_total=8,
            )
            orch.self_model = SelfModel(path=root / "sm.json")
            orch.memory_scopes = ScopedMemoryStore(path=root / "m.json")
            with patch.object(
                type(orch),
                "start_background_self_development",
                autospec=True,
            ) as start_bg:
                orch.start(start_self_development=True, self_dev_max_cycles=1)
                start_bg.assert_called_once()
            types = {a.agent_type for a in orch.registry.list_all()}
            self.assertIn("self_development", types)
            orch.stop()

    def test_no_self_dev_thread_name_after_product_start_false(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            orch = ElianOrchestrator(
                language="en-US",
                state_path=root / "o.json",
                max_agents_total=8,
            )
            orch.self_model = SelfModel(path=root / "sm.json")
            orch.memory_scopes = ScopedMemoryStore(path=root / "m.json")
            orch.start(start_self_development=False)
            names = [t.name for t in threading.enumerate()]
            self.assertFalse(
                any("self-dev" in n.lower() or "self_dev" in n.lower() for n in names)
            )
            types = {a.agent_type for a in orch.registry.list_all()}
            self.assertNotIn("self_development", types)
            orch.stop()

    def test_cli_module_still_importable(self) -> None:
        import core.self_improve.cli as cli

        self.assertTrue(hasattr(cli, "main") or callable(cli))


if __name__ == "__main__":
    unittest.main()
