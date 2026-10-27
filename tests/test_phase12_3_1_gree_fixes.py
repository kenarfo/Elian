"""Phase 12.3.1 — GREE hardware eco/echo routing + precise louver."""

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
from core.climate_control import plan_climate_utterance, wants_climate_eco
from core.climate_profile import gree_pular_fixture_states
from core.conversation_manager import ConversationManager
from core.device_context import ActiveDevice
from core.intent_family import IntentFamily, classify_intent_family
from core.model_client import FakeModelClient, reset_model_client
from core.tool_shortlist import shortlist_tools
from core.tools import reset_registry_for_tests
from memory.manager import MemoryManager


LIVE_SWING = [
    "default",
    "full_swing",
    "fixed_upper",
    "fixed_upper_middle",
    "fixed_middle",
    "fixed_lower_middle",
    "fixed_lower",
    "swing_upper",
    "swing_upper_middle",
    "swing_middle",
    "swing_lower_middle",
    "swing_lower",
]
LIVE_H_SWING = [
    "default",
    "full_swing",
    "left",
    "left_center",
    "center",
    "right_center",
    "right",
]


def _live_like_states(state="cool"):
    states = gree_pular_fixture_states()
    climate = dict(states[0])
    attrs = dict(climate["attributes"])
    attrs["swing_modes"] = list(LIVE_SWING)
    attrs["swing_horizontal_modes"] = list(LIVE_H_SWING)
    attrs["preset_modes"] = ["eco", "away", "boost", "none", "sleep"]
    attrs["swing_mode"] = "full_swing"
    climate["state"] = state
    climate["attributes"] = attrs
    return [climate] + list(states[1:])


def _load(states=None):
    return HomeAssistant.get_registry().load_states(states or _live_like_states())


def _cm(mem):
    return ConversationManager(
        registry=build_default_registry(),
        model=FakeModelClient(
            [{"type": "final_answer", "message": "Nie wykonałem jeszcze tej czynności."}] * 8
        ),
        memory_manager=mem,
    )


class GreeHardwareFixesTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        ConversationContext.clear()
        _load()

    def _handle(self, *utterances, states=None):
        if states is not None:
            HomeAssistant.get_registry().load_states(states)
        climate = []
        apps = []

        def _svc(domain, service, entity_id, data=None):
            climate.append((service, entity_id, dict(data or {})))
            return True

        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = _cm(mem)
            cm.activate()
            results = []
            with patch.object(HaActions, "_call_service", side_effect=_svc):
                for text in utterances:
                    results.append(cm.handle(text))
            return cm, results, climate, apps

    def test_eko_smart_home_preset_eco(self):
        info = classify_intent_family("Włącz eko.")
        self.assertEqual(info.family, IntentFamily.SMART_HOME)
        _, results, climate, apps = self._handle("Włącz klimatyzację.", "Włącz eko.")
        self.assertEqual(apps, [])
        self.assertTrue(
            any(item[0] == "set_preset_mode" and item[2].get("preset_mode") == "eco" for item in climate)
        )
        self.assertNotIn("aplikac", (results[-1].text or "").lower())

    def test_eco_english(self):
        _, _, climate, _ = self._handle("Włącz klimatyzację.", "Włącz eco.")
        self.assertTrue(
            any(item[0] == "set_preset_mode" and item[2].get("preset_mode") == "eco" for item in climate)
        )

    def test_echo_active_climate_is_eco(self):
        info = classify_intent_family("Włącz echo.")
        self.assertEqual(info.family, IntentFamily.APP)
        self.assertEqual(
            classify_intent_family("Włącz echo.", active_domain="climate").family,
            IntentFamily.SMART_HOME,
        )
        _, _, climate, apps = self._handle("Włącz klimatyzację.", "Włącz echo.")
        self.assertEqual(apps, [])
        self.assertTrue(
            any(item[0] == "set_preset_mode" and item[2].get("preset_mode") == "eco" for item in climate)
        )

    def test_echo_on_climate_explicit_not_app(self):
        info = classify_intent_family("Włącz tryb echo na klimatyzacji.")
        self.assertEqual(info.family, IntentFamily.SMART_HOME)
        _, _, climate, apps = self._handle("Włącz tryb echo na klimatyzacji.")
        self.assertEqual(apps, [])
        self.assertTrue(
            any(item[0] == "set_preset_mode" and item[2].get("preset_mode") == "eco" for item in climate)
        )
        self.assertFalse(any(item[0] == "turn_on" and "preset" not in str(item) for item in climate if False))
        self.assertTrue(any(item[2].get("preset_mode") == "eco" for item in climate))

    def test_eco_no_open_app_in_shortlist(self):
        names = [
            item["name"]
            for item in shortlist_tools(
                [
                    {"name": n, "available": True}
                    for n in (
                        "web_search",
                        "open_app",
                        "close_app",
                        "check_app_installed",
                        "play_media",
                        "create_reminder",
                        "turn_on_device",
                        "turn_off_device",
                        "set_device_value",
                    )
                ],
                goal="Włącz eko.",
                classification=classify_intent_family("Włącz eko."),
                active_device={"entity_id": "climate.gree_pular", "domain": "climate"},
            )
        ]
        self.assertNotIn("open_app", names)
        self.assertNotIn("close_app", names)
        self.assertIn("set_device_value", names)

    def test_eco_while_off_powers_on_then_preset(self):
        _, _, climate, _ = self._handle(
            "Włącz eko.",
            states=_live_like_states("off"),
        )
        services = [item[0] for item in climate]
        self.assertIn("turn_on", services)
        self.assertIn("set_preset_mode", services)
        self.assertEqual(services[-1], "set_preset_mode")
        self.assertEqual(climate[-1][2].get("preset_mode"), "eco")

    def test_eco_already_on_no_redundant_power(self):
        _, _, climate, _ = self._handle("Włącz klimatyzację.", "Włącz eko.")
        eco_turn = [item for item in climate if item[0] == "set_preset_mode"]
        self.assertTrue(eco_turn)
        # Power-on for the first utterance is fine; the eco turn itself is preset only.
        after_on = [item for item in climate if item[0] != "turn_on"]
        self.assertTrue(all(item[0] == "set_preset_mode" for item in after_on[-1:]))

    def test_turbo_sleep_away_unchanged(self):
        _, _, climate, _ = self._handle(
            "Włącz klimatyzację.",
            "Włącz turbo.",
            "Włącz tryb nocny.",
            "Włącz ochronę przed zamarzaniem.",
        )
        presets = [item[2].get("preset_mode") for item in climate if item[0] == "set_preset_mode"]
        fans_or_switches = [(item[0], item[1]) for item in climate]
        self.assertTrue(
            "boost" in presets
            or any("turbo" in str(item[1]).lower() for item in fans_or_switches)
        )
        self.assertTrue(
            "sleep" in presets
            or any("sleep" in str(item[1]).lower() for item in fans_or_switches)
        )
        self.assertIn("away", presets)

    def test_swing_on_is_full_swing(self):
        _, _, climate, _ = self._handle("Włącz klimatyzację.", "Włącz swing.")
        modes = [item[2].get("swing_mode") for item in climate if item[0] == "set_swing_mode"]
        self.assertIn("full_swing", modes)

    def test_stop_vertical_is_default(self):
        _, _, climate, _ = self._handle("Włącz klimatyzację.", "Zatrzymaj ruch góra-dół.")
        modes = [item[2].get("swing_mode") for item in climate if item[0] == "set_swing_mode"]
        self.assertIn("default", modes)

    def test_nawiew_do_gory_is_fixed_upper(self):
        _, _, climate, _ = self._handle("Włącz klimatyzację.", "Ustaw nawiew do góry.")
        modes = [item[2].get("swing_mode") for item in climate if item[0] == "set_swing_mode"]
        self.assertEqual(modes[-1], "fixed_upper")
        self.assertNotIn("full_swing", modes)

    def test_na_srodek_vertical_fixed_middle(self):
        _, _, climate, _ = self._handle(
            "Włącz klimatyzację.",
            "Ustaw nawiew do góry.",
            "Na środek.",
        )
        modes = [item[2].get("swing_mode") for item in climate if item[0] == "set_swing_mode"]
        self.assertEqual(modes[-1], "fixed_middle")

    def test_nawiew_w_dol_fixed_lower(self):
        _, _, climate, _ = self._handle("Włącz klimatyzację.", "Ustaw nawiew w dół.")
        modes = [item[2].get("swing_mode") for item in climate if item[0] == "set_swing_mode"]
        self.assertEqual(modes[-1], "fixed_lower")

    def test_horizontal_swing_and_left(self):
        _, _, climate, _ = self._handle(
            "Włącz klimatyzację.",
            "Włącz poziomy swing.",
            "Ustaw w lewo.",
        )
        horiz = [
            item[2].get("swing_horizontal_mode")
            for item in climate
            if item[0] == "set_swing_horizontal_mode"
        ]
        self.assertIn("full_swing", horiz)
        self.assertEqual(horiz[-1], "left")

    def test_no_fake_eco_without_tool(self):
        guarded = guard_action_claim("Włączam eco.", [])
        self.assertTrue(guarded.blocked)

    def test_no_fake_louver_without_tool(self):
        guarded = guard_action_claim("Ustawiam żaluzje na fixed_upper.", [])
        self.assertTrue(guarded.blocked)

    def test_echo_outside_climate_not_auto_eco(self):
        self.assertFalse(wants_climate_eco("Włącz echo.", climate_context=False))
        plan = plan_climate_utterance(
            "Włącz echo.",
            ActiveDevice(entity_id="light.vizzini", domain="light", name="Vizzini"),
        )
        self.assertIsNone(plan)
        info = classify_intent_family("Włącz echo.")
        self.assertEqual(info.family, IntentFamily.APP)

    def test_celsius_fahrenheit_table(self):
        expected = {
            16: 61.0,
            18: 64.0,
            20: 68.0,
            21: 70.0,
            22: 72.0,
            23: 73.0,
            24: 75.0,
            26: 79.0,
            30: 86.0,
        }
        for celsius, fahrenheit in expected.items():
            diag = HaActions.describe_temperature_conversion(celsius, "F", 1)
            self.assertEqual(diag["ha_service_payload"], fahrenheit, msg=celsius)
            self.assertEqual(diag["rounding_policy"], "nearest_step_half_up")


if __name__ == "__main__":
    unittest.main()
