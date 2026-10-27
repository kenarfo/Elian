# ==========================
# ELIAN — "nie aż tak" reverses last soft nudge
# ==========================
from __future__ import annotations

import unittest

from core.jarvis_behavioral.harness import jarvis_session
from core.soft_goals import detect_soft_goal


class _Ctx:
    def __init__(self, previous_goal, active=None):
        self.previous_goal = previous_goal
        self.active_entities = active or {"domain": "light", "entity_id": "light.desk"}
        self.media_active = False
        self.previous_tool = ""
        self.unresolved_task = {}
        self.pending_executable = None
        self.previous_result = {}
        self.last_journal_action = None


class SoftNieAzTakReverseTests(unittest.TestCase):
    def test_after_brightness_down_brightens(self):
        soft = detect_soft_goal(
            "nie aż tak",
            goal_context=_Ctx(
                {
                    "soft_kind": "brightness_down",
                    "intent_family": "SMART_HOME",
                    "objective": "Przyciemnij — za jasno",
                    "slots": {"desired": "darker"},
                }
            ),
        )
        self.assertIsNotNone(soft)
        self.assertEqual(soft.get("kind"), "brightness_up")

    def test_after_brightness_up_dims(self):
        soft = detect_soft_goal(
            "nie aż tak",
            goal_context=_Ctx(
                {
                    "soft_kind": "brightness_up",
                    "intent_family": "SMART_HOME",
                    "objective": "Dostosuj jasność światła",
                    "slots": {"desired": "brighter"},
                }
            ),
        )
        self.assertIsNotNone(soft)
        self.assertEqual(soft.get("kind"), "brightness_down")

    def test_after_volume_up_scisza(self):
        soft = detect_soft_goal(
            "nie aż tak",
            goal_context=_Ctx(
                {
                    "soft_kind": "volume_up",
                    "intent_family": "SYSTEM",
                    "objective": "Podgłośnij",
                },
                active={},
            ),
        )
        self.assertIsNotNone(soft)
        self.assertEqual(soft.get("kind"), "volume_down")

    def test_session_za_jasno_then_reverse(self):
        with jarvis_session() as (cm, _backend, log):
            cm.handle("za jasno")
            log.clear()
            out = cm.handle("nie aż tak")
            tools = [r.get("tool") for r in log]
            text = (getattr(out, "text", None) or str(out)).casefold()
            self.assertIn("set_device_value", tools)
            self.assertNotIn("przyciemniłem", text)
            self.assertTrue(
                any(tok in text for tok in ("jaśniej", "jasniej", "rozjaśn", "zwiększ")),
                msg=f"text={text!r} tools={tools}",
            )


if __name__ == "__main__":
    unittest.main()
