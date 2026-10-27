"""Phase 10.4 — clarification slot resolution + ACK morphology."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from HomeAssistant import HaEntity
from core.clarification_slots import (
    build_pending_clarification,
    resolve_light_target_answer,
)
from core.conversation_manager import ConversationManager, should_end_conversation
from core.intent_family import classify_intent_family, is_clarification_answer
from core.model_client import FakeModelClient, reset_model_client
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from memory.manager import MemoryManager


def _light(entity_id: str, name: str) -> HaEntity:
    domain, object_id = entity_id.split(".", 1)
    return HaEntity(
        entity_id=entity_id,
        domain=domain,
        object_id=object_id,
        friendly_name=name,
        aliases=(name,),
    )


def _registry(log):
    registry = ToolRegistry(confirm_medium=False)

    def open_app(args):
        log.append({"tool": "open_app", "arguments": dict(args)})
        return ToolResult.ok(f"Uruchomiłem {args.get('name')}.", tool_name="open_app")

    def play_media(args):
        log.append({"tool": "play_media", "arguments": dict(args)})
        return ToolResult.ok("Odtwarzam.", tool_name="play_media", data=args)

    def set_device_value(args):
        log.append({"tool": "set_device_value", "arguments": dict(args)})
        return ToolResult.ok("OK.", tool_name="set_device_value", data=args)

    for name, handler, props, required in (
        ("open_app", open_app, {"name": {"type": "string"}}, ["name"]),
        ("play_media", play_media, {"query": {"type": "string"}}, ["query"]),
        ("set_device_value", set_device_value, {
            "action": {"type": "string"},
            "target": {"type": "string"},
            "domain": {"type": "string"},
            "value": {"type": "integer"},
            "entity_id": {"type": "string"},
        }, []),
    ):
        registry.register(Tool(
            name=name,
            description=name,
            handler=handler,
            risk_level=RiskLevel.SAFE,
            parameters={"type": "object", "properties": props, "required": required},
        ))
    return registry


class AckMorphologyTests(unittest.TestCase):

    def test_dziekuje_is_ack(self):
        for text in ("Dziękuję.", "Dzięki wielkie.", "Ok, dzięki.", "thx", "Thanks!"):
            info = classify_intent_family(text)
            self.assertEqual(info.family.value, "CONVERSATION_CONTROL", text)
            self.assertTrue(info.slots.get("acknowledgement"), text)

    def test_dziekuje_open_discord_is_app(self):
        info = classify_intent_family("Dziękuję, otwórz Discord.")
        self.assertEqual(info.family.value, "APP")
        self.assertEqual(info.slots.get("app_name"), "Discord")

    def test_dzieki_play_media(self):
        info = classify_intent_family("Dzięki, puść Michaela Jacksona.")
        self.assertEqual(info.family.value, "MEDIA")
        self.assertIn("michael", str(info.slots.get("media_query") or "").lower())

    def test_dziekuje_not_end_phrase(self):
        self.assertFalse(should_end_conversation("dziękuję"))
        self.assertFalse(should_end_conversation("dziękuję."))


class ClarificationSlotTests(unittest.TestCase):

    def setUp(self):
        reset_model_client()
        self.tmp = tempfile.TemporaryDirectory()
        self.log = []
        self.registry = _registry(self.log)
        reset_registry_for_tests(self.registry)
        self.mem = MemoryManager(Path(self.tmp.name) / "mem.json")
        self.model = FakeModelClient([
            {"type": "clarification", "question": "Czy chcesz otworzyć aplikację?"},
            {"type": "tool_call", "tool": "play_media", "arguments": {}},
            {"type": "tool_call", "tool": "open_app", "arguments": {}},
        ])
        self.manager = ConversationManager(
            registry=self.registry,
            model=self.model,
            memory_manager=self.mem,
        )
        self.manager.activate()

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()
        self.tmp.cleanup()

    def test_ciemno_clarification_missing_target(self):
        lights = [
            _light("light.pokoj", "Lampa w pokoju"),
            _light("light.salon", "Lampa w salonie"),
        ]
        with patch(
            "core.device_catalog._light_entities_from_registry",
            return_value=lights,
        ):
            with patch("core.device_catalog.default_fallback_entity", return_value=""):
                result = self.manager.handle("Trochę tu ciemno.")
        self.assertTrue(result.should_speak)
        self.assertIn("świat", (result.text or "").lower())
        pending = self.manager.pending_clarification or {}
        self.assertEqual(pending.get("intent_family"), "SMART_HOME")
        self.assertIn("target", pending.get("missing_slots") or [])
        self.assertEqual(pending.get("question_type"), "light_target")

    def test_lampe_fills_or_narrows_without_identical_loop(self):
        lights = [
            _light("light.pokoj", "Lampa w pokoju"),
            _light("light.salon", "Lampa w salonie"),
        ]
        with patch(
            "core.device_catalog._light_entities_from_registry",
            return_value=lights,
        ):
            with patch("core.device_catalog.default_fallback_entity", return_value=""):
                self.manager.handle("Trochę tu ciemno.")
        first_q = (self.manager.pending_clarification or {}).get("question") or ""
        single = [_light("light.pokoj", "Lampa w pokoju")]
        with patch(
            "core.clarification_slots.resolve_device",
        ) as mock_resolve:
            from DeviceResolver import DeviceMatch

            mock_resolve.return_value = DeviceMatch(
                status="exact",
                entity=single[0],
                candidates=(single[0],),
                domain="light",
            )
            result = self.manager.handle("Lampę.")
        self.assertNotEqual(result.source, "clarify_limit")
        if result.source == "clarify":
            self.assertNotEqual((result.text or "").strip().lower(), first_q.strip().lower())
        else:
            # Resolved → tool or agent success
            self.assertTrue(
                any(item.get("tool") == "set_device_value" for item in self.log)
                or result.source in ("agent", "deterministic", "clarify")
            )
            if any(item.get("tool") == "set_device_value" for item in self.log):
                target = self.log[-1]["arguments"].get("target")
                self.assertEqual(target, "light.pokoj")

    def test_lampe_w_pokoju_resolves_entity(self):
        pending = build_pending_clarification(
            goal="Trochę tu ciemno.",
            question="Które światło mam rozjaśnić?",
            intent_family="SMART_HOME",
            action="brightness_increase",
            question_type="light_target",
            missing_slots=["target"],
        )
        lights = [
            _light("light.pokoj", "Lampa w pokoju"),
            _light("light.salon", "Lampa w salonie"),
        ]
        result = resolve_light_target_answer(
            pending,
            "lampę w moim pokoju",
            entities=lights,
        )
        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.target, "light.pokoj")
        self.assertTrue(result.progress)

    def test_multiple_lights_generic_lampe_options(self):
        pending = build_pending_clarification(
            goal="Trochę tu ciemno.",
            question="Które światło mam rozjaśnić?",
            intent_family="SMART_HOME",
            question_type="light_target",
            missing_slots=["target"],
        )
        lights = [
            _light("light.pokoj", "Lampa w pokoju"),
            _light("light.salon", "Lampa w salonie"),
        ]
        result = resolve_light_target_answer(pending, "Lampę.", entities=lights)
        self.assertEqual(result.status, "ambiguous")
        self.assertTrue(result.progress)
        q = (result.question or "").lower()
        self.assertNotEqual(q, "które światło mam rozjaśnić?")
        self.assertTrue("pokoj" in q or "salon" in q or "czy" in q)

    def test_cm_lampe_no_identical_question(self):
        lights = [
            _light("light.pokoj", "Lampa w pokoju"),
            _light("light.salon", "Lampa w salonie"),
        ]
        with patch(
            "core.device_catalog._light_entities_from_registry",
            return_value=lights,
        ):
            with patch("core.device_catalog.default_fallback_entity", return_value=""):
                self.manager.handle("Trochę tu ciemno.")
        first_q = ((self.manager.pending_clarification or {}).get("question") or "").strip().lower()
        with patch("core.clarification_slots.resolve_device") as mock_resolve:
            from DeviceResolver import DeviceMatch

            mock_resolve.return_value = DeviceMatch(
                status="ambiguous",
                candidates=tuple(lights),
                domain="light",
                message="Masz na myśli Lampa w pokoju czy Lampa w salonie?",
            )
            result = self.manager.handle("Lampę.")
        self.assertEqual(result.source, "clarify")
        second_q = (result.text or "").strip().lower()
        self.assertNotEqual(second_q, first_q)
        self.assertTrue("pokoj" in second_q or "salon" in second_q or "czy" in second_q)

    def test_dziekuje_ack_zero_tools(self):
        result = self.manager.handle("Dziękuję.")
        self.assertEqual(result.source, "ack")
        self.assertEqual(len(self.log), 0)

    def test_dzieki_wielkie_ack(self):
        result = self.manager.handle("Dzięki wielkie.")
        self.assertEqual(result.source, "ack")
        self.assertEqual(len(self.log), 0)

    def test_ok_dzieki_ack(self):
        result = self.manager.handle("Ok, dzięki.")
        self.assertEqual(result.source, "ack")
        self.assertEqual(len(self.log), 0)

    def test_dziekuje_open_discord_tool(self):
        result = self.manager.handle("Dziękuję, otwórz Discord.")
        self.assertTrue(any(item.get("tool") == "open_app" for item in self.log))
        self.assertEqual(self.log[-1]["arguments"].get("name"), "Discord")
        self.assertNotEqual(result.source, "ack")

    def test_dzieki_play_michael(self):
        result = self.manager.handle("Dzięki, puść Michaela Jacksona.")
        self.assertTrue(any(item.get("tool") == "play_media" for item in self.log))
        self.assertIn("michael", str(self.log[-1]["arguments"]).lower())

    def test_lampe_is_clarification_answer_for_light_q(self):
        self.assertTrue(
            is_clarification_answer(
                "Lampę.",
                pending_question="Które światło mam rozjaśnić?",
            )
        )


if __name__ == "__main__":
    unittest.main()
