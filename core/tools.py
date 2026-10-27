"""Central tool registry. Planner will invoke the same registry later."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from typing import Any, Callable

from Diagnostics import log_turn
from Memory import record_action

from core.risk import RiskLevel
from core.tool_result import ToolResult, ToolStatus


Handler = Callable[[dict[str, Any]], ToolResult]

_COMPUTER_JOIN_TOOLS = frozenset(
    {
        "type_text",
        "hotkey",
        "press_key",
        "select_all",
        "undo",
        "copy",
        "paste",
        "cut",
        "delete_last_word",
        "open_app",
        "focus_window",
        "focus_or_open",
        "click_ui_element",
        "inspect_ui",
    }
)

_BROWSER_JOIN_TOOLS = frozenset(
    {
        "browser_launch",
        "browser_attach",
        "browser_open_url",
        "browser_read_title",
        "browser_read_page",
        "browser_find_element",
        "browser_click",
        "browser_type",
        "browser_select",
        "browser_check",
        "browser_scroll",
        "browser_back",
        "browser_forward",
        "browser_wait",
        "browser_screenshot",
        "browser_search_service",
        "browser_select_result",
        "browser_play_media",
        "close_browser_tab",
    }
)

COMPUTER_JOIN_GRACE_SEC = 2.0


@dataclass
class Tool:
    name: str
    description: str
    handler: Handler
    parameters: dict[str, Any] = field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.SAFE
    available: bool = True
    timeout: float | None = None
    confirmation_prompt: str = ""

    def prompt_for_confirmation(self, arguments=None):
        if self.confirmation_prompt:
            return self.confirmation_prompt
        args = arguments or {}
        detail = args.get("name") or args.get("target") or args.get("app") or ""
        if detail:
            return f"Czy na pewno mam wykonać {self.name} ({detail})?"
        return f"Czy na pewno mam wykonać: {self.name}?"


class ToolRegistry:
    def __init__(self, confirm_medium=True):
        self._tools: dict[str, Tool] = {}
        self.confirm_medium = bool(confirm_medium)
        self.last_result: ToolResult | None = None
        self.history: list[dict[str, Any]] = []

    def register(self, tool: Tool):
        if not tool.name:
            raise ValueError("Tool name is required")
        self._tools[tool.name] = tool
        return tool

    def unregister(self, name):
        return self._tools.pop(name, None)

    def get(self, name):
        return self._tools.get(name)

    def has(self, name):
        return name in self._tools

    def list_tools(self):
        return sorted(self._tools.values(), key=lambda item: item.name)

    def descriptions_for_planner(self):
        """Catalog for Planner / LLM tool-calling prompts."""
        catalog = []
        for tool in self.list_tools():
            params = dict(tool.parameters or {})
            required = list(params.get("required") or [])
            properties = dict(params.get("properties") or {})
            catalog.append({
                "name": tool.name,
                "description": tool.description,
                "parameters": params,
                "required": required,
                "properties": properties,
                "risk_level": tool.risk_level.value,
                "available": tool.available,
            })
        return catalog

    def validate_call(self, name, arguments=None) -> ToolResult | None:
        """
        Validate a tool call before execution.
        Returns None if valid, or a ToolResult describing the validation error.
        """
        arguments = dict(arguments or {})
        tool = self.get(name)
        if tool is None:
            return ToolResult.fail(
                f"Nie znam narzędzia: {name}.",
                tool_name=str(name or ""),
                error="unknown_tool",
            )

        params = tool.parameters or {}
        properties = dict(params.get("properties") or {})
        required = list(params.get("required") or [])

        for key in required:
            value = arguments.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                return ToolResult.fail(
                    f"Brakuje wymaganego argumentu: {key}.",
                    tool_name=tool.name,
                    error="missing_argument",
                    data={"argument": key},
                )

        if properties:
            allowed = set(properties.keys())
            # Also allow common aliases used by adapters.
            allowed |= {
                "app",
                "app_name",
                "q",
                "url",
                "element_id",
                "id",
                "confirmed",
                "keys",
                "key",
                "direction",
                "amount",
                "question",
                "query",
                "text",
                "website_direct",
                "explicit_app",
                "value",
                "x",
                "y",
                "active_window_only",
                "when",
                "due_at",
                "in",
                "title",
                "start",
                "end",
                "duration",
                "duration_minutes",
                "location",
                "description",
                "calendar",
                "allow_conflict",
                "event_id",
                "reminder_id",
                "recipient",
                "contact",
                "message",
                "channel",
                "day",
                "scene",
                "query",
                "provider",
                "semantic_action",
                "extra",
                "name",
                "plan_id",
                "step_id",
                "attempt_id",
                "append",
                "text_payload",
                "window_id",
                "timeout",
                "path",
                "role",
                "label",
                "placeholder",
                "selector",
                "authorized_secret",
                "tab_id",
                "headless",
                "cdp_url",
                "ws_url",
                "checked",
            }
            unknown = [key for key in arguments if key not in allowed]
            if unknown and allowed:
                return ToolResult.fail(
                    f"Niedozwolone argumenty: {', '.join(unknown)}.",
                    tool_name=tool.name,
                    error="invalid_arguments",
                    data={"unknown": unknown},
                )

            for key, value in arguments.items():
                if key not in properties:
                    continue
                schema = properties.get(key) or {}
                expected = schema.get("type")
                if expected == "string" and value is not None and not isinstance(value, str):
                    return ToolResult.fail(
                        f"Argument {key} musi być tekstem.",
                        tool_name=tool.name,
                        error="invalid_type",
                    )
                if expected == "integer" and value is not None and not isinstance(value, int):
                    # Allow numeric strings that look like ints.
                    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
                        continue
                    if isinstance(value, float) and value.is_integer():
                        continue
                    return ToolResult.fail(
                        f"Argument {key} musi być liczbą całkowitą.",
                        tool_name=tool.name,
                        error="invalid_type",
                    )

        return None

    def invoke(
        self,
        name,
        arguments=None,
        *,
        confirmed=False,
        force=False,
        skip_validation=False,
    ) -> ToolResult:
        arguments = dict(arguments or {})
        if not skip_validation:
            validation = self.validate_call(name, arguments)
            if validation is not None:
                return self._finalize(validation, arguments)

        tool = self.get(name)
        if tool is None:
            result = ToolResult.fail(
                f"Nie znam narzędzia: {name}.",
                tool_name=str(name or ""),
                error="unknown_tool",
                error_code="UNKNOWN_TOOL",
            )
            return self._finalize(result, arguments)

        if not force:
            try:
                from core.capability_registry import capability_gate_for_tool

                gated = capability_gate_for_tool(tool.name)
                if gated is not None:
                    return self._finalize(gated, arguments)
            except Exception:
                pass

        if not tool.available and not force:
            result = ToolResult.unavailable(
                f"Narzędzie {tool.name} jest obecnie niedostępne.",
                tool_name=tool.name,
                error_code="TOOL_UNAVAILABLE",
            )
            return self._finalize(result, arguments)

        needs_confirm = tool.risk_level.requires_confirmation(self.confirm_medium)
        if needs_confirm and not confirmed:
            result = ToolResult.needs_confirmation(
                tool.prompt_for_confirmation(arguments),
                tool_name=tool.name,
                data={"arguments": arguments, "risk_level": tool.risk_level.value},
            )
            return self._finalize(result, arguments, skip_record=True)

        try:
            from core.hud_events import emit_tool_started

            emit_tool_started(tool.name)
        except Exception:
            pass
        before_state = None
        try:
            from core.action_journal import begin_capture

            before_state = begin_capture(tool.name, arguments)
        except Exception:
            before_state = None
        try:
            if tool.timeout and tool.timeout > 0:
                tool_name = str(getattr(tool, "name", "") or "")
                if tool_name in _COMPUTER_JOIN_TOOLS:
                    result = self._invoke_computer_tool(tool, arguments)
                elif tool_name in _BROWSER_JOIN_TOOLS:
                    result = self._invoke_browser_tool(tool, arguments)
                else:
                    result = self._invoke_with_timeout(tool, arguments)
            else:
                result = tool.handler(arguments)
        except Exception as error:
            print(f"Błąd narzędzia {tool.name}:", error)
            result = ToolResult.fail(
                f"Nie udało się wykonać {tool.name}.",
                error=str(error),
                tool_name=tool.name,
                retryable=True,
            )

        if not isinstance(result, ToolResult):
            result = ToolResult.ok(str(result), tool_name=tool.name)

        if not result.tool_name:
            result.tool_name = tool.name
        finalized = self._finalize(result, arguments, before_state=before_state)
        try:
            from core.hud_events import emit_tool_finished

            emit_tool_finished(
                finalized.tool_name or tool.name,
                success=bool(finalized.success),
            )
        except Exception:
            pass
        return finalized

    def _invoke_computer_tool(self, tool, arguments):
        """Persistent UIA thread. Never abandon a side-effecting worker."""
        from core.computer_agent import get_computer_agent, take_stashed_verified_result
        from core.computer_ui_worker import submit_computer_job, wait_computer_idle

        agent = get_computer_agent()
        timeout = float(tool.timeout or 12)
        agent.begin_job(timeout)
        future = submit_computer_job(tool.handler, arguments)
        try:
            result = future.result(timeout=timeout)
            return result
        except FuturesTimeout:
            agent.signal_deadline()
            try:
                result = future.result(timeout=COMPUTER_JOIN_GRACE_SEC)
            except FuturesTimeout:
                # Still running — JOIN until idle so the next plan step cannot start.
                wait_computer_idle(timeout=max(timeout, 15.0))
                try:
                    result = future.result(timeout=0.05)
                except Exception:
                    result = None
                try:
                    from core.computer_ui_worker import computer_worker_busy
                    from core.computer_agent import _METRICS

                    if computer_worker_busy():
                        _METRICS["post_timeout_side_effect_race"] = int(
                            _METRICS.get("post_timeout_side_effect_race") or 0
                        ) + 1
                except Exception:
                    pass
            if result is None:
                stashed = take_stashed_verified_result()
                if stashed is not None and stashed.success:
                    return stashed
                if agent._action_started:
                    return ToolResult.unknown_side_effect(
                        f"Nie potwierdziłem skutku {tool.name}.",
                        tool_name=tool.name,
                    )
                return ToolResult.timed_out(
                    f"Narzędzie {tool.name} przekroczyło limit czasu.",
                    tool_name=tool.name,
                    data={"side_effect_may_have_occurred": False},
                )
            if getattr(result, "success", False):
                return result
            stashed = take_stashed_verified_result()
            if stashed is not None and stashed.success:
                return stashed
            return result

    def _invoke_browser_tool(self, tool, arguments):
        """Persistent Playwright thread. Do not abandon a live browser job."""
        from core.browser_worker import submit_browser_job, wait_browser_idle

        timeout = float(tool.timeout or 15)
        future = submit_browser_job(tool.handler, arguments)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout:
            wait_browser_idle(timeout=min(timeout, 8.0))
            try:
                return future.result(timeout=0.05)
            except Exception:
                return ToolResult.timed_out(
                    f"Narzędzie {tool.name} przekroczyło limit czasu.",
                    tool_name=tool.name,
                )

    def _invoke_with_timeout(self, tool, arguments):
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(tool.handler, arguments)
        try:
            result = future.result(timeout=tool.timeout)
            pool.shutdown(wait=True)
            return result
        except FuturesTimeout:
            pool.shutdown(wait=False)
            return ToolResult.timed_out(
                f"Narzędzie {tool.name} przekroczyło limit czasu.",
                tool_name=tool.name,
            )
        except Exception:
            pool.shutdown(wait=False)
            raise

    def _finalize(self, result: ToolResult, arguments, skip_record=False, before_state=None):
        self.last_result = result
        observation = result.to_observation()
        observation["arguments"] = dict(arguments)
        observation["ts"] = time.time()
        self.history.append(observation)
        if len(self.history) > 40:
            self.history = self.history[-40:]

        log_turn(
            "tool",
            name=result.tool_name,
            status=result.status.value,
            success=result.success,
            message=result.message[:120] if result.message else "",
        )
        try:
            from core.metrics import get_metrics

            get_metrics().record_tool(success=bool(result.success))
        except Exception:
            pass
        try:
            from core.self_improve.runtime_collector import note_tool_event

            error_code = str(getattr(result, "error_code", "") or "")
            # Preserve richer semantic codes from data without changing ToolResult.fail().
            data = getattr(result, "data", None) or {}
            if isinstance(data, dict):
                nested = str(data.get("error_code") or "").strip()
                if nested and (
                    not error_code or error_code.strip().upper() == "FAILURE"
                ):
                    error_code = nested
            note_tool_event(
                tool_name=str(result.tool_name or ""),
                status=str(getattr(result.status, "value", result.status) or ""),
                success=bool(result.success),
                error_code=error_code,
            )
        except Exception:
            pass

        if not skip_record:
            try:
                from core.world_state import apply_tool_result

                apply_tool_result(result.tool_name, arguments, result)
            except Exception:
                pass

        if not skip_record and result.tool_name:
            record_action(
                result.tool_name,
                argument=str(
                    arguments.get("name")
                    or arguments.get("target")
                    or arguments.get("query")
                    or arguments.get("url")
                    or ""
                ),
                result=result.message,
                success=result.success,
            )
            try:
                from core.action_journal import note_registry_result

                note_registry_result(
                    result.tool_name,
                    arguments,
                    result,
                    before_state=before_state,
                )
            except Exception:
                pass
        return result


_default_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _default_registry
    if _default_registry is None:
        from core.builtin_tools import build_default_registry

        _default_registry = build_default_registry()
    # Self-Improvement System: pick up human-approved learned tools (hot-swap).
    try:
        from core.self_improve.tool_hot_swap import load_learned_tools

        load_learned_tools(_default_registry)
    except Exception:
        pass
    return _default_registry


def reset_registry_for_tests(registry=None):
    global _default_registry
    _default_registry = registry
    try:
        from core.active_service import reset_active_service

        reset_active_service()
    except Exception:
        pass
    try:
        from core.world_state import reset_world_state

        reset_world_state()
    except Exception:
        pass
