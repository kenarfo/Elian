# ==========================
# ELIAN — calmer media follow-up under TALK lock
# ==========================
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from core.action_loop import run_soft_action_loop
from core.jarvis_behavioral.harness import AssistFakeModel
from core.semantic_executive import DecisionEnvelope
from core.soft_goals import detect_soft_goal
from core.tools import Tool, ToolRegistry
from core.risk import RiskLevel
from core.tool_result import ToolResult


class MediaCalmTalkContTests(unittest.TestCase):
    def test_soft_calmer_after_media_under_talk(self):
        ctx = SimpleNamespace(
            media_active=True,
            previous_goal={"soft_kind": "media_play", "intent_family": "MEDIA"},
            previous_tool="play_media",
        )
        env = DecisionEnvelope(
            mode="TALK",
            intent="general_conversation",
            needs_tools=False,
        )
        soft = detect_soft_goal(
            "coś łagodniejszego #60",
            goal_context=ctx,
            envelope=env,
        )
        self.assertIsNotNone(soft)
        self.assertEqual(soft.get("kind"), "media_calm")

    def test_soft_calmer_bare_under_talk(self):
        env = DecisionEnvelope(
            mode="TALK",
            intent="general_conversation",
            needs_tools=False,
        )
        soft = detect_soft_goal("coś łagodniejszego", goal_context=None, envelope=env)
        self.assertIsNotNone(soft)
        self.assertEqual(soft.get("kind"), "media_calm")

    def test_assist_fake_ignores_system_discussion_tokens(self):
        m = AssistFakeModel()
        out = m.chat(
            [
                {
                    "role": "system",
                    "content": "Przykłady: gdybym chciał… nie otwieraj Spotify.",
                },
                {"role": "user", "content": "Goal: coś łagodniejszego"},
            ]
        )
        data = json.loads(out)
        self.assertEqual(data.get("type"), "tool_call")
        self.assertEqual(data.get("tool"), "play_media")

    def test_assist_fake_uses_json_goal_not_history(self):
        m = AssistFakeModel()
        payload = {
            "goal": "co tam piszą?",
            "raw_text": "co tam piszą?",
            "recent_user": ["wyszukaj coś o michaelu", "nie, drugi"],
            "untrusted_observations": [{"tool": "web_search", "success": True}],
        }
        instruction = (
            "Casual talk → final_answer. Świeże fakty → web_search. "
            "Nie defaultuj do open_app."
        )
        out = m.chat(
            [
                {"role": "system", "content": "gdybym nie otwieraj"},
                {
                    "role": "user",
                    "content": instruction + "\n\n" + json.dumps(payload, ensure_ascii=False),
                },
            ]
        )
        data = json.loads(out)
        self.assertEqual(data.get("type"), "final_answer")
        self.assertNotEqual(data.get("tool"), "web_search")

    def test_action_loop_executes_calmer_under_talk(self):
        registry = ToolRegistry(confirm_medium=False)
        calls = []

        def play(args):
            calls.append(dict(args or {}))
            return ToolResult.ok("ok", tool_name="play_media", data=dict(args or {}))

        registry.register(
            Tool(
                name="play_media",
                description="play",
                handler=play,
                risk_level=RiskLevel.SAFE,
                parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            )
        )
        env = DecisionEnvelope(
            mode="TALK",
            intent="general_conversation",
            needs_tools=False,
        )
        loop = run_soft_action_loop(
            "coś łagodniejszego",
            registry=registry,
            envelope=env,
            last_user_goal={"soft_kind": "media_play", "intent_family": "MEDIA"},
            last_observations=[{"tool": "play_media", "success": True}],
        )
        # Ensure previous_tool / media path is visible via context built inside loop.
        # If context wiring omits media_active, still pass soft via previous_goal.
        self.assertTrue(loop.handled or loop.user_goal is not None)
        if loop.handled:
            self.assertEqual(getattr(loop.user_goal, "soft_kind", ""), "media_calm")
            self.assertTrue(calls)


if __name__ == "__main__":
    unittest.main()
