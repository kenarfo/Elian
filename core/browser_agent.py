# ==========================
# ELIAN — BrowserAgent facade (Phase 13 Stage D)
# ==========================
"""DOM/a11y browser control. Not a second planner/chat loop.

ConversationManager.handle → Planner → ToolRegistry → BrowserAgent.
ComputerAgent stays the Windows UI fallback. Screenshot is diagnostic only.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List, Optional

from core.browser_observation import (
    AVAILABLE,
    BLOCKED,
    FAILED,
    NEEDS_LOGIN,
    SUCCESS,
    UNAVAILABLE,
    BrowserActionResult,
    BrowserObservation,
    scrub_browser_payload,
)
from core.tool_result import ToolResult, ToolStatus

MAX_STEPS = 12
MAX_WALL_SECONDS = 45.0
ACTION_TIMEOUT_SEC = 8.0

PASSWORD_HINT = re.compile(r"password|passwd|has[lł]o|token|secret|api[_-]?key", re.I)
CAPTCHA_HINT = re.compile(
    r"captcha|recaptcha|hcaptcha|nie\s+jestem\s+robotem|i'?m\s+not\s+a\s+robot",
    re.I,
)
LOGIN_HINT = re.compile(r"\b(?:log\s*in|sign\s*in|zaloguj|logowanie|signin|auth)\b", re.I)
BLOCK_HINT = re.compile(
    r"access\s+denied|attention\s+required|cf-browser-verification|bot\s+detected|"
    r"sorry,\s+you\s+have\s+been\s+blocked",
    re.I,
)
PURCHASE_HINT = re.compile(
    r"\b(?:kup(?:i[eę]|i[cć]|\s+teraz)?|zap[lł]a[cć]|p[lł]atno[sś][cć]|buy\s+now|"
    r"purchase|place\s+order|checkout|zam[oó]w)\b",
    re.I,
)
SUBMIT_HINT = re.compile(r"\b(?:wy[sś]lij|submit|potwierd[zź]\s+zakup)\b", re.I)

DISABLED_MSG = (
    "Sterowanie przeglądarką jest wyłączone w konfiguracji."
)
UNAVAILABLE_MSG = (
    "Nie mogę teraz sterować kartą przeglądarki."
)
NO_PLAYWRIGHT_MSG = (
    "Przeglądarka jest włączona w konfiguracji, "
    "ale Playwright nie jest zainstalowany."
)
SECRET_MSG = "Nie wpisuję haseł ani tokenów z pamięci."
CAPTCHA_MSG = "Strona pokazuje CAPTCHA. Nie omijam zabezpieczeń."
LOGIN_MSG = "Ta strona wymaga logowania."
BLOCKED_MSG = "Strona blokuje automatyzację."
CONFIRM_MSG = "To wygląda na nieodwracalną czynność. Potwierdź, żebym kontynuował."

_AGENT: Optional["BrowserAgent"] = None
_AGENT_LOCK = threading.Lock()


def browser_opt_in() -> bool:
    try:
        from Config import get_config_value

        section = get_config_value("integrations", "browser", default={}) or {}
        return bool(isinstance(section, dict) and section.get("enabled"))
    except Exception:
        return False


def _config_number(key: str, default: float) -> float:
    try:
        from Config import get_config_value

        section = get_config_value("integrations", "browser", default={}) or {}
        if isinstance(section, dict) and key in section:
            return float(section.get(key) or default)
    except Exception:
        pass
    return float(default)


def playwright_installed() -> bool:
    try:
        from core.browser_playwright import playwright_importable

        return bool(playwright_importable())
    except Exception:
        return False


def browser_runtime_available(agent: Optional["BrowserAgent"] = None) -> bool:
    current = agent if agent is not None else _AGENT
    if current is not None:
        return bool(current.is_available())
    return bool(browser_opt_in() and playwright_installed())


class BrowserAgent:
    """Tool provider. Planner owns PLAN→EXECUTE→VERIFY→REPAIR."""

    def __init__(self, backend=None, *, available: Optional[bool] = None) -> None:
        self.backend = backend
        self._forced_available = available
        self.step_count = 0
        self.started_at = 0.0
        self.last_observation: Optional[BrowserObservation] = None
        self.last_results: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        if self._forced_available is not None:
            return bool(self._forced_available)
        if self.backend is not None:
            ready = True
            checker = getattr(self.backend, "is_ready", None)
            if callable(checker):
                ready = bool(checker())
            return bool(browser_opt_in() and ready)
        return bool(browser_opt_in() and playwright_installed())

    def unavailable_message(self) -> str:
        if not browser_opt_in() and self._forced_available is not True:
            return DISABLED_MSG
        if not playwright_installed() and self.backend is None:
            return NO_PLAYWRIGHT_MSG
        if not self.is_available():
            return UNAVAILABLE_MSG
        return UNAVAILABLE_MSG

    def _ensure_backend(self):
        if self.backend is not None:
            return self.backend
        if not playwright_installed():
            return None
        from core.browser_playwright import PlaywrightBrowserBackend

        self.backend = PlaywrightBrowserBackend()
        return self.backend

    def begin_plan(self) -> None:
        self.step_count = 0
        self.started_at = time.time()

    def budget_exceeded(self) -> str:
        max_steps = int(_config_number("max_steps", MAX_STEPS))
        max_wall = float(_config_number("max_wall_seconds", MAX_WALL_SECONDS))
        if self.step_count >= max(1, max_steps):
            return "max_steps"
        if self.started_at and (time.time() - self.started_at) > max_wall:
            return "max_wall_seconds"
        return ""

    def public_snapshot(self) -> Dict[str, Any]:
        obs = self.last_observation
        if obs is None:
            return {}
        return scrub_browser_payload(obs.planner_fragment())

    def observe(self) -> BrowserObservation:
        backend = self._ensure_backend()
        if backend is None:
            return BrowserObservation(status=UNAVAILABLE, errors=["unavailable"])
        obs = backend.observe()
        obs.status = self._page_status(obs)
        self.last_observation = obs
        self._note_world(obs)
        return obs

    def act(self, action: str, arguments: Optional[Dict[str, Any]] = None) -> ToolResult:
        args = dict(arguments or {})
        name = str(action or args.get("semantic_action") or "").strip()
        if not self.is_available():
            return self._unavailable(name, args)
        backend = self._ensure_backend()
        if backend is None:
            return self._unavailable(name, args)
        needs_worker = bool(getattr(backend, "needs_worker", False))
        if needs_worker:
            from core.browser_worker import browser_worker_thread_id, submit_browser_job

            if threading.get_ident() != browser_worker_thread_id():
                timeout = float(_config_number("max_wall_seconds", MAX_WALL_SECONDS))
                future = submit_browser_job(self._act_locked, name, args)
                try:
                    return future.result(timeout=max(4.0, timeout))
                except Exception as exc:
                    return _tool(
                        False,
                        "Przekroczono limit czasu przeglądarki.",
                        name,
                        status=FAILED,
                        error=str(exc) or "timeout",
                        tool_status=ToolStatus.TIMEOUT,
                    )
        with self._lock:
            return self._act_locked(name, args)

    def _act_locked(self, name: str, args: Dict[str, Any]) -> ToolResult:
        if not self.started_at:
            self.begin_plan()
        budget = self.budget_exceeded()
        if budget:
            return _tool(
                False,
                "Zatrzymałem sterowanie przeglądarką — limit kroków.",
                name,
                status=FAILED,
                error=budget,
            )
        self.step_count += 1
        before = self._safe_observe()
        try:
            result = self._dispatch(name, args, before)
        except TimeoutError:
            return _tool(
                False,
                "Przekroczono limit czasu na stronie.",
                name,
                status=FAILED,
                error="timeout",
                before=before,
                tool_status=ToolStatus.TIMEOUT,
            )
        except LookupError as exc:
            err = str(exc) or "missing"
            if err == "ambiguous":
                return _tool(
                    False,
                    "Znalazłem kilka pasujących elementów — uściślij.",
                    name,
                    status=FAILED,
                    error="ambiguous",
                    before=before,
                )
            return _tool(
                False,
                "Nie znalazłem tego elementu.",
                name,
                status=FAILED,
                error=err,
                before=before,
            )
        except RuntimeError as exc:
            return self._from_runtime(name, str(exc), before)
        except Exception as exc:
            if type(exc).__name__ == "TimeoutError" or (
                "timeout" in str(exc).lower()
                and ("exceeded" in str(exc).lower() or "waiting" in str(exc).lower())
            ):
                return _tool(
                    False,
                    "Przekroczono limit czasu na stronie.",
                    name,
                    status=FAILED,
                    error="timeout",
                    before=before,
                    tool_status=ToolStatus.TIMEOUT,
                )
            return _tool(
                False,
                "Nie udało się wykonać czynności w przeglądarce.",
                name,
                status=FAILED,
                error=str(exc),
                before=before,
            )
        after = result.after or self._safe_observe()
        result.after = after
        page_status = self._page_status(after)
        if page_status == BLOCKED and name not in ("browser_screenshot", "browser_read_page", "browser_read_title"):
            return _tool(
                False,
                BLOCKED_MSG,
                name,
                status=BLOCKED,
                error="blocked",
                before=before,
                after=after,
                target=result.target,
            )
        if page_status == NEEDS_LOGIN and name in (
            "browser_search_service",
            "browser_play_media",
            "browser_select_result",
        ):
            return _tool(
                False,
                LOGIN_MSG,
                name,
                status=NEEDS_LOGIN,
                error="needs_login",
                before=before,
                after=after,
                target=result.target,
            )
        self.last_observation = after
        self._note_world(after)
        return _from_action(result)

    def _dispatch(
        self,
        name: str,
        args: Dict[str, Any],
        before: Optional[BrowserObservation],
    ) -> BrowserActionResult:
        backend = self._ensure_backend()
        if backend is None:
            raise RuntimeError("unavailable")
        if name in ("browser_launch", "launch_browser", "browser_new_session"):
            after = backend.launch(args)
            return self._ok(name, before, after, target=str(args.get("url") or ""))
        if name in ("browser_attach", "attach_browser"):
            after = backend.attach(args)
            return self._ok(name, before, after, target=str(args.get("cdp_url") or ""))
        if name in ("browser_open_url", "browser_navigate", "open_url"):
            url = str(args.get("url") or args.get("target") or args.get("query") or "").strip()
            if not url:
                raise LookupError("missing_url")
            after = backend.goto(url)
            return self._verified_nav(name, before, after, url)
        if name in ("browser_read_title", "read_title"):
            after = backend.observe()
            return BrowserActionResult(
                success=True,
                action=name,
                before=before,
                after=after,
                status=SUCCESS,
                message=after.title or "Brak tytułu.",
                verification={"title": after.title, "url": after.url},
            )
        if name in ("browser_read_page", "browser_inspect", "read_page"):
            after = backend.observe()
            summary = (after.visible_text or "")[:280] or after.title
            return BrowserActionResult(
                success=True,
                action=name,
                before=before,
                after=after,
                status=SUCCESS,
                message=summary or "Strona jest pusta.",
                verification={"url": after.url, "title": after.title},
            )
        if name in ("browser_find_element", "find_element"):
            target = self._resolve_target(backend, args)
            if target is None:
                raise LookupError("missing")
            after = backend.observe()
            label = _target_label(target, args)
            return self._ok(name, before, after, target=label, message=f"Znalazłem: {label}.")
        if name in ("browser_click", "click"):
            return self._act_click(backend, args, before)
        if name in ("browser_type", "type_in_page", "fill"):
            return self._act_type(backend, args, before)
        if name in ("browser_select", "select"):
            target = self._resolve_target(backend, args, role=str(args.get("role") or "combobox"))
            if target is None:
                raise LookupError("missing")
            value = str(args.get("value") or args.get("text") or args.get("query") or "")
            after = backend.select_option(target, value)
            return self._ok(name, before, after, target=value, message="Wybrałem opcję.")
        if name in ("browser_check", "checkbox"):
            target = self._resolve_target(backend, args, role="checkbox")
            if target is None:
                raise LookupError("missing")
            checked = args.get("checked")
            if checked is None:
                checked = True
            after = backend.set_checked(target, bool(checked))
            return self._ok(name, before, after, message="Zaznaczyłem pole.")
        if name in ("browser_scroll", "scroll"):
            direction = str(args.get("direction") or "down").lower()
            amount = int(args.get("amount") or 400)
            if direction in ("up", "góra", "gore"):
                amount = -abs(amount)
            after = backend.scroll(amount)
            return self._ok(name, before, after, message="Przewinąłem.")
        if name in ("browser_back", "back"):
            after = backend.back()
            return self._ok(name, before, after, message="Wróciłem.")
        if name in ("browser_forward", "forward"):
            after = backend.forward()
            return self._ok(name, before, after, message="Poszedłem naprzód.")
        if name in ("browser_wait", "wait_for_change"):
            timeout = float(args.get("timeout") or ACTION_TIMEOUT_SEC)
            after = backend.wait_for(
                selector=str(args.get("selector") or args.get("element_id") or ""),
                text=str(args.get("text") or args.get("query") or ""),
                timeout=timeout,
            )
            return self._ok(name, before, after, message="Doczekałem zmiany.")
        if name in ("browser_screenshot", "screenshot"):
            path = backend.screenshot()
            after = backend.observe()
            return BrowserActionResult(
                success=True,
                action=name,
                before=before,
                after=after,
                status=SUCCESS,
                message="Zrzut diagnostyczny gotowy." if path else "Nie zapisałem zrzutu.",
                verification={"diagnostic": True, "path": path},
            )
        if name in ("close_browser_tab", "browser_close_tab"):
            tab_id = str(args.get("tab_id") or args.get("name") or "")
            after = backend.close_tab(tab_id=str(args.get("tab_id") or ""))
            if after.status == UNAVAILABLE or not after.active_tab and not after.url:
                if before and (before.url or before.active_tab):
                    return self._ok(name, before, after, target=tab_id, message="Zamknąłem kartę.")
            return self._ok(name, before, after, target=tab_id, message="Zamknąłem kartę.")
        if name == "browser_search_service":
            return self._act_search(backend, args, before)
        if name == "browser_select_result":
            return self._act_select_result(backend, args, before)
        if name == "browser_play_media":
            return self._act_play(backend, args, before)
        raise RuntimeError("unmapped")

    def _act_click(self, backend, args, before) -> BrowserActionResult:
        if bool(args.get("confirmed")) is False or not args.get("confirmed"):
            query = " ".join(
                str(args.get(key) or "")
                for key in ("query", "text", "name", "label")
            )
            if PURCHASE_HINT.search(query) or SUBMIT_HINT.search(query):
                raise RuntimeError("needs_confirmation")
        target = self._resolve_target(backend, args)
        if target is None:
            raise LookupError("missing")
        label = _target_label(target, args)
        if (PURCHASE_HINT.search(label) or SUBMIT_HINT.search(label)) and not args.get("confirmed"):
            raise RuntimeError("needs_confirmation")
        if _target_is_password(target) and not args.get("authorized_secret"):
            raise RuntimeError("secret_blocked")
        after = backend.click(target)
        expected = str(args.get("url") or "")
        verification = {
            "url": after.url,
            "title": after.title,
            "url_changed": bool(before and before.url != after.url),
        }
        if expected and expected not in (after.url or ""):
            return BrowserActionResult(
                success=False,
                action="browser_click",
                target=label,
                before=before,
                after=after,
                verification=verification,
                error="verify_url",
                status=FAILED,
                message="Kliknąłem, ale adres się nie zgadza.",
            )
        return BrowserActionResult(
            success=True,
            action="browser_click",
            target=label,
            before=before,
            after=after,
            verification=verification,
            status=SUCCESS,
            message="Kliknąłem.",
        )

    def _act_type(self, backend, args, before) -> BrowserActionResult:
        text = str(args.get("text") or args.get("value") or args.get("query") or "")
        if _looks_like_stored_secret(text):
            raise RuntimeError("secret_blocked")
        target = self._resolve_target(
            backend,
            args,
            role=str(args.get("role") or "") or None,
            for_typing=True,
        )
        if target is None:
            target = self._resolve_target(
                backend, {**args, "role": "textbox"}, for_typing=True
            )
        if target is None:
            target = self._resolve_target(
                backend, {**args, "role": "searchbox"}, for_typing=True
            )
        if target is None:
            raise LookupError("missing")
        if _target_is_password(target) and not args.get("authorized_secret"):
            raise RuntimeError("secret_blocked")
        after = backend.type_text(target, text)
        observed = _read_value(target, after)
        verify_ok = True
        if observed and not _target_is_password(target):
            verify_ok = text in str(observed)
        return BrowserActionResult(
            success=bool(verify_ok),
            action="browser_type",
            target=_target_label(target, args),
            before=before,
            after=after,
            verification={"typed": text if not _target_is_password(target) else "", "observed_match": verify_ok},
            error="" if verify_ok else "verify_value",
            status=SUCCESS if verify_ok else FAILED,
            message="Wpisałem." if verify_ok else "Wpisałem, ale nie potwierdziłem treści.",
        )

    def _act_search(self, backend, args, before) -> BrowserActionResult:
        query = str(args.get("query") or args.get("q") or args.get("text") or "").strip()
        if not query:
            raise LookupError("missing_query")
        self._ensure_session(backend, args)
        box = self._resolve_target(backend, {"role": "searchbox"}) or self._resolve_target(
            backend, {"placeholder": "Szukaj"}
        ) or self._resolve_target(backend, {"placeholder": "Search"})
        if box is None:
            box = self._resolve_target(backend, {"role": "textbox"})
        if box is None:
            raise LookupError("missing")
        backend.type_text(box, query)
        after = backend.press_enter(box)
        if not [el for el in (after.actionable or []) if el.role == "link"]:
            try:
                after = backend.observe()
            except Exception:
                pass
        links = [el.to_dict() for el in (after.actionable or []) if el.role == "link"]
        self.last_results = links
        return BrowserActionResult(
            success=True,
            action="browser_search_service",
            target=query,
            before=before,
            after=after,
            verification={"result_count": len(links), "url": after.url},
            status=SUCCESS,
            message=f"Wyszukałem: {query}." if links else f"Wpisane: {query}.",
        )

    def _act_select_result(self, backend, args, before) -> BrowserActionResult:
        index = args.get("index")
        try:
            index_i = int(index) if index is not None else 0
        except Exception:
            index_i = 0
        query = str(args.get("query") or args.get("text") or "").strip()
        obs = backend.observe()
        links = [el for el in obs.actionable if el.role == "link"]
        chosen = None
        if query:
            for el in links:
                hay = f"{el.name} {el.text}".lower()
                if query.lower() in hay:
                    chosen = el
                    break
        if chosen is None and 0 <= index_i < len(links):
            chosen = links[index_i]
        if chosen is None:
            raise LookupError("missing")
        target = self._resolve_target(
            backend,
            {"element_id": chosen.element_id, "text": chosen.text or chosen.name, "role": "link"},
        )
        if target is None:
            raise LookupError("missing")
        after = backend.click(target)
        return self._ok("browser_select_result", before, after, target=chosen.name or chosen.text)

    def _act_play(self, backend, args, before) -> BrowserActionResult:
        query = str(args.get("query") or args.get("name") or "").strip()
        self._ensure_session(backend, args)
        target = None
        if query:
            target = self._resolve_target(backend, {"role": "link", "text": query})
        if target is None:
            target = self._resolve_target(backend, {"role": "button", "name": "Play"}) or self._resolve_target(
                backend, {"role": "button", "text": "Odtwórz"}
            )
        if target is None:
            raise LookupError("missing")
        after = backend.click(target)
        return self._ok("browser_play_media", before, after, target=query, message="Odtwarzam.")

    def _ensure_session(self, backend, args: Dict[str, Any]) -> None:
        obs = backend.observe()
        if obs.url:
            return
        url = str(args.get("url") or "").strip()
        if not url:
            name = str(args.get("name") or "").strip()
            if name:
                try:
                    from core.official_services import official_url_for

                    url = official_url_for(name) or ""
                except Exception:
                    url = ""
        if url:
            backend.launch({"url": url})
        else:
            backend.launch(args)

    def _resolve_target(
        self,
        backend,
        args: Dict[str, Any],
        role: Optional[str] = None,
        *,
        for_typing: bool = False,
    ):
        hints = {
            "element_id": str(args.get("element_id") or args.get("id") or ""),
            "role": str(role or args.get("role") or ""),
            "name": str(args.get("name") or args.get("label") or ""),
            "label": str(args.get("label") or args.get("name") or ""),
            "text": "" if for_typing else str(args.get("text") or args.get("query") or ""),
            "placeholder": str(args.get("placeholder") or ""),
            "selector": str(args.get("selector") or ""),
        }
        if not any(hints.values()):
            return None
        return backend.find(**hints)

    def _safe_observe(self) -> Optional[BrowserObservation]:
        try:
            backend = self._ensure_backend()
            if backend is None:
                return None
            return backend.observe()
        except Exception:
            return self.last_observation

    def _page_status(self, obs: Optional[BrowserObservation]) -> str:
        if obs is None:
            return UNAVAILABLE
        blob = " ".join(
            [
                obs.url or "",
                obs.title or "",
                (obs.visible_text or "")[:2000],
            ]
        )
        if BLOCK_HINT.search(blob) or "elian-browser-status" in blob.lower() and "blocked" in blob.lower():
            return BLOCKED
        meta_blocked = any(
            "blocked" in str(err).lower() for err in (obs.errors or [])
        )
        if meta_blocked:
            return BLOCKED
        if CAPTCHA_HINT.search(blob):
            return BLOCKED
        password_fields = [el for el in obs.actionable if el.is_password]
        if password_fields and LOGIN_HINT.search(blob):
            return NEEDS_LOGIN
        if obs.status in (BLOCKED, NEEDS_LOGIN, UNAVAILABLE, FAILED):
            return obs.status
        return AVAILABLE if obs.url or obs.title else obs.status or AVAILABLE

    def _verified_nav(
        self,
        name: str,
        before: Optional[BrowserObservation],
        after: BrowserObservation,
        expected_url: str,
    ) -> BrowserActionResult:
        ok = bool(after.url) and (
            expected_url in after.url
            or after.url.endswith(expected_url)
            or expected_url.split("/")[-1] in after.url
        )
        page_status = self._page_status(after)
        if page_status == BLOCKED:
            return BrowserActionResult(
                success=False,
                action=name,
                target=expected_url,
                before=before,
                after=after,
                verification={"url": after.url, "expected": expected_url},
                error="blocked",
                status=BLOCKED,
                message=BLOCKED_MSG,
            )
        spoken = (after.title or "Otworzyłem stronę.") if ok else "Nie potwierdziłem adresu strony."
        return BrowserActionResult(
            success=ok,
            action=name,
            target=expected_url,
            before=before,
            after=after,
            verification={"url": after.url, "title": after.title, "ok": ok},
            error="" if ok else "verify_url",
            status=SUCCESS if ok else FAILED,
            message=spoken,
        )

    def _ok(
        self,
        name: str,
        before: Optional[BrowserObservation],
        after: BrowserObservation,
        *,
        target: str = "",
        message: str = "",
    ) -> BrowserActionResult:
        return BrowserActionResult(
            success=True,
            action=name,
            target=target,
            before=before,
            after=after,
            verification={"url": after.url, "title": after.title},
            status=SUCCESS,
            message=message or after.title or "Gotowe.",
        )

    def _unavailable(self, name: str, args: Dict[str, Any]) -> ToolResult:
        payload = {
            "capability": name,
            "available": False,
            "deferred_stage": "D",
            "opt_in": browser_opt_in(),
            "playwright": playwright_installed(),
            "browser_status": UNAVAILABLE,
        }
        if args:
            payload["query"] = args.get("query") or args.get("q") or ""
            if args.get("index") is not None:
                payload["index"] = args.get("index")
            payload["tab_id"] = args.get("tab_id") or ""
            payload["service"] = args.get("name") or args.get("service") or ""
        return ToolResult.unavailable(
            self.unavailable_message(),
            tool_name=name or "browser",
            data=payload,
        )

    def _from_runtime(self, name: str, error: str, before: Optional[BrowserObservation]) -> ToolResult:
        if error.startswith("playwright_missing") or error == "unavailable":
            return self._unavailable(name, {})
        if error.startswith("browser_missing"):
            return ToolResult.unavailable(
                "Playwright jest, ale nie mam zainstalowanej przeglądarki Chromium.",
                tool_name=name,
                data={"browser_status": UNAVAILABLE, "error": error},
            )
        if error == "attach_not_supported":
            return ToolResult.unavailable(
                "Podłączanie do istniejącej przeglądarki będzie później.",
                tool_name=name,
                data={"browser_status": UNAVAILABLE, "error": error},
            )
        if error == "needs_confirmation":
            return ToolResult.needs_confirmation(
                CONFIRM_MSG,
                tool_name=name,
                data={"browser_status": FAILED, "error": "needs_confirmation"},
            )
        if error == "secret_blocked":
            return ToolResult.fail(
                SECRET_MSG,
                tool_name=name,
                error="secret_blocked",
                data={"browser_status": FAILED},
            )
        if error == "unmapped":
            return ToolResult.unsupported(
                "Tej czynności przeglądarki jeszcze nie wykonuję.",
                tool_name=name,
                data={"browser_status": FAILED},
            )
        if error == "tab_closed":
            return _tool(
                False,
                "Karta przeglądarki jest już zamknięta.",
                name,
                status=FAILED,
                error=error,
                before=before,
            )
        if error in ("page_crash", "browser_crash"):
            return _tool(
                False,
                "Przeglądarka uległa awarii.",
                name,
                status=FAILED,
                error=error,
                before=before,
            )
        if error.startswith("navigation_failed"):
            return _tool(
                False,
                "Nie udało się otworzyć strony.",
                name,
                status=FAILED,
                error=error,
                before=before,
            )
        return _tool(False, "Nie udało się wykonać czynności w przeglądarce.", name, status=FAILED, error=error, before=before)

    def _note_world(self, obs: BrowserObservation) -> None:
        try:
            from core.world_state import note_browser

            note_browser(obs.planner_fragment())
        except Exception:
            pass

    def close(self) -> None:
        backend = self.backend
        if backend is None:
            return
        closer = getattr(backend, "close_session", None)
        if not callable(closer):
            return

        def _close() -> None:
            try:
                closer()
            except Exception:
                pass

        needs_worker = bool(getattr(backend, "needs_worker", False))
        if needs_worker:
            try:
                from core.browser_worker import browser_worker_thread_id, submit_browser_job

                if threading.get_ident() != browser_worker_thread_id():
                    submit_browser_job(_close).result(timeout=15.0)
                    return
            except Exception:
                pass
        _close()


def _target_label(target: Any, args: Dict[str, Any]) -> str:
    for key in ("text", "name", "label", "query", "element_id"):
        value = str(args.get(key) or "").strip()
        if value:
            return value
    name = getattr(target, "element_id", "") or ""
    if hasattr(target, "get_attribute"):
        try:
            name = target.get_attribute("aria-label") or name
        except Exception:
            pass
    return str(name or "element")


def _target_is_password(target: Any) -> bool:
    if bool(getattr(target, "is_password", False)):
        return True
    attrs = getattr(target, "attrs", None)
    if isinstance(attrs, dict):
        blob = " ".join(str(v) for v in attrs.values())
        if (str(attrs.get("type") or "").lower() == "password") or PASSWORD_HINT.search(blob):
            return True
    getter = getattr(target, "get_attribute", None)
    if callable(getter):
        try:
            kind = str(getter("type") or "")
            label = str(getter("aria-label") or getter("name") or "")
            if kind.lower() == "password" or PASSWORD_HINT.search(label):
                return True
        except Exception:
            return False
    return False


def _read_value(target: Any, after: BrowserObservation) -> str:
    element_id = str(getattr(target, "element_id", "") or "")
    if element_id:
        for el in after.actionable:
            if el.element_id == element_id:
                return el.value
    getter = getattr(target, "input_value", None)
    if callable(getter):
        try:
            return str(getter() or "")
        except Exception:
            pass
    evaluate = getattr(target, "evaluate", None)
    if callable(evaluate):
        try:
            return str(evaluate("el => el && el.value != null ? String(el.value) : ''") or "")
        except Exception:
            pass
    return ""


def _looks_like_stored_secret(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    try:
        from Config import load_secrets

        secrets = load_secrets() or {}
        if not isinstance(secrets, dict):
            return False
        for item in secrets.values():
            if str(item) and str(item) == value:
                return True
    except Exception:
        return False
    return False


def _tool(
    ok: bool,
    message: str,
    tool: str,
    *,
    status: str,
    error: str = "",
    before: Optional[BrowserObservation] = None,
    after: Optional[BrowserObservation] = None,
    target: str = "",
    tool_status: Optional[ToolStatus] = None,
) -> ToolResult:
    payload = {
        "browser_status": status,
        "action": tool,
        "target": target,
        "before": before.planner_fragment() if before else {},
        "after": after.planner_fragment() if after else {},
        "verification": {},
    }
    if ok:
        return ToolResult.ok(message, data=payload, tool_name=tool)
    if tool_status is ToolStatus.TIMEOUT:
        return ToolResult.timed_out(message, tool_name=tool, data=payload)
    if status == UNAVAILABLE:
        return ToolResult.unavailable(message, tool_name=tool, data=payload)
    result = ToolResult.fail(message, error=error or status.lower(), data=payload, tool_name=tool)
    return result


def _from_action(result: BrowserActionResult) -> ToolResult:
    payload = result.to_dict()
    payload["browser_status"] = result.status
    if result.status == SUCCESS and result.success:
        return ToolResult.ok(result.message or "Gotowe.", data=payload, tool_name=result.action)
    if result.status == UNAVAILABLE:
        return ToolResult.unavailable(result.message or UNAVAILABLE_MSG, tool_name=result.action, data=payload)
    if result.status == BLOCKED:
        return ToolResult.fail(
            result.message or BLOCKED_MSG,
            error="blocked",
            data=payload,
            tool_name=result.action,
        )
    if result.status == NEEDS_LOGIN:
        return ToolResult.fail(
            result.message or LOGIN_MSG,
            error="needs_login",
            data=payload,
            tool_name=result.action,
        )
    return ToolResult.fail(
        result.message or "Operacja nie powiodła się.",
        error=result.error or "failed",
        data=payload,
        tool_name=result.action,
    )


def get_browser_agent() -> BrowserAgent:
    global _AGENT
    with _AGENT_LOCK:
        if _AGENT is None:
            _AGENT = BrowserAgent()
        return _AGENT


def set_browser_agent(agent: Optional[BrowserAgent]) -> None:
    global _AGENT
    with _AGENT_LOCK:
        _AGENT = agent


def reset_browser_agent_for_tests(agent: Optional[BrowserAgent] = None) -> BrowserAgent:
    with _AGENT_LOCK:
        previous = _AGENT
    if previous is not None:
        try:
            previous.close()
        except Exception:
            pass
    created = agent if agent is not None else BrowserAgent()
    set_browser_agent(created)
    return created


def get_browser_session_snapshot() -> Dict[str, Any]:
    """WorldState-safe url/title/tab fragment. No pixels, no secrets."""
    try:
        return get_browser_agent().public_snapshot()
    except Exception:
        return {}
