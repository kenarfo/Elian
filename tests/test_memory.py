"""Phase 3 memory / preferences / context tests (offline, temp storage)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.conversation_manager import ConversationManager
from core.device_context import get_active_device
from core.model_client import FakeModelClient
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from memory.manager import MemoryManager, reset_memory_manager
from memory.sensitive import looks_sensitive


class MemoryCoreTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "mem.json"
        self.mem = MemoryManager(path=self.path)
        reset_memory_manager(self.path)

    def tearDown(self):
        reset_memory_manager(None)
        self.tmp.cleanup()

    def test_preference_set_get(self):
        ok, _ = self.mem.set_preference("music.default_app", "Spotify")
        self.assertTrue(ok)
        self.assertEqual(self.mem.get_preference("music.default_app"), "Spotify")

    def test_preference_overwrite(self):
        self.mem.set_preference("music.default_app", "Spotify")
        self.mem.set_preference("music.default_app", "YouTube Music")
        self.assertEqual(self.mem.get_preference("music.default_app"), "YouTube Music")
        self.assertEqual(len(self.mem.preferences.all()), 1)

    def test_duplicate_fact(self):
        self.mem.remember_fact("lubię krótkie odpowiedzi")
        self.mem.remember_fact("lubię krótkie odpowiedzi")
        self.assertEqual(len(self.mem.long.all()), 1)

    def test_forget_fact(self):
        self.mem.remember_fact("moja ulubiona gra to Minecraft")
        removed = self.mem.forget("ulubiona gra")
        self.assertGreaterEqual(len(removed["facts"]), 1)
        self.assertEqual(self.mem.search_facts("ulubiona gra"), [])

    def test_unknown_fact_does_not_invent(self):
        facts = self.mem.search_facts("ulubiony kolor")
        self.assertEqual(facts, [])

    def test_persistence_across_instances(self):
        self.mem.set_preference("music.default_app", "Spotify")
        self.mem.remember_fact("moja ulubiona gra to Minecraft")
        again = MemoryManager(path=self.path)
        self.assertEqual(again.get_preference("music.default_app"), "Spotify")
        hits = again.search_facts("ulubiona gra")
        self.assertTrue(any("minecraft" in item["text"].lower() for item in hits))

    def test_sensitive_not_saved(self):
        self.assertTrue(looks_sensitive("zapamiętaj hasło do banku 1234"))
        ok, message = self.mem.remember_fact("moje hasło to sekret123")
        self.assertFalse(ok)
        self.assertIn("token", message.lower())
        self.assertEqual(self.mem.long.all(), [])

    def test_short_term_trim(self):
        short = self.mem.short
        short.max_turns = 4
        for index in range(10):
            short.add_turn("user", f"wiadomość {index}")
        self.assertEqual(len(short.turns), 4)
        self.assertEqual(short.turns[-1]["content"], "wiadomość 9")

    def test_context_entity_pronoun(self):
        self.mem.note_tool_success(
            "turn_on_device",
            {"target": "lampa w salonie", "domain": "light"},
            {
                "success": True,
                "status": "success",
                "message": "ok",
                "tool": "turn_on_device",
                "data": {
                    "resolved_entity_id": "light.salon",
                    "resolved_target": "light.salon",
                    "resolved_domain": "light",
                    "resolved_name": "Lampa w salonie",
                    "alias": "lampa w salonie",
                },
            },
        )
        device = self.mem.short.get_entity("device")
        self.assertEqual(device.id, "light.salon")
        rewritten = self.mem.expand_utterance("ustaw ją na 30%")
        # Pronoun kept; canonical id lives in short-term + ConversationContext.
        self.assertIn("ją", rewritten.lower())
        self.assertEqual(get_active_device(self.mem).entity_id, "light.salon")

    def test_fast_path_updates_active_app(self):
        registry = ToolRegistry(confirm_medium=False)

        def open_app(arguments):
            return ToolResult.ok(
                f"Uruchomiłem {arguments['name']}.",
                tool_name="open_app",
                data={"app": arguments["name"]},
            )

        registry.register(Tool(
            name="open_app",
            description="open",
            handler=open_app,
            risk_level=RiskLevel.SAFE,
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        ))
        from core.fast_path import run_fast_path

        with patch("core.fast_path.can_fast_path", return_value=True), patch(
            "core.fast_path.parse_commands",
            return_value=[
                type(
                    "C",
                    (),
                    {
                        "action": "open_app",
                        "target": "Spotify",
                        "application": "Spotify",
                        "domain": "",
                        "entity_id": "",
                        "extra": "",
                        "value": 0,
                        "query": "",
                        "clarification": "",
                    },
                )()
            ],
        ):
            run_fast_path("otwórz spotify", registry, memory=self.mem)

        entity = self.mem.short.get_entity("application")
        self.assertIsNotNone(entity)
        self.assertEqual(entity.name, "Spotify")

    def test_session_pronoun_close_app(self):
        registry = ToolRegistry(confirm_medium=False)
        closed = []

        def open_app(arguments):
            return ToolResult.ok("Uruchomiłem Spotify.", tool_name="open_app", data={"app": "Spotify"})

        def close_app(arguments):
            closed.append(arguments.get("name"))
            return ToolResult.ok(
                f"Zamknąłem {arguments.get('name')}.",
                tool_name="close_app",
            )

        registry.register(Tool(
            name="open_app",
            description="open",
            handler=open_app,
            risk_level=RiskLevel.SAFE,
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        ))
        registry.register(Tool(
            name="close_app",
            description="close",
            handler=close_app,
            risk_level=RiskLevel.SAFE,
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        ))
        reset_registry_for_tests(registry)
        manager = ConversationManager(
            registry=registry,
            memory_manager=self.mem,
            model=FakeModelClient([]),
        )
        manager.activate()

        with patch("core.conversation_manager.run_fast_path") as fast_mock:
            def fake_fast(text, registry, confirmed=False, memory=None, **kwargs):
                if "spotify" in text.lower() and "zamkn" not in text.lower():
                    result = registry.invoke("open_app", {"name": "Spotify"})
                    if memory:
                        memory.note_tool_success(
                            "open_app",
                            {"name": "Spotify"},
                            result.to_observation(),
                        )
                    return result.message
                if "spotify" in text.lower() and "zamkn" in text.lower():
                    result = registry.invoke("close_app", {"name": "Spotify"})
                    return result.message
                return None

            fast_mock.side_effect = fake_fast
            manager.handle("otwórz spotify")
            follow = manager.handle("zamknij je")

        self.assertIn("Spotify", follow.text)
        self.assertEqual(closed, ["Spotify"])
        reset_registry_for_tests(None)

    def test_preference_survives_restart_for_agent_context(self):
        self.mem.set_preference("music.default_app", "Spotify")
        restarted = MemoryManager(path=self.path)
        built = restarted.build_context("puść muzykę")
        self.assertEqual(built["preferences"].get("music.default_app"), "Spotify")

    def test_explicit_commands_via_manager(self):
        manager = ConversationManager(
            registry=ToolRegistry(confirm_medium=False),
            memory_manager=self.mem,
            model=FakeModelClient([{"type": "final_answer", "message": "ok"}]),
        )
        manager.activate()
        with patch("core.conversation_manager.run_agent", return_value=None), patch(
            "core.conversation_manager.run_fast_path",
            return_value=None,
        ), patch("core.conversation_manager.run_skills", return_value=None), patch(
            "core.conversation_manager.run_command",
            return_value=None,
        ), patch("core.conversation_manager.run_intent", return_value=None), patch(
            "core.conversation_manager.ask_ai",
            return_value=None,
        ):
            r1 = manager.handle("zawsze używaj Spotify do muzyki")
            self.assertEqual(r1.source, "memory")
            self.assertEqual(self.mem.get_preference("music.default_app"), "Spotify")

            r2 = manager.handle("zapamiętaj że moja ulubiona gra to Minecraft")
            self.assertEqual(r2.source, "memory")

            r3 = manager.handle("jaka jest moja ulubiona gra")
            self.assertIn("minecraft", r3.text.lower())

            r4 = manager.handle("zapomnij moją ulubioną grę")
            self.assertEqual(r4.source, "memory")
            self.assertEqual(self.mem.search_facts("ulubiona gra"), [])

    def test_agent_receives_preference_in_context(self):
        self.mem.set_preference("music.default_app", "Spotify")
        model = FakeModelClient([
            {
                "type": "tool_call",
                "tool": "open_app",
                "arguments": {"name": "Spotify"},
            },
            {"type": "final_answer", "message": "Gotowe."},
        ])
        registry = ToolRegistry(confirm_medium=False)
        registry.register(Tool(
            name="open_app",
            description="open",
            handler=lambda a: ToolResult.ok("ok", tool_name="open_app"),
            risk_level=RiskLevel.SAFE,
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        ))
        manager = ConversationManager(
            registry=registry,
            memory_manager=self.mem,
            model=model,
        )
        manager.activate()
        with patch("core.conversation_manager.run_fast_path", return_value=None):
            result = manager.handle("puść muzykę")
        self.assertEqual(result.source, "agent")
        # First model call payload includes preference.
        payload = model.calls[0]["messages"][-1]["content"]
        self.assertIn("Spotify", payload)


if __name__ == "__main__":
    unittest.main()
