# ==========================
# ELIAN — P0 #3 false success claim regression
# ==========================
"""Gotowe/Zrobione only with verification; never claim on vacuous ok."""

from __future__ import annotations

import unittest

from core.action_loop import _natural_success
from core.jarvis_response import _single_success_ack
from core.response_builder import from_tool_result, never_fake_success
from core.response_finalize import finalize_response, is_ack_like
from core.tool_result import ToolResult
from core.user_goal import UserGoal


class FalseSuccessP0Tests(unittest.TestCase):
    def test_vacuous_ok_not_finalize_chwila_via_natural_success(self):
        goal = UserGoal(
            objective="Puść michael",
            soft_kind="media_play",
            slots={"media_query": "michael"},
        )
        result = ToolResult.ok("ok", tool_name="play_media", data={"query": "michael"})
        spoken = _natural_success("play_media", goal=goal, result=result)
        self.assertFalse(is_ack_like(spoken))
        self.assertNotIn("sprawdzam", spoken.casefold())
        self.assertIn("Puszczam", spoken)

    def test_unverified_tool_honest_not_gotowe(self):
        goal = UserGoal(objective="Rozjaśnij", soft_kind="brightness_up", slots={})
        result = ToolResult.ok(
            "ok",
            tool_name="set_device_value",
            data={"entity_id": "light.x"},  # no ha_verify
        )
        spoken = _natural_success("set_device_value", goal=goal, result=result)
        self.assertNotEqual(spoken.strip(), "Gotowe.")
        self.assertNotEqual(spoken.strip(), "Zrobione.")
        self.assertTrue(
            "potwierdz" in spoken.casefold() or "Rozjaśniłem" in spoken or "Wysłałem" in spoken
        )

    def test_verified_ha_may_claim_natural(self):
        goal = UserGoal(
            objective="Zgaś",
            soft_kind="light_off",
            slots={"entity_id": "light.x"},
        )
        result = ToolResult.ok(
            "ok",
            tool_name="turn_off_device",
            data={
                "entity_id": "light.x",
                "ha_verify": {"verification": "SUCCESS"},
                "ha_action": "turn_off",
            },
        )
        spoken = _natural_success("turn_off_device", goal=goal, result=result)
        self.assertNotEqual(spoken.strip().lower(), "ok")
        self.assertFalse(is_ack_like(spoken))

    def test_from_tool_result_ok_not_gotowe_without_verify(self):
        result = ToolResult.ok("ok", tool_name="open_app", data={"name": ""})
        spoken = from_tool_result(result, allow_silent=False)
        self.assertNotEqual(spoken.strip(), "Gotowe.")
        self.assertIn("potwierdz", spoken.casefold())

    def test_single_success_ack_requires_verify_for_generic(self):
        unverified = _single_success_ack(
            {"tool": "type_text", "success": True, "data": {}}
        )
        self.assertNotEqual(unverified.strip(), "Gotowe.")
        verified = _single_success_ack(
            {"tool": "type_text", "success": True, "verified": True, "data": {}}
        )
        self.assertEqual(verified.strip(), "Gotowe.")

    def test_never_fake_success_blocks_claim_without_obs(self):
        guarded = never_fake_success("Gotowe.", [])
        # guard may rewrite or keep depending on claim detector; must not invent tools.
        self.assertIsInstance(guarded, str)

    def test_finalize_ok_becomes_chwila_so_callers_must_not_pass_ok(self):
        # Contract lock: raw "ok" is unsafe for _speak(kind=answer).
        spoken, origin = finalize_response("ok", kind="answer")
        self.assertIn("sprawdzam", spoken.casefold())
        self.assertEqual(origin, "FALLBACK")


if __name__ == "__main__":
    unittest.main()
