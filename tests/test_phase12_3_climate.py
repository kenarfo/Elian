"""Phase 12.3 — GREE climate capability-aware control."""

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
from core.climate_control import plan_climate_utterance, validate_temperature
from core.climate_profile import (
    gree_pular_fixture_states,
    profile_from_ha_entity,
    related_from_states,
)
from core.conversation_manager import ConversationManager
from core.device_context import get_active_device
from core.intent_family import IntentClassification, IntentFamily
from core.model_client import FakeModelClient, reset_model_client
from core.tool_shortlist import shortlist_tools
from core.tools import reset_registry_for_tests
from memory.manager import MemoryManager


def _load_fixture(extra=None):
    states = gree_pular_fixture_states()
    if extra:
        states = list(states) + list(extra)
    return HomeAssistant.get_registry().load_states(states)


def _cm(mem):
    registry = build_default_registry()
    cm = ConversationManager(
        registry=registry,
        model=FakeModelClient(
            [{"type": "final_answer", "message": "Ustawiam 22 stopnie."}] * 8
        ),
        memory_manager=mem,
    )
    cm.activate()
    return cm


class ClimateControlTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        ConversationContext.clear()
        _load_fixture()

    def _handle(self, *utterances):
        climate = []
        lights = []

        def _svc(domain, service, entity_id, data=None):
            if domain == "climate":
                climate.append((service, entity_id, dict(data or {})))
            if domain == "light":
                lights.append(entity_id)
            return True

        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = _cm(mem)
            results = []
            with patch.object(HaActions, "_call_service", side_effect=_svc):
                for text in utterances:
                    results.append(cm.handle(text))
            return cm, results, climate, lights

    def test_absolute_temp_22(self):
        cm, results, climate, lights = self._handle(
            "Włącz klimatyzację.",
            "Ustaw ją na 22 stopnie.",
        )
        self.assertTrue(any(item[0] == "set_temperature" for item in climate))
        temp = next(item for item in climate if item[0] == "set_temperature")
        self.assertEqual(temp[1], "climate.gree_pular")
        self.assertEqual(float(temp[2].get("temperature")), 22)
        self.assertEqual(lights, [])
        device = get_active_device(cm.mem)
        self.assertEqual(device.entity_id, "climate.gree_pular")
        self.assertEqual(device.domain, "climate")

    def test_followup_teraz_23(self):
        _, _, climate, _ = self._handle(
            "Włącz klimatyzację.",
            "Ustaw ją na 22 stopnie.",
            "Teraz 23.",
        )
        temps = [item for item in climate if item[0] == "set_temperature"]
        self.assertGreaterEqual(len(temps), 2)
        self.assertEqual(float(temps[-1][2].get("temperature")), 23)

    def test_chlodniej_and_plus_two(self):
        _, _, climate, _ = self._handle(
            "Włącz klimatyzację.",
            "Ustaw klimatyzację na 22 stopnie.",
            "Zrób chłodniej.",
            "Podnieś o dwa stopnie.",
        )
        temps = [float(item[2].get("temperature")) for item in climate if item[0] == "set_temperature"]
        self.assertIn(21.0, temps)
        self.assertTrue(any(value >= 23 for value in temps))

    def test_hvac_modes(self):
        _, _, climate, _ = self._handle(
            "Włącz chłodzenie.",
            "Włącz grzanie.",
            "Włącz osuszanie.",
            "Włącz sam nawiew.",
            "Ustaw auto.",
        )
        modes = [item[2].get("hvac_mode") for item in climate if item[0] == "set_hvac_mode"]
        for expected in ("cool", "heat", "dry", "fan_only", "auto"):
            self.assertIn(expected, modes)

    def test_fan_absolute_and_relative(self):
        _, _, climate, _ = self._handle(
            "Włącz klimatyzację.",
            "Ustaw nawiew na niski.",
            "Ustaw nawiew na średni.",
            "Ustaw nawiew na wysoki.",
            "Zwiększ nawiew.",
            "Zmniejsz nawiew.",
        )
        fans = [item[2].get("fan_mode") for item in climate if item[0] == "set_fan_mode"]
        self.assertIn("low", fans)
        self.assertIn("medium", fans)
        self.assertIn("high", fans)
        self.assertTrue(len(fans) >= 5)

    def test_quiet_supported_and_turbo_switch(self):
        _, results, climate, lights = self._handle(
            "Włącz klimatyzację.",
            "Włącz tryb cichy.",
            "Włącz turbo.",
            "Włącz tryb nocny.",
            "Włącz eco.",
        )
        self.assertEqual(lights, [])
        joined = " ".join(item.text for item in results)
        self.assertNotIn("przyciemniam", joined.lower())

    def test_swing_and_related_switches(self):
        calls = []

        def _svc(domain, service, entity_id, data=None):
            calls.append((domain, service, entity_id, dict(data or {})))
            return True

        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = _cm(mem)
            with patch.object(HaActions, "_call_service", side_effect=_svc):
                cm.handle("Włącz klimatyzację.")
                cm.handle("Włącz swing.")
                cm.handle("Włącz poziomy swing.")
                cm.handle("Wyłącz wyświetlacz klimatyzacji.")
                cm.handle("Wyłącz pikanie.")
                cm.handle("Włącz Health.")
                cm.handle("Włącz X-Fan.")
                cm.handle("Włącz samooczyszczanie.")
        self.assertTrue(any(item[1] == "set_swing_mode" for item in calls))
        self.assertTrue(any("panel_light" in item[2] for item in calls))
        self.assertTrue(any("beeper" in item[2] for item in calls))
        self.assertTrue(any("health" in item[2] for item in calls))
        self.assertTrue(any("xfan" in item[2] for item in calls))
        self.assertTrue(any("self_clean" in item[2] for item in calls))
        self.assertFalse(any("vizzini" in item[2] for item in calls))

    def test_status_questions(self):
        cm, results, climate, _ = self._handle(
            "Włącz klimatyzację.",
            "Jaka jest ustawiona temperatura?",
            "Jaki jest tryb?",
            "Jaki jest nawiew?",
        )
        texts = " ".join(item.text for item in results[1:])
        self.assertIn("22", texts)
        self.assertTrue("cool" in texts.lower() or "tryb" in texts.lower())
        self.assertTrue("medium" in texts.lower() or "nawiew" in texts.lower())
        self.assertFalse(any(item[0] == "set_temperature" for item in climate[1:] if False))

    def test_percent_never_celsius(self):
        _, results, climate, lights = self._handle(
            "Włącz klimatyzację.",
            "Ustaw ją na 50%.",
        )
        self.assertFalse(
            any(
                item[0] == "set_temperature" and float(item[2].get("temperature") or 0) == 50
                for item in climate
            )
        )
        self.assertIn("procent", (results[-1].text or "").lower())
        self.assertEqual(lights, [])

    def test_min_max_rejected(self):
        _, results, climate, _ = self._handle(
            "Włącz klimatyzację.",
            "Ustaw 35 stopni.",
            "Ustaw 10 stopni.",
        )
        self.assertFalse(any(item[0] == "set_temperature" for item in climate))
        self.assertIn("maksymalnie", (results[1].text or "").lower())
        self.assertIn("minimalnie", (results[2].text or "").lower())

    def test_light_degrees_rejected(self):
        _, results, climate, lights = self._handle(
            "Włącz lampę.",
            "Ustaw ją na 22 stopnie.",
        )
        self.assertEqual(climate, [])
        self.assertIn("światło", (results[-1].text or "").lower().replace("swiatlo", "światło"))

    def test_context_switch_light_climate(self):
        cm, _, climate, lights = self._handle(
            "Włącz lampę.",
            "Ustaw ją na 40%.",
            "Włącz klimatyzację.",
            "Ustaw ją na 22 stopnie.",
            "Zrób chłodniej.",
            "Włącz lampę.",
            "Ustaw ją na 50%.",
        )
        self.assertTrue(any(item[1] == "climate.gree_pular" for item in climate))
        self.assertTrue(any("vizzini" in item for item in lights))
        device = get_active_device(cm.mem)
        self.assertEqual(device.domain, "light")

    def test_pronoun_climate_off(self):
        _, _, climate, lights = self._handle(
            "Włącz klimatyzację.",
            "Wyłącz ją.",
        )
        self.assertTrue(any(item[0] == "turn_off" for item in climate))
        self.assertEqual(lights, [])

    def test_8c_and_quiet_unsupported_honest(self):
        slim = [
            {
                "entity_id": "climate.gree_pular",
                "state": "cool",
                "attributes": {
                    "friendly_name": "Klimatyzacja GREE Pular",
                    "hvac_modes": ["off", "cool", "heat"],
                    "temperature": 22,
                    "min_temp": 16,
                    "max_temp": 30,
                    "target_temp_step": 1,
                },
            },
            gree_pular_fixture_states()[-1],
        ]
        HomeAssistant.get_registry().load_states(slim)
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = _cm(mem)
            with patch.object(HaActions, "_call_service", return_value=True):
                quiet = cm.handle("Włącz tryb cichy.")
                turbo = cm.handle("Włącz turbo.")
                frost = cm.handle("Włącz ochronę przed zamarzaniem.")
        for result in (quiet, turbo, frost):
            text = (result.text or "").lower()
            self.assertNotIn("włączam tryb cichy", text)
            self.assertTrue(
                "home assistant" in text or "nie widzę" in text or "nie widze" in text
            )

    def test_fake_claim_blocked(self):
        guarded = guard_action_claim("Ustawiam 22 stopnie.", [])
        self.assertTrue(guarded.blocked)

    def test_multi_step_partial(self):
        calls = []

        def _svc(domain, service, entity_id, data=None):
            calls.append((domain, service, entity_id))
            return True

        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = _cm(mem)
            with patch.object(HaActions, "_call_service", side_effect=_svc):
                cm.handle("Włącz klimatyzację.")
                result = cm.handle(
                    "Ustaw 23 stopnie, nawiew na auto i włącz fresh air."
                )
        self.assertTrue(any(item[1] == "set_temperature" for item in calls))
        self.assertTrue(
            "home assistant" in (result.text or "").lower()
            or "nie widzę" in (result.text or "").lower()
            or "fresh" in (result.text or "").lower()
        )

    def test_shortlist_climate_only(self):
        catalog = [
            {"name": n, "available": True}
            for n in (
                "web_search",
                "open_app",
                "play_media",
                "create_reminder",
                "turn_on_device",
                "turn_off_device",
                "set_device_value",
            )
        ]
        names = [
            item["name"]
            for item in shortlist_tools(
                catalog,
                goal="Ustaw ją na 22 stopnie.",
                classification=IntentClassification(
                    family=IntentFamily.SMART_HOME,
                    is_complete=True,
                    slots={
                        "resolved_context": True,
                        "entity_id": "climate.gree_pular",
                        "domain": "climate",
                        "semantic_action": "climate_temperature",
                    },
                ),
                active_device={"entity_id": "climate.gree_pular", "domain": "climate"},
            )
        ]
        self.assertEqual(
            set(names),
            {"turn_on_device", "turn_off_device", "set_device_value"},
        )

    def test_never_target_vizzini(self):
        _, _, climate, lights = self._handle(
            "Włącz klimatyzację.",
            "Ustaw ją na 22 stopnie.",
            "Wyłącz wyświetlacz klimatyzacji.",
        )
        self.assertTrue(all(item[1] != "light.vizzini" for item in climate))
        self.assertNotIn("light.vizzini", lights)

    def test_fahrenheit_ha_range_uses_spoken_celsius(self):
        HomeAssistant.get_registry().load_states(
            [
                {
                    "entity_id": "climate.gree_pular",
                    "state": "cool",
                    "attributes": {
                        "friendly_name": "Klimatyzacja",
                        "hvac_modes": ["off", "cool", "heat"],
                        "temperature": 61,
                        "current_temperature": 79,
                        "min_temp": 46,
                        "max_temp": 86,
                        "target_temp_step": 1,
                    },
                }
            ]
        )
        entity = HomeAssistant.get_registry().get("climate.gree_pular")
        profile = profile_from_ha_entity(entity)
        self.assertEqual(profile.temperature_unit, "F")
        ok, err = validate_temperature(profile, 22)
        self.assertEqual(ok, 22)
        self.assertIsNone(err)
        bad, err = validate_temperature(profile, 35)
        self.assertIsNone(bad)
        self.assertIn("30", err)
        _, results, climate, _ = self._handle("Ustaw 22 stopnie.")
        self.assertTrue(any(item[0] == "set_temperature" for item in climate))
        self.assertNotIn("minimalnie", (results[-1].text or "").lower())

    def test_validate_temperature_helper(self):
        entity = HomeAssistant.get_registry().get("climate.gree_pular")
        related = related_from_states("climate.gree_pular", gree_pular_fixture_states())
        profile = profile_from_ha_entity(entity, related=related, simulated=True)
        ok, err = validate_temperature(profile, 22)
        self.assertEqual(ok, 22)
        self.assertIsNone(err)
        bad, err = validate_temperature(profile, 35)
        self.assertIsNone(bad)
        self.assertIn("maksymalnie", err)

    def test_plan_none_for_light_dim(self):
        from core.device_context import ActiveDevice

        plan = plan_climate_utterance(
            "Przyciemnij ją.",
            ActiveDevice(entity_id="light.vizzini", domain="light", name="Vizzini"),
        )
        self.assertIsNone(plan)

    def test_status_after_light_still_answers_climate(self):
        _cm_obj, results, _climate, _lights = self._handle(
            "Włącz lampę.",
            "Jaka jest ustawiona temperatura?",
        )
        text = (results[-1].text or "").lower()
        self.assertNotIn("niedozwolone", text)
        self.assertNotIn("chwila", text)
        self.assertTrue(
            "22" in text or "stopni" in text or "temperatur" in text,
            results[-1].text,
        )


if __name__ == "__main__":
    unittest.main()
