"""Elian agent core (Phase 1–2 foundation)."""

from core.agent import agent_enabled, run_agent
from core.conversation_manager import ConversationManager, HandleResult, should_end_conversation
from core.model_client import FakeModelClient, get_model_client, set_model_client
from core.planner import Planner, PlannerResult
from core.risk import RiskLevel
from core.tool_result import ToolResult, ToolStatus
from core.tools import Tool, ToolRegistry, get_registry, reset_registry_for_tests

__all__ = [
    "ConversationManager",
    "FakeModelClient",
    "HandleResult",
    "Planner",
    "PlannerResult",
    "RiskLevel",
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "ToolStatus",
    "agent_enabled",
    "get_model_client",
    "get_registry",
    "reset_registry_for_tests",
    "run_agent",
    "set_model_client",
    "should_end_conversation",
]
