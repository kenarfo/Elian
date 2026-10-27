"""Phase 10.4.1 — light action result correctness + brightness mapping."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import HaActions
import HomeAssistant
from core.agent_repair import build_deterministic_decision
from core.builtin_tools import _message_success, _set_device_value, _tool_result_from_ha_message
from core.intent_family import IntentClassification, IntentFamily
from core.model_client import FakeModelClient, reset_model_client
from core.planner import Planner
from core.response_builder import never_fake_success
from core.risk import RiskLevel
from core.tool_result import ToolResult, ToolStatus
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
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


def _load(states):
    return HomeAssistant.get_registry().load_states(states)


class UnsupportedNotSuccessTests(unittest.TestCase):

    def test_nie_obsluguje_message_not_success(self):
        msg = "Nie obsługuję tej czynności dla Inspire Vizzini D56 Vizzini."
        self.assertFalse(_message_success(msg))
        result = _tool_result_from_ha_message(msg, tool_name="set_device_value")
        self.assertFalse(result.success)
        self.assertNotEqual(result.status, ToolStatus.SUCCESS)
        self.assertEqual(result.status, ToolStatus.UNSUPPORTED)

    def test_unknown_action_via_execute(self):
        _load([SAMPLE_LIGHT])
        message = HaActions.execute_device_command(
            "totally_fake_action",
            target="light.vizzini",
            domain="light",
            entity_id="light.vizzini",
        )
        self.assertIn("Nie obsługuję", message)
        with patch("core.builtin_tools.execute_device_command", return_value=message):
            result = _set_device_value(
                {"action": "totally_fake_action", "target": "light.vizzini", "domain": "light"}
            )
        self.assertFalse(result.success)
        self.assertNotEqual(result.status.value, "success")


class BrightnessMappingTests(unittest.TestCase):

    def setUp(self):
        _load([SAMPLE_LIGHT])

    def test_brightness_up_maps_to_adjust_and_increases(self):
        with patch.object(HaActions, "api_request", return_value=Mock(status_code=200)) as req:
            message = HaActions.execute_device_command(
                "brightness_up",
                target="light.vizzini",
                domain="light",
                entity_id="light.vizzini",
            )
        self.assertIn("jasność", message.lower())
        self.assertNotIn("Nie obsługuję", message)
        payload = req.call_args.kwargs["json"]
        self.assertEqual(payload["entity_id"], "light.vizzini")
        # current ~50% (128/255) + 15 → ~65
        self.assertEqual(payload.get("brightness_pct"), 65)

    def test_adjust_brightness_canonical_from_repair(self):
        info = IntentClassification(
            family=IntentFamily.SMART_HOME,
            is_complete=True,
            slots={
                "implicit_light": True,
                "target": "light.vizzini",
                "domain": "light",
            },
            raw_text="Trochę tu ciemno.",
        )
        decision = build_deterministic_decision(info)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.tool, "set_device_value")
        self.assertEqual(decision.arguments.get("action"), "adjust_brightness")
        self.assertEqual(decision.arguments.get("domain"), "light")
        self.assertEqual(decision.arguments.get("target"), "light.vizzini")
        self.assertEqual(
            int(decision.arguments.get("value") or 0),
            HaActions.DEFAULT_BRIGHTNESS_STEP,
        )

    def test_onoff_light_unsupported_brightness_message(self):
        _load([ONOFF_ONLY])
        info = IntentClassification(
            family=IntentFamily.SMART_HOME,
            is_complete=True,
            slots={
                "implicit_light": True,
                "target": "light.switchy",
                "domain": "light",
            },
        )
        decision = build_deterministic_decision(info)
        self.assertEqual(decision.type, "final_answer")
        self.assertIn("jasności", (decision.message or "").lower())
        self.assertFalse(decision.is_tool_call)

    def test_domain_stays_light_not_climate(self):
        info = IntentClassification(
            family=IntentFamily.SMART_HOME,
            is_complete=True,
            slots={
                "implicit_light": True,
                "target": "light.vizzini",
                "domain": "light",
            },
        )
        decision = build_deterministic_decision(info)
        self.assertEqual(decision.arguments.get("domain"), "light")
        self.assertNotIn("climate", str(decision.arguments).lower())
        self.assertNotEqual(decision.tool, "set_temperature")


class AgentUnsupportedSpeechTests(unittest.TestCase):

    def test_never_fake_success_on_unsupported_obs(self):
        spoken = never_fake_success(
            "Gotowe.",
            [{
                "tool": "set_device_value",
                "success": False,
                "status": "unsupported",
                "message": "Nie obsługuję tej czynności dla Inspire Vizzini.",
            }],
        )
        self.assertIn("Nie obsługuję", spoken)
        self.assertNotEqual(spoken.strip().lower(), "gotowe.")

    def test_planner_reports_failure_not_success(self):
        reset_model_client()
        log = []
        registry = ToolRegistry(confirm_medium=False)

        def handler(args):
            log.append(dict(args))
            return ToolResult.unsupported(
                "Nie obsługuję tej czynności dla Inspire Vizzini.",
                tool_name="set_device_value",
            )

        registry.register(Tool(
            name="set_device_value",
            description="set",
            handler=handler,
            risk_level=RiskLevel.SAFE,
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "target": {"type": "string"},
                    "domain": {"type": "string"},
                    "value": {"type": "integer"},
                },
            },
        ))
        reset_registry_for_tests(registry)
        model = FakeModelClient([])
        planner = Planner(registry, model=model, max_steps=2)
        classification = IntentClassification(
            family=IntentFamily.SMART_HOME,
            is_complete=True,
            slots={
                "implicit_light": True,
                "target": "light.vizzini",
                "domain": "light",
            },
            raw_text="Trochę tu ciemno.",
        )
        # Directly run decision handling with unsupported tool result.
        from core.agent_types import AgentDecision

        decision = AgentDecision(
            type="tool_call",
            tool="set_device_value",
            arguments={
                "action": "adjust_brightness",
                "target": "light.vizzini",
                "domain": "light",
                "value": 15,
            },
        )
        result = planner._handle_decision(
            decision,
            goal="Trochę tu ciemno.",
            context={"intent_classification": classification},
            classification=classification,
            steps=[],
            observations=[],
            recent_fingerprints=[],
            max_repeats=2,
            confirmed=False,
            step_index=1,
            terminate_after_tool=True,
        )
        self.assertIsNotNone(result)
        self.assertFalse(result.success)
        self.assertIn("Nie obsługuję", result.message)
        obs = result.observations[-1]
        self.assertFalse(obs.get("success"))
        self.assertNotEqual(obs.get("status"), "success")
        reset_registry_for_tests(None)
        reset_model_client()


if __name__ == "__main__":
    unittest.main()
