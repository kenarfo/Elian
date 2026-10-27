# ==========================
# ELIAN — Agent registry
# ==========================
from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

from .types import AgentIdentity, AgentStatus


class AgentRegistry:
    """In-memory registry of live agent identities (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._agents: Dict[str, AgentIdentity] = {}

    def register(self, identity: AgentIdentity) -> AgentIdentity:
        with self._lock:
            self._agents[identity.agent_id] = identity
            return identity

    def get(self, agent_id: str) -> Optional[AgentIdentity]:
        with self._lock:
            return self._agents.get(agent_id)

    def update_status(self, agent_id: str, status: str, *, task: str = "") -> None:
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return
            agent.status = status
            agent.last_active = time.time()
            if task:
                agent.task = task

    def remove(self, agent_id: str) -> bool:
        with self._lock:
            return self._agents.pop(agent_id, None) is not None

    def list_all(self) -> List[AgentIdentity]:
        with self._lock:
            return list(self._agents.values())

    def list_by_type(self, agent_type: str) -> List[AgentIdentity]:
        with self._lock:
            return [a for a in self._agents.values() if a.agent_type == agent_type]

    def children_of(self, parent_id: str) -> List[AgentIdentity]:
        with self._lock:
            return [a for a in self._agents.values() if a.parent_agent_id == parent_id]

    def find_idle(self, agent_type: str, *, role: str = "") -> Optional[AgentIdentity]:
        with self._lock:
            for agent in self._agents.values():
                if agent.agent_type != agent_type:
                    continue
                if role and agent.role != role:
                    continue
                if agent.status == AgentStatus.IDLE.value:
                    return agent
            return None

    def count(self) -> int:
        with self._lock:
            return len(self._agents)

    def active_count(self) -> int:
        busy = {
            AgentStatus.WORKING.value,
            AgentStatus.WAITING.value,
            AgentStatus.INITIALIZING.value,
            AgentStatus.BLOCKED.value,
        }
        with self._lock:
            return sum(1 for a in self._agents.values() if a.status in busy)

    def snapshot(self) -> List[dict]:
        with self._lock:
            return [a.to_dict() for a in self._agents.values()]
