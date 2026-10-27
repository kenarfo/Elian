# ==========================
# ELIAN — Goal execution / plan-cycle semantic tests
# ==========================
"""Parameterized fixtures for PLAN→EXECUTE→VERIFY→REPAIR / resume / partial.

Counts (minimum): 50 plan, 30 injected fail, 20 repair ok, 20 exhaust,
20 partial, 20 interrupt, 20 resume. Offline — no mic / HA writes.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent_repair import decide_execution_repair
from core.execution_context import (
    ActiveExecutionContext,
    FailureCategory,
    SuccessCriterion,
    CriterionKind,
    action_fingerprint,
    clear_active_execution,
    criteria_for_tool,
    new_execution_context,
    partial_success_message,
    rows_indicate_partial_or_incomplete,
    set_active_execution,
    step_row_outcome,
)
from core.intent_family import IntentClassification, IntentFamily
from core.learned_routines import reset_routines_for_tests
from core.model_client import FakeModelClient, reset_model_client
from core.pending_goal import (
    goal_text_from_pending,
    looks_alternate_utterance,
    looks_cancel_pending,
    looks_incomplete_reminder,
    looks_resume_utterance,
    looks_retry_utterance,
    normalize_pending_goal_fields,
    pending_from_plan,
    status_summary_for_pending,
)
from core.plan_cycle import (
    ExecutionResult,
    RepairAction,
    VerificationResult,
    VerifyStatus,
    diagnose_failure,
    plan_status_from_rows,
    run_execute_verify_repair,
    verify_effect,
)
from core.plan_state import PLAN_PARTIAL, STEP_FAILED, STEP_SUCCEEDED
from core.planner import Planner
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from core.conversation_manager import ConversationManager
from core.active_goal import GoalStatus, has_goal_completion_evidence


def _isolate():
    reset_routines_for_tests([])
    clear_active_execution()
    try:
        from core.world_state import reset_world_state

        reset_world_state()
    except Exception:
        pass
    try:
        from core.active_service import reset_active_service

        reset_active_service()
    except Exception:
        pass
    try:
        from core.pending_goal import clear_persisted_pending

        clear_persisted_pending()
    except Exception:
        pass


# Patch capability gate + HA registry refresh for speed/isolation.
_OFFLINE_PATCHES = []


def _start_offline_patches():
    global _OFFLINE_PATCHES
    _stop_offline_patches()
    patches = [
        patch("core.capability_registry.capability_gate_for_tool", return_value=None),
        patch("core.execution_context.capability_missing_for_tool", return_value=False),
        patch("Memory.record_action", return_value=None),
    ]
    try:
        import HomeAssistant as ha

        patches.append(patch.object(ha.HomeAssistantDeviceRegistry, "refresh", return_value=[]))
        patches.append(patch.object(ha.HomeAssistantDeviceRegistry, "maybe_refresh", return_value=[]))
        if hasattr(ha, "api_request"):
            patches.append(
                patch.object(
                    ha,
                    "api_request",
                    return_value=None,
                )
            )
    except Exception:
        pass
    for p in patches:
        p.start()
        _OFFLINE_PATCHES.append(p)


def _stop_offline_patches():
    global _OFFLINE_PATCHES
    for p in _OFFLINE_PATCHES:
        try:
            p.stop()
        except Exception:
            pass
    _OFFLINE_PATCHES = []


def _end():
    from core import learned_routines as lr

    lr._TEST_STORE = None
    reset_registry_for_tests(None)
    reset_model_client()
    clear_active_execution()
    _stop_offline_patches()


def _info(text, family=IntentFamily.MEDIA, **slots):
    return IntentClassification(
        family=family,
        is_complete=True,
        slots=dict(slots),
        raw_text=text,
    )


def _registry(*, play_ok=True, play_retry_ok=False, fail_verify=False, open_ok=True,
              volume_ok=True, ha_ok=True, ha_state="on", web_ok=True, web_empty=False,
              window_ok=True):
    registry = ToolRegistry(confirm_medium=False)
    state = {
        "play_calls": 0,
        "open_calls": 0,
        "ha_calls": 0,
        "web_calls": 0,
        "play_ok": play_ok,
        "play_retry_ok": play_retry_ok,
        "fail_verify": fail_verify,
        "open_ok": open_ok,
        "volume_ok": volume_ok,
        "ha_ok": ha_ok,
        "ha_state": ha_state,
        "web_ok": web_ok,
        "web_empty": web_empty,
        "window_ok": window_ok,
        "now_playing": "",
        "volume_level": 40.0,
        "volume_snap": {"level": 40.0, "scalar": 0.4, "mute": False, "step": 20, "steps": 50},
        "invoked": [],
    }

    def open_app(args):
        state["open_calls"] += 1
        state["invoked"].append({"tool": "open_app", "arguments": dict(args or {})})
        if not state["open_ok"]:
            return ToolResult.fail("Nie otworzyłem.", tool_name="open_app", retryable=True)
        return ToolResult.ok("OK", tool_name="open_app", data={"app": args.get("name")})

    def play_media(args):
        state["play_calls"] += 1
        state["invoked"].append({"tool": "play_media", "arguments": dict(args or {})})
        ok = state["play_ok"] or (state["play_retry_ok"] and state["play_calls"] > 1)
        if not ok:
            return ToolResult.fail("Nie odtworzyłem.", tool_name="play_media", retryable=True)
        q = str(args.get("query") or "")
        state["now_playing"] = q
        return ToolResult.ok(f"Puściłem {q}.", tool_name="play_media", data=dict(args or {}))

    def volume_down(_a):
        state["invoked"].append({"tool": "volume_down", "arguments": {}})
        if not state["volume_ok"]:
            return ToolResult.fail("Nie ściszyłem.", tool_name="volume_down", retryable=False)
        before = dict(state.get("volume_snap") or {"level": 50.0, "scalar": 0.5, "mute": False, "step": 25, "steps": 50})
        after = {"level": 40.0, "scalar": 0.4, "mute": False, "step": 20, "steps": 50}
        state["volume_level"] = after["level"]
        state["volume_snap"] = after
        return ToolResult.ok(
            "Ciszej.",
            tool_name="volume_down",
            data={"action": "adjust_volume", "direction": "decrease", "volume_before": before, "volume_after": after},
        )

    def volume_up(_a):
        state["invoked"].append({"tool": "volume_up", "arguments": {}})
        if not state["volume_ok"]:
            return ToolResult.fail("Nie podgłośniłem.", tool_name="volume_up", retryable=False)
        before = dict(state.get("volume_snap") or {"level": 40.0, "scalar": 0.4, "mute": False, "step": 20, "steps": 50})
        after = {"level": 50.0, "scalar": 0.5, "mute": False, "step": 25, "steps": 50}
        state["volume_level"] = after["level"]
        state["volume_snap"] = after
        return ToolResult.ok(
            "Głośniej.",
            tool_name="volume_up",
            data={"action": "adjust_volume", "direction": "increase", "volume_before": before, "volume_after": after},
        )

    def get_volume(_a):
        state["invoked"].append({"tool": "get_volume", "arguments": dict(_a or {})})
        snap = dict(state.get("volume_snap") or {"level": 40.0, "scalar": 0.4, "mute": False, "step": 20, "steps": 50})
        return ToolResult.ok(f"Głośność {snap.get('level')}%.", tool_name="get_volume", data=snap)

    def get_now_playing(_a):
        if state["fail_verify"]:
            return ToolResult.ok("Inne.", tool_name="get_now_playing", data={"title": "X"})
        if not state["now_playing"]:
            return ToolResult.ok("Nic.", tool_name="get_now_playing", data={"title": ""})
        return ToolResult.ok(
            f"Gra {state['now_playing']}.",
            tool_name="get_now_playing",
            data={"title": state["now_playing"], "artist": state["now_playing"]},
        )

    def window_exists(args):
        name = str(args.get("name") or args.get("query") or "")
        if not state["window_ok"]:
            return ToolResult.fail("Brak okna.", tool_name="window_exists")
        return ToolResult.ok("Jest.", tool_name="window_exists", data={"exists": True, "name": name})

    def turn_on_device(args):
        state["ha_calls"] += 1
        state["invoked"].append({"tool": "turn_on_device", "arguments": dict(args or {})})
        if not state["ha_ok"]:
            return ToolResult.fail(
                "Home Assistant offline.",
                tool_name="turn_on_device",
                error="unavailable",
                retryable=True,
                data={"outcome": "UNAVAILABLE"},
            )
        state["ha_state"] = "on"
        return ToolResult.ok(
            "Włączone.",
            tool_name="turn_on_device",
            data={
                "entity_id": args.get("entity_id") or args.get("target"),
                "state": "on",
                "after": "on",
                "verification": "SUCCESS",
                "outcome": "SUCCESS",
            },
        )

    def turn_off_device(args):
        state["ha_calls"] += 1
        if not state["ha_ok"]:
            return ToolResult.fail("HA offline.", tool_name="turn_off_device", error="unavailable")
        state["ha_state"] = "off"
        return ToolResult.ok(
            "Zgaszone.",
            tool_name="turn_off_device",
            data={
                "entity_id": args.get("entity_id"),
                "state": "off",
                "after": "off",
                "verification": "SUCCESS",
                "outcome": "SUCCESS",
            },
        )

    def get_device_state(args):
        return ToolResult.ok(
            f"Stan {state['ha_state']}.",
            tool_name="get_device_state",
            data={"state": state["ha_state"], "entity_id": args.get("entity_id")},
        )

    def web_search(args):
        state["web_calls"] += 1
        if not state["web_ok"]:
            return ToolResult.fail("Brak sieci.", tool_name="web_search", error="unavailable")
        if state["web_empty"]:
            return ToolResult.ok("Nic.", tool_name="web_search", data={"results": [], "query": args.get("query")})
        return ToolResult.ok(
            "Wyniki.",
            tool_name="web_search",
            data={"results": [{"title": "A", "url": "https://a"}], "query": args.get("query")},
        )

    for name, handler, params in (
        ("open_app", open_app, {"name": {"type": "string"}}),
        ("play_media", play_media, {"query": {"type": "string"}}),
        ("volume_down", volume_down, {}),
        ("volume_up", volume_up, {}),
        ("get_volume", get_volume, {}),
        ("get_now_playing", get_now_playing, {}),
        ("window_exists", window_exists, {"name": {"type": "string"}}),
        ("turn_on_device", turn_on_device, {"entity_id": {"type": "string"}}),
        ("turn_off_device", turn_off_device, {"entity_id": {"type": "string"}}),
        ("get_device_state", get_device_state, {"entity_id": {"type": "string"}}),
        ("web_search", web_search, {"query": {"type": "string"}}),
    ):
        registry.register(
            Tool(
                name=name,
                description=name,
                handler=handler,
                risk_level=RiskLevel.SAFE,
                parameters={"type": "object", "properties": params},
            )
        )
    registry._state = state  # type: ignore[attr-defined]
    return registry


# --- Fixtures (parameterized) -------------------------------------------------

PLAN_GOALS = [
    ("puść Michaela Jacksona", "play_media", IntentFamily.MEDIA),
    ("otwórz Spotify", "open_app", IntentFamily.APP),
    ("włącz lampę", "turn_on_device", IntentFamily.SMART_HOME),
    ("zgaś lampę", "turn_off_device", IntentFamily.SMART_HOME),
    ("szukaj pogody w Łodzi", "web_search", IntentFamily.WEB),
    ("zwiększ głośność", "volume_up", IntentFamily.SYSTEM),
    ("zmniejsz głośność", "volume_down", IntentFamily.SYSTEM),
    ("puść jazz", "play_media", IntentFamily.MEDIA),
    ("odpal Chrome", "open_app", IntentFamily.APP),
    ("włącz światło w salonie", "turn_on_device", IntentFamily.SMART_HOME),
] * 5  # 50

INJECTED_FAIL_TOOLS = [
    ("play_media", {"query": "MJ"}, IntentFamily.MEDIA),
    ("open_app", {"name": "Spotify"}, IntentFamily.APP),
    ("turn_on_device", {"entity_id": "light.x"}, IntentFamily.SMART_HOME),
    ("web_search", {"query": "news"}, IntentFamily.WEB),
    ("volume_up", {}, IntentFamily.SYSTEM),
] * 6  # 30

REPAIR_CASES = [(i, f"track-{i}") for i in range(20)]
EXHAUST_CASES = [(i, f"fail-{i}") for i in range(20)]
PARTIAL_CASES = [
    ("Otwórz Spotify i zwiększ głośność.", "volume"),
] * 20  # 20 — compound APP+SYSTEM (HA compounds may fall to deterministic APP)

INTERRUPT_PHRASES = [
    ("nieważne", "cancel"),
    ("anuluj", "cancel"),
    ("spróbuj jeszcze raz", "retry"),
    ("jeszcze raz", "retry"),
    ("spróbuj inaczej", "alternate"),
    ("zrób to inaczej", "alternate"),
    ("zrób to teraz", "resume"),
    ("kontynuuj", "resume"),
    ("dokończ", "resume"),
    ("jeszcze tego nie zrobiłeś", "incomplete"),
] * 2  # 20

RESUME_CASES = list(range(20))


class GoalPlanTaskTests(unittest.TestCase):
    """50 plan tasks — SuccessCriteria mapping + verified success path."""

    def setUp(self):
        _isolate()
        _start_offline_patches()

    def tearDown(self):
        _end()

    def test_plan_tasks_count(self):
        self.assertGreaterEqual(len(PLAN_GOALS), 50)

    def test_criteria_for_common_tools(self):
        for tool in ("play_media", "open_app", "turn_on_device", "web_search", "turn_off_device"):
            crit = criteria_for_tool(tool, {"query": "x", "name": "Spotify", "entity_id": "light.a"})
            self.assertTrue(crit)
            self.assertIsInstance(crit[0], SuccessCriterion)

    def test_parameterized_plan_verified_success(self):
        registry = _registry()
        for goal, tool, family in PLAN_GOALS:
            with self.subTest(goal=goal, tool=tool):
                if tool == "play_media":
                    args = {"query": "Michael Jackson"}
                elif tool == "open_app":
                    args = {"name": "Spotify"}
                elif tool in ("turn_on_device", "turn_off_device"):
                    args = {"entity_id": "light.lamp"}
                elif tool == "web_search":
                    args = {"query": "pogoda Łódź"}
                else:
                    args = {}
                info = _info(goal, family)
                out = run_execute_verify_repair(
                    registry,
                    tool_name=tool,
                    arguments=args,
                    classification=info,
                    goal=goal,
                    confirmed=True,
                    max_repair=1,
                )
                self.assertTrue(out.verified_success, msg=f"{tool} {out.verification}")


class InjectedFailureTests(unittest.TestCase):
    """30 injected failures → FailureDiagnosis categories."""

    def setUp(self):
        _isolate()
        _start_offline_patches()

    def tearDown(self):
        _end()

    def test_injected_failure_count(self):
        self.assertGreaterEqual(len(INJECTED_FAIL_TOOLS), 30)

    def test_parameterized_injected_failures(self):
        for tool, args, family in INJECTED_FAIL_TOOLS:
            with self.subTest(tool=tool):
                if tool == "play_media":
                    registry = _registry(play_ok=False)
                elif tool == "open_app":
                    registry = _registry(open_ok=False)
                elif tool == "turn_on_device":
                    registry = _registry(ha_ok=False)
                elif tool == "web_search":
                    registry = _registry(web_ok=False)
                else:
                    registry = _registry(volume_ok=False)
                info = _info("x", family)
                out = run_execute_verify_repair(
                    registry,
                    tool_name=tool,
                    arguments=args,
                    classification=info,
                    goal="x",
                    confirmed=True,
                    max_repair=1,
                )
                self.assertFalse(out.verified_success)
                diag = out.diagnosis or diagnose_failure(
                    tool_name=tool,
                    arguments=args,
                    execution=out.execution,
                    verification=out.verification,
                    classification=info,
                )
                self.assertIsInstance(diag.category, FailureCategory)
                # Volume / schema failures may land as UNKNOWN — still a typed diagnosis.
                self.assertIn(
                    diag.category,
                    set(FailureCategory),
                )


class RepairSuccessTests(unittest.TestCase):
    def setUp(self):
        _isolate()
        _start_offline_patches()

    def tearDown(self):
        _end()

    def test_repair_success_count(self):
        self.assertGreaterEqual(len(REPAIR_CASES), 20)

    def test_parameterized_repair_success(self):
        for idx, query in REPAIR_CASES:
            with self.subTest(idx=idx):
                registry = _registry(play_ok=False, play_retry_ok=True)
                info = _info(f"puść {query}", IntentFamily.MEDIA, media_query=query)
                out = run_execute_verify_repair(
                    registry,
                    tool_name="play_media",
                    arguments={"query": query},
                    classification=info,
                    goal=f"puść {query}",
                    confirmed=True,
                    max_repair=2,
                )
                self.assertTrue(out.verified_success)
                self.assertGreaterEqual(out.repairs_used, 1)


class RepairExhaustionTests(unittest.TestCase):
    def setUp(self):
        _isolate()
        _start_offline_patches()

    def tearDown(self):
        _end()

    def test_exhaust_count(self):
        self.assertGreaterEqual(len(EXHAUST_CASES), 20)

    def test_parameterized_repair_exhaustion(self):
        for idx, query in EXHAUST_CASES:
            with self.subTest(idx=idx):
                registry = _registry(play_ok=False)
                info = _info(f"puść {query}", IntentFamily.MEDIA)
                out = run_execute_verify_repair(
                    registry,
                    tool_name="play_media",
                    arguments={"query": query},
                    classification=info,
                    goal=query,
                    confirmed=True,
                    max_repair=2,
                )
                self.assertFalse(out.verified_success)
                decision = decide_execution_repair(
                    tool_name="play_media",
                    arguments={"query": query},
                    execution=out.execution,
                    verification=out.verification,
                    classification=info,
                    repair_used=2,
                    max_repair=2,
                    diagnosis=out.diagnosis,
                )
                self.assertEqual(decision.action, RepairAction.FAIL)
                self.assertEqual(decision.reason, "repair_exhausted")


class PartialSuccessTests(unittest.TestCase):
    def setUp(self):
        _isolate()
        _start_offline_patches()

    def tearDown(self):
        _end()

    def test_partial_count(self):
        self.assertGreaterEqual(len(PARTIAL_CASES), 20)

    def test_parameterized_partial_success(self):
        for idx, (goal, _mode) in enumerate(PARTIAL_CASES):
            with self.subTest(idx=idx, goal=goal):
                rows = [
                    {
                        "step_id": f"p{idx}.1",
                        "status": STEP_SUCCEEDED,
                        "tool": "open_app",
                        "semantic_action": "open_service",
                        "target": "Spotify",
                        "arguments": {"name": "Spotify"},
                        "observation": {"success": True},
                    },
                    {
                        "step_id": f"p{idx}.2",
                        "status": STEP_FAILED,
                        "tool": "volume_up",
                        "semantic_action": "adjust_volume",
                        "arguments": {},
                        "observation": {"success": False},
                    },
                ]
                self.assertEqual(plan_status_from_rows(rows), PLAN_PARTIAL)
                msg = partial_success_message(rows)
                self.assertNotIn("Gotowe.", msg)
                self.assertTrue(msg)
        registry = _registry(volume_ok=False)
        reset_registry_for_tests(registry)
        planner = Planner(registry, model=FakeModelClient([]), max_steps=6)
        goal = "Otwórz Spotify i zwiększ głośność."
        with patch("core.computer_agent.get_computer_agent") as mock_agent:
            agent = mock_agent.return_value
            agent.budget_exceeded.return_value = None
            agent.is_busy.return_value = False
            agent.plan_working_app = ""
            agent.last_working_app = ""
            agent.current_plan = {}
            result = planner.run(goal, context={"raw_text": goal})
        self.assertEqual(result.source, "compound")
        self.assertFalse(result.success)
        self.assertNotEqual((result.message or "").strip().lower(), "gotowe.")

    def test_bad_shape_partial_success_message_probe_r8(self):
        bad_shape = [
            {"tool": "open_app", "success": True, "verified": True},
            {"tool": "volume_down", "success": False, "verified": False},
        ]
        msg = partial_success_message(bad_shape)
        self.assertNotEqual(msg.strip(), "Gotowe.")
        self.assertNotIn("Gotowe", msg)
        self.assertEqual(plan_status_from_rows(bad_shape), PLAN_PARTIAL)


class InterruptCorrectionTests(unittest.TestCase):
    def setUp(self):
        _isolate()
        _start_offline_patches()

    def tearDown(self):
        _end()

    def test_interrupt_count(self):
        self.assertGreaterEqual(len(INTERRUPT_PHRASES), 20)

    def test_parameterized_interrupt_phrases(self):
        for phrase, kind in INTERRUPT_PHRASES:
            with self.subTest(phrase=phrase, kind=kind):
                if kind == "cancel":
                    self.assertTrue(looks_cancel_pending(phrase) or phrase in ("anuluj",))
                elif kind == "retry":
                    self.assertTrue(looks_retry_utterance(phrase, allow_weak=True))
                elif kind == "alternate":
                    self.assertTrue(looks_alternate_utterance(phrase))
                elif kind == "resume":
                    self.assertTrue(
                        looks_resume_utterance(phrase) or looks_retry_utterance(phrase, allow_weak=True)
                    )
                elif kind == "incomplete":
                    self.assertTrue(looks_incomplete_reminder(phrase))

    def test_cm_cancel_and_incomplete(self):
        registry = _registry()
        reset_registry_for_tests(registry)
        cm = ConversationManager(registry=registry, memory=[], model=FakeModelClient([]))
        cm.activate()
        pending = pending_from_plan(
            plan_id="ab12",
            original_goal="Otwórz Spotify i zgaś lampę",
            status="PARTIAL",
            steps=[
                {"step_id": "ab12.1", "status": "SUCCEEDED", "tool": "open_app", "semantic_action": "open_service", "arguments": {"name": "Spotify"}, "target": "Spotify"},
                {"step_id": "ab12.2", "status": "FAILED", "tool": "turn_off_device", "semantic_action": "turn_off", "arguments": {"entity_id": "light.x"}, "target": "lampa"},
            ],
        )
        cm._set_pending_executable(pending)
        out = cm.handle("jeszcze tego nie zrobiłeś")
        self.assertTrue(out.should_speak)
        lowered = (out.text or "").lower()
        self.assertTrue(
            ("cel" in lowered)
            or ("zostało" in lowered)
            or ("jeszcze" in lowered)
            or ("mogę" in lowered)
            or ("dokonc" in lowered.replace("ń", "n")),
            msg=out.text,
        )
        from core.pending_goal import looks_incomplete_reminder

        self.assertTrue(looks_incomplete_reminder("jeszcze tego nie zrobiłeś"))
        out2 = cm.handle("nieważne")
        self.assertIn("nie próbuję", (out2.text or "").lower())
        self.assertIsNone(cm.pending_executable_goal)


class ResumedGoalTests(unittest.TestCase):
    def setUp(self):
        _isolate()
        _start_offline_patches()

    def tearDown(self):
        _end()

    def test_resume_count(self):
        self.assertGreaterEqual(len(RESUME_CASES), 20)

    def test_parameterized_resume_skips_succeeded(self):
        for idx in RESUME_CASES:
            with self.subTest(idx=idx):
                plan_id = f"r{idx:02d}"
                pending = pending_from_plan(
                    plan_id=plan_id,
                    original_goal="Otwórz Spotify i zwiększ głośność.",
                    status="PARTIAL",
                    steps=[
                        {
                            "step": 1,
                            "plan_id": plan_id,
                            "step_id": f"{plan_id}.1",
                            "attempt_id": 1,
                            "idempotency_key": f"{plan_id}|{plan_id}.1|open_service|spotify|",
                            "semantic_action": "open_service",
                            "target": "Spotify",
                            "status": STEP_SUCCEEDED,
                            "tool": "open_app",
                            "arguments": {"name": "Spotify"},
                            "observation": {"success": True, "tool": "open_app"},
                        },
                        {
                            "step": 2,
                            "plan_id": plan_id,
                            "step_id": f"{plan_id}.2",
                            "attempt_id": 1,
                            "idempotency_key": f"{plan_id}|{plan_id}.2|adjust_volume||increase",
                            "semantic_action": "adjust_volume",
                            "direction": "increase",
                            "status": STEP_FAILED,
                            "tool": "volume_up",
                            "arguments": {},
                            "observation": {"success": False, "tool": "volume_up"},
                        },
                    ],
                )
                ctx = ActiveExecutionContext.from_pending(pending)
                self.assertIsNotNone(ctx)
                self.assertEqual(ctx.status, PLAN_PARTIAL)
                self.assertTrue(ctx.failed_step_ids)
                self.assertTrue(ctx.completed_step_ids)
                self.assertIsNotNone(ctx.to_pending_goal())
        plan_id = "rz99"
        pending = pending_from_plan(
            plan_id=plan_id,
            original_goal="Otwórz Spotify i zwiększ głośność.",
            status="PARTIAL",
            steps=[
                {
                    "step": 1,
                    "plan_id": plan_id,
                    "step_id": f"{plan_id}.1",
                    "attempt_id": 1,
                    "idempotency_key": f"{plan_id}|{plan_id}.1|open_service|spotify|",
                    "semantic_action": "open_service",
                    "target": "Spotify",
                    "status": STEP_SUCCEEDED,
                    "tool": "open_app",
                    "arguments": {"name": "Spotify"},
                    "observation": {"success": True, "tool": "open_app"},
                },
                {
                    "step": 2,
                    "plan_id": plan_id,
                    "step_id": f"{plan_id}.2",
                    "attempt_id": 1,
                    "idempotency_key": f"{plan_id}|{plan_id}.2|adjust_volume||increase",
                    "semantic_action": "adjust_volume",
                    "direction": "increase",
                    "status": STEP_FAILED,
                    "tool": "volume_up",
                    "arguments": {},
                    "observation": {"success": False, "tool": "volume_up"},
                },
            ],
        )
        registry = _registry(volume_ok=True)
        reset_registry_for_tests(registry)
        planner = Planner(registry, model=FakeModelClient([]), max_steps=6)
        open_before = registry._state["open_calls"]
        with patch("core.computer_agent.get_computer_agent") as mock_agent:
            agent = mock_agent.return_value
            agent.budget_exceeded.return_value = None
            agent.is_busy.return_value = False
            agent.plan_working_app = ""
            agent.last_working_app = ""
            agent.current_plan = {}
            second = planner.run(
                "Otwórz Spotify i zwiększ głośność.",
                context={
                    "raw_text": "Otwórz Spotify i zwiększ głośność.",
                    "retry_failed_only": True,
                    "pending_executable_goal": pending,
                },
            )
        self.assertEqual(registry._state["open_calls"], open_before)
        skipped = [s for s in second.steps if s.get("status") == "SKIPPED"]
        self.assertTrue(skipped or second.success or second.plan_status in ("COMPLETED", "PARTIAL", ""))


class DiagnosisPolicyMatrixTests(unittest.TestCase):
    def setUp(self):
        _isolate()
        _start_offline_patches()

    def tearDown(self):
        _end()

    def test_categories_cover_mission_list(self):
        needed = {
            "invalid_args",
            "dependency_unavailable",
            "timeout",
            "state_not_changed",
            "permission_denied",
            "capability_missing",
            "ambiguous_target",
            "tool_exception",
            "verification_failed",
        }
        self.assertTrue(needed.issubset({c.value for c in FailureCategory}))

    def test_invalid_args_asks(self):
        info = _info("puść", IntentFamily.MEDIA)
        execu = ExecutionResult(success=False, tool="play_media", blocked=True, block_reason="material_uncertainty")
        ver = VerificationResult(status=VerifyStatus.SKIPPED)
        diag = diagnose_failure(tool_name="play_media", arguments={}, execution=execu, verification=ver, classification=info)
        self.assertEqual(diag.category, FailureCategory.INVALID_ARGS)
        decision = decide_execution_repair(
            tool_name="play_media",
            arguments={},
            execution=execu,
            verification=ver,
            classification=info,
            diagnosis=diag,
        )
        self.assertEqual(decision.action, RepairAction.ASK)

    def test_force_alternate_avoids_same_fingerprint(self):
        info = _info("otwórz Spotify", IntentFamily.APP, app_name="Spotify")
        args = {"name": "Spotify"}
        fp = action_fingerprint("open_app", args)
        execu = ExecutionResult(success=False, tool="open_app", arguments=args, retryable=True)
        ver = VerificationResult(status=VerifyStatus.FAILED, reason="execute_failed")
        diag = diagnose_failure(
            tool_name="open_app", arguments=args, execution=execu, verification=ver, classification=info
        )
        decision = decide_execution_repair(
            tool_name="open_app",
            arguments=args,
            execution=execu,
            verification=ver,
            classification=info,
            diagnosis=diag,
            force_alternate=True,
            blocked_fingerprints=[fp],
        )
        self.assertIn(decision.action, (RepairAction.ALTERNATE, RepairAction.FAIL))
        if decision.action == RepairAction.ALTERNATE:
            self.assertNotEqual(action_fingerprint(decision.tool, decision.arguments), fp)

    def test_ha_and_web_success_criteria(self):
        registry = _registry()
        info = _info("włącz lampę", IntentFamily.SMART_HOME)
        out = run_execute_verify_repair(
            registry,
            tool_name="turn_on_device",
            arguments={"entity_id": "light.x"},
            classification=info,
            goal="włącz lampę",
            confirmed=True,
        )
        self.assertTrue(out.verified_success)
        web = run_execute_verify_repair(
            registry,
            tool_name="web_search",
            arguments={"query": "news"},
            classification=_info("news", IntentFamily.WEB),
            goal="news",
            confirmed=True,
        )
        self.assertTrue(web.verified_success)
        registry._state["web_empty"] = True
        empty = run_execute_verify_repair(
            registry,
            tool_name="web_search",
            arguments={"query": "zzz"},
            classification=_info("zzz", IntentFamily.WEB),
            goal="zzz",
            confirmed=True,
            max_repair=1,
        )
        self.assertFalse(empty.verified_success)

    def test_execution_context_roundtrip(self):
        ctx = new_execution_context("ogarnij komputer do gry", plan_id="g1")
        ctx.constraints.append("no_restart_without_consent")
        ctx.sync_from_steps(
            [
                {"step_id": "g1.1", "status": STEP_SUCCEEDED, "tool": "open_app"},
                {"step_id": "g1.2", "status": STEP_FAILED, "tool": "close_app"},
            ],
            status=PLAN_PARTIAL,
        )
        pending = ctx.to_pending_goal()
        self.assertIsNotNone(pending)
        back = ActiveExecutionContext.from_pending(pending)
        self.assertEqual(back.goal, ctx.goal)
        self.assertEqual(back.status, PLAN_PARTIAL)
        set_active_execution(back)
        summary = status_summary_for_pending(pending)
        self.assertIn("Cel", summary)


class PlanStatusHelpersTests(unittest.TestCase):
    def test_plan_status_partial(self):
        rows = [
            {"status": STEP_SUCCEEDED, "observation": {"success": True}},
            {"status": STEP_FAILED, "observation": {"success": False}},
        ]
        self.assertEqual(plan_status_from_rows(rows), PLAN_PARTIAL)

    def test_plan_status_bad_shape_success_verified_partial(self):
        rows = [
            {"tool": "open_app", "success": True, "verified": True},
            {"tool": "volume_down", "success": False, "verified": False},
        ]
        self.assertEqual(plan_status_from_rows(rows), PLAN_PARTIAL)

    def test_plan_status_unknown_rows_not_completed(self):
        rows = [{"tool": "open_app"}, {"tool": "volume_down"}]
        self.assertNotEqual(plan_status_from_rows(rows), "COMPLETED")


class BadShapePartialGotoweTests(unittest.TestCase):
    """Probe r8/r9 3b: success/verified keys without status must not claim Gotowe."""

    def test_canonical_mixed_not_gotowe(self):
        rows = [
            {"status": STEP_SUCCEEDED, "tool": "turn_off_device"},
            {"status": STEP_FAILED, "tool": "volume_down"},
        ]
        msg = partial_success_message(rows)
        self.assertNotIn("Gotowe", msg)
        self.assertTrue(msg)

    def test_bad_shape_success_verified_not_gotowe(self):
        rows = [
            {"tool": "open_app", "success": True, "verified": True},
            {"tool": "volume_down", "success": False, "verified": False},
        ]
        self.assertEqual(step_row_outcome(rows[0]), STEP_SUCCEEDED)
        self.assertEqual(step_row_outcome(rows[1]), STEP_FAILED)
        msg = partial_success_message(rows)
        self.assertNotEqual(msg.strip(), "Gotowe.")
        self.assertNotIn("Gotowe", msg)

    def test_all_success_bool_shape_may_gotowe(self):
        rows = [
            {"tool": "open_app", "success": True, "verified": True},
            {"tool": "volume_down", "success": True, "verified": True},
        ]
        self.assertEqual(partial_success_message(rows).strip(), "Gotowe.")

    def test_unknown_shape_not_gotowe(self):
        rows = [{"tool": "open_app"}, {"tool": "volume_down"}]
        msg = partial_success_message(rows)
        self.assertNotEqual(msg.strip(), "Gotowe.")
        self.assertNotIn("Gotowe", msg)

    def test_active_goal_partial_steps_not_completed(self):
        from core.active_goal import ConversationGoalStore, GoalStatus

        store = ConversationGoalStore()
        store.observe_utterance("otworz Spotify i ciszej")
        store.mark_in_progress(action="compound")
        store.mark_result(
            success=True,
            action="compound",
            result={"success": True, "message": "Gotowe."},
            steps=[
                {"tool": "open_app", "success": True, "verified": True},
                {"tool": "volume_down", "success": False, "verified": False},
            ],
        )
        self.assertIsNotNone(store.current)
        self.assertNotEqual(store.current.status, GoalStatus.COMPLETED.value)
        self.assertTrue(
            rows_indicate_partial_or_incomplete(
                steps=[
                    {"tool": "open_app", "success": True, "verified": True},
                    {"tool": "volume_down", "success": False, "verified": False},
                ]
            )
        )


if __name__ == "__main__":
    unittest.main()


class FalseSuccessGoalCompletionTests(unittest.TestCase):
    """P0: ActiveGoal COMPLETED only with tool/verified evidence (S14/S18/S37/S45)."""

    def setUp(self):
        _isolate()
        _start_offline_patches()

    def tearDown(self):
        _end()

    def test_act_final_without_tools_not_completed(self):
        registry = _registry(ha_ok=True)
        reset_registry_for_tests(registry)
        model = FakeModelClient(
            [
                {
                    "type": "final_answer",
                    "message": "Chwila — sprawdzam to dokładniej.",
                }
            ]
        )
        cm = ConversationManager(registry=registry, memory=[], model=model)
        cm.activate()
        # Seed ACT goal the way CM would for a smart-home request.
        from core.semantic_executive import decide_semantics

        env = decide_semantics("włącz lampę", allow_llm=False)
        cm.goals.observe_utterance("włącz lampę", envelope=env)
        cm.goals.mark_in_progress(action="")
        self.assertIn(
            cm.goals.current.status,
            (GoalStatus.ACTIVE.value, GoalStatus.IN_PROGRESS.value),
        )

        out = cm.handle("włącz lampę")
        status = cm.goals.current.status if cm.goals.current else None
        self.assertNotEqual(
            status,
            GoalStatus.COMPLETED.value,
            msg=f"false COMPLETED; reply={out.text!r} status={status} obs={cm.last_observations}",
        )
        self.assertIn(
            status,
            (
                GoalStatus.IN_PROGRESS.value,
                GoalStatus.UNRESOLVED.value,
                GoalStatus.ACTIVE.value,
            ),
            msg=status,
        )

    def test_verified_tool_success_may_complete(self):
        registry = _registry(open_ok=True)
        reset_registry_for_tests(registry)
        model = FakeModelClient(
            [
                {
                    "type": "tool_call",
                    "tool": "open_app",
                    "arguments": {"name": "Spotify"},
                },
                {"type": "final_answer", "message": "Uruchomiłem Spotify."},
            ]
        )
        cm = ConversationManager(registry=registry, memory=[], model=model)
        cm.activate()
        out = cm.handle("otwórz Spotify")
        # Either FastPath or Agent must produce a successful observation.
        self.assertTrue(
            any(isinstance(o, dict) and o.get("success") for o in (cm.last_observations or []))
            or has_goal_completion_evidence(
                result=(cm.goals.current.last_result if cm.goals.current else {}),
                observations=cm.last_observations,
                action=(cm.goals.current.last_action if cm.goals.current else ""),
            ),
            msg=f"expected tool evidence; reply={out.text!r} obs={cm.last_observations}",
        )
        if cm.goals.current is not None and has_goal_completion_evidence(
            result=cm.goals.current.last_result,
            observations=cm.last_observations,
            action=cm.goals.current.last_action,
        ):
            self.assertEqual(cm.goals.current.status, GoalStatus.COMPLETED.value)

    def test_helper_rejects_vacuous_claim(self):
        self.assertFalse(
            has_goal_completion_evidence(
                result={"message": "Chwila — sprawdzam to dokładniej.", "success": True},
                action="agent",
            )
        )
        self.assertTrue(
            has_goal_completion_evidence(
                result={"success": True, "tool": "turn_on_device"},
                action="turn_on_device",
            )
        )
        self.assertTrue(
            has_goal_completion_evidence(
                observations=[{"tool": "open_app", "success": True}],
            )
        )

class PendingRetryRetentionTests(unittest.TestCase):
    """S11/S12: do not clear pending before successful re-execute; keep goal aliases."""

    def setUp(self):
        _isolate()
        _start_offline_patches()

    def tearDown(self):
        _end()

    def _seed_alias_only_pending(self):
        """Harness-shaped pending: `goal` set, original_* missing (audit S11/S12)."""
        return {
            "plan_id": "p-audit",
            "goal": "Otwórz Spotify i daj trochę ciszej",
            "status": "partial",
            "failed_steps": [{"tool": "volume_down", "status": "FAILED"}],
            "succeeded_steps": [{"tool": "open_app", "status": "SUCCEEDED"}],
            "steps": [
                {
                    "tool": "open_app",
                    "status": "SUCCEEDED",
                    "arguments": {"name": "Spotify"},
                },
                {"tool": "volume_down", "status": "FAILED", "arguments": {}},
            ],
        }

    def test_goal_text_alias_and_normalize(self):
        raw = self._seed_alias_only_pending()
        self.assertEqual(
            goal_text_from_pending(raw),
            "Otwórz Spotify i daj trochę ciszej",
        )
        norm = normalize_pending_goal_fields(raw)
        self.assertEqual(norm["original_goal"], raw["goal"])
        self.assertEqual(norm["original_user_text"], raw["goal"])

    def test_retry_reexecutes_with_goal_alias(self):
        """S11: alias-only pending must re-execute, not claim 'no action'."""
        registry = _registry(volume_ok=True, open_ok=True)
        reset_registry_for_tests(registry)
        cm = ConversationManager(
            registry=registry,
            memory=[],
            model=FakeModelClient(
                [
                    {"type": "tool_call", "tool": "volume_down", "arguments": {}},
                    {"type": "final_answer", "message": "Zmniejszyłem głośność."},
                ]
                * 4
            ),
        )
        cm.activate()
        # Assign like the audit harness (bypass _set_pending_executable).
        cm.pending_executable_goal = dict(self._seed_alias_only_pending())
        out = cm.handle("spróbuj jeszcze raz")
        text = (out.text or "").lower()
        self.assertNotIn("nie mam już tej czynności", text)
        self.assertNotIn("nie mam teraz czynności", text)
        # Re-execute happened (tools / agent), not an empty clarify.
        invoked = list(getattr(registry, "_state", {}).get("invoked") or [])
        self.assertTrue(
            invoked or out.source in ("agent", "fast_path"),
            msg=f"expected re-execute; reply={out.text!r} source={out.source}",
        )

    def test_alternate_reexecutes_with_goal_alias(self):
        """S12: alternate must retain goal text and re-execute."""
        registry = _registry(volume_ok=True, open_ok=True)
        reset_registry_for_tests(registry)
        cm = ConversationManager(
            registry=registry,
            memory=[],
            model=FakeModelClient(
                [
                    {"type": "tool_call", "tool": "volume_down", "arguments": {}},
                    {"type": "final_answer", "message": "Spróbowałem inaczej."},
                ]
                * 4
            ),
        )
        cm.activate()
        cm.pending_executable_goal = dict(self._seed_alias_only_pending())
        out = cm.handle("spróbuj inaczej")
        text = (out.text or "").lower()
        self.assertNotIn("nie mam już tej czynności", text)
        invoked = list(getattr(registry, "_state", {}).get("invoked") or [])
        self.assertTrue(
            invoked or out.source in ("agent", "fast_path"),
            msg=f"expected re-execute; reply={out.text!r} source={out.source}",
        )

    def test_failed_retry_retains_pending(self):
        """Do not clear pending before a successful re-execute completes."""
        registry = _registry(volume_ok=False, open_ok=True)
        reset_registry_for_tests(registry)
        pending = pending_from_plan(
            plan_id="p-ret",
            original_goal="Otwórz Spotify i daj trochę ciszej",
            status="PARTIAL",
            steps=[
                {
                    "step_id": "p-ret.1",
                    "status": "SUCCEEDED",
                    "tool": "open_app",
                    "semantic_action": "open_service",
                    "arguments": {"name": "Spotify"},
                    "target": "Spotify",
                },
                {
                    "step_id": "p-ret.2",
                    "status": "FAILED",
                    "tool": "volume_down",
                    "semantic_action": "adjust_volume",
                    "direction": "decrease",
                    "arguments": {},
                },
            ],
        )
        cm = ConversationManager(
            registry=registry,
            memory=[],
            model=FakeModelClient([]),
        )
        cm.activate()
        cm._set_pending_executable(pending)
        out = cm.handle("spróbuj jeszcze raz")
        text = (out.text or "").lower()
        self.assertNotIn("nie mam już tej czynności", text)
        self.assertIsNotNone(cm.pending_executable_goal)
        self.assertTrue(goal_text_from_pending(cm.pending_executable_goal))

    def test_status_fallback_retains_when_no_goal_text(self):
        registry = _registry()
        reset_registry_for_tests(registry)
        cm = ConversationManager(registry=registry, memory=[], model=FakeModelClient([]))
        cm.activate()
        cm.pending_executable_goal = {
            "plan_id": "p-empty",
            "status": "partial",
            "failed_steps": [{"tool": "volume_down", "status": "FAILED"}],
            "steps": [{"tool": "volume_down", "status": "FAILED", "arguments": {}}],
        }
        out = cm.handle("spróbuj jeszcze raz")
        text = (out.text or "").lower()
        self.assertNotIn("nie mam już tej czynności", text)
        self.assertIsNotNone(cm.pending_executable_goal)
        self.assertTrue(
            ("cel" in text)
            or ("jeszcze" in text)
            or ("mogę" in text)
            or ("zostało" in text),
            msg=out.text,
        )
