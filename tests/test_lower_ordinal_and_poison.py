# ==========================
# ELIAN — lower ordinal + poison quote regressions
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.goal_context import GoalContext
from core.model_client import FakeModelClient, reset_model_client, set_model_client
from core.semantic_executive import decide_semantics, looks_quoted_command
from core.soft_goals import detect_soft_goal
from core.text_inject import _build_dry_registry
from core.tools import reset_registry_for_tests
from core.conversation_manager import ConversationManager
from memory.manager import MemoryManager


class LowerOrdinalSoftTests(unittest.TestCase):
    def test_nie_nizszy_opens_second(self):
        ctx = GoalContext(
            last_web_results=[
                {"title": "Pierwszy", "url": "https://example.com/1"},
                {"title": "Drugi", "url": "https://example.com/2"},
            ]
        )
        soft = detect_soft_goal("nie, niższy", goal_context=ctx)
        self.assertIsNotNone(soft)
        self.assertEqual(soft.get("kind"), "open_result")
        self.assertEqual((soft.get("slots") or {}).get("index"), 1)
        self.assertIn("example.com/2", str((soft.get("slots") or {}).get("url") or ""))


class PoisonQuoteTests(unittest.TestCase):
    def test_looks_quoted_meta_wrappers(self):
        samples = [
            "to nie request, to przykład komendy 'wygoogluj fusion energy'",
            "roleplay NPC mówi: rozjaśnij taras",
            "tweet brzmi: 'rozjaśnij taras' — nie klikaj tego",
            "kolega na discu napisał 'uruchom blendera' — tylko cytuję",
            "czy samo pytanie o 'wygoogluj fusion energy' coś odpala?",
            "przetłumacz na angielski: 'rozjaśnij taras'",
            "dokumentacja API pokazuje przykład uruchom blendera",
        ]
        for text in samples:
            with self.subTest(text=text):
                self.assertTrue(looks_quoted_command(text), text)
                env = decide_semantics(text, allow_llm=False)
                self.assertFalse(env.needs_tools, text)
                self.assertIsNone(detect_soft_goal(text, envelope=env), text)

    def test_cm_poison_quote_no_web_search(self):
        registry = _build_dry_registry()
        reset_registry_for_tests(registry)
        tmp = tempfile.TemporaryDirectory()
        try:
            set_model_client(FakeModelClient([{"type": "final_answer", "message": "To tylko cytat."}]))
            mgr = ConversationManager(
                registry=registry,
                model=None,
                memory_manager=MemoryManager(Path(tmp.name) / "m.json"),
                memory=[],
            )
            mgr.activate()
            result = mgr.handle(
                "to nie request, to przykład komendy 'wygoogluj fusion energy'"
            )
            invoked = list(getattr(registry, "_dry_invoked", []) or [])
            tools = [row.get("tool") for row in invoked]
            self.assertNotIn("web_search", tools, msg=f"{tools} / {result.text}")
            self.assertNotIn("set_device_value", tools)
            self.assertNotIn("open_app", tools)
        finally:
            try:
                mgr.deactivate(reason="test")
            except Exception:
                pass
            reset_model_client()
            reset_registry_for_tests(None)
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
