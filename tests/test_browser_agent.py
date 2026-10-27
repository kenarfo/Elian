# ==========================
# ELIAN — BrowserAgent Stage D tests (local HTML fixtures, no live internet)
# ==========================
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from core.browser_agent import (
    BrowserAgent,
    reset_browser_agent_for_tests,
)
from core.browser_dom import FakeBrowserBackend
from core.browser_observation import BLOCKED, FAILED, SUCCESS, UNAVAILABLE
from core.builtin_tools import build_default_registry
from core.model_client import FakeModelClient, reset_model_client
from core.planner import Planner
from core.tool_result import ToolStatus
from core.tools import reset_registry_for_tests
from core.world_state import get_observation, reset_world_state

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "browser"


def _uri(name: str) -> str:
    return (FIXTURES / name).resolve().as_uri()


def _fake_agent() -> BrowserAgent:
    backend = FakeBrowserBackend(fixture_root=FIXTURES)
    return reset_browser_agent_for_tests(BrowserAgent(backend=backend, available=True))


class BrowserUnavailableTests(unittest.TestCase):
    def tearDown(self):
        reset_browser_agent_for_tests(BrowserAgent())

    def test_default_opt_out_is_unavailable(self):
        agent = reset_browser_agent_for_tests(BrowserAgent())
        result = agent.act("browser_open_url", {"url": _uri("home.html")})
        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolStatus.UNAVAILABLE)
        self.assertIn("przeglądark", result.message.lower())
        self.assertEqual((result.data or {}).get("browser_status"), UNAVAILABLE)

    def test_opt_in_without_playwright_is_unavailable(self):
        with patch("core.browser_agent.browser_opt_in", return_value=True):
            with patch("core.browser_agent.playwright_installed", return_value=False):
                agent = reset_browser_agent_for_tests(BrowserAgent())
                result = agent.act("browser_search_service", {"query": "x"})
        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolStatus.UNAVAILABLE)
        self.assertIn("playwright", result.message.lower())

    def test_registry_close_tab_stays_unavailable_by_default(self):
        reset_browser_agent_for_tests(BrowserAgent())
        reset_registry_for_tests()
        result = build_default_registry(confirm_medium=False).invoke(
            "close_browser_tab", {"name": "Spotify"}
        )
        self.assertFalse(result.success)
        self.assertEqual(result.status, ToolStatus.UNAVAILABLE)
        self.assertEqual(result.tool_name, "close_browser_tab")


