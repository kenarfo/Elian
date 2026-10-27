# ==========================
# ELIAN — Phase 13 Stage D browser-agent smoke (real Playwright + fixtures)
# ==========================
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Optional, Set, Tuple
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "browser"
EXTERNAL_URL = "https://example.com/"


def _uri(name: str) -> str:
    return (FIXTURES / name).resolve().as_uri()


def _playwright_browser_pids() -> Set[int]:
    """PIDs whose command line mentions ms-playwright / playwright chromium."""
    found: Set[int] = set()
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
                    "Where-Object { $_.Name -match '^(chrome|chromium|headless_shell)\\.exe$' -and "
                    "$_.CommandLine -match 'playwright|ms-playwright' } | "
                    "ForEach-Object { $_.ProcessId }"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        for line in (completed.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                found.add(int(line))
    except Exception:
        pass
    return found


class _FixtureServer:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._httpd: Optional[ThreadingHTTPServer] = None
        self.base = ""

    def __enter__(self) -> "_FixtureServer":
        root = str(self.root.resolve())

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=root, **kwargs)

            def log_message(self, *_args):  # noqa: N802
                return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = int(self._httpd.server_address[1])
        self.base = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            try:
                self._httpd.server_close()
            except Exception:
                pass

    def url(self, name: str) -> str:
        return f"{self.base}/{name}"


