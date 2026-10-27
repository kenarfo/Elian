# ==========================
# ELIAN — PLAN → EXECUTE → VERIFY → REPAIR cycle tests
# ==========================
"""Offline cycle + FastPath regression. No mic / HA writes."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent_repair import decide_execution_repair
from core.fast_path import FastPathStatus, can_fast_path, evaluate_fast_path
from core.intent_family import IntentClassification, IntentFamily, classify_intent_family
from core.learned_routines import reset_routines_for_tests
from core.model_client import FakeModelClient, reset_model_client
from core.plan_cycle import (
    VerificationResult,
    RepairAction,
    VerifyStatus,
    consume_world_snapshot,
    enrich_compound_preconditions,
    guard_before_execute,
    material_uncertainty,
    run_execute_verify_repair,
    service_already_active,
    verification_allows_success_claim,
    verify_effect,
)
from core.planner import Planner
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from core.conversation_manager import ConversationManager


def _isolate_runtime():
    reset_routines_for_tests([])
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


def _end_isolate():
    from core import learned_routines as lr

    lr._TEST_STORE = None
    reset_registry_for_tests(None)
    reset_model_client()


def _classification(text: str, family: IntentFamily = IntentFamily.MEDIA, complete: bool = True, **slots):
    return IntentClassification(
        family=family,
        is_complete=complete,
        slots=dict(slots),
        raw_text=text,
    )


def _cycle_registry(*, play_ok=True, play_retry_ok=False, now_playing="Michael Jackson", fail_verify=False):
    registry = ToolRegistry(confirm_medium=False)
    state = {
        "play_calls": 0,
        "open_calls": 0,
        "volume_calls": 0,
        "verify_calls": 0,
        "now_playing": now_playing,
        "play_ok": play_ok,
        "play_retry_ok": play_retry_ok,
        "fail_verify": fail_verify,
        "open_ok": True,
        "volume_ok": True,
        "volume_level": 40.0,
        "volume_snap": {"level": 40.0, "scalar": 0.4, "mute": False, "step": 20, "steps": 50},
        "invoked": [],
    }

    def open_app(args):
        state["open_calls"] += 1
        state["invoked"].append({"tool": "open_app", "arguments": dict(args or {})})
        if not state["open_ok"]:
            return ToolResult.fail("Nie otworzyłem.", tool_name="open_app", retryable=True)
        return ToolResult.ok(
            f"Uruchomiłem {args.get('name')}.",
            tool_name="open_app",
            data={"app": args.get("name")},
        )

    def play_media(args):
        state["play_calls"] += 1
        state["invoked"].append({"tool": "play_media", "arguments": dict(args or {})})
        ok = state["play_ok"] or (state["play_retry_ok"] and state["play_calls"] > 1)
        if not ok:
            return ToolResult.fail("Nie odtworzyłem.", tool_name="play_media", retryable=True)
        query = str(args.get("query") or "")
        state["now_playing"] = query or state["now_playing"]
        return ToolResult.ok(
            f"Puścimy {query}.",
            tool_name="play_media",
            data=dict(args or {}),
        )

    def volume_down(_args):
        state["volume_calls"] += 1
        state["invoked"].append({"tool": "volume_down", "arguments": {}})
        if not state["volume_ok"]:
            return ToolResult.fail("Nie ściszyłem.", tool_name="volume_down", retryable=False)
        before = {"level": 50.0, "scalar": 0.5, "mute": False, "step": 25, "steps": 50}
        after = {"level": 40.0, "scalar": 0.4, "mute": False, "step": 20, "steps": 50}
        state["volume_level"] = after["level"]
        state["volume_snap"] = after
        return ToolResult.ok(
            "Zmniejszam głośność.",
            tool_name="volume_down",
            data={
                "action": "adjust_volume",
                "direction": "decrease",
                "volume_before": before,
                "volume_after": after,
            },
        )

    def volume_up(_args):
        state["volume_calls"] += 1
        state["invoked"].append({"tool": "volume_up", "arguments": {}})
        if not state["volume_ok"]:
            return ToolResult.fail("Nie podgłośniłem.", tool_name="volume_up", retryable=False)
        before = {"level": 40.0, "scalar": 0.4, "mute": False, "step": 20, "steps": 50}
        after = {"level": 50.0, "scalar": 0.5, "mute": False, "step": 25, "steps": 50}
        state["volume_level"] = after["level"]
        state["volume_snap"] = after
        return ToolResult.ok(
            "Zwiększam głośność.",
            tool_name="volume_up",
            data={
                "action": "adjust_volume",
                "direction": "increase",
                "volume_before": before,
                "volume_after": after,
            },
        )

    def get_volume(_args):
        state["invoked"].append({"tool": "get_volume", "arguments": dict(_args or {})})
        snap = dict(state.get("volume_snap") or {"level": 40.0, "scalar": 0.4, "mute": False, "step": 20, "steps": 50})
        return ToolResult.ok(
            f"Głośność {snap.get('level')}%.",
            tool_name="get_volume",
            data=snap,
        )

    def get_now_playing(_args):
        state["verify_calls"] += 1
        state["invoked"].append({"tool": "get_now_playing", "arguments": {}})
        if state["fail_verify"]:
            return ToolResult.ok(
                "Teraz gra coś innego.",
                tool_name="get_now_playing",
                data={"title": "Something Else", "artist": "Unknown"},
            )
        if state["play_calls"] == 0:
            return ToolResult.ok(
                "Nic nie gra.",
                tool_name="get_now_playing",
                data={"title": "", "artist": ""},
            )
        return ToolResult.ok(
            f"Gra {state['now_playing']}.",
            tool_name="get_now_playing",
            data={"title": state["now_playing"], "artist": state["now_playing"]},
        )

    def window_exists(args):
        state["verify_calls"] += 1
        state["invoked"].append({"tool": "window_exists", "arguments": dict(args or {})})
        name = str((args or {}).get("name") or (args or {}).get("query") or "")
        if state.get("fail_window"):
            return ToolResult.fail("Brak okna.", tool_name="window_exists")
        return ToolResult.ok(
            f"Okno {name} jest.",
            tool_name="window_exists",
            data={"exists": True, "name": name},
        )

    registry.register(Tool(
        name="open_app",
        description="Open app",
        handler=open_app,
        risk_level=RiskLevel.SAFE,
        parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    ))
    registry.register(Tool(
        name="play_media",
        description="Play",
        handler=play_media,
        risk_level=RiskLevel.SAFE,
        parameters={"type": "object", "properties": {"query": {"type": "string"}, "app": {"type": "string"}}, "required": ["query"]},
    ))
    registry.register(Tool(
        name="volume_down",
        description="Volume down",
        handler=volume_down,
        risk_level=RiskLevel.SAFE,
        parameters={"type": "object", "properties": {}},
    ))
    registry.register(Tool(
        name="volume_up",
        description="Volume up",
        handler=volume_up,
        risk_level=RiskLevel.SAFE,
        parameters={"type": "object", "properties": {}},
    ))
    registry.register(Tool(
        name="get_volume",
        description="Volume readback",
        handler=get_volume,
        risk_level=RiskLevel.SAFE,
        parameters={"type": "object", "properties": {}},
    ))
    registry.register(Tool(
        name="get_now_playing",
        description="Now playing",
        handler=get_now_playing,
        risk_level=RiskLevel.SAFE,
        parameters={"type": "object", "properties": {}},
    ))
    registry.register(Tool(
        name="window_exists",
        description="Window exists",
        handler=window_exists,
        risk_level=RiskLevel.SAFE,
        parameters={"type": "object", "properties": {"name": {"type": "string"}, "query": {"type": "string"}}},
    ))
    registry._state = state  # type: ignore[attr-defined]
    return registry


class WorldSnapshotHookTests(unittest.TestCase):

    def test_consume_world_snapshot_copies_and_falls_back(self):
        snap = consume_world_snapshot({"active_device": {"entity_id": "light.x"}, "active_service": {"name": "Spotify"}})
        self.assertIsInstance(snap, dict)
        snap["active_device"] = {"hack": True}
        again = consume_world_snapshot({"active_device": {"entity_id": "light.x"}})
        self.assertNotEqual(again.get("active_device"), {"hack": True})
        self.assertIn("source", snap)


class GuardAndAskTests(unittest.TestCase):

    def test_ambiguous_request_asks_not_guesses(self):
        self.assertIsNotNone(material_uncertainty("play_media", {}))
        info = _classification("puść to", IntentFamily.MEDIA, True)
        guard = guard_before_execute(
            tool_name="play_media",
            arguments={},
            classification=info,
            goal="puść to",
        )
        self.assertIsNotNone(guard)
        self.assertTrue(guard.ask)
        self.assertEqual(guard.repair.action, RepairAction.ASK)
        self.assertIn("puścić", guard.ask_question.lower())

    def test_safety_block_unknown_incomplete(self):
        info = classify_intent_family("eee")
        if info.family != IntentFamily.UNKNOWN:
            info = _classification("eee", IntentFamily.UNKNOWN, False)
        guard = guard_before_execute(
            tool_name="open_app",
            arguments={"name": "Spotify"},
            classification=info,
            goal="eee",
        )
        self.assertIsNotNone(guard)
        self.assertTrue(guard.blocked)
        self.assertEqual(guard.execution.block_reason, "safety_block")


class CycleUnitTests(unittest.TestCase):

    def setUp(self):
        _isolate_runtime()

    def tearDown(self):
        _end_isolate()

    def test_full_success_with_verify(self):
        registry = _cycle_registry()
        info = _classification(
            "puść Michaela Jacksona",
            IntentFamily.MEDIA,
            True,
            media_query="Michael Jackson",
        )
        out = run_execute_verify_repair(
            registry,
            tool_name="play_media",
            arguments={"query": "Michael Jackson"},
            classification=info,
            goal="puść Michaela Jacksona",
            confirmed=True,
        )
        self.assertTrue(out.execution.success)
        self.assertTrue(out.verified_success)
        self.assertEqual(out.verification.status, VerifyStatus.PASSED)
        self.assertEqual(out.repairs_used, 0)
        phases = [row["phase"] for row in out.trace]
        self.assertIn("execute", phases)
        self.assertIn("verify", phases)

    def test_tool_failure(self):
        registry = _cycle_registry(play_ok=False)
        info = _classification("puść MJ", IntentFamily.MEDIA, True, media_query="MJ")
        out = run_execute_verify_repair(
            registry,
            tool_name="play_media",
            arguments={"query": "MJ"},
            classification=info,
            goal="puść MJ",
            confirmed=True,
            max_repair=0,
        )
        self.assertFalse(out.execution.success)
        self.assertFalse(out.verified_success)

    def test_verify_failure(self):
        registry = _cycle_registry(fail_verify=True)
        info = _classification("puść MJ", IntentFamily.MEDIA, True, media_query="Michael Jackson")
        out = run_execute_verify_repair(
            registry,
            tool_name="play_media",
            arguments={"query": "Michael Jackson"},
            classification=info,
            goal="puść MJ",
            confirmed=True,
            max_repair=0,
        )
        self.assertTrue(out.execution.success)
        self.assertEqual(out.verification.status, VerifyStatus.FAILED)
        self.assertFalse(out.verified_success)

    def test_successful_repair(self):
        registry = _cycle_registry(play_ok=False, play_retry_ok=True)
        info = _classification("puść MJ", IntentFamily.MEDIA, True, media_query="Michael Jackson")
        out = run_execute_verify_repair(
            registry,
            tool_name="play_media",
            arguments={"query": "Michael Jackson"},
            classification=info,
            goal="puść MJ",
            confirmed=True,
            max_repair=2,
        )
        self.assertTrue(out.verified_success)
        self.assertGreaterEqual(out.repairs_used, 1)
        self.assertGreaterEqual(registry._state["play_calls"], 2)
        self.assertTrue(any(row.get("phase") == "repair" for row in out.trace))

    def test_repair_exhausted(self):
        registry = _cycle_registry(play_ok=False)
        info = _classification("puść MJ", IntentFamily.MEDIA, True, media_query="MJ")
        out = run_execute_verify_repair(
            registry,
            tool_name="play_media",
            arguments={"query": "MJ"},
            classification=info,
            goal="puść MJ",
            confirmed=True,
            max_repair=2,
        )
        self.assertFalse(out.verified_success)
        self.assertEqual(out.repairs_used, 2)
        decision = decide_execution_repair(
            tool_name="play_media",
            arguments={"query": "MJ"},
            execution=out.execution,
            verification=out.verification,
            classification=info,
            repair_used=2,
            max_repair=2,
        )
        self.assertEqual(decision.action, RepairAction.FAIL)
        self.assertEqual(decision.reason, "repair_exhausted")

    def test_does_not_remutate_verified_success(self):
        registry = _cycle_registry()
        info = _classification("puść MJ", IntentFamily.MEDIA, True, media_query="Michael Jackson")
        out = run_execute_verify_repair(
            registry,
            tool_name="play_media",
            arguments={"query": "Michael Jackson"},
            classification=info,
            goal="puść MJ",
            confirmed=True,
            prior_verified=True,
        )
        self.assertTrue(out.execution.skipped)
        self.assertEqual(registry._state["play_calls"], 0)
        decision = decide_execution_repair(
            tool_name="play_media",
            arguments={"query": "Michael Jackson"},
            execution=out.execution,
            verification=out.verification,
            classification=info,
        )
        self.assertEqual(decision.action, RepairAction.SKIP)
        self.assertEqual(decision.reason, "already_verified")


class PlannerCycleIntegrationTests(unittest.TestCase):

    def setUp(self):
        _isolate_runtime()
        self.registry = _cycle_registry()
        reset_registry_for_tests(self.registry)
        self.model = FakeModelClient([])
        self.planner = Planner(self.registry, model=self.model, max_steps=6)

    def tearDown(self):
        _end_isolate()

    def test_full_success_compound_mj_volume(self):
        result = self.planner.run(
            "puść Michaela Jacksona na Spotify i daj trochę ciszej",
            context={"raw_text": "puść Michaela Jacksona na Spotify i daj trochę ciszej"},
        )
        tools = [str(step.get("tool") or "") for step in result.steps]
        self.assertIn("play_media", tools)
        self.assertIn("volume_down", tools)
        self.assertTrue(any("michael" in str(step.get("arguments") or {}).lower() for step in result.steps))
        self.assertEqual(result.source, "compound")
        self.assertIn(result.plan_status, ("COMPLETED", "PARTIAL", ""))
        self.assertTrue(result.cycle_trace or result.steps)
        self.assertNotIn("Czy chcesz otworzyć aplikację", result.message)

    def test_partial_success(self):
        self.registry._state["volume_ok"] = False
        result = self.planner.run(
            "Otwórz Spotify i zwiększ głośność.",
            context={"raw_text": "Otwórz Spotify i zwiększ głośność."},
        )
        self.assertEqual(result.source, "compound")
        self.assertGreaterEqual(len(result.steps), 2)
        self.assertTrue(result.steps[0]["observation"]["success"])
        self.assertFalse(result.steps[1]["observation"]["success"])
        self.assertIn(result.plan_status, ("PARTIAL", "FAILED"))
        self.assertFalse(result.success)

    def test_tool_failure_open_then_volume(self):
        self.registry._state["open_ok"] = False
        result = self.planner.run(
            "Otwórz Spotify i zwiększ głośność.",
            context={"raw_text": "Otwórz Spotify i zwiększ głośność."},
        )
        self.assertEqual(result.source, "compound")
        self.assertFalse(result.steps[0]["observation"]["success"])

    def test_fastpath_not_stolen_spotify_app(self):
        result = self.planner.run("możesz odpalić mi spotify")
        mutation = [
            step for step in result.steps
            if str(step.get("tool") or "") not in ("get_now_playing", "window_exists", "list_windows")
        ]
        self.assertTrue(mutation)
        self.assertEqual(mutation[0]["tool"], "open_app")
        self.assertEqual(str((mutation[0].get("arguments") or {}).get("name") or "").lower(), "spotify")
        self.assertFalse(any(step.get("tool") == "play_media" for step in mutation))
        self.assertFalse(result.needs_clarification)

    def test_fastpath_not_stolen_michael_jackson_media(self):
        result = self.planner.run("puść coś michaela jacksona")
        mutation = [
            step for step in result.steps
            if str(step.get("tool") or "") not in ("get_now_playing", "window_exists", "list_windows")
        ]
        self.assertTrue(mutation)
        self.assertEqual(mutation[0]["tool"], "play_media")
        self.assertIn("michael", str(mutation[0].get("arguments") or {}).lower())
        self.assertFalse(result.needs_clarification)
        self.assertNotIn("otworzyć aplikację", (result.message or "").lower())


class FastPathRegressionTests(unittest.TestCase):

    def setUp(self):
        _isolate_runtime()
        self.registry = _cycle_registry()
        reset_registry_for_tests(self.registry)
        self.model = FakeModelClient([
            "broken json {{{",
            {"type": "clarification", "question": "Czy chcesz otworzyć aplikację?"},
        ])
        self.manager = ConversationManager(
            registry=self.registry,
            memory=[],
            model=self.model,
        )
        self.manager.activate()

    def tearDown(self):
        _end_isolate()

    def test_simple_commands_still_eligible_for_fastpath(self):
        spotify = evaluate_fast_path("możesz odpalić mi spotify")
        mj = evaluate_fast_path("puść coś michaela jacksona")
        compound = evaluate_fast_path("puść Michaela Jacksona na Spotify i daj trochę ciszej")
        self.assertNotEqual(compound.status, FastPathStatus.HANDLED_COMPLETE)
        self.assertTrue(
            can_fast_path("możesz odpalić mi spotify")
            or classify_intent_family("możesz odpalić mi spotify").family in (IntentFamily.APP, IntentFamily.MEDIA)
        )
        self.assertIn(spotify.status, (FastPathStatus.HANDLED_COMPLETE, FastPathStatus.NOT_HANDLED, FastPathStatus.PARTIAL_MATCH))
        self.assertIn(mj.status, (FastPathStatus.HANDLED_COMPLETE, FastPathStatus.NOT_HANDLED, FastPathStatus.PARTIAL_MATCH))

    def test_cm_spotify_no_generic_open_app_clarification(self):
        result = self.manager.handle("możesz odpalić mi spotify")
        self.assertNotEqual(result.source, "clarify")
        self.assertNotIn("Czy chcesz otworzyć aplikację", result.text or "")
        self.assertTrue(
            result.source in ("fast_path", "agent")
            or "Spotify" in (result.text or "")
        )

    def test_cm_michael_jackson_not_open_app_fallback(self):
        result = self.manager.handle("puść coś michaela jacksona")
        self.assertNotEqual(result.source, "clarify")
        self.assertNotIn("otworzyć aplikację", (result.text or "").lower())
        invoked = [item.get("tool") for item in self.registry._state["invoked"]]
        self.assertIn("play_media", invoked)
        self.assertNotEqual(invoked[:1], ["open_app"])

    def test_json_retry_does_not_fallback_to_open_app(self):
        with patch("core.conversation_manager.run_fast_path", return_value=None):
            result = self.manager.handle("puść coś michaela jacksona")
        self.assertNotIn("Czy chcesz otworzyć aplikację", result.text or "")
        tools = [item.get("tool") for item in self.registry._state["invoked"]]
        self.assertIn("play_media", tools)
        self.assertNotEqual(tools[:1], ["open_app"])


class EnrichPreconditionsTests(unittest.TestCase):

    def setUp(self):
        _isolate_runtime()

    def tearDown(self):
        _end_isolate()

    def test_media_with_named_app_adds_open_unless_world_active(self):
        from core.compound_plan import parse_compound_goal

        plan = parse_compound_goal("puść Michaela Jacksona na Spotify i daj trochę ciszej")
        if not plan.detected:
            self.skipTest("compound parser did not split example utterance")
        registry = _cycle_registry()
        enriched = enrich_compound_preconditions(plan, world={}, registry=registry)
        actions = [step.semantic_action for step in enriched.steps]
        self.assertIn("play_media", actions)
        self.assertIn("adjust_volume", actions)
        world_on = {
            "services": {
                "spotify": {"service_name": "Spotify", "name": "Spotify"},
            }
        }
        self.assertTrue(service_already_active(world_on, "Spotify"))
        plan2 = parse_compound_goal("puść Michaela Jacksona na Spotify i daj trochę ciszej")
        enriched2 = enrich_compound_preconditions(plan2, world=world_on, registry=registry)
        opens = [s for s in enriched2.steps if s.semantic_action == "open_service"]
        self.assertEqual(len(opens), 0)





class VerifyToolUnregisteredTests(unittest.TestCase):
    """Probe FAIL C: open_app must not claim success when verify tool missing."""

    def setUp(self):
        _isolate_runtime()

    def tearDown(self):
        _end_isolate()


    def test_skipped_unregistered_never_verified_success_gate(self):
        """Probe FAIL C: SKIPPED/FAILED verify_tool_unregistered must not set verified_success."""
        from core.plan_cycle import VerificationResult, verification_allows_success_claim
        skipped = VerificationResult(
            status=VerifyStatus.SKIPPED,
            tool="window_exists",
            reason="verify_tool_unregistered",
        )
        failed = VerificationResult(
            status=VerifyStatus.FAILED,
            tool="window_exists",
            reason="verify_tool_unregistered",
        )
        self.assertFalse(verification_allows_success_claim(skipped))
        self.assertFalse(verification_allows_success_claim(failed))
        # no_verify_spec must NOT allow success claim (S38/S42 fail-closed)
        self.assertFalse(
            verification_allows_success_claim(
                VerificationResult(status=VerifyStatus.SKIPPED, reason="no_verify_spec")
            )
        )
        # Intentional non_mutation skip still allowed
        self.assertTrue(
            verification_allows_success_claim(
                VerificationResult(status=VerifyStatus.SKIPPED, reason="non_mutation")
            )
        )

    def test_open_app_unregistered_verify_not_verified_success(self):
        registry = ToolRegistry(confirm_medium=False)

        def open_app(args):
            return ToolResult.ok(
                "Uruchomiłem Spotify.",
                tool_name="open_app",
                data={"app": args.get("name")},
            )

        registry.register(Tool(
            name="open_app",
            description="Open",
            handler=open_app,
            risk_level=RiskLevel.SAFE,
            parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        ))
        info = _classification("otwórz Spotify", IntentFamily.APP, True, app_name="Spotify")
        out = run_execute_verify_repair(
            registry,
            tool_name="open_app",
            arguments={"name": "Spotify"},
            classification=info,
            goal="otwórz Spotify",
            confirmed=True,
            max_repair=0,
        )
        self.assertTrue(out.execution.success)
        # Unregistered required verify is FAIL (not intentional SKIPPED) — no success claim.
        self.assertEqual(out.verification.status, VerifyStatus.FAILED)
        self.assertEqual(out.verification.reason, "verify_tool_unregistered")
        self.assertFalse(out.verified_success)
        self.assertFalse(verification_allows_success_claim(out.verification))

    def test_open_app_with_window_exists_verified(self):
        registry = _cycle_registry()
        info = _classification("otwórz Spotify", IntentFamily.APP, True, app_name="Spotify")
        out = run_execute_verify_repair(
            registry,
            tool_name="open_app",
            arguments={"name": "Spotify"},
            classification=info,
            goal="otwórz Spotify",
            confirmed=True,
        )
        self.assertTrue(out.execution.success)
        self.assertEqual(out.verification.status, VerifyStatus.PASSED)
        self.assertTrue(out.verified_success)

    def test_verify_effect_unregistered_reason(self):
        registry = ToolRegistry(confirm_medium=False)
        ver = verify_effect(
            registry,
            tool_name="open_app",
            arguments={"name": "Spotify"},
        )
        self.assertEqual(ver.status, VerifyStatus.FAILED)
        self.assertEqual(ver.reason, "verify_tool_unregistered")
        self.assertFalse(verification_allows_success_claim(ver))




class VolumeNoVerifyFixTests(unittest.TestCase):
    """Probe S38/S42: volume must not claim success without OS volume observation."""

    def setUp(self):
        _isolate_runtime()

    def tearDown(self):
        _end_isolate()

    def test_verify_spec_maps_volume_to_get_volume(self):
        from core.plan_cycle import verify_spec_for

        self.assertEqual(verify_spec_for("volume_down", {}), ("get_volume", {}))
        self.assertEqual(verify_spec_for("volume_up", {}), ("get_volume", {}))

    def test_no_verify_spec_never_allows_success_claim(self):
        skipped = VerificationResult(status=VerifyStatus.SKIPPED, reason="no_verify_spec")
        self.assertFalse(verification_allows_success_claim(skipped))

    def test_volume_down_with_get_volume_verified_success(self):
        registry = _cycle_registry()
        info = _classification("ciszej", IntentFamily.SYSTEM, True)
        out = run_execute_verify_repair(
            registry,
            tool_name="volume_down",
            arguments={},
            classification=info,
            goal="ciszej",
            confirmed=True,
            max_repair=0,
        )
        self.assertTrue(out.execution.success)
        self.assertEqual(out.verification.status, VerifyStatus.PASSED)
        self.assertTrue(out.verified_success)
        tools = [x.get("tool") for x in registry._state["invoked"]]
        self.assertIn("volume_down", tools)
        self.assertIn("get_volume", tools)

    def test_volume_without_get_volume_not_verified_even_if_execute_ok(self):
        registry = ToolRegistry(confirm_medium=False)

        def volume_down(_args):
            return ToolResult.ok(
                "Zmniejszam głośność.",
                tool_name="volume_down",
                data={
                    "action": "adjust_volume",
                    "direction": "decrease",
                    # no before/after → cannot prove via observation alone
                },
            )

        registry.register(Tool(
            name="volume_down",
            description="Volume down",
            handler=volume_down,
            risk_level=RiskLevel.SAFE,
            parameters={"type": "object", "properties": {}},
        ))
        info = _classification("ciszej", IntentFamily.SYSTEM, True)
        out = run_execute_verify_repair(
            registry,
            tool_name="volume_down",
            arguments={},
            classification=info,
            goal="ciszej",
            confirmed=True,
            max_repair=0,
        )
        self.assertTrue(out.execution.success)
        self.assertEqual(out.verification.status, VerifyStatus.FAILED)
        self.assertEqual(out.verification.reason, "verify_tool_unregistered")
        self.assertFalse(out.verified_success)
        self.assertFalse(verification_allows_success_claim(out.verification))

    def test_volume_embedded_before_after_passes_without_get_volume(self):
        """HA-style embedded observation still proves effect when verify tool missing."""
        registry = ToolRegistry(confirm_medium=False)

        def volume_down(_args):
            return ToolResult.ok(
                "Zmniejszam głośność.",
                tool_name="volume_down",
                data={
                    "action": "adjust_volume",
                    "direction": "decrease",
                    "volume_before": {"level": 50.0, "step": 25, "steps": 50},
                    "volume_after": {"level": 40.0, "step": 20, "steps": 50},
                },
            )

        registry.register(Tool(
            name="volume_down",
            description="Volume down",
            handler=volume_down,
            risk_level=RiskLevel.SAFE,
            parameters={"type": "object", "properties": {}},
        ))
        info = _classification("ciszej", IntentFamily.SYSTEM, True)
        out = run_execute_verify_repair(
            registry,
            tool_name="volume_down",
            arguments={},
            classification=info,
            goal="ciszej",
            confirmed=True,
            max_repair=0,
        )
        self.assertTrue(out.execution.success)
        self.assertEqual(out.verification.status, VerifyStatus.PASSED)
        self.assertTrue(out.verified_success)


if __name__ == "__main__":
    unittest.main()
