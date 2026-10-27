# ==========================
# ELIAN — Wave A–D routing / briefing / policy regressions
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.action_firewall import allow_side_effect, allow_tool
from core.action_history import record_action, reset_action_history, undo_last_action
from core.browser_contract import browser_search_service
from core.clarification_policy import sanitize_clarification_question
from core.fast_path import FastPathStatus, evaluate_fast_path
from core.intent_family import IntentFamily, classify_intent_family
from core.model_client import ModelClient, reset_model_client
from core.pending_context import pending_from_manager
from core.procedure_run import run_named_procedure
from core.read_intents import (
    looks_home_overview,
    looks_list_reminders,
    looks_missed_digest,
    looks_simulate,
    looks_today_schedule,
    looks_weather,
)
from core.semantic_executive import decide_semantics, fast_path_permitted
from core.session_digest import consume_digest_spoken, note_digest_line, reset_digest_for_tests
from core.tool_result import ToolResult
from core.tool_shortlist import shortlist_tools
from core.tools import reset_registry_for_tests
from core.world_state import get_observation, note_media, note_network, reset_world_state
from memory.vocabulary import apply_aliases


class BoomClient(ModelClient):
    def chat(self, messages, *, temperature=0.2, format=None, model=None) -> str:
        raise AssertionError("planner LLM must not run")


class WaveARoutingTests(unittest.TestCase):
    def test_media_shortlist_uses_real_track_tools(self):
        catalog = [
            {"name": n, "available": True}
            for n in (
                "next_track",
                "previous_track",
                "get_now_playing",
                "skip_next",
                "play_media",
            )
        ]
        names = [
            item["name"]
            for item in shortlist_tools(
                catalog,
                goal="następny utwór",
                classification=classify_intent_family("puść muzykę"),
            )
        ]
        self.assertIn("next_track", names)
        self.assertNotIn("skip_next", names)
        self.assertIn("get_now_playing", names)

    def test_read_tools_in_families(self):
        self.assertTrue(looks_home_overview("Co w domu?"))
        self.assertTrue(looks_list_reminders("Jakie mam przypomnienia?"))
        self.assertTrue(looks_today_schedule("Co mam dziś?"))
        self.assertFalse(looks_today_schedule("Jaki mam dzień?"))
        env = decide_semantics("Co w domu?", allow_llm=False)
        self.assertEqual(env.intent, "home_overview")
        self.assertTrue(fast_path_permitted("Co w domu?", env))
        self.assertEqual(
            evaluate_fast_path("Co w domu?", env).status,
            FastPathStatus.HANDLED_COMPLETE,
        )
        rem = decide_semantics("Jakie mam przypomnienia?", allow_llm=False)
        self.assertEqual(rem.intent, "list_reminders")
        day = decide_semantics("Co mam dziś?", allow_llm=False)
        self.assertEqual(day.intent, "today_schedule")
        brief = decide_semantics("Jaki mam dzień?", allow_llm=False)
        self.assertEqual(brief.intent, "situation_briefing")

    def test_compound_not_stolen_by_fastpath(self):
        text = "Otwórz Spotify i zwiększ głośność"
        env = decide_semantics(text, allow_llm=False)
        self.assertNotEqual(
            evaluate_fast_path(text, env).status,
            FastPathStatus.HANDLED_COMPLETE,
        )

    def test_english_clarification_sanitized(self):
        q = sanitize_clarification_question(
            "Do you want to open an application?",
            goal="przypomnij mi o lekach",
        )
        self.assertNotIn("application", q.lower())
        self.assertNotIn("Do you", q)

    def test_calendar_family_for_today(self):
        info = classify_intent_family("co mam dziś")
        self.assertEqual(info.family, IntentFamily.CALENDAR)


class WaveBBriefingTests(unittest.TestCase):
    def test_weather_and_missed_intents(self):
        self.assertTrue(looks_weather("Jaka jest pogoda?"))
        self.assertTrue(looks_missed_digest("Co mnie ominęło?"))
        env = decide_semantics("Jaka jest pogoda?", allow_llm=False)
        self.assertEqual(env.intent, "weather_status")
        missed = decide_semantics("Co mnie ominęło?", allow_llm=False)
        self.assertEqual(missed.intent, "missed_digest")

    def test_digest_roundtrip(self):
        reset_digest_for_tests()
        note_digest_line("Przypomnienie: leki.")
        spoken = consume_digest_spoken()
        self.assertIn("leki", spoken.lower())
        self.assertIn("sesji", consume_digest_spoken().lower())


