"""Structured agent decisions (tool_call / final_answer / clarification)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

from core.agent_diagnostics import ParseDiagnostics


DECISION_TYPES = ("tool_call", "final_answer", "clarification")


@dataclass
class AgentDecision:
    type: str
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    question: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_tool_call(self):
        return self.type == "tool_call"

    @property
    def is_final(self):
        return self.type == "final_answer"

    @property
    def is_clarification(self):
        return self.type == "clarification"


def _extract_json_object(text):
    text = str(text or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _response_kind(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return "empty"
    if raw.startswith("```"):
        return "markdown_json"
    if raw.startswith("{") or raw.startswith("["):
        return "json"
    if "{" in raw and "}" in raw:
        return "embedded_json"
    return "text"


def _validate_decision_data(data: dict) -> tuple[bool, str]:
    kind = str(data.get("type") or "").strip().lower()
    if not kind:
        if data.get("tool"):
            kind = "tool_call"
        elif data.get("question"):
            kind = "clarification"
        elif data.get("message") or data.get("answer"):
            kind = "final_answer"
        else:
            return False, "missing_type"
    if kind not in DECISION_TYPES:
        return False, "unsupported_type"
    if kind == "tool_call" and not str(data.get("tool") or data.get("name") or "").strip():
        return False, "missing_tool"
    if kind == "clarification" and not str(data.get("question") or data.get("message") or "").strip():
        return False, "missing_question"
    return True, ""


def parse_agent_decision_detailed(text) -> Tuple[Optional["AgentDecision"], ParseDiagnostics]:
    diag = ParseDiagnostics()
    raw = str(text or "")
    diag.response_kind = _response_kind(raw)

    data = _extract_json_object(text)
    if not data:
        diag.json_parse_ok = False
        diag.parse_error_type = "no_json_object" if raw.strip() else "empty_response"
        diag.retry_reason = diag.parse_error_type
        return None, diag

    diag.json_parse_ok = True
    ok, verr = _validate_decision_data(data)
    diag.schema_validation_ok = ok
    if not ok:
        diag.validation_error = verr
        diag.parse_error_type = "schema_validation"
        diag.retry_reason = verr
        return None, diag

    decision = parse_agent_decision(text)
    if decision is None:
        diag.parse_error_type = "normalize_failed"
        diag.retry_reason = "normalize_failed"
        return None, diag

    diag.decision_type = decision.type
    diag.tool_name = decision.tool
    diag.argument_keys = ",".join(sorted(decision.arguments.keys())) if decision.arguments else ""
    return decision, diag


def parse_agent_decision(text) -> AgentDecision | None:
    data = _extract_json_object(text)
    if not data:
        return None

    kind = str(data.get("type") or "").strip().lower()
    if kind not in DECISION_TYPES:
        # Tolerate alternate keys from local models.
        if data.get("tool"):
            kind = "tool_call"
        elif data.get("question"):
            kind = "clarification"
        elif data.get("message") or data.get("answer"):
            kind = "final_answer"
        else:
            return None

    tool = str(data.get("tool") or data.get("name") or "").strip()
    arguments = data.get("arguments") or data.get("args") or {}
    if not isinstance(arguments, dict):
        arguments = {}

    # Normalize common aliases from the model.
    if "app_name" in arguments and "name" not in arguments:
        arguments["name"] = arguments.pop("app_name")
    if "app" in arguments and "name" not in arguments:
        arguments["name"] = arguments.get("app")

    message = str(
        data.get("message")
        or data.get("answer")
        or data.get("text")
        or ""
    ).strip()
    question = str(data.get("question") or message or "").strip()

    if kind == "tool_call" and not tool:
        return None
    if kind == "final_answer" and not message:
        message = "Gotowe."
    if kind == "clarification" and not question:
        return None

    return AgentDecision(
        type=kind,
        tool=tool,
        arguments=dict(arguments),
        message=message,
        question=question if kind == "clarification" else "",
        raw=data,
    )


AGENT_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": list(DECISION_TYPES),
        },
        "tool": {"type": "string"},
        "arguments": {"type": "object"},
        "message": {"type": "string"},
        "question": {"type": "string"},
    },
    "required": ["type"],
}
