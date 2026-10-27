# ==========================
# ELIAN — RC validation harness tests (Phase 9.5, offline)
# ==========================

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.conversation_manager import ConversationManager, HandleResult
from core.metrics import reset_metrics
from core.rc_validation.collector import (
    RCValidationCollector,
    activate_collector,
    deactivate_collector,
    record_interaction_if_active,
)
from core.rc_validation.report import build_report, classify_release, render_markdown
from core.rc_validation.tests import RC_TEST_CASES
from core.tools import ToolRegistry
from core.tool_result import ToolResult
from core.risk import RiskLevel
from core.tools import Tool


class LegacyRateTests(unittest.TestCase):
    def test_legacy_fallback_rate(self):
        col = RCValidationCollector()
        col.active = True
        for source in ("fast_path", "fast_path", "agent", "intent"):
            col.record_interaction(
                "test",
                HandleResult.speak("ok", source=source),
            )
        snap = col.snapshot_metrics()
        self.assertEqual(snap["routable_interactions"], 4)
        self.assertEqual(snap["legacy_route_count"], 1)
        self.assertEqual(snap["legacy_fallback_rate_pct"], 25.0)


class RoutingDistributionTests(unittest.TestCase):
    def test_routing_percentages(self):
        col = RCValidationCollector()
        col.active = True
        sources = ["fast_path"] * 45 + ["agent"] * 43 + ["chat"] * 8 + ["skill"] * 4
        for src in sources:
            col.record_interaction("x", HandleResult.speak("y", source=src))
        dist = col.snapshot_metrics()["routing_distribution_pct"]
        self.assertAlmostEqual(dist["fast_path"], 45.0, delta=0.2)
        self.assertAlmostEqual(dist["agent"], 43.0, delta=0.2)
        self.assertAlmostEqual(dist["brain"], 8.0, delta=0.2)
        self.assertAlmostEqual(dist["legacy"], 4.0, delta=0.2)


class PrivacyTests(unittest.TestCase):
    def test_no_transcript_by_default(self):
        col = RCValidationCollector(store_transcripts=False)
        col.active = True
        col.record_interaction(
            "tajne hasło użytkownika",
            HandleResult.speak("ok", source="chat"),
        )
        trace = col.interaction_traces[0]
        self.assertNotIn("transcript", trace)
        self.assertIn("text_fingerprint", trace)
        self.assertNotIn("tajne", str(trace))

    def test_transcript_when_enabled(self):
        col = RCValidationCollector(store_transcripts=True)
        col.active = True
        col.record_interaction(
            "test phrase",
            HandleResult.speak("ok", source="chat"),
        )
        self.assertEqual(col.interaction_traces[0]["transcript"], "test phrase")


class ReleaseClassificationTests(unittest.TestCase):
    def test_not_ready_on_crashes(self):
        metrics = {
            "crashes": 2,
            "legacy_fallback_rate_pct": 5,
            "agent_success": 1,
            "agent_failure": 0,
            "wake_model": {"custom_elian_onnx_installed": True},
        }
        decision, notes = classify_release(metrics, [{"result": "PASS"}])
        self.assertEqual(decision, "NOT READY")
        self.assertTrue(notes)

    def test_fail_never_ready(self):
        metrics = {
            "crashes": 0,
            "wake_model": {"custom_elian_onnx_installed": True},
        }
        decision, notes = classify_release(
            metrics, [{"result": "FAIL", "test_id": "x"}]
        )
        self.assertEqual(decision, "NOT READY")

    def test_ready_with_issues_partial(self):
        metrics = {
            "crashes": 0,
            "wake_model": {"custom_elian_onnx_installed": True},
        }
        decision, notes = classify_release(
            metrics, [{"result": "PARTIAL"}, {"result": "PASS"}]
        )
        self.assertEqual(decision, "READY WITH ISSUES")

    def test_ready_clean(self):
        metrics = {
            "crashes": 0,
            "wake_model": {"custom_elian_onnx_installed": True},
        }
        decision, notes = classify_release(metrics, [{"result": "PASS"}])
        self.assertEqual(decision, "READY")
        self.assertEqual(notes, [])