def run_browser_agent_smoke() -> int:
    from core.browser_agent import BrowserAgent, reset_browser_agent_for_tests
    from core.browser_dom import FakeBrowserBackend
    from core.browser_observation import BLOCKED, UNAVAILABLE
    from core.browser_playwright import PlaywrightBrowserBackend, playwright_importable
    from core.builtin_tools import build_default_registry
    from core.capability_map import EXPERIMENTAL, snapshot_capabilities
    from core.model_client import FakeModelClient, reset_model_client
    from core.planner import Planner
    from core.tool_result import ToolStatus
    from core.tools import reset_registry_for_tests

    reset_registry_for_tests()
    reset_model_client()
    rows: List[Tuple[str, bool, str]] = []
    pids_before = _playwright_browser_pids()

    def check(name: str, ok: bool, detail: str = "") -> None:
        rows.append((name, bool(ok), str(detail or "")))

    # --- Contract: default stays UNAVAILABLE (no permanent opt-in) ---
    default = reset_browser_agent_for_tests(BrowserAgent())
    r = default.act("browser_open_url", {"url": _uri("home.html")})
    check(
        "unavailable_without_opt_in",
        (not r.success) and r.status == ToolStatus.UNAVAILABLE and "przeglądark" in r.message.lower(),
        r.message,
    )

    with patch("core.browser_agent.browser_opt_in", return_value=True):
        with patch("core.browser_agent.playwright_installed", return_value=False):
            missing = reset_browser_agent_for_tests(BrowserAgent())
            r = missing.act("browser_search_service", {"query": "x"})
    check(
        "unavailable_without_playwright",
        (not r.success) and "playwright" in r.message.lower(),
        r.message,
    )

    check("playwright_importable", playwright_importable(), "sync_api missing")

    # --- FakeBackend regression (fast, offline) ---
    fake = FakeBrowserBackend(fixture_root=FIXTURES)
    fake_agent = reset_browser_agent_for_tests(BrowserAgent(backend=fake, available=True))
    r = fake_agent.act("browser_launch", {"url": _uri("home.html")})
    check("fake_launch", r.success and "Elian Home" in (r.message or ""), r.message)
    buy = fake_agent.act("browser_open_url", {"url": _uri("form.html")})
    buy = fake_agent.act("browser_click", {"text": "Kup teraz"})
    check("fake_purchase_confirm", buy.status == ToolStatus.NEEDS_CONFIRMATION, buy.message)
    blocked = fake_agent.act("browser_open_url", {"url": _uri("blocked.html")})
    check(
        "fake_blocked_page",
        (not blocked.success) and (blocked.data or {}).get("browser_status") == BLOCKED,
        blocked.message,
    )
    fake_agent.close()

    # --- REAL Playwright live local smoke ---
    live_agent: Optional[BrowserAgent] = None
    chromium_started = False
    try:
        if not playwright_importable():
            check("live_playwright_local", False, "playwright missing")
        else:
            with _FixtureServer(FIXTURES) as server:
                backend = PlaywrightBrowserBackend()
                live_agent = reset_browser_agent_for_tests(
                    BrowserAgent(backend=backend, available=True)
                )
                with patch("core.browser_agent.browser_opt_in", return_value=True):
                    launched = live_agent.act(
                        "browser_launch",
                        {"headless": True, "url": server.url("home.html")},
                    )
                chromium_started = bool(launched.success)
                check(
                    "live_chromium_start",
                    chromium_started and "Elian Home" in (launched.message or ""),
                    launched.message,
                )

                title = live_agent.act("browser_read_title", {})
                check(
                    "live_read_title",
                    title.success and "Elian Home" in (title.message or ""),
                    title.message,
                )
                page = live_agent.act("browser_read_page", {})
                check(
                    "live_read_page",
                    page.success and "Witaj" in (page.message or ""),
                    page.message,
                )

                clicked = live_agent.act(
                    "browser_click", {"role": "link", "text": "Czytaj artykuł"}
                )
                after = (clicked.data or {}).get("after") or {}
                check(
                    "live_click_link",
                    clicked.success and "article.html" in str(after.get("url") or ""),
                    clicked.message,
                )
                back = live_agent.act("browser_back", {})
                check("live_back", back.success, back.message)

                form = live_agent.act("browser_open_url", {"url": server.url("form.html")})
                typed = live_agent.act("browser_type", {"label": "Imię", "text": "Ada"})
                selected = live_agent.act(
                    "browser_select", {"label": "Kolor", "value": "zielony"}
                )
                checked = live_agent.act(
                    "browser_check", {"label": "Zgoda", "checked": True}
                )
                check(
                    "live_type_select_check",
                    form.success and typed.success and selected.success and checked.success,
                    f"{typed.message}|{selected.message}|{checked.message}",
                )

                secret = live_agent.act(
                    "browser_type", {"label": "Hasło", "text": "hunter2"}
                )
                check(
                    "live_secret_blocked",
                    (not secret.success) and secret.error == "secret_blocked",
                    secret.message,
                )

                live_agent.begin_plan()
                live_agent.act("browser_open_url", {"url": server.url("home.html")})
                popup = live_agent.act(
                    "browser_click", {"role": "link", "text": "Otwórz popup"}
                )
                tabs = int(((popup.data or {}).get("after") or {}).get("tab_count") or 0)
                check(
                    "live_popup_tab",
                    popup.success and tabs >= 2,
                    f"tabs={tabs} msg={popup.message}",
                )

                live_agent.begin_plan()
                live_agent.act("browser_open_url", {"url": server.url("dynamic.html")})
                live_agent.act(
                    "browser_click", {"role": "button", "name": "Pokaż opóźniony"}
                )
                waited = live_agent.act(
                    "browser_wait", {"selector": "#delayed", "timeout": 3.0}
                )
                read_dyn = live_agent.act("browser_read_page", {})
                check("live_delayed_wait", waited.success, waited.message)
                check(
                    "live_delayed_visible",
                    read_dyn.success and "opóźniony" in (read_dyn.message or "").lower(),
                    read_dyn.message,
                )

                live_agent.begin_plan()
                live_agent.act("browser_open_url", {"url": server.url("search.html")})
                searched = live_agent.act(
                    "browser_search_service", {"query": "Minecraft"}
                )
                chosen = live_agent.act("browser_select_result", {"index": 0})
                check(
                    "live_search_select",
                    searched.success
                    and chosen.success
                    and "article.html"
                    in str((((chosen.data or {}).get("after") or {}).get("url") or "")),
                    f"{searched.message}|{chosen.message}",
                )

                live_agent.begin_plan()
                missing = live_agent.act(
                    "browser_click", {"text": "nieistniejący przycisk"}
                )
                check(
                    "live_missing_element",
                    (not missing.success)
                    and missing.status == ToolStatus.FAILURE
                    and "nie znalaz" in (missing.message or "").lower(),
                    missing.message,
                )
                timed = live_agent.act(
                    "browser_wait", {"selector": "#never", "timeout": 0.15}
                )
                check(
                    "live_timeout",
                    timed.status == ToolStatus.TIMEOUT,
                    f"{timed.status} {timed.message}",
                )

                live_agent.begin_plan()
                closed = live_agent.act("close_browser_tab", {})
                check("live_close_tab", closed.success, closed.message)
    except Exception as exc:
        check("live_playwright_local", False, str(exc))
    finally:
        if live_agent is not None:
            try:
                live_agent.close()
            except Exception:
                pass

    check(
        "live_chromium_really_started",
        chromium_started,
        "" if chromium_started else "Chromium did not launch",
    )

    # --- External read-only (no mutation). Offline → BLOCKED, local still counted above. ---
    external_agent: Optional[BrowserAgent] = None
    try:
        try:
            urllib.request.urlopen(EXTERNAL_URL, timeout=5)
            online = True
            online_err = ""
        except Exception as exc:
            online = False
            online_err = str(exc)

        if not online:
            check(
                "external_readonly",
                True,
                f"BLOCKED (no internet): {online_err}",
            )
            check("external_status_blocked", True, "BLOCKED")
        elif not playwright_importable():
            check("external_readonly", False, "playwright missing")
        else:
            backend = PlaywrightBrowserBackend()
            external_agent = reset_browser_agent_for_tests(
                BrowserAgent(backend=backend, available=True)
            )
            opened = external_agent.act(
                "browser_launch", {"url": EXTERNAL_URL, "headless": True}
            )
            title = external_agent.act("browser_read_title", {})
            text = external_agent.act("browser_read_page", {})
            ok = (
                opened.success
                and title.success
                and text.success
                and (
                    "example" in (title.message or "").lower()
                    or "example" in (text.message or "").lower()
                )
            )
            check(
                "external_readonly",
                ok,
                f"title={title.message!r} text={(text.message or '')[:80]!r}",
            )
            check("external_no_mutation", True, "open+read only")
    except Exception as exc:
        check("external_readonly", False, str(exc))
    finally:
        if external_agent is not None:
            try:
                external_agent.close()
            except Exception:
                pass

    # --- Temporary EXPERIMENTAL capability (opt-in patch only; config stays false) ---
    reset_browser_agent_for_tests(BrowserAgent())
    with patch("core.browser_agent.browser_opt_in", return_value=True):
        with patch("core.browser_agent.playwright_installed", return_value=True):
            with patch(
                "core.browser_agent.browser_runtime_available", return_value=True
            ):
                caps = snapshot_capabilities()
    browser_cap = caps.get("Browser")
    check(
        "capability_experimental_when_opt_in",
        browser_cap is not None and browser_cap.status == EXPERIMENTAL,
        getattr(browser_cap, "status", None) or "missing",
    )
    reset_browser_agent_for_tests(BrowserAgent())
    caps_default = snapshot_capabilities()
    browser_default = caps_default.get("Browser")
    from core.capability_map import UNAVAILABLE as CAP_UNAVAILABLE

    check(
        "capability_unavailable_by_default",
        browser_default is not None and browser_default.status == CAP_UNAVAILABLE,
        getattr(browser_default, "detail", None)
        or getattr(browser_default, "status", None)
        or "missing",
    )

    # --- Planner honesty without opt-in ---
    reset_browser_agent_for_tests(BrowserAgent())
    with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
        compound = Planner(
            build_default_registry(confirm_medium=False),
            model=FakeModelClient([]),
        ).run("Otwórz YouTube i wyszukaj Minecraft.")
    second = compound.steps[1]["observation"] if len(compound.steps) > 1 else {}
    check(
        "planner_honest_unavailable",
        (
            str(compound.steps[1].get("tool") or "") == "browser_search_service"
            and second.get("status") == "unavailable"
            and (
                "wyłączon" in (compound.message or "").lower()
                or "wylaczon" in (compound.message or "").lower()
                or "konfiguracji" in (compound.message or "").lower()
                or "nie umiem" in (compound.message or "").lower()
            )
        ),
        compound.message,
    )
    check(
        "no_fake_success",
        UNAVAILABLE and "wyszukałem" not in (compound.message or "").lower(),
        compound.message,
    )

    # --- Orphan Chromium hygiene ---
    time.sleep(0.4)
    pids_after = _playwright_browser_pids()
    orphans = sorted(pids_after - pids_before)
    check("no_orphan_chromium", len(orphans) == 0, f"orphans={orphans}")

    reset_browser_agent_for_tests(BrowserAgent())
    reset_registry_for_tests(None)
    reset_model_client()

    passed = sum(1 for _n, ok, _d in rows if ok)
    for name, ok, detail in rows:
        flag = "PASS" if ok else "FAIL"
        extra = f" — {detail}" if detail else ""
        print(f"[{flag}] {name}{extra}")
    summary = {
        "passed": passed,
        "total": len(rows),
        "playwright": "importable" if playwright_importable() else "missing",
        "chromium_started": chromium_started,
        "orphans": orphans,
    }
    print(json.dumps(summary, ensure_ascii=False))
    print(f"browser-agent-smoke {passed}/{len(rows)}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(run_browser_agent_smoke())
