# ==========================
# ELIAN — Agent factory / spawner
# ==========================
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Type

from .base import (
    BaseAgent,
    ConversationBridgeAgent,
    MemoryAgent,
    SelfDevelopmentAgent,
    SpecialistWorkerAgent,
    ToolWorkerAgent,
)
from .types import (
    PERM_CHECKPOINT,
    PERM_GUI_CONTROL,
    PERM_READ_MEMORY,
    PERM_REPO_WRITE,
    PERM_REQUEST_AGENTS,
    PERM_REQUEST_TOOLS,
    PERM_ROLLBACK,
    PERM_RUN_TESTS,
    PERM_TOOL_INVOKE,
    PERM_WRITE_MEMORY,
    AgentIdentity,
    AgentPriority,
    AgentStatus,
    SpawnRequest,
    new_agent_id,
)


_TYPE_MAP: Dict[str, Type[BaseAgent]] = {
    "conversation": ConversationBridgeAgent,
    "memory": MemoryAgent,
    "tool": ToolWorkerAgent,
    "specialist": SpecialistWorkerAgent,
    "research": SpecialistWorkerAgent,
    "planner": SpecialistWorkerAgent,
    "self_development": SelfDevelopmentAgent,
    "proactive": SpecialistWorkerAgent,
}

_DEFAULT_PERMS: Dict[str, list] = {
    "conversation": [PERM_READ_MEMORY, PERM_REQUEST_TOOLS, PERM_REQUEST_AGENTS],
    "memory": [PERM_READ_MEMORY, PERM_WRITE_MEMORY],
    "tool": [PERM_TOOL_INVOKE],
    "specialist": [PERM_REQUEST_AGENTS],
    "research": [PERM_REQUEST_AGENTS, PERM_READ_MEMORY],
    "planner": [PERM_REQUEST_AGENTS, PERM_READ_MEMORY],
    "self_development": [
        PERM_REPO_WRITE,
        PERM_RUN_TESTS,
        PERM_CHECKPOINT,
        PERM_ROLLBACK,
        PERM_REQUEST_AGENTS,
        PERM_READ_MEMORY,
    ],
    "proactive": [PERM_READ_MEMORY, PERM_REQUEST_AGENTS],
    "gui": [PERM_GUI_CONTROL, PERM_READ_MEMORY],
}


class AgentFactory:
    """Creates agent instances with scoped identity — orchestrator remains authoritative."""

    def __init__(self, on_event: Optional[Callable[[str, dict], None]] = None) -> None:
        self.on_event = on_event
        self._custom: Dict[str, Type[BaseAgent]] = {}

    def register_type(self, agent_type: str, cls: Type[BaseAgent]) -> None:
        self._custom[str(agent_type)] = cls

    def resolve_class(self, agent_type: str) -> Type[BaseAgent]:
        key = str(agent_type or "specialist")
        return self._custom.get(key) or _TYPE_MAP.get(key) or SpecialistWorkerAgent

    def build_identity(
        self,
        req: SpawnRequest,
        *,
        parent_depth: int = 0,
        persistent: bool = False,
        model: str = "",
    ) -> AgentIdentity:
        agent_type = str(req.agent_type or "specialist")
        perms = list(req.capabilities) if req.capabilities else list(
            _DEFAULT_PERMS.get(agent_type, [PERM_REQUEST_AGENTS])
        )
        return AgentIdentity(
            agent_id=new_agent_id(agent_type),
            agent_type=agent_type,
            role=str(req.role or agent_type),
            status=AgentStatus.INITIALIZING.value,
            parent_agent_id=str(req.parent_agent_id or ""),
            capabilities=list(req.capabilities or [agent_type, req.role]),
            permissions=perms,
            task=str(req.task or ""),
            priority=str(req.priority or AgentPriority.NORMAL.value),
            model=str(model or req.model or ""),
            persistent=bool(persistent),
            spawn_depth=int(parent_depth) + (1 if req.parent_agent_id else 0),
            metadata={"justification": str(req.justification or "")[:300]},
        )

    def spawn(
        self,
        req: SpawnRequest,
        *,
        orchestrator: Any = None,
        parent_depth: int = 0,
        persistent: bool = False,
        model: str = "",
        scoped_context: Optional[Dict[str, Any]] = None,
    ) -> BaseAgent:
        identity = self.build_identity(
            req, parent_depth=parent_depth, persistent=persistent, model=model
        )
        cls = self.resolve_class(identity.agent_type)
        agent = cls(
            identity,
            orchestrator=orchestrator,
            on_event=self.on_event,
        )
        # Scoped context only — never dump full parent context.
        agent.identity.metadata["scoped_context_keys"] = sorted(
            list((scoped_context or {}).keys())
        )
        agent.start()
        if callable(self.on_event):
            self.on_event(
                "agent.created",
                {
                    "agent_id": identity.agent_id,
                    "agent_type": identity.agent_type,
                    "role": identity.role,
                    "parent_agent_id": identity.parent_agent_id,
                    "persistent": identity.persistent,
                    "justification": req.justification,
                },
            )
        return agent

    def clone_agent(
        self,
        source: BaseAgent,
        *,
        orchestrator: Any = None,
        role: str = "",
        task: str = "",
    ) -> BaseAgent:
        req = SpawnRequest(
            agent_type=source.identity.agent_type,
            role=role or source.identity.role,
            task=task or source.identity.task,
            parent_agent_id=source.identity.agent_id,
            priority=source.identity.priority,
            justification=f"clone of {source.identity.agent_id}",
            capabilities=list(source.identity.capabilities),
            model=source.identity.model,
        )
        return self.spawn(
            req,
            orchestrator=orchestrator or source.orchestrator,
            parent_depth=source.identity.spawn_depth,
            persistent=False,
            model=source.identity.model,
        )

    def specialize_agent(
        self,
        source: BaseAgent,
        role: str,
        task: str,
        *,
        orchestrator: Any = None,
    ) -> BaseAgent:
        req = SpawnRequest(
            agent_type="specialist",
            role=role,
            task=task,
            parent_agent_id=source.identity.agent_id,
            priority=source.identity.priority,
            justification=f"specialize {source.identity.agent_type} → {role}",
        )
        return self.spawn(
            req,
            orchestrator=orchestrator or source.orchestrator,
            parent_depth=source.identity.spawn_depth,
            persistent=False,
        )