class WaveCPolicyTests(unittest.TestCase):
    def test_simulate_blocks_mutation(self):
        self.assertTrue(looks_simulate("symuluj włączenie lampy"))
        env = decide_semantics("symuluj włączenie lampy", allow_llm=False)
        self.assertEqual(env.mode, "SIMULATE")
        self.assertFalse(allow_side_effect(env, "turn_on_device", "symuluj włączenie lampy"))

    def test_pending_facade(self):
        class Fake:
            pending_clarification = {"q": 1}
            pending_tool = None
            pending_executable_goal = None
            pending_wait_goal = "przypomnij mi"

        ctx = pending_from_manager(Fake())
        self.assertFalse(ctx.is_empty())
        self.assertEqual(ctx.wait_goal, "przypomnij mi")

    def test_undo_last_action(self):
        reset_action_history()
        record_action("volume_up", {}, ToolResult.ok("ok", tool_name="volume_up"))
        with patch(
            "core.tools.get_registry"
        ) as get_reg:
            class R:
                def invoke(self, name, args, confirmed=False):
                    return ToolResult.ok("Ciszej.", tool_name=name)

            get_reg.return_value = R()
            result = undo_last_action()
        self.assertTrue(result.success)
        self.assertEqual(result.data.get("inverse"), "volume_down")


class WaveDLaterTests(unittest.TestCase):
    def test_vocabulary_alias(self):
        text = apply_aliases(
            "odpal lampkę",
            vocab={"phrases": [{"from": "lampkę", "to": "lampę"}], "people": [], "apps": [], "projects": [], "devices": []},
        )
        self.assertIn("lampę", text)

    def test_world_state_media_network(self):
        reset_world_state()
        note_media({"title": "Billie Jean"})
        note_network(online=True)
        media = get_observation("media")
        network = get_observation("network")
        self.assertEqual((media.payload if media else {}).get("title"), "Billie Jean")
        self.assertTrue((network.payload if network else {}).get("online"))

    def test_browser_stays_unavailable_without_opt_in(self):
        result = browser_search_service({"query": "x"})
        self.assertFalse(result.success)
        self.assertIn("przeglądark", result.message.lower())

    def test_morning_procedure_uses_briefing(self):
        class R:
            def invoke(self, name, args, confirmed=False):
                return ToolResult.ok("Następne: trening o 17.", tool_name=name)

        result = run_named_procedure("morning", registry=R())
        self.assertTrue(result.success)
        self.assertIn("trening", result.message)
        gaming = run_named_procedure("gaming", registry=R())
        self.assertFalse(gaming.success)


class WaveWaitAndVisionTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()

    def test_wait_holds_reminder_slot(self):
        from core.conversation_manager import ConversationManager
        from core.builtin_tools import build_default_registry
        from memory.manager import MemoryManager

        cm = ConversationManager(
            registry=build_default_registry(),
            model=BoomClient(),
            memory_manager=MemoryManager(Path(tempfile.mkdtemp()) / "m.json"),
        )
        cm.activate()
        result = cm.handle("przypomnij mi o lekach czekaj")
        self.assertEqual(result.source, "wait")
        self.assertTrue(cm.pending_wait_goal)
        self.assertIn("lek", cm.pending_wait_goal.lower())

    def test_vision_clears_stale_clarification(self):
        from core.conversation_manager import ConversationManager
        from core.builtin_tools import build_default_registry
        from memory.manager import MemoryManager

        cm = ConversationManager(
            registry=build_default_registry(),
            model=BoomClient(),
            memory_manager=MemoryManager(Path(tempfile.mkdtemp()) / "m.json"),
        )
        cm.activate()
        cm.pending_clarification = {"question": "Którą aplikację mam otworzyć?"}
        env = decide_semantics("Co teraz widzisz na ekranie?", allow_llm=False)
        self.assertEqual(env.intent, "current_screen_observation")


if __name__ == "__main__":
    unittest.main()
