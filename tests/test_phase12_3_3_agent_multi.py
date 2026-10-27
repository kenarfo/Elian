"""Phase 12.3.3 — compound APP+SYSTEM, app-first, official web fallback, claims."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.action_claim import HONEST_NO_TOOL, detect_all_action_claims, guard_action_claim
from core.builtin_tools import build_default_registry
from core.compound_plan import is_cross_domain_compound, parse_compound_goal
from core.conversation_manager import ConversationManager
from core.fast_path import FastPathStatus, evaluate_fast_path
from core.intent_family import IntentFamily, classify_intent_family
from core.model_client import FakeModelClient, reset_model_client
from core.official_services import official_url_for
from core.planner import Planner
from core.service_open import KIND_APP, KIND_WEBSITE, open_named_service
from core.tool_result import ToolResult
from core.tool_shortlist import shortlist_tools
from core.tools import reset_registry_for_tests
from memory.manager import MemoryManager

try:
    from tests.volume_sim import simulated_system_volume
except ImportError:
    from volume_sim import simulated_system_volume


class AppFirstTests(unittest.TestCase):
    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()

    def test_installed_spotify_opens_local_app(self):
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")) as launch:
            with patch("WebActions.open_website", return_value=(True, "web")) as web:
                result = build_default_registry(confirm_medium=False).invoke(
                    "open_app", {"name": "Spotify"}
                )
        self.assertTrue(result.success)
        self.assertEqual(result.data.get("resolved_kind"), KIND_APP)
        self.assertTrue(result.data.get("app_found"))
        self.assertFalse(result.data.get("fallback_used"))
        self.assertIn("Spotify otwarte", result.message)
        launch.assert_called()
        web.assert_not_called()

    def test_missing_spotify_official_website_fallback(self):
        with patch("core.builtin_tools.launch_application", return_value=(False, "brak")):
            with patch(
                "WebActions.open_website",
                return_value=(True, "ok"),
            ) as web:
                result = build_default_registry(confirm_medium=False).invoke(
                    "open_app", {"name": "Spotify"}
                )
        self.assertTrue(result.success)
        self.assertEqual(result.data.get("resolved_kind"), KIND_WEBSITE)
        self.assertTrue(result.data.get("fallback_used"))
        self.assertTrue(result.data.get("official_url_verified"))
        self.assertIn("oficjaln", result.message.lower())
        self.assertNotIn("aplikację Spotify", result.message)
        web.assert_called()
        url = str(web.call_args[0][0])
        self.assertEqual(url, official_url_for("spotify"))
        self.assertIn("spotify", url.lower())

    def test_explicit_website_skips_app_lookup(self):
        with patch("core.builtin_tools.launch_application", return_value=(True, "app")) as launch:
            with patch("WebActions.open_website", return_value=(True, "ok")) as web:
                result = build_default_registry(confirm_medium=False).invoke(
                    "open_app",
                    {
                        "name": "Spotify",
                        "website_direct": True,
                        "text": "Otwórz stronę Spotify.",
                    },
                )
        self.assertTrue(result.success)
        self.assertEqual(result.data.get("resolved_kind"), KIND_WEBSITE)
        self.assertFalse(result.data.get("app_checked"))
        launch.assert_not_called()
        web.assert_called()

    def test_spotify_com_is_website_direct(self):
        info = classify_intent_family("Wejdź na spotify.com.")
        self.assertTrue(info.slots.get("website_direct"))
        self.assertTrue(info.is_complete)

    def test_explicit_app_wording_uses_resolver(self):
        info = classify_intent_family("Otwórz aplikację Spotify.")
        self.assertTrue(info.slots.get("explicit_app"))
        self.assertFalse(info.slots.get("website_direct"))
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            result = build_default_registry(confirm_medium=False).invoke(
                "open_app",
                {"name": "Spotify", "explicit_app": True, "text": "Otwórz aplikację Spotify."},
            )
        self.assertEqual(result.data.get("resolved_kind"), KIND_APP)

    def test_explicit_app_missing_truthful_fallback(self):
        with patch("core.builtin_tools.launch_application", return_value=(False, "brak")):
            with patch("WebActions.open_website", return_value=(True, "ok")):
                result = build_default_registry(confirm_medium=False).invoke(
                    "open_app",
                    {
                        "name": "Spotify",
                        "explicit_app": True,
                        "text": "Otwórz aplikację Spotify.",
                    },
                )
        self.assertTrue(result.success)
        self.assertTrue(result.data.get("fallback_used"))
        self.assertIn("Nie znalazłem aplikacji", result.message)
        self.assertIn("oficjaln", result.message.lower())

    def test_unknown_app_no_guessed_url(self):
        with patch("core.builtin_tools.launch_application", return_value=(False, "brak")):
            with patch("WebActions.open_website", return_value=(True, "ok")) as web:
                with patch("WebActions.search_web") as search:
                    result = build_default_registry(confirm_medium=False).invoke(
                        "open_app", {"name": "FoobarXYZ"}
                    )
        self.assertFalse(result.success)
        self.assertIn("pewnej oficjalnej strony", result.message)
        self.assertNotIn("foobarxyz.com", result.message.lower())
        web.assert_not_called()
        search.assert_not_called()

    def test_open_named_service_never_uses_search(self):
        with patch("AppLauncher.find_start_menu_app", return_value=None):
            with patch("WebActions.search_web") as search:
                with patch("WebActions.open_website", return_value=(True, "ok")):
                    opened = open_named_service("Spotify")
        self.assertTrue(opened.fallback_used)
        search.assert_not_called()
        self.assertTrue(opened.official_url_verified)


class AgentMultiContractTests(unittest.TestCase):
    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()

    def test_compound_two_semantic_steps(self):
        plan = parse_compound_goal("Otwórz Spotify i zwiększ głośność.")
        self.assertTrue(plan.detected)
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.steps[0].semantic_action, "open_service")
        self.assertEqual(plan.steps[1].semantic_action, "adjust_volume")
        self.assertTrue(plan.complete)
        self.assertTrue(is_cross_domain_compound("Otwórz Spotify i zwiększ głośność."))

    def test_compound_classification_complete(self):
        info = classify_intent_family("Otwórz Spotify i zwiększ głośność.")
        self.assertTrue(info.slots.get("compound"))
        self.assertTrue(info.is_complete)
        self.assertEqual(info.slots.get("families"), ["APP", "SYSTEM"])
        self.assertTrue(info.slots["compound_steps"][0]["complete"])
        self.assertTrue(info.slots["compound_steps"][1]["complete"])

    def test_chrome_volume_compound(self):
        info = classify_intent_family("Otwórz Chrome i zmniejsz głośność.")
        self.assertTrue(info.slots.get("compound"))
        self.assertEqual(info.slots["compound_steps"][1]["slots"]["direction"], "decrease")

    def test_explicit_website_compound(self):
        info = classify_intent_family("Otwórz stronę Spotify i zwiększ głośność.")
        self.assertTrue(info.slots.get("website_direct"))
        self.assertTrue(info.slots.get("compound"))

    def test_shortlist_covers_app_and_volume(self):
        info = classify_intent_family("Otwórz Spotify i zwiększ głośność.")
        catalog = build_default_registry(confirm_medium=False).descriptions_for_planner()
        names = [
            item.get("name")
            for item in shortlist_tools(catalog, goal=info.raw_text, classification=info)
        ]
        self.assertIn("open_app", names)
        self.assertIn("volume_up", names)

    def test_planner_steps_and_legacy_zero(self):
        registry = build_default_registry(confirm_medium=False)
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                planner = Planner(registry, model=FakeModelClient([]))
                result = planner.run("Otwórz Spotify i zwiększ głośność.")
        self.assertGreaterEqual(len(result.steps), 2)
        self.assertEqual(result.steps[0]["tool"], "open_app")
        self.assertEqual(result.steps[1]["tool"], "volume_up")
        self.assertEqual(result.source, "compound")

    def test_installed_resolved_kind_app(self):
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                result = Planner(
                    build_default_registry(confirm_medium=False),
                    model=FakeModelClient([]),
                ).run("Otwórz Spotify i zwiększ głośność.")
        data = result.steps[0]["observation"].get("data") or {}
        self.assertEqual(data.get("resolved_kind"), KIND_APP)

    def test_missing_resolved_kind_website(self):
        with patch("core.builtin_tools.launch_application", return_value=(False, "brak")):
            with patch("WebActions.open_website", return_value=(True, "ok")):
                with patch("core.builtin_tools.Music.run", return_value="ok"):
                    result = Planner(
                        build_default_registry(confirm_medium=False),
                        model=FakeModelClient([]),
                    ).run("Otwórz Spotify i zwiększ głośność.")
        data = result.steps[0]["observation"].get("data") or {}
        self.assertEqual(data.get("resolved_kind"), KIND_WEBSITE)
        self.assertTrue(data.get("fallback_used"))

    def test_both_success_allows_both_claims(self):
        obs = [
            {"tool": "open_app", "success": True, "data": {"resolved_kind": "APP"}},
            {"tool": "volume_up", "success": True, "data": {}},
        ]
        guarded = guard_action_claim(
            "Otworzyłem Spotify i zwiększyłem głośność.",
            obs,
        )
        self.assertFalse(guarded.blocked)

    def test_one_success_cannot_authorize_two_claims(self):
        guarded = guard_action_claim(
            "Otworzyłem Spotify i zwiększyłem głośność.",
            [{"tool": "open_app", "success": True, "data": {}}],
        )
        self.assertTrue(guarded.blocked)
        self.assertEqual(guarded.text, HONEST_NO_TOOL)

    def test_future_claim_blocked_without_tools(self):
        guarded = guard_action_claim("Otworzę Spotify i zwiększę głośność.", [])
        self.assertTrue(guarded.blocked)
        self.assertEqual(guarded.text, HONEST_NO_TOOL)
        self.assertIn("app_open", detect_all_action_claims("Otworzę Spotify i zwiększę głośność."))
        self.assertIn("volume", detect_all_action_claims("Otworzę Spotify i zwiększę głośność."))

    def test_future_volume_alone_blocked(self):
        guarded = guard_action_claim("Zwiększę głośność.", [])
        self.assertTrue(guarded.blocked)

    def test_zero_tools_future_compound_blocked(self):
        guarded = guard_action_claim("Otworzę Spotify i zwiększę głośność.", [])
        self.assertTrue(guarded.blocked)

    def test_partial_spotify_ok_volume_fail(self):
        registry = build_default_registry(confirm_medium=False)
        original = registry.invoke

        def _invoke(name, arguments=None, **kwargs):
            if name == "volume_up":
                return ToolResult.fail("Nie udało się.", tool_name="volume_up")
            return original(name, arguments, **kwargs)

        registry.invoke = _invoke  # type: ignore[method-assign]
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            result = Planner(registry, model=FakeModelClient([])).run(
                "Otwórz Spotify i zwiększ głośność."
            )
        self.assertFalse(result.success)
        self.assertIn("Spotify", result.message)
        self.assertIn("nie udało", result.message.lower())
        self.assertIn("głośn", result.message.lower())

    def test_partial_spotify_fail_volume_ok(self):
        with patch("core.builtin_tools.launch_application", return_value=(False, "brak")):
            with patch("WebActions.open_website", return_value=(False, "brak")):
                with simulated_system_volume():
                    result = Planner(
                        build_default_registry(confirm_medium=False),
                        model=FakeModelClient([]),
                    ).run("Otwórz Spotify i zwiększ głośność.")
        self.assertFalse(result.success)
        self.assertIn("nie udało mi się otworzyć", result.message.lower())
        self.assertIn("głośn", result.message.lower())

    def test_single_app_not_compound(self):
        info = classify_intent_family("Otwórz Spotify.")
        self.assertFalse(info.slots.get("compound"))
        self.assertTrue(info.is_complete)
        self.assertEqual(info.family, IntentFamily.APP)

    def test_single_volume_fastpath_regression(self):
        evaluation = evaluate_fast_path("głośniej")
        self.assertEqual(evaluation.status, FastPathStatus.HANDLED_COMPLETE)
        evaluation_multi = evaluate_fast_path("Otwórz Spotify i zwiększ głośność.")
        self.assertEqual(evaluation_multi.status, FastPathStatus.NOT_HANDLED)

    def test_conversation_manager_agent_not_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            manager = ConversationManager(
                registry=build_default_registry(confirm_medium=False),
                memory_manager=mem,
                memory=[],
                model=FakeModelClient([]),
            )
            manager.activate()
            with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
                with patch("core.builtin_tools.Music.run", return_value="ok"):
                    result = manager.handle("Otwórz Spotify i zwiększ głośność.")
        self.assertEqual(result.source, "agent")
        self.assertGreaterEqual(int((result.data or {}).get("steps") or 0), 2)
        self.assertNotIn(result.source, ("skill", "command", "intent"))

    def test_website_followup_rewrites_to_official_site(self):
        from core.conversation_focus import enrich_goal_with_focus, update_focus

        focus = update_focus(None, user_text="Otwórz Spotify.")
        rewritten = enrich_goal_with_focus("A teraz stronę.", focus)
        self.assertIn("stronę", rewritten.lower())
        self.assertIn("spotify", rewritten.lower())


if __name__ == "__main__":
    unittest.main()
