# ==========================
# ELIAN — ReminderRule rehydrate / cold-disable regression
# ==========================
"""create → save → fresh MemoryManager+CognitiveState → rehydrate → disable → no ROUTINE."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.cognitive_core import (
    CognitiveState,
    assess_and_apply,
    rehydrate_reminder_rules_from_memory,
)
from memory.manager import MemoryManager, reset_memory_manager
from memory.types import MemoryType


class ReminderRehydrateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "elian_memory_rehydrate.json"
        reset_memory_manager(None)

    def tearDown(self):
        reset_memory_manager(None)
        self.tmp.cleanup()

    def _active_routines(self, mem: MemoryManager, needle: str = "ładowar"):
        entries = mem.typed.all(include_session=False) or []
        out = []
        for e in entries:
            if str(e.get("type") or "") != MemoryType.ROUTINE.value:
                continue
            if str(e.get("status") or "active") not in ("", "active", "enabled"):
                continue
            blob = f"{e.get('text') or ''} {e.get('value') or ''}".lower()
            if needle.lower() in blob or needle.casefold() in blob.casefold():
                out.append(e)
        return out

    def test_rehydrate_then_cold_disable_clears_routine(self):
        mem = MemoryManager(path=self.path)
        state = CognitiveState()
        g1 = assess_and_apply(
            "przypominaj mi rano o ładowarce",
            state=state,
            memory=mem,
        )
        self.assertEqual(g1.assessment.rationale_code, "RECURRING_REMINDER_RULE")
        self.assertGreaterEqual(len(state.enabled_rules()), 1)
        self.assertGreaterEqual(len(self._active_routines(mem)), 1)
        mem.save()

        reset_memory_manager(None)
        mem2 = MemoryManager(path=self.path)
        state2 = CognitiveState()
        self.assertEqual(len(state2.enabled_rules()), 0)
        loaded = rehydrate_reminder_rules_from_memory(state2, mem2)
        self.assertGreaterEqual(len(loaded), 1)
        self.assertGreaterEqual(len(state2.enabled_rules()), 1)

        g2 = assess_and_apply(
            "już nie przypominaj mi o ładowarce",
            state=state2,
            memory=mem2,
        )
        self.assertEqual(g2.assessment.rationale_code, "DISABLE_REMINDER_RULE")
        self.assertEqual(len(state2.enabled_rules()), 0)
        self.assertEqual(len(self._active_routines(mem2)), 0)
        mem2.save()

        reset_memory_manager(None)
        mem3 = MemoryManager(path=self.path)
        state3 = CognitiveState()
        rehydrate_reminder_rules_from_memory(state3, mem3)
        self.assertEqual(len(state3.enabled_rules()), 0)
        self.assertEqual(len(self._active_routines(mem3)), 0)

    def test_cold_disable_without_prior_rehydrate_clears_routine(self):
        mem = MemoryManager(path=self.path)
        state = CognitiveState()
        assess_and_apply(
            "przypominaj mi rano o ładowarce",
            state=state,
            memory=mem,
        )
        mem.save()

        reset_memory_manager(None)
        mem2 = MemoryManager(path=self.path)
        cold = CognitiveState()  # no rehydrate
        self.assertEqual(len(cold.enabled_rules()), 0)
        self.assertGreaterEqual(len(self._active_routines(mem2)), 1)

        g = assess_and_apply(
            "już nie przypominaj mi o ładowarce",
            state=cold,
            memory=mem2,
        )
        self.assertEqual(g.assessment.rationale_code, "DISABLE_REMINDER_RULE")
        self.assertNotIn("nie mam", (g.short_circuit_reply or "").lower())
        self.assertEqual(len(self._active_routines(mem2)), 0)
        self.assertEqual(len(cold.enabled_rules()), 0)

    def test_soft_intent_not_auto_reminder(self):
        mem = MemoryManager(path=self.path)
        state = CognitiveState()
        g = assess_and_apply(
            "jutro chyba pójdę do sklepu",
            state=state,
            memory=mem,
        )
        self.assertIn(
            g.assessment.rationale_code,
            ("TEMPORARY_FUTURE_INTENT", "UNCERTAIN_FUTURE"),
        )
        self.assertEqual(len(state.enabled_rules()), 0)
        routines = [
            e
            for e in (mem.typed.all(include_session=False) or [])
            if str(e.get("type") or "") == MemoryType.ROUTINE.value
        ]
        self.assertEqual(len(routines), 0)


if __name__ == "__main__":
    unittest.main()
