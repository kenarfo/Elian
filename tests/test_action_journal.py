# ==========================
# ELIAN — Action Journal + multi-level undo
# ==========================
from __future__ import annotations

import unittest
from unittest.mock import patch

from core.action_journal import (
    EntryStatus,
    get_journal,
    record_action,
    reset_action_history,
    undo_last_action,
)
from core.fast_path import FastPathStatus, evaluate_fast_path, run_fast_path
from core.read_intents import looks_undo, undo_count
from core.semantic_executive import decide_semantics, fast_path_permitted
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests


class FakeRegistry:
    def __init__(self, fail_tools=None):
        self.calls = []
        self.fail_tools = set(fail_tools or ())

    def invoke(self, name, args, confirmed=False, **_kwargs):
        self.calls.append((str(name), dict(args or {}), bool(confirmed)))
        if str(name) in self.fail_tools:
            return ToolResult.fail("Nie udało się.", tool_name=str(name))
        return ToolResult.ok("ok", tool_name=str(name), data=dict(args or {}))


class ActionJournalTests(unittest.TestCase):
    def setUp(self):
        reset_action_history()
        self.journal = get_journal()
        self.live = {}
        self.journal.set_state_reader(self._read_live)
        self.registry = FakeRegistry()

    def tearDown(self):
        reset_action_history()
        reset_registry_for_tests(None)

    def _read_live(self, entry):
        key = str(
            (entry.after_state or {}).get("entity_id")
            or (entry.before_state or {}).get("entity_id")
            or entry.target
            or entry.action_id
        )
        if key in self.live:
            return dict(self.live[key])
        if entry.action_id in self.live:
            return dict(self.live[entry.action_id])
        return dict(entry.after_state or {}) if entry.after_state else None

    def _record_volume(self):
        return self.journal.record(
            "volume_up",
            {},
            result=ToolResult.ok("ok", tool_name="volume_up"),
        )

    def test_single_undo(self):
        entry = self._record_volume()
        self.assertIsNotNone(entry)
        outcome = self.journal.undo(1, registry=self.registry)
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.undid, [entry.action_id])
        self.assertEqual(self.registry.calls[0][0], "volume_down")
        stored = self.journal.get(entry.action_id)
        self.assertEqual(stored.status, EntryStatus.UNDONE.value)

    def test_multi_undo(self):
        first = self.journal.record(
            "volume_up", {}, result=ToolResult.ok("ok", tool_name="volume_up")
        )
        second = self.journal.record(
            "pause_media", {}, result=ToolResult.ok("ok", tool_name="pause_media")
        )
        third = self.journal.record(
            "volume_down", {}, result=ToolResult.ok("ok", tool_name="volume_down")
        )
        outcome = self.journal.undo(3, registry=self.registry)
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.undid, [third.action_id, second.action_id, first.action_id])
        tools = [row[0] for row in self.registry.calls]
        self.assertEqual(tools, ["volume_up", "resume_media", "volume_down"])

    def test_already_undone(self):
        entry = self._record_volume()
        first = self.journal.undo(action_id=entry.action_id, registry=self.registry)
        self.assertTrue(first.success)
        second = self.journal.undo(action_id=entry.action_id, registry=self.registry)
        self.assertFalse(second.success)
        self.assertIn(entry.action_id, second.already_undone)
        self.assertEqual(len(self.registry.calls), 1)

    def test_irreversible_action(self):
        self.journal.record(
            "send_message",
            {"text": "cześć"},
            result=ToolResult.ok("wysłane", tool_name="send_message"),
        )
        outcome = self.journal.undo(1, registry=self.registry)
        self.assertFalse(outcome.success)
        self.assertTrue(outcome.skipped_irreversible)
        self.assertEqual(self.registry.calls, [])
        result = undo_last_action()
        self.assertFalse(result.success)

    def test_irreversible_not_silent_success_in_window(self):
        volume = self._record_volume()
        self.journal.record(
            "send_message",
            {"text": "x"},
            result=ToolResult.ok("wysłane", tool_name="send_message"),
        )
        paused = self.journal.record(
            "pause_media",
            {},
            result=ToolResult.ok("pauza", tool_name="pause_media"),
        )
        outcome = self.journal.undo(3, registry=self.registry)
        self.assertTrue(outcome.success)
        self.assertIn(paused.action_id, outcome.undid)
        self.assertIn(volume.action_id, outcome.undid)
        self.assertTrue(outcome.skipped_irreversible)
        self.assertIn("nie umiem cofnąć", outcome.message.lower())

    def test_external_state_conflict(self):
        entry = self.journal.record(
            "set_device_value",
            {
                "action": "light_brightness",
                "entity_id": "light.vizzini",
                "value": 20,
            },
            result=ToolResult.ok(
                "ok",
                tool_name="set_device_value",
                data={"entity_id": "light.vizzini", "before": "on", "after": "on"},
            ),
            before_state={
                "kind": "ha",
                "entity_id": "light.vizzini",
                "state": "on",
                "brightness": 80,
            },
            after_state={
                "kind": "ha",
                "entity_id": "light.vizzini",
                "state": "on",
                "brightness": 20,
            },
        )
        self.live["light.vizzini"] = {
            "kind": "ha",
            "entity_id": "light.vizzini",
            "state": "on",
            "brightness": 50,
        }
        outcome = self.journal.undo(1, registry=self.registry)
        self.assertFalse(outcome.success)
        self.assertIn(entry.action_id, outcome.conflicts)
        self.assertEqual(self.registry.calls, [])
        self.assertIn("ciemno", outcome.message.lower())
        stored = self.journal.get(entry.action_id)
        self.assertEqual(stored.status, EntryStatus.RECORDED.value)

    def test_ha_undo(self):
        entry = self.journal.record(
            "turn_on_device",
            {"entity_id": "light.vizzini", "target": "lampa"},
            result=ToolResult.ok(
                "ok",
                tool_name="turn_on_device",
                data={"entity_id": "light.vizzini", "before": "off", "after": "on"},
            ),
            before_state={"kind": "ha", "entity_id": "light.vizzini", "state": "off"},
            after_state={"kind": "ha", "entity_id": "light.vizzini", "state": "on"},
        )
        self.live["light.vizzini"] = {
            "kind": "ha",
            "entity_id": "light.vizzini",
            "state": "on",
        }

        def invoke(name, args, confirmed=False, **_kwargs):
            self.registry.calls.append((name, dict(args), confirmed))
            if name == "turn_off_device":
                self.live["light.vizzini"] = {
                    "kind": "ha",
                    "entity_id": "light.vizzini",
                    "state": "off",
                }
            return ToolResult.ok("ok", tool_name=name)

        self.registry.invoke = invoke
        outcome = self.journal.undo(1, registry=self.registry)
        self.assertTrue(outcome.success)
        self.assertEqual(self.registry.calls[0][0], "turn_off_device")
        self.assertEqual(self.registry.calls[0][1].get("entity_id"), "light.vizzini")
        self.assertEqual(self.journal.get(entry.action_id).status, EntryStatus.UNDONE.value)

    def test_volume_undo(self):
        entry = self._record_volume()
        outcome = self.journal.undo(1, registry=self.registry)
        self.assertTrue(outcome.success)
        self.assertEqual(self.registry.calls[0][0], "volume_down")
        self.assertIn(entry.action_id, outcome.undid)

    def test_media_undo(self):
        entry = self.journal.record(
            "pause_media",
            {},
            result=ToolResult.ok("pauza", tool_name="pause_media"),
        )
        outcome = self.journal.undo(1, registry=self.registry)
        self.assertTrue(outcome.success)
        self.assertEqual(self.registry.calls[0][0], "resume_media")
        self.assertEqual(self.journal.get(entry.action_id).status, EntryStatus.UNDONE.value)

    def test_tool_failure_during_undo(self):
        entry = self._record_volume()
        failing = FakeRegistry(fail_tools={"volume_down"})
        outcome = self.journal.undo(1, registry=failing)
        self.assertFalse(outcome.success)
        self.assertIn(entry.action_id, outcome.failed)
        self.assertEqual(self.journal.get(entry.action_id).status, EntryStatus.RECORDED.value)

    def test_verification_failure(self):
        entry = self.journal.record(
            "turn_on_device",
            {"entity_id": "light.x"},
            result=ToolResult.ok(
                "ok",
                tool_name="turn_on_device",
                data={"entity_id": "light.x", "before": "off", "after": "on"},
            ),
            before_state={"kind": "ha", "entity_id": "light.x", "state": "off"},
            after_state={"kind": "ha", "entity_id": "light.x", "state": "on"},
        )
        self.live["light.x"] = {"kind": "ha", "entity_id": "light.x", "state": "on"}
        outcome = self.journal.undo(1, registry=self.registry)
        self.assertFalse(outcome.success)
        self.assertIn(entry.action_id, outcome.verification_failed)
        self.assertEqual(self.journal.get(entry.action_id).status, EntryStatus.UNDONE.value)
        self.assertEqual(self.registry.calls[0][0], "turn_off_device")

    def test_registry_wrap_records_volume(self):
        reset_action_history()
        registry = ToolRegistry(confirm_medium=False)
        registry.register(
            Tool(
                name="volume_up",
                description="up",
                handler=lambda _a: ToolResult.ok("głośniej", tool_name="volume_up"),
            )
        )
        registry.register(
            Tool(
                name="volume_down",
                description="down",
                handler=lambda _a: ToolResult.ok("ciszej", tool_name="volume_down"),
            )
        )
        reset_registry_for_tests(registry)
        result = registry.invoke("volume_up", {})
        self.assertTrue(result.success)
        last = get_journal().last_recorded()
        self.assertIsNotNone(last)
        self.assertEqual(last.tool, "volume_up")
        self.assertTrue(last.reversible)


