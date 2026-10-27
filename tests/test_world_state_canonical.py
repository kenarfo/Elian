# ==========================
# ELIAN — canonical WorldState (live snapshot)
# ==========================
from __future__ import annotations

import unittest

from core.context_selection import select_context
from core.events import Event, HA_UNAVAILABLE
from core.semantic_executive import DecisionEnvelope, refine_with_context
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from core.world_state import (
    CURRENT,
    KNOWN,
    STALE,
    UNAVAILABLE,
    apply_tool_result,
    compact_planner_view,
    compact_world_for_llm,
    get_world_state,
    ingest_event,
    journal_entries,
    mark_unavailable,
    note_media,
    note_smart_home,
    observation_freshness,
    observation_status,
    reset_world_state,
    resolve_pronoun_referent,
    take_snapshot,
)


class CanonicalWorldStateTests(unittest.TestCase):
    def setUp(self):
        reset_world_state()

    def tearDown(self):
        reset_world_state()

    def test_update_after_tool_execution(self):
        result = ToolResult.ok(
            "Wyłączono Vizzini.",
            tool_name="turn_off_device",
            data={
                "entity_id": "light.vizzini",
                "name": "Vizzini",
                "domain": "light",
                "state": "off",
            },
        )
        apply_tool_result("turn_off_device", {"entity_id": "light.vizzini"}, result)
        snap = take_snapshot()
        ha = snap.fact("smart_home")
        self.assertIsNotNone(ha)
        self.assertEqual(ha.status, KNOWN)
        self.assertEqual((ha.value or {}).get("light.vizzini", {}).get("state"), "off")
        referent = snap.fact("active_referent")
        self.assertEqual((referent.value or {}).get("entity_id"), "light.vizzini")
        actions = snap.fact("last_actions")
        self.assertTrue(actions)
        self.assertTrue(journal_entries())

    def test_update_after_registry_invoke(self):
        registry = ToolRegistry(confirm_medium=False)

        def _off(_arguments):
            return ToolResult.ok(
                "ok",
                tool_name="turn_off_device",
                data={
                    "entity_id": "light.vizzini",
                    "domain": "light",
                    "state": "off",
                    "name": "Vizzini",
                },
            )

        registry.register(
            Tool(
                name="turn_off_device",
                description="test",
                handler=_off,
                parameters={"type": "object", "properties": {"entity_id": {"type": "string"}}},
            )
        )
        registry.invoke("turn_off_device", {"entity_id": "light.vizzini"}, confirmed=True)
        self.assertEqual(
            (take_snapshot().fact("smart_home").value or {}).get("light.vizzini", {}).get("state"),
            "off",
        )

    def test_stale_data(self):
        note_media({"title": "Billie Jean"})
        self.assertEqual(observation_freshness("media"), CURRENT)
        self.assertEqual(observation_status("media"), KNOWN)
        snap = take_snapshot(now=take_snapshot().fact("media").timestamp + 20.0)
        self.assertEqual(snap.fact("media").status, STALE)
        self.assertIn("STALE", snap.compact_for_llm())

    def test_unavailable_integration(self):
        note_smart_home(
            {"entity_id": "light.vizzini", "domain": "light", "state": "on", "name": "Vizzini"}
        )
        ingest_event(Event(type=HA_UNAVAILABLE, source="ha_watcher", data={}))
        self.assertEqual(observation_freshness("smart_home"), UNAVAILABLE)
        self.assertEqual(take_snapshot().fact("smart_home").status, UNAVAILABLE)
        apply_tool_result(
            "get_now_playing",
            {},
            ToolResult.unavailable("Media niedostępne.", tool_name="get_now_playing"),
        )
        self.assertEqual(observation_freshness("media"), UNAVAILABLE)

    def test_conflict_resolution(self):
        note_smart_home(
            {"entity_id": "light.vizzini", "domain": "light", "state": "on", "name": "Vizzini"},
            source="LOCAL_HOME_ASSISTANT",
        )
        note_smart_home(
            {"entity_id": "light.vizzini", "domain": "light", "state": "off", "name": "Vizzini"},
            source="MODEL_KNOWLEDGE",
        )
        ha = take_snapshot().fact("smart_home")
        self.assertEqual((ha.value or {}).get("light.vizzini", {}).get("state"), "on")
        note_media({"title": "Real Track"}, source="LOCAL_RUNTIME")
        note_media({"title": "Hallucinated"}, source="MODEL_KNOWLEDGE")
        self.assertEqual((take_snapshot().fact("media").value or {}).get("title"), "Real Track")

    def test_ha_state(self):
        note_smart_home(
            {"entity_id": "light.vizzini", "domain": "light", "state": "on", "name": "Vizzini"}
        )
        public = get_world_state()
        self.assertIn("light.vizzini", public.get("smart_home") or {})
        self.assertEqual(observation_status("smart_home"), KNOWN)
        brief = compact_world_for_llm()
        self.assertIn("light.vizzini", brief)
        self.assertLess(len(brief), 800)
        view = compact_planner_view()
        self.assertEqual(view.get("source"), "world_state")
        self.assertIn("smart_home", view)
        self.assertLess(len(str(view)), 4000)

    def test_media_state(self):
        apply_tool_result(
            "play_media",
            {"query": "Michael Jackson"},
            ToolResult.ok(
                "Odtwarzam: Michael Jackson.",
                tool_name="play_media",
                data={"query": "Michael Jackson", "app": "spotify"},
            ),
        )
        media = take_snapshot().fact("media")
        self.assertEqual(media.status, KNOWN)
        self.assertIn("Michael Jackson", str((media.value or {}).get("query") or (media.value or {}).get("title")))

    def test_pronoun_resolution(self):
        note_smart_home(
            {"entity_id": "light.vizzini", "domain": "light", "state": "on", "name": "Vizzini"}
        )
        zgas = resolve_pronoun_referent("zgaś ją")
        self.assertIsNotNone(zgas)
        self.assertEqual(zgas.entity_id, "light.vizzini")
        self.assertEqual(zgas.kind, "device")
        wylacz = resolve_pronoun_referent("wyłącz to")
        self.assertEqual(wylacz.entity_id, "light.vizzini")
        tamto = resolve_pronoun_referent("wyłącz tamto")
        self.assertEqual(tamto.entity_id, "light.vizzini")
        ciszej = resolve_pronoun_referent("daj ciszej")
        self.assertEqual(ciszej.kind, "volume")
        env = DecisionEnvelope(
            mode="ACT",
            intent="close_referent",
            user_goal="zgaś ją",
            context_requested=True,
            continuation=True,
            needs_tools=True,
        )
        refined = refine_with_context(env, {"utterance": "zgaś ją"})
        self.assertIn("light.vizzini", refined.referents)
        sel = select_context("zgaś ją")
        self.assertEqual((sel.fragments.get("active_referent") or {}).get("entity_id"), "light.vizzini")

    def test_snapshot_immutability(self):
        note_smart_home(
            {"entity_id": "light.vizzini", "domain": "light", "state": "on", "name": "Vizzini"}
        )
        snap = take_snapshot()
        mutated = snap.to_dict()
        mutated["facts"]["smart_home"]["value"]["light.vizzini"]["state"] = "hacked"
        value = dict(snap.fact("smart_home").value)
        value["light.vizzini"]["state"] = "hacked"
        later = take_snapshot()
        self.assertEqual(
            (later.fact("smart_home").value or {}).get("light.vizzini", {}).get("state"),
            "on",
        )
        with self.assertRaises(Exception):
            snap.facts["smart_home"] = snap.fact("smart_home")  # mappingproxy is immutable

    def test_stage_b_public_keys_unchanged(self):
        note_smart_home({"entity_id": "light.vizzini", "domain": "light", "state": "on"})
        self.assertEqual(
            set(get_world_state().keys()),
            {"services", "conversation", "active_plan", "smart_home", "computer", "screen"},
        )

    def test_does_not_invent_unknown_ha(self):
        snap = take_snapshot()
        self.assertIsNone(snap.fact("smart_home"))
        self.assertNotIn("light.vizzini", compact_world_for_llm())
        mark_unavailable("calendar", source="LOCAL_FILE")
        self.assertEqual(observation_status("calendar"), UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
