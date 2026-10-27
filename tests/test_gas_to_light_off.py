# ==========================
# ELIAN — colloquial "gaś to" (not only "zgaś")
# ==========================
from __future__ import annotations

import unittest

from core.device_pronoun import detect_device_action_hint
from core.semantic_executive import decide_semantics
from core.soft_goals import detect_soft_goal
from core.text_inject import _make_manager, run_one_turn


class GasToLightOffTests(unittest.TestCase):
    def test_device_hint_gas_to(self):
        self.assertEqual(detect_device_action_hint("gaś to"), "turn_off")
        self.assertEqual(detect_device_action_hint("gaś to w tarasie"), "turn_off")
        self.assertEqual(detect_device_action_hint("zgaś ją"), "turn_off")

    def test_soft_gas_to(self):
        soft = detect_soft_goal("gaś to")
        self.assertIsNotNone(soft)
        self.assertEqual(soft.get("kind"), "light_off")

    def test_semantic_gas_not_mere_talk(self):
        env = decide_semantics(
            "gaś to",
            allow_llm=False,
            conversation={"recent_domain": "SMART_HOME", "active_device": {"domain": "light"}},
        )
        # Prefer ACT; at minimum must not be empty-chat REFERENCE without tools when domain known.
        self.assertTrue(
            env.mode == "ACT" or env.needs_tools or env.intent == "smart_home_control",
            msg=f"mode={env.mode} intent={env.intent} needs={env.needs_tools}",
        )

    def test_inject_gas_to_after_light_on(self):
        mgr = _make_manager(live_tools=False)
        try:
            run_one_turn(mgr, "włącz lampę", debug=False)
            row = run_one_turn(mgr, "gaś to", debug=False)
            tools = row.get("TOOL") or row.get("tools") or []
            names = [
                str(t.get("tool") or "")
                for t in tools
                if isinstance(t, dict)
            ]
            text = str(row.get("FINAL_RESPONSE") or row.get("text") or "").lower()
            self.assertEqual(str(row.get("soft_kind") or ""), "light_off")
            self.assertTrue(
                "turn_off_device" in names
                or "wyłącz" in text
                or "światło" in text
                or "zgas" in text,
                msg=f"tools={names} text={text[:160]!r}",
            )
            self.assertNotIn("które światło", text)
            self.assertNotIn("jasne, słucham", text)
            self.assertNotIn("nic nie robię", text)
            self.assertNotIn("według źródeł", text)
        finally:
            try:
                mgr.deactivate(reason="test_end")
            except Exception:
                pass

    def test_wygas_and_odetnij_prad(self):
        self.assertEqual(detect_device_action_hint("wygaś to"), "turn_off")
        soft = detect_soft_goal("odetnij prąd od lampy")
        self.assertEqual(soft and soft.get("kind"), "light_off")
        mgr = _make_manager(live_tools=False)
        try:
            run_one_turn(mgr, "włącz lampę", debug=False)
            for utter in ("wygaś to", "odetnij prąd od lampy"):
                row = run_one_turn(mgr, utter, debug=False)
                text = str(row.get("FINAL_RESPONSE") or row.get("text") or "").lower()
                self.assertEqual(
                    str(row.get("soft_kind") or ""),
                    "light_off",
                    msg=f"{utter!r} soft={row.get('soft_kind')!r} text={text[:120]!r}",
                )
                self.assertNotIn("według źródeł", text)
                self.assertNotIn("jasne, słucham", text)
        finally:
            try:
                mgr.deactivate(reason="test_end")
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
