# ==========================
# ELIAN — proactive_reminders integration tests
# ==========================
from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock

from integrations.datetime_parse import now_local
from integrations.reminders.store import ReminderStore, reset_reminder_store_for_tests


class ProactiveRemindersStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "reminders.json"
        reset_reminder_store_for_tests(ReminderStore(path=self.path))

    def tearDown(self):
        reset_reminder_store_for_tests(None)
        self.tmp.cleanup()

    def test_empty_state_safe(self):
        from core import proactive_reminders as pr

        self.assertEqual(pr.pending_reminders(path=self.path), [])
        self.assertEqual(pr.due_reminders(path=self.path), [])

    def test_schedule_and_load(self):
        from core import proactive_reminders as pr

        due = (now_local() + timedelta(hours=2)).isoformat()
        out = pr.schedule_reminder("herbata", when_iso=due, path=self.path)
        self.assertTrue(out.get("ok"), out)
        rows = pr.pending_reminders(path=self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "herbata")
        # restart: new store instance on same path
        reset_reminder_store_for_tests(None)
        rows2 = pr.pending_reminders(path=self.path)
        self.assertEqual(len(rows2), 1)
        self.assertEqual(rows2[0]["id"], out["id"])

    def test_future_not_due(self):
        from core import proactive_reminders as pr

        due = (now_local() + timedelta(hours=3)).isoformat()
        pr.schedule_reminder("later", when_iso=due, path=self.path)
        self.assertEqual(pr.due_reminders(path=self.path), [])

    def test_due_detection(self):
        from core import proactive_reminders as pr

        due = (now_local() - timedelta(minutes=5)).isoformat()
        # bypass schedule_reminder future check via store
        store = ReminderStore(path=self.path)
        from datetime import datetime

        store.create("due now", datetime.fromisoformat(due))
        reset_reminder_store_for_tests(store)
        due_list = pr.due_reminders(path=self.path)
        self.assertEqual(len(due_list), 1)
        self.assertEqual(due_list[0]["text"], "due now")

    def test_delivered_not_repeated(self):
        from core import proactive_reminders as pr

        due = (now_local() - timedelta(minutes=1)).isoformat()
        store = ReminderStore(path=self.path)
        from datetime import datetime

        item = store.create("once", datetime.fromisoformat(due))
        reset_reminder_store_for_tests(store)
        self.assertTrue(pr.mark_delivered(item.id, path=self.path))
        self.assertEqual(pr.due_reminders(path=self.path), [])
        self.assertFalse(pr.mark_delivered(item.id, path=self.path))

    def test_broken_state_fail_open(self):
        from core import proactive_reminders as pr

        bad = Path(self.tmp.name) / "missing" / "nope.json"
        # path parent missing — create may fail; due must not raise
        self.assertEqual(pr.due_reminders(path=bad), [])
        self.assertEqual(pr.pending_reminders(path=bad), [])


class ProactiveRemindersCMTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "reminders.json"
        reset_reminder_store_for_tests(ReminderStore(path=self.path))

    def tearDown(self):
        reset_reminder_store_for_tests(None)
        self.tmp.cleanup()

    def test_poll_and_deliver_via_cm(self):
        from core import proactive_reminders as pr
        from core.conversation_manager import HandleResult
        from datetime import datetime

        due = now_local() - timedelta(minutes=2)
        store = ReminderStore(path=self.path)
        item = store.create("herbata", due)
        reset_reminder_store_for_tests(store)

        cm = MagicMock()
        cm.handle_proactive_turn.return_value = HandleResult.speak(
            "Przypomnienie: herbata.",
            source="proactive_reminder",
        )
        delivered = pr.poll_and_deliver(cm, path=self.path)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["id"], item.id)
        cm.handle_proactive_turn.assert_called_once()
        # not due again
        self.assertEqual(pr.due_reminders(path=self.path), [])

    def test_cm_poll_method_fail_open(self):
        from core.conversation_manager import ConversationManager
        from core.model_client import FakeModelClient
        from core.tools import ToolRegistry
        from memory.manager import MemoryManager

        cm = ConversationManager(
            registry=ToolRegistry(),
            model=FakeModelClient(),
            memory_manager=MemoryManager(),
        )
        # No due reminders — must not raise
        self.assertEqual(cm.poll_proactive_reminders(), [])

    def test_activate_tolerates_reminder_errors(self):
        from core.conversation_manager import ConversationManager
        from core.model_client import FakeModelClient
        from core.tools import ToolRegistry
        from memory.manager import MemoryManager
        from unittest.mock import patch

        cm = ConversationManager(
            registry=ToolRegistry(),
            model=FakeModelClient(),
            memory_manager=MemoryManager(),
        )
        with patch(
            "core.proactive_reminders.poll_and_deliver",
            side_effect=RuntimeError("boom"),
        ):
            # activate catches and continues
            cm.activate()
            self.assertTrue(cm.active)


if __name__ == "__main__":
    unittest.main()
