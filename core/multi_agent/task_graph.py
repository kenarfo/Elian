# ==========================
# ELIAN — Lightweight task DAG
# ==========================
from __future__ import annotations

import threading
import time
import uuid
from typing import Dict, List, Optional, Set

from .types import AgentResult, TaskNode, new_task_id


class TaskGraph:
    """Dependency-aware task graph for parallel agent work."""

    def __init__(self, graph_id: str = "") -> None:
        self.graph_id = graph_id or f"graph_{uuid.uuid4().hex[:8]}"
        self._lock = threading.RLock()
        self._nodes: Dict[str, TaskNode] = {}

    def add(
        self,
        name: str,
        *,
        agent_type: str = "",
        depends_on: Optional[List[str]] = None,
        priority: str = "NORMAL",
        task_id: str = "",
    ) -> TaskNode:
        node = TaskNode(
            task_id=task_id or new_task_id(),
            name=name,
            agent_type=agent_type,
            depends_on=list(depends_on or []),
            priority=priority,
        )
        with self._lock:
            self._nodes[node.task_id] = node
            self._refresh_ready_unlocked()
        return node

    def mark_running(self, task_id: str, agent_id: str = "") -> None:
        with self._lock:
            node = self._nodes.get(task_id)
            if not node:
                return
            node.status = "running"
            if agent_id:
                node.agent_id = agent_id

    def mark_done(self, task_id: str, result: AgentResult) -> None:
        with self._lock:
            node = self._nodes.get(task_id)
            if not node:
                return
            node.status = "done" if result.status == "ok" else "failed"
            node.result = result
            self._refresh_ready_unlocked()

    def cancel(self, task_id: str) -> None:
        with self._lock:
            node = self._nodes.get(task_id)
            if not node:
                return
            if node.status in ("done", "failed"):
                return
            node.status = "cancelled"
            self._refresh_ready_unlocked()

    def ready_tasks(self) -> List[TaskNode]:
        with self._lock:
            self._refresh_ready_unlocked()
            return [n for n in self._nodes.values() if n.status == "ready"]

    def all_terminal(self) -> bool:
        with self._lock:
            for node in self._nodes.values():
                if node.status not in ("done", "failed", "cancelled"):
                    return False
            return True

    def results(self) -> List[AgentResult]:
        with self._lock:
            out = []
            for node in self._nodes.values():
                if node.result is not None:
                    out.append(node.result)
            return out

    def snapshot(self) -> Dict:
        with self._lock:
            return {
                "graph_id": self.graph_id,
                "nodes": [n.to_dict() for n in self._nodes.values()],
            }

    def _refresh_ready_unlocked(self) -> None:
        done: Set[str] = {
            tid
            for tid, node in self._nodes.items()
            if node.status in ("done", "cancelled")
        }
        failed_deps: Set[str] = {
            tid for tid, node in self._nodes.items() if node.status == "failed"
        }
        for node in self._nodes.values():
            if node.status not in ("pending", "ready"):
                continue
            if any(dep in failed_deps for dep in node.depends_on):
                node.status = "cancelled"
                continue
            if all(dep in done for dep in node.depends_on):
                node.status = "ready"
            else:
                node.status = "pending"
