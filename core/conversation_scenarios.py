# ==========================
# ELIAN — conversation scenario harness (Phase 8)
# ==========================
"""Offline multi-turn conversation checks without microphone/Ollama/network."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional


@dataclass
class TurnExpectation:
    tools: Optional[List[str]] = None
    no_tools: Optional[List[str]] = None
    source: Optional[str] = None
    clarification: Optional[bool] = None
    confirmation: Optional[bool] = None
    contains: Optional[str] = None
    not_contains: Optional[str] = None
    silent: Optional[bool] = None
    custom: Optional[Callable[[Any, List[dict]], None]] = None


@dataclass
class ScenarioTurn:
    user: str
    expect: TurnExpectation = field(default_factory=TurnExpectation)


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    details: str = ""


class ConversationScenarioRunner:
    def __init__(self, manager, *, tool_log: Optional[List[dict]] = None):
        self.manager = manager
        self.tool_log = tool_log if tool_log is not None else []
        self.turns: List[Any] = []

    def run(self, name: str, turns: List[ScenarioTurn]) -> ScenarioResult:
        self.manager.activate()
        before = len(self.tool_log)
        try:
            for index, turn in enumerate(turns):
                start = len(self.tool_log)
                result = self.manager.handle(turn.user)
                self.turns.append(result)
                called = [item.get("tool") for item in self.tool_log[start:]]
                exp = turn.expect
                if exp.tools is not None:
                    for tool in exp.tools:
                        if tool not in called:
                            return ScenarioResult(
                                name,
                                False,
                                f"turn {index}: expected tool {tool}, got {called}",
                            )
                if exp.no_tools is not None:
                    for tool in exp.no_tools:
                        if tool in called:
                            return ScenarioResult(
                                name,
                                False,
                                f"turn {index}: unexpected tool {tool}",
                            )
                if exp.source is not None and result.source != exp.source:
                    return ScenarioResult(
                        name,
                        False,
                        f"turn {index}: source {result.source} != {exp.source}",
                    )
                if exp.clarification is True and result.source not in ("clarify", "clarification"):
                    return ScenarioResult(
                        name,
                        False,
                        f"turn {index}: expected clarification, source={result.source}",
                    )
                if exp.confirmation is True and result.source not in ("confirm", "confirmation"):
                    return ScenarioResult(
                        name,
                        False,
                        f"turn {index}: expected confirmation, source={result.source}",
                    )
                text = str(result.text or "")
                if exp.contains and exp.contains.lower() not in text.lower():
                    return ScenarioResult(
                        name,
                        False,
                        f"turn {index}: missing '{exp.contains}' in '{text}'",
                    )
                if exp.not_contains and exp.not_contains.lower() in text.lower():
                    return ScenarioResult(
                        name,
                        False,
                        f"turn {index}: unexpected '{exp.not_contains}' in '{text}'",
                    )
                if exp.silent is True and (result.should_speak and text.strip()):
                    return ScenarioResult(
                        name,
                        False,
                        f"turn {index}: expected silent reply",
                    )
                if exp.custom:
                    exp.custom(result, self.tool_log[start:])
            return ScenarioResult(name, True, f"turns={len(turns)} tools={len(self.tool_log) - before}")
        finally:
            try:
                self.manager.deactivate(reason="scenario_end")
            except Exception:
                pass
