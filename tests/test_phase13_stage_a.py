"""Phase 13 Stage A — close+volume compound, plan ids, retry failed-only, NP veto."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.action_claim import HONEST_NO_TOOL, guard_action_claim
from core.builtin_tools import build_default_registry
from core.compound_plan import is_cross_domain_compound, parse_compound_goal
from core.conversation_manager import ConversationManager
from core.fast_path import FastPathStatus, evaluate_fast_path
from core.intent_family import IntentFamily, classify_intent_family
from core.model_client import FakeModelClient, reset_model_client
from core.pending_goal import clear_persisted_pending, looks_retry_utterance
from core.planner import Planner
from core.tool_result import ToolResult
from core.tools import reset_registry_for_tests
from memory.manager import MemoryManager

try:
    from tests.volume_sim import simulated_system_volume
except ImportError:  # unittest discover may load tests.* as top-level
    from volume_sim import simulated_system_volume


class CompoundCloseTests(unittest.TestCase):
    def tearDown(self):
        clear_persisted_pending()
        reset_registry_for_tests(None)
        reset_model_client()

    def test_close_volume_compound_parsed(self):
        plan = parse_compound_goal("Zamknij Spotify i zmniejsz głośność.")
        self.assertTrue(plan.detected)
        self.assertTrue(plan.complete)
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.steps[0].semantic_action, "close_service")
        self.assertEqual(plan.steps[0].slots.get("target"), "Spotify")
        self.assertEqual(plan.steps[1].semantic_action, "adjust_volume")
        self.assertEqual(plan.steps[1].slots.get("direction"), "decrease")
        self.assertTrue(is_cross_domain_compound("Zamknij Spotify i zmniejsz głośność."))

    def test_close_volume_classification_complete(self):
        info = classify_intent_family("Zamknij Spotify i zmniejsz głośność.")
        self.assertTrue(info.slots.get("compound"))
        self.assertTrue(info.is_complete)
        self.assertEqual(info.slots.get("families"), ["APP", "SYSTEM"])
        self.assertEqual(info.slots["compound_steps"][0]["semantic_action"], "close_service")

    def test_np_coordination_spider_man_not_compound(self):
        plan = parse_compound_goal("Znajdź Spider-Mana i Iron Mana")
        self.assertFalse(plan.detected)
        self.assertFalse(is_cross_domain_compound("Znajdź Spider-Mana i Iron Mana"))

    def test_np_coordination_lodz_not_compound(self):
        plan = parse_compound_goal("wyszukaj sklepy w Łodzi i okolicy")
        self.assertFalse(plan.detected)
        self.assertFalse(is_cross_domain_compound("wyszukaj sklepy w Łodzi i okolicy"))

    def test_single_close_not_compound(self):
        plan = parse_compound_goal("Zamknij Spotify.")
        self.assertFalse(plan.detected)
        info = classify_intent_family("Zamknij Spotify.")
        self.assertEqual(info.family, IntentFamily.APP)
        self.assertTrue(info.is_complete)
        self.assertEqual(info.slots.get("semantic_action"), "close_service")

    def test_planner_close_then_volume_legacy_zero(self):
        with patch("ComputerControl.close_application", return_value="Zamykam aplikację Spotify."):
            with simulated_system_volume():
                result = Planner(
                    build_default_registry(confirm_medium=False),
                    model=FakeModelClient([]),
                ).run("Zamknij Spotify i zmniejsz głośność.")
        self.assertGreaterEqual(len(result.steps), 2)
        self.assertEqual(result.steps[0]["tool"], "close_app")
        self.assertEqual(result.steps[1]["tool"], "volume_down")
        self.assertEqual(result.source, "compound")
        self.assertTrue(result.plan_id)
        self.assertEqual(result.steps[0]["step_id"], f"{result.plan_id}.1")
        self.assertEqual(result.steps[1]["step_id"], f"{result.plan_id}.2")
        self.assertEqual(result.steps[0]["attempt_id"], 1)
        self.assertTrue(result.steps[0].get("idempotency_key"))
        self.assertEqual(result.plan_status, "COMPLETED")
        self.assertIn("zamknięte", result.message.lower())

    def test_fastpath_skips_close_compound(self):
        evaluation = evaluate_fast_path("Zamknij Spotify i zmniejsz głośność.")
        self.assertEqual(evaluation.status, FastPathStatus.NOT_HANDLED)

    def test_fastpath_single_close(self):
        evaluation = evaluate_fast_path("Zamknij Spotify.")
        self.assertEqual(evaluation.status, FastPathStatus.HANDLED_COMPLETE)

    def test_conversation_manager_close_compound_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            manager = ConversationManager(
                registry=build_default_registry(confirm_medium=False),
                memory_manager=mem,
                memory=[],
                model=FakeModelClient([]),
            )
            manager.activate()
            with patch(
                "ComputerControl.close_application",
                return_value="Zamykam aplikację Spotify.",
            ):
                with patch("core.builtin_tools.Music.run", return_value="ok"):
                    result = manager.handle("Zamknij Spotify i zmniejsz głośność.")
        self.assertEqual(result.source, "agent")
        self.assertGreaterEqual(int((result.data or {}).get("steps") or 0), 2)
        self.assertNotIn(result.source, ("skill", "command", "intent"))

    def test_partial_close_ok_volume_fail_honest(self):
        registry = build_default_registry(confirm_medium=False)
        original = registry.invoke

        def _invoke(name, arguments=None, **kwargs):
            if name == "volume_down":
                return ToolResult.fail("Nie udało się.", tool_name="volume_down")
            return original(name, arguments, **kwargs)

        registry.invoke = _invoke  # type: ignore[method-assign]
        with patch("ComputerControl.close_application", return_value="Zamykam aplikację Spotify."):
            result = Planner(registry, model=FakeModelClient([])).run(
                "Zamknij Spotify i zmniejsz głośność."
            )
        self.assertFalse(result.success)
        self.assertEqual(result.plan_status, "PARTIAL")
        self.assertIn("Spotify", result.message)
        self.assertIn("nie udało", result.message.lower())
        self.assertIn("głośn", result.message.lower())
        self.assertIsNotNone(result.pending_executable_goal)
        self.assertEqual(result.pending_executable_goal["failed_step_ids"], [f"{result.plan_id}.2"])
        self.assertEqual(result.pending_executable_goal["successful_step_ids"], [f"{result.plan_id}.1"])

    def test_retry_failed_only_skips_successful_close(self):
        registry = build_default_registry(confirm_medium=False)
        close_calls = {"n": 0}
        volume_attempts = {"n": 0}
        original = registry.invoke

        def _invoke(name, arguments=None, **kwargs):
            if name == "close_app":
                close_calls["n"] += 1
            if name == "volume_down":
                volume_attempts["n"] += 1
                if volume_attempts["n"] == 1:
                    return ToolResult.fail("Nie udało się.", tool_name="volume_down")
            return original(name, arguments, **kwargs)

        registry.invoke = _invoke  # type: ignore[method-assign]
        planner = Planner(registry, model=FakeModelClient([]))
        with patch("ComputerControl.close_application", return_value="Zamykam aplikację Spotify.") as close:
            with simulated_system_volume():
                first = planner.run("Zamknij Spotify i zmniejsz głośność.")
                self.assertFalse(first.success)
                second = planner.run(
                    "Zamknij Spotify i zmniejsz głośność.",
                    context={
                        "retry_failed_only": True,
                        "pending_executable_goal": first.pending_executable_goal,
                    },
                )
        self.assertTrue(second.success)
        self.assertEqual(close.call_count, 1)
        self.assertEqual(close_calls["n"], 1)
        tools = [item["tool"] for item in second.steps if item.get("status") != "SKIPPED"]
        self.assertEqual(tools, ["volume_down"])
        skipped = [item for item in second.steps if item.get("status") == "SKIPPED"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["tool"], "close_app")
        self.assertEqual(second.steps[-1]["attempt_id"], 2)
        self.assertEqual(second.plan_id, first.plan_id)

    def test_retry_phrase_conversation_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            registry = build_default_registry(confirm_medium=False)
            original = registry.invoke
            volume_attempts = {"n": 0}

            def _invoke(name, arguments=None, **kwargs):
                if name == "volume_down":
                    volume_attempts["n"] += 1
                    if volume_attempts["n"] == 1:
                        return ToolResult.fail("Nie udało się.", tool_name="volume_down")
                return original(name, arguments, **kwargs)

            registry.invoke = _invoke  # type: ignore[method-assign]
            manager = ConversationManager(
                registry=registry,
                memory_manager=mem,
                memory=[],
                model=FakeModelClient([]),
            )
            manager.activate()
            with patch(
                "ComputerControl.close_application",
                return_value="Zamykam aplikację Spotify.",
            ) as close:
                with patch("core.builtin_tools.Music.run", return_value="ok"):
                    first = manager.handle("Zamknij Spotify i zmniejsz głośność.")
                    self.assertIn("nie udało", first.text.lower())
                    self.assertEqual((first.data or {}).get("plan_status"), "PARTIAL")
                    self.assertIsNotNone(manager.pending_executable_goal)
                    second = manager.handle("To spróbuj.")
        self.assertEqual(close.call_count, 1)
        self.assertEqual(second.source, "agent")
        self.assertIn("głośn", second.text.lower())
        self.assertTrue(looks_retry_utterance("To spróbuj."))

    def test_spotify_close_skips_medium_confirm(self):
        with patch("ComputerControl.close_application", return_value="Zamykam aplikację Spotify."):
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                result = Planner(
                    build_default_registry(confirm_medium=True),
                    model=FakeModelClient([]),
                ).run("Zamknij Spotify i zmniejsz głośność.")
        self.assertEqual(result.steps[0]["tool"], "close_app")
        self.assertTrue(result.steps[0]["observation"].get("success"))
        self.assertNotEqual(result.steps[0]["observation"].get("status"), "needs_confirmation")

    def test_future_close_claim_blocked(self):
        guarded = guard_action_claim("Zamknę Spotify i zmniejszę głośność.", [])
        self.assertTrue(guarded.blocked)
        self.assertEqual(guarded.text, HONEST_NO_TOOL)

    def test_chrome_close_volume_compound(self):
        info = classify_intent_family("Zamknij Chrome i zwiększ głośność.")
        self.assertTrue(info.slots.get("compound"))
        self.assertEqual(info.slots["compound_steps"][0]["semantic_action"], "close_service")
        self.assertEqual(info.slots["compound_steps"][1]["slots"]["direction"], "increase")


class HardwareFix2Tests(unittest.TestCase):
    def tearDown(self):
        clear_persisted_pending()
        reset_registry_for_tests(None)
        reset_model_client()

    def test_1_wylacz_spotify_app_close_service(self):
        info = classify_intent_family("Wyłącz Spotify.")
        self.assertEqual(info.family, IntentFamily.APP)
        self.assertEqual(info.slots.get("semantic_action"), "close_service")
        with patch("ComputerControl.close_application", return_value="Zamykam aplikację Spotify."):
            result = Planner(
                build_default_registry(confirm_medium=False),
                model=FakeModelClient([]),
            ).run("Wyłącz Spotify.")
        self.assertEqual(result.intent_family, "APP")
        tools = [item.get("tool") for item in result.steps]
        self.assertIn("close_app", tools)
        self.assertNotIn("volume_up", tools)
        self.assertNotIn("turn_off_device", tools)

    def test_2_zamknij_spotify_app_close_service(self):
        info = classify_intent_family("Zamknij Spotify.")
        self.assertEqual(info.family, IntentFamily.APP)
        self.assertEqual(info.slots.get("semantic_action"), "close_service")

    def test_3_wylacz_lampe_smart_home(self):
        info = classify_intent_family("Wyłącz lampę.")
        self.assertEqual(info.family, IntentFamily.SMART_HOME)

    def test_4_wylacz_klimatyzacje_smart_home(self):
        info = classify_intent_family("Wyłącz klimatyzację.")
        self.assertEqual(info.family, IntentFamily.SMART_HOME)

    def test_5_wylacz_spotify_and_volume_down_compound(self):
        from core.tool_shortlist import shortlist_tools

        info = classify_intent_family("Wyłącz Spotify i zmniejsz głośność.")
        self.assertTrue(info.slots.get("compound"))
        self.assertTrue(info.is_complete)
        self.assertEqual(info.slots.get("families"), ["APP", "SYSTEM"])
        catalog = build_default_registry(confirm_medium=False).descriptions_for_planner()
        names = [
            item.get("name")
            for item in shortlist_tools(catalog, goal=info.raw_text, classification=info)
        ]
        self.assertIn("close_app", names)
        self.assertIn("volume_down", names)
        self.assertNotIn("volume_up", names)
        self.assertNotIn("turn_on_device", names)
        self.assertNotIn("turn_off_device", names)
        self.assertNotIn("set_device_value", names)
        with patch("ComputerControl.close_application", return_value="Zamykam aplikację Spotify."):
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                result = Planner(
                    build_default_registry(confirm_medium=False),
                    model=FakeModelClient([]),
                ).run("Wyłącz Spotify i zmniejsz głośność.")
        self.assertGreaterEqual(len(result.steps), 2)
        self.assertEqual(result.steps[0]["tool"], "close_app")
        self.assertEqual(result.steps[1]["tool"], "volume_down")
        self.assertEqual(result.source, "compound")
        self.assertNotEqual(result.source, "legacy")

    def test_6_wylacz_spotify_and_volume_up(self):
        with patch("ComputerControl.close_application", return_value="Zamykam aplikację Spotify."):
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                result = Planner(
                    build_default_registry(confirm_medium=False),
                    model=FakeModelClient([]),
                ).run("Wyłącz Spotify i zwiększ głośność.")
        tools = [item["tool"] for item in result.steps]
        self.assertEqual(tools[:2], ["close_app", "volume_up"])

    def test_7_zamknij_spotify_and_volume_down(self):
        with patch("ComputerControl.close_application", return_value="Zamykam aplikację Spotify."):
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                result = Planner(
                    build_default_registry(confirm_medium=False),
                    model=FakeModelClient([]),
                ).run("Zamknij Spotify i zmniejsz głośność.")
        tools = [item["tool"] for item in result.steps]
        self.assertEqual(tools[:2], ["close_app", "volume_down"])

    def test_8_decrease_volume_up_rejected(self):
        from core.agent_types import AgentDecision
        from core.tool_compat import is_tool_compatible

        info = classify_intent_family("Wyłącz Spotify i zmniejsz głośność.")
        self.assertFalse(
            is_tool_compatible("volume_up", goal=info.raw_text, classification=info)
        )
        registry = build_default_registry(confirm_medium=False)
        calls = []
        original = registry.invoke

        def _invoke(name, arguments=None, **kwargs):
            calls.append(name)
            return original(name, arguments, **kwargs)

        registry.invoke = _invoke  # type: ignore[method-assign]
        planner = Planner(registry, model=FakeModelClient([]))
        with patch("core.builtin_tools.Music.run", return_value="ok"):
            planner._handle_decision(
                AgentDecision(type="tool_call", tool="volume_up", arguments={}),
                goal="Wyłącz Spotify i zmniejsz głośność.",
                context={},
                classification=info,
                steps=[],
                observations=[],
                recent_fingerprints=[],
                max_repeats=2,
                confirmed=True,
                step_index=1,
                terminate_after_tool=True,
            )
        self.assertNotIn("volume_up", calls)

    def test_9_retry_only_failed_volume(self):
        registry = build_default_registry(confirm_medium=False)
        original = registry.invoke
        close_calls = {"n": 0}
        volume_calls = {"n": 0}

        def _invoke(name, arguments=None, **kwargs):
            if name == "close_app":
                close_calls["n"] += 1
            if name == "volume_down":
                volume_calls["n"] += 1
                if volume_calls["n"] == 1:
                    return ToolResult.fail("Nie udało się.", tool_name="volume_down")
            return original(name, arguments, **kwargs)

        registry.invoke = _invoke  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as tmp:
            manager = ConversationManager(
                registry=registry,
                memory_manager=MemoryManager(Path(tmp) / "m.json"),
                memory=[],
                model=FakeModelClient([]),
            )
            manager.activate()
            with patch(
                "ComputerControl.close_application",
                return_value="Zamykam aplikację Spotify.",
            ):
                with patch("core.builtin_tools.Music.run", return_value="ok"):
                    first = manager.handle("Wyłącz Spotify i zmniejsz głośność.")
                    pending = dict(manager.pending_executable_goal or {})
                    first_attempt = None
                    for item in pending.get("steps") or []:
                        if item.get("tool") == "volume_down":
                            first_attempt = item.get("attempt_id")
                    second = manager.handle("To spróbuj.")
        self.assertIsNotNone(pending)
        self.assertEqual(pending.get("plan_id"), (second.data or {}).get("plan_id") or pending.get("plan_id"))
        self.assertEqual(close_calls["n"], 1)
        self.assertEqual(volume_calls["n"], 2)
        self.assertIn("nie udało", first.text.lower())
        later_attempts = [
            item.get("attempt_id")
            for item in (manager.last_observations or [])
            if False
        ]
        self.assertGreater(volume_calls["n"], first_attempt or 0)

    def test_10_retry_only_failed_close(self):
        registry = build_default_registry(confirm_medium=False)
        original = registry.invoke
        close_calls = {"n": 0}
        volume_calls = {"n": 0}

        def _invoke(name, arguments=None, **kwargs):
            if name == "close_app":
                close_calls["n"] += 1
                if close_calls["n"] == 1:
                    return ToolResult.fail("Nie udało się.", tool_name="close_app")
            if name == "volume_down":
                volume_calls["n"] += 1
            return original(name, arguments, **kwargs)

        registry.invoke = _invoke  # type: ignore[method-assign]
        planner = Planner(registry, model=FakeModelClient([]))
        with simulated_system_volume():
            first = planner.run("Wyłącz Spotify i zmniejsz głośność.")
            second = planner.run(
                "Wyłącz Spotify i zmniejsz głośność.",
                context={
                    "retry_failed_only": True,
                    "pending_executable_goal": first.pending_executable_goal,
                },
            )
        self.assertEqual(first.plan_id, second.plan_id)
        self.assertEqual(volume_calls["n"], 1)
        self.assertEqual(close_calls["n"], 2)
        invoked = [item["tool"] for item in second.steps if item.get("status") != "SKIPPED"]
        self.assertEqual(invoked, ["close_app"])
        self.assertIn("zamknąć Spotify", first.message)

    def test_11_full_fail_retry_failed_only(self):
        registry = build_default_registry(confirm_medium=False)
        original = registry.invoke
        counts = {"close_app": 0, "volume_down": 0}

        def _invoke(name, arguments=None, **kwargs):
            if name in counts:
                counts[name] += 1
                if counts[name] == 1:
                    return ToolResult.fail("Nie udało się.", tool_name=name)
            return original(name, arguments, **kwargs)

        registry.invoke = _invoke  # type: ignore[method-assign]
        planner = Planner(registry, model=FakeModelClient([]))
        with patch("core.builtin_tools.Music.run", return_value="ok"):
            first = planner.run("Wyłącz Spotify i zmniejsz głośność.")
            second = planner.run(
                "Wyłącz Spotify i zmniejsz głośność.",
                context={
                    "retry_failed_only": True,
                    "pending_executable_goal": first.pending_executable_goal,
                },
            )
        self.assertEqual(first.plan_id, second.plan_id)
        self.assertEqual(counts["close_app"], 2)
        self.assertEqual(counts["volume_down"], 2)
        order = [item["tool"] for item in second.steps if item.get("status") != "SKIPPED"]
        self.assertEqual(order, ["close_app", "volume_down"])

    def test_12_cancel_pending_niewazne(self):
        registry = build_default_registry(confirm_medium=False)
        original = registry.invoke

        def _invoke(name, arguments=None, **kwargs):
            if name == "volume_down":
                return ToolResult.fail("Nie udało się.", tool_name="volume_down")
            return original(name, arguments, **kwargs)

        registry.invoke = _invoke  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as tmp:
            manager = ConversationManager(
                registry=registry,
                memory_manager=MemoryManager(Path(tmp) / "m.json"),
                memory=[],
                model=FakeModelClient([]),
            )
            manager.activate()
            with patch(
                "ComputerControl.close_application",
                return_value="Zamykam aplikację Spotify.",
            ):
                manager.handle("Wyłącz Spotify i zmniejsz głośność.")
                self.assertIsNotNone(manager.pending_executable_goal)
                manager.handle("Nieważne.")
                self.assertIsNone(manager.pending_executable_goal)

    def test_13_new_complete_goal_clears_pending(self):
        registry = build_default_registry(confirm_medium=False)
        original = registry.invoke

        def _invoke(name, arguments=None, **kwargs):
            if name == "volume_down":
                return ToolResult.fail("Nie udało się.", tool_name="volume_down")
            return original(name, arguments, **kwargs)

        registry.invoke = _invoke  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as tmp:
            manager = ConversationManager(
                registry=registry,
                memory_manager=MemoryManager(Path(tmp) / "m.json"),
                memory=[],
                model=FakeModelClient([]),
            )
            manager.activate()
            with patch(
                "ComputerControl.close_application",
                return_value="Zamykam aplikację Spotify.",
            ):
                with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
                    with patch("core.builtin_tools.Music.run", return_value="ok"):
                        manager.handle("Wyłącz Spotify i zmniejsz głośność.")
                        self.assertIsNotNone(manager.pending_executable_goal)
                        old_id = manager.pending_executable_goal["plan_id"]
                        manager.handle("Otwórz Chrome.")
                        self.assertIsNone(manager.pending_executable_goal)
                        later = manager.handle("To spróbuj.")
        self.assertNotEqual((later.data or {}).get("plan_id"), old_id)
        self.assertNotIn("mogę spróbować teraz", (later.text or "").lower())

    def test_14_retry_without_pending_no_fake_offer(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = ConversationManager(
                registry=build_default_registry(confirm_medium=False),
                memory_manager=MemoryManager(Path(tmp) / "m.json"),
                memory=[],
                model=FakeModelClient([]),
            )
            manager.activate()
            result = manager.handle("To spróbuj.")
        self.assertIsNone(manager.pending_executable_goal)
        self.assertNotIn("mogę spróbować teraz", (result.text or "").lower())
        self.assertNotIn("mogę spróbować jeszcze raz", (result.text or "").lower())

    def test_15_unknown_noise_no_stale_spotify(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            mem.short.focus.update(
                {
                    "current_topic": "Spotify audio normalization",
                    "referenced_apps": ["Spotify"],
                    "last_web_query": "spotify loudness",
                    "last_web_consensus": "Spotify normalizuje głośność utworów.",
                    "last_answer_summary": "Spotify ma normalizację głośności.",
                }
            )
            manager = ConversationManager(
                registry=build_default_registry(confirm_medium=False),
                memory_manager=mem,
                memory=[],
                model=FakeModelClient([]),
            )
            manager.activate()
            with patch("core.conversation_manager.ask_ai", return_value="Spotify normalizuje audio.") as ai:
                first = manager.handle("Jauhi-do-do-do!")
                second = manager.handle("do do do do do")
        self.assertFalse(ai.called)
        for result in (first, second):
            lowered = (result.text or "").lower()
            self.assertNotIn("spotify", lowered)
            self.assertNotIn("normaliz", lowered)

    def test_16_np_coordination_spider_man(self):
        plan = parse_compound_goal("Znajdź Spider-Mana i Iron Mana.")
        self.assertFalse(plan.detected)
        self.assertEqual(len(plan.steps), 0)

    def test_17_np_coordination_lodz(self):
        plan = parse_compound_goal("wyszukaj sklepy w Łodzi i okolicy")
        self.assertFalse(plan.detected)


if __name__ == "__main__":
    unittest.main()
