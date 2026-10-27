# ==========================
# ELIAN — in-memory HTML backend for BrowserAgent tests (Phase 13 Stage D)
# ==========================
"""Local fixtures only. Not a second planner. Prefer role/label/text over coordinates."""

from __future__ import annotations

import re
import time
import uuid
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from core.browser_observation import ActionableElement, BrowserObservation

VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

ROLE_MAP = {
    "a": "link",
    "button": "button",
    "select": "combobox",
    "textarea": "textbox",
    "h1": "heading",
    "h2": "heading",
    "h3": "heading",
    "option": "option",
}


class Node:
    __slots__ = ("tag", "attrs", "children", "text", "parent", "element_id")

    def __init__(self, tag: str, attrs: Dict[str, str] | None = None):
        self.tag = str(tag or "").lower()
        self.attrs = {str(k).lower(): str(v) for k, v in dict(attrs or {}).items()}
        self.children: List[Node] = []
        self.text = ""
        self.parent: Optional[Node] = None
        self.element_id = ""

    def descendant_text(self) -> str:
        parts = [self.text]
        for child in self.children:
            parts.append(child.descendant_text())
        return re.sub(r"\s+", " ", " ".join(parts)).strip()

    def attr(self, name: str) -> str:
        return str(self.attrs.get(str(name).lower()) or "")


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        parent = self.stack[-1]
        node.parent = parent
        parent.children.append(node)
        if str(tag).lower() not in VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        needle = str(tag).lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == needle:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.stack:
            self.stack[-1].text += str(data or "")


