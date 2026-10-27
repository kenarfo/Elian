# ==========================
# ELIAN — P0–P3 runtime completion tests
# ==========================
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from core.multi_agent.hardware import HardwareProbe
from core.multi_agent.memory_scopes import ScopedMemoryStore
from core.multi_agent.orchestrator import ElianOrchestrator, set_orchestrator
from core.multi_agent.resource_manager import ResourceManager
from core.multi_agent.self_model import SelfModel
from core.multi_agent.types import AgentPriority, SpawnRequest
from voice.tts_engine import (
    measure_english_synthesis,
    reset_tts_engine_for_tests,
    get_tts_engine,
)


class HardwareProfileTests(unittest.TestCase):
    def test_vram_not_confused_with_system_ram(self) -> None:
        hw = HardwareProbe().refresh()
        self.assertGreater(hw.ram_total_gb, 0)
        if hw.cuda_available:
            self.assertGreater(hw.vram_total_gb, 0)
            # System RAM (~32 GB) must not be reported as VRAM.
            self.assertLess(hw.vram_total_gb, hw.ram_total_gb)
            self.assertLess(hw.vram_total_gb, 20.0)
            self.assertIn("NVIDIA", hw.gpu_name.upper())
            self.assertTrue(any("system_ram_gb" in n for n in hw.notes))


class ResourceSchedulingTests(unittest.TestCase):
    def test_interactive_yields_background(self) -> None:
        rm = ResourceManager(max_gpu_slots=1, max_cpu_slots=2)
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

    def test_limits_five_tool_agents(self) -> None:
        rm = ResourceManager(max_gpu_slots=1, max_cpu_slots=2)
        rm.max_concurrent_normal = 2
        got = 0
        for i in range(5):
            if rm.acquire(
                f"tool{i}",
                kind="gpu",
                priority=AgentPriority.NORMAL.value,
                timeout_s=0.0,
            ):
                got += 1
        self.assertLessEqual(got, 2)


class EnglishTTSTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_tts_engine_for_tests()

    def test_english_synthesis_and_fish_unavailable(self) -> None:
        reset_tts_engine_for_tests()
        result = measure_english_synthesis("Elian speaking English.")
        self.assertTrue(result["ok"], msg=str(result))
        self.assertGreater(result["bytes"], 1000)
        self.assertEqual(result["fish_readiness"], "UNAVAILABLE")
        self.assertEqual(result["fallback_readiness"], "READY")
        self.assertIn(result["active_engine"], ("english_piper", "fallback_piper"))
        self.assertGreater(result["generation_ms"], 0)

    def test_health_truthful(self) -> None:
        st = get_tts_engine().health_check()
        self.assertEqual(st.extras.get("fish_readiness"), "UNAVAILABLE")
        self.assertEqual(st.extras.get("fallback_readiness"), "READY")
        self.assertFalse((st.extras.get("fish") or {}).get("healthy"))


class BackgroundSelfDevStartupTests(unittest.TestCase):
    def test_start_includes_persistent_self_dev(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            orch = ElianOrchestrator(
                language="en-US",
                state_path=root / "o.json",
                max_agents_total=12,
            )
            orch.self_model = SelfModel(path=root / "sm.json")
            orch.memory_scopes = ScopedMemoryStore(path=root / "m.json")
            with patch.object(
                type(orch),
                "start_background_self_development",
                autospec=True,
            ) as start_bg:
                orch.start(start_self_development=True, self_dev_max_cycles=1)
                start_bg.assert_called_once()
            types = {a.agent_type for a in orch.registry.list_all()}
            self.assertIn("conversation", types)
            self.assertIn("memory", types)
            self.assertIn("self_development", types)
            orch.stop()

    def test_config_default_disables_background(self) -> None:
        from Config import config

        ma = config.get("multi_agent") or {}
        self.assertFalse(ma.get("background_self_development"))
        auto = (config.get("self_improve") or {}).get("autonomous") or {}
        self.assertFalse(auto.get("enabled"))


class ConcurrentRuntimeGuiTests(unittest.TestCase):
    def test_gui_snapshot_shows_live_fields(self) -> None:
        from core.control_center.server import maybe_start_control_center

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            orch = ElianOrchestrator(
                language="en-US",
                state_path=root / "o.json",
            )
            orch.self_model = SelfModel(path=root / "sm.json")
            orch.memory_scopes = ScopedMemoryStore(path=root / "m.json")
            cm = MagicMock()
            cm.active = True
            cm.handle.return_value = MagicMock(text="Hello", source="chat")
            orch.conversation_manager = cm
            orch.start(start_self_development=False)
            set_orchestrator(orch)
            with patch(
                "Config.get_config_value",
                side_effect=lambda s, k, default=None: {
                    ("gui", "enabled"): True,
                    ("gui", "host"): "127.0.0.1",
                    ("gui", "port"): 18788,
                }.get((s, k), default),
            ):
                host = maybe_start_control_center(orchestrator=orch, port=18788)
                self.assertIsNotNone(host)
                try:
                    time.sleep(0.15)
                    with urllib.request.urlopen(
                        "http://127.0.0.1:18788/api/snapshot", timeout=3
                    ) as resp:
                        body = json.loads(resp.read().decode("utf-8"))
                    self.assertIn("live", body)
                    self.assertIn("tts", body["live"])
                    self.assertIn("hardware", body["live"])
                    self.assertIn("self_development", body["live"])
                    self.assertIn("self_model", body)
                finally:
                    host.stop()
                    orch.stop()
                    set_orchestrator(None)


class ConcurrentConversationSelfDevTests(unittest.TestCase):
    def test_user_while_self_dev_background(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            orch = ElianOrchestrator(
                language="en-US",
                state_path=root / "o.json",
            )
            orch.self_model = SelfModel(path=root / "sm.json")
            orch.memory_scopes = ScopedMemoryStore(path=root / "m.json")
            cm = MagicMock()
            cm.active = True
            cm.handle.return_value = MagicMock(
                text="Calendar is clear.", source="chat"
            )
            orch.conversation_manager = cm
            orch.start(start_self_development=False)
            set_orchestrator(orch)
            flags = {}

            def bg():
                with patch(
                    "core.self_improve.self_development.SelfDevelopmentEngine"
                ) as Eng:
                    inst = Eng.return_value
                    inst.state.load.return_value = {"current_cycle": 4}
                    inst.state.status_summary.return_value = {
                        "current_cycle": 4,
                        "current_phase": "TEST",
                        "current_candidate": "runtime_completion",
                        "last_result": "KEEP",
                        "last_successful_improvement": "runtime_completion",
                        "last_failed_improvement": "",
                    }

                    def slow():
                        time.sleep(0.35)
                        return 0

                    inst.run.side_effect = slow
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
                    flags["dev"] = agent.handle_task(
                        "cycle",
                        context={
                            "max_cycles": 1,
                            "use_analyst": True,
                            "skip_agent_smoke": True,
                        },
                    )

            def user():
                time.sleep(0.05)
                flags["user"] = orch.handle_user_text("What's on my calendar?")

            t1 = threading.Thread(target=bg)
            t2 = threading.Thread(target=user)
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)
            self.assertEqual(flags["user"].status, "ok")
            self.assertEqual(flags["dev"].status, "ok")
            snap = orch.snapshot()
            self.assertIn("hardware", snap["resources"])
            orch.stop()
            set_orchestrator(None)


if __name__ == "__main__":
    unittest.main()
