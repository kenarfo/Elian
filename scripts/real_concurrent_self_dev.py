# ==========================
# Real concurrent: conversation + one SelfDevelopmentEngine cycle
# ==========================
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from core.multi_agent.memory_scopes import ScopedMemoryStore
    from core.multi_agent.orchestrator import ElianOrchestrator, set_orchestrator
    from core.multi_agent.self_model import SelfModel
    from core.multi_agent.types import AgentPriority, SpawnRequest
    from core.self_improve.self_development import SelfDevelopmentEngine

    tmp = ROOT / "Data" / "multi_agent" / "_concurrent_verify"
    tmp.mkdir(parents=True, exist_ok=True)
    orch = ElianOrchestrator(
        language="en-US",
        state_path=tmp / "orch.json",
        max_concurrent_agents=3,
    )
    orch.self_model = SelfModel(path=tmp / "sm.json")
    orch.memory_scopes = ScopedMemoryStore(path=tmp / "mem.json")
    cm = MagicMock()
    cm.active = True
    cm.handle.return_value = MagicMock(
        text="Conversation remained available.", source="chat"
    )
    orch.conversation_manager = cm
    orch.start(start_self_development=False)
    set_orchestrator(orch)

    results = {}

    def run_dev():
        agent = orch._create_agent(
            SpawnRequest(
                agent_type="self_development",
                role="self_developer",
                task="one_cycle",
                parent_agent_id="",
                priority=AgentPriority.BACKGROUND.value,
                justification="real concurrent validation",
            ),
            persistent=True,
        )
        # Real engine — one cycle, skip agent-smoke for gate speed.
        t0 = time.time()
        engine = SelfDevelopmentEngine(
            max_cycles=1,
            skip_agent_smoke=True,
            include_runtime=True,
            include_repo=True,
            sleep_between_cycles_s=0.0,
        )
        orch.self_model.set_development(active=True, phase="OBSERVE", cycle=0)
        code = engine.run()
        st = engine.state.status_summary()
        orch.self_model.set_development(
            active=False,
            cycle=int(st.get("current_cycle") or 0),
            phase=str(st.get("current_phase") or ""),
            candidate=str(st.get("current_candidate") or ""),
            last_improvement=str(st.get("last_successful_improvement") or ""),
            last_rollback=str(st.get("last_failed_improvement") or ""),
        )
        results["dev"] = {
            "code": code,
            "status": st,
            "duration_s": round(time.time() - t0, 2),
            "agent": agent.identity.agent_id,
        }

    def run_user():
        # Overlap with development.
        time.sleep(0.3)
        orch.resources.note_interactive(True)
        try:
            for _ in range(3):
                r = orch.handle_user_text("What are you doing?")
                results.setdefault("user_turns", []).append(
                    {"status": r.status, "summary": r.summary[:120]}
                )
                time.sleep(0.4)
        finally:
            orch.resources.note_interactive(False)

    td = threading.Thread(target=run_dev, name="real-self-dev")
    tu = threading.Thread(target=run_user, name="user-conv")
    td.start()
    tu.start()
    tu.join(timeout=600)
    td.join(timeout=600)

    print("USER_TURNS", results.get("user_turns"))
    print("DEV", results.get("dev"))
    print("DESCRIBE", orch.describe_self()[:400])
    user_ok = all(t["status"] == "ok" for t in results.get("user_turns") or [])
    dev_ok = results.get("dev") is not None
    orch.stop()
    set_orchestrator(None)
    print("RESULT", "PASS" if user_ok and dev_ok else "FAIL")
    return 0 if user_ok and dev_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