def parse_html(html: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(str(html or ""))
    builder.close()
    counter = [0]

    def assign(node: Node) -> None:
        counter[0] += 1
        node.element_id = f"e{counter[0]}"
        for child in node.children:
            assign(child)

    assign(builder.root)
    return builder.root


def walk(node: Node):
    yield node
    for child in node.children:
        yield from walk(child)


def implicit_role(node: Node) -> str:
    explicit = node.attr("role").lower()
    if explicit:
        return explicit
    if node.tag in ROLE_MAP:
        return ROLE_MAP[node.tag]
    if node.tag == "input":
        kind = (node.attr("type") or "text").lower()
        if kind == "checkbox":
            return "checkbox"
        if kind == "radio":
            return "radio"
        if kind in ("submit", "button", "reset"):
            return "button"
        if kind == "search":
            return "searchbox"
        if kind == "password":
            return "textbox"
        return "textbox"
    return node.tag


def is_password_node(node: Node) -> bool:
    if (node.attr("type") or "").lower() == "password":
        return True
    role = implicit_role(node)
    name = f"{node.attr('name')} {node.attr('aria-label')} {node.attr('id')}"
    return role == "textbox" and bool(re.search(r"password|has[lł]o", name, re.I))


def accessible_name(node: Node, root: Node | None = None) -> str:
    aria = node.attr("aria-label")
    if aria:
        return aria.strip()
    if root is not None and node.attr("id"):
        for other in walk(root):
            if other.tag == "label" and other.attr("for") == node.attr("id"):
                text = other.descendant_text()
                if text:
                    return text
    parent = node.parent
    while parent is not None:
        if parent.tag == "label":
            text = parent.descendant_text()
            if text:
                return text
            break
        parent = parent.parent
    for key in ("title", "alt"):
        value = node.attr(key)
        if value:
            return value.strip()
    text = node.descendant_text()
    if text:
        return text
    return node.attr("placeholder")


class FakeTab:
    def __init__(self, url: str = "about:blank", html: str = "", title: str = "") -> None:
        self.tab_id = uuid.uuid4().hex[:10]
        self.url = url
        self.html = html or "<html><head><title>blank</title></head><body></body></html>"
        self.root = parse_html(self.html)
        self.title = title or _title_from_tree(self.root) or "blank"
        self.history = [self.url]
        self.history_index = 0
        self.scroll_y = 0
        self.loading = False
        self.values: Dict[str, str] = {}
        self.checked: Dict[str, bool] = {}
        self.selected: Dict[str, str] = {}
        self.popup_opened = False
        self.submitted = False
        self.seed_values()

    def seed_values(self) -> None:
        for node in walk(self.root):
            if node.tag in ("input", "textarea"):
                current = node.attr("value")
                if current:
                    self.values[node.element_id] = current
            if node.tag == "input" and (node.attr("type") or "").lower() == "checkbox":
                self.checked[node.element_id] = "checked" in node.attrs
            if node.tag == "select":
                chosen = ""
                for child in node.children:
                    if child.tag == "option" and "selected" in child.attrs:
                        chosen = child.descendant_text() or child.attr("value")
                        break
                if not chosen:
                    for child in node.children:
                        if child.tag == "option":
                            chosen = child.descendant_text() or child.attr("value")
                            break
                if chosen:
                    self.selected[node.element_id] = chosen

    def load(self, url: str, html: str) -> None:
        self.url = url
        self.html = html
        self.root = parse_html(html)
        self.title = _title_from_tree(self.root) or url
        self.loading = False
        self.values = {}
        self.checked = {}
        self.selected = {}
        self.submitted = False
        self.seed_values()


def _title_from_tree(root: Node) -> str:
    for node in walk(root):
        if node.tag == "title":
            return node.descendant_text()
    return ""


def _file_from_url(url: str) -> Optional[Path]:
    raw = str(url or "")
    if raw.startswith("file:"):
        path = raw.replace("file:///", "").replace("file://", "")
        path = path.replace("/", "\\") if "\\" in path or ":\\" in path or re.match(r"^[a-zA-Z]:", path) else path
        candidate = Path(path)
        if candidate.exists():
            return candidate
        # POSIX-style file URI on Windows
        candidate = Path(raw.replace("file:///", "").replace("file://", ""))
        if candidate.exists():
            return candidate
    return None


def load_url(url: str, *, fixture_root: Path | None = None) -> Tuple[str, str]:
    raw = str(url or "").strip() or "about:blank"
    if raw == "about:blank":
        return raw, "<html><head><title>blank</title></head><body></body></html>"
    path = _file_from_url(raw)
    if path is None and fixture_root is not None and not re.match(r"^[a-zA-Z]+://", raw):
        path = (fixture_root / raw).resolve()
    if path is not None and path.exists() and path.is_file():
        return path.resolve().as_uri(), path.read_text(encoding="utf-8")
    if raw.startswith("data:text/html"):
        html = raw.split(",", 1)[-1]
        return raw, html
    return raw, (
        "<html><head><title>Missing</title></head>"
        "<body><h1>Missing</h1><p>Nie znaleziono strony.</p></body></html>"
    )


class FakeBrowserBackend:
    needs_worker = False

    def __init__(self, *, fixture_root: Path | None = None) -> None:
        self.fixture_root = fixture_root
        self.tabs: List[FakeTab] = []
        self.active_index = 0
        self.launched = False
        self.closed = False
        self.last_error = ""
        self.wait_fail_once = ""

    def is_ready(self) -> bool:
        return True

    def current(self) -> Optional[FakeTab]:
        if not self.tabs:
            return None
        if self.active_index < 0 or self.active_index >= len(self.tabs):
            self.active_index = 0
        return self.tabs[self.active_index]

    def launch(self, arguments: Dict[str, Any] | None = None) -> BrowserObservation:
        args = dict(arguments or {})
        self.closed = False
        self.launched = True
        if not self.tabs:
            url = str(args.get("url") or "about:blank")
            resolved, html = load_url(url, fixture_root=self.fixture_root)
            tab = FakeTab(url=resolved, html=html)
            self.tabs = [tab]
            self.active_index = 0
        return self.observe()

    def attach(self, arguments: Dict[str, Any] | None = None) -> BrowserObservation:
        raise RuntimeError("attach_not_supported")

    def close_session(self) -> None:
        self.tabs = []
        self.launched = False
        self.closed = True

    def close_tab(self, tab_id: str = "") -> BrowserObservation:
        if not self.tabs:
            raise LookupError("no_tab")
        if tab_id:
            self.tabs = [tab for tab in self.tabs if tab.tab_id != tab_id]
        elif self.tabs:
            del self.tabs[self.active_index]
        if not self.tabs:
            self.active_index = 0
            return self.observe()
        self.active_index = min(self.active_index, len(self.tabs) - 1)
        return self.observe()

    def goto(self, url: str) -> BrowserObservation:
        if not self.launched:
            self.launch({"url": url})
        tab = self.current()
        if tab is None:
            self.launch({"url": url})
            return self.observe()
        tab.loading = True
        resolved, html = load_url(url, fixture_root=self.fixture_root)
        tab.load(resolved, html)
        tab.history = tab.history[: tab.history_index + 1]
        tab.history.append(resolved)
        tab.history_index = len(tab.history) - 1
        return self.observe()

    def back(self) -> BrowserObservation:
        tab = self.current()
        if tab is None or tab.history_index <= 0:
            raise LookupError("no_history")
        tab.history_index -= 1
        resolved, html = load_url(tab.history[tab.history_index], fixture_root=self.fixture_root)
        tab.load(resolved, html)
        return self.observe()

    def forward(self) -> BrowserObservation:
        tab = self.current()
        if tab is None or tab.history_index >= len(tab.history) - 1:
            raise LookupError("no_history")
        tab.history_index += 1
        resolved, html = load_url(tab.history[tab.history_index], fixture_root=self.fixture_root)
        tab.load(resolved, html)
        return self.observe()

    def observe(self) -> BrowserObservation:
        tab = self.current()
        if tab is None:
            return BrowserObservation(status="UNAVAILABLE", errors=["no_session"])
        actionable: List[ActionableElement] = []
        labels_by_for = {}
        for node in walk(tab.root):
            if node.tag == "label" and node.attr("for"):
                labels_by_for[node.attr("for")] = node.descendant_text()
        texts: List[str] = []
        for node in walk(tab.root):
            if node.tag in ("script", "style"):
                continue
            role = implicit_role(node)
            if node.tag in ("p", "h1", "h2", "h3", "li", "button", "a", "label"):
                chunk = node.descendant_text()
                if chunk:
                    texts.append(chunk)
            if role in (
                "link",
                "button",
                "textbox",
                "searchbox",
                "checkbox",
                "combobox",
                "radio",
            ) or node.tag in ("input", "select", "textarea", "a", "button"):
                name = labels_by_for.get(node.attr("id")) or accessible_name(node, tab.root)
                password = is_password_node(node)
                value = "" if password else tab.values.get(node.element_id, node.attr("value"))
                if node.tag == "select":
                    value = tab.selected.get(node.element_id, value)
                actionable.append(
                    ActionableElement(
                        element_id=node.element_id,
                        role=role,
                        name=name,
                        text="" if password else node.descendant_text(),
                        placeholder=node.attr("placeholder"),
                        value=value,
                        checked=tab.checked.get(node.element_id),
                        href=node.attr("href"),
                        is_password=password,
                        disabled="disabled" in node.attrs,
                    )
                )
        return BrowserObservation(
            url=tab.url,
            title=tab.title,
            visible_text=" ".join(texts),
            actionable=actionable,
            active_tab=tab.tab_id,
            tabs=[{"tab_id": item.tab_id, "url": item.url, "title": item.title} for item in self.tabs],
            loading=bool(tab.loading),
            errors=[self.last_error] if self.last_error else [],
            status="AVAILABLE",
        )

    def find(
        self,
        *,
        role: str = "",
        name: str = "",
        label: str = "",
        text: str = "",
        placeholder: str = "",
        selector: str = "",
        element_id: str = "",
        **_ignored,
    ) -> Optional[Node]:
        tab = self.current()
        if tab is None:
            return None
        name = str(name or label or "").strip()
        matches: List[Node] = []
        for node in walk(tab.root):
            if element_id and node.element_id == element_id:
                return node
            if selector and not _selector_match(node, selector):
                continue
            if role and implicit_role(node) != str(role).lower():
                if not (str(role).lower() == "button" and node.tag == "button"):
                    continue
            if placeholder and placeholder.lower() not in node.attr("placeholder").lower():
                continue
            if name:
                acc = accessible_name(node, tab.root).lower()
                if str(name).lower() not in acc and str(name).lower() not in node.attr("id").lower():
                    continue
            if text and str(text).lower() not in node.descendant_text().lower():
                continue
            if node.tag in ("document", "html", "head", "body", "script", "style"):
                if not (selector and _selector_match(node, selector) and selector.lower() in ("html", "body")):
                    continue
            if selector or role or name or text or placeholder:
                matches.append(node)
        if not matches:
            return None
        preferred = [
            node
            for node in matches
            if implicit_role(node) in ("link", "button", "textbox", "searchbox", "checkbox", "combobox")
            or node.tag in ("a", "button", "input", "select", "textarea")
        ]
        chosen = preferred[0] if preferred else matches[0]
        return chosen

    def click(self, node: Node) -> BrowserObservation:
        tab = self.current()
        if tab is None:
            raise RuntimeError("no_session")
        href = node.attr("href")
        target = node.attr("target")
        if node.tag == "a" and href:
            resolved = _resolve_href(tab.url, href, self.fixture_root)
            if target == "_blank" or node.attr("data-popup"):
                html_url, html = load_url(resolved, fixture_root=self.fixture_root)
                new_tab = FakeTab(url=html_url, html=html)
                new_tab.popup_opened = True
                self.tabs.append(new_tab)
                self.active_index = len(self.tabs) - 1
                return self.observe()
            return self.goto(resolved)
        if implicit_role(node) == "checkbox" or (node.attr("type") or "").lower() == "checkbox":
            current = bool(tab.checked.get(node.element_id))
            tab.checked[node.element_id] = not current
            return self.observe()
        if node.tag == "button" or (node.attr("type") or "").lower() in ("submit", "button"):
            tab.submitted = True
            nxt = node.attr("data-next") or node.attr("formaction")
            if nxt:
                return self.goto(_resolve_href(tab.url, nxt, self.fixture_root))
        return self.observe()

    def type_text(self, node: Node, text: str) -> BrowserObservation:
        tab = self.current()
        if tab is None:
            raise RuntimeError("no_session")
        tab.values[node.element_id] = str(text or "")
        if implicit_role(node) in ("searchbox", "textbox") and node.attr("data-search") == "1":
            self._inject_search_results(tab, str(text or ""))
        return self.observe()

    def select_option(self, node: Node, value: str) -> BrowserObservation:
        tab = self.current()
        if tab is None:
            raise RuntimeError("no_session")
        tab.selected[node.element_id] = str(value or "")
        return self.observe()

    def set_checked(self, node: Node, checked: bool) -> BrowserObservation:
        tab = self.current()
        if tab is None:
            raise RuntimeError("no_session")
        tab.checked[node.element_id] = bool(checked)
        return self.observe()

    def scroll(self, amount: int) -> BrowserObservation:
        tab = self.current()
        if tab is None:
            raise RuntimeError("no_session")
        tab.scroll_y = max(0, tab.scroll_y + int(amount or 0))
        return self.observe()

    def press_enter(self, node: Optional[Node] = None) -> BrowserObservation:
        tab = self.current()
        if tab is None:
            raise RuntimeError("no_session")
        if node is not None and implicit_role(node) in ("searchbox", "textbox"):
            query = tab.values.get(node.element_id, "")
            nxt = node.attr("data-next")
            if nxt:
                dest = _resolve_href(tab.url, nxt, self.fixture_root)
                if "{q}" in dest:
                    dest = dest.replace("{q}", query)
                return self.goto(dest)
            self._inject_search_results(tab, query)
        return self.observe()

    def wait_for(self, *, selector: str = "", text: str = "", timeout: float = 2.0) -> BrowserObservation:
        deadline = time.time() + max(0.05, float(timeout or 0))
        if self.wait_fail_once:
            reason = self.wait_fail_once
            self.wait_fail_once = ""
            raise TimeoutError(reason)
        while time.time() < deadline:
            if selector and self.find(selector=selector) is not None:
                return self.observe()
            if text:
                obs = self.observe()
                if str(text).lower() in (obs.visible_text or "").lower():
                    return obs
            if not selector and not text:
                return self.observe()
            time.sleep(0.02)
        raise TimeoutError("timeout")

    def screenshot(self) -> str:
        return ""

    def _inject_search_results(self, tab: FakeTab, query: str) -> None:
        q = str(query or "").strip() or "wynik"
        extra = (
            f"<ul id='results'>"
            f"<li><a href='article.html' data-index='1'>{q} jeden</a></li>"
            f"<li><a href='article.html' data-index='2'>{q} dwa</a></li>"
            f"</ul>"
        )
        if "id='results'" in tab.html or 'id="results"' in tab.html:
            tab.html = re.sub(
                r"<ul[^>]*id=['\"]results['\"][^>]*>.*?</ul>",
                extra,
                tab.html,
                flags=re.I | re.S,
            )
        else:
            tab.html = tab.html.replace("</body>", extra + "</body>")
        tab.root = parse_html(tab.html)
        counter = [0]

        def assign(node: Node) -> None:
            counter[0] += 1
            node.element_id = f"e{counter[0]}"
            for child in node.children:
                assign(child)

        assign(tab.root)
        tab.title = _title_from_tree(tab.root) or tab.title


def _selector_match(node: Node, selector: str) -> bool:
    raw = str(selector or "").strip()
    if not raw:
        return False
    if raw.startswith("#"):
        return node.attr("id") == raw[1:]
    if raw.startswith("."):
        classes = node.attr("class").split()
        return raw[1:] in classes
    attr = re.match(r"^\[([^=\]]+)=['\"]?([^'\"]+)['\"]?\]$", raw)
    if attr:
        return node.attr(attr.group(1)) == attr.group(2)
    if re.match(r"^[a-z0-9]+$", raw, re.I):
        return node.tag == raw.lower()
    if raw.startswith("[") and raw.endswith("]"):
        key = raw[1:-1]
        return key.lower() in node.attrs
    return node.attr("id") == raw or raw.lower() in node.attr("class").lower()


def _resolve_href(current: str, href: str, fixture_root: Optional[Path]) -> str:
    target = str(href or "").strip()
    if not target:
        return current
    if target.startswith(("http://", "https://", "file:", "data:", "about:")):
        return target
    current_path = _file_from_url(current)
    if current_path is not None:
        return (current_path.parent / target).resolve().as_uri()
    if fixture_root is not None:
        return (fixture_root / target).resolve().as_uri()
    return urljoin(current, target)


def path_to_uri(path: Path) -> str:
    return Path(path).resolve().as_uri()
