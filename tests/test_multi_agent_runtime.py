# ==========================
# ELIAN — multi-agent runtime tests
# ==========================
from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from core.multi_agent.memory_scopes import ScopedMemoryStore
from core.multi_agent.orchestrator import ElianOrchestrator, set_orchestrator
from core.multi_agent.self_model import SelfModel
from core.multi_agent.task_graph import TaskGraph
from core.multi_agent.types import (
    AgentPriority,
    AgentResult,
    AgentStatus,
    SpawnRequest,
)
from voice.tts_engine import FishAudioTTS, FallbackTTS, RoutedTTS, reset_tts_engine_for_tests


class MultiAgentRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.orch = ElianOrchestrator(
            max_agents_total=8,
            max_concurrent_agents=4,
            max_children_per_agent=3,
            max_spawn_depth=2,
            max_agents_per_task=4,
            language="en-US",
            state_path=root / "orch.json",
        )
        self.orch.self_model = SelfModel(path=root / "self_model.json")
        self.orch.memory_scopes = ScopedMemoryStore(path=root / "memory.json")
        self.orch.start(start_self_development=False)
        set_orchestrator(self.orch)

    def tearDown(self) -> None:
        try:
            self.orch.stop()
        except Exception:
            pass
        set_orchestrator(None)
        self.tmp.cleanup()

    def test_core_agents_registered(self) -> None:
        types = {a.agent_type for a in self.orch.registry.list_all()}
        self.assertIn("conversation", types)
        self.assertIn("memory", types)

    def test_dynamic_spawn_and_cleanup(self) -> None:
        parent = self.orch._first_of_type("conversation")
        self.assertIsNotNone(parent)
        req = SpawnRequest(
            agent_type="specialist",
            role="product_comparison",
            task="compare three products",
            parent_agent_id=parent.identity.agent_id,
            priority=AgentPriority.NORMAL.value,
            justification="Need comparison specialist",
            context={"payload": {"products": ["a", "b", "c"]}},
        )
        before = self.orch.registry.count()
        result = self.orch.spawn_and_run(req)
        self.assertEqual(result.status, "ok")
        self.assertIn("product_comparison", result.summary)
        # Temporary worker cleaned up after completion.
        self.assertLessEqual(self.orch.registry.count(), before + 1)

    def test_reuse_idle_specialist(self) -> None:
        parent = self.orch._first_of_type("conversation")
        req = SpawnRequest(
            agent_type="specialist",
            role="web_research",
            task="noop keep idle",
            parent_agent_id=parent.identity.agent_id,
            justification="create researcher",
        )
        agent = self.orch._create_agent(req, persistent=True)
        agent.identity.status = AgentStatus.IDLE.value
        self.orch.registry.update_status(agent.identity.agent_id, AgentStatus.IDLE.value)
        reuse = self.orch.spawn_agent(
            SpawnRequest(
                agent_type="specialist",
                role="web_research",
                task="again",
                parent_agent_id=parent.identity.agent_id,
                justification="should reuse",
            )
        )
        self.assertEqual(reuse.status, "ok")
        self.assertTrue(reuse.artifacts.get("reused"))

    def test_spawn_depth_limit(self) -> None:
        parent = self.orch._first_of_type("conversation")
        # Manually create a deep parent.
        deep = self.orch._create_agent(
            SpawnRequest(
                agent_type="specialist",
                role="mid",
                task="",
                parent_agent_id=parent.identity.agent_id,
                justification="mid",
            ),
            persistent=True,
        )
        deep.identity.spawn_depth = 2
        self.orch.registry.register(deep.identity)
        res = self.orch.spawn_agent(
            SpawnRequest(
                agent_type="specialist",
                role="too_deep",
                task="x",
                parent_agent_id=deep.identity.agent_id,
                justification="should reject",
            )
        )
        self.assertEqual(res.status, "failed")
        self.assertIn("max_spawn_depth", res.errors[0] if res.errors else res.summary)

    def test_max_agents_queues(self) -> None:
        self.orch.max_agents_total = self.orch.registry.count()
        parent = self.orch._first_of_type("conversation")
        res = self.orch.spawn_agent(
            SpawnRequest(
                agent_type="specialist",
                role="overflow",
                task="x",
                parent_agent_id=parent.identity.agent_id,
                justification="overflow",
            )
        )
        self.assertEqual(res.status, "queued")

    def test_parallel_task_graph(self) -> None:
        parent = self.orch._first_of_type("conversation")
        results = self.orch.run_task_graph(
            [
                {
                    "name": "a",
                    "agent_type": "specialist",
                    "role": "research",
                    "task": "research",
                    "justification": "parallel a",
                },
                {
                    "name": "b",
                    "agent_type": "specialist",
                    "role": "availability",
                    "task": "stock",
                    "justification": "parallel b",
                },
            ],
            parent_agent_id=parent.identity.agent_id,
        )
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.status == "ok" for r in results))

    def test_task_graph_dependencies(self) -> None:
        g = TaskGraph()
        a = g.add("A")
        b = g.add("B", depends_on=[a.task_id])
        ready = g.ready_tasks()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].task_id, a.task_id)
        g.mark_running(a.task_id)
        g.mark_done(a.task_id, AgentResult(task_id=a.task_id, status="ok", summary="A"))
        ready2 = g.ready_tasks()
        self.assertEqual(len(ready2), 1)
        self.assertEqual(ready2[0].task_id, b.task_id)

    def test_self_model_tracks_agents(self) -> None:
        desc = self.orch.describe_self()
        self.assertIn("I am Elian", desc)
        snap = self.orch.self_model.snapshot()
        ids = {a["agent_id"] for a in snap["active_agents"]}
        live = {a.agent_id for a in self.orch.registry.list_all()}
        self.assertEqual(ids, live)

    def test_memory_scopes_isolated(self) -> None:
        store = self.orch.memory_scopes
        store.put("SESSION", "topic", "calendar")
        store.put("TASK", "topic", "research")
        self.assertEqual(store.get("SESSION", "topic"), "calendar")
        self.assertEqual(store.get("TASK", "topic"), "research")

    def test_concurrency_isolation(self) -> None:
        """Conversation + memory + specialist + failing worker coexist."""
        outcomes = {}

        def mem_work():
            m = self.orch._first_of_type("memory")
            outcomes["memory"] = m.handle_task(
                "store",
                context={"action": "store", "scope": "SESSION", "key": "k", "value": 1},
            )

        def specialist_work():
            parent = self.orch._first_of_type("conversation")
            outcomes["spec"] = self.orch.spawn_and_run(
                SpawnRequest(
                    agent_type="specialist",
                    role="worker",
                    task="parallel work",
                    parent_agent_id=parent.identity.agent_id,
                    justification="concurrency test",
                )
            )

        def failing_work():
            parent = self.orch._first_of_type("conversation")
            agent = self.orch._create_agent(
                SpawnRequest(
                    agent_type="specialist",
                    role="bomber",
                    task="fail",
                    parent_agent_id=parent.identity.agent_id,
                    justification="isolation",
                ),
                persistent=True,
            )

            def boom(task, *, context, task_id):
                raise RuntimeError("boom")

            agent.execute = boom  # type: ignore
            outcomes["fail"] = agent.handle_task("fail")

        def conv_work():
            cm = MagicMock()
            cm.active = True
            cm.handle.return_value = MagicMock(text="Hello from Elian", source="chat")
            self.orch.conversation_manager = cm
            conv = self.orch._first_of_type("conversation")
            outcomes["conv"] = conv.handle_task(
                "hi", context={"conversation_manager": cm}
            )

        threads = [
            threading.Thread(target=mem_work),
            threading.Thread(target=specialist_work),
            threading.Thread(target=failing_work),
            threading.Thread(target=conv_work),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(outcomes["memory"].status, "ok")
        self.assertEqual(outcomes["spec"].status, "ok")
        self.assertEqual(outcomes["fail"].status, "failed")
        self.assertEqual(outcomes["conv"].status, "ok")
        # Failed worker must not stop conversation agent.
        conv = self.orch._first_of_type("conversation")
        self.assertIsNotNone(conv)
        self.assertNotEqual(conv.identity.status, AgentStatus.STOPPED.value)

    def test_resource_interactive_preempts_background(self) -> None:
        rm = self.orch.resources
        self.assertTrue(
            rm.acquire("bg", kind="gpu", priority=AgentPriority.BACKGROUND.value)
        )
        rm.note_interactive(True)
        self.assertFalse(
            rm.can_acquire(kind="gpu", priority=AgentPriority.BACKGROUND.value)
        )
        self.assertTrue(
            rm.acquire("user", kind="gpu", priority=AgentPriority.CRITICAL.value)
        )
        rm.release_all("user")
        rm.note_interactive(False)

    def test_self_dev_spawns_analyst_worker(self) -> None:
        """Self-Development Agent can spawn a specialist through orchestrator."""
        with patch(
            "core.self_improve.self_development.SelfDevelopmentEngine"
        ) as Engine:
            inst = Engine.return_value
            inst.state.load.return_value = {"current_cycle": 31}
            inst.state.status_summary.return_value = {
                "current_cycle": 31,
                "current_phase": "TEST",
                "current_candidate": "planner_recovery",
                "last_result": "KEEP",
                "last_successful_improvement": "planner_recovery",
                "last_failed_improvement": "",
            }
            inst.run.return_value = 0
            agent = self.orch._create_agent(
                SpawnRequest(
                    agent_type="self_development",
                    role="self_developer",
                    task="cycle",
                    parent_agent_id="",
                    priority=AgentPriority.BACKGROUND.value,
                    justification="test",
                ),
                persistent=True,
            )
            result = agent.handle_task(
                "cycle",
                context={
                    "max_cycles": 1,
                    "skip_agent_smoke": True,
                    "use_analyst": True,
                },
            )
            self.assertEqual(result.status, "ok")
            self.assertIn("codebase_analyst", result.summary.lower() + str(result.artifacts))
            sm = self.orch.self_model.snapshot()
            self.assertEqual(sm["development"]["cycle"], 31)

    def test_control_center_snapshot_endpoint(self) -> None:
        from core.control_center.server import ControlCenterState, maybe_start_control_center
        import urllib.request

        # Bind ephemeral port.
        with patch("Config.get_config_value", side_effect=lambda s, k, default=None: {
            ("gui", "enabled"): True,
            ("gui", "host"): "127.0.0.1",
            ("gui", "port"): 18787,
        }.get((s, k), default)):
            host = maybe_start_control_center(orchestrator=self.orch, port=18787)
            self.assertIsNotNone(host)
            try:
                time.sleep(0.2)
                with urllib.request.urlopen("http://127.0.0.1:18787/api/snapshot", timeout=3) as resp:
                    body = resp.read().decode("utf-8")
                self.assertIn("self_model", body)
                self.assertIn("Elian", body)
            finally:
                host.stop()


class TTSEngineTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_tts_engine_for_tests()

    def test_fish_absent_falls_back(self) -> None:
        routed = RoutedTTS(prefer_fish=True)
        st = routed.health_check()
        self.assertIn(st.extras.get("active"), ("english_piper", "fallback_piper"))
        self.assertEqual(st.extras.get("fish_readiness"), "UNAVAILABLE")
        self.assertFalse(FishAudioTTS.sdk_available() and st.extras.get("fish", {}).get("healthy"))

    def test_fallback_stop_safe(self) -> None:
        fb = FallbackTTS()
        fb.stop()  # must not raise


class SelfModelDescribeTests(unittest.TestCase):
    def test_describe_from_real_state(self) -> None:
        with TemporaryDirectory() as tmp:
            sm = SelfModel(path=Path(tmp) / "sm.json")
            sm.set_agents(
                [
                    {
                        "agent_id": "conversation_1",
                        "agent_type": "conversation",
                        "status": "WORKING",
                        "task": "user chat",
                    }
                ]
            )
            sm.set_development(active=True, cycle=31, phase="TEST", candidate="x")
            sm.add_limitation("Calendar OAuth unavailable")
            text = sm.describe()
            self.assertIn("1 working", text)
            self.assertIn("cycle 31", text)
            self.assertIn("Calendar OAuth unavailable", text)


if __name__ == "__main__":
    unittest.main()
