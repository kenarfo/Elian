"""Natural multi-turn UX — soft goals + discourse, no semantic mocks."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.conversation_manager import ConversationManager
from core.model_client import FakeModelClient, reset_model_client
from core.risk import RiskLevel
from core.soft_goals import detect_soft_goal
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from core.utterance_class import UtteranceClass, classify_utterance
from memory.manager import MemoryManager


def _registry(log):
    registry = ToolRegistry(confirm_medium=False)

    def make(name, msg):
        def handler(args, _n=name, _m=msg):
            data = dict(args or {})
            if _n == "web_search":
                data = {
                    "query": args.get("query"),
                    "results": [
                        {"title": "Pierwszy", "url": "https://example.com/1", "snippet": "x"}
                    ],
                    "ranked_results": [
                        {"title": "Pierwszy", "url": "https://example.com/1", "snippet": "x"}
                    ],
                    "result_count": 1,
                }
            if _n == "get_now_playing":
                data = {"title": "Billie Jean", "artist": "MJ", "playing": True}
            log.append({"tool": _n, "arguments": dict(args or {})})
            return ToolResult.ok(_m, tool_name=_n, data=data)

        return handler

    specs = [
        ("volume_down", "Ściszyłem.", {}, []),
        ("volume_up", "Podgłośniłem.", {}, []),
        ("play_media", "Odtwarzam.", {"query": {"type": "string"}}, ["query"]),
        ("get_now_playing", "Leci X.", {}, []),
        (
            "set_device_value",
            "OK.",
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
            "Włączam.",
            {
                "target": {"type": "string"},
                "entity_id": {"type": "string"},
                "domain": {"type": "string"},
                "utterance": {"type": "string"},
            },
            [],
        ),
        ("web_search", "Znalazłem.", {"query": {"type": "string"}}, ["query"]),
        ("open_website", "Otwieram.", {"target": {"type": "string"}}, ["target"]),
        ("open_app", "Uruchomiłem.", {"name": {"type": "string"}}, ["name"]),
    ]
    for name, msg, props, req in specs:
        registry.register(
            Tool(
                name=name,
                description=name,
                handler=make(name, msg),
                risk_level=RiskLevel.SAFE,
                parameters={"type": "object", "properties": props, "required": req},
            )
        )
    return registry


class DiscourseShapeTests(unittest.TestCase):
    def test_relative_dampen_with_goal(self):
        u = classify_utterance(
            "nie aż tak",
            conversation={"has_active_goal": True, "active_parameter": "brightness"},
        )
        self.assertEqual(u.utterance_class, UtteranceClass.CHANGE_PARAMETER.value)

    def test_act_on_open_goal(self):
        u = classify_utterance(
            "weź coś z tym zrób",
            conversation={"has_active_goal": True},
        )
        self.assertEqual(u.utterance_class, UtteranceClass.CONFIRMATION.value)

    def test_gaming_soft_with_mi(self):
        soft = detect_soft_goal("ogarnij mi komputer do grania")
        self.assertIsNotNone(soft)
        self.assertEqual(soft["kind"], "gaming_setup")

    def test_media_now_and_vague_play(self):
        self.assertEqual(detect_soft_goal("co teraz leci")["kind"], "media_now")
        self.assertEqual(detect_soft_goal("odpal coś")["kind"], "media_play")


class NaturalMultiTurnCMTests(unittest.TestCase):
    def setUp(self):
        reset_registry_for_tests()
        reset_model_client()
        self.log = []
        self.registry = _registry(self.log)
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = MemoryManager(path=Path(self.tmp.name) / "mem.json")
        self.mem.short.set_entity(
            "device",
            "Lampa",
            entity_id="light.lampa",
            domain="light",
            capabilities=["brightness", "on_off"],
        )
        self.mem.short.focus["current_topic"] = "sztuczna inteligencja"
        self.cm = ConversationManager(
            registry=self.registry,
            memory_manager=self.mem,
            model=FakeModelClient([{"type": "final_answer", "message": "OK z modelu."}] * 40),
        )
        self.cm.active = True

    def tearDown(self):
        self.tmp.cleanup()
        reset_registry_for_tests()
        reset_model_client()

    def test_dark_room_chain(self):
        r1 = self.cm.handle("jest trochę ciemno")
        # First wish may clarify which light, or act if already resolved.
        self.assertTrue(
            r1.source in ("action_loop", "agent", "clarify", "fast_path")
            or "świat" in (r1.text or "").lower()
            or "rozjaś" in (r1.text or "").lower()
            or "lamp" in (r1.text or "").lower()
        )
        # Seed resolved lighting goal for follow-ups (as after a successful first act).
        self.cm._user_goal = __import__(
            "core.user_goal", fromlist=["UserGoal"]
        ).UserGoal(
            objective="Rozjaśnij — jest ciemno",
            soft_kind="brightness_up",
            intent_family="SMART_HOME",
            requires_action=True,
            confidence=0.9,
            slots={
                "implicit_light": True,
                "desired": "brighter",
                "domain": "light",
                "entity_id": "light.lampa",
                "resolved_context": True,
            },
            target="light.lampa",
        )
        self.cm.goals.observe_utterance(
            "jest trochę ciemno",
            envelope=getattr(self.cm, "_decision_envelope", None),
        )
        if self.cm.goals.current is not None:
            self.cm.goals.current.domain = "SMART_HOME"
            self.cm.goals.current.parameter = "brightness"
            self.cm.goals.current.status = "ACTIVE"
        self.log.clear()
        r2 = self.cm.handle("weź coś z tym zrób")
        self.assertTrue(r2.source in ("action_loop", "agent", "fast_path"))
        self.assertTrue(
            any(x["tool"] == "set_device_value" for x in self.log)
            or "rozjaś" in (r2.text or "").lower()
        )
        self.log.clear()
        r3 = self.cm.handle("nie aż tak")
        self.assertTrue(
            "przyciemn" in (r3.text or "").lower()
            or any(x["tool"] == "set_device_value" for x in self.log)
        )

    def test_web_then_open_first(self):
        r1 = self.cm.handle("wyszukaj mi coś o tym")
        self.assertTrue(any(x["tool"] == "web_search" for x in self.log))
        query = self.log[-1]["arguments"].get("query") or ""
        self.assertIn("inteligenc", query.lower())
        self.log.clear()
        r2 = self.cm.handle("otwórz pierwszy")
        self.assertTrue(any(x["tool"] == "open_website" for x in self.log))
        self.assertIn("example.com", str(self.log[-1]["arguments"]))

    def test_media_chain(self):
        r1 = self.cm.handle("odpal coś")
        self.assertTrue(any(x["tool"] == "play_media" for x in self.log))
        self.log.clear()
        r2 = self.cm.handle("trochę ciszej")
        self.assertTrue(any(x["tool"] == "volume_down" for x in self.log))
        self.log.clear()
        r3 = self.cm.handle("co teraz leci")
        self.assertTrue(any(x["tool"] == "get_now_playing" for x in self.log))
        self.assertIn("leci", (r3.text or "").lower())

    def test_gaming_and_cancel(self):
        r1 = self.cm.handle("ogarnij mi komputer do grania")
        self.assertTrue(any(x["tool"] == "open_app" for x in self.log))
        self.assertIn("steam", (r1.text or "").lower())
        r2 = self.cm.handle("dobra nieważne")
        self.assertIn("odpuszcz", (r2.text or "").lower())


if __name__ == "__main__":
    unittest.main()
