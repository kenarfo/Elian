"""Phase 12.3.2 — local device status must beat web + context isolation."""

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
from core.climate_control import plan_climate_utterance
from core.climate_profile import gree_pular_fixture_states
from core.conversation_focus import enrich_goal_with_focus, update_focus
from core.conversation_manager import ConversationManager
from core.device_context import ActiveDevice
from core.freshness import analyze_freshness
from core.intent_family import IntentFamily, classify_intent_family
from core.local_device_status import (
    INTENT_KNOWLEDGE,
    INTENT_LOCAL_DEVICE_CAPABILITY,
    INTENT_LOCAL_DEVICE_STATUS,
    SOURCE_LOCAL_HA,
    classify_local_request,
)
from core.model_client import FakeModelClient, reset_model_client
from core.tool_shortlist import shortlist_tools
from core.tools import reset_registry_for_tests
from memory.manager import MemoryManager


def _load():
    return HomeAssistant.get_registry().load_states(gree_pular_fixture_states())


def _cm(mem):
    return ConversationManager(
        registry=build_default_registry(),
        model=FakeModelClient(
            [
                {
                    "type": "final_answer",
                    "message": "Tryb Auto oznacza, że urządzenie samo dobiera chłodzenie.",
                }
            ]
            * 8
        ),
        memory_manager=mem,
    )


def _active_climate():
    return ActiveDevice(
        entity_id="climate.gree_pular",
        domain="climate",
        name="Klimatyzacja",
    )


class LocalDeviceStatusClassifierTests(unittest.TestCase):
    def test_hvac_status_not_web(self):
        raw = "Jaki jest teraz tryb klimatyzacji?"
        local = classify_local_request(raw, active_domain="climate")
        self.assertEqual(local.intent, INTENT_LOCAL_DEVICE_STATUS)
        self.assertEqual(local.kind, SOURCE_LOCAL_HA)
        self.assertEqual(local.semantic_action, "climate_status_hvac")
        self.assertFalse(local.web_allowed)
        info = classify_intent_family(raw, active_domain="climate")
        self.assertEqual(info.family, IntentFamily.SMART_HOME)
        self.assertTrue(info.is_complete)
        self.assertFalse(analyze_freshness(raw).freshness_required)

    def test_w_jakim_trybie(self):
        local = classify_local_request("W jakim trybie działa klima?")
        self.assertEqual(local.semantic_action, "climate_status_hvac")
        self.assertEqual(classify_intent_family("W jakim trybie działa klima?").family, IntentFamily.SMART_HOME)

    def test_summary(self):
        local = classify_local_request("Co teraz robi klimatyzacja?")
        self.assertEqual(local.semantic_action, "climate_status_summary")

    def test_target_temp(self):
        local = classify_local_request(
            "Jaka jest ustawiona temperatura?",
            active_domain="climate",
        )
        self.assertEqual(local.semantic_action, "climate_status_target_temperature")

    def test_current_temp(self):
        local = classify_local_request("Jaka jest temperatura w pokoju?")
        self.assertEqual(local.semantic_action, "climate_status_current_temperature")

    def test_fan(self):
        local = classify_local_request("Jaki jest nawiew?")
        self.assertEqual(local.semantic_action, "climate_status_fan")

    def test_swing(self):
        local = classify_local_request("Czy swing jest włączony?")
        self.assertEqual(local.semantic_action, "climate_status_vertical_swing")

    def test_eco_preset(self):
        local = classify_local_request("Czy eco jest włączone?")
        self.assertEqual(local.semantic_action, "climate_status_preset")

    def test_quiet(self):
        local = classify_local_request("Czy tryb cichy jest włączony?")
        self.assertEqual(local.semantic_action, "climate_status_quiet")

    def test_meaning_is_knowledge_not_status(self):
        raw = "Co oznacza tryb auto w klimatyzacji?"
        local = classify_local_request(raw)
        self.assertEqual(local.intent, INTENT_KNOWLEDGE)
        self.assertNotEqual(local.intent, INTENT_LOCAL_DEVICE_STATUS)
        self.assertEqual(classify_intent_family(raw).family, IntentFamily.KNOWLEDGE)

    def test_own_modes_are_capability(self):
        raw = "Jakie tryby ma moja klimatyzacja?"
        local = classify_local_request(raw)
        self.assertEqual(local.intent, INTENT_LOCAL_DEVICE_CAPABILITY)
        self.assertEqual(classify_intent_family(raw).family, IntentFamily.SMART_HOME)

    def test_generic_ac_modes_are_knowledge(self):
        raw = "Jakie tryby mają klimatyzatory?"
        local = classify_local_request(raw)
        self.assertEqual(local.intent, INTENT_KNOWLEDGE)
        self.assertEqual(classify_intent_family(raw).family, IntentFamily.KNOWLEDGE)

    def test_teraz_on_local_device_not_web(self):
        fresh = analyze_freshness("Jaki jest teraz tryb klimatyzacji?")
        self.assertFalse(fresh.freshness_required)
        self.assertNotEqual(
            classify_intent_family("Jaki jest teraz tryb klimatyzacji?").family,
            IntentFamily.WEB,
        )

    def test_political_freshness_still_web(self):
        raw = "Kto jest teraz premierem Wielkiej Brytanii?"
        fresh = analyze_freshness(raw)
        self.assertTrue(fresh.freshness_required)
        self.assertEqual(classify_intent_family(raw).family, IntentFamily.WEB)
        self.assertEqual(classify_local_request(raw).kind, "UNKNOWN")


