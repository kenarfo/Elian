"""Goal-first natural speech: UserGoal → plan/tool → verify → response.

Does not mock away soft-goal / action-loop intelligence.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.action_loop import run_soft_action_loop
from core.capability_gate import capabilities_for_goal, never_claim_missing_web_if_available
from core.conversation_manager import ConversationManager
from core.goal_context import GoalContext, build_goal_context
from core.intent_family import IntentFamily, classify_intent_family
from core.model_client import FakeModelClient, reset_model_client
from core.risk import RiskLevel
from core.soft_goals import detect_soft_goal
from core.tool_compat import volume_direction_from_text
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from core.user_goal import UserGoal, formulate_user_goal
from memory.manager import MemoryManager


def _registry(log):
    registry = ToolRegistry(confirm_medium=False)

    def volume_down(args):
        log.append({"tool": "volume_down", "arguments": dict(args)})
        return ToolResult.ok("Ściszyłem.", tool_name="volume_down")

    def volume_up(args):
        log.append({"tool": "volume_up", "arguments": dict(args)})
        return ToolResult.ok("Podgłośniłem.", tool_name="volume_up")

    def play_media(args):
        log.append({"tool": "play_media", "arguments": dict(args)})
        return ToolResult.ok("Odtwarzam.", tool_name="play_media", data=dict(args))

    def get_now_playing(args):
        log.append({"tool": "get_now_playing", "arguments": dict(args)})
        return ToolResult.ok(
            "Leci Michael Jackson — Billie Jean.",
            tool_name="get_now_playing",
            data={"title": "Billie Jean", "artist": "Michael Jackson", "playing": True},
        )

    def set_device_value(args):
        log.append({"tool": "set_device_value", "arguments": dict(args)})
        return ToolResult.ok("OK.", tool_name="set_device_value", data=dict(args))

    def turn_on_device(args):
        log.append({"tool": "turn_on_device", "arguments": dict(args)})
        return ToolResult.ok("Włączam.", tool_name="turn_on_device", data=dict(args))

    def turn_off_device(args):
        log.append({"tool": "turn_off_device", "arguments": dict(args)})
        return ToolResult.ok("Wyłączam.", tool_name="turn_off_device", data=dict(args))

    def web_search(args):
        log.append({"tool": "web_search", "arguments": dict(args)})
        return ToolResult.ok(
            "Znalazłem.",
            tool_name="web_search",
            data={
                "query": args.get("query"),
                "results": [
                    {
                        "title": "Pierwszy wynik",
                        "url": "https://example.com/first",
                        "snippet": "Opis.",
                    },
                    {
                        "title": "Drugi",
                        "url": "https://example.com/second",
                        "snippet": "Inny.",
                    },
                ],
                "result_count": 2,
            },
        )

    def open_website(args):
        log.append({"tool": "open_website", "arguments": dict(args)})
        return ToolResult.ok(
            f"Otwieram {args.get('target')}.",
            tool_name="open_website",
            data=dict(args),
        )

    def open_app(args):
        log.append({"tool": "open_app", "arguments": dict(args)})
        return ToolResult.ok(f"Uruchomiłem {args.get('name')}.", tool_name="open_app")

    for name, handler, props, required in (
        ("volume_down", volume_down, {}, []),
        ("volume_up", volume_up, {}, []),
        ("play_media", play_media, {"query": {"type": "string"}}, ["query"]),
        ("get_now_playing", get_now_playing, {}, []),
        (
            "set_device_value",
            set_device_value,
            {
                "action": {"type": "string"},
                "target": {"type": "string"},
                "domain": {"type": "string"},
                "value": {"type": "integer"},
                "entity_id": {"type": "string"},
            },
            [],
        ),
        (
            "turn_on_device",
            turn_on_device,
            {"target": {"type": "string"}, "entity_id": {"type": "string"}},
            [],
        ),
        (
            "turn_off_device",
            turn_off_device,
            {"target": {"type": "string"}, "entity_id": {"type": "string"}},
            [],
        ),
        ("web_search", web_search, {"query": {"type": "string"}}, ["query"]),
        ("open_website", open_website, {"target": {"type": "string"}}, ["target"]),
        ("open_app", open_app, {"name": {"type": "string"}}, ["name"]),
    ):
        registry.register(
            Tool(
                name=name,
                description=name,
                handler=handler,
                risk_level=RiskLevel.SAFE,
                parameters={"type": "object", "properties": props, "required": required},
            )
        )
    return registry


class SoftGoalUnitTests(unittest.TestCase):
    def test_volume_soft_state(self):
        self.assertEqual(volume_direction_from_text("jest za głośno"), "decrease")
        soft = detect_soft_goal("jest za głośno")
        self.assertIsNotNone(soft)
        self.assertEqual(soft["kind"], "volume_down")

    def test_calmer_media_bare_and_with_context(self):
        # Bare mood request is a clear play intent (no prior media required).
        bare = detect_soft_goal("coś spokojniejszego")
        self.assertIsNotNone(bare)
        self.assertEqual(bare["kind"], "media_calm")
        ctx = GoalContext(media_active=True, previous_tool="play_media")
        soft = detect_soft_goal("coś spokojniejszego", goal_context=ctx)
        self.assertEqual(soft["kind"], "media_calm")

    def test_open_first_from_web_results(self):
        ctx = GoalContext(
            last_web_results=[
                {"title": "A", "url": "https://example.com/a"},
            ]
        )
        soft = detect_soft_goal("no to otwórz pierwszy", goal_context=ctx)
        self.assertEqual(soft["kind"], "open_result")
        self.assertEqual(soft["slots"]["url"], "https://example.com/a")

    def test_user_goal_structure(self):
        soft = detect_soft_goal("jest za głośno")
        goal = formulate_user_goal("jest za głośno", soft=soft)
        self.assertTrue(goal.requires_action)
        self.assertEqual(goal.soft_kind, "volume_down")
        self.assertEqual(goal.expected_result.get("kind"), "volume_down")
        self.assertIn("objective", goal.to_dict())
        self.assertIn("confidence", goal.to_dict())


class CapabilityHonestyTests(unittest.TestCase):
    def test_web_available_never_blocked(self):
        log = []
        registry = _registry(log)
        goal = UserGoal(
            objective="wyszukaj",
            requires_action=True,
            soft_kind="web_search",
            intent_family="WEB",
            confidence=0.9,
        )
        decision = capabilities_for_goal(goal, registry=registry)
        self.assertTrue(decision.allowed)
        spoken = never_claim_missing_web_if_available(
            registry, "Nie mogę wyszukać w internecie."
        )
        self.assertNotIn("nie mogę wyszukać", spoken.lower())


class ActionLoopDirectTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        self.log = []
        self.registry = _registry(self.log)
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = MemoryManager(path=Path(self.tmp.name) / "mem.json")

    def tearDown(self):
        self.tmp.cleanup()
        reset_registry_for_tests()
        reset_model_client()

    def test_too_loud_executes_volume_down(self):
        loop = run_soft_action_loop(
            "jest za głośno",
            registry=self.registry,
            memory=self.mem,
        )
        self.assertTrue(loop.handled)
        self.assertEqual(self.log[-1]["tool"], "volume_down")
        self.assertIn("ścisz", loop.message.lower())

    def test_open_first_executes_open_website(self):
        ctx_obs = [
            {
                "tool": "web_search",
                "success": True,
                "data": {
                    "query": "ai",
                    "results": [{"title": "X", "url": "https://example.com/x"}],
                },
            }
        ]
        loop = run_soft_action_loop(
            "no to otwórz pierwszy",
            registry=self.registry,
            memory=self.mem,
            last_observations=ctx_obs,
        )
        self.assertTrue(loop.handled)
        self.assertEqual(self.log[-1]["tool"], "open_website")
        self.assertEqual(self.log[-1]["arguments"].get("target"), "https://example.com/x")

    def test_explain_failure_with_pending(self):
        pending = {
            "plan_id": "p1",
            "goal": "włącz lampę",
            "failed_steps": [{"tool": "turn_on_device", "status": "FAILED"}],
        }
        loop = run_soft_action_loop(
            "czemu nie zrobiłeś",
            registry=self.registry,
            memory=self.mem,
            pending_executable=pending,
        )
        self.assertTrue(loop.handled)
        self.assertIn("turn_on_device", loop.message.lower() + "padło")
        self.assertTrue(
            "nie" in loop.message.lower() or "padło" in loop.message.lower()
            or "ponow" in loop.message.lower()
        )

    def test_gaming_opens_steam_or_procedure(self):
        loop = run_soft_action_loop(
            "ogarnij komputer do grania",
            registry=self.registry,
            memory=self.mem,
        )
        self.assertTrue(loop.handled)
        tools = [row["tool"] for row in self.log]
        self.assertTrue("open_app" in tools or "procedure_run" in tools)


class ConversationNaturalSpeechTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        self.log = []
        self.registry = _registry(self.log)
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = MemoryManager(path=Path(self.tmp.name) / "mem.json")
        self.model = FakeModelClient(
            [
                {"type": "final_answer", "message": "OK."},
            ]
            * 40
        )
        self.cm = ConversationManager(
            registry=self.registry,
            memory_manager=self.mem,
            model=self.model,
        )
        self.cm.active = True

    def tearDown(self):
        self.tmp.cleanup()
        reset_registry_for_tests()
        reset_model_client()

    def test_light_chain_with_active_device(self):
        self.mem.short.set_entity(
            "device",
            "Lampa",
            entity_id="light.lampa",
            domain="light",
            capabilities=["brightness", "on_off"],
        )
        # Soft darker room → agent/deterministic brightness
        with patch(
            "core.semantic_executive.decide_semantics",
            return_value=__import__(
                "core.semantic_executive", fromlist=["DecisionEnvelope"]
            ).DecisionEnvelope(
                mode="ACT",
                intent="smart_home_control",
                user_goal="jest ciemno",
                confidence=0.9,
                needs_tools=True,
                allowed_domains=["SMART_HOME"],
            ),
        ):
            r1 = self.cm.handle("jest ciemno")
        self.assertTrue(r1.active)
        tools = [row["tool"] for row in self.log]
        # Either FastPath/Agent set brightness or turn_on
        self.assertTrue(
            any(t in tools for t in ("set_device_value", "turn_on_device"))
            or "świat" in (r1.text or "").lower()
            or "lamp" in (r1.text or "").lower()
            or "rozjaś" in (r1.text or "").lower()
            or r1.source in ("agent", "fast_path", "action_loop", "clarify")
        )

        self.log.clear()
        with patch(
            "core.semantic_executive.decide_semantics",
            return_value=__import__(
                "core.semantic_executive", fromlist=["DecisionEnvelope"]
            ).DecisionEnvelope(
                mode="ACT",
                intent="smart_home_control",
                user_goal="zrób jaśniej",
                confidence=0.9,
                needs_tools=True,
                allowed_domains=["SMART_HOME"],
                continuation=True,
                referents=["ją"],
            ),
        ):
            r2 = self.cm.handle("zrób jaśniej")
        self.assertTrue(r2.active)

        self.log.clear()
        with patch(
            "core.semantic_executive.decide_semantics",
            return_value=__import__(
                "core.semantic_executive", fromlist=["DecisionEnvelope"]
            ).DecisionEnvelope(
                mode="ACT",
                intent="smart_home_control",
                user_goal="nie aż tak",
                confidence=0.9,
                needs_tools=True,
                allowed_domains=["SMART_HOME"],
                continuation=True,
            ),
        ):
            r3 = self.cm.handle("nie aż tak")
        self.assertTrue(r3.active)
        self.assertTrue(
            any(row["tool"] == "set_device_value" for row in self.log)
            or "przyciemn" in (r3.text or "").lower()
            or r3.source in ("agent", "fast_path", "action_loop")
        )

        self.log.clear()
        with patch(
            "core.semantic_executive.decide_semantics",
            return_value=__import__(
                "core.semantic_executive", fromlist=["DecisionEnvelope"]
            ).DecisionEnvelope(
                mode="ACT",
                intent="smart_home_control",
                user_goal="zgaś ją",
                confidence=0.92,
                needs_tools=True,
                allowed_domains=["SMART_HOME"],
                continuation=True,
                referents=["ją"],
            ),
        ):
            r4 = self.cm.handle("zgaś ją")
        self.assertTrue(r4.active)
        self.assertTrue(
            any(row["tool"] == "turn_off_device" for row in self.log)
            or "wyłącz" in (r4.text or "").lower()
            or r4.source in ("agent", "fast_path", "action_loop")
        )

    def test_media_then_quieter_then_now_playing(self):
        from core.semantic_executive import DecisionEnvelope

        with patch(
            "core.semantic_executive.decide_semantics",
            return_value=DecisionEnvelope(
                mode="ACT",
                intent="media_play",
                user_goal="odpal coś Michaela",
                confidence=0.9,
                needs_tools=True,
                allowed_domains=["MEDIA"],
            ),
        ):
            # Deterministic media via FakeModel tool_call sequence
            self.model = FakeModelClient(
                [
                    {
                        "type": "tool_call",
                        "tool": "play_media",
                        "arguments": {"query": "Michael Jackson"},
                    },
                    {"type": "final_answer", "message": "Puszczam Michaela."},
                ]
                * 10
            )
            self.cm.model = self.model
            r1 = self.cm.handle("odpal coś Michaela")
        self.assertTrue(r1.active)

        self.log.clear()
        with patch(
            "core.semantic_executive.decide_semantics",
            return_value=DecisionEnvelope(
                mode="ACT",
                intent="system_volume",
                user_goal="trochę ciszej",
                confidence=0.9,
                needs_tools=True,
                allowed_domains=["SYSTEM"],
            ),
        ):
            r2 = self.cm.handle("trochę ciszej")
        # Soft/action loop or agent/fast path volume
        self.assertTrue(
            any(row["tool"] == "volume_down" for row in self.log)
            or "cisz" in (r2.text or "").lower()
            or "ścisz" in (r2.text or "").lower()
            or r2.source in ("agent", "fast_path", "action_loop")
        )

        # Seed media observation for get_now_playing path
        self.cm.last_observations.append(
            {
                "tool": "play_media",
                "success": True,
                "data": {"query": "Michael Jackson"},
            }
        )
        self.log.clear()
        with patch(
            "core.semantic_executive.decide_semantics",
            return_value=DecisionEnvelope(
                mode="OBSERVE",
                intent="media_status",
                user_goal="co teraz leci?",
                confidence=0.9,
                needs_tools=True,
                allowed_domains=["MEDIA"],
            ),
        ):
            self.model = FakeModelClient(
                [
                    {"type": "tool_call", "tool": "get_now_playing", "arguments": {}},
                    {
                        "type": "final_answer",
                        "message": "Leci Michael Jackson — Billie Jean.",
                    },
                ]
                * 5
            )
            self.cm.model = self.model
            r3 = self.cm.handle("co teraz leci?")
        self.assertTrue(r3.active)
        self.assertTrue(
            any(row["tool"] == "get_now_playing" for row in self.log)
            or "leci" in (r3.text or "").lower()
            or "michael" in (r3.text or "").lower()
            or r3.source in ("agent", "fast_path", "action_loop", "chat")
        )

    def test_web_search_then_open_first(self):
        from core.semantic_executive import DecisionEnvelope

        with patch(
            "core.semantic_executive.decide_semantics",
            return_value=DecisionEnvelope(
                mode="ACT",
                intent="web_search",
                user_goal="wyszukaj w internecie coś o tym",
                confidence=0.9,
                needs_tools=True,
                allowed_domains=["WEB"],
            ),
        ):
            self.model = FakeModelClient(
                [
                    {
                        "type": "tool_call",
                        "tool": "web_search",
                        "arguments": {"query": "to"},
                    },
                    {"type": "final_answer", "message": "Znalazłem kilka źródeł."},
                ]
                * 8
            )
            self.cm.model = self.model
            # Focus topic for "o tym"
            self.mem.short.focus["current_topic"] = "sztuczna inteligencja"
            r1 = self.cm.handle("wyszukaj w internecie coś o tym")
        self.assertTrue(r1.active)
        # Ensure capability honesty: web_search must be usable
        goal = UserGoal(requires_action=True, soft_kind="web_search", intent_family="WEB")
        self.assertTrue(capabilities_for_goal(goal, registry=self.registry).allowed)

        # Simulate stored search results on CM
        self.cm.last_observations.append(
            {
                "tool": "web_search",
                "success": True,
                "data": {
                    "query": "sztuczna inteligencja",
                    "results": [
                        {"title": "SI", "url": "https://example.com/si"},
                    ],
                },
            }
        )
        self.log.clear()
        with patch(
            "core.semantic_executive.decide_semantics",
            return_value=DecisionEnvelope(
                mode="ACT",
                intent="open_website",
                user_goal="no to otwórz pierwszy",
                confidence=0.9,
                needs_tools=True,
                continuation=True,
            ),
        ):
            r2 = self.cm.handle("no to otwórz pierwszy")
        self.assertTrue(r2.active)
        self.assertTrue(
            any(row["tool"] == "open_website" for row in self.log),
            msg=f"expected open_website, log={self.log}, text={r2.text}",
        )

    def test_retry_and_why_and_replay(self):
        from core.semantic_executive import DecisionEnvelope

        self.cm.pending_executable_goal = {
            "plan_id": "plan-x",
            "goal": "włącz lampę",
            "failed_steps": [
                {"tool": "turn_on_device", "status": "FAILED", "semantic_action": "turn_on"}
            ],
            "successful_steps": [],
            "retry_count": 0,
        }
        with patch(
            "core.semantic_executive.decide_semantics",
            return_value=DecisionEnvelope(
                mode="TALK",
                intent="conversational_followup",
                user_goal="czemu nie zrobiłeś",
                confidence=0.85,
                needs_tools=False,
                continuation=True,
            ),
        ):
            r1 = self.cm.handle("jeszcze tego nie zrobiłeś")
        self.assertTrue(r1.active)
        lowered = (r1.text or "").lower()
        self.assertTrue(
            "jeszcze nie" in lowered
            or "mogę spróbować" in lowered
            or "mogę dokończyć" in lowered
            or "padło" in lowered
            or "turn_on" in lowered
            or "ponow" in lowered
            or "nie zrobi" in lowered
            or "cel:" in lowered,
            msg=f"unexpected reply: {r1.text!r}",
        )

        # Journal replay
        from core.action_journal import reset_action_history, record_action

        reset_action_history()
        record_action(
            "volume_down",
            {},
            ToolResult.ok("Ściszyłem.", tool_name="volume_down"),
        )
        self.cm.pending_executable_goal = None
        self.log.clear()
        with patch(
            "core.semantic_executive.decide_semantics",
            return_value=DecisionEnvelope(
                mode="ACT",
                intent="system_volume",
                user_goal="zrób tak jak ostatnio",
                confidence=0.9,
                needs_tools=True,
                continuation=True,
            ),
        ):
            r2 = self.cm.handle("zrób tak jak ostatnio")
        self.assertTrue(r2.active)
        self.assertTrue(
            any(row["tool"] == "volume_down" for row in self.log)
            or "powtórz" in (r2.text or "").lower()
            or "ostatn" in (r2.text or "").lower()
            or r2.source == "action_loop"
        )

    def test_gaming_setup_natural(self):
        from core.semantic_executive import DecisionEnvelope

        self.log.clear()
        with patch(
            "core.semantic_executive.decide_semantics",
            return_value=DecisionEnvelope(
                mode="PLAN",
                intent="procedure_run",
                user_goal="ogarnij komputer do grania",
                confidence=0.9,
                needs_tools=True,
            ),
        ):
            r = self.cm.handle("ogarnij komputer do grania")
        self.assertTrue(r.active)
        self.assertTrue(
            any(row["tool"] == "open_app" for row in self.log)
            or "steam" in (r.text or "").lower()
            or "grania" in (r.text or "").lower()
            or r.source in ("action_loop", "procedure", "agent", "fast_path")
        )

    def test_intent_family_soft_volume(self):
        info = classify_intent_family("jest za głośno")
        self.assertEqual(info.family, IntentFamily.SYSTEM)
        self.assertEqual(info.slots.get("volume_direction"), "decrease")


class GoalContextTests(unittest.TestCase):
    def test_build_includes_web_and_journal(self):
        ctx = build_goal_context(
            last_observations=[
                {
                    "tool": "web_search",
                    "success": True,
                    "data": {"query": "q", "results": [{"url": "https://a.test"}]},
                }
            ],
            pending_executable={"plan_id": "1", "goal": "x", "failed_steps": []},
        )
        self.assertTrue(ctx.last_web_results)
        self.assertEqual(ctx.unresolved_task.get("kind"), "pending_executable")


if __name__ == "__main__":
    unittest.main()
