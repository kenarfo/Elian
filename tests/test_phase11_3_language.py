# ==========================
# ELIAN — Phase 11.3 language + meta ACK regressions
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.conversation_focus import update_focus
from core.conversation_manager import ConversationManager
from core.freshness import (
    analyze_freshness,
    is_meta_utterance,
    meta_ack_phrase,
    split_meta_compound,
)
from core.model_client import FakeModelClient
from core.planner import Planner
from core.response_language import (
    coerce_to_session_language,
    detect_utterance_language,
    is_language_only_request,
    parse_language_request,
)
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from core.web_grounding import rank_hits
from memory.manager import MemoryManager


def _web_registry():
    registry = ToolRegistry(confirm_medium=False)
    calls = []

    def web_search(args):
        calls.append(dict(args or {}))
        hits = [
            {
                "title": "The White House",
                "url": "https://www.whitehouse.gov/",
                "snippet": "President Donald J. Trump",
                "source": "whitehouse.gov",
            },
            {
                "title": "Joe Biden - Wikipedia",
                "url": "https://en.wikipedia.org/wiki/Joe_Biden",
                "snippet": "Joe Biden was the 46th president.",
                "source": "en.wikipedia.org",
            },
        ]
        ranked = rank_hits(hits, query=str((args or {}).get("query") or ""))
        return ToolResult.ok(
            "ok",
            tool_name="web_search",
            data={
                "query": (args or {}).get("query"),
                "results": hits,
                "ranked_results": [h.to_dict() for h in ranked],
                "result_count": 2,
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


class MetaAckDetectionTests(unittest.TestCase):
    def test_teraz_wiem_is_meta(self):
        self.assertTrue(is_meta_utterance("Teraz wiem."))
        self.assertEqual(split_meta_compound("Teraz wiem.")[0], "meta")
        fresh = analyze_freshness("Teraz wiem.")
        self.assertEqual(fresh.semantic_kind, "META")
        self.assertFalse(fresh.freshness_required)

    def test_no_okej_teraz_wiem_is_meta(self):
        self.assertTrue(is_meta_utterance("No, okej, teraz wiem."))
        self.assertEqual(split_meta_compound("No, okej, teraz wiem.")[0], "meta")

    def test_juz_rozumiem_is_meta(self):
        self.assertTrue(is_meta_utterance("Już rozumiem."))
        self.assertEqual(analyze_freshness("Już rozumiem.").semantic_kind, "META")

    def test_compound_meta_strips_to_freshness_question(self):
        kind, rest = split_meta_compound(
            "Teraz wiem, ale kto jest premierem Wielkiej Brytanii?"
        )
        self.assertEqual(kind, "compound")
        self.assertIn("premier", rest.lower())
        fresh = analyze_freshness(
            "Teraz wiem, ale kto jest premierem Wielkiej Brytanii?"
        )
        self.assertTrue(fresh.freshness_required)
        self.assertEqual(fresh.semantic_kind, "QUESTION")
        self.assertFalse(fresh.is_meta)

    def test_meta_ack_phrase_short(self):
        phrase = meta_ack_phrase()
        self.assertIn(phrase, ("Okej.", "Jasne.", "Super."))


class MetaAckRoutingTests(unittest.TestCase):
    def test_planner_meta_zero_tools_no_replay(self):
        registry = _web_registry()
        prev = "The current President of the United States is Donald Trump."
        result = Planner(
            registry,
            model=FakeModelClient(
                [{"type": "final_answer", "message": prev}]
            ),
        ).run(
            "No, okej, teraz wiem.",
            context={
                "last_assistant": prev,
                "conversation_focus": {
                    "current_topic": "prezydent USA",
                    "last_answer_summary": prev,
                },
                "response_language": "pl",
            },
        )
        self.assertEqual(result.source, "meta")
        self.assertEqual(registry._calls, [])
        self.assertNotIn("trump", (result.message or "").lower())
        self.assertNotIn("president", (result.message or "").lower())
        self.assertIn(result.message, ("Okej.", "Jasne.", "Super."))

    def test_cm_meta_no_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            mem.short.update_focus(
                user_text="Kto jest prezydentem USA?",
                assistant_text=(
                    "The current President of the United States is Donald Trump."
                ),
            )
            cm = ConversationManager(
                registry=ToolRegistry(confirm_medium=False),
                model=FakeModelClient(
                    [
                        {
                            "type": "final_answer",
                            "message": (
                                "The current President of the United States "
                                "is Donald Trump."
                            ),
                        }
                    ]
                ),
                memory_manager=mem,
            )
            cm.activate()
            cm.last_assistant = (
                "The current President of the United States is Donald Trump."
            )
            topic_before = str(mem.short.focus.get("current_topic") or "")
            result = cm.handle("No, okej, teraz wiem.")
            topic_after = str(mem.short.focus.get("current_topic") or "")
            self.assertEqual(result.source, "ack")
            self.assertNotIn("trump", (result.text or "").lower())
            self.assertNotIn("president", (result.text or "").lower())
            self.assertIn(result.text, ("Okej.", "Jasne.", "Super."))
            self.assertEqual(topic_before, topic_after)

    def test_compound_triggers_web(self):
        registry = _web_registry()
        result = Planner(
            registry,
            model=FakeModelClient(
                [
                    {
                        "type": "final_answer",
                        "message": (
                            "Obecnym premierem Wielkiej Brytanii jest Keir Starmer."
                        ),
                    }
                ]
            ),
        ).run(
            "Teraz wiem, ale kto jest premierem Wielkiej Brytanii?",
            context={"response_language": "pl", "session_language": "pl"},
        )
        self.assertGreaterEqual(len(registry._calls), 1)
        self.assertFalse(analyze_freshness(
            "Teraz wiem, ale kto jest premierem Wielkiej Brytanii?"
        ).is_meta)

    def test_meta_keeps_topic_in_focus(self):
        focus = update_focus(
            None,
            user_text="Kto jest prezydentem USA?",
            assistant_text="Obecnym prezydentem USA jest Donald Trump.",
        )
        topic = focus["current_topic"]
        focus2 = update_focus(
            focus,
            user_text="No, okej, teraz wiem.",
            assistant_text="Okej.",
        )
        self.assertEqual(focus2["current_topic"], topic)


class ResponseLanguageTests(unittest.TestCase):
    def test_detect_polish(self):
        self.assertEqual(detect_utterance_language("Kto jest prezydentem USA?"), "pl")

    def test_parse_language_requests(self):
        self.assertEqual(parse_language_request("Mów po polsku."), "pl")
        self.assertEqual(parse_language_request("Ale mów po polsku."), "pl")
        self.assertEqual(parse_language_request("Mów po angielsku."), "en")
        self.assertEqual(parse_language_request("Speak English."), "en")
        self.assertTrue(is_language_only_request("Ale mów po polsku."))

    def test_coerce_english_web_to_polish(self):
        hits = [
            {
                "title": "The White House",
                "url": "https://www.whitehouse.gov/",
                "snippet": "President Donald J. Trump",
                "source": "whitehouse.gov",
            }
        ]
        ranked = rank_hits(hits, query="current president of the United States")
        out = coerce_to_session_language(
            "The current President of the United States is Donald Trump.",
            "pl",
            ranked=ranked,
            query="Kto jest prezydentem USA?",
        )
        self.assertFalse(out.lower().startswith("the current"))
        self.assertIn("trump", out.lower())
        self.assertTrue(
            any(ch in out.lower() for ch in ("prezydent", "stanów", "stanow", "usa"))
        )

    def test_web_final_coerced_to_polish(self):
        registry = _web_registry()
        result = Planner(
            registry,
            model=FakeModelClient(
                [
                    {
                        "type": "final_answer",
                        "message": (
                            "The current President of the United States "
                            "is Donald Trump."
                        ),
                    }
                ]
            ),
        ).run(
            "Kto jest prezydentem USA?",
            context={"response_language": "pl", "session_language": "pl"},
        )
        self.assertGreaterEqual(len(registry._calls), 1)
        msg = (result.message or "").lower()
        self.assertNotIn("the current president", msg)
        self.assertIn("trump", msg)

    def test_session_language_pl_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = ConversationManager(
                registry=ToolRegistry(confirm_medium=False),
                model=FakeModelClient([]),
                memory_manager=mem,
            )
            cm.activate()
            result = cm.handle("Mów po polsku.")
            self.assertEqual(cm.session_language, "pl")
            self.assertIn("polsku", (result.text or "").lower())

    def test_session_language_en_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = ConversationManager(
                registry=ToolRegistry(confirm_medium=False),
                model=FakeModelClient([]),
                memory_manager=mem,
            )
            cm.activate()
            result = cm.handle("Mów po angielsku.")
            self.assertEqual(cm.session_language, "en")
            self.assertIn("english", (result.text or "").lower())

    def test_polish_then_president_web_polish(self):
        registry = _web_registry()
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = ConversationManager(
                registry=registry,
                model=FakeModelClient(
                    [
                        {
                            "type": "final_answer",
                            "message": (
                                "The current President of the United States "
                                "is Donald Trump."
                            ),
                        }
                    ]
                ),
                memory_manager=mem,
            )
            cm.activate()
            cm.handle("Mów po polsku.")
            self.assertEqual(cm.session_language, "pl")
            result = cm.handle("Kto jest prezydentem USA?")
            self.assertGreaterEqual(len(registry._calls), 1)
            self.assertNotIn(
                "the current president",
                (result.text or "").lower(),
            )
            self.assertIn("trump", (result.text or "").lower())

    def test_language_only_does_not_set_preference(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            registry = ToolRegistry(confirm_medium=False)
            calls = []

            def set_pref(args):
                calls.append(args)
                return ToolResult.ok("ok", tool_name="set_preference")

            registry.register(
                Tool(
                    name="set_preference",
                    description="pref",
                    handler=set_pref,
                    risk_level=RiskLevel.SAFE,
                    parameters={
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "value": {"type": "string"},
                        },
                        "required": ["key", "value"],
                    },
                )
            )
            cm = ConversationManager(
                registry=registry,
                model=FakeModelClient(
                    [
                        {
                            "type": "tool_call",
                            "tool": "set_preference",
                            "arguments": {"key": "lang", "value": "pl"},
                        }
                    ]
                ),
                memory_manager=mem,
            )
            cm.activate()
            cm.handle("Ale mów po polsku.")
            self.assertEqual(calls, [])
            self.assertEqual(cm.session_language, "pl")


if __name__ == "__main__":
    unittest.main()
