# ==========================
# ELIAN — Phase 13 C.5.4 AI Semantic Executive
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.active_service import (
    KIND_APP,
    ActiveServiceContext,
    reset_active_service,
    set_active_service,
)
from core.builtin_tools import build_default_registry
from core.compound_plan import parse_compound_goal
from core.context_selection import select_context
from core.conversation_manager import ConversationManager
from core.fast_path import FastPathStatus, evaluate_fast_path
from core.followup_policy import (
    apply_followup_policy,
    has_generic_closer,
    should_offer_followup,
    strip_generic_closers,
)
from core.jarvis_response import polish_spoken, social_reply
from core.model_client import FakeModelClient, reset_model_client
from core.planner import Planner
from core.semantic_eval import all_eval_cases, run_behavioral_eval
from core.semantic_executive import (
    CONFIDENCE_STRONG,
    CONFIDENCE_USABLE,
    DecisionEnvelope,
    decide_semantics,
    fallback_when_unavailable,
    fast_path_permitted,
    validate_envelope,
)
from core.semantic_talk import humor_reply, talk_reply
from core.tools import reset_registry_for_tests
from memory.manager import MemoryManager


class Phase13C54SemanticExecutiveTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        reset_active_service()

    def tearDown(self):
        reset_active_service()
        reset_registry_for_tests(None)
        reset_model_client()

    def test_joke_is_talk_zero_tools(self):
        env = decide_semantics("Opowiedz mi jakiś żart.", allow_llm=False)
        self.assertEqual(env.mode, "TALK")
        self.assertEqual(env.intent, "creative_humor")
        self.assertFalse(env.needs_tools)
        self.assertEqual(env.allowed_domains, [])

    def test_smalltalk(self):
        env = decide_semantics("Co tam?", allow_llm=False)
        self.assertEqual(env.mode, "TALK")
        self.assertEqual(env.intent, "social_smalltalk")
        self.assertFalse(env.needs_tools)

    def test_screen_observe(self):
        env = decide_semantics("Co teraz widzisz na ekranie?", allow_llm=False)
        self.assertEqual(env.mode, "OBSERVE")
        self.assertTrue(env.requires_fresh_observation)
        self.assertIn("READ_ONLY_SCREEN", env.allowed_domains)

    def test_media_plan(self):
        env = decide_semantics("Otwórz Spotify i puść Billie Jean.", allow_llm=False)
        self.assertEqual(env.mode, "PLAN")
        self.assertTrue(env.needs_tools)
        self.assertTrue(set(env.allowed_domains) & {"APP", "MEDIA"})

    def test_action_vs_discussion(self):
        act = decide_semantics("Otwórz Spotify.", allow_llm=False)
        how = decide_semantics("Jak otworzyć Spotify?", allow_llm=False)
        can = decide_semantics("Czy umiesz otworzyć Spotify?", allow_llm=False)
        hypo = decide_semantics("Co się stanie, jak otworzysz Spotify?", allow_llm=False)
        dont = decide_semantics("Nie otwieraj Spotify.", allow_llm=False)
        quoted = decide_semantics("Powiedziałem mu: otwórz Spotify.", allow_llm=False)
        self.assertEqual(act.mode, "ACT")
        self.assertTrue(act.needs_tools)
        for env in (how, can, hypo, dont, quoted):
            self.assertIn(env.mode, ("ANSWER", "TALK"))
            self.assertFalse(env.needs_tools)

    def test_spotify_context_does_not_invent_joke_intent(self):
        env = decide_semantics(
            "Opowiedz żart.",
            runtime_context={"active_service": {"service_name": "Spotify"}},
            allow_llm=False,
        )
        self.assertEqual(env.mode, "TALK")
        self.assertEqual(env.intent, "creative_humor")
        self.assertFalse(env.needs_tools)
        self.assertFalse(env.context_requested)
        sel = select_context(
            "Opowiedz żart.",
            envelope=env,
        )
        self.assertFalse(sel.include_service)

    def test_close_him_uses_context_after_pass1(self):
        env = decide_semantics(
            "Zamknij go.",
            runtime_context={"active_service": {"service_name": "Spotify", "resolved_kind": "APP"}},
            allow_llm=False,
        )
        self.assertEqual(env.mode, "ACT")
        self.assertTrue(env.context_requested)
        self.assertIn("Spotify", env.referents or env.user_goal)

    def test_fastpath_volume_ok_but_not_question(self):
        self.assertTrue(fast_path_permitted("głośniej"))
        self.assertFalse(fast_path_permitted("czy możesz powiedzieć, co znaczy głośniej?"))
        self.assertEqual(
            evaluate_fast_path("czy możesz powiedzieć, co znaczy głośniej?").status,
            FastPathStatus.NOT_HANDLED,
        )

    def test_schema_validation_rejects_prose(self):
        with self.assertRaises(ValueError):
            validate_envelope({"mode": "explode", "confidence": 1})
        env = validate_envelope(
            {"mode": "TALK", "intent": "x", "confidence": 0.95, "needs_tools": True}
        )
        self.assertFalse(env.needs_tools)

    def test_weak_confidence_no_side_effect(self):
        env = validate_envelope(
            {
                "mode": "ACT",
                "intent": "open_app",
                "confidence": 0.4,
                "needs_tools": True,
                "allowed_domains": ["APP"],
            }
        )
        self.assertEqual(env.mode, "ASK")
        self.assertFalse(env.needs_tools)
        self.assertGreaterEqual(CONFIDENCE_STRONG, CONFIDENCE_USABLE)

    def test_unavailable_fallback_not_spotify(self):
        env = fallback_when_unavailable("hmm no nie wiem")
        self.assertFalse(env.needs_tools)
        self.assertNotIn("APP", env.allowed_domains)

    def test_planner_joke_with_spotify_active_zero_tools(self):
        reset_active_service()
        set_active_service(
            ActiveServiceContext(
                service_name="Spotify",
                resolved_kind=KIND_APP,
                capabilities=["OPEN", "CLOSE"],
            )
        )
        calls = []
        registry = build_default_registry(confirm_medium=False)
        original = registry.invoke

        def _wrap(name, args=None, **kwargs):
            calls.append(name)
            return original(name, args, **kwargs)

        registry.invoke = _wrap
        planner = Planner(registry, model=FakeModelClient([]))
        result = planner.run(
            "opowiedz żart",
            context={"active_service": {"service_name": "Spotify"}},
        )
        self.assertEqual(calls, [])
        self.assertIn(result.source, ("talk", "social"))
        self.assertTrue(result.message)
        self.assertNotIn("nie rozumiem", (result.message or "").lower())
        self.assertNotIn("spotify", (result.message or "").lower())

    def test_cm_joke_does_not_open_spotify(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = []
        registry = build_default_registry(confirm_medium=False)
        original = registry.invoke

        def _wrap(name, args=None, **kwargs):
            log.append(name)
            return original(name, args, **kwargs)

        registry.invoke = _wrap
        reset_active_service()
        set_active_service(
            ActiveServiceContext(service_name="Spotify", resolved_kind=KIND_APP)
        )
        mgr = ConversationManager(
            registry=registry,
            memory_manager=MemoryManager(Path(tmp.name) / "m.json"),
            memory=[],
            model=FakeModelClient([]),
        )
        mgr.activate()
        result = mgr.handle("Opowiedz żart.")
        self.assertNotIn("open_app", log)
        self.assertNotIn("play_media", log)
        self.assertTrue(result.should_speak)
        low = (result.text or "").lower()
        self.assertNotIn("nie rozumiem", low)
        self.assertNotIn("nie znam takiej komendy", low)
        self.assertFalse(has_generic_closer(result.text or ""))

    def test_mixed_joke_and_volume_plan(self):
        plan = parse_compound_goal("Powiedz mi żart i potem zwiększ głośność.")
        self.assertTrue(plan.detected)
        self.assertGreaterEqual(len(plan.steps), 2)
        families = [step.family for step in plan.steps]
        self.assertIn("KNOWLEDGE", families)
        self.assertIn("SYSTEM", families)

    def test_followup_not_on_volume(self):
        env = DecisionEnvelope(mode="ACT", intent="system_volume", user_goal="głośniej")
        self.assertFalse(should_offer_followup(env, goal="Zwiększ głośność."))
        spoken, offered = apply_followup_policy(
            "Zwiększyłem głośność. Czy mogę pomóc w czymś jeszcze?",
            env,
            goal="Zwiększ głośność.",
        )
        self.assertFalse(offered)
        self.assertFalse(has_generic_closer(spoken))

    def test_followup_allowed_for_document(self):
        env = DecisionEnvelope(mode="ACT", intent="open_app", user_goal="Otwórz dokument z planem.")
        self.assertTrue(should_offer_followup(env, goal="Otwórz dokument z planem."))

    def test_style_no_tool_jargon(self):
        spoken = polish_spoken("LOCAL_COMPUTER ToolResult status=success tool=type_text")
        low = spoken.lower()
        self.assertNotIn("local_computer", low)
        self.assertNotIn("toolresult", low)

    def test_polish_spoken_keeps_natural_narzedzie(self):
        spoken = polish_spoken(
            "W skrócie: Piper to lokalne narzędzie opisane w dokumentacji."
        )
        self.assertIn("narzędzie", spoken.lower())
        self.assertNotIn("jasno", spoken.lower())
        leak = polish_spoken("Uruchomiłem narzędzie turn_on_device.")
        self.assertIn("jasno", leak.lower())

    def test_humor_reply_varies_by_seed_but_is_polish(self):
        a = humor_reply("żart")
        b = humor_reply("suchar")
        self.assertTrue(a)
        self.assertNotEqual(a.lower(), "nie rozumiem.")
        self.assertTrue(any(ch in (a + b) for ch in "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ") or "programista" in a.lower() or "żart" in a.lower() or "bit" in a.lower())

    def test_behavioral_eval_minimum_650(self):
        cases = all_eval_cases()
        self.assertGreaterEqual(len(cases), 650)
        det = [c for c in cases if c.category == "deterministic"]
        poison = [c for c in cases if c.category == "context-poison"]
        semantic = [c for c in cases if c.category == "semantic-nl"]
        self.assertGreaterEqual(len(det), 250)
        self.assertGreaterEqual(len(poison), 250)
        self.assertGreaterEqual(len(semantic), 150)
        summary = run_behavioral_eval()
        self.assertGreaterEqual(summary["total"], 650)
        self.assertEqual(summary["failed"], 0, summary["failures"][:12])
        self.assertGreaterEqual(summary.get("proactive_cases") or 0, 50)
        self.assertGreaterEqual(summary.get("multi_turn_scenarios") or 0, 30)

    def test_generic_callcenter_closer_stripped(self):
        self.assertEqual(strip_generic_closers("Gotowe. Czy mogę pomóc w czymś jeszcze?"), "Gotowe.")
        self.assertEqual(social_reply("co tam?"), "Spokojnie. Jestem, słucham.")


if __name__ == "__main__":
    unittest.main()
