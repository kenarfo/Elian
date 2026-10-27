"""Phase 12 — canonical device context + smart-home percent follow-up."""

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
from core.agent_repair import build_deterministic_decision
from core.builtin_tools import _set_device_value, _turn_off_device, build_default_registry
from core.conversation_manager import ConversationManager
from core.device_context import (
    domain_locked_percent_action,
    followup_intent_slots,
    get_active_device,
)
from core.intent_family import IntentClassification, IntentFamily
from core.model_client import FakeModelClient, reset_model_client
from core.tool_result import ToolStatus
from core.tool_shortlist import shortlist_tools
from core.tools import reset_registry_for_tests
from memory.manager import MemoryManager


SAMPLE_LIGHT = {
    "entity_id": "light.vizzini",
    "state": "on",
    "attributes": {
        "friendly_name": "Inspire Vizzini D56 Vizzini",
        "supported_color_modes": ["rgb", "color_temp", "brightness"],
        "brightness": 128,
    },
}

ONOFF_ONLY = {
    "entity_id": "light.switchy",
    "state": "on",
    "attributes": {
        "friendly_name": "Przekaźnik",
        "supported_color_modes": ["onoff"],
    },
}

CLIMATE = {
    "entity_id": "climate.salon",
    "state": "heat",
    "attributes": {
        "friendly_name": "Klimatyzacja salon",
        "temperature": 22,
    },
}


def _load(states):
    return HomeAssistant.get_registry().load_states(states)


class CanonicalDeviceContextTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        ConversationContext.clear()
        _load([SAMPLE_LIGHT, CLIMATE])

    def test_fastpath_turn_off_resolved_target(self):
        with patch.object(HaActions, "_call_service", return_value=True):
            result = _turn_off_device({"target": "lampę", "domain": "light"})
        self.assertTrue(result.success)
        self.assertEqual(result.data.get("resolved_entity_id"), "light.vizzini")
        self.assertEqual(result.data.get("resolved_domain"), "light")
        self.assertIn("Vizzini", result.data.get("resolved_name") or "")

    def test_context_stores_canonical_not_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            with patch.object(HaActions, "_call_service", return_value=True):
                result = _turn_off_device({"target": "lampę", "domain": "light"})
            mem.note_tool_success(
                "turn_off_device",
                {"target": "lampę", "domain": "light"},
                result.to_observation(),
            )
            device = get_active_device(mem)
            self.assertIsNotNone(device)
            self.assertEqual(device.entity_id, "light.vizzini")
            self.assertEqual(device.domain, "light")
            self.assertNotEqual(device.entity_id.lower(), "lampe")
            self.assertNotEqual((device.name or "").lower(), "lampe")

    def test_pronoun_followup_brightness_40(self):
        climate = []
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            registry = build_default_registry()
            cm = ConversationManager(
                registry=registry,
                model=FakeModelClient(
                    [{"type": "final_answer", "message": "Okej — o czym chcesz pogadać?"}]
                ),
                memory_manager=mem,
            )
            cm.activate()

            def _svc(domain, service, entity_id, data=None):
                if domain == "climate":
                    climate.append(entity_id)
                return True

            with patch.object(HaActions, "_call_service", side_effect=_svc):
                cm.handle("Wyłącz lampę.")
                device = get_active_device(cm.mem)
                self.assertEqual(device.entity_id, "light.vizzini")
                r2 = cm.handle("Ustaw ją na 40%.")
            self.assertEqual(climate, [])
            last = cm.last_observations[-1]
            self.assertEqual(last.get("tool"), "set_device_value")
            self.assertEqual((last.get("data") or {}).get("resolved_entity_id"), "light.vizzini")
            self.assertEqual((last.get("data") or {}).get("action"), "light_brightness")
            self.assertEqual(int((last.get("data") or {}).get("value") or 0), 40)
            self.assertNotIn("okej — o czym", (r2.text or "").lower())

    def test_implicit_followup_brightness_55(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            registry = build_default_registry()
            cm = ConversationManager(
                registry=registry,
                model=FakeModelClient(
                    [{"type": "final_answer", "message": "Okej — o czym chcesz pogadać?"}]
                ),
                memory_manager=mem,
            )
            cm.activate()
            with patch.object(HaActions, "_call_service", return_value=True):
                cm.handle("Włącz lampę.")
                cm.handle("Ustaw na 55%.")
            last = cm.last_observations[-1]
            data = last.get("data") or {}
            self.assertEqual(data.get("resolved_entity_id"), "light.vizzini")
            self.assertEqual(data.get("action"), "light_brightness")
            self.assertEqual(int(data.get("value") or 0), 55)

    def test_percent_zero_climate(self):
        climate = []
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            registry = build_default_registry()
            cm = ConversationManager(
                registry=registry,
                model=FakeModelClient(
                    [
                        {
                            "type": "tool_call",
                            "tool": "set_device_value",
                            "arguments": {
                                "action": "set_temperature",
                                "target": "climate.salon",
                                "domain": "climate",
                                "value": 50,
                            },
                        }
                    ]
                ),
                memory_manager=mem,
            )
            cm.activate()

            def _svc(domain, service, entity_id, data=None):
                if domain == "climate":
                    climate.append(entity_id)
                return True

            with patch.object(HaActions, "_call_service", side_effect=_svc):
                cm.handle("Włącz lampę.")
                cm.handle("Ustaw na 50%.")
            self.assertEqual(climate, [])
            last = cm.last_observations[-1]
            self.assertNotEqual((last.get("data") or {}).get("domain"), "climate")
            self.assertNotIn("climate.", str((last.get("data") or {}).get("resolved_entity_id") or ""))

    def test_unsupported_brightness_no_climate(self):
        _load([ONOFF_ONLY, CLIMATE])
        climate = []
        with patch.object(HaActions, "_call_service", side_effect=lambda *a, **k: climate.append(a) or True):
            result = _set_device_value(
                {
                    "action": "light_brightness",
                    "target": "light.switchy",
                    "entity_id": "light.switchy",
                    "domain": "light",
                    "value": 40,
                }
            )
        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolStatus.UNSUPPORTED)
        self.assertEqual(climate, [])
        self.assertEqual(result.data.get("resolved_entity_id"), "light.switchy")

    def test_turn_off_keeps_active_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            with patch.object(HaActions, "_call_service", return_value=True):
                result = _turn_off_device(
                    {"target": "lampę", "domain": "light", "entity_id": "light.vizzini"}
                )
            mem.note_tool_success(
                "turn_off_device",
                {"target": "lampę"},
                result.to_observation(),
            )
            device = get_active_device(mem)
            self.assertEqual(device.entity_id, "light.vizzini")
            self.assertEqual(device.domain, "light")

    def test_alias_survives_as_alias_not_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            with patch.object(HaActions, "_call_service", return_value=True):
                result = _turn_off_device({"target": "lampę", "domain": "light"})
            mem.note_tool_success(
                "turn_off_device",
                {"target": "lampę"},
                result.to_observation(),
            )
            entity = mem.short.get_entity("device")
            self.assertEqual(entity.id, "light.vizzini")
            rewritten = mem.expand_utterance("Ustaw ją na 40%.")
            # Must not stringify to raw alias as execution identity.
            self.assertNotIn("lampe", rewritten.lower().replace("lampę", ""))
            self.assertIn("ją", rewritten.lower())

    def test_shortlist_smart_home_only(self):
        catalog = [
            {"name": "web_search", "available": True},
            {"name": "open_app", "available": True},
            {"name": "play_media", "available": True},
            {"name": "turn_on_device", "available": True},
            {"name": "turn_off_device", "available": True},
            {"name": "set_device_value", "available": True},
            {"name": "create_reminder", "available": True},
        ]
        info = IntentClassification(
            family=IntentFamily.SMART_HOME,
            is_complete=True,
            slots={
                "resolved_context": True,
                "entity_id": "light.vizzini",
                "domain": "light",
                "percent": 40,
                "action": "light_brightness",
            },
        )
        names = [
            item["name"]
            for item in shortlist_tools(
                catalog,
                goal="Ustaw ją na 40%.",
                classification=info,
                active_device={"entity_id": "light.vizzini", "domain": "light"},
            )
        ]
        self.assertEqual(
            set(names),
            {"turn_on_device", "turn_off_device", "set_device_value"},
        )
        self.assertNotIn("web_search", names)
        self.assertNotIn("play_media", names)

    def test_domain_lock_climate_percent(self):
        from core.device_context import ActiveDevice

        device = ActiveDevice(
            entity_id="climate.salon",
            domain="climate",
            name="Klimatyzacja",
            capabilities=["temperature"],
        )
        args, err = domain_locked_percent_action(device, 40)
        self.assertIsNone(args)
        self.assertIsNotNone(err)
        self.assertIn("temperatur", (err or "").lower())

    def test_chain_30_then_60(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            registry = build_default_registry()
            cm = ConversationManager(
                registry=registry,
                model=FakeModelClient(
                    [{"type": "final_answer", "message": "Okej?"}] * 5
                ),
                memory_manager=mem,
            )
            cm.activate()
            with patch.object(HaActions, "_call_service", return_value=True):
                cm.handle("Włącz lampę.")
                cm.handle("Ustaw ją na 30%.")
                cm.handle("Teraz na 60%.")
            for obs in cm.last_observations[-2:]:
                data = obs.get("data") or {}
                self.assertEqual(data.get("resolved_entity_id"), "light.vizzini")
                self.assertEqual(data.get("resolved_domain"), "light")
            self.assertEqual(
                int((cm.last_observations[-1].get("data") or {}).get("value") or 0),
                60,
            )

    def test_deterministic_brightness_slots(self):
        info = IntentClassification(
            family=IntentFamily.SMART_HOME,
            is_complete=True,
            slots={
                "resolved_context": True,
                "entity_id": "light.vizzini",
                "domain": "light",
                "action": "light_brightness",
                "value": 40,
                "percent": 40,
            },
        )
        decision = build_deterministic_decision(info)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.tool, "set_device_value")
        self.assertEqual(decision.arguments.get("domain"), "light")
        self.assertEqual(decision.arguments.get("action"), "light_brightness")
        self.assertEqual(int(decision.arguments.get("value") or 0), 40)


if __name__ == "__main__":
    unittest.main()
