# ==========================
# ELIAN — Phase 11 conversation + web regressions
# ==========================
from __future__ import annotations

import unittest
from unittest.mock import patch

from core.conversation_focus import enrich_goal_with_focus, is_followup_utterance, update_focus
from core.intent_family import IntentFamily, classify_intent_family
from core.planner import Planner
from core.response_builder import never_fake_web_claim
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tool_shortlist import looks_like_pure_talk, shortlist_tools
from core.tools import Tool, ToolRegistry
from core.web_fetch import is_safe_public_http_url
from core.model_client import FakeModelClient


class Phase11ConversationTests(unittest.TestCase):
    def test_casual_talk_is_not_app_or_preference(self):
        for text in (
            "Fajny masz głos.",
            "Nudzę się.",
            "Spotify jest fajne.",
        ):
            info = classify_intent_family(text)
            self.assertNotEqual(info.family, IntentFamily.APP)
            self.assertNotEqual(info.family, IntentFamily.PREFERENCE)
            self.assertTrue(looks_like_pure_talk(text, info) or info.family == IntentFamily.UNKNOWN)

    def test_knowledge_vs_web_hints(self):
        knowledge = classify_intent_family("Co to jest AI?")
        self.assertEqual(knowledge.family, IntentFamily.KNOWLEDGE)
        web = classify_intent_family("Kto jest teraz prezydentem USA?")
        self.assertEqual(web.family, IntentFamily.WEB)
        news = classify_intent_family("Co wydarzyło się dzisiaj w technologii?")
        self.assertEqual(news.family, IntentFamily.WEB)

    def test_hypothetical_shutdown_is_knowledge(self):
        info = classify_intent_family("Co by było gdybym wyłączył komputer?")
        self.assertEqual(info.family, IntentFamily.KNOWLEDGE)

    def test_shortlist_empty_for_pure_talk(self):
        catalog = [
            {"name": "open_app", "available": True},
            {"name": "web_search", "available": True},
            {"name": "set_preference", "available": True},
        ]
        info = classify_intent_family("Fajny masz głos.")
        short = shortlist_tools(
            catalog,
            goal="Fajny masz głos.",
            classification=info,
            talk_only=looks_like_pure_talk("Fajny masz głos.", info),
        )
        self.assertEqual(short, [])

    def test_never_fake_web_claim(self):
        text = "Sprawdziłem w internecie, że X."
        cleaned = never_fake_web_claim(text, [])
        self.assertNotIn("Sprawdziłem w internecie", cleaned)
        grounded = never_fake_web_claim(
            text,
            [
                {
                    "success": True,
                    "tool": "web_search",
                    "data": {"results": [{"title": "t"}], "result_count": 1},
                }
            ],
        )
        self.assertIn("Sprawdziłem", grounded)

    def test_followup_keeps_topic(self):
        focus = update_focus(
            None,
            user_text="Kto stworzył Minecrafta?",
            assistant_text="Markus Persson stworzył Minecrafta.",
        )
        self.assertTrue(is_followup_utterance("A kiedy?"))
        enriched = enrich_goal_with_focus("A kiedy?", focus)
        self.assertIn("Minecraft", enriched)
        self.assertIn("Markus", enriched)

    def test_ssrf_blocks_localhost(self):
        ok, _ = is_safe_public_http_url("http://127.0.0.1/secret")
        self.assertFalse(ok)
        ok2, _ = is_safe_public_http_url("file:///etc/passwd")
        self.assertFalse(ok2)

    def test_planner_talk_zero_tools(self):
        registry = ToolRegistry(confirm_medium=False)
        calls = []

        def open_app(args):
            calls.append("open_app")
            return ToolResult.ok("ok", tool_name="open_app")

        registry.register(
            Tool(
                name="open_app",
                description="launch app",
                handler=open_app,
                risk_level=RiskLevel.SAFE,
                parameters={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            )
        )
        model = FakeModelClient(
            [{"type": "final_answer", "message": "Dzięki!"}]
        )
        result = Planner(registry, model=model).run(
            "Fajny masz głos.",
            context={"raw_text": "Fajny masz głos."},
        )
        self.assertTrue(result.success)
        self.assertEqual(calls, [])
        self.assertFalse(result.steps)
        self.assertNotIn("nie zrozumiałem", (result.message or "").lower())

    def test_web_search_tool_structured(self):
        from core.web_search_provider import WebSearchHit, WebSearchResult

        fake = WebSearchResult(
            success=True,
            query="test",
            provider="test",
            results=[
                WebSearchHit(
                    title="T",
                    url="https://example.com",
                    snippet="S",
                    source="example.com",
                )
            ],
            latency_ms=1,
        )
        with patch(
            "core.web_search_provider.search_structured",
            return_value=fake,
        ), patch(
            "core.web_search_provider.provider_available",
            return_value=True,
        ):
            from core.builtin_tools import _web_search_tool

            result = _web_search_tool({"query": "test"})
            self.assertTrue(result.success)
            self.assertEqual(result.data.get("result_count"), 1)


if __name__ == "__main__":
    unittest.main()
