"""Phase 2 planner / agent tests (offline FakeModelClient)."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent_types import parse_agent_decision
from core.conversation_manager import ConversationManager
from core.model_client import FakeModelClient, set_model_client, reset_model_client
from core.planner import Planner
from core.risk import RiskLevel
from core.tool_result import ToolResult, ToolStatus
from core.tools import Tool, ToolRegistry, reset_registry_for_tests


def _build_registry():
    registry = ToolRegistry(confirm_medium=True)

    def open_app(arguments):
        name = str(arguments.get("name") or "")
        if name.lower() == "missingapp":
            return ToolResult.fail(
                "Nie znaleziono aplikacji MissingApp.",
                tool_name="open_app",
                retryable=True,
                data={"app": name},
            )
        return ToolResult.ok(
            f"Uruchomiłem {name}.",
            tool_name="open_app",
            data={"app": name},
        )

    def volume_up(_arguments):
        return ToolResult.ok("Zwiększam głośność.", tool_name="volume_up")

    def open_website(arguments):
        target = arguments.get("target") or arguments.get("url") or "stronę"
        return ToolResult.ok(
            f"Otwieram {target}.",
            tool_name="open_website",
            data={"target": target},
        )

    def shutdown(_arguments):
        return ToolResult.ok("Wyłączam komputer.", tool_name="shutdown_pc")

    registry.register(Tool(
        name="open_app",
        description="Open an application",
        handler=open_app,
        risk_level=RiskLevel.SAFE,
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    ))
    registry.register(Tool(
        name="volume_up",
        description="Increase volume",
        handler=volume_up,
        risk_level=RiskLevel.SAFE,
        parameters={"type": "object", "properties": {}},
    ))
    registry.register(Tool(
        name="volume_down",
        description="Decrease volume",
        handler=lambda _arguments: ToolResult.ok(
            "Zmniejszam głośność.", tool_name="volume_down"
        ),
        risk_level=RiskLevel.SAFE,
        parameters={"type": "object", "properties": {}},
    ))
    registry.register(Tool(
        name="open_website",
        description="Open a website",
        handler=open_website,
        risk_level=RiskLevel.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "target": {"type": "string"},
            },
        },
    ))
    registry.register(Tool(
        name="shutdown_pc",
        description="Shut down the computer",
        handler=shutdown,
        risk_level=RiskLevel.DANGEROUS,
        confirmation_prompt="Czy na pewno mam wyłączyć komputer?",
        parameters={"type": "object", "properties": {}},
    ))
    return registry


class ParseDecisionTests(unittest.TestCase):

    def test_parses_tool_call_and_aliases(self):
        decision = parse_agent_decision(
            '{"type":"tool_call","tool":"open_app","arguments":{"app_name":"Spotify"}}'
        )
        self.assertTrue(decision.is_tool_call)
        self.assertEqual(decision.arguments.get("name"), "Spotify")


class PlannerCoreTests(unittest.TestCase):

    def setUp(self):
        self.registry = _build_registry()
        reset_registry_for_tests(self.registry)

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()

    def test_single_tool_success(self):
        model = FakeModelClient([
            {"type": "tool_call", "tool": "open_app", "arguments": {"name": "Spotify"}},
            {"type": "final_answer", "message": "Gotowe."},
        ])
        planner = Planner(self.registry, model=model, max_steps=6)
        result = planner.run("Otwórz Spotify")
        self.assertIn("Spotify", result.message)
        self.assertEqual(len(result.steps), 1)
        self.assertTrue(result.steps[0]["observation"]["success"])
        self.assertEqual(len(model.calls), 0)
        self.assertEqual(result.source, "deterministic")

    def test_multi_tool_open_then_volume(self):
        model = FakeModelClient([])
        planner = Planner(self.registry, model=model)
        result = planner.run("Otwórz Spotify i zwiększ głośność.")
        self.assertGreaterEqual(len(result.steps), 2)
        self.assertEqual(result.steps[0]["tool"], "open_app")
        self.assertEqual(result.steps[1]["tool"], "volume_up")
        self.assertTrue(all(step["observation"]["success"] for step in result.steps))
        self.assertEqual(result.source, "compound")

    def test_failed_tool_then_recovery(self):
        model = FakeModelClient([
            {"type": "tool_call", "tool": "open_app", "arguments": {"name": "MissingApp"}},
            {
                "type": "tool_call",
                "tool": "open_website",
                "arguments": {"target": "Spotify"},
            },
            {"type": "final_answer", "message": "Otworzyłem wersję webową."},
        ])
        planner = Planner(self.registry, model=model)
        result = planner.run("Otwórz MissingApp")
        self.assertFalse(result.steps[0]["observation"]["success"])
        self.assertTrue(result.steps[1]["observation"]["success"])
        self.assertIn("webową", result.message.lower())

    def test_unknown_tool_rejected(self):
        model = FakeModelClient([
            {"type": "tool_call", "tool": "hack_computer", "arguments": {}},
            {"type": "final_answer", "message": "Nie mogę tego zrobić."},
        ])
        planner = Planner(self.registry, model=model)
        result = planner.run("zhakuj komputer")
        self.assertEqual(result.steps[0]["observation"]["error"], "unknown_tool")
        self.assertFalse(result.steps[0]["observation"]["success"])
        self.assertEqual(result.source, "validation_failure")
        self.assertIn("narzędzia", result.message.lower())

    def test_invalid_arguments_rejected(self):
        model = FakeModelClient([
            {"type": "tool_call", "tool": "open_app", "arguments": {}},
            {"type": "final_answer", "message": "Podaj nazwę aplikacji."},
        ])
        planner = Planner(self.registry, model=model)
        result = planner.run("otwórz coś")
        self.assertEqual(result.steps[0]["observation"]["error"], "missing_argument")
        self.assertEqual(result.source, "validation_failure")
        self.assertEqual(len(result.steps), 1)

    def test_dangerous_needs_confirmation(self):
        model = FakeModelClient([
            {"type": "tool_call", "tool": "shutdown_pc", "arguments": {}},
        ])
        planner = Planner(self.registry, model=model)
        result = planner.run("wyłącz komputer")
        self.assertTrue(result.needs_confirmation)
        self.assertEqual(result.pending_tool["name"], "shutdown_pc")
        self.assertIn("pewno", result.message.lower())

    def test_clarification(self):
        model = FakeModelClient([
            {"type": "clarification", "question": "Którą grę masz na myśli?"},
        ])
        planner = Planner(self.registry, model=model)
        result = planner.run("odpal grę")
        self.assertTrue(result.needs_clarification)
        self.assertIn("grę", result.message.lower())

    def test_max_steps_stops(self):
        model = FakeModelClient([
            {"type": "tool_call", "tool": "volume_up", "arguments": {}},
            {"type": "tool_call", "tool": "volume_up", "arguments": {}},
            {"type": "tool_call", "tool": "volume_up", "arguments": {}},
            # force_final wrap-up call
            {"type": "final_answer", "message": "Zatrzymałem się po limicie kroków."},
        ])
        planner = Planner(self.registry, model=model, max_steps=2)
        result = planner.run("głośniej wiele razy")
        # Phase 10.3: identical successful tool is not re-executed.
        self.assertGreaterEqual(len(result.steps), 1)
        self.assertLessEqual(len(result.steps), 2)
        self.assertTrue(result.message)

    def test_invalid_json_retries_then_fallback(self):
        model = FakeModelClient([
            "not-json",
            "still bad {{{",
        ])
        planner = Planner(self.registry, model=model)
        result = planner.run("zrób coś")
        self.assertTrue(result.success)
        lowered = result.message.lower()
        self.assertTrue(
            "zrozumiałem" in lowered
            or "chcesz pogadać" in lowered
            or "co mam zrobić" in lowered
            or "okej" in lowered,
            result.message,
        )
        self.assertGreaterEqual(len(model.calls), 2)


class ConversationAgentIntegrationTests(unittest.TestCase):

    def setUp(self):
        self.registry = _build_registry()
        reset_registry_for_tests(self.registry)
        self.model = FakeModelClient([])
        self.manager = ConversationManager(
            registry=self.registry,
            memory=[],
            model=self.model,
        )
        self.manager.activate()

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()

    def test_clarification_preserves_context_on_follow_up(self):
        self.model.push(
            {"type": "clarification", "question": "Którą grę masz na myśli?"},
            {
                "type": "tool_call",
                "tool": "open_app",
                "arguments": {"name": "Battlefront"},
            },
            {"type": "final_answer", "message": "Gotowe."},
        )
        first = self.manager.handle("odpal grę")
        self.assertEqual(first.source, "clarify")
        self.assertIsNotNone(self.manager.pending_clarification)

        second = self.manager.handle("battlefront")
        self.assertEqual(second.source, "agent")
        # Tool may succeed without verify stubs — claim Gotowe only when verified;
        # honest success speech still names the app (not false failure).
        low = (second.text or "").casefold()
        self.assertTrue(
            second.text.strip() == "Gotowe."
            or "battlefront" in low
            or "uruchomi" in low
            or "otwier" in low,
            msg=second.text,
        )
        self.assertNotIn("nie udało", low)
        self.assertIsNone(self.manager.pending_clarification)

    def test_confirmation_then_yes(self):
        self.model.push(
            {"type": "tool_call", "tool": "shutdown_pc", "arguments": {}},
        )
        ask = self.manager.handle("wyłącz komputer")
        self.assertEqual(ask.source, "confirm")
        self.assertIsNotNone(self.manager.pending_tool)

        done = self.manager.handle("tak")
        self.assertEqual(done.source, "tool")
        self.assertIn("Wyłączam", done.text)

    def test_fast_path_does_not_call_llm(self):
        with patch(
            "core.conversation_manager.run_fast_path",
            return_value="Włączam światło.",
        ) as fast_mock:
            result = self.manager.handle("włącz światło")
        fast_mock.assert_called_once()
        self.assertEqual(result.source, "fast_path")
        self.assertEqual(len(self.model.calls), 0)


if __name__ == "__main__":
    unittest.main()
