# ==========================
# ELIAN — P0 #2 light resolution regression
# ==========================
"""resolve_light_target + soft light_off / brightness / domain switch."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.action_loop import run_soft_action_loop
from core.device_catalog import resolve_light_target
from core.goal_context import GoalContext
from core.soft_goals import detect_soft_goal
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from core.risk import RiskLevel


class _TraceReg(ToolRegistry):
    def __init__(self):
        super().__init__(confirm_medium=False)
        self.calls = []

    def invoke(self, name, arguments=None, *, confirmed: bool = False):
        args = dict(arguments or {})
        self.calls.append({"tool": name, "args": args})
        data = dict(args)
        if name in ("turn_on_device", "turn_off_device", "set_device_value"):
            data.setdefault("resolved_entity_id", args.get("entity_id") or "light.desk")
            data["ha_verify"] = {"verification": "SUCCESS"}
            data["ha_action"] = (
                "turn_off"
                if name == "turn_off_device"
                else ("turn_on" if name == "turn_on_device" else "adjust_brightness")
            )
            return ToolResult.ok("ok", tool_name=name, data=data)
        if name in ("volume_down", "volume_up"):
            return ToolResult.ok("ok", tool_name=name, data=args)
        return ToolResult.fail(name, "missing", error_code="TOOL_UNAVAILABLE")


def _reg():
    reg = _TraceReg()
    for name in (
        "turn_on_device",
        "turn_off_device",
        "set_device_value",
        "volume_down",
        "volume_up",
    ):
        reg.register(
            Tool(
                name=name,
                description=name,
                handler=lambda args: None,
                risk_level=RiskLevel.SAFE,
                parameters={"type": "object", "properties": {}},
            )
        )
    return reg


class LightResolutionP0Tests(unittest.TestCase):
    def test_one_known_light_binds(self):
        light = SimpleNamespace(domain="light", entity_id="light.only", name="Only")
        with patch("core.device_catalog._light_entities_from_registry", return_value=[light]):
            with patch("core.device_catalog.resolve_alias", return_value=None):
                with patch("core.device_catalog.default_fallback_entity", return_value=""):
                    eid, reason = resolve_light_target("ciemno tu", prefer_active=False)
        self.assertEqual(eid, "light.only")
        self.assertEqual(reason, "unique_registry")

    def test_multiple_lights_ambiguous(self):
        lights = [
            SimpleNamespace(domain="light", entity_id="light.a", name="A"),
            SimpleNamespace(domain="light", entity_id="light.b", name="B"),
        ]
        with patch("core.device_catalog._light_entities_from_registry", return_value=lights):
            with patch("core.device_catalog.resolve_alias", return_value=None):
                with patch("core.device_catalog.default_fallback_entity", return_value=""):
                    with patch(
                        "DeviceResolver.resolve_device",
                        return_value=SimpleNamespace(status="ambiguous", entity=None),
                    ):
                        eid, reason = resolve_light_target("światło", prefer_active=False)
        self.assertIsNone(eid)
        self.assertEqual(reason, "ambiguous")

    def test_explicit_alias(self):
        with patch("core.device_catalog.resolve_alias", return_value="light.alias"):
            eid, reason = resolve_light_target("vizzini", prefer_active=False)
        self.assertEqual(eid, "light.alias")
        self.assertEqual(reason, "alias")

    def test_prior_referent_active(self):
        mem = MagicMock()
        active = SimpleNamespace(domain="light", entity_id="light.active", name="Active")
        with patch("core.device_context.get_active_device", return_value=active):
            eid, reason = resolve_light_target("jaśniej", memory=mem, prefer_active=True)
        self.assertEqual(eid, "light.active")
        self.assertEqual(reason, "active")

    def test_no_referent_none(self):
        with patch("core.device_catalog._light_entities_from_registry", return_value=[]):
            with patch("core.device_catalog.resolve_alias", return_value=None):
                with patch("core.device_catalog.default_fallback_entity", return_value=""):
                    eid, reason = resolve_light_target("", prefer_active=False)
        self.assertIsNone(eid)
        self.assertEqual(reason, "none")

    def test_stale_referent_ignored_when_prefer_active_false(self):
        mem = MagicMock()
        active = SimpleNamespace(domain="light", entity_id="light.stale", name="Stale")
        with patch("core.device_context.get_active_device", return_value=active):
            with patch("core.device_catalog.resolve_alias", return_value=None):
                with patch("core.device_catalog._light_entities_from_registry", return_value=[]):
                    with patch("core.device_catalog.default_fallback_entity", return_value=""):
                        eid, reason = resolve_light_target(
                            "xyz", memory=mem, prefer_active=False
                        )
        self.assertIsNone(eid)
        self.assertIn(reason, ("none", "ambiguous"))

    def test_zgas_ja_soft_light_off(self):
        ctx = GoalContext(
            previous_goal={
                "soft_kind": "brightness_up",
                "intent_family": "SMART_HOME",
                "slots": {"entity_id": "light.desk", "resolved_context": True},
            },
            active_entities={"domain": "light", "entity_id": "light.desk"},
        )
        soft = detect_soft_goal("zgaś ją", goal_context=ctx)
        self.assertIsNotNone(soft)
        self.assertEqual(soft["kind"], "light_off")
        self.assertEqual(soft["slots"].get("entity_id"), "light.desk")

    def test_brightness_followup_nie_az_tak(self):
        ctx = GoalContext(
            previous_goal={
                "soft_kind": "brightness_up",
                "intent_family": "SMART_HOME",
                "slots": {"entity_id": "light.desk"},
            },
            active_entities={"domain": "light", "entity_id": "light.desk"},
        )
        soft = detect_soft_goal("nie aż tak", goal_context=ctx)
        self.assertIsNotNone(soft)
        self.assertEqual(soft["kind"], "brightness_down")
        self.assertTrue(soft["slots"].get("resolved_context"))

    def test_correction_followup_keeps_light_domain(self):
        ctx = GoalContext(
            previous_goal={
                "soft_kind": "brightness_up",
                "intent_family": "SMART_HOME",
                "objective": "Rozjaśnij — jest ciemno",
                "slots": {"entity_id": "light.desk", "resolved_context": True},
            },
            active_entities={"domain": "light", "entity_id": "light.desk"},
        )
        soft = detect_soft_goal(
            "jeszcze jaśniej",
            goal_context=ctx,
            envelope=SimpleNamespace(intent="smart_home_control", utterance_class="CHANGE_PARAMETER"),
        )
        self.assertIsNotNone(soft)
        self.assertEqual(soft["kind"], "brightness_up")

    def test_context_switch_light_to_volume(self):
        ctx = GoalContext(
            previous_goal={
                "soft_kind": "brightness_up",
                "intent_family": "SMART_HOME",
                "slots": {"entity_id": "light.desk"},
            },
            active_entities={"domain": "light", "entity_id": "light.desk"},
        )
        soft = detect_soft_goal("trochę ciszej", goal_context=ctx)
        self.assertIsNotNone(soft)
        self.assertEqual(soft["kind"], "volume_down")
        self.assertTrue(soft["slots"].get("clear_light_context"))

    def test_action_loop_asks_once_when_ambiguous(self):
        reg = _reg()
        soft = {
            "kind": "brightness_up",
            "intent_family": "SMART_HOME",
            "objective": "Rozjaśnij",
            "confidence": 0.9,
            "source": "test",
            "needs_clarification": True,
            "slots": {
                "domain": "light",
                "desired": "brighter",
                "implicit_light": True,
                "resolve_reason": "ambiguous",
            },
            "expected_result": {"kind": "brightness_up"},
        }
        with patch("core.action_loop.detect_soft_goal", return_value=soft):
            loop = run_soft_action_loop("ciemno tu", registry=reg, memory=None)
        self.assertTrue(loop.handled)
        self.assertTrue(loop.data.get("ask_once") or loop.data.get("needs_clarification"))
        self.assertIn("światło", (loop.message or "").casefold())
        self.assertFalse(reg.calls)

    def test_action_loop_executes_resolved_brightness(self):
        reg = _reg()
        soft = {
            "kind": "brightness_up",
            "intent_family": "SMART_HOME",
            "objective": "Rozjaśnij",
            "confidence": 0.9,
            "source": "test",
            "slots": {
                "entity_id": "light.desk",
                "resolved_context": True,
                "domain": "light",
                "desired": "brighter",
                "implicit_light": True,
            },
            "expected_result": {"kind": "brightness_up"},
            "target": "light.desk",
        }
        with patch("core.action_loop.detect_soft_goal", return_value=soft):
            loop = run_soft_action_loop("jeszcze jaśniej", registry=reg, memory=None)
        self.assertTrue(loop.handled)
        self.assertTrue(reg.calls)
        self.assertEqual(reg.calls[0]["tool"], "set_device_value")
        self.assertNotIn("Chwila", loop.message or "")
        self.assertNotEqual((loop.message or "").strip().lower(), "ok")


if __name__ == "__main__":
    unittest.main()
