"""Light color FastPath — CommandParser must emit light_color, not drop to agent."""

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
from CommandParser import parse_commands
from core.builtin_tools import build_default_registry
from core.conversation_manager import ConversationManager
from core.fast_path import FastPathStatus, evaluate_fast_path
from core.model_client import FakeModelClient, reset_model_client
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

COLORS = (
    ("czerwony", [255, 0, 0]),
    ("zielony", [0, 255, 0]),
    ("niebieski", [0, 0, 255]),
    ("żółty", [255, 255, 0]),
    ("fioletowy", [160, 32, 240]),
)


class LightColorFastPathTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        ConversationContext.clear()
        HomeAssistant.get_registry().load_states([SAMPLE_LIGHT])

    def test_parse_each_canonical_color(self):
        for name, _rgb in COLORS:
            with self.subTest(color=name):
                commands = parse_commands(f"Ustaw lampę na {name}.")
                self.assertTrue(commands, name)
                self.assertEqual(commands[0].action, "light_color")
                self.assertEqual(commands[0].domain, "light")
                self.assertEqual(commands[0].extra, name)

    def test_parse_feminine_and_adverb(self):
        for utterance, canonical in (
            ("Ustaw lampę na kolor czerwona.", "czerwony"),
            ("Ustaw lampę na czerwono.", "czerwony"),
            ("Ustaw światło na zielono.", "zielony"),
        ):
            with self.subTest(utterance=utterance):
                commands = parse_commands(utterance)
                self.assertTrue(commands)
                self.assertEqual(commands[0].action, "light_color")
                self.assertEqual(commands[0].extra, canonical)

    def test_fast_path_handles_color(self):
        result = evaluate_fast_path("Ustaw lampę na czerwony.")
        self.assertEqual(result.status, FastPathStatus.HANDLED_COMPLETE)

    def test_cm_sets_each_rgb(self):
        calls = []

        def _svc(domain, service, entity_id, data=None):
            calls.append((domain, service, entity_id, dict(data or {})))
            return True

        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            cm = ConversationManager(
                registry=build_default_registry(),
                model=FakeModelClient(
                    [{"type": "final_answer", "message": "Chwila — sprawdzam to dokładniej."}] * 12
                ),
                memory_manager=mem,
            )
            cm.activate()
            with patch.object(HaActions, "_call_service", side_effect=_svc):
                for name, rgb in COLORS:
                    before = len(calls)
                    result = cm.handle(f"Ustaw lampę na {name}.")
                    new_calls = calls[before:]
                    self.assertTrue(
                        any(item[3].get("rgb_color") == rgb for item in new_calls),
                        f"{name}: {result.text!r} calls={new_calls}",
                    )
                    self.assertNotIn("chwila", (result.text or "").lower())


if __name__ == "__main__":
    unittest.main()
