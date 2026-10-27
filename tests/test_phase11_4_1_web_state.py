# ==========================
# ELIAN — Phase 11.4.1 web-state / stale ACK / raw error
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.conversation_manager import ConversationManager
from core.model_client import FakeModelClient
from core.planner import Planner
from core.response_finalize import (
    finalize_response,
    is_forbidden_web_final,
    map_provider_error,
)
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from core.web_grounding import rank_hits
from memory.manager import MemoryManager


def _ok_web_registry():
    registry = ToolRegistry(confirm_medium=False)
    calls = []

    def web_search(args):
        calls.append(dict(args or {}))
        hits = [
            {
                "title": "UK Government",
                "url": "https://www.gov.uk/",
                "snippet": "Prime Minister Keir Starmer",
                "source": "gov.uk",
            }
        ]
        ranked = rank_hits(hits, query=str((args or {}).get("query") or ""))
        return ToolResult.ok(
            "Znalazłem 5 wyników.",
            tool_name="web_search",
            data={
                "query": (args or {}).get("query"),
                "results": hits,
                "ranked_results": [h.to_dict() for h in ranked],
                "result_count": 5,
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


def _fail_web_registry():
    registry = ToolRegistry(confirm_medium=False)
    calls = []

    def web_search(args):
        calls.append(dict(args or {}))
        return ToolResult.fail(
            "No results found.",
            tool_name="web_search",
            error="No results found.",
            data={"query": (args or {}).get("query"), "provider_error": "No results found."},
            retryable=True,
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


class RawWebErrorTests(unittest.TestCase):
    def test_no_results_mapped_polish(self):
        mapped = map_provider_error("No results found.", lang="pl")
        self.assertIsNotNone(mapped)
        self.assertIn("nie udało", mapped.lower())
        spoken, _ = finalize_response(
            "No results found.", lang="pl", kind="tool_failure"
        )
        self.assertNotEqual(spoken.lower(), "no results found.")
        self.assertIn("wynik", spoken.lower())

    def test_planner_failure_not_raw_english(self):
        registry = _fail_web_registry()
        result = Planner(
            registry,
            model=FakeModelClient(
                [{"type": "final_answer", "message": "No results found."}]
            ),
        ).run(
            "Kto jest premierem Wielkiej Brytanii?",
            context={"response_language": "pl", "session_language": "pl"},
        )
        self.assertNotIn("no results found", (result.message or "").lower())
        self.assertFalse(
            (result.message or "").strip().lower().startswith("no results")
        )


class StaleAckGuardTests(unittest.TestCase):
    def test_forbidden_web_final_detects_okej(self):
        self.assertTrue(is_forbidden_web_final("Okej."))
        self.assertTrue(is_forbidden_web_final("Znalazłem 5 wyników."))

    def test_meta_then_web_not_okej(self):
        registry = _ok_web_registry()
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = ConversationManager(
                registry=registry,
                model=FakeModelClient(
                    [
                        {
                            "type": "final_answer",
                            "message": "Okej.",  # would be wrong after web
                        }
                    ]
                ),
                memory_manager=mem,
            )
            cm.activate()
            cm.session_language = "pl"
            meta = cm.handle("Okej, już teraz wiem.")
            self.assertEqual(meta.source, "ack")
            prev = meta.text
            result = cm.handle("Jeszcze raz, kto jest premierem Wielkiej Brytanii?")
            self.assertGreaterEqual(len(registry._calls), 1)
            self.assertNotEqual((result.text or "").strip().lower().rstrip("."), "okej")
            self.assertNotEqual(result.text, prev)
            self.assertIn("starmer", (result.text or "").lower())
            self.assertIn(
                getattr(result, "data", {}).get("response_origin")
                or cm._turn_response_origin,
                ("WEB_SYNTHESIS", "WEB_REPAIR", "WEB_EVIDENCE_FALLBACK"),
            )

    def test_pending_cleared_on_new_web(self):
        registry = _ok_web_registry()
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = ConversationManager(
                registry=registry,
                model=FakeModelClient(
                    [
                        {
                            "type": "final_answer",
                            "message": "Obecnym premierem jest Keir Starmer.",
                        }
                    ]
                ),
                memory_manager=mem,
            )
            cm.activate()
            cm.pending_clarification = {
                "question": "Okej.",
                "last_answer": "Okej.",
                "original_goal": "coś",
                "intent_family": "UNKNOWN",
            }
            result = cm.handle("Kto jest premierem Wielkiej Brytanii?")
            self.assertIsNone(cm.pending_clarification)
            self.assertNotEqual((result.text or "").strip(), "Okej.")


if __name__ == "__main__":
    unittest.main()
