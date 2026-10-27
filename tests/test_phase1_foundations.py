# ==========================
# ELIAN — Phase 1 foundations (WorldState / Salience / memory layers)
# ==========================
from __future__ import annotations

import time
import unittest

from core.capability_map import (
    CAPABILITY_IDS,
    DISABLED,
    UNAVAILABLE,
    snapshot_capabilities,
)
from core.collaboration import ActiveTaskContext
from core.salience import reset_salience_metrics, salience_metrics, select_salient
from core.semantic_executive import decide_semantics, fast_path_permitted
from core.world_state import (
    CURRENT,
    STALE,
    get_observation,
    get_world_state,
    note_computer,
    note_screen,
    observation_freshness,
    reset_world_state,
)
from memory.layers import PROJECT, WORKING, layers_from_manager, salient_layers
from memory.vocabulary import lookup_alias


class _Mem:
    def __init__(self):
        self.short = type(
            "S",
            (),
            {
                "turns": [{"role": "user", "text": "cześć"}],
                "topic": "AI",
                "focus": {"current_topic": "AI"},
                "entities": {},
                "recent_observations": [],
            },
        )()
        self.long = type("L", (), {"facts": [{"text": "Lubię krótko"}]})()
        self._extra_layers = {
            "projects": {"elian": {"goal": "master build"}},
            "task": {"goal": "audit"},
            "procedures": [],
        }


class Phase1WorldStateTests(unittest.TestCase):
    def setUp(self):
        reset_world_state()

    def tearDown(self):
        reset_world_state()

    def test_screen_observation_scrubs_pixels_and_is_current(self):
        note_screen(
            {
                "title": "Elian - Cursor",
                "process": "Cursor.exe",
                "pixels": b"not-allowed",
                "password": "secret",
            }
        )
        screen = get_world_state().get("screen") or {}
        self.assertNotIn("pixels", screen)
        self.assertNotIn("password", screen)
        self.assertEqual(screen.get("process"), "Cursor.exe")
        self.assertEqual(observation_freshness("screen"), CURRENT)
        obs = get_observation("screen")
        self.assertIsNotNone(obs)
        self.assertEqual(obs.freshness(now=obs.timestamp + 10.0), STALE)

    def test_public_world_state_keys_unchanged(self):
        note_computer({"hwnd": 1, "process": "notepad.exe"})
        self.assertEqual(
            set(get_world_state().keys()),
            {"services", "conversation", "active_plan", "smart_home", "computer", "screen"},
        )


class Phase1SalienceTests(unittest.TestCase):
    def setUp(self):
        reset_world_state()
        reset_salience_metrics()

    def tearDown(self):
        reset_world_state()
        reset_salience_metrics()

    def test_joke_does_not_inject_notepad_computer(self):
        note_computer({"hwnd": 9, "process": "notepad.exe", "title": "Bez tytułu — Notatnik"})
        env = decide_semantics("opowiedz mi żart", allow_llm=False)
        sel = select_salient("opowiedz mi żart", envelope=env)
        self.assertEqual(env.mode, "TALK")
        self.assertFalse(sel.include_computer)
        self.assertNotIn("computer", sel.fragments)
        self.assertEqual(salience_metrics()["irrelevant_context_injection_rate"], 0.0)

    def test_unchanged_hwnd_not_required_for_talk(self):
        env = decide_semantics("masz jakiegoś suchara?", allow_llm=False)
        self.assertFalse(env.needs_tools)
        self.assertFalse(fast_path_permitted("masz jakiegoś suchara?", env))


class Phase1EnvelopeFlagTests(unittest.TestCase):
    def test_hypothetical_flag_no_tools(self):
        env = decide_semantics("co się stanie jeśli włączysz światło?", allow_llm=False)
        self.assertTrue(env.hypothetical)
        self.assertFalse(env.needs_tools)
        self.assertIn(env.mode, ("ANSWER", "TALK", "SIMULATE"))

    def test_quoted_flag_no_fastpath(self):
        env = decide_semantics("powiedziałem mu: włącz światło", allow_llm=False)
        self.assertTrue(env.quoted)
        self.assertFalse(fast_path_permitted("powiedziałem mu: włącz światło", env))

    def test_wait_and_cancel_flags(self):
        wait = decide_semantics("poczekaj", allow_llm=False)
        self.assertTrue(wait.wait_requested)
        cancel = decide_semantics("anuluj", allow_llm=False)
        self.assertTrue(cancel.cancel_requested or cancel.intent == "cancel")


class Phase1MemoryLayerTests(unittest.TestCase):
    def test_layers_do_not_mix_unrelated_project(self):
        layers = layers_from_manager(_Mem(), project="vr glove")
        self.assertEqual(layers[PROJECT], {})
        elian = layers_from_manager(_Mem(), project="elian")
        self.assertEqual(elian[PROJECT].get("goal"), "master build")

    def test_salient_joke_keeps_working_memory_only(self):
        selected = salient_layers(_Mem(), utterance="opowiedz żart")
        self.assertIn(WORKING, selected)
        self.assertNotIn(PROJECT, selected)

    def test_vocabulary_alias_lookup(self):
        hits = lookup_alias("otwórz vizzini", vocab={"devices": ["vizzini"], "apps": []})
        self.assertEqual(hits["hits"][0]["name"], "vizzini")


class Phase1CapabilityTests(unittest.TestCase):
    def test_named_capabilities_exist(self):
        snap = snapshot_capabilities()
        for name in CAPABILITY_IDS:
            self.assertIn(name, snap)
        self.assertEqual(snap["Browser"].status, DISABLED)
        self.assertEqual(snap["Camera"].status, DISABLED)
        self.assertEqual(snap["MR"].status, DISABLED)


class Phase1TaskSchemaTests(unittest.TestCase):
    def test_active_task_has_resume_fields(self):
        task = ActiveTaskContext(task_id="t1", goal="x", semantic_goal="audit")
        data = task.to_dict()
        self.assertIn("resumable", data)
        self.assertIn("failed_steps", data)
        self.assertIn("expected_completion_condition", data)
        self.assertTrue(data["resumable"])

    def test_no_side_effects_from_foundations(self):
        reset_world_state()
        note_screen({"title": "x", "process": "Cursor.exe"})
        decide_semantics("opowiedz żart", allow_llm=False)
        self.assertNotIn("pixels", str(get_world_state()))


if __name__ == "__main__":
    unittest.main()
