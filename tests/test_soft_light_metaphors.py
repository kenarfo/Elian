# ==========================
# ELIAN — soft light metaphors
# ==========================
from __future__ import annotations

import unittest

from core.intent_family import looks_insufficient_light
from core.jarvis_behavioral.harness import jarvis_session
from core.semantic_executive import decide_semantics
from core.soft_goals import detect_soft_goal


class SoftLightMetaphorTests(unittest.TestCase):
    def test_darkness_metaphors_are_light_goals(self):
        for phrase in ("w tarasie jest jak w jaskini", "w sypialni jak w piwnicy"):
            self.assertTrue(looks_insufficient_light(phrase), msg=phrase)
            soft = detect_soft_goal(phrase)
            self.assertIsNotNone(soft, msg=phrase)
            self.assertEqual(soft.get("kind"), "brightness_up", msg=phrase)

    def test_english_room_dark_is_brightness_up(self):
        for phrase in (
            "a bit dark in here",
            "it's too dark",
            "can't see",
            "like a cave",
        ):
            self.assertTrue(looks_insufficient_light(phrase), msg=phrase)
            soft = detect_soft_goal(phrase)
            self.assertIsNotNone(soft, msg=phrase)
            self.assertEqual(soft.get("kind"), "brightness_up", msg=phrase)
            self.assertEqual(soft.get("intent_family"), "SMART_HOME", msg=phrase)

    def test_turn_up_the_volume_is_not_lighting(self):
        """Regression: 'turn up' must not match light need without 'lights'."""
        from core.intent_family import classify_intent_family, looks_want_brighter

        phrase = "turn up the volume"
        self.assertFalse(looks_want_brighter(phrase))
        soft = detect_soft_goal(phrase)
        self.assertIsNotNone(soft)
        self.assertEqual(soft.get("kind"), "volume_up")
        fam = classify_intent_family(phrase)
        self.assertEqual(fam.family.value, "SYSTEM")

    def test_talk_locked_darkness_metaphor_still_softens_to_light(self):
        env = decide_semantics("w tarasie jest jak w jaskini", allow_llm=False)
        soft = detect_soft_goal("w tarasie jest jak w jaskini", envelope=env)
        self.assertIsNotNone(soft)
        self.assertEqual(soft.get("kind"), "brightness_up")

    def test_session_darkness_metaphor_not_empty_chat(self):
        with jarvis_session() as (cm, _backend, log):
            out = cm.handle("w tarasie jest jak w jaskini")
            tools = [r.get("tool") for r in log]
            text = (getattr(out, "text", None) or str(out)).casefold()
            self.assertTrue(
                "set_device_value" in tools or "turn_on_device" in tools,
                msg=f"tools={tools} text={text!r}",
            )
            self.assertNotIn("jasne, słucham", text)


if __name__ == "__main__":
    unittest.main()
