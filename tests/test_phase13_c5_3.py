# ==========================
# ELIAN — Phase 13 C.5.3 fresh screen truth
# ==========================
from __future__ import annotations

import time
import unittest

from core.builtin_tools import build_default_registry
from core.clock import FakeClock
from core.computer_agent import ComputerAgent, notepad_fixture_store, reset_computer_agent_for_tests
from core.events import ACTIVE_WINDOW_CHANGED, Event, Priority
from core.model_client import FakeModelClient, reset_model_client
from core.perception.foreground import FakeForegroundProvider
from core.perception.frame_provider import MemoryFrameProvider
from core.perception.metrics import perception_metrics, reset_perception_metrics
from core.perception.screen_runtime import ScreenPerceptionRuntime, reset_screen_runtime_for_tests
from core.perception.screen_truth import (
    UNCERTAIN_SPEECH,
    answer_screen_query,
    format_screen_observation,
    looks_screen_observation_query,
)
from core.perception.visual_referent import VisualReferent
from core.planner import Planner
from core.proactive import ProactiveManager
from core.tools import ToolRegistry, reset_registry_for_tests
from vision.uia import FakeUIBackend


class Phase13C53ScreenTruthTests(unittest.TestCase):
    def setUp(self):
        reset_perception_metrics()
        reset_registry_for_tests()
        reset_model_client()
        self.fg = FakeForegroundProvider(
            hwnd=1, title="Bez tytułu — Notatnik", process="notepad.exe"
        )
        self.runtime = ScreenPerceptionRuntime(
            provider=MemoryFrameProvider(),
            foreground_provider=self.fg,
        )
        reset_screen_runtime_for_tests(self.runtime)
        self.runtime.observe_window_meta(
            hwnd=1, title="Bez tytułu — Notatnik", process="notepad.exe"
        )
        self.registry = build_default_registry(confirm_medium=False)
        self.calls = []
        original = self.registry.invoke

        def _wrap(name, args=None, **kwargs):
            self.calls.append(name)
            return original(name, args, **kwargs)

        self.registry.invoke = _wrap
        self.planner = Planner(self.registry, model=FakeModelClient([]))

    def tearDown(self):
        self.runtime.stop()
        reset_registry_for_tests(None)

    def test_screen_query_prefers_chrome_over_last_working_notepad(self):
        store = notepad_fixture_store()
        agent = ComputerAgent(store)
        agent._fake_ui = FakeUIBackend()
        reset_computer_agent_for_tests(agent)
        agent.last_working_app = "Notatnik"
        agent.plan_working_app = "Notatnik"
        self.fg.set(hwnd=42, title="ChatGPT - Google Chrome", process="chrome.exe")
        result = self.planner.run("co teraz widzisz na ekranie?")
        self.assertEqual(result.source, "perception")
        low = (result.message or "").lower()
        self.assertIn("chrome", low)
        self.assertIn("chatgpt", low)
        self.assertNotIn("notatnik", low)
        self.assertEqual(agent.last_working_app, "Notatnik")
        self.assertEqual(agent.plan_working_app, "Notatnik")

    def test_foreground_event_updates_screenstate(self):
        self.runtime.start()
        self.fg.set(hwnd=42, title="ChatGPT - Google Chrome", process="chrome.exe")
        window = self.runtime.get_state().active_window
        self.assertEqual(int(window.get("hwnd") or 0), 42)
        self.assertIn("chrome", str(window.get("process") or "").lower())
        self.assertNotIn("notepad", str(window.get("process") or "").lower())

    def test_user_manual_switch_updates_via_hook(self):
        self.runtime.start()
        self.assertTrue(self.fg.started)
        self.fg.set(hwnd=7, title="Inbox - Google Chrome", process="chrome.exe")
        self.assertEqual(self.runtime.get_state().active_window.get("hwnd"), 7)

    def test_screen_question_forces_refresh(self):
        self.fg.set(hwnd=42, title="ChatGPT - Google Chrome", process="chrome.exe")
        spoken, obs = answer_screen_query("co mam na ekranie?")
        self.assertTrue(obs.refreshed)
        self.assertEqual(obs.fresh_app, "ChatGPT")
        self.assertIn("ChatGPT", spoken)

    def test_stale_age_forces_refresh(self):
        self.runtime.state.timestamp = time.time() - 2.0
        self.fg.set(hwnd=42, title="x - Google Chrome", process="chrome.exe")
        obs = self.runtime.fresh_screen_observation(force=False, max_age_ms=500)
        self.assertTrue(obs.refreshed)
        self.assertEqual(obs.hwnd, 42)

    def test_refresh_fail_does_not_claim_stale_notepad(self):
        self.fg.set(hwnd=0, title="", process="")
        spoken, obs = answer_screen_query("co jest teraz otwarte?")
        self.assertFalse(obs.ok)
        self.assertEqual(spoken, UNCERTAIN_SPEECH)
        self.assertNotIn("Notatnik", spoken)
        self.assertNotIn("notatnik", spoken.lower())

    def test_chatgpt_chrome_title_mentions_both(self):
        self.fg.set(hwnd=9, title="ChatGPT - Google Chrome", process="chrome.exe")
        obs = self.runtime.fresh_screen_observation(force=True)
        text = format_screen_observation(obs)
        self.assertIn("ChatGPT", text)
        self.assertIn("Chrome", text)

    def test_no_vision_invents_page_content(self):
        self.runtime.vision.provider.mock = {
            "description": "rozmowa o kwantowej fizyce i przepisie na barszcz"
        }
        self.fg.set(hwnd=9, title="ChatGPT - Google Chrome", process="chrome.exe")
        result = self.planner.run("co teraz widzisz na ekranie?")
        low = (result.message or "").lower()
        self.assertNotIn("kwantow", low)
        self.assertNotIn("barszcz", low)
        self.assertNotIn("rozmowa o", low)

    def test_active_window_changed_remains_silent(self):
        spoken = []
        clock = FakeClock()
        pm = ProactiveManager(
            clock=clock,
            speak_fn=lambda m: spoken.append(m),
            busy_check=lambda: False,
        )
        decision = pm.handle_event(
            Event(
                type=ACTIVE_WINDOW_CHANGED,
                source="perception",
                data={"hwnd": 42, "title": "Chrome"},
                priority=Priority.NORMAL,
                timestamp=clock.time(),
                ttl_seconds=300,
            )
        )
        self.assertEqual(decision.action, "silent")
        self.assertEqual(spoken, [])

    def test_old_notepad_referent_invalidated(self):
        ref = VisualReferent(label="Edytor", confidence=0.95, hwnd=1, bbox=[0, 0, 10, 10])
        self.runtime.referents.remember(ref)
        self.runtime.start()
        self.fg.set(hwnd=42, title="Chrome", process="chrome.exe")
        self.assertIsNone(self.runtime.referents.last)
        self.assertTrue(self.runtime.get_state().ui_snapshot_stale or not self.runtime.get_state().ui_snapshot)

    def test_working_window_independent_of_screen_query(self):
        store = notepad_fixture_store()
        agent = ComputerAgent(store)
        agent._fake_ui = FakeUIBackend()
        reset_computer_agent_for_tests(agent)
        agent.last_working_app = "Notatnik"
        agent.plan_working_window = "win_notepad"
        self.fg.set(hwnd=42, title="Google Chrome", process="chrome.exe")
        result = self.planner.run("co jest aktywne?")
        self.assertIn("Chrome", result.message)
        self.assertEqual(agent.last_working_app, "Notatnik")
        self.assertEqual(agent.plan_working_window, "win_notepad")

    def test_screen_query_zero_tool_side_effects(self):
        self.fg.set(hwnd=42, title="ChatGPT - Google Chrome", process="chrome.exe")
        result = self.planner.run("co teraz widzisz na ekranie?")
        self.assertEqual(result.source, "perception")
        self.assertEqual(self.calls, [])
        self.assertEqual(result.steps, [])

    def test_query_phrases_detected(self):
        for phrase in (
            "co teraz widzisz na ekranie?",
            "co mam na ekranie?",
            "co jest otwarte?",
            "na co patrzę?",
            "co teraz jest przed tobą?",
            "co jest aktywne?",
        ):
            self.assertTrue(looks_screen_observation_query(phrase), phrase)


if __name__ == "__main__":
    unittest.main()
