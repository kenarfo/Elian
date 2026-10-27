"""Phase 12.1 — value-only device continuation, repeat guard, fake claims."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import ConversationContext
import HaActions
import HomeAssistant
from core.builtin_tools import build_default_registry
from core.compound_action import split_dismiss_and_action
from core.conversation_manager import ConversationManager
from core.device_context import (
    ActiveDevice,
    LastDeviceOperation,
    canonical_tool_signature,
    followup_intent_slots,
    get_last_device_operation,
)
from core.model_client import FakeModelClient, reset_model_client
from core.pl_numbers import extract_number, parse_pl_number_words
from core.planner import Planner
from core.response_builder import never_fake_success
from core.tools import reset_registry_for_tests
from memory.manager import MemoryManager


SAMPLE_LIGHT = {
    "entity_id": "light.vizzini",
    "state": "on",
    "attributes": {
        "friendly_name": "Inspire Vizzini D56 Vizzini",
        "supported_color_modes": ["rgb", "color_temp", "brightness"],
        "brightness": 76,
    },
}


def _load():
    return HomeAssistant.get_registry().load_states([SAMPLE_LIGHT])


class PlNumberTests(unittest.TestCase):
    def test_number_words(self):
        self.assertEqual(parse_pl_number_words("sześćdziesiąt"), 60)
        self.assertEqual(extract_number("pięćdziesiąt"), 50)
        self.assertEqual(extract_number("czterdzieści"), 40)
        self.assertEqual(extract_number("trzydzieści"), 30)
        self.assertEqual(extract_number("Na sześćdziesiąt."), 60)
        self.assertEqual(extract_number("siedemdziesiąt pięć"), 75)


class ValueInheritanceTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        ConversationContext.clear()
        _load()

    def test_na_szescdziesiat_inherits_brightness(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            registry = build_default_registry()
            cm = ConversationManager(
                registry=registry,
                model=FakeModelClient(
                    [{"type": "final_answer", "message": "Okej — o czym chcesz pogadać?"}]
                    * 5
                ),
                memory_manager=mem,
            )
            cm.activate()
            off_calls = []

            def _svc(domain, service, entity_id, data=None):
                if service == "turn_off":
                    off_calls.append(entity_id)
                return True

            with patch.object(HaActions, "_call_service", side_effect=_svc):
                cm.handle("Ustaw lampę na 30%.")
                op = get_last_device_operation(cm.mem)
                self.assertIsNotNone(op)
                self.assertEqual(op.action, "light_brightness")
                self.assertEqual(int(op.value or 0), 30)
                r2 = cm.handle("Na sześćdziesiąt.")
            self.assertEqual(off_calls, [])
            last = cm.last_observations[-1]
            self.assertEqual(last.get("tool"), "set_device_value")
            data = last.get("data") or {}
            self.assertEqual(data.get("action"), "light_brightness")
            self.assertEqual(int(data.get("value") or 0), 60)
            self.assertNotIn("wyłącz", (r2.text or "").lower())

    def test_bare_60_inherits(self):
        device = ActiveDevice(
            entity_id="light.vizzini",
            domain="light",
            name="Vizzini",
            capabilities=["brightness"],
        )
        op = LastDeviceOperation(
            entity_id="light.vizzini",
            domain="light",
            action="light_brightness",
            value=30,
            unit="percent",
        )
        slots = followup_intent_slots("60.", device, op)
        self.assertEqual(slots.get("action"), "light_brightness")
        self.assertEqual(int(slots.get("value") or 0), 60)


class RepeatGuardTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        ConversationContext.clear()
        _load()

    def test_canonical_signature_aliases(self):
        a = canonical_tool_signature(
            "turn_off_device",
            {"target": "lampe"},
            {"data": {"resolved_entity_id": "light.vizzini"}},
        )
        b = canonical_tool_signature(
            "turn_off_device",
            {"target": "Inspire Vizzini D56 Vizzini", "entity_id": "light.vizzini"},
        )
        c = canonical_tool_signature(
            "turn_off_device",
            {"entity_id": "light.vizzini", "target": "lampe"},
        )
        self.assertEqual(a, "turn_off_device|entity=light.vizzini")
        self.assertEqual(a, b)
        self.assertEqual(b, c)

    def test_identical_turn_off_once(self):
        calls = []

        def turn_off(args):
            calls.append(dict(args))
            from core.tool_result import ToolResult

            return ToolResult.ok(
                "Wyłączam Inspire Vizzini D56 Vizzini.",
                tool_name="turn_off_device",
                data={
                    "resolved_entity_id": "light.vizzini",
                    "resolved_domain": "light",
                    "resolved_name": "Inspire Vizzini D56 Vizzini",
                    "alias": args.get("target"),
                },
            )

        registry = build_default_registry()
        registry._tools["turn_off_device"].handler = turn_off  # type: ignore[attr-defined]

        model = FakeModelClient(
            [
                {
                    "type": "tool_call",
                    "tool": "turn_off_device",
                    "arguments": {"target": "lampe", "domain": "light"},
                },
                {
                    "type": "tool_call",
                    "tool": "turn_off_device",
                    "arguments": {
                        "target": "Inspire Vizzini D56 Vizzini",
                        "entity_id": "light.vizzini",
                    },
                },
                {
                    "type": "tool_call",
                    "tool": "turn_off_device",
                    "arguments": {"target": "lampe"},
                },
                {"type": "final_answer", "message": "Gotowe."},
            ]
        )
        planner = Planner(registry=registry, model=model, max_steps=6)
        result = planner.run(
            "wyłącz lampę",
            context={
                "active_device": {
                    "entity_id": "light.vizzini",
                    "domain": "light",
                    "name": "Inspire Vizzini D56 Vizzini",
                },
                "raw_text": "wyłącz lampę",
                "intent_classification": None,
            },
        )
        self.assertEqual(len(calls), 1)
        self.assertIn(result.source, ("repeat_blocked", "planner", "deterministic"))


class FakeClaimTests(unittest.TestCase):
    def test_wlaczam_without_tool_blocked(self):
        spoken = never_fake_success("Włączam lampę.", [])
        self.assertNotIn("włączam", spoken.lower())
        self.assertIn("nie wykonałem", spoken.lower())

    def test_wlaczam_with_success_allowed(self):
        spoken = never_fake_success(
            "Włączam lampę.",
            [{"success": True, "tool": "turn_on_device", "message": "Włączam Vizzini."}],
        )
        self.assertIn("włączam", spoken.lower())


class CompoundDismissTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        ConversationContext.clear()
        _load()

    def test_split_niewazne_wlacz(self):
        prefix, rest = split_dismiss_and_action("Nieważne. Włącz lampę.")
        self.assertTrue(prefix)
        self.assertIn("łącz", rest.lower())

    def test_niewazne_wlacz_calls_turn_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            registry = build_default_registry()
            cm = ConversationManager(
                registry=registry,
                model=FakeModelClient(
                    [{"type": "final_answer", "message": "Włączam lampę."}]
                ),
                memory_manager=mem,
            )
            cm.activate()
            on_calls = []

            def _svc(domain, service, entity_id, data=None):
                if service == "turn_on":
                    on_calls.append(entity_id)
                return True

            with patch.object(HaActions, "_call_service", side_effect=_svc):
                light = HomeAssistant.get_registry().get("light.vizzini")
                if light is not None:
                    light.state = "off"
                result = cm.handle("Nieważne. Włącz lampę.")
            self.assertEqual(on_calls, ["light.vizzini"])
            self.assertTrue(any(o.get("tool") == "turn_on_device" for o in cm.last_observations))
            self.assertNotEqual((result.text or "").strip(), "Włączam lampę.")


if __name__ == "__main__":
    unittest.main()
