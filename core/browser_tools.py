# ==========================
# ELIAN — BrowserAgent ToolRegistry adapters (Phase 13 Stage D)
# ==========================
"""Thin ToolRegistry wrappers. Execution lives in BrowserAgent. No second loop."""

from __future__ import annotations

from core.browser_agent import get_browser_agent
from core.risk import RiskLevel
from core.tools import Tool, ToolRegistry


def _agent():
    return get_browser_agent()


def _call(action: str, arguments=None):
    return _agent().act(action, dict(arguments or {}))


def register_browser_tools(registry: ToolRegistry) -> ToolRegistry:
    from Config import get_config_value

    def _timeout() -> float:
        try:
            section = get_config_value("integrations", "browser", default={}) or {}
            raw = float(section.get("action_timeout_sec") or 12)
        except Exception:
            raw = 12.0
        return max(4.0, min(raw, 45.0))

    timeout = _timeout()
    specs = (
        (
            "browser_launch",
            "Launch a managed Playwright browser session.",
            {"url": {"type": "string"}, "headless": {"type": "boolean"}},
            RiskLevel.SAFE,
        ),
        (
            "browser_attach",
            "Attach to a browser over CDP when configured. Honest unavailable otherwise.",
            {"cdp_url": {"type": "string"}, "ws_url": {"type": "string"}},
            RiskLevel.SAFE,
        ),
        (
            "browser_open_url",
            "Open a URL in the managed browser session.",
            {"url": {"type": "string"}, "target": {"type": "string"}},
            RiskLevel.SAFE,
        ),
        (
            "browser_read_title",
            "Read the current page title.",
            {},
            RiskLevel.SAFE,
        ),
        (
            "browser_read_page",
            "Read visible text and actionable elements (role/label). No screenshot.",
            {},
            RiskLevel.SAFE,
        ),
        (
            "browser_find_element",
            "Find an element by role, label, text, placeholder, or selector.",
            {
                "role": {"type": "string"},
                "name": {"type": "string"},
                "label": {"type": "string"},
                "text": {"type": "string"},
                "placeholder": {"type": "string"},
                "selector": {"type": "string"},
                "element_id": {"type": "string"},
            },
            RiskLevel.SAFE,
        ),
        (
            "browser_click",
            "Click a resolved element by role/label/text. Purchases need confirmation.",
            {
                "role": {"type": "string"},
                "name": {"type": "string"},
                "label": {"type": "string"},
                "text": {"type": "string"},
                "query": {"type": "string"},
                "selector": {"type": "string"},
                "element_id": {"type": "string"},
                "confirmed": {"type": "boolean"},
            },
            RiskLevel.SAFE,
        ),
        (
            "browser_type",
            "Type into a textbox. Never fills passwords/tokens from memory.",
            {
                "text": {"type": "string"},
                "value": {"type": "string"},
                "role": {"type": "string"},
                "label": {"type": "string"},
                "placeholder": {"type": "string"},
                "selector": {"type": "string"},
                "element_id": {"type": "string"},
                "authorized_secret": {"type": "boolean"},
            },
            RiskLevel.SAFE,
        ),
        (
            "browser_select",
            "Select an option in a combobox.",
            {
                "value": {"type": "string"},
                "text": {"type": "string"},
                "label": {"type": "string"},
                "selector": {"type": "string"},
                "element_id": {"type": "string"},
            },
            RiskLevel.SAFE,
        ),
        (
            "browser_check",
            "Toggle a checkbox.",
            {
                "checked": {"type": "boolean"},
                "label": {"type": "string"},
                "name": {"type": "string"},
                "selector": {"type": "string"},
                "element_id": {"type": "string"},
            },
            RiskLevel.SAFE,
        ),
        (
            "browser_scroll",
            "Scroll the page. Not a coordinate click.",
            {"direction": {"type": "string"}, "amount": {"type": "integer"}},
            RiskLevel.SAFE,
        ),
        (
            "browser_back",
            "Go back in the current tab.",
            {},
            RiskLevel.SAFE,
        ),
        (
            "browser_forward",
            "Go forward in the current tab.",
            {},
            RiskLevel.SAFE,
        ),
        (
            "browser_wait",
            "Wait for a selector or text change. Bounded timeout.",
            {
                "selector": {"type": "string"},
                "text": {"type": "string"},
                "query": {"type": "string"},
                "timeout": {"type": "number"},
            },
            RiskLevel.SAFE,
        ),
        (
            "browser_screenshot",
            "Diagnostic screenshot fallback only. Not primary control.",
            {},
            RiskLevel.SAFE,
        ),
        (
            "browser_search_service",
            "Wyszukuje w otwartej usłudze web (BrowserAgent).",
            {"query": {"type": "string"}, "name": {"type": "string"}, "url": {"type": "string"}},
            RiskLevel.SAFE,
        ),
        (
            "browser_select_result",
            "Wybiera wynik na stronie (BrowserAgent).",
            {"index": {"type": "integer"}, "query": {"type": "string"}},
            RiskLevel.SAFE,
        ),
        (
            "browser_play_media",
            "Odtwarza media na stronie (BrowserAgent).",
            {"query": {"type": "string"}, "name": {"type": "string"}},
            RiskLevel.SAFE,
        ),
        (
            "close_browser_tab",
            "Zamyka konkretną kartę usługi (BrowserAgent).",
            {"name": {"type": "string"}, "tab_id": {"type": "string"}},
            RiskLevel.SAFE,
        ),
    )

    def _handler(action: str):
        def inner(arguments):
            return _call(action, arguments)

        return inner

    for name, description, properties, risk in specs:
        if registry.get(name) is not None:
            registry.unregister(name)
        registry.register(
            Tool(
                name=name,
                description=description,
                parameters={"type": "object", "properties": properties},
                risk_level=risk,
                handler=_handler(name),
                timeout=timeout if name != "browser_wait" else max(timeout, 18.0),
            )
        )
    return registry
