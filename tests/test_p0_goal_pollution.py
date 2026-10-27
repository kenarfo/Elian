# ==========================
# ELIAN — P0 #1 goal pollution regression
# ==========================
"""PRIMARY vs DISCOURSE separation across cancel / revive / choice / domain B."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from core.active_goal import ConversationGoalStore, GoalStatus
from core.semantic_executive import decide_semantics
from core.utterance_class import classify_utterance


def _observe(store: ConversationGoalStore, text: str, *, conversation=None):
    conv = conversation if conversation is not None else store.semantic_conversation()
    env = decide_semantics(text, conversation=conv, allow_llm=False)
    cls = classify_utterance(text, conversation=conv)
    goal = store.observe_utterance(text, envelope=env, classification=cls)
    return env, goal


class GoalPollutionP0Tests(unittest.TestCase):
    def test_choice_does_not_replace_primary(self):
        store = ConversationGoalStore()
        _observe(store, "wyszukaj coś o michaelu")
        primary = store.current.objective
        self.assertTrue(primary)
        store.set_presented_options(["A", "B"])
        env, goal = _observe(store, "nie, drugi")
        self.assertEqual(goal.objective, primary)
        self.assertNotIn("correct", (goal.objective or "").casefold())
        self.assertEqual(store.discourse.get("kind"), "choice")
        self.assertFalse(
            ConversationGoalStore._is_choice_boilerplate(env.user_goal or "")
        )

    def test_cancel_niewazne_clears_discourse(self):
        store = ConversationGoalStore()
        _observe(store, "otwórz Spotify")
        store.set_presented_options(["pierwszy", "drugi"])
        self.assertTrue(store.discourse)
        store.mark_cancelled()
        self.assertEqual(store.current.status, GoalStatus.CANCELLED.value)
        self.assertEqual(store.discourse, {})

    def test_revive_uses_primary_not_discourse(self):
        store = ConversationGoalStore()
        _observe(store, "wyszukaj coś o jarvis")
        primary = store.current.objective
        store.set_presented_options(["wynik A", "wynik B"])
        store.mark_result(
            success=False,
            action="web_search",
            result={"success": False, "reason": "timeout", "tool": "web_search"},
            reason="timeout",
        )
        self.assertTrue(store.current.is_unresolved)
        env, goal = _observe(store, "jeszcze tego nie zrobiłeś")
        self.assertEqual(goal.objective, primary)
        self.assertNotIn("correct", (goal.objective or "").casefold())
        self.assertNotIn("choice", (goal.objective or "").casefold())

    def test_unrelated_goal_b_not_polluted_by_a(self):
        store = ConversationGoalStore()
        _observe(store, "wyszukaj coś o lampie")
        store.set_presented_options(["A", "B"])
        _observe(store, "nie, drugi")
        env, goal = _observe(store, "trochę ciszej")
        self.assertNotIn("correct", (goal.objective or "").casefold())
        # Fresh volume / system act may soft-replace primary.
        low = (goal.objective or "").casefold()
        self.assertTrue(
            "ciszej" in low
            or "głoś" in low
            or "glos" in low
            or "ścisz" in low
            or "scisz" in low
            or "volume" in low
            or goal.domain in ("SYSTEM", "SMART_HOME", "WEB", "")
        )

    def test_pass2_correction_without_retarget_keeps_primary_text(self):
        store = ConversationGoalStore()
        _observe(store, "odpal Discord")
        primary = store.current.objective
        conv = store.semantic_conversation()
        env = decide_semantics(
            "chodziło mi o to",
            conversation=conv,
            allow_llm=False,
        )
        # No named retarget → must not invent English choice boilerplate.
        self.assertNotIn("correct the previous choice", (env.user_goal or "").casefold())
        self.assertNotIn("correct or resolve", (env.user_goal or "").casefold())
        goal = store.observe_utterance(
            "chodziło mi o to",
            envelope=env,
            classification=classify_utterance("chodziło mi o to", conversation=conv),
        )
        self.assertTrue(goal.objective)
        self.assertNotIn("correct", (goal.objective or "").casefold())
        # Primary should remain the open APP goal or raw correction text — not boilerplate.
        self.assertTrue(
            goal.objective == primary
            or "discord" in (goal.objective or "").casefold()
            or "chodziło" in (goal.objective or "").casefold()
            or "to" in (goal.objective or "").casefold()
        )

    def test_cancel_blocks_soft_resume_previous(self):
        """P0#1 rule E: after nieważne, soft must not revive last_user_goal."""
        from core.goal_context import GoalContext
        from core.soft_goals import detect_soft_goal

        prev = {
            "objective": "wyszukaj coś o michaelu",
            "soft_kind": "web_search",
            "intent_family": "WEB",
            "status": "CANCELLED",
        }
        ctx = GoalContext(previous_goal=prev)
        self.assertIsNone(detect_soft_goal("zrób to", goal_context=ctx))
        self.assertIsNone(detect_soft_goal("jeszcze tego nie zrobiłeś", goal_context=ctx))
        # Empty previous after cancel cleanup — no bare resume.
        empty = GoalContext(previous_goal={})
        self.assertIsNone(detect_soft_goal("spróbuj jeszcze", goal_context=empty))

    def test_cm_soft_cancel_clears_last_user_goal(self):
        from core.conversation_manager import ConversationManager
        from core.model_client import FakeModelClient, reset_model_client
        from core.tools import ToolRegistry, reset_registry_for_tests
        from core.user_goal import UserGoal

        registry = ToolRegistry(confirm_medium=True)
        reset_registry_for_tests(registry)
        model = FakeModelClient([])
        try:
            mgr = ConversationManager(registry=registry, memory=[], model=model)
            mgr.activate()
            mgr._user_goal = UserGoal(
                objective="wyszukaj coś o jarvis",
                soft_kind="web_search",
                intent_family="WEB",
            )
            mgr._soft_goal_cancelled = False
            result = mgr.handle("dobra nieważne")
            self.assertIsNone(mgr._user_goal)
            self.assertTrue(mgr._soft_goal_cancelled)
            self.assertTrue(
                "odpuszcz" in (result.text or "").casefold()
                or "ok" in (result.text or "").casefold()
                or "dobra" in (result.text or "").casefold()
                or result.source in ("action_loop", "collab", "cognitive")
            )
            # Next soft context must not carry web_search.
            last = None
            try:
                if mgr._user_goal is not None and hasattr(mgr._user_goal, "to_dict"):
                    last = mgr._user_goal.to_dict()
            except Exception:
                last = None
            self.assertIsNone(last)
        finally:
            reset_registry_for_tests(None)
            reset_model_client()


if __name__ == "__main__":
    unittest.main()
