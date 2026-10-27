# ==========================
# ELIAN — ProcedureEngine 2.0 regressions
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.builtin_tools import build_default_registry
from core.conversation_manager import ConversationManager
from core.model_client import ModelClient, reset_model_client
from core.procedure_engine import (
    ProcedureStep,
    compile_instruction,
    define_from_teach,
    execute_procedure,
    find_by_trigger,
    get_procedure,
    get_procedure_run_snapshot,
    list_procedures,
    reset_procedures_for_tests,
    run_procedure,
    save_procedure,
    set_enabled,
    set_sleep_fn_for_tests,
)
from core.procedure_run import run_named_procedure
from core.risk import RiskLevel
from core.semantic_executive import decide_semantics, fast_path_permitted
from core.tool_result import ToolResult, ToolStatus
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from memory.manager import MemoryManager


class BoomClient(ModelClient):
    def chat(self, messages, *, temperature=0.2, format=None, model=None) -> str:
        raise AssertionError("planner LLM must not run for procedure tests")


def _dry_registry(log: list | None = None) -> ToolRegistry:
    registry = ToolRegistry(confirm_medium=True)
    calls = log if log is not None else []

    def _ok(name):
        def inner(arguments):
            calls.append({"tool": name, "arguments": dict(arguments or {})})
            return ToolResult.ok(f"{name} ok", tool_name=name, data=dict(arguments or {}))

        return inner

    def _fail(name):
        def inner(arguments):
            calls.append({"tool": name, "arguments": dict(arguments or {})})
            return ToolResult.fail(f"{name} fail", tool_name=name)

        return inner

    safe = (
        "turn_off_device",
        "turn_on_device",
        "set_device_value",
        "get_device_state",
        "pause_media",
        "get_situation_briefing",
        "get_today_schedule",
        "get_calendar_events",
        "volume_up",
        "volume_down",
        "undo_last_action",
        "open_app",
    )
    for name in safe:
        registry.register(
            Tool(name=name, description=name, handler=_ok(name), risk_level=RiskLevel.SAFE)
        )
    registry.register(
        Tool(
            name="shutdown_pc",
            description="shutdown",
            handler=_ok("shutdown_pc"),
            risk_level=RiskLevel.DANGEROUS,
            confirmation_prompt="Mam wyłączyć komputer?",
        )
    )
    registry.register(
        Tool(
            name="close_app",
            description="close",
            handler=_ok("close_app"),
            risk_level=RiskLevel.MEDIUM,
        )
    )
    from core.procedure_engine import register_procedure_tools

    register_procedure_tools(registry)
    registry._calls = calls  # type: ignore[attr-defined]
    registry._fail = _fail  # type: ignore[attr-defined]
    return registry


def _cm(registry=None):
    mem = MemoryManager(Path(tempfile.mkdtemp()) / "m.json")
    cm = ConversationManager(
        registry=registry or _dry_registry(),
        model=BoomClient(),
        memory_manager=mem,
    )
    cm.activate()
    return cm


SLEEP_TEACH = (
    "jak powiem idę spać to zgaś lampę ustaw głośność na 15 procent "
    "powiedz co mam jutro i wyłącz komputer za 10 minut"
)
MORNING_TEACH = (
    "jak powiem zaczynamy poranek to podsumuj sytuację i powiedz co mam dziś"
)


