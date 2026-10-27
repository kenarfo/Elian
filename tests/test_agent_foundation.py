"""Phase 1 agent foundation tests: tools, risk, conversation manager."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.conversation_manager import ConversationManager, should_end_conversation
from core.risk import RiskLevel
from core.tool_result import ToolResult, ToolStatus
from core.tools import Tool, ToolRegistry, reset_registry_for_tests


class ToolResultTests(unittest.TestCase):

    def test_success_result(self):
        result = ToolResult.ok("Uruchomiono Spotify", data={"app": "spotify"})
        self.assertTrue(result.success)
        self.assertEqual(result.status, ToolStatus.SUCCESS)
        self.assertEqual(result.data["app"], "spotify")
        observation = result.to_observation()
        self.assertTrue(observation["success"])

    def test_error_result(self):
        result = ToolResult.fail("Błąd", error="boom", retryable=True)
        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolStatus.RETRYABLE)
        self.assertTrue(result.retryable)


class ToolRegistryTests(unittest.TestCase):

    def setUp(self):
        self.registry = ToolRegistry(confirm_medium=True)
        reset_registry_for_tests(self.registry)

        def echo(arguments):
            return ToolResult.ok(
                f"echo:{arguments.get('value', '')}",
                data=dict(arguments),
                tool_name="echo",
            )

        self.registry.register(Tool(
            name="echo",
            description="Echo tool",
            handler=echo,
            risk_level=RiskLevel.SAFE,
            parameters={"type": "object", "properties": {"value": {"type": "string"}}},
        ))

    def tearDown(self):
        reset_registry_for_tests(None)

    def test_register_and_get(self):
        tool = self.registry.get("echo")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.description, "Echo tool")
        self.assertEqual(len(self.registry.list_tools()), 1)

    def test_execute_tool(self):
        result = self.registry.invoke("echo", {"value": "hi"})
        self.assertTrue(result.success)
        self.assertEqual(result.message, "echo:hi")
        self.assertEqual(self.registry.last_result.message, "echo:hi")

    def test_unknown_tool(self):
        result = self.registry.invoke("nope")
        self.assertFalse(result.success)
        self.assertIn("nope", result.message)

    def test_planner_catalog_shape(self):
        catalog = self.registry.descriptions_for_planner()
        self.assertEqual(catalog[0]["name"], "echo")
        self.assertIn("risk_level", catalog[0])


class RiskLevelTests(unittest.TestCase):

    def test_safe_executes_without_confirmation(self):
        registry = ToolRegistry(confirm_medium=True)
        registry.register(Tool(
            name="safe_tool",
            description="safe",
            risk_level=RiskLevel.SAFE,
            handler=lambda _a: ToolResult.ok("done", tool_name="safe_tool"),
        ))
        result = registry.invoke("safe_tool")
        self.assertTrue(result.success)
        self.assertEqual(result.message, "done")

    def test_dangerous_requires_confirmation(self):
        registry = ToolRegistry(confirm_medium=True)
        registry.register(Tool(
            name="shutdown_pc",
            description="dangerous",
            risk_level=RiskLevel.DANGEROUS,
            handler=lambda _a: ToolResult.ok("off", tool_name="shutdown_pc"),
            confirmation_prompt="Czy na pewno wyłączyć komputer?",
        ))
        blocked = registry.invoke("shutdown_pc")
        self.assertEqual(blocked.status, ToolStatus.NEEDS_CONFIRMATION)
        self.assertFalse(blocked.success)

        confirmed = registry.invoke("shutdown_pc", confirmed=True)
        self.assertTrue(confirmed.success)
        self.assertEqual(confirmed.message, "off")

    def test_medium_requires_confirmation_when_enabled(self):
        registry = ToolRegistry(confirm_medium=True)
        registry.register(Tool(
            name="close_app",
            description="medium",
            risk_level=RiskLevel.MEDIUM,
            handler=lambda _a: ToolResult.ok("closed", tool_name="close_app"),
        ))
        blocked = registry.invoke("close_app", {"name": "Discord"})
        self.assertEqual(blocked.status, ToolStatus.NEEDS_CONFIRMATION)


class ConversationManagerTests(unittest.TestCase):

    def setUp(self):
        self.registry = ToolRegistry(confirm_medium=True)
        reset_registry_for_tests(self.registry)
        self.manager = ConversationManager(registry=self.registry, memory=[])

    def tearDown(self):
        reset_registry_for_tests(None)

    def test_wait_phrase_keeps_session_active(self):
        self.manager.activate()
        result = self.manager.handle("daj mi chwilę")
        self.assertTrue(self.manager.active)
        self.assertTrue(result.should_speak)
        self.assertEqual(result.source, "wait")
        self.assertIn("czekam", result.text.lower())

    def test_end_session(self):
        self.manager.activate()
        result = self.manager.handle("koniec")
        self.assertFalse(self.manager.active)
        self.assertTrue(result.ended)
        self.assertFalse(result.should_speak)

    def test_cancel_pending_tool(self):
        self.manager.activate()
        self.manager.pending_tool = {
            "name": "close_app",
            "arguments": {"name": "Discord"},
        }
        result = self.manager.handle("anuluj")
        self.assertIsNone(self.manager.pending_tool)
        self.assertEqual(result.text, "Anulowałem.")

    def test_normal_utterance_uses_legacy_intent_when_no_fast_path(self):
        self.manager.activate()
        with patch(
            "core.conversation_manager.run_skills",
            return_value=None,
        ), patch(
            "core.conversation_manager.run_command",
            return_value=None,
        ), patch(
            "core.conversation_manager.run_agent",
            return_value=None,
        ), patch(
            "core.conversation_manager.run_intent",
            return_value="Uruchomiłem Discord.",
        ) as intent_mock, patch(
            "core.conversation_manager.ask_ai",
        ) as ask_mock:
            result = self.manager.handle("potrzebuję discorda")
        intent_mock.assert_called_once()
        ask_mock.assert_not_called()
        self.assertEqual(result.text, "Uruchomiłem Discord.")
        self.assertEqual(result.source, "intent")

    def test_end_phrase_helper_unchanged(self):
        self.assertTrue(should_end_conversation("koniec"))
        self.assertFalse(should_end_conversation("na koniec dnia zrób herbatę"))


class BuiltinToolAdapterTests(unittest.TestCase):

    def test_open_app_tool_adapts_launcher(self):
        from core.builtin_tools import build_default_registry

        registry = build_default_registry(confirm_medium=False)
        with patch(
            "core.builtin_tools.launch_application",
            return_value=(True, "Uruchomiłem Spotify."),
        ):
            result = registry.invoke("open_app", {"name": "Spotify"})
        self.assertTrue(result.success)
        self.assertIn("Spotify", result.message)

    def test_close_app_is_medium_risk(self):
        from core.builtin_tools import build_default_registry

        registry = build_default_registry(confirm_medium=True)
        tool = registry.get("close_app")
        self.assertEqual(tool.risk_level, RiskLevel.MEDIUM)
        blocked = registry.invoke("close_app", {"name": "Discord"})
        self.assertEqual(blocked.status, ToolStatus.NEEDS_CONFIRMATION)


if __name__ == "__main__":
    unittest.main()