class ReportGenerationTests(unittest.TestCase):
    def test_build_and_render_report(self):
        col = RCValidationCollector()
        col.active = True
        col.record_manual_test("fastpath_volume", "PASS", "ok")
        col.deactivate()
        report = build_report(col)
        self.assertEqual(report["title"], "ELIAN 2.0 RC VALIDATION")
        md = render_markdown(report)
        self.assertIn("Legacy fallback rate", md)
        self.assertIn("fastpath_volume", md)

    def test_write_report_files(self):
        from core.rc_validation.report import write_reports

        col = RCValidationCollector()
        col.active = True
        col.deactivate()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("core.rc_validation.report.DATA_DIR", tmp):
                stamped, md = write_reports(col)
                self.assertTrue(stamped.exists())
                self.assertTrue(md.exists())
                latest = Path(tmp) / "diagnostics" / "rc_validation_latest.json"
                self.assertTrue(latest.exists())
                payload = json.loads(latest.read_text(encoding="utf-8"))
                self.assertIn("metrics", payload)


class CollectorHookTests(unittest.TestCase):
    def test_record_interaction_if_active(self):
        reset_metrics()
        activate_collector(store_transcripts=False)
        registry = ToolRegistry()

        def vol(_a):
            return ToolResult.ok("ok", tool_name="volume_up")

        registry.register(
            Tool(name="volume_up", description="", handler=vol, risk_level=RiskLevel.SAFE)
        )
        manager = ConversationManager(memory=[], registry=registry)
        with patch("core.conversation_manager.run_agent", return_value=None), patch(
            "core.conversation_manager.run_skills", return_value=None
        ), patch("core.conversation_manager.run_command", return_value=None), patch(
            "core.conversation_manager.run_intent", return_value=None
        ):
            manager.activate()
            result = manager.handle("głośniej")
        record_interaction_if_active("głośniej", result)
        col = deactivate_collector()
        self.assertIsNotNone(col)
        self.assertGreaterEqual(col.interactions, 1)
        # Soft volume-up uses ActionLoop; RC buckets it with FastPath.
        self.assertEqual(col.route_counts.get("fast_path"), 1)
        self.assertIn(getattr(result, "source", ""), ("fast_path", "action_loop"))


class ManualResultTests(unittest.TestCase):
    def test_manual_pass_fail(self):
        col = RCValidationCollector()
        col.record_manual_test("wake_word", "PASS")
        col.record_manual_test("bad", "INVALID")
        col.record_manual_test("stt", "FAIL", "zły transcript")
        self.assertEqual(len(col.manual_results), 2)
        self.assertEqual(col.manual_results[0].result, "PASS")


class TestCatalogTests(unittest.TestCase):
    def test_catalog_not_empty(self):
        self.assertGreaterEqual(len(RC_TEST_CASES), 20)
        ids = {t.id for t in RC_TEST_CASES}
        self.assertIn("fastpath_volume", ids)
        self.assertIn("wake_word", ids)


