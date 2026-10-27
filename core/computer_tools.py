# ==========================
# ELIAN — ComputerAgent ToolRegistry adapters (Phase 13 Stage C)
# ==========================
"""Thin ToolRegistry wrappers. Execution lives in ComputerAgent."""

from __future__ import annotations

from core.computer_agent import get_computer_agent
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry


def _agent():
    return get_computer_agent()


def _call(action: str, arguments=None) -> ToolResult:
    return _agent().act(action, dict(arguments or {}))


def register_computer_tools(registry: ToolRegistry) -> ToolRegistry:
    from Config import get_config_value

    def _timeout_for(name: str) -> float:
        try:
            action = float(
                get_config_value("computer_control", "computer_action_timeout", default=12) or 12
            )
            observation = float(
                get_config_value("computer_control", "computer_observation_timeout", default=15) or 15
            )
        except Exception:
            action, observation = 12.0, 15.0
        action = max(4.0, min(action, 30.0))
        observation = max(6.0, min(observation, 30.0))
        if name in {"wait_for_window", "wait_for_element", "wait_for_state"}:
            return max(observation, 18.0)
        if name in {
            "inspect_ui",
            "list_windows",
            "get_active_window",
            "find_file",
            "window_exists",
        }:
            return observation
        return action

    specs = (
        ("list_windows", "Lists open windows (title, process, bounds).", {}, RiskLevel.SAFE),
        ("get_active_window", "Returns the foreground window.", {}, RiskLevel.SAFE),
        ("focus_window", "Focus an existing window by name/id. Do not relaunch if already open.",
         {"window_id": {"type": "string"}, "name": {"type": "string"}, "query": {"type": "string"}},
         RiskLevel.SAFE),
        ("switch_window", "Switch to an existing window.",
         {"window_id": {"type": "string"}, "name": {"type": "string"}}, RiskLevel.SAFE),
        ("minimize_window", "Minimize a window.",
         {"window_id": {"type": "string"}, "name": {"type": "string"}}, RiskLevel.SAFE),
        ("maximize_window", "Maximize a window.",
         {"window_id": {"type": "string"}, "name": {"type": "string"}}, RiskLevel.SAFE),
        ("restore_window", "Restore a window.",
         {"window_id": {"type": "string"}, "name": {"type": "string"}}, RiskLevel.SAFE),
        ("close_window", "Close a window. Stops on unsaved-document dialogs.",
         {"window_id": {"type": "string"}, "name": {"type": "string"}}, RiskLevel.MEDIUM),
        ("wait_for_window", "Wait until a named window exists.",
         {"name": {"type": "string"}, "timeout": {"type": "number"}}, RiskLevel.SAFE),
        ("window_exists", "Check whether a window/app is open.",
         {"name": {"type": "string"}, "query": {"type": "string"}}, RiskLevel.SAFE),
        ("inspect_ui", "Cheap UIA inspect of the active window. No full screenshot unless needed.",
         {"query": {"type": "string"}}, RiskLevel.SAFE),
        ("double_click_ui_element", "Double-click a resolved UI element_id.",
         {"element_id": {"type": "string"}, "query": {"type": "string"}}, RiskLevel.SAFE),
        ("right_click_ui_element", "Right-click a resolved UI element_id.",
         {"element_id": {"type": "string"}, "query": {"type": "string"}}, RiskLevel.SAFE),
        ("type_text", "Type into the current editor. Verifies Value/Text when available.",
         {"text": {"type": "string"}, "element_id": {"type": "string"}, "app_name": {"type": "string"}},
         RiskLevel.SAFE),
        ("hotkey", "Send a whitelisted hotkey (ctrl+a, ctrl+z, ...).",
         {"keys": {"type": "string"}, "key": {"type": "string"}}, RiskLevel.SAFE),
        ("scroll_up", "Scroll up in the active window.",
         {"amount": {"type": "integer"}}, RiskLevel.SAFE),
        ("scroll_down", "Scroll down in the active window.",
         {"amount": {"type": "integer"}}, RiskLevel.SAFE),
        ("scroll_element", "Scroll a resolved element.",
         {"element_id": {"type": "string"}, "direction": {"type": "string"}, "amount": {"type": "integer"}},
         RiskLevel.SAFE),
        ("drag_element", "Drag only when start and destination are confidently resolved.",
         {"element_id": {"type": "string"}, "to_element_id": {"type": "string"}}, RiskLevel.MEDIUM),
        ("drag_to_element", "Drag source to destination element.",
         {"element_id": {"type": "string"}, "to_element_id": {"type": "string"}}, RiskLevel.MEDIUM),
        ("drag_by", "Drag by offset from a resolved element.",
         {"element_id": {"type": "string"}, "dx": {"type": "integer"}, "dy": {"type": "integer"}},
         RiskLevel.MEDIUM),
        ("wait_for_element", "Wait until a UI element is present.",
         {"query": {"type": "string"}, "timeout": {"type": "number"}}, RiskLevel.SAFE),
        ("wait_for_state", "Wait for a window/element state.",
         {"name": {"type": "string"}, "timeout": {"type": "number"}}, RiskLevel.SAFE),
        ("find_file", "Find a file by name. Clarifies if multiple matches.",
         {"query": {"type": "string"}}, RiskLevel.SAFE),
        ("open_file", "Open a file path.",
         {"path": {"type": "string"}, "name": {"type": "string"}}, RiskLevel.MEDIUM),
        ("list_folder", "List a folder.",
         {"path": {"type": "string"}}, RiskLevel.SAFE),
        ("read_clipboard", "Read clipboard only when the user explicitly asks. Do not log contents.",
         {}, RiskLevel.SAFE),
        ("lock_pc", "Lock the workstation.", {}, RiskLevel.MEDIUM),
        ("sleep_pc", "Sleep the PC. Requires confirmation.", {}, RiskLevel.DANGEROUS),
        ("shutdown_pc", "Shut down. Requires confirmation. Never silent.", {}, RiskLevel.DANGEROUS),
        ("restart_pc", "Restart. Requires confirmation. Never silent.", {}, RiskLevel.DANGEROUS),
    )
    aliases = {
        "focus_window": "focus_window",
        "switch_window": "switch_window",
        "minimize_window": "minimize_window",
        "maximize_window": "maximize_window",
        "restore_window": "restore_window",
        "close_window": "close_window",
        "wait_for_window": "wait_for_window",
        "window_exists": "window_exists",
        "inspect_ui": "inspect_ui",
        "list_windows": "list_windows",
        "get_active_window": "get_active_window",
        "double_click_ui_element": "double_click_ui_element",
        "right_click_ui_element": "right_click_ui_element",
        "type_text": "type_text",
        "hotkey": "hotkey",
        "scroll_up": "scroll_up",
        "scroll_down": "scroll_down",
        "scroll_element": "scroll_element",
        "drag_element": "drag_element",
        "drag_to_element": "drag_to_element",
        "drag_by": "drag_by",
        "wait_for_element": "wait_for_element",
        "wait_for_state": "wait_for_state",
        "find_file": "find_file",
        "open_file": "open_file",
        "list_folder": "list_folder",
        "read_clipboard": "read_clipboard",
        "lock_pc": "lock_pc",
        "sleep_pc": "sleep_pc",
        "shutdown_pc": "shutdown_pc",
        "restart_pc": "restart_pc",
    }

    def _handler(action: str):
        def inner(arguments):
            return _call(action, arguments)

        return inner

    for name, description, properties, risk in specs:
        if registry.get(name) is not None:
            registry.unregister(name)
        params = {"type": "object", "properties": properties}
        registry.register(
            Tool(
                name=name,
                description=description,
                parameters=params,
                risk_level=risk,
                handler=_handler(aliases.get(name, name)),
                timeout=_timeout_for(name),
                confirmation_prompt=(
                    "Mam wyłączyć komputer?" if name == "shutdown_pc"
                    else "Mam zrestartować komputer?" if name == "restart_pc"
                    else "Mam uśpić komputer?" if name == "sleep_pc"
                    else "Mam zablokować komputer?" if name == "lock_pc"
                    else ""
                ),
            )
        )

    def _click_wrap(arguments):
        args = dict(arguments or {})
        if args.get("element_id"):
            from vision.tools import _click_ui_element

            return _click_ui_element(args)
        return _call("click_ui_element", args)

    existing_click = registry.get("click_ui_element")
    if existing_click is not None:
        existing_click.handler = _click_wrap
    else:
        registry.register(
            Tool(
                name="click_ui_element",
                description="Click a UI element by element_id or query. Confidence lock 0.8; ambiguous → clarify.",
                parameters={
                    "type": "object",
                    "properties": {
                        "element_id": {"type": "string"},
                        "query": {"type": "string"},
                    },
                },
                risk_level=RiskLevel.SAFE,
                handler=_click_wrap,
                timeout=30,
            )
        )
    return registry
