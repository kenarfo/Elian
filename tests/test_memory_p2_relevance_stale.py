# ==========================
# ELIAN — P2.2 Memory relevance / STALE WorldState
# ==========================
from __future__ import annotations

import os
import tempfile
import time
import unittest

from core.cognitive_core import (
    CandidateMemory,
    CognitiveState,
    assess_and_apply,
    invalidate_session_intention,
    promote_candidate_to_memory,
    upsert_candidate,
)
from core.context_selection import select_context
from core.world_state import (
    KNOWN,
    STALE,
    active_device_from_world,
    note_smart_home,
    reset_world_state,
    resolve_pronoun_referent,
    take_snapshot,
)
from memory.manager import MemoryManager
from memory.retrieval import is_retrieval_relevant, select_compact
from memory.types import MemoryType


class CandidateSessionOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = MemoryManager(path=os.path.join(self.tmp.name, "elian_memory.json"))
        self.state = CognitiveState()

    def tearDown(self):
        self.tmp.cleanup()

    def test_soft_uncertain_not_promoted(self):
        g = assess_and_apply(
            "chyba jutro pójdę do dentysty",
            state=self.state,
            memory=self.mem,
        )
        self.assertEqual(g.assessment.rationale_code, "TEMPORARY_FUTURE_INTENT")
        self.assertEqual(len(self.state.active_intentions()), 1)
        self.assertTrue(self.state.candidates)
        self.assertEqual(self.state.candidates[0].status, "candidate")
        self.assertEqual(
            self.mem.typed.all(include_session=True),
            [],
            "soft uncertain must stay session-only",
        )

    def test_promote_gate_rejects_unreinforced(self):
        cand = upsert_candidate(
            self.state,
            "wolę jazz",
            kind="preference",
            confidence=0.4,
        )
        self.assertEqual(cand.status, "candidate")
        self.assertIsNone(promote_candidate_to_memory(cand, self.mem, explicit=False))
        self.assertEqual(cand.status, "candidate")
        self.assertEqual(self.mem.typed.all(include_session=True), [])

    def test_promote_gate_rejects_soft_intention(self):
        cand = CandidateMemory(
            id="cand_x",
            text="jutro dentysta",
            kind="intention",
            status="reinforced",
            confidence=0.8,
            confirmations=3,
            created_at=time.time(),
            updated_at=time.time(),
            key="dentysta",
        )
        self.assertIsNone(promote_candidate_to_memory(cand, self.mem, explicit=False))
        self.assertEqual(self.mem.typed.all(include_session=True), [])

    def test_reinforced_preference_may_promote(self):
        assess_and_apply("wolę ciemny motyw", state=self.state, memory=self.mem)
        assess_and_apply("wolę ciemny motyw", state=self.state, memory=self.mem)
        rows = self.mem.typed.all(include_session=False) or []
        self.assertTrue(any(str(r.get("type")) == MemoryType.PREFERENCE.value for r in rows))