class RCStatusDisplayTests(unittest.TestCase):
    def test_idle_listening_processing_speaking_active_reported(self):
        from core.rc_validation.status import RCStatusDisplay
        from voice.state import SpeechState, SpeechStateMachine

        lines = []
        display = RCStatusDisplay(enabled=True, print_fn=lines.append)
        sm = SpeechStateMachine(SpeechState.IDLE)
        display.attach(sm)

        sm.set(SpeechState.WAKE_LISTENING)
        sm.set(SpeechState.ACTIVE_LISTENING)
        sm.set(SpeechState.PROCESSING)
        sm.set(SpeechState.SPEAKING)
        sm.set(SpeechState.WAITING)
        sm.set(SpeechState.IDLE)

        joined = "\n".join(lines)
        self.assertIn("IDLE", joined)
        self.assertIn("LISTENING", joined)
        self.assertIn("PROCESSING", joined)
        self.assertIn("SPEAKING", joined)
        self.assertIn("ACTIVE SESSION", joined)

        # Sequence of phases present in history
        labels = [h.replace("[STATUS] ", "") for h in display.history]
        kinds = []
        for label in labels:
            if "IDLE" in label and "ACTIVE" not in label:
                kinds.append("IDLE")
            elif "LISTENING" in label:
                kinds.append("LISTENING")
            elif "PROCESSING" in label:
                kinds.append("PROCESSING")
            elif "SPEAKING" in label:
                kinds.append("SPEAKING")
            elif "ACTIVE SESSION" in label:
                kinds.append("ACTIVE")
        self.assertEqual(
            kinds,
            ["IDLE", "LISTENING", "PROCESSING", "SPEAKING", "ACTIVE", "IDLE"],
        )

    def test_wake_detected_and_heard(self):
        from core.rc_validation.status import RCStatusDisplay

        lines = []
        display = RCStatusDisplay(enabled=True, print_fn=lines.append)
        display.wake_detected()
        display.heard("głośniej")
        joined = "\n".join(lines)
        self.assertIn("WAKE DETECTED", joined)
        self.assertIn('HEARD: "głośniej"', joined)
        self.assertIn("ACTIVE SESSION", joined)

    def test_disabled_prints_nothing(self):
        from core.rc_validation.status import RCStatusDisplay
        from voice.state import SpeechState, SpeechStateMachine

        lines = []
        display = RCStatusDisplay(enabled=False, print_fn=lines.append)
        sm = SpeechStateMachine()
        display.attach(sm)
        sm.set(SpeechState.SPEAKING)
        display.wake_detected()
        display.heard("x")
        self.assertEqual(lines, [])

    def test_listener_does_not_change_state_semantics(self):
        from core.rc_validation.status import RCStatusDisplay
        from voice.state import SpeechState, SpeechStateMachine

        sm = SpeechStateMachine(SpeechState.IDLE)
        display = RCStatusDisplay(enabled=True, print_fn=lambda *_: None)
        display.attach(sm)
        sm.set(SpeechState.ACTIVE_LISTENING)
        self.assertEqual(sm.state, SpeechState.ACTIVE_LISTENING)
        self.assertTrue(sm.is_active_session())
        sm.set(SpeechState.SPEAKING)
        self.assertTrue(sm.is_speaking())


class RCWakePolicyTests(unittest.TestCase):
    def test_missing_custom_model_uses_legacy_stt_not_openwakeword(self):
        from core.rc_validation.wake_policy import (
            ACOUSTIC_STATUS_MISSING,
            has_custom_elian_wake_model,
            resolve_rc_wake_listener,
        )
        from voice.settings import VoiceSettings
        from voice.wake import AcousticWakeListener, LegacySttWakeListener

        self.assertFalse(has_custom_elian_wake_model(""))
        self.assertFalse(has_custom_elian_wake_model("hey_jarvis_v0.1.onnx"))
        self.assertFalse(
            has_custom_elian_wake_model(r"C:\missing\elian.onnx")
        )

        settings = VoiceSettings(
            wake_enabled=True,
            wake_model="",
            wake_legacy_stt_fallback=True,
        )
        listener, mode, meta = resolve_rc_wake_listener(
            settings,
            listen_fn=lambda **kwargs: "elian",
        )
        self.assertEqual(mode, "legacy_stt")
        self.assertIsInstance(listener, LegacySttWakeListener)
        self.assertNotIsInstance(listener, AcousticWakeListener)
        self.assertTrue(meta.get("forced_legacy_stt"))
        self.assertEqual(meta.get("acoustic_wake_status"), ACOUSTIC_STATUS_MISSING)

    def test_existing_elian_onnx_allows_acoustic_path(self):
        import tempfile
        from pathlib import Path

        from core.rc_validation.wake_policy import has_custom_elian_wake_model

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "elian.onnx"
            path.write_bytes(b"fake")
            self.assertTrue(has_custom_elian_wake_model(str(path)))

    def test_report_marks_acoustic_missing(self):
        from core.rc_validation.collector import RCValidationCollector
        from core.rc_validation.report import build_report, render_markdown
        from core.rc_validation.wake_policy import ACOUSTIC_STATUS_MISSING

        col = RCValidationCollector()
        col.active = True
        col.set_wake_policy_meta(
            {
                "custom_elian_onnx_installed": False,
                "wake_model_path": "",
                "acoustic_wake_status": ACOUSTIC_STATUS_MISSING,
                "forced_legacy_stt": True,
            },
            mode="legacy_stt",
        )
        col.deactivate()
        report = build_report(col)
        wake = report["metrics"]["wake_model"]
        self.assertEqual(wake["acoustic_wake_status"], ACOUSTIC_STATUS_MISSING)
        self.assertEqual(wake["wake_mode_used"], "legacy_stt")
        md = render_markdown(report)
        self.assertIn("MANUAL TEST REQUIRED / CUSTOM MODEL MISSING", md)


