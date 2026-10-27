# ==========================
# ELIAN — Memory 2.0 regression tests
# ==========================
"""Typed memory: create/update/expiry/conflict/cleanup. Offline temp storage."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory.classify import classify_remember_text
from memory.manager import MemoryManager, reset_memory_manager
from memory.store import SCHEMA_VERSION
from memory.types import MemoryType, WriteStatus


class MemoryV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "mem.json"
        self.now = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)
        self.mem = MemoryManager(path=self.path, now_fn=lambda: self._iso())
        reset_memory_manager(self.path)

    def tearDown(self):
        reset_memory_manager(None)
        self.tmp.cleanup()

    def _iso(self):
        return self.now.isoformat(timespec="seconds")

    def _set_now(self, when: datetime):
        self.now = when
        self.mem.now_fn = lambda: self._iso()
        self.mem.typed.now_fn = self.mem.now_fn

    def test_create_and_update(self):
        created = self.mem.remember(
            type=MemoryType.FACT,
            key="ulubiona.gra",
            value="Minecraft",
            text="moja ulubiona gra to Minecraft",
        )
        self.assertTrue(created.ok)
        self.assertEqual(created.status, WriteStatus.CREATED)
        updated = self.mem.remember(
            type=MemoryType.FACT,
            key="ulubiona.gra",
            value="Terraria",
            text="moja ulubiona gra to Terraria",
            confidence=1.0,
            explicit=True,
            ask_on_conflict=False,
        )
        self.assertEqual(updated.status, WriteStatus.UPDATED)
        hits = self.mem.retrieve("gra", types=(MemoryType.FACT,), limit=5)
        self.assertTrue(any("Terraria" in str(item.get("value")) for item in hits))

    def test_expiry_temporary(self):
        expiry = (self.now + timedelta(hours=1)).isoformat(timespec="seconds")
        result = self.mem.remember(
            type=MemoryType.TEMPORARY,
            key="appointment.dentist",
            value="jutro mam dentystę",
            expires_at=expiry,
            provenance={"calendar": False, "kind": "user_said"},
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(self.mem.retrieve("dentyst")), 1)
        self._set_now(self.now + timedelta(hours=2))
        self.assertEqual(self.mem.retrieve("dentyst"), [])
        self.mem.save()
        again = MemoryManager(path=self.path, now_fn=lambda: self._iso())
        self.assertEqual(again.retrieve("dentyst"), [])

    def test_conflict_asks_when_two_strong_facts(self):
        self.mem.remember(
            type=MemoryType.FACT,
            key="ulubiona.gra",
            value="Minecraft",
            explicit=True,
            confidence=1.0,
        )
        conflict = self.mem.remember(
            type=MemoryType.FACT,
            key="ulubiona.gra",
            value="Fortnite",
            explicit=True,
            confidence=1.0,
            ask_on_conflict=True,
        )
        self.assertEqual(conflict.status, WriteStatus.CONFLICT_ASK)
        self.assertIn("Minecraft", conflict.message)
        kept = self.mem.typed.find_by_key("ulubiona.gra", type=MemoryType.FACT)
        self.assertEqual(kept.get("value"), "Minecraft")

    def test_low_confidence_does_not_overwrite_strong(self):
        self.mem.set_preference("music.default_app", "Spotify", explicit=True)
        weak = self.mem.remember(
            type=MemoryType.PREFERENCE,
            key="music.default_app",
            value="YouTube Music",
            explicit=False,
            confidence=0.3,
            ask_on_conflict=True,
        )
        self.assertEqual(weak.status, WriteStatus.REJECTED_WEAK)
        self.assertEqual(self.mem.get_preference("music.default_app"), "Spotify")

    def test_preference_priority_over_fact(self):
        self.mem.set_preference("music.default_app", "Spotify", explicit=True)
        blocked = self.mem.remember(
            type=MemoryType.FACT,
            key="music.default_app",
            value="YouTube Music",
            explicit=False,
            confidence=0.4,
        )
        self.assertEqual(blocked.status, WriteStatus.REJECTED_WEAK)
        self.assertEqual(self.mem.get_preference("music.default_app"), "Spotify")

    def test_retrieval_by_meaning_and_key(self):
        self.mem.remember_fact("moja ulubiona gra to Minecraft")
        self.mem.remember(
            type=MemoryType.RELATION,
            key="alias.lampa",
            value="light.vizzini",
            text="moja lampa to light.vizzini",
        )
        by_meaning = self.mem.retrieve("ulubiona gra")
        self.assertTrue(any("minecraft" in str(item.get("value") or item.get("text")).lower() for item in by_meaning))
        by_key = self.mem.retrieve("", key="alias.lampa", types=(MemoryType.RELATION,))
        self.assertEqual(by_key[0]["value"], "light.vizzini")
        used = by_key[0]["last_used_at"]
        self.assertTrue(used)

    def test_secret_rejection(self):
        ok, message = self.mem.remember_fact("moje hasło to sekret123")
        self.assertFalse(ok)
        self.assertIn("token", message.lower())
        secret = self.mem.remember(
            type=MemoryType.FACT,
            key="login",
            value="password hunter2",
        )
        self.assertEqual(secret.status, WriteStatus.REJECTED_SECRET)
        self.assertEqual(self.mem.typed.all(), [])

    def test_duplicate_merge(self):
        first = self.mem.remember(
            type=MemoryType.FACT,
            key="miasto",
            value="Łódź",
            text="Moje miasto to Łódź",
        )
        second = self.mem.remember(
            type=MemoryType.FACT,
            key="miasto",
            value="Łódź",
            text="Moje miasto to Łódź",
        )
        self.assertEqual(second.status, WriteStatus.MERGED)
        self.assertEqual(first.entry["id"], second.entry["id"])
        facts = [
            item
            for item in self.mem.typed.all()
            if item.get("type") == MemoryType.FACT.value and item.get("key") == "miasto"
        ]
        self.assertEqual(len(facts), 1)
        self.assertGreaterEqual(int(facts[0].get("confirmations") or 1), 2)

    def test_stale_cleanup_candidates_not_auto_deleted(self):
        result = self.mem.remember(
            type=MemoryType.FACT,
            key="weak.note",
            value="ktoś wspomniał o pogodzie",
            explicit=False,
            confidence=0.3,
            importance=0.2,
        )
        entry_id = result.entry["id"]
        old = "2026-01-01T00:00:00+00:00"
        for item in self.mem.typed._items:
            if item.get("id") == entry_id:
                item["created_at"] = old
                item["updated_at"] = old
                item["last_used_at"] = old
        candidates = self.mem.cleanup_candidates()
        self.assertTrue(any(item.get("id") == entry_id for item in candidates))
        self.assertIsNotNone(self.mem.typed.get(entry_id))
        self.mem.set_preference("music.default_app", "Spotify")
        pref_ids = {item.get("id") for item in self.mem.cleanup_candidates()}
        pref = self.mem.typed.find_by_key("music.default_app", type=MemoryType.PREFERENCE)
        self.assertNotIn(pref["id"], pref_ids)

    def test_session_vs_persistent(self):
        session = self.mem.remember(
            type=MemoryType.SESSION,
            key="topic",
            value="rozmowa o dentyście",
        )
        persistent = self.mem.remember(
            type=MemoryType.FACT,
            key="miasto",
            value="Łódź",
        )
        self.assertTrue(session.ok)
        self.assertTrue(persistent.ok)
        live = self.mem.retrieve("dentyst", include_session=True)
        self.assertTrue(any(item.get("type") == MemoryType.SESSION.value for item in live))
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        types = {item.get("type") for item in payload.get("entries") or []}
        self.assertNotIn(MemoryType.SESSION.value, types)
        self.assertIn(MemoryType.FACT.value, types)
        again = MemoryManager(path=self.path)
        self.assertEqual(again.retrieve("dentyst", include_session=True), [])
        self.assertTrue(again.retrieve("Łódź"))
        self.mem.clear_session()
        self.assertEqual(self.mem.retrieve("dentyst", include_session=True), [])

    def test_schema3_migration_keeps_preferences(self):
        legacy = {
            "schema_version": 3,
            "facts": [
                {
                    "id": "fact_legacy",
                    "text": "Moje miasto to Łódź",
                    "category": "fact",
                    "tags": [],
                    "explicit": True,
                    "source": "user",
                    "confidence": 1.0,
                    "created_at": "2026-08-24T16:01:18+00:00",
                    "updated_at": "2026-08-24T16:01:18+00:00",
                    "last_used_at": "2026-08-24T16:01:18+00:00",
                }
            ],
            "preferences": [
                {
                    "id": "preference_legacy",
                    "key": "music.default_app",
                    "value": "Spotify",
                    "explicit": True,
                    "source": "user",
                    "created_at": "2026-08-25T10:55:58+00:00",
                    "updated_at": "2026-08-25T10:56:52+00:00",
                }
            ],
            "recent_actions": [],
            "conversation": [],
            "episodic_hooks": [],
        }
        path = Path(self.tmp.name) / "legacy.json"
        path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")
        migrated = MemoryManager(path=path)
        self.assertEqual(migrated.get_preference("music.default_app"), "Spotify")
        hits = migrated.search_facts("miasto")
        self.assertTrue(any("łódź" in item["text"].lower() or "lodz" in item["text"].lower() for item in hits))
        dumped = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(dumped.get("schema_version"), SCHEMA_VERSION)
        self.assertTrue(dumped.get("entries"))
        self.assertEqual(dumped["preferences"][0]["value"], "Spotify")
        self.assertEqual(dumped["facts"][0]["text"], "Moje miasto to Łódź")

    def test_classify_examples(self):
        morning = classify_remember_text("wolę żeby rano mówił krótko")
        self.assertEqual(morning["type"], MemoryType.PREFERENCE)
        self.assertEqual(morning["key"], "voice.morning_brief")
        relation = classify_remember_text("moja lampa to light.vizzini")
        self.assertEqual(relation["type"], MemoryType.RELATION)
        self.assertEqual(relation["value"], "light.vizzini")
        temp = classify_remember_text("jutro mam dentystę", now=self._iso())
        self.assertEqual(temp["type"], MemoryType.TEMPORARY)
        self.assertTrue(temp.get("expires_at"))
        self.assertFalse(temp["provenance"].get("calendar"))

    def test_remember_fact_classifies_and_morning_pref_command(self):
        ok, _ = self.mem.remember_fact("moja lampa to light.vizzini")
        self.assertTrue(ok)
        rel = self.mem.typed.find_by_key("alias.lampa", type=MemoryType.RELATION)
        self.assertIsNotNone(rel)
        kind, payload = self.mem.parse_explicit_memory_command(
            "wolę żeby rano mówił krótko"
        )
        self.assertEqual(kind, "prefer")
        self.assertEqual(payload["key"], "voice.morning_brief")
        self.mem.set_preference(payload["key"], payload["value"])
        bundle = self.mem.behavior_preferences()
        self.assertEqual(bundle["morning_brief"], "short")

    def test_compact_context_is_goal_scoped(self):
        self.mem.set_preference("music.default_app", "Spotify")
        self.mem.remember_fact("moja ulubiona gra to Minecraft")
        self.mem.remember(
            type=MemoryType.RELATION,
            key="alias.lampa",
            value="light.vizzini",
            text="moja lampa to light.vizzini",
        )
        music = self.mem.select_compact_memory("puść muzykę")
        keys = {item.get("key") for item in music["entries"]}
        self.assertIn("music.default_app", keys)
        lamp = self.mem.select_compact_memory("włącz lampę")
        lamp_blob = json.dumps(lamp["entries"], ensure_ascii=False).lower()
        self.assertIn("vizzini", lamp_blob)
        self.assertLessEqual(len(music["entries"]), 8)
        built = self.mem.build_context("puść muzykę")
        self.assertEqual(built["preferences"].get("music.default_app"), "Spotify")
        self.assertIn("Spotify", built["text"])
        self.assertLess(len(built["text"]), 1800)

    def test_explicit_preference_still_overwrites(self):
        self.mem.set_preference("music.default_app", "Spotify")
        self.mem.set_preference("music.default_app", "YouTube Music")
        self.assertEqual(self.mem.get_preference("music.default_app"), "YouTube Music")
        self.assertEqual(len(self.mem.preferences.all()), 1)


if __name__ == "__main__":
    unittest.main()