class ProcedureEngineTests(unittest.TestCase):
    def setUp(self):
        reset_procedures_for_tests([])
        reset_registry_for_tests()
        reset_model_client()
        self._delays: list[float] = []
        set_sleep_fn_for_tests(self._delays.append)

    def tearDown(self):
        set_sleep_fn_for_tests(None)
        reset_procedures_for_tests([])
        reset_registry_for_tests(None)
        reset_model_client()

    def test_sleep_routine_compile_and_run(self):
        registry = _dry_registry()
        result = define_from_teach(
            "idę spać",
            "zgaś lampę ustaw głośność na 15 procent powiedz co mam jutro i wyłącz komputer za 10 minut",
            registry=registry,
        )
        self.assertTrue(result.success, result.message)
        proc = find_by_trigger("idę spać")
        self.assertIsNotNone(proc)
        tools = [step.tool for step in proc.steps]
        self.assertEqual(tools[0], "turn_off_device")
        self.assertIn("set_device_value", tools)
        self.assertIn("get_calendar_events", tools)
        self.assertIn("shutdown_pc", tools)
        shutdown = next(step for step in proc.steps if step.tool == "shutdown_pc")
        self.assertGreaterEqual(shutdown.delay_sec, 600)
        self.assertTrue(shutdown.confirm)
        ran = run_procedure("idę spać", registry=registry, confirmed=True)
        self.assertTrue(ran.success, ran.message)
        names = [row["tool"] for row in registry._calls]
        self.assertIn("turn_off_device", names)
        self.assertIn("shutdown_pc", names)
        self.assertTrue(any(d >= 600 for d in self._delays))

    def test_morning_routine(self):
        registry = _dry_registry()
        result = define_from_teach(
            "zaczynamy poranek",
            "podsumuj sytuację i powiedz co mam dziś",
            registry=registry,
        )
        self.assertTrue(result.success, result.message)
        ran = run_procedure("zaczynamy poranek", registry=registry, confirmed=True)
        self.assertTrue(ran.success, ran.message)
        names = [row["tool"] for row in registry._calls]
        self.assertIn("get_situation_briefing", names)
        self.assertIn("get_today_schedule", names)

    def test_custom_trigger_and_crud(self):
        registry = _dry_registry()
        reset_registry_for_tests(registry)
        saved = define_from_teach(
            "tryb film",
            "zgaś lampę i pauza mediów",
            registry=registry,
        )
        self.assertTrue(saved.success, saved.message)
        cm = _cm(registry)
        listed = cm.handle("jakie mam procedury")
        self.assertIn("tryb film", listed.text.lower())
        ran = cm.handle("uruchom procedurę tryb film")
        self.assertTrue(ran.should_speak)
        names = [row["tool"] for row in registry._calls]
        self.assertIn("turn_off_device", names)
        edited = cm.handle("edytuj procedurę tryb film to zapal lampę")
        self.assertTrue(edited.should_speak)
        proc = find_by_trigger("tryb film")
        self.assertEqual(proc.steps[0].tool, "turn_on_device")
        disabled = cm.handle("wyłącz procedurę tryb film")
        self.assertIn("wyłączam", disabled.text.lower())
        self.assertFalse(find_by_trigger("tryb film", enabled_only=True))
        blocked = run_procedure("tryb film", registry=registry, confirmed=True)
        self.assertFalse(blocked.success)
        deleted = cm.handle("usuń procedurę tryb film")
        self.assertIn("usun", deleted.text.lower())
        self.assertIsNone(find_by_trigger("tryb film", enabled_only=False))

    def test_cancel_while_executing(self):
        registry = _dry_registry()
        save_procedure(
            trigger="długa",
            steps=[
                ProcedureStep(tool="turn_off_device", args={"target": "lampa"}),
                ProcedureStep(tool="pause_media", args={}, delay_sec=1),
                ProcedureStep(tool="get_today_schedule", args={}),
            ],
            registry=registry,
        )
        state = {"n": 0}

        def cancel_after_first():
            state["n"] += 1
            return state["n"] > 2

        result = run_procedure(
            "długa",
            registry=registry,
            confirmed=True,
            cancel_check=cancel_after_first,
        )
        self.assertTrue(result.success)
        self.assertTrue((result.data or {}).get("cancelled"))
        names = [row["tool"] for row in registry._calls]
        self.assertNotIn("get_today_schedule", names)

    def test_delay_and_condition(self):
        registry = _dry_registry()
        save_procedure(
            trigger="warunek",
            steps=[
                ProcedureStep(
                    tool="turn_off_device",
                    args={"target": "lampa"},
                    condition={
                        "kind": "tool_success",
                        "tool": "get_device_state",
                        "args": {"target": "lampa"},
                        "expect": {"success": True},
                    },
                    delay_sec=0.4,
                )
            ],
            registry=registry,
        )
        result = run_procedure("warunek", registry=registry, confirmed=True)
        self.assertTrue(result.success)
        self.assertTrue(any(abs(d - 0.4) < 0.001 for d in self._delays))
        names = [row["tool"] for row in registry._calls]
        self.assertEqual(names[0], "get_device_state")
        self.assertIn("turn_off_device", names)

        def _fail_state(_arguments):
            return ToolResult.fail("off", tool_name="get_device_state")

        registry.unregister("get_device_state")
        registry.register(
            Tool(
                name="get_device_state",
                description="state",
                handler=_fail_state,
                risk_level=RiskLevel.SAFE,
            )
        )
        registry._calls.clear()
        skipped = run_procedure("warunek", registry=registry, confirmed=True)
        self.assertTrue(skipped.success)
        self.assertNotIn("turn_off_device", [row["tool"] for row in registry._calls])

    def test_failure_halfway(self):
        registry = _dry_registry()
        registry.unregister("pause_media")
        registry.register(
            Tool(
                name="pause_media",
                description="pause",
                handler=lambda _a: ToolResult.fail("pause fail", tool_name="pause_media"),
                risk_level=RiskLevel.SAFE,
            )
        )
        save_procedure(
            trigger="połowa",
            steps=[
                ProcedureStep(tool="turn_off_device", args={"target": "lampa"}),
                ProcedureStep(tool="pause_media", args={}, on_fail="stop"),
                ProcedureStep(tool="get_today_schedule", args={}),
            ],
            registry=registry,
        )
        result = run_procedure("połowa", registry=registry, confirmed=True)
        self.assertFalse(result.success)
        self.assertTrue((result.data or {}).get("partial") or (result.data or {}).get("completed") == 1)
        invoked = [str(item.get("tool") or item.get("name") or "") for item in registry.history]
        self.assertIn("turn_off_device", invoked)
        self.assertIn("pause_media", invoked)
        self.assertNotIn("get_today_schedule", invoked)

    def test_verification_failure(self):
        registry = _dry_registry()
        registry.unregister("get_device_state")
        registry.register(
            Tool(
                name="get_device_state",
                description="state",
                handler=lambda _a: ToolResult.fail("still on", tool_name="get_device_state"),
                risk_level=RiskLevel.SAFE,
            )
        )
        save_procedure(
            trigger="weryfikacja",
            steps=[
                ProcedureStep(
                    tool="turn_off_device",
                    args={"target": "lampa"},
                    verify={
                        "tool": "get_device_state",
                        "args": {"target": "lampa"},
                        "expect": {"success": True},
                    },
                    on_fail="stop",
                )
            ],
            registry=registry,
        )
        result = run_procedure("weryfikacja", registry=registry, confirmed=True)
        self.assertFalse(result.success)
        self.assertEqual(str(result.error or ""), "verification_failed")

    def test_unsafe_operation_rejected(self):
        registry = _dry_registry()
        steps, errors = compile_instruction("eval(__import__('os').system('dir'))", registry)
        self.assertFalse(steps)
        self.assertTrue(errors)
        bad = define_from_teach("zło", "os.system('rm -rf /')", registry=registry)
        self.assertFalse(bad.success)
        forbidden = save_procedure(
            trigger="shell",
            steps=[ProcedureStep(tool="os.system", args={"cmd": "whoami"})],
            registry=registry,
        )
        self.assertFalse(forbidden.success)
        unknown = save_procedure(
            trigger="brak",
            steps=[ProcedureStep(tool="not_a_real_tool", args={})],
            registry=registry,
        )
        self.assertFalse(unknown.success)

    def test_conflicting_trigger_asks(self):
        registry = _dry_registry()
        reset_registry_for_tests(registry)
        first = define_from_teach("idę spać", "zgaś lampę i pauza mediów", registry=registry)
        self.assertTrue(first.success, first.message)
        second = define_from_teach(
            "idę spać",
            "zgaś lampę i wyłącz komputer",
            registry=registry,
        )
        self.assertEqual(second.status, ToolStatus.NEEDS_CONFIRMATION)
        self.assertTrue((second.data or {}).get("conflict"))
        cm = _cm(registry)
        asked = cm.handle(SLEEP_TEACH)
        self.assertEqual(asked.source, "confirm")
        self.assertIn("nadpisa", asked.text.lower())
        yes = cm.handle("tak")
        self.assertTrue(yes.should_speak)
        proc = find_by_trigger("idę spać")
        self.assertTrue(any(step.tool == "shutdown_pc" for step in proc.steps))

    def test_corrupted_procedure_data(self):
        reset_procedures_for_tests(
            [
                {"id": "broken", "trigger": "zepsuta", "steps": "not-a-list"},
                {"id": "empty", "trigger": "pusta"},
                {
                    "id": "badtool",
                    "trigger": "zła",
                    "steps": [{"tool": "eval", "args": {}}],
                },
            ]
        )
        self.assertIsNone(get_procedure("broken"))
        self.assertIsNone(get_procedure("empty"))
        self.assertIsNone(get_procedure("badtool"))
        result = run_procedure("zła")
        self.assertFalse(result.success)

    def test_does_not_steal_fastpath_spotify(self):
        env = decide_semantics("możesz odpalić mi spotify", allow_llm=False)
        self.assertNotEqual(env.intent, "procedure_run")
        self.assertNotEqual(env.intent, "procedure_define")
        env2 = decide_semantics("otwórz spotify", allow_llm=False)
        self.assertNotEqual(env2.intent, "procedure_run")

    def test_wstalem_not_procedure_unless_saved(self):
        env = decide_semantics("Wstałem.", allow_llm=False)
        self.assertEqual(env.intent, "situation_briefing")
        self.assertTrue(fast_path_permitted("Wstałem.", env))

    def test_single_teach_stays_learned_routine(self):
        registry = _dry_registry()
        reset_registry_for_tests(registry)
        cm = _cm(registry)
        from unittest.mock import patch

        with patch("memory.vocabulary.remember_phrase", return_value=True):
            result = cm.handle("Jak powiem szot to otwórz Spotify")
        self.assertEqual(result.source, "teach")
        self.assertIsNone(find_by_trigger("szot"))

    def test_v1_morning_sleep_fallback(self):
        class R:
            def invoke(self, name, args, confirmed=False):
                return ToolResult.ok("Następne: trening o 17.", tool_name=name)

        result = run_named_procedure("morning", registry=R())
        self.assertTrue(result.success)
        self.assertIn("trening", result.message)
        gaming = run_named_procedure("gaming", registry=R())
        self.assertFalse(gaming.success)

    def test_run_state_snapshot_for_worldstate(self):
        snap = get_procedure_run_snapshot()
        self.assertIn("active", snap)
        self.assertIn("procedure_id", snap)
        self.assertFalse(snap["active"])

    def test_cm_trigger_runs_saved_procedure(self):
        registry = _dry_registry()
        reset_registry_for_tests(registry)
        define_from_teach("tryb noc", "zgaś lampę i pauza mediów", registry=registry)
        cm = _cm(registry)
        result = cm.handle("tryb noc")
        self.assertTrue(result.should_speak)
        names = [row["tool"] for row in registry._calls]
        self.assertIn("turn_off_device", names)

    def test_unsafe_high_risk_still_asks(self):
        registry = _dry_registry()
        save_procedure(
            trigger="gaśnica",
            steps=[ProcedureStep(tool="shutdown_pc", args={}, confirm=True)],
            registry=registry,
        )
        asked = run_procedure("gaśnica", registry=registry, confirmed=False)
        self.assertEqual(asked.status, ToolStatus.NEEDS_CONFIRMATION)
        self.assertFalse(registry._calls)
        ran = run_procedure("gaśnica", registry=registry, confirmed=True)
        self.assertTrue(ran.success)
        self.assertEqual(registry._calls[0]["tool"], "shutdown_pc")


if __name__ == "__main__":
    unittest.main()
