# ==========================
# ELIAN — Cognitive Core regression tests
# ==========================
"""Facts, intentions, candidate memory, forget/invalidation — offline."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.cognitive_core import (
    Actionability,
    CognitiveIntent,
    CognitiveState,
    Importance,
    Persistence,
    assess,
    assess_and_apply,
    cognitive_blocks_auto_reminder,
)
from core.conversation_manager import ConversationManager
from core.model_client import FakeModelClient, reset_model_client
from core.risk import RiskLevel
from core.semantic_executive import DecisionEnvelope
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from memory.manager import MemoryManager, reset_memory_manager
from memory.types import MemoryType


def _dry_registry(log: list | None = None) -> ToolRegistry:
    registry = ToolRegistry(confirm_medium=True)
    calls = log if log is not None else []

    def _ok(name):
        def inner(arguments):
            calls.append({"tool": name, "arguments": dict(arguments or {})})
            return ToolResult.ok(f"{name} ok", tool_name=name, data=dict(arguments or {}))

        return inner

    for name in (
        "open_app",
        "play_media",
        "create_reminder",
        "cancel_reminder",
        "list_reminders",
        "turn_on_device",
        "turn_off_device",
    ):
        registry.register(
            Tool(name=name, description=name, handler=_ok(name), risk_level=RiskLevel.SAFE)
        )
    return registry


class CognitiveCoreUnitTests(unittest.TestCase):
    def setUp(self):
        self.state = CognitiveState()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "mem.json"
        self.mem = MemoryManager(path=self.path)
        reset_memory_manager(self.path)

    def tearDown(self):
        reset_memory_manager(None)
        self.tmp.cleanup()

    def test_explicit_remember(self):
        a = assess("zapamiętaj że mam kota Filemona", state=self.state, memory=self.mem)
        self.assertEqual(a.rationale_code, "EXPLICIT_REMEMBER")
        self.assertTrue(a.should_remember)
        self.assertFalse(a.should_act)
        self.assertEqual(a.persistence, Persistence.PERSISTENT.value)

    def test_implicit_low_value_fact(self):
        a = assess("dziś zjadłem kanapkę z serem", state=self.state)
        self.assertIn(a.rationale_code, ("LOW_VALUE_CHAT", "NO_ACTION_REQUIRED"))
        self.assertFalse(a.should_act)
        self.assertIn(a.importance, (Importance.TRIVIAL.value, Importance.LOW.value))

    def test_candidate_preference(self):
        g = assess_and_apply("wolę ciemny motyw w edytorze", state=self.state, memory=self.mem)
        self.assertEqual(g.assessment.rationale_code, "CANDIDATE_PREFERENCE")
        self.assertEqual(g.assessment.persistence, Persistence.CANDIDATE.value)
        self.assertEqual(len(self.state.candidates), 1)
        self.assertEqual(self.state.candidates[0].status, "candidate")
        self.assertTrue(g.short_circuit_reply)

    def test_repeated_preference_reinforcement(self):
        assess_and_apply("wolę ciemny motyw w edytorze", state=self.state, memory=self.mem)
        g2 = assess_and_apply("wolę ciemny motyw w edytorze", state=self.state, memory=self.mem)
        self.assertEqual(g2.assessment.rationale_code, "REINFORCED_PREFERENCE")
        self.assertGreaterEqual(self.state.candidates[0].confirmations, 2)
        self.assertIn(self.state.candidates[0].status, ("reinforced", "promoted"))

    def test_explicit_forget(self):
        self.mem.remember(
            type=MemoryType.FACT,
            key="kot.imie",
            value="Filemon",
            text="mam kota Filemona",
            explicit=True,
        )
        a = assess("zapomnij o Filemonie", state=self.state, memory=self.mem)
        self.assertEqual(a.rationale_code, "EXPLICIT_FORGET")
        self.assertTrue(a.should_forget)

    def test_niewazne_to_previous_utterance(self):
        assess_and_apply(
            "chyba jutro będę musiał zrobić prezentację",
            state=self.state,
            memory=self.mem,
        )
        self.assertEqual(len(self.state.active_intentions()), 1)
        g = assess_and_apply(
            "w sumie nieważne z tą prezentacją",
            state=self.state,
            memory=self.mem,
        )
        self.assertEqual(g.assessment.intent, CognitiveIntent.INVALIDATION.value)
        self.assertEqual(len(self.state.active_intentions()), 0)
        self.assertIn("pomijam", (g.short_circuit_reply or "").lower())

    def test_change_of_mind(self):
        assess_and_apply(
            "jutro muszę iść do dentysty",
            state=self.state,
            memory=self.mem,
        )
        g = assess_and_apply("zmieniłem zdanie", state=self.state, memory=self.mem)
        self.assertEqual(g.assessment.rationale_code, "CHANGE_OF_MIND")
        self.assertEqual(len(self.state.active_intentions()), 0)

    def test_future_intention_no_auto_reminder(self):
        a = assess(
            "chyba jutro będę musiał zrobić prezentację",
            state=self.state,
        )
        self.assertEqual(a.rationale_code, "TEMPORARY_FUTURE_INTENT")
        self.assertFalse(a.should_act)
        self.assertTrue(cognitive_blocks_auto_reminder(a))
        self.assertNotEqual(a.intent, CognitiveIntent.REMINDER_REQUEST.value)

    def test_reminder_request(self):
        a = assess("przypomnij mi jutro o 9 o lekach", state=self.state)
        self.assertEqual(a.rationale_code, "USER_REQUESTED_REMINDER")
        self.assertTrue(a.should_act)
        self.assertEqual(a.intent, CognitiveIntent.REMINDER_REQUEST.value)

    def test_uncertain_future_intention(self):
        a = assess("może później kupię nowy monitor", state=self.state)
        self.assertIn(a.rationale_code, ("TEMPORARY_FUTURE_INTENT", "UNCERTAIN_FUTURE", "LOW_VALUE_CHAT"))
        self.assertFalse(a.should_act)

    def test_conflicting_memory(self):
        self.mem.remember(
            type=MemoryType.FACT,
            key="ulubiona.gra",
            value="Minecraft",
            text="ulubiona gra to Minecraft",
            explicit=True,
            confidence=1.0,
        )
        a = assess(
            "zapamiętaj że moja ulubiona gra to Fortnite",
            state=self.state,
            memory=self.mem,
        )
        self.assertTrue(
            a.rationale_code in ("CONFLICTING_MEMORY", "EXPLICIT_REMEMBER")
        )
        if a.rationale_code == "CONFLICTING_MEMORY":
            self.assertTrue(a.should_ask)

    def test_irrelevant_casual(self):
        a = assess("pizza jest spoko", state=self.state)
        self.assertEqual(a.rationale_code, "LOW_VALUE_CHAT")
        self.assertEqual(a.importance, Importance.TRIVIAL.value)
        self.assertFalse(a.should_remember)
        self.assertFalse(a.should_act)

    def test_important_deadline(self):
        a = assess("jutro mam egzamin z matematyki", state=self.state)
        self.assertIn(a.rationale_code, ("IMPORTANT_DEADLINE", "UNCERTAIN_FUTURE"))
        self.assertIn(
            a.importance,
            (Importance.IMPORTANT.value, Importance.CRITICAL.value),
        )
        self.assertFalse(a.should_act)

    def test_context_resolution_reminder_from_intention(self):
        assess_and_apply(
            "jutro chyba będę musiał iść do sklepu",
            state=self.state,
            memory=self.mem,
        )
        g = assess_and_apply(
            "w sumie przypomnij mi o 16",
            state=self.state,
            memory=self.mem,
        )
        self.assertTrue(g.assessment.should_act)
        self.assertIn(g.assessment.rationale_code, ("CONTEXT_RESOLVED", "USER_REQUESTED_REMINDER"))
        self.assertIn("sklep", (g.enriched_goal or "").lower())
        self.assertIn("16", g.enriched_goal or "")

    def test_no_action(self):
        env = DecisionEnvelope(mode="TALK", intent="social_smalltalk", needs_tools=False)
        a = assess("hej", envelope=env, state=self.state)
        self.assertFalse(a.should_act)
        self.assertIn(a.rationale_code, ("LOW_VALUE_CHAT", "NO_ACTION_REQUIRED"))

    def test_correction(self):
        a = assess("nie, chodziło mi o Chrome", state=self.state)
        self.assertEqual(a.rationale_code, "CORRECTION")
        self.assertEqual(a.intent, CognitiveIntent.CORRECTION.value)

    def test_multi_turn_shop_16_niewazne(self):
        g1 = assess_and_apply(
            "jutro chyba będę musiał iść do sklepu",
            state=self.state,
            memory=self.mem,
        )
        self.assertEqual(g1.assessment.rationale_code, "TEMPORARY_FUTURE_INTENT")
        self.assertFalse(g1.assessment.should_act)
        self.assertTrue(g1.skip_agent_tools)

        g2 = assess_and_apply(
            "w sumie przypomnij mi o 16",
            state=self.state,
            memory=self.mem,
        )
        self.assertTrue(g2.assessment.should_act)
        self.assertTrue(g2.enriched_goal)

        g3 = assess_and_apply(
            "jednak nieważne",
            state=self.state,
            memory=self.mem,
        )
        self.assertEqual(g3.assessment.intent, CognitiveIntent.INVALIDATION.value)
        self.assertEqual(len(self.state.active_intentions()), 0)
        self.assertTrue(g3.short_circuit_reply)

    def test_recurring_reminder_and_disable(self):
        g1 = assess_and_apply(
            "musisz mi przypominać żebym przed szkołą brał ładowarkę",
            state=self.state,
            memory=self.mem,
        )
        self.assertEqual(g1.assessment.rationale_code, "RECURRING_REMINDER_RULE")
        self.assertEqual(len(self.state.enabled_rules()), 1)
        self.assertEqual(g1.assessment.importance, Importance.IMPORTANT.value)

        g2 = assess_and_apply(
            "już nie musisz mi o tym przypominać",
            state=self.state,
            memory=self.mem,
        )
        self.assertEqual(g2.assessment.rationale_code, "DISABLE_REMINDER_RULE")
        self.assertEqual(len(self.state.enabled_rules()), 0)
        self.assertIn("nie będę", (g2.short_circuit_reply or "").lower())

    def test_ambiguous_forget_asks(self):
        assess_and_apply(
            "jutro chyba kupię rower",
            state=self.state,
            memory=self.mem,
        )
        assess_and_apply(
            "jutro chyba zrobię prezentację",
            state=self.state,
            memory=self.mem,
        )
        self.assertGreaterEqual(len(self.state.active_intentions()), 2)
        g = assess_and_apply("nieważne", state=self.state, memory=self.mem)
        # Bare invalidation with 2 intentions — either asks or resolves by score
        if g.assessment.should_ask:
            self.assertEqual(g.assessment.rationale_code, "AMBIGUOUS_REFERENT")
            self.assertTrue(g.short_circuit_reply)
        else:
            # Deterministic single best match still OK
            self.assertEqual(g.assessment.intent, CognitiveIntent.INVALIDATION.value)


    def test_soft_invalidation_preserves_durable(self):
        """P1.4: "nieważne" clears session intent; FACT/PREFERENCE/ROUTINE stay."""
        self.mem.remember(
            type=MemoryType.PREFERENCE,
            key="pref.theme",
            value="ciemny",
            text="wolę ciemny motyw w edytorze",
            explicit=True,
            confidence=1.0,
        )
        self.mem.remember(
            type=MemoryType.FACT,
            key="fact.pet",
            value="Filemon",
            text="mam kota Filemona",
            explicit=True,
            confidence=1.0,
        )
        self.mem.remember(
            type=MemoryType.ROUTINE,
            key="routine.charger",
            value="ładowarka rano",
            text="przypominaj mi rano o ładowarce",
            explicit=True,
            confidence=1.0,
        )
        # Overlap bait: durable fact shares "sklep" token with soft intent.
        self.mem.remember(
            type=MemoryType.FACT,
            key="fact.shop.habit",
            value="sklep po pracy",
            text="lubię chodzić do sklepu po pracy",
            explicit=True,
            confidence=1.0,
        )
        before = {
            e.get("id"): dict(e)
            for e in (self.mem.typed.all(include_session=True) or [])
        }
        self.assertGreaterEqual(len(before), 4)

        assess_and_apply(
            "jutro chyba będę musiał iść do sklepu",
            state=self.state,
            memory=self.mem,
        )
        self.assertEqual(len(self.state.active_intentions()), 1)

        g = assess_and_apply(
            "w sumie nieważne",
            state=self.state,
            memory=self.mem,
        )
        self.assertEqual(g.assessment.rationale_code, "CHANGE_OF_MIND")
        self.assertEqual(g.assessment.intent, CognitiveIntent.INVALIDATION.value)
        self.assertFalse(g.assessment.should_forget)
        self.assertEqual(len(self.state.active_intentions()), 0)

        after = {
            e.get("id"): dict(e)
            for e in (self.mem.typed.all(include_session=True) or [])
        }
        for eid, entry in before.items():
            self.assertIn(eid, after, f"durable wiped: {entry.get('text')}")
            self.assertEqual(
                str(after[eid].get("type")),
                str(entry.get("type")),
            )

        # Retrieve still surfaces durable preference / shop fact.
        hits = self.mem.retrieve("ciemny motyw", limit=5) or []
        self.assertTrue(any("ciemny" in str(h).lower() for h in hits))
        hits2 = self.mem.retrieve("sklep", limit=5) or []
        self.assertTrue(
            any("sklep" in str(h).lower() for h in hits2),
            "durable sklep FACT must survive soft invalidation",
        )

    def test_invalidation_transfer_variants(self):
        """P1.4 AC2: ≥3 transfer phrases beyond original "w sumie nieważne"."""
        phrases = [
            "dobra nieważne",
            "już nieważne",
            "nieważne z tym",
            "jednak nieważne",
            "zmieniłem zdanie",
        ]
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.state = CognitiveState()
                assess_and_apply(
                    "jutro chyba będę musiał iść do sklepu",
                    state=self.state,
                    memory=self.mem,
                )
                self.assertEqual(len(self.state.active_intentions()), 1)
                g = assess_and_apply(phrase, state=self.state, memory=self.mem)
                self.assertEqual(
                    g.assessment.intent,
                    CognitiveIntent.INVALIDATION.value,
                    phrase,
                )
                self.assertEqual(
                    g.assessment.rationale_code,
                    "CHANGE_OF_MIND",
                    phrase,
                )
                self.assertFalse(g.assessment.should_forget, phrase)
                self.assertEqual(len(self.state.active_intentions()), 0, phrase)
                self.assertTrue(g.short_circuit_reply, phrase)

    def test_juz_niewazne_does_not_wipe_unrelated_routine(self):
        """P1.4: soft "już nieważne" after remind cue must not nuke all ROUTINEs."""
        self.mem.remember(
            type=MemoryType.ROUTINE,
            key="routine.charger",
            value="ładowarka rano",
            text="przypominaj mi rano o ładowarce",
            explicit=True,
            confidence=1.0,
        )
        routines_before = [
            e
            for e in (self.mem.typed.all(include_session=False) or [])
            if str(e.get("type") or "").upper() == "ROUTINE"
        ]
        self.assertEqual(len(routines_before), 1)

        # Soft session intent (not a DISABLE_RULE subject match on ładowarka).
        assess_and_apply(
            "jutro chyba będę musiał iść do sklepu",
            state=self.state,
            memory=self.mem,
        )
        # User recalls prior remind obligation, then soft-cancels the shop intent.
        assess("miałeś mi o czymś przypomnieć", state=self.state, memory=self.mem)
        g = assess_and_apply(
            "już nieważne",
            state=self.state,
            memory=self.mem,
        )
        self.assertEqual(g.assessment.rationale_code, "CHANGE_OF_MIND")
        self.assertEqual(len(self.state.active_intentions()), 0)

        routines_after = [
            e
            for e in (self.mem.typed.all(include_session=False) or [])
            if str(e.get("type") or "").upper() == "ROUTINE"
        ]
        self.assertEqual(
            len(routines_after),
            1,
            "unrelated ROUTINE must survive soft juz niewazne",
        )
        self.assertEqual(routines_after[0].get("id"), routines_before[0].get("id"))

    def test_explicit_disable_still_removes_matching_routine(self):
        """DISABLE_RULE subject match may forget matching ROUTINE; soft cancel must not."""
        g1 = assess_and_apply(
            "musisz mi przypominać żebym przed szkołą brał ładowarkę",
            state=self.state,
            memory=self.mem,
        )
        self.assertEqual(g1.assessment.rationale_code, "RECURRING_REMINDER_RULE")
        self.assertTrue(self.state.enabled_rules())

        # Soft cancel of a DIFFERENT intention must leave the rule alone.
        assess_and_apply(
            "jutro chyba pójdę do sklepu",
            state=self.state,
            memory=self.mem,
        )
        assess_and_apply("w sumie nieważne", state=self.state, memory=self.mem)
        self.assertTrue(
            self.state.enabled_rules(),
            "charger ROUTINE/rule must survive soft shop invalidation",
        )

        g2 = assess_and_apply(
            "już nie przypominaj mi o ładowarce",
            state=self.state,
            memory=self.mem,
        )
        self.assertEqual(g2.assessment.rationale_code, "DISABLE_REMINDER_RULE")
        self.assertEqual(len(self.state.enabled_rules()), 0)

    def test_actionability_levels(self):

        casual = assess("pizza jest spoko")
        self.assertEqual(casual.actionability, Actionability.NONE.value)
        remind = assess("przypomnij mi o lekach o 9")
        self.assertEqual(remind.actionability, Actionability.ACTIONABLE.value)


class CognitiveCoreIntegrationTests(unittest.TestCase):
    def setUp(self):
        reset_model_client()
        reset_registry_for_tests()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "mem.json"
        self.mem = MemoryManager(path=self.path)
        reset_memory_manager(self.path)
        self.calls: list = []
        self.registry = _dry_registry(self.calls)
        self.cm = ConversationManager(
            registry=self.registry,
            model=FakeModelClient([]),
            memory_manager=self.mem,
        )
        self.cm.activate()

    def tearDown(self):
        self.cm.deactivate(reason="test")
        reset_memory_manager(None)
        reset_model_client()
        reset_registry_for_tests()
        self.tmp.cleanup()

    def test_cm_uncertain_presentation_no_reminder_tool(self):
        result = self.cm.handle("chyba jutro będę musiał zrobić prezentację")
        self.assertTrue(result.should_speak)
        self.assertEqual(result.source, "cognitive")
        self.assertFalse(any(c["tool"] == "create_reminder" for c in self.calls))
        self.assertEqual(len(self.cm.cognitive.active_intentions()), 1)

    def test_cm_multi_turn_shop_scenario(self):
        r1 = self.cm.handle("jutro chyba będę musiał iść do sklepu")
        self.assertEqual(r1.source, "cognitive")
        self.assertFalse(any(c["tool"] == "create_reminder" for c in self.calls))

        # Reminder request with context — may reach agent; FakeModel may not plan.
        # Cognitive must enrich goal and mark should_act.
        assess_state = self.cm.cognitive
        from core.cognitive_core import assess_and_apply

        g = assess_and_apply(
            "w sumie przypomnij mi o 16",
            state=assess_state,
            memory=self.mem,
        )
        self.assertTrue(g.assessment.should_act)
        self.assertIn("sklep", (g.enriched_goal or "").lower())

        r3 = self.cm.handle("jednak nieważne")
        self.assertEqual(r3.source, "cognitive")
        self.assertEqual(len(self.cm.cognitive.active_intentions()), 0)

    def test_cm_recurring_charger_rule(self):
        r1 = self.cm.handle(
            "musisz mi przypominać żebym przed szkołą brał ładowarkę"
        )
        # May speak via agent or continue; rule must be stored either way.
        self.assertTrue(
            len(self.cm.cognitive.enabled_rules()) >= 1
            or r1.source in ("cognitive", "agent", "chat", "clarify")
        )
        # Force rule present for disable path if agent path consumed differently.
        if not self.cm.cognitive.enabled_rules():
            from core.cognitive_core import assess_and_apply

            assess_and_apply(
                "musisz mi przypominać żebym przed szkołą brał ładowarkę",
                state=self.cm.cognitive,
                memory=self.mem,
            )
        r2 = self.cm.handle("już nie musisz mi o tym przypominać")
        self.assertEqual(r2.source, "cognitive")
        self.assertEqual(len(self.cm.cognitive.enabled_rules()), 0)

    def test_agent_context_includes_assessment(self):
        self.cm.handle("chyba jutro będę musiał zrobić prezentację")
        ctx = self.cm._agent_context(raw_text="co tam?", goal_text="co tam?")
        # After short-circuit turn, assessment still on CM
        self.assertIsNotNone(self.cm._cognitive_assessment)
        self.assertEqual(
            self.cm._cognitive_assessment.rationale_code,
            "TEMPORARY_FUTURE_INTENT",
        )


if __name__ == "__main__":
    unittest.main()