class StaleWorldStateResolveTests(unittest.TestCase):
    def setUp(self):
        reset_world_state()

    def tearDown(self):
        reset_world_state()

    def test_stale_active_referent_does_not_drive_pronoun(self):
        note_smart_home(
            {
                "entity_id": "light.vizzini",
                "domain": "light",
                "state": "on",
                "name": "Vizzini",
            }
        )
        snap = take_snapshot()
        ref = snap.fact("active_referent")
        self.assertIsNotNone(ref)
        self.assertEqual(ref.status, KNOWN)
        live = resolve_pronoun_referent("zgaś ją")
        self.assertIsNotNone(live)
        self.assertEqual(live.entity_id, "light.vizzini")

        stale_now = float(ref.timestamp) + 400.0  # > active_referent TTL (300s)
        snap_stale = take_snapshot(now=stale_now)
        self.assertEqual(snap_stale.fact("active_referent").status, STALE)
        # smart_home TTL is 15s — also STALE by then
        self.assertEqual(snap_stale.fact("smart_home").status, STALE)

        resolved = resolve_pronoun_referent("zgaś ją", now=stale_now)
        self.assertIsNone(
            resolved,
            "STALE active_referent/smart_home must not drive pronoun resolve",
        )
        self.assertIsNone(active_device_from_world(now=stale_now))

        sel = select_context("zgaś ją")
        # Live path still ok in setUp time; re-check with stale via resolve only above.
        # Context attach uses resolve without now — ensure live still works.
        self.assertEqual(
            (select_context("zgaś ją").fragments.get("active_referent") or {}).get(
                "entity_id"
            ),
            "light.vizzini",
        )

    def test_known_referent_still_resolves(self):
        note_smart_home(
            {
                "entity_id": "light.vizzini",
                "domain": "light",
                "state": "on",
                "name": "Vizzini",
            }
        )
        res = resolve_pronoun_referent("wyłącz to")
        self.assertIsNotNone(res)
        self.assertEqual(res.status, KNOWN)
        self.assertEqual(res.entity_id, "light.vizzini")
        dev = active_device_from_world()
        self.assertIsNotNone(dev)
        self.assertEqual(dev.entity_id, "light.vizzini")


class SoftIntentRelevanceFilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = MemoryManager(path=os.path.join(self.tmp.name, "elian_memory.json"))
        self.state = CognitiveState()

    def tearDown(self):
        self.tmp.cleanup()

    def test_change_of_mind_hides_soft_temporary_from_retrieval(self):
        # Simulate a TEMPORARY that slipped into typed store (pre-gate / legacy).
        written = self.mem.remember(
            type=MemoryType.TEMPORARY,
            key="intent.dentysta",
            value="jutro dentysta",
            text="chyba jutro pójdę do dentysty",
            explicit=False,
            confidence=0.4,
            provenance={"source": "cognitive_core", "candidate_id": "cand_legacy"},
        )
        self.assertTrue(written.ok)
        entry_id = (written.entry or {}).get("id")
        self.assertTrue(entry_id)

        assess_and_apply(
            "chyba jutro pójdę do dentysty",
            state=self.state,
            memory=self.mem,
        )
        intention = self.state.active_intentions()[0]
        # Link legacy typed id so invalidate can match.
        intention.linked_memory_ids.append(entry_id)

        g = assess_and_apply("zmieniłem zdanie", state=self.state, memory=self.mem)
        self.assertEqual(g.assessment.rationale_code, "CHANGE_OF_MIND")
        self.assertEqual(len(self.state.active_intentions()), 0)

        stored = self.mem.typed.get(entry_id)
        self.assertIsNotNone(stored)
        self.assertEqual(str(stored.get("status")), "invalidated")
        self.assertFalse(is_retrieval_relevant(stored))

        hits = self.mem.retrieve("dentyst", include_session=True) or []
        self.assertFalse(
            any(str(h.get("id")) == entry_id for h in hits),
            "invalidated soft TEMPORARY must not resurface via retrieve",
        )
        compact = select_compact(self.mem.typed, "dentysta jutro")
        self.assertFalse(
            any(str(e.get("id")) == entry_id for e in compact.get("entries") or []),
            "invalidated soft TEMPORARY must not resurface via select_compact",
        )

    def test_durable_fact_survives_soft_cancel_and_retrieves(self):
        self.mem.remember(
            type=MemoryType.FACT,
            key="fact.dentist.clinic",
            value="dentysta na Mokotowie",
            text="chodzę do dentysty na Mokotowie",
            explicit=True,
            confidence=1.0,
        )
        assess_and_apply(
            "chyba jutro pójdę do dentysty",
            state=self.state,
            memory=self.mem,
        )
        assess_and_apply("zmieniłem zdanie", state=self.state, memory=self.mem)
        hits = self.mem.retrieve("dentyst", limit=5) or []
        self.assertTrue(
            any("Mokotowie" in str(h.get("text") or h.get("value") or "") for h in hits),
            "durable FACT must still retrieve after soft CHANGE_OF_MIND",
        )


if __name__ == "__main__":
    unittest.main()