class LocalStatusContextIsolationTests(unittest.TestCase):
    def test_spotify_pref_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            mem.set_preference("music.default_app", "Spotify", explicit=True, source="user")
            built = mem.build_context("Jaki jest teraz tryb klimatyzacji?")
            self.assertEqual(built["preferences"], {})
            self.assertNotIn("music.default_app", built["preferences"])

    def test_website_context_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            mem.short.set_entity(
                "website",
                "i jest teraz tryb klimatyzacji",
            )
            mem.short.set_entity(
                "device",
                "Klimatyzacja",
                entity_id="climate.gree_pular",
                domain="climate",
            )
            built = mem.build_context("Jaki jest teraz tryb klimatyzacji?")
            self.assertNotIn("website", built["entities"])
            self.assertIn("device", built["entities"])

    def test_stale_tcl_web_answer_excluded(self):
        focus = update_focus(
            None,
            user_text="sprawdź tryb mojej klimatyzacji.",
            assistant_text="Tryb Auto w klimatyzacji TCL oznacza automatyczny dobór.",
        )
        focus["referenced_people"] = ["Fresh Air", "Gentle Wind", "Tryb Auto"]
        enriched = enrich_goal_with_focus("Jaki jest teraz tryb klimatyzacji?", focus)
        self.assertNotIn("TCL", enriched)
        self.assertNotIn("Ostatnia odpowiedź", enriched)
        self.assertEqual(enriched, "Jaki jest teraz tryb klimatyzacji?")


class LocalStatusExecutionTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        ConversationContext.clear()
        _load()

    def _run(self, utterance, *, seed_on=False):
        calls = []

        def _svc(domain, service, entity_id, data=None):
            calls.append((domain, service, entity_id, dict(data or {})))
            return True

        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = _cm(mem)
            cm.activate()
            with patch.object(HaActions, "_call_service", side_effect=_svc):
                if seed_on:
                    cm.handle("Włącz klimatyzację.")
                    calls.clear()
                result = cm.handle(utterance)
            plan = plan_climate_utterance(utterance, _active_climate())
            return result, calls, plan

    def test_hvac_status_reads_ha_no_web_no_mutation(self):
        result, calls, plan = self._run("Jaki jest teraz tryb klimatyzacji?")
        self.assertTrue(plan and plan.status_text)
        self.assertEqual(plan.semantic_action, "climate_status_hvac")
        self.assertEqual(plan.entity_id, "climate.gree_pular")
        self.assertFalse(plan.steps)
        self.assertEqual(calls, [])
        text = (result.text or "").lower()
        self.assertTrue("chłodz" in text or "trybie" in text)
        self.assertNotIn("tcl", text)
        self.assertNotIn("oznacza", text)

    def test_zero_mutation_on_status(self):
        mutating = {
            "set_hvac_mode",
            "turn_on",
            "turn_off",
            "set_temperature",
            "set_fan_mode",
            "set_preset_mode",
        }
        phrases = (
            "Jaki jest teraz tryb klimatyzacji?",
            "Jaka jest ustawiona temperatura?",
            "Jaka jest temperatura w pokoju?",
            "Jaki jest nawiew?",
            "Czy swing jest włączony?",
            "Czy eco jest włączone?",
            "Czy tryb cichy jest włączony?",
            "Co teraz robi klimatyzacja?",
        )
        for phrase in phrases:
            result, calls, plan = self._run(phrase)
            self.assertTrue(plan and plan.status_text, phrase)
            self.assertFalse(plan.steps, phrase)
            self.assertFalse(any(item[1] in mutating for item in calls), phrase)
            names = [item.get("name") for item in shortlist_tools(
                [{"name": "web_search", "available": True},
                 {"name": "fetch_web_page", "available": True},
                 {"name": "open_app", "available": True},
                 {"name": "play_media", "available": True},
                 {"name": "create_reminder", "available": True},
                 {"name": "get_device_state", "available": True},
                 {"name": "turn_on_device", "available": True},
                 {"name": "set_device_value", "available": True}],
                goal=phrase,
                classification=classify_intent_family(phrase, active_domain="climate"),
                active_device={"entity_id": "climate.gree_pular", "domain": "climate"},
            )]
            self.assertNotIn("web_search", names, phrase)
            self.assertNotIn("open_app", names, phrase)
            self.assertNotIn("play_media", names, phrase)
            self.assertNotIn("create_reminder", names, phrase)

    def test_status_polish_values(self):
        _, _, hvac = self._run("Jaki jest teraz tryb klimatyzacji?")
        self.assertIn("chłodzenia", (hvac.status_text or "").lower())
        _, _, fan = self._run("Jaki jest nawiew?")
        self.assertIn("średni", (fan.status_text or "").lower())
        _, _, quiet = self._run("Czy tryb cichy jest włączony?")
        self.assertIn("wyłączony", (quiet.status_text or "").lower())
        _, _, cap = self._run("Jakie tryby ma moja klimatyzacja?")
        self.assertEqual(cap.semantic_action, "climate_status_capabilities")
        self.assertIn("chłodzenie", (cap.status_text or "").lower())

    def test_knowledge_does_not_read_as_status(self):
        plan = plan_climate_utterance(
            "Co oznacza tryb auto w klimatyzacji?",
            _active_climate(),
        )
        self.assertTrue(plan is None or not plan.handled)


if __name__ == "__main__":
    unittest.main()