class FakeBrowserFlowTests(unittest.TestCase):
    def setUp(self):
        reset_world_state()
        self.agent = _fake_agent()

    def tearDown(self):
        reset_browser_agent_for_tests(BrowserAgent())
        reset_world_state()

    def test_open_find_click_and_fill(self):
        opened = self.agent.act("browser_open_url", {"url": _uri("home.html")})
        self.assertTrue(opened.success)
        self.assertEqual((opened.data or {}).get("browser_status"), SUCCESS)
        self.assertIn("Elian Home", opened.message)

        found = self.agent.act(
            "browser_find_element",
            {"role": "link", "text": "Czytaj artykuł"},
        )
        self.assertTrue(found.success)

        clicked = self.agent.act("browser_click", {"role": "link", "text": "Czytaj artykuł"})
        self.assertTrue(clicked.success)
        after = (clicked.data or {}).get("after") or {}
        self.assertIn("article.html", str(after.get("url") or ""))

        form = self.agent.act("browser_open_url", {"url": _uri("form.html")})
        self.assertTrue(form.success)
        typed = self.agent.act(
            "browser_type",
            {"label": "Imię", "text": "Ada"},
        )
        self.assertTrue(typed.success)
        selected = self.agent.act("browser_select", {"label": "Kolor", "value": "zielony"})
        self.assertTrue(selected.success)
        checked = self.agent.act("browser_check", {"label": "Zgoda", "checked": True})
        self.assertTrue(checked.success)

    def test_multi_step_navigation_back_forward(self):
        self.agent.act("browser_open_url", {"url": _uri("home.html")})
        self.agent.act("browser_click", {"role": "link", "text": "Czytaj artykuł"})
        back = self.agent.act("browser_back", {})
        self.assertTrue(back.success)
        self.assertIn("home.html", str(((back.data or {}).get("after") or {}).get("url") or ""))
        forward = self.agent.act("browser_forward", {})
        self.assertTrue(forward.success)
        self.assertIn("article.html", str(((forward.data or {}).get("after") or {}).get("url") or ""))

    def test_timeout_and_missing_element(self):
        self.agent.act("browser_open_url", {"url": _uri("home.html")})
        missing = self.agent.act("browser_click", {"role": "link", "text": "tego nie ma"})
        self.assertFalse(missing.success)
        self.assertEqual(missing.status, ToolStatus.FAILURE)
        self.assertIn("nie znalaz", missing.message.lower())

        timed = self.agent.act(
            "browser_wait",
            {"selector": "#never-here", "timeout": 0.15},
        )
        self.assertFalse(timed.success)
        self.assertEqual(timed.status, ToolStatus.TIMEOUT)

    def test_popup_new_tab(self):
        self.agent.act("browser_open_url", {"url": _uri("home.html")})
        clicked = self.agent.act("browser_click", {"role": "link", "text": "Otwórz popup"})
        self.assertTrue(clicked.success)
        after = (clicked.data or {}).get("after") or {}
        self.assertGreaterEqual(int(after.get("tab_count") or 0), 2)
        self.assertIn("Nowa karta", str(after.get("title") or ""))

    def test_blocked_page_is_not_success(self):
        result = self.agent.act("browser_open_url", {"url": _uri("blocked.html")})
        self.assertFalse(result.success)
        self.assertEqual((result.data or {}).get("browser_status"), BLOCKED)
        self.assertIn("blok", result.message.lower())

    def test_password_from_memory_blocked(self):
        self.agent.act("browser_open_url", {"url": _uri("form.html")})
        typed = self.agent.act(
            "browser_type",
            {"label": "Hasło", "text": "hunter2"},
        )
        self.assertFalse(typed.success)
        self.assertIn("haseł", typed.message.lower())
        self.assertEqual(typed.error, "secret_blocked")

    def test_purchase_needs_confirmation(self):
        self.agent.act("browser_open_url", {"url": _uri("form.html")})
        buy = self.agent.act("browser_click", {"text": "Kup teraz"})
        self.assertEqual(buy.status, ToolStatus.NEEDS_CONFIRMATION)
        confirmed = self.agent.act(
            "browser_click",
            {"text": "Kup teraz", "confirmed": True},
        )
        self.assertTrue(confirmed.success)

    def test_search_and_select_result(self):
        self.agent.act("browser_open_url", {"url": _uri("search.html")})
        search = self.agent.act("browser_search_service", {"query": "Minecraft"})
        self.assertTrue(search.success)
        chosen = self.agent.act("browser_select_result", {"index": 0})
        self.assertTrue(chosen.success)
        self.assertIn("article.html", str(((chosen.data or {}).get("after") or {}).get("url") or ""))

    def test_world_state_note_does_not_break_stage_b_keys(self):
        self.agent.act("browser_open_url", {"url": _uri("home.html")})
        obs = get_observation("browser")
        self.assertIsNotNone(obs)
        self.assertIn("url", (obs.payload if obs else {}))
        from core.world_state import get_world_state

        self.assertEqual(
            set(get_world_state().keys()),
            {"services", "conversation", "active_plan", "smart_home", "computer", "screen"},
        )

    def test_screenshot_is_diagnostic_not_required(self):
        self.agent.act("browser_open_url", {"url": _uri("home.html")})
        shot = self.agent.act("browser_screenshot", {})
        self.assertTrue(shot.success)
        self.assertTrue(((shot.data or {}).get("verification") or {}).get("diagnostic"))

    def test_step_budget_stops(self):
        self.agent.act("browser_launch", {})
        self.agent.step_count = 50
        blocked = self.agent.act("browser_read_title", {})
        self.assertFalse(blocked.success)
        self.assertEqual((blocked.data or {}).get("browser_status"), FAILED)


class BrowserPlannerIntegrationTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        self.agent = _fake_agent()
        self.registry = build_default_registry(confirm_medium=False)

    def tearDown(self):
        reset_registry_for_tests(None)
        reset_model_client()
        reset_browser_agent_for_tests(BrowserAgent())

    def test_planner_invokes_browser_tools_not_a_second_loop(self):
        home = _uri("home.html")
        planner = Planner(self.registry, model=FakeModelClient([]))
        invoked = self.registry.invoke("browser_open_url", {"url": home})
        self.assertTrue(invoked.success)
        clicked = self.registry.invoke(
            "browser_click",
            {"role": "link", "text": "Czytaj artykuł"},
        )
        self.assertTrue(clicked.success)
        self.assertEqual(clicked.tool_name, "browser_click")
        # Compound still uses ToolRegistry; Planner does not grow a nested browser loop.
        self.assertTrue(hasattr(planner, "run"))
        self.assertFalse(hasattr(self.agent, "run_plan"))

    def test_youtube_search_stays_unavailable_without_opt_in(self):
        reset_browser_agent_for_tests(BrowserAgent())
        registry = build_default_registry(confirm_medium=False)
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            result = Planner(registry, model=FakeModelClient([])).run(
                "Otwórz YouTube i wyszukaj Minecraft."
            )
        self.assertGreaterEqual(len(result.steps), 2)
        self.assertEqual(result.steps[1].get("tool"), "browser_search_service")
        self.assertEqual(result.steps[1]["observation"].get("status"), "unavailable")
        # DISABLED (opt-in false) — honest disabled copy, not capability-blind "nie umiem".
        lowered = result.message.lower()
        self.assertTrue(
            "wyłączon" in lowered or "wylaczon" in lowered or "konfiguracji" in lowered,
            result.message,
        )