class UndoUtteranceTests(unittest.TestCase):
    def test_looks_undo_phrases(self):
        self.assertTrue(looks_undo("cofnij"))
        self.assertTrue(looks_undo("cofnij ostatnią rzecz"))
        self.assertTrue(looks_undo("cofnij ostatnie trzy rzeczy"))
        self.assertTrue(looks_undo("przywróć jak było wcześniej"))
        self.assertFalse(looks_undo("cofnij w notatniku"))
        self.assertFalse(looks_undo("włącz światło"))
        self.assertEqual(undo_count("cofnij ostatnie trzy rzeczy"), 3)
        self.assertEqual(undo_count("cofnij ostatnie 2 rzeczy"), 2)
        self.assertEqual(undo_count("cofnij"), 1)

    def test_fastpath_undo_not_stolen_by_computer(self):
        env = decide_semantics("cofnij", allow_llm=False)
        self.assertEqual(env.intent, "undo_action")
        self.assertEqual(env.mode, "ACT")
        self.assertTrue(fast_path_permitted("cofnij", env))
        self.assertEqual(
            evaluate_fast_path("cofnij", env).status,
            FastPathStatus.HANDLED_COMPLETE,
        )
        light = decide_semantics("włącz światło", allow_llm=False)
        self.assertNotEqual(light.intent, "undo_action")

    def test_fastpath_n_undo_and_restore_phrase(self):
        three = decide_semantics("cofnij ostatnie trzy rzeczy", allow_llm=False)
        self.assertEqual(three.intent, "undo_action")
        self.assertEqual(three.payload, "3")
        restore = decide_semantics("przywróć jak było wcześniej", allow_llm=False)
        self.assertEqual(restore.intent, "undo_action")

    def test_fastpath_executes_undo_tool(self):
        reset_action_history()
        registry = ToolRegistry(confirm_medium=False)
        seen = []

        def handler(arguments):
            seen.append(dict(arguments or {}))
            return ToolResult.ok("Cofnięte.", tool_name="undo_last_action")

        registry.register(
            Tool(
                name="undo_last_action",
                description="undo",
                handler=handler,
            )
        )
        env = decide_semantics("cofnij ostatnie trzy rzeczy", allow_llm=False)
        result = run_fast_path("cofnij ostatnie trzy rzeczy", registry, envelope=env)
        self.assertTrue(getattr(result, "success", False))
        self.assertEqual(seen[0].get("count"), 3)


if __name__ == "__main__":
    unittest.main()
