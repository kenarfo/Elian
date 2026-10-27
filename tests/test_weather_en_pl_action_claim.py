# ==========================
# ELIAN — EN+PL weather routing + hard action_claim (Faza 2.3)
# ==========================
from __future__ import annotations

import unittest

from core.action_claim import (
    CLAIM_CHECK,
    detect_action_claim,
    guard_action_claim,
    honest_no_tool_message,
    honest_tool_outcome_message,
)
from core.read_intents import looks_weather
from core.risk import RiskLevel
from core.semantic_executive import decide_semantics
from core.tool_result import ToolResult
from core.tool_shortlist import shortlist_tools
from core.tools import Tool, ToolRegistry
from core.utterance_class import UtteranceClass, classify_utterance


class WeatherEnPlRoutingTests(unittest.TestCase):
    PL = (
        "jaka jest dzisiaj pogoda?",
        "sprawdź pogodę",
        "jaka będzie pogoda jutro?",
        "sprawdź pogodę dla Łodzi",
    )
    EN = (
        "what's the weather?",
        "check the weather for me",
        "what's the weather today?",
        "can you check the weather?",
        "Okay, so can you check the weather for me?",
    )

    def test_looks_weather_pl_en(self) -> None:
        for utt in self.PL + self.EN:
            self.assertTrue(looks_weather(utt), msg=utt)

    def test_semantic_weather_needs_tools(self) -> None:
        for utt in self.PL + self.EN:
            env = decide_semantics(utt, allow_llm=False)
            self.assertEqual(env.intent, "weather_status", msg=utt)
            self.assertTrue(env.needs_tools, msg=utt)
            self.assertNotEqual(env.mode, "TALK", msg=utt)

    def test_shortlist_includes_get_weather(self) -> None:
        catalog = [
            {"name": "get_weather", "risk": "read"},
            {"name": "open_app", "risk": "low"},
        ]
        for utt in ("sprawdź pogodę", "check the weather for me", "what's the weather?"):
            items = shortlist_tools(catalog, goal=utt)
            names = {str(item.get("name") or "") for item in items}
            self.assertIn("get_weather", names, msg=utt)

    def test_en_weather_invokes_tool_not_fake_claim(self) -> None:
        from core.fast_path import run_fast_path

        reg = ToolRegistry()

        def _weather(args):
            return ToolResult.ok(
                message="In Lodz it is 12 degrees and cloudy.", data={"temp": 12}
            )

        reg.register(
            Tool(
                name="get_weather",
                description="weather",
                handler=_weather,
                risk_level=RiskLevel.SAFE,
            )
        )
        env = decide_semantics("can you check the weather?", allow_llm=False)
        self.assertTrue(env.needs_tools)
        self.assertEqual(env.intent, "weather_status")
        outcome = run_fast_path(
            "can you check the weather?",
            reg,
            confirmed=True,
            envelope=env,
        )
        self.assertIsNotNone(outcome)
        msg = str(getattr(outcome, "message", "") or "")
        self.assertTrue(getattr(outcome, "success", False) or "12" in msg)


class WeatherFollowupDoItTests(unittest.TestCase):
    def test_okay_so_do_it_is_confirmation(self) -> None:
        cls = classify_utterance("Okay, so do it")
        self.assertEqual(cls.utterance_class, UtteranceClass.CONFIRMATION.value)

    def test_do_it_after_weather_reroutes_to_weather(self) -> None:
        first = decide_semantics(
            "Okay, so can you check the weather for me?", allow_llm=False
        )
        self.assertEqual(first.intent, "weather_status")
        follow = decide_semantics(
            "Okay, so do it",
            allow_llm=False,
            conversation={"last_intent": "weather_status", "last_mode": first.mode},
        )
        self.assertEqual(follow.intent, "weather_status")
        self.assertTrue(follow.needs_tools)


class HardActionClaimTests(unittest.TestCase):
    def test_no_toolresult_blocks_ill_check_weather(self) -> None:
        text = "I'll check the weather for you."
        self.assertEqual(detect_action_claim(text), CLAIM_CHECK)
        guarded = guard_action_claim(text, [])
        self.assertTrue(guarded.blocked)
        self.assertNotIn("I'll check", guarded.text)

    def test_tool_success_allows_claim(self) -> None:
        text = "I'll check the weather for you."
        obs = [{"tool": "get_weather", "success": True, "status": "ok"}]
        guarded = guard_action_claim(text, obs)
        self.assertFalse(guarded.blocked)
        self.assertEqual(guarded.text, text)

    def test_tool_failure_honest(self) -> None:
        text = "I checked the weather."
        obs = [{"tool": "get_weather", "success": False, "status": "error"}]
        guarded = guard_action_claim(text, obs)
        self.assertTrue(guarded.blocked)
        self.assertIn(
            guarded.text,
            {
                honest_tool_outcome_message("failed"),
                honest_no_tool_message(),
            },
        )

    def test_tool_timeout_honest(self) -> None:
        text = "I checked the weather."
        obs = [{"tool": "get_weather", "success": False, "status": "timeout"}]
        guarded = guard_action_claim(text, obs)
        self.assertTrue(guarded.blocked)
        self.assertEqual(guarded.text, honest_tool_outcome_message("timeout"))

    def test_tool_unavailable_honest(self) -> None:
        text = "I'm checking the weather."
        obs = [{"tool": "get_weather", "success": False, "status": "unavailable"}]
        guarded = guard_action_claim(text, obs)
        self.assertTrue(guarded.blocked)
        self.assertEqual(guarded.text, honest_tool_outcome_message("unavailable"))

    def test_missing_toolresult_blocks_polish_claim(self) -> None:
        text = "Sprawdzam pogodę."
        guarded = guard_action_claim(text, None)
        self.assertTrue(guarded.blocked)

    def test_wrong_tool_does_not_satisfy_check_claim(self) -> None:
        text = "I'll check the weather for you."
        obs = [{"tool": "open_app", "success": True}]
        guarded = guard_action_claim(text, obs)
        self.assertTrue(guarded.blocked)


if __name__ == "__main__":
    unittest.main()
