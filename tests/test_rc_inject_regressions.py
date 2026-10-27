"""RC inject regressions: greeting, color-when-on, brightness 'lampy', climate status."""

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
from core.climate_profile import gree_pular_fixture_states
from core.conversation_manager import ConversationManager
from core.jarvis_response import social_reply
from core.model_client import FakeModelClient, reset_model_client
from core.semantic_executive import decide_semantics
from core.tools import reset_registry_for_tests
from memory.manager import MemoryManager

SAMPLE_ON = {
    "entity_id": "light.vizzini",
    "state": "on",
    "attributes": {
        "friendly_name": "Inspire Vizzini D56 Vizzini",
        "supported_color_modes": ["rgb", "color_temp", "brightness"],
        "brightness": 200,
        "rgb_color": [255, 206, 166],
    },
}


def _cm():
    mem = MemoryManager(Path(tempfile.mkdtemp()) / "m.json")
    cm = ConversationManager(
        registry=build_default_registry(),
        model=FakeModelClient(
            [{"type": "final_answer", "message": "Chwila — sprawdzam to dokładniej."}] * 12
        ),
        memory_manager=mem,
    )
    cm.activate()
    return cm


class RcInjectRegressionTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        ConversationContext.clear()

    def test_czesc_is_not_jestem_mow(self):
        env = decide_semantics("cześć", allow_llm=False)
        self.assertEqual(env.intent, "social_smalltalk")
        self.assertNotEqual(env.user_goal.lower(), "casual conversation")
        self.assertEqual(social_reply(env.user_goal), "Cześć.")
        result = _cm().handle("Cześć.")
        text = (result.text or "").strip()
        self.assertEqual(text, "Cześć.")
        self.assertNotIn("jestem. mów", text.lower())

    def test_color_when_already_on_sends_rgb(self):
        HomeAssistant.get_registry().load_states([SAMPLE_ON])
        calls = []

        def _svc(domain, service, entity_id, data=None):
            calls.append((domain, service, entity_id, dict(data or {})))
            return True

        cm = _cm()
        with patch.object(HaActions, "_call_service", side_effect=_svc):
            result = cm.handle("Ustaw lampę na czerwony.")
        text = (result.text or "").lower()
        self.assertTrue(
            any(item[3].get("rgb_color") == [255, 0, 0] for item in calls),
            calls,
        )
        self.assertNotIn("już jest włączone", text)
        self.assertNotIn("chwila", text)
        self.assertNotIn("chcesz ustawić", text)
        self.assertIn("czerwony", text)

    def test_brightness_lampy_genitive(self):
        HomeAssistant.get_registry().load_states([SAMPLE_ON])
        calls = []

        def _svc(domain, service, entity_id, data=None):
            calls.append((domain, service, entity_id, dict(data or {})))
            return True

        cm = _cm()
        with patch.object(HaActions, "_call_service", side_effect=_svc):
            result = cm.handle("Ustaw jasność lampy na 20 procent.")
        self.assertTrue(
            any(int(item[3].get("brightness_pct") or -1) == 20 for item in calls),
            f"{result.text!r} {calls}",
        )
        self.assertNotIn("nie udało się", (result.text or "").lower())

    def test_climate_status_after_light_not_invalid_args(self):
        HomeAssistant.get_registry().load_states(gree_pular_fixture_states())
        cm = _cm()
        with patch.object(HaActions, "_call_service", return_value=True):
            cm.handle("Włącz lampę.")
            result = cm.handle("Jaka jest ustawiona temperatura?")
        text = (result.text or "").lower()
        self.assertNotIn("niedozwolone", text)
        self.assertNotIn("utterance", text)
        self.assertTrue("22" in text or "stopni" in text or "temperatur" in text, result.text)

    def test_negation_does_not_turn_on_lamp(self):
        HomeAssistant.get_registry().load_states([SAMPLE_ON])
        calls = []

        def _svc(domain, service, entity_id, data=None):
            calls.append((domain, service, entity_id, dict(data or {})))
            return True

        cm = _cm()
        with patch.object(HaActions, "_call_service", side_effect=_svc):
            result = cm.handle("Nie włączaj lampy.")
        text = (result.text or "").lower()
        self.assertFalse(calls, calls)
        self.assertNotIn("włączone", text)
        self.assertTrue("nie" in text or "dobrze" in text, result.text)

    def test_jak_tam_is_smalltalk(self):
        result = _cm().handle("Jak tam.")
        text = (result.text or "").lower()
        self.assertIn("słucham", text)
        self.assertNotIn("chcesz otworzyć", text)

    def test_ascii_lampe_color_is_fastpath_not_invented_tool(self):
        from core.fast_path import FastPathStatus, evaluate_fast_path
        from core.intent_family import IntentFamily, classify_intent_family

        info = classify_intent_family("ustaw lampe na czerwony")
        self.assertEqual(info.family, IntentFamily.SMART_HOME)
        env = decide_semantics("ustaw lampe na czerwony", allow_llm=False)
        self.assertEqual(env.mode, "ACT")
        self.assertEqual(env.intent, "smart_home_control")
        self.assertEqual(
            evaluate_fast_path("ustaw lampe na czerwony", env).status,
            FastPathStatus.HANDLED_COMPLETE,
        )
        HomeAssistant.get_registry().load_states([SAMPLE_ON])
        calls = []

        def _svc(domain, service, entity_id, data=None):
            calls.append((domain, service, entity_id, dict(data or {})))
            return True

        cm = _cm()
        with patch.object(HaActions, "_call_service", side_effect=_svc):
            result = cm.handle("ustaw lampe na czerwony")
        text = (result.text or "").lower()
        self.assertTrue(
            any(item[3].get("rgb_color") == [255, 0, 0] for item in calls),
            calls,
        )
        self.assertNotIn("nie znam narzędzia", text)
        self.assertNotIn("ustaw_lamp", text)
        self.assertIn("czerwony", text)


if __name__ == "__main__":
    unittest.main()
