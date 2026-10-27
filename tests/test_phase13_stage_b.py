"""Phase 13 Stage B — multi-domain planner, ActiveService, contracts."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import HaActions
import HomeAssistant
from core.active_service import (
    KIND_APP,
    KIND_WEBSITE,
    ActiveServiceContext,
    capabilities_for,
    get_active_service,
    reset_active_service,
    set_active_service,
)
from core.builtin_tools import build_default_registry
from core.compound_execute import source_kind_for_step
from core.compound_plan import is_cross_domain_compound, parse_compound_goal
from core.context_selection import select_context
from core.conversation_manager import ConversationManager
from core.intent_family import IntentFamily, classify_intent_family
from core.model_client import FakeModelClient, reset_model_client
from core.official_services import official_url_for
from core.planner import Planner
from core.semantic_media import parse_media_goal
from core.tool_result import ToolStatus
from core.tool_shortlist import shortlist_tools
from core.tools import reset_registry_for_tests
from memory.manager import MemoryManager


SAMPLE_LIGHT = {
    "entity_id": "light.vizzini",
    "state": "on",
    "attributes": {
        "friendly_name": "Inspire Vizzini D56 Vizzini",
        "supported_color_modes": ["rgb", "color_temp", "brightness"],
        "brightness": 128,
    },
}
CLIMATE = {
    "entity_id": "climate.salon",
    "state": "heat",
    "attributes": {"friendly_name": "Klimatyzacja salon", "temperature": 22},
}


class StageBFoundationTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        reset_active_service()
        HomeAssistant.get_registry().load_states([SAMPLE_LIGHT, CLIMATE])

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()
        reset_active_service()

    def test_app_system_ha_three_steps(self):
        plan = parse_compound_goal("Otwórz Spotify, zwiększ głośność i wyłącz lampę.")
        self.assertTrue(plan.detected)
        self.assertEqual([s.family for s in plan.steps], ["APP", "SYSTEM", "SMART_HOME"])
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                with patch.object(HaActions, "_call_service", return_value=True):
                    result = Planner(
                        build_default_registry(confirm_medium=False),
                        model=FakeModelClient([]),
                    ).run("Otwórz Spotify, zwiększ głośność i wyłącz lampę.")
        self.assertEqual(result.source, "compound")
        self.assertGreaterEqual(len(result.steps), 3)
        self.assertEqual(result.steps[0]["family"], "APP")
        self.assertEqual(result.steps[1]["family"], "SYSTEM")
        self.assertEqual(result.steps[2]["family"], "SMART_HOME")
        self.assertEqual(result.steps[0]["tool"], "open_app")
        self.assertEqual(result.steps[1]["tool"], "volume_up")
        self.assertEqual(result.steps[2]["tool"], "turn_off_device")

    def test_ha_ha_climate_and_light(self):
        plan = parse_compound_goal("Wyłącz lampę i ustaw klimę na 22 stopnie.")
        self.assertEqual([s.family for s in plan.steps], ["SMART_HOME", "SMART_HOME"])
        self.assertEqual(plan.steps[0].semantic_action, "turn_off")
        self.assertEqual(plan.steps[1].semantic_action, "set_temperature")
        with patch.object(HaActions, "_call_service", return_value=True):
            result = Planner(
                build_default_registry(confirm_medium=False),
                model=FakeModelClient([]),
            ).run("Wyłącz lampę i ustaw klimę na 22 stopnie.")
        self.assertEqual(result.source, "compound")
        self.assertEqual(result.steps[0]["tool"], "turn_off_device")
        self.assertEqual(result.steps[1]["tool"], "set_device_value")

    def test_app_ha_close_chrome_and_light(self):
        plan = parse_compound_goal("Zamknij Chrome i wyłącz lampę.")
        self.assertEqual([s.family for s in plan.steps], ["APP", "SMART_HOME"])
        with patch("ComputerControl.close_application", return_value="Zamykam aplikację Chrome."):
            with patch.object(HaActions, "_call_service", return_value=True):
                result = Planner(
                    build_default_registry(confirm_medium=False),
                    model=FakeModelClient([]),
                ).run("Zamknij Chrome i wyłącz lampę.")
        self.assertEqual(result.steps[0]["tool"], "close_app")
        self.assertEqual(result.steps[1]["tool"], "turn_off_device")

    def test_youtube_search_browser_unavailable(self):
        plan = parse_compound_goal("Otwórz YouTube i wyszukaj Minecraft.")
        self.assertEqual(plan.steps[1].semantic_action, "browser_search_service")
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            result = Planner(
                build_default_registry(confirm_medium=False),
                model=FakeModelClient([]),
            ).run("Otwórz YouTube i wyszukaj Minecraft.")
        self.assertFalse(result.steps[1]["observation"].get("success"))
        self.assertEqual(result.steps[1]["observation"].get("status"), "unavailable")
        lowered = result.message.lower()
        self.assertTrue(
            "wyłączon" in lowered or "wylaczon" in lowered or "konfiguracji" in lowered,
            result.message,
        )
        self.assertNotIn("wyszukałem", result.message.lower())

    def test_chrome_youtube_minecraft_three_semantic_steps(self):
        plan = parse_compound_goal("Otwórz Chrome, wejdź na YouTube i wyszukaj Minecraft.")
        self.assertGreaterEqual(len(plan.steps), 3)
        self.assertEqual(plan.steps[0].family, "APP")
        self.assertEqual(plan.steps[1].family, "WEB")
        self.assertEqual(plan.steps[2].semantic_action, "browser_search_service")

    def test_np_and_info_not_compound(self):
        self.assertFalse(parse_compound_goal("Znajdź Spider-Mana i Iron Mana").detected)
        self.assertFalse(is_cross_domain_compound("Wyszukaj sklepy w Łodzi i okolicy."))
        self.assertFalse(parse_compound_goal("Powiedz coś o Japonii i Korei.").detected)

    def test_source_per_step_not_whole_utterance_web(self):
        plan = parse_compound_goal("Co robi klima i otwórz Spotify.")
        self.assertEqual(plan.steps[0].slots.get("source_kind"), "LOCAL_HA")
        self.assertEqual(plan.steps[1].slots.get("source_kind"), "LOCAL_COMPUTER")
        self.assertEqual(
            source_kind_for_step(plan.steps[0].family, plan.steps[0].semantic_action, plan.steps[0].text),
            "LOCAL_HA",
        )

    def test_shortlist_union_strips_unrelated(self):
        info = classify_intent_family("Otwórz Spotify, zwiększ głośność i wyłącz lampę.")
        catalog = build_default_registry(confirm_medium=False).descriptions_for_planner()
        names = [
            item.get("name")
            for item in shortlist_tools(catalog, goal=info.raw_text, classification=info)
        ]
        self.assertIn("open_app", names)
        self.assertIn("volume_up", names)
        self.assertIn("turn_off_device", names)
        self.assertNotIn("volume_down", names)
        self.assertNotIn("web_search", names)
        self.assertNotIn("create_reminder", names)
        self.assertNotIn("describe_screen", names)

    def test_active_service_app_not_website(self):
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            result = build_default_registry(confirm_medium=False).invoke(
                "open_app", {"name": "Spotify"}
            )
        self.assertEqual(result.data.get("resolved_kind"), KIND_APP)
        service = get_active_service()
        self.assertIsNotNone(service)
        self.assertEqual(service.resolved_kind, KIND_APP)
        self.assertIn("CLOSE", service.capabilities)

    def test_active_service_website_fallback_does_not_set_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            with patch("core.builtin_tools.launch_application", return_value=(False, "brak")):
                with patch("WebActions.open_website", return_value=(True, "ok")):
                    result = build_default_registry(confirm_medium=False).invoke(
                        "open_app", {"name": "Spotify"}
                    )
                    mem.note_tool_success("open_app", {"name": "Spotify"}, result.to_observation())
        self.assertEqual(result.data.get("resolved_kind"), KIND_WEBSITE)
        service = get_active_service()
        self.assertEqual(service.resolved_kind, KIND_WEBSITE)
        self.assertNotIn("CLOSE", service.capabilities)
        self.assertIsNone(mem.short.get_entity("application"))

    def test_close_website_is_unavailable_not_close_app(self):
        set_active_service(
            ActiveServiceContext(
                service_name="Spotify",
                resolved_kind=KIND_WEBSITE,
                official_url="https://open.spotify.com",
                capabilities=["OPEN"],
            )
        )
        with patch("ComputerControl.close_application") as close:
            result = build_default_registry(confirm_medium=False).invoke(
                "close_app", {"name": "Spotify"}
            )
        close.assert_not_called()
        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolStatus.UNAVAILABLE)
        self.assertEqual(result.tool_name, "close_browser_tab")

    def test_context_small_talk_excludes_service(self):
        selection = select_context("Jak się masz?")
        self.assertEqual(
            selection.excluded, ["service", "device", "browser", "execution_plan"]
        )

    def test_referent_ambiguity_clarifies(self):
        selection = select_context(
            "Zamknij to.",
            close_candidates=[
                {"name": "Spotify", "confidence": 0.48},
                {"name": "Chrome", "confidence": 0.45},
            ],
        )
        self.assertTrue(selection.needs_clarification)
        self.assertIn("Spotify", selection.clarification)
        self.assertIn("Chrome", selection.clarification)

    def test_music_semantic_goals(self):
        track = parse_media_goal("Puść Billie Jean.")
        self.assertIsNotNone(track)
        self.assertEqual(track.semantic_action, "play_track")
        self.assertEqual(track.query, "Billie Jean")
        artist = parse_media_goal("Puść coś Michaela Jacksona.")
        self.assertEqual(artist.semantic_action, "play_media")
        self.assertIn("jackson", artist.artist.lower())
        mood = parse_media_goal("Puść coś spokojnego.")
        self.assertIn("spokojn", mood.semantic_query.lower())
        similar = parse_media_goal("Puść coś podobnego.")
        self.assertEqual(similar.referent, "current_media")

    def test_website_capabilities_defer_stage_d(self):
        caps = capabilities_for(KIND_WEBSITE, service_name="Spotify")
        self.assertEqual(caps, ["OPEN"])
        self.assertNotIn("PLAY_TRACK", caps)
        self.assertNotIn("IN_PAGE_SEARCH", caps)

    def test_official_registry_has_verified_urls_only(self):
        self.assertTrue(str(official_url_for("netflix")).startswith("https://www.netflix.com"))
        self.assertTrue(str(official_url_for("google calendar")).startswith("https://calendar.google.com"))
        self.assertIsNone(official_url_for("totally-unknown-brand-xyz"))

    def test_stage_a_close_volume_still_compound(self):
        plan = parse_compound_goal("Wyłącz Chrome i zmniejsz głośność.")
        self.assertEqual([s.family for s in plan.steps], ["APP", "SYSTEM"])
        with patch("ComputerControl.close_application", return_value="Zamykam aplikację Chrome."):
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                result = Planner(
                    build_default_registry(confirm_medium=False),
                    model=FakeModelClient([]),
                ).run("Wyłącz Chrome i zmniejsz głośność.")
        self.assertEqual(result.source, "compound")
        self.assertEqual(result.steps[0]["tool"], "close_app")
        self.assertEqual(result.steps[1]["tool"], "volume_down")

    def test_hardware_phrase_chrome_volume_light(self):
        plan = parse_compound_goal("Otwórz Chrome, zwiększ głośność i wyłącz lampę.")
        self.assertEqual([s.family for s in plan.steps], ["APP", "SYSTEM", "SMART_HOME"])
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                with patch.object(HaActions, "_call_service", return_value=True):
                    result = Planner(
                        build_default_registry(confirm_medium=False),
                        model=FakeModelClient([]),
                    ).run("Otwórz Chrome, zwiększ głośność i wyłącz lampę.")
        self.assertEqual(result.source, "compound")
        self.assertGreaterEqual(len(result.steps), 3)
        self.assertNotEqual(result.source, "legacy")


class StageBConversationTests(unittest.TestCase):
    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()
        reset_active_service()

    def test_small_talk_does_not_force_service_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = MemoryManager(Path(tmp) / "m.json")
            mgr = ConversationManager(
                registry=build_default_registry(confirm_medium=False),
                memory_manager=mem,
                memory=[],
                model=FakeModelClient(
                    [{"type": "final_answer", "message": "Dobrze, a Ty?"}]
                ),
            )
            mgr.activate()
            ctx = mgr._agent_context(raw_text="Jak się masz?", goal_text="Jak się masz?")
        self.assertEqual(ctx.get("entities") or {}, {})
        self.assertEqual(ctx.get("active_device") or {}, {})
        excluded = ((ctx.get("context_selection") or {}).get("excluded") or [])
        self.assertIn("service", excluded)
