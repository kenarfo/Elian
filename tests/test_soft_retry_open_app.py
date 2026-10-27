# ==========================
# ELIAN — soft retry after open_app (BAD_REPAIR cluster)
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.conversation_manager import ConversationManager
from core.goal_context import GoalContext
from core.model_client import FakeModelClient, reset_model_client, set_model_client
from core.soft_goals import detect_soft_goal
from core.text_inject import _build_dry_registry
from core.tools import reset_registry_for_tests
from memory.manager import MemoryManager


class SoftRetryOpenAppTests(unittest.TestCase):
    def tearDown(self):
        reset_model_client()
        reset_registry_for_tests(None)

    def test_detect_sproboj_ponownie_replays_open_app(self):
        ctx = GoalContext(
            previous_tool="open_app",
            previous_result={"tool": "open_app", "data": {"name": "Obsidian"}},
            active_entities={"app": "Obsidian"},
        )
        env = SimpleNamespace(intent="retry_goal", utterance_class="RETRY", mode="ACT", needs_tools=True)
        soft = detect_soft_goal("spróbuj ponownie", goal_context=ctx, envelope=env)
        self.assertIsNotNone(soft)
        self.assertEqual(soft.get("kind"), "replay_last")
        self.assertEqual((soft.get("slots") or {}).get("tool"), "open_app")
        self.assertEqual((soft.get("slots") or {}).get("arguments", {}).get("name"), "Obsidian")

    def test_cm_open_then_retry_invokes_open_app_again(self):
        registry = _build_dry_registry()
        reset_registry_for_tests(registry)
        tmp = tempfile.TemporaryDirectory()
        try:
            set_model_client(
                FakeModelClient([{"type": "final_answer", "message": "Jasne."}] * 4)
            )
            mgr = ConversationManager(
                registry=registry,
                model=None,
                memory_manager=MemoryManager(Path(tmp.name) / "m.json"),
                memory=[],
            )
            mgr.activate()
            mgr.handle("odpal obsidian")
            before = len(getattr(registry, "_dry_invoked", []) or [])
            result = mgr.handle("spróbuj ponownie")
            after = list(getattr(registry, "_dry_invoked", []) or [])
            open_calls = [r for r in after if r.get("tool") == "open_app"]
            self.assertGreaterEqual(len(open_calls), 2, msg=f"{after} / {result.text}")
            self.assertNotIn("nic nie robię", (result.text or "").lower())
            self.assertGreater(len(after), before)
        finally:
            try:
                mgr.deactivate(reason="test")
            except Exception:
                pass
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
