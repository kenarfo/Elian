# ==========================
# ELIAN — summarize-web / wake-ack / text-inject regressions
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.action_loop import run_soft_action_loop
from core.conversation_manager import ConversationManager
from core.goal_context import GoalContext
from core.model_client import FakeModelClient, reset_model_client
from core.risk import RiskLevel
from core.soft_goals import detect_soft_goal
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from memory.manager import MemoryManager


def _web_registry():
    registry = ToolRegistry(confirm_medium=False)
    calls = []

    def web_search(args):
        calls.append(("web_search", dict(args or {})))
        return ToolResult.ok(
            "Znalazłem wyniki.",
            tool_name="web_search",
            data={
                "query": args.get("query"),
                "results": [
                    {
                        "title": "Piper TTS",
                        "url": "https://example.com/piper",
                        "snippet": "Piper is a fast local neural text to speech system.",
                        "source": "example.com",
                    },
                    {
                        "title": "Piper voice models",
                        "url": "https://example.com/piper2",
                        "snippet": "Offline voices for Piper.",
                        "source": "example.com",
                    },
                ],
                "result_count": 2,
                "provider": "dry",
                "success": True,
            },
        )

    registry.register(
        Tool(
            name="web_search",
            description="web",
            handler=web_search,
            risk_level=RiskLevel.SAFE,
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
    )
    registry._calls = calls  # type: ignore[attr-defined]
    return registry


class SummarizeWebSoftTests(unittest.TestCase):
    def test_detect_stresc_with_results(self):
        ctx = GoalContext(
            last_web_results=[
                {"title": "Piper", "url": "https://example.com", "snippet": "TTS"}
            ],
            last_web_query="piper",
            last_assistant="Piper to lokalny TTS.",
        )
        soft = detect_soft_goal("streść to krótko", goal_context=ctx)
        self.assertIsNotNone(soft)
        self.assertEqual(soft["kind"], "summarize_web")

    def test_detect_stresc_talk_locked(self):
        ctx = GoalContext(
            last_web_results=[{"title": "X", "url": "https://x", "snippet": "y"}],
            last_web_query="x",
        )
        env = SimpleNamespace(
            mode="ANSWER",
            needs_tools=False,
            intent="information_request",
            action_discussion=False,
        )
        soft = detect_soft_goal("streść to", goal_context=ctx, envelope=env)
        self.assertEqual(soft["kind"], "summarize_web")

    def test_missing_without_results(self):
        soft = detect_soft_goal("streść to krótko", goal_context=GoalContext())
        self.assertEqual(soft["kind"], "unclear")

    def test_action_loop_no_research(self):
        registry = _web_registry()
        ctx_obs = [
            {
                "tool": "web_search",
                "success": True,
                "message": "ok",
                "data": {
                    "query": "piper",
                    "results": [
                        {
                            "title": "Piper TTS",
                            "url": "https://example.com/piper",
                            "snippet": "Piper is local TTS.",
                            "source": "example.com",
                        }
                    ],
                    "result_count": 1,
                },
            }
        ]
        loop = run_soft_action_loop(
            "streść to krótko",
            registry=registry,
            last_observations=ctx_obs,
            last_assistant="Piper to szybki lokalny TTS.",
        )
        self.assertTrue(loop.handled)
        self.assertEqual(loop.user_goal.soft_kind, "summarize_web")
        self.assertTrue(loop.message)
        self.assertEqual(registry._calls, [])  # type: ignore[attr-defined]
        self.assertNotIn("nie mam", loop.message.lower())
        self.assertNotRegex(loop.message, r"(?i)znalaz[łl]em\s+wynik")
        self.assertTrue(
            "piper" in loop.message.lower() or "tts" in loop.message.lower() or "skrócie" in loop.message.lower()
        )


class WakeAckInSessionTests(unittest.TestCase):
    def test_bare_wake_acks(self):
        reset_registry_for_tests()
        reset_model_client()
        tmp = tempfile.TemporaryDirectory()
        try:
            mem = MemoryManager(Path(tmp.name) / "m.json")
            mgr = ConversationManager(
                registry=ToolRegistry(confirm_medium=False),
                model=FakeModelClient([]),
                memory_manager=mem,
                memory=[],
            )
            mgr.activate()
            result = mgr.handle("hej elian")
            self.assertEqual(result.source, "wake_ack")
            self.assertTrue(result.should_speak)
            ack = (result.text or "").lower()
            self.assertTrue("słucham" in ack or "listening" in ack)
        finally:
            tmp.cleanup()
            reset_model_client()
            reset_registry_for_tests(None)


class TextInjectCliSmoke(unittest.TestCase):
    def test_inject_module_runs(self):
        from core.text_inject import run_inject_text

        code = run_inject_text("Co tam?", live_tools=False, debug=False)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
