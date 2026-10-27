# ==========================
# Demo: user conversation concurrent with self-development worker
# ==========================
from __future__ import annotations

import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from core.multi_agent.memory_scopes import ScopedMemoryStore
from core.multi_agent.orchestrator import ElianOrchestrator, set_orchestrator
from core.multi_agent.self_model import SelfModel
from core.multi_agent.types import AgentPriority, SpawnRequest


def main() -> int:
    tmp = TemporaryDirectory()
    root = Path(tmp.name)
    orch = ElianOrchestrator(
        language="en-US",
        state_path=root / "o.json",
        max_agents_total=12,
    )
    orch.self_model = SelfModel(path=root / "sm.json")
    orch.memory_scopes = ScopedMemoryStore(path=root / "m.json")
    cm = MagicMock()
    cm.active = True
    cm.handle.return_value = MagicMock(
        text="Calendar is clear this afternoon.", source="chat"
    )
    orch.conversation_manager = cm
    orch.start(start_self_development=False)
    set_orchestrator(orch)

    flags: dict = {"user": None, "dev": None}

    def user() -> None:
        t0 = time.time()
        result = orch.handle_user_text("What's on my calendar?")
        flags["user"] = (result.status, result.summary, time.time() - t0)

    def bg() -> None:
        with patch("core.self_improve.self_development.SelfDevelopmentEngine") as eng:
            inst = eng.return_value
            inst.state.load.return_value = {"current_cycle": 31}
            inst.state.status_summary.return_value = {
                "current_cycle": 31,
                "current_phase": "TEST",
                "current_candidate": "planner_recovery",
                "last_result": "KEEP",
                "last_successful_improvement": "planner_recovery",
                "last_failed_improvement": "",
            }

            def slow_run() -> int:
                time.sleep(0.4)
                return 0

            inst.run.side_effect = slow_run
            agent = orch._create_agent(
                SpawnRequest(
                    agent_type="self_development",
                    role="self_developer",
                    task="cycle",
                    parent_agent_id="",
                    priority=AgentPriority.BACKGROUND.value,
                    justification="bg",
                ),
                persistent=True,
            )
            t0 = time.time()
            result = agent.handle_task(
                "cycle",
                context={
                    "max_cycles": 1,
                    "use_analyst": True,
                    "skip_agent_smoke": True,
                },
            )
            flags["dev"] = (result.status, result.summary[:160], time.time() - t0)

    td = threading.Thread(target=bg)
    tu = threading.Thread(target=user)
    td.start()
    time.sleep(0.05)
    tu.start()
    tu.join()
    td.join()

    print("USER", flags["user"])
    print("DEV", flags["dev"])
    print("DESCRIBE", orch.describe_self())
    print("AGENTS", len(orch.registry.list_all()))
    orch.stop()
    set_orchestrator(None)
    tmp.cleanup()
    print("CONCURRENCY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
