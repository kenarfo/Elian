# ==========================
# ELIAN — Playwright backend (Phase 13 Stage D)
# ==========================
"""MANAGED Chromium. ATTACHED CDP is optional. Prefer get_by_role over CSS."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.browser_observation import ActionableElement, BrowserObservation


def playwright_importable() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401

        return True
    except Exception:
        return False


class PlaywrightBrowserBackend:
    needs_worker = True

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._context = None
        self._pages: List[Any] = []
        self._active = 0
        self.last_error = ""

    def is_ready(self) -> bool:
        return playwright_importable()

    def _page(self):
        if not self._pages:
            return None
        if self._active < 0 or self._active >= len(self._pages):
            self._active = 0
        return self._pages[self._active]

    def _sync_pages(self) -> None:
        if self._context is None:
            return
        pages = list(self._context.pages)
        self._pages = pages
        current = self._page()
        if current is not None and current in pages:
            self._active = pages.index(current)
        elif pages:
            self._active = len(pages) - 1

    def launch(self, arguments: Dict[str, Any] | None = None) -> BrowserObservation:
        args = dict(arguments or {})
        if not playwright_importable():
            raise RuntimeError("playwright_missing")
        from playwright.sync_api import sync_playwright

        headless = args.get("headless")
        if headless is None:
            headless = True
        if self._pw is None:
            self._pw = sync_playwright().start()
        if self._browser is None:
            try:
                self._browser = self._pw.chromium.launch(headless=bool(headless))
            except Exception as exc:
                raise RuntimeError(f"browser_missing:{exc}") from exc
        if self._context is None:
            self._context = self._browser.new_context(ignore_https_errors=True)
        page = self._context.new_page()
        self._pages = list(self._context.pages)
        self._active = self._pages.index(page)
        url = str(args.get("url") or "").strip()
        if url:
            return self.goto(url)
        return self.observe()

    def attach(self, arguments: Dict[str, Any] | None = None) -> BrowserObservation:
        args = dict(arguments or {})
        cdp = str(args.get("cdp_url") or args.get("ws_url") or "").strip()
        if not cdp:
            raise RuntimeError("attach_not_supported")
        if not playwright_importable():
            raise RuntimeError("playwright_missing")
        from playwright.sync_api import sync_playwright

        if self._pw is None:
            self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(cdp)
        contexts = list(self._browser.contexts)
        self._context = contexts[0] if contexts else self._browser.new_context()
        self._sync_pages()
        if not self._pages:
            page = self._context.new_page()
            self._pages = [page]
            self._active = 0
        return self.observe()

    def close_session(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None
        self._browser = None
        self._context = None
        self._pages = []
        self._active = 0

    def close_tab(self, tab_id: str = "") -> BrowserObservation:
        self._sync_pages()
        page = None
        if tab_id:
            for item in self._pages:
                if _tab_id(item) == tab_id:
                    page = item
                    break
        if page is None:
            page = self._page()
        if page is None:
            raise LookupError("no_tab")
        page.close()
        self._sync_pages()
        return self.observe()

    def goto(self, url: str) -> BrowserObservation:
        page = self._page()
        if page is None:
            self.launch({"url": url})
            return self.observe()
        try:
            page.goto(str(url), wait_until="domcontentloaded")
        except Exception as exc:
            _reraise_playwright(exc)
        self._sync_pages()
        return self.observe()

    def back(self) -> BrowserObservation:
        page = self._page()
        if page is None:
            raise RuntimeError("no_session")
        try:
            page.go_back(wait_until="domcontentloaded")
        except Exception as exc:
            _reraise_playwright(exc)
        return self.observe()

    def forward(self) -> BrowserObservation:
        page = self._page()
        if page is None:
            raise RuntimeError("no_session")
        try:
            page.go_forward(wait_until="domcontentloaded")
        except Exception as exc:
            _reraise_playwright(exc)
        return self.observe()

    def observe(self) -> BrowserObservation:
        self._sync_pages()
        page = self._page()
        if page is None:
            return BrowserObservation(status="UNAVAILABLE", errors=["no_session"])
        title = ""
        url = ""
        try:
            title = str(page.title() or "")
            url = str(page.url or "")
        except Exception as exc:
            if _is_target_closed(exc):
                return BrowserObservation(errors=["tab_closed"], status="FAILED")
            return BrowserObservation(errors=[str(exc)], status="FAILED")
        visible = ""
        try:
            visible = str(page.inner_text("body") or "")
        except Exception:
            visible = ""
        tabs = []
        for item in self._pages:
            try:
                tabs.append(
                    {
                        "tab_id": _tab_id(item),
                        "url": str(item.url or ""),
                        "title": str(item.title() or ""),
                    }
                )
            except Exception:
                continue
        actionable = _actionable_from_page(page)
        return BrowserObservation(
            url=url,
            title=title,
            visible_text=visible,
            actionable=actionable,
            active_tab=_tab_id(page),
            tabs=tabs,
            loading=False,
            status="AVAILABLE",
        )

    def find(self, **hints):
        page = self._page()
        if page is None:
            return None
        locator = _locator_for(page, **hints)
        if locator is None:
            return None
        try:
            count = int(locator.count())
            if count <= 0:
                return None
            element_id = str(hints.get("element_id") or "").strip()
            if count > 1 and not element_id:
                raise LookupError("ambiguous")
            return locator.first
        except LookupError:
            raise
        except Exception as exc:
            _reraise_playwright(exc)
            return None

    def click(self, target) -> BrowserObservation:
        page = self._page()
        if page is None:
            raise RuntimeError("no_session")
        before_count = len(self._context.pages) if self._context is not None else len(self._pages)
        opens_blank = _target_opens_blank(target)
        try:
            if opens_blank:
                with page.expect_popup(timeout=4000) as popup_info:
                    target.click(timeout=4000)
                popup = popup_info.value
                try:
                    popup.wait_for_load_state("domcontentloaded", timeout=4000)
                except Exception as exc:
                    _reraise_playwright(exc, soft=True)
            else:
                target.click(timeout=4000)
                # Optional new tab (JS window.open without target=_blank attr).
                deadline = time.time() + 0.8
                while time.time() < deadline:
                    self._sync_pages()
                    if self._context is not None and len(self._context.pages) > before_count:
                        break
                    time.sleep(0.05)
        except Exception as exc:
            _reraise_playwright(exc)
        self._sync_pages()
        if self._context is not None and len(self._context.pages) > before_count:
            self._active = len(self._pages) - 1
        return self.observe()

    def type_text(self, target, text: str) -> BrowserObservation:
        try:
            target.fill(str(text or ""))
        except Exception as exc:
            _reraise_playwright(exc)
        return self.observe()

    def select_option(self, target, value: str) -> BrowserObservation:
        try:
            try:
                target.select_option(label=str(value or ""))
            except Exception:
                target.select_option(value=str(value or ""))
        except Exception as exc:
            _reraise_playwright(exc)
        return self.observe()

    def set_checked(self, target, checked: bool) -> BrowserObservation:
        try:
            if checked:
                target.check()
            else:
                target.uncheck()
        except Exception as exc:
            _reraise_playwright(exc)
        return self.observe()

    def scroll(self, amount: int) -> BrowserObservation:
        page = self._page()
        if page is None:
            raise RuntimeError("no_session")
        try:
            page.mouse.wheel(0, int(amount or 400))
        except Exception as exc:
            _reraise_playwright(exc)
        return self.observe()

    def press_enter(self, target=None) -> BrowserObservation:
        page = self._page()
        if page is None:
            raise RuntimeError("no_session")
        try:
            if target is not None:
                target.press("Enter")
            else:
                page.keyboard.press("Enter")
        except Exception as exc:
            _reraise_playwright(exc)
        time.sleep(0.05)
        self._sync_pages()
        return self.observe()

    def wait_for(self, *, selector: str = "", text: str = "", timeout: float = 2.0) -> BrowserObservation:
        page = self._page()
        if page is None:
            raise RuntimeError("no_session")
        ms = int(max(0.05, float(timeout or 0)) * 1000)
        try:
            if selector:
                page.wait_for_selector(selector, timeout=ms)
            elif text:
                page.get_by_text(str(text), exact=False).first.wait_for(timeout=ms)
            else:
                page.wait_for_load_state("domcontentloaded", timeout=ms)
        except Exception as exc:
            _reraise_playwright(exc)
        return self.observe()

    def screenshot(self) -> str:
        page = self._page()
        if page is None:
            return ""
        folder = Path(tempfile.gettempdir()) / "elian-browser"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"shot-{int(time.time() * 1000)}.png"
        try:
            page.screenshot(path=str(path), full_page=False)
        except Exception as exc:
            _reraise_playwright(exc)
            return ""
        return str(path)


def _is_playwright_timeout(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    if name == "timeouterror":
        return True
    msg = str(exc or "").lower()
    return "timeout" in msg and ("exceeded" in msg or "waiting" in msg)


def _is_target_closed(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc or "").lower()
    return "targetclosed" in name or "target closed" in msg or "has been closed" in msg


def _reraise_playwright(exc: BaseException, *, soft: bool = False) -> None:
    if soft and (_is_playwright_timeout(exc) or _is_target_closed(exc)):
        return
    if _is_playwright_timeout(exc):
        raise TimeoutError(str(exc) or "timeout") from exc
    if _is_target_closed(exc):
        raise RuntimeError("tab_closed") from exc
    msg = str(exc or "").lower()
    if "crash" in msg or "page crashed" in msg:
        raise RuntimeError("page_crash") from exc
    if "browser has been closed" in msg or "browser closed" in msg:
        raise RuntimeError("browser_crash") from exc
    if "net::" in msg or "navigation" in msg or "goto:" in msg:
        raise RuntimeError(f"navigation_failed:{exc}") from exc
    raise exc


def _target_opens_blank(target) -> bool:
    try:
        return bool(
            target.evaluate(
                """el => {
                  const node = el && el.closest ? (el.closest('a,area,button,[role=link]') || el) : el;
                  if (!node || !node.getAttribute) return false;
                  const t = String(node.getAttribute('target') || '').toLowerCase();
                  return t === '_blank' || node.hasAttribute('data-popup');
                }"""
            )
        )
    except Exception:
        return False


def _tab_id(page) -> str:
    try:
        guid = str(getattr(page, "_guid", "") or "")
        if guid:
            return guid[-10:]
    except Exception:
        pass
    try:
        return str(id(page))
    except Exception:
        return "tab"


def _locator_for(page, **hints):
    element_id = str(hints.get("element_id") or "").strip()
    role = str(hints.get("role") or "").strip()
    name = str(hints.get("name") or hints.get("label") or "").strip()
    text = str(hints.get("text") or "").strip()
    placeholder = str(hints.get("placeholder") or "").strip()
    selector = str(hints.get("selector") or "").strip()
    if element_id:
        loc = page.locator(f"[data-elian-id='{element_id}'], #{element_id}")
        if loc.count() > 0:
            return loc
    if role:
        kwargs = {}
        if name:
            kwargs["name"] = name
        elif text:
            kwargs["name"] = text
        if kwargs:
            loc = page.get_by_role(role, **kwargs)
            if loc.count() > 0:
                return loc
    if name:
        loc = page.get_by_label(name, exact=False)
        if loc.count() > 0:
            return loc
        loc = page.get_by_role("textbox", name=name)
        if loc.count() > 0:
            return loc
        loc = page.get_by_role("button", name=name)
        if loc.count() > 0:
            return loc
        loc = page.get_by_role("link", name=name)
        if loc.count() > 0:
            return loc
        loc = page.get_by_role("checkbox", name=name)
        if loc.count() > 0:
            return loc
    if placeholder:
        loc = page.get_by_placeholder(placeholder)
        if loc.count() > 0:
            return loc
    if text:
        loc = page.get_by_text(text, exact=False)
        if loc.count() > 0:
            return loc
    if selector:
        loc = page.locator(selector)
        if loc.count() > 0:
            return loc
    return None


def _actionable_from_page(page) -> List[ActionableElement]:
    rows: List[ActionableElement] = []
    script = """
    () => {
      const nodes = Array.from(document.querySelectorAll(
        'a, button, input, select, textarea, [role="button"], [role="link"], [role="textbox"], [role="searchbox"], [role="checkbox"]'
      )).slice(0, 40);
      return nodes.map((el, i) => {
        const tag = el.tagName.toLowerCase();
        const type = (el.getAttribute('type') || '').toLowerCase();
        let role = el.getAttribute('role') || tag;
        if (tag === 'a') role = 'link';
        else if (tag === 'button') role = 'button';
        else if (tag === 'select') role = 'combobox';
        else if (tag === 'textarea') role = 'textbox';
        else if (tag === 'input') {
          if (type === 'checkbox') role = 'checkbox';
          else if (type === 'radio') role = 'radio';
          else if (type === 'search') role = 'searchbox';
          else role = 'textbox';
        }
        const name = el.getAttribute('aria-label') || el.getAttribute('name') || (el.innerText || '').trim().slice(0, 80);
        return {
          element_id: el.id || ('pw' + i),
          role: type === 'password' ? 'textbox' : role,
          name: name,
          text: (el.innerText || '').trim().slice(0, 80),
          placeholder: el.getAttribute('placeholder') || '',
          value: type === 'password' ? '' : (el.value || ''),
          checked: type === 'checkbox' ? !!el.checked : null,
          href: el.getAttribute('href') || '',
          is_password: type === 'password',
          disabled: !!el.disabled
        };
      });
    }
    """
    try:
        payload = page.evaluate(script) or []
    except Exception:
        payload = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        rows.append(
            ActionableElement(
                element_id=str(item.get("element_id") or ""),
                role=str(item.get("role") or ""),
                name=str(item.get("name") or ""),
                text="" if item.get("is_password") else str(item.get("text") or ""),
                placeholder=str(item.get("placeholder") or ""),
                value="" if item.get("is_password") else str(item.get("value") or ""),
                checked=item.get("checked"),
                href=str(item.get("href") or ""),
                is_password=bool(item.get("is_password")),
                disabled=bool(item.get("disabled")),
            )
        )
    return rows