@unittest.skipUnless(
    __import__("core.browser_playwright", fromlist=["playwright_importable"]).playwright_importable(),
    "Playwright not installed",
)
class PlaywrightFixtureTests(unittest.TestCase):
    def setUp(self):
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
        import threading

        from core.browser_playwright import PlaywrightBrowserBackend

        root = str(FIXTURES.resolve())

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=root, **kwargs)

            def log_message(self, *_args):
                return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._base = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

        self.backend = PlaywrightBrowserBackend()
        self.agent = reset_browser_agent_for_tests(
            BrowserAgent(backend=self.backend, available=True)
        )
        opened = self.agent.act(
            "browser_launch", {"headless": True, "url": f"{self._base}/home.html"}
        )
        if not opened.success:
            self.skipTest(opened.message or "Chromium unavailable")

    def tearDown(self):
        try:
            self.agent.close()
        except Exception:
            pass
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except Exception:
            pass
        reset_browser_agent_for_tests(BrowserAgent())

    def _url(self, name: str) -> str:
        return f"{self._base}/{name}"

    def test_playwright_open_click_fill(self):
        opened = self.agent.act("browser_open_url", {"url": self._url("home.html")})
        self.assertTrue(opened.success, opened.message)
        clicked = self.agent.act("browser_click", {"role": "link", "text": "Formularz"})
        self.assertTrue(clicked.success, clicked.message)
        typed = self.agent.act("browser_type", {"label": "Imię", "text": "Ada"})
        self.assertTrue(typed.success, typed.message)

    def test_playwright_popup_and_timeout(self):
        self.agent.act("browser_open_url", {"url": self._url("home.html")})
        popup = self.agent.act("browser_click", {"role": "link", "text": "Otwórz popup"})
        self.assertTrue(popup.success, popup.message)
        after = (popup.data or {}).get("after") or {}
        self.assertGreaterEqual(int(after.get("tab_count") or 0), 2)
        timed = self.agent.act("browser_wait", {"selector": "#never", "timeout": 0.12})
        self.assertEqual(timed.status, ToolStatus.TIMEOUT)

    def test_playwright_delayed_and_search(self):
        self.agent.act("browser_open_url", {"url": self._url("dynamic.html")})
        self.agent.act("browser_click", {"role": "button", "name": "Pokaż opóźniony"})
        waited = self.agent.act("browser_wait", {"selector": "#delayed", "timeout": 3.0})
        self.assertTrue(waited.success, waited.message)
        page = self.agent.act("browser_read_page", {})
        self.assertIn("opóźniony", (page.message or "").lower())

        self.agent.act("browser_open_url", {"url": self._url("search.html")})
        searched = self.agent.act("browser_search_service", {"query": "Minecraft"})
        self.assertTrue(searched.success, searched.message)
        chosen = self.agent.act("browser_select_result", {"index": 0})
        self.assertTrue(chosen.success, chosen.message)
        self.assertIn(
            "article.html",
            str((((chosen.data or {}).get("after") or {}).get("url") or "")),
        )


class CapabilityExperimentalTests(unittest.TestCase):
    def tearDown(self):
        reset_browser_agent_for_tests(BrowserAgent())

    def test_opt_in_maps_to_registry_ready(self):
        from core.capability_map import READY, snapshot_capabilities
        from core.capability_registry import reset_capability_registry

        reset_capability_registry()
        with patch("core.browser_agent.browser_opt_in", return_value=True):
            with patch("core.browser_agent.playwright_installed", return_value=True):
                with patch(
                    "core.browser_agent.browser_runtime_available", return_value=True
                ):
                    caps = snapshot_capabilities()
        self.assertEqual(caps["Browser"].status, READY)


if __name__ == "__main__":
    unittest.main()
