"""Phase 12.2 — pronoun-first device bind + fake-action claim guard."""

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
from core.action_claim import guard_action_claim
from core.builtin_tools import build_default_registry
from core.conversation_manager import ConversationManager
from core.device_context import get_active_device
from core.device_pronoun import resolve_pronoun_device_action
from core.intent_family import classify_intent_family
from core.model_client import FakeModelClient, reset_model_client
from core.response_builder import never_fake_success
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

CLIMATE = {
    "entity_id": "climate.salon",
    "state": "heat",
    "attributes": {"friendly_name": "Klimatyzacja salon", "temperature": 22},
}


def _load():
    return HomeAssistant.get_registry().load_states([SAMPLE_LIGHT, CLIMATE])


def _seed_light(mem: MemoryManager):
    mem.note_tool_success(
        "turn_on_device",
        {"target": "lampę", "domain": "light"},
        {
            "success": True,
            "status": "success",
            "tool": "turn_on_device",
            "message": "Włączam Inspire Vizzini D56 Vizzini.",
            "data": {
                "resolved_entity_id": "light.vizzini",
                "resolved_target": "light.vizzini",
                "resolved_domain": "light",
                "resolved_name": "Inspire Vizzini D56 Vizzini",
                "capabilities": ["brightness", "color", "on_off"],
                "alias": "lampę",
            },
        },
    )


def _cm(mem, model_msgs=None):
    registry = build_default_registry()
    cm = ConversationManager(
        registry=registry,
        model=FakeModelClient(
            model_msgs
            or [{"type": "final_answer", "message": "Przyciemniam Inspire Vizzini D56 Vizzini."}]
            * 4
        ),
        memory_manager=mem,
    )
    cm.activate()
    _seed_light(mem)
    return cm


class PronounDeviceTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        ConversationContext.clear()
        _load()

    def test_dim_pronoun_executes_negative_brightness(self):
        climate = []
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = _cm(mem)
            device = get_active_device(cm.mem)
            self.assertEqual(device.entity_id, "light.vizzini")

            def _svc(domain, service, entity_id, data=None):
                if domain == "climate":
                    climate.append(entity_id)
                return True

            with patch.object(HaActions, "_call_service", side_effect=_svc):
                result = cm.handle("Przyciemnij ją.")
            last = cm.last_observations[-1]
            data = last.get("data") or {}
            ctx = cm._agent_context(raw_text="Przyciemnij ją.", goal_text="Przyciemnij ją.")
            self.assertIn(ctx["intent_family"], ("SMART_HOME", "UNKNOWN", "SYSTEM"))
            # Pronoun bind already executed (handler=device_pronoun); family
            # re-classify may mark learned_routine incomplete — tool truth wins.
            self.assertEqual(last.get("tool"), "set_device_value")
            self.assertEqual(data.get("resolved_entity_id"), "light.vizzini")
            self.assertEqual(data.get("action"), "adjust_brightness")
            self.assertLess(int(data.get("value") or 0), 0)
            self.assertEqual(climate, [])
            self.assertNotIn("które światło", (result.text or "").lower())

    def test_brighten_pronoun_no_clarification(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = _cm(mem)
            with patch.object(HaActions, "_call_service", return_value=True):
                result = cm.handle("Rozjaśnij ją.")
            last = cm.last_observations[-1]
            data = last.get("data") or {}
            self.assertEqual(last.get("tool"), "set_device_value")
            self.assertEqual(data.get("resolved_entity_id"), "light.vizzini")
            self.assertEqual(data.get("action"), "adjust_brightness")
            self.assertGreater(int(data.get("value") or 0), 0)
            self.assertNotIn("które światło", (result.text or "").lower())
            self.assertNotEqual(result.source, "clarify")

    def test_brighten_uses_memory_when_ha_context_empty(self):
        """Target-slot ConversationContext can be empty while memory still has canonical device."""
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = _cm(mem)
            ConversationContext.clear()
            with patch.object(HaActions, "_call_service", return_value=True):
                result = cm.handle("Rozjaśnij ją.")
            last = cm.last_observations[-1]
            data = last.get("data") or {}
            self.assertEqual(last.get("tool"), "set_device_value")
            self.assertEqual(data.get("resolved_entity_id"), "light.vizzini")
            self.assertGreater(int(data.get("value") or 0), 0)
            self.assertNotIn("które światło", (result.text or "").lower())

    def test_off_on_percent_pronoun(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = _cm(mem)
            with patch.object(HaActions, "_call_service", return_value=True):
                cm.handle("Wyłącz ją.")
                self.assertEqual(cm.last_observations[-1].get("tool"), "turn_off_device")
                cm.handle("Włącz ją.")
                self.assertEqual(cm.last_observations[-1].get("tool"), "turn_on_device")
                cm.handle("Ustaw ją na 40%.")
            last = cm.last_observations[-1]
            data = last.get("data") or {}
            self.assertEqual(data.get("action"), "light_brightness")
            self.assertEqual(int(data.get("value") or 0), 40)
            self.assertEqual(data.get("resolved_entity_id"), "light.vizzini")

    def test_no_active_device_brighten_clarifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            registry = build_default_registry()
            cm = ConversationManager(
                registry=registry,
                model=FakeModelClient([]),
                memory_manager=mem,
            )
            cm.activate()
            with patch.object(HaActions, "_call_service", return_value=True):
                result = cm.handle("Rozjaśnij ją.")
            text = (result.text or "")
            self.assertTrue(
                result.source == "clarify"
                or "?" in text
                or "ktore" in text.lower().replace("ó", "o")
                or "swiat" in text.lower().replace("ł", "l").replace("ś", "s")
            )
            self.assertFalse(
                any(
                    o.get("success") and o.get("tool") == "set_device_value"
                    for o in cm.last_observations
                )
            )

    def test_shortlist_excludes_web_media(self):
        catalog = [
            {"name": n, "available": True}
            for n in (
                "web_search",
                "fetch_web_page",
                "open_app",
                "play_media",
                "create_reminder",
                "open_website",
                "turn_on_device",
                "turn_off_device",
                "set_device_value",
            )
        ]
        from core.intent_family import IntentClassification, IntentFamily

        info = IntentClassification(
            family=IntentFamily.SMART_HOME,
            is_complete=True,
            slots={
                "resolved_context": True,
                "entity_id": "light.vizzini",
                "domain": "light",
                "pronoun_resolved": True,
                "action": "adjust_brightness",
            },
        )
        names = [
            item["name"]
            for item in shortlist_tools(
                catalog,
                goal="Przyciemnij ją.",
                classification=info,
                active_device={"entity_id": "light.vizzini", "domain": "light"},
            )
        ]
        self.assertEqual(
            set(names),
            {"turn_on_device", "turn_off_device", "set_device_value"},
        )


class FakeActionGuardTests(unittest.TestCase):
    def test_przyciemniam_without_tool_blocked(self):
        spoken = never_fake_success("Przyciemniam Inspire Vizzini D56 Vizzini.", [])
        self.assertNotIn("przyciemniam", spoken.lower())
        guarded = guard_action_claim("Przyciemniam lampę.", [])
        self.assertTrue(guarded.blocked)

    def test_matching_current_turn_allows_claim(self):
        spoken = never_fake_success(
            "Przyciemniam lampę.",
            [
                {
                    "success": True,
                    "tool": "set_device_value",
                    "data": {
                        "action": "adjust_brightness",
                        "value": -15,
                        "resolved_entity_id": "light.vizzini",
                    },
                }
            ],
        )
        self.assertIn("przyciemniam", spoken.lower())

    def test_unrelated_tool_cannot_authorize_light_claim(self):
        spoken = never_fake_success(
            "Przyciemniam lampę.",
            [
                {
                    "success": True,
                    "tool": "open_app",
                    "message": "Uruchomiłem Spotify.",
                    "data": {"app": "Spotify"},
                }
            ],
        )
        self.assertNotIn("przyciemniam", spoken.lower())

    def test_previous_turn_not_used(self):
        # Guard only sees the list passed in — CM must pass current-turn obs.
        guarded = guard_action_claim("Przyciemniam lampę.", [])
        self.assertTrue(guarded.blocked)


class ResolverUnitTests(unittest.TestCase):
    def test_resolver_needs_active_device(self):
        res = resolve_pronoun_device_action("Rozjaśnij ją.", None)
        self.assertIsNotNone(res)
        self.assertTrue(res.pronoun_detected)
        self.assertFalse(res.complete)

    def test_classify_przyciemnij_not_unknown(self):
        info = classify_intent_family("Przyciemnij ją.")
        self.assertEqual(info.family.value, "SMART_HOME")


if __name__ == "__main__":
    unittest.main()