class RcInjectTests(unittest.TestCase):
    def test_parse_rc_inject_strips_quotes(self):
        from core.rc_validation.inject import NOTEPAD_STAGE_C_PHRASE, parse_rc_inject_line

        is_cmd, text = parse_rc_inject_line(
            f'rc inject "{NOTEPAD_STAGE_C_PHRASE}"'
        )
        self.assertTrue(is_cmd)
        self.assertEqual(text, NOTEPAD_STAGE_C_PHRASE)
        is_cmd, text = parse_rc_inject_line(f"rc inject {NOTEPAD_STAGE_C_PHRASE}")
        self.assertTrue(is_cmd)
        self.assertEqual(text, NOTEPAD_STAGE_C_PHRASE)
        is_cmd, text = parse_rc_inject_line("rc inject")
        self.assertTrue(is_cmd)
        self.assertEqual(text, "")
        is_cmd, _ = parse_rc_inject_line("rc status")
        self.assertFalse(is_cmd)

    def test_rc_inject_same_semantic_plan_as_stt(self):
        from core.compound_plan import parse_compound_goal
        from core.rc_validation.inject import (
            NOTEPAD_STAGE_C_PHRASE,
            dispatch_rc_inject,
            parse_rc_inject_line,
        )

        is_cmd, injected = parse_rc_inject_line(
            f'rc inject "{NOTEPAD_STAGE_C_PHRASE}"'
        )
        self.assertTrue(is_cmd)
        stt_plan = parse_compound_goal(NOTEPAD_STAGE_C_PHRASE)
        inject_plan = parse_compound_goal(injected)
        self.assertTrue(stt_plan.detected)
        self.assertEqual(
            [s.family for s in stt_plan.steps],
            ["APP", "COMPUTER", "COMPUTER"],
        )
        self.assertEqual(
            [s.semantic_action for s in stt_plan.steps],
            ["open_service", "type_text", "select_all"],
        )
        self.assertEqual(
            [s.family for s in inject_plan.steps],
            [s.family for s in stt_plan.steps],
        )
        self.assertEqual(
            [s.semantic_action for s in inject_plan.steps],
            [s.semantic_action for s in stt_plan.steps],
        )

        calls = []

        class _Mgr:
            active = False

            def activate(self):
                self.active = True

            def handle(self, text):
                calls.append(text)
                return HandleResult.speak("ok", source="agent")

        mgr = _Mgr()
        dispatch_rc_inject(mgr, injected)
        self.assertTrue(mgr.active)
        self.assertEqual(calls, [NOTEPAD_STAGE_C_PHRASE])

    def test_rc_inject_is_not_a_user_intent(self):
        from core.intent_family import classify_intent_family
        from core.rc_validation.inject import parse_rc_inject_line

        spoken = "rc inject otwórz notatnik"
        is_cmd, _ = parse_rc_inject_line(spoken)
        self.assertTrue(is_cmd)
        info = classify_intent_family(spoken)
        self.assertNotIn("inject", str(info.family).lower())
        slots = info.slots or {}
        self.assertNotEqual(slots.get("semantic_action"), "rc_inject")


if __name__ == "__main__":
    unittest.main()
