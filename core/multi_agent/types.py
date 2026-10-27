# ==========================
# ELIAN — Multi-agent types
# ==========================
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class AgentStatus(str, Enum):
    CREATED = "CREATED"
    INITIALIZING = "INITIALIZING"
    IDLE = "IDLE"
    WORKING = "WORKING"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    EXPIRED = "EXPIRED"


class AgentPriority(str, Enum):
    CRITICAL = "CRITICAL"  # interactive user
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    BACKGROUND = "BACKGROUND"  # self-development
    LOW = "LOW"


class MemoryScope(str, Enum):
    GLOBAL = "GLOBAL"
    USER = "USER"
    SESSION = "SESSION"
    TASK = "TASK"
    AGENT = "AGENT"
    EPHEMERAL = "EPHEMERAL"


class SystemMode(str, Enum):
    NORMAL = "NORMAL"
    DEVELOPMENT = "DEVELOPMENT"
    MAINTENANCE = "MAINTENANCE"
    DEGRADED = "DEGRADED"


class HealthState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    OFFLINE = "OFFLINE"


class VoiceUIState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    WORKING = "WORKING"
    WARNING = "WARNING"
    ERROR = "ERROR"
    OFFLINE = "OFFLINE"


# Capability / permission tokens
PERM_READ_MEMORY = "read_memory"
PERM_WRITE_MEMORY = "write_memory"
PERM_REQUEST_TOOLS = "request_tools"
PERM_REQUEST_AGENTS = "request_agents"
PERM_TOOL_INVOKE = "tool_invoke"
PERM_REPO_WRITE = "repo_write"
PERM_RUN_TESTS = "run_tests"
PERM_CHECKPOINT = "checkpoint"
PERM_ROLLBACK = "rollback"
PERM_GUI_CONTROL = "gui_control"


@dataclass
class AgentIdentity:
    agent_id: str
    agent_type: str
    role: str
    status: str = AgentStatus.CREATED.value
    parent_agent_id: str = ""
    capabilities: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    task: str = ""
    priority: str = AgentPriority.NORMAL.value
    model: str = ""
    persistent: bool = False
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    health: str = HealthState.HEALTHY.value
    resource_usage: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    spawn_depth: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "role": self.role,
            "status": self.status,
            "parent_agent_id": self.parent_agent_id,
            "capabilities": list(self.capabilities),
            "permissions": list(self.permissions),
            "task": self.task,
            "priority": self.priority,
            "model": self.model,
            "persistent": self.persistent,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "health": self.health,
            "resource_usage": dict(self.resource_usage),
            "metadata": dict(self.metadata),
            "spawn_depth": self.spawn_depth,
        }

    @classmethod
    def from_dict(cls, row: Any) -> Optional["AgentIdentity"]:
        if not isinstance(row, dict) or not row.get("agent_id"):
            return None
        return cls(
            agent_id=str(row["agent_id"]),
            agent_type=str(row.get("agent_type") or "worker"),
            role=str(row.get("role") or ""),
            status=str(row.get("status") or AgentStatus.CREATED.value),
            parent_agent_id=str(row.get("parent_agent_id") or ""),
            capabilities=[str(x) for x in row.get("capabilities") or []],
            permissions=[str(x) for x in row.get("permissions") or []],
            task=str(row.get("task") or ""),
            priority=str(row.get("priority") or AgentPriority.NORMAL.value),
            model=str(row.get("model") or ""),
            persistent=bool(row.get("persistent")),
            created_at=float(row.get("created_at") or time.time()),
            last_active=float(row.get("last_active") or time.time()),
            health=str(row.get("health") or HealthState.HEALTHY.value),
            resource_usage={str(k): float(v) for k, v in (row.get("resource_usage") or {}).items()},
            metadata=dict(row.get("metadata") or {}),
            spawn_depth=int(row.get("spawn_depth") or 0),
        )


@dataclass
class AgentResult:
    task_id: str
    status: str  # ok | failed | cancelled | queued
    summary: str
    evidence: List[str] = field(default_factory=list)
    confidence: float = 0.0
    errors: List[str] = field(default_factory=list)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    next_action: str = ""
    source_agent: str = ""
    target_agent: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "summary": self.summary,
            "evidence": list(self.evidence),
            "confidence": self.confidence,
            "errors": list(self.errors),
            "artifacts": dict(self.artifacts),
            "next_action": self.next_action,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
        }


@dataclass
class SpawnRequest:
    agent_type: str
    role: str
    task: str
    parent_agent_id: str
    priority: str = AgentPriority.NORMAL.value
    capabilities: List[str] = field(default_factory=list)
    model: str = ""
    justification: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: f"spawn_{uuid.uuid4().hex[:10]}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "agent_type": self.agent_type,
            "role": self.role,
            "task": self.task,
            "parent_agent_id": self.parent_agent_id,
            "priority": self.priority,
            "capabilities": list(self.capabilities),
            "model": self.model,
            "justification": self.justification,
            "context": dict(self.context),
        }


@dataclass
class TaskNode:
    task_id: str
    name: str
    agent_type: str = ""
    agent_id: str = ""
    status: str = "pending"  # pending | ready | running | done | failed | cancelled
    depends_on: List[str] = field(default_factory=list)
    result: Optional[AgentResult] = None
    priority: str = AgentPriority.NORMAL.value
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "agent_type": self.agent_type,
            "agent_id": self.agent_id,
            "status": self.status,
            "depends_on": list(self.depends_on),
            "result": self.result.to_dict() if self.result else None,
            "priority": self.priority,
            "created_at": self.created_at,
        }


def new_agent_id(agent_type: str) -> str:
    slug = "".join(ch if ch.isalnum() else "_" for ch in (agent_type or "agent").lower())
    return f"{slug}_{uuid.uuid4().hex[:8]}"


def new_task_id(prefix: str = "task") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"
