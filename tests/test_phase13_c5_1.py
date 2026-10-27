# ==========================
# ELIAN — Phase 13 C.5.1 raw wake pipeline + Jarvis compound speech
# ==========================
from __future__ import annotations

import inspect
import unittest

from core.asr_repair import repair_action_verbs
from core.compound_plan import parse_compound_goal
from core.jarvis_response import (
    ResponseContext,
    compound_from_executed_rows,
    execution_reply,
    social_reply,
    summarize_compound_steps,
)
from core.planner import _synthesize_compound_reply
from core.text_pipeline import prepare_user_text
from core.transcript import envelope_from_whisper
from WakeWord import extract_wake


WHISPER = (
    "Hej Elian, otwórz notatnik i wpisz Elian Działek Agent i zaznacz cały tekst."
)
SECOND_ELIAN = "Hej Elian, wpisz Elian działa jak agent."


def _ok_row(action: str, message: str, **data):
    return {
        "semantic_action": action,
        "tool": "open_app" if action in ("open_app", "open_service") else action,
        "status": "SUCCEEDED",
        "observation": {
            "success": True,
            "message": message,
            "data": dict(data) if data else {"app": "Notatnik"} if action in ("open_app", "open_service") else {},
        },
    }


def _fail_row(action: str, message: str, **extra):
    data = dict(extra)
    return {
        "semantic_action": action,
        "tool": action,
        "status": "FAILED",
        "observation": {
            "success": False,
            "message": message,
            "data": data,
        },
    }


class Phase13C51RawWakeTests(unittest.TestCase):
    def test_raw_transcript_preserves_casing_through_wake(self):
        envelope = envelope_from_whisper(WHISPER, corrected=WHISPER.lower())
        self.assertEqual(envelope.raw_transcript, WHISPER)
        self.assertIn("Elian Działek Agent", envelope.raw_transcript)
        wake = extract_wake(envelope.raw_transcript)
        self.assertTrue(wake.wake_detected)
        self.assertIn("Elian Działek Agent", wake.raw_command)
        self.assertIn("Elian Działek Agent", wake.raw_transcript)

    def test_wake_command_raw_preserves_casing(self):
        envelope = envelope_from_whisper(WHISPER)
        wake = extract_wake(envelope.raw_transcript)
        self.assertEqual(
            wake.raw_command,
            "otwórz notatnik i wpisz Elian Działek Agent i zaznacz cały tekst",
        )
        import Voice
        from voice.pipeline import VoicePipeline

        src_voice = inspect.getsource(Voice.transcribe_audio)
        self.assertIn("envelope.raw_transcript", src_voice)
        # Corrected STT may be returned for commands; never force .lower() on return.
        self.assertNotIn("return corrected.lower()", src_voice)
        self.assertNotIn("return envelope.raw_transcript.lower()", src_voice)
        src_pipe = inspect.getsource(VoicePipeline.transcript_from_wake)
        self.assertIn(".raw_command", src_pipe)

    def test_normalized_command_lowercases_separately(self):
        wake = extract_wake(WHISPER)
        prepared = prepare_user_text(WHISPER)
        self.assertEqual(
            wake.normalized_command,
            "otwórz notatnik i wpisz elian działek agent i zaznacz cały tekst",
        )
        self.assertNotEqual(wake.raw_command, wake.normalized_command)
        self.assertIn("Elian Działek Agent", prepared.raw_text)
        self.assertIn("elian działek agent", prepared.raw_text.lower())

    def test_second_elian_survives(self):
        wake = extract_wake(SECOND_ELIAN)
        self.assertTrue(wake.wake_detected)
        self.assertEqual(wake.raw_command, "wpisz Elian działa jak agent")
        plan = parse_compound_goal(wake.raw_command)
        if plan.detected:
            typed = next(s for s in plan.steps if s.semantic_action == "type_text")
            payload = typed.slots.get("text_payload")
        else:
            from core.computer_semantics import extract_type_payload

            payload = extract_type_payload(wake.raw_command)
        self.assertEqual(payload, "Elian działa jak agent")

    def test_payload_elian_dzialek_agent_exact(self):
        wake = extract_wake(WHISPER)
        plan = parse_compound_goal(wake.raw_command)
        typed = next(s for s in plan.steps if s.semantic_action == "type_text")
        self.assertEqual(typed.slots.get("text_payload"), "Elian Działek Agent")
        self.assertEqual(typed.raw_clause, "wpisz Elian Działek Agent")
        self.assertEqual(typed.normalized_clause.lower().startswith("wpisz"), True)
        from core.compound_execute import resolve_step_tool

        _tool, args, _reason = resolve_step_tool(typed)
        self.assertEqual(args.get("text"), "Elian Działek Agent")

    def test_payload_not_fuzzy_corrected(self):
        heard = "wpisz Elian Działek Agent"
        result = repair_action_verbs(heard)
        self.assertNotIn("działa jak agent", (result.canonical or "").lower())
        self.assertIn("Działek Agent", result.canonical or heard)
        wake = extract_wake(WHISPER)
        plan = parse_compound_goal(wake.raw_command)
        typed = next(s for s in plan.steps if s.semantic_action == "type_text")
        self.assertEqual(typed.slots.get("text_payload"), "Elian Działek Agent")
        self.assertNotEqual(typed.slots.get("text_payload"), "Elian działa jak agent")

    def test_zaznasz_still_repairs_to_select_all(self):
        result = repair_action_verbs("zaznasz cały tekst")
        self.assertTrue(result.applied)
        self.assertEqual(result.canonical, "zaznacz cały tekst")
        wake = extract_wake(
            "Hej Elian, otwórz notatnik i wpisz Elian Działek Agent i zaznasz cały tekst."
        )
        plan = parse_compound_goal(wake.raw_command)
        self.assertEqual(plan.steps[-1].semantic_action, "select_all")
        self.assertEqual(plan.steps[1].slots.get("text_payload"), "Elian Działek Agent")


class Phase13C51JarvisResponseTests(unittest.TestCase):
    def test_three_success_not_gotowe_gotowe(self):
        rows = [
            _ok_row("open_service", "Notatnik otwarte.", app="Notatnik"),
            _ok_row("type_text", "Gotowe."),
            _ok_row("select_all", "Gotowe."),
        ]
        plan = parse_compound_goal(
            "otwórz notatnik i wpisz Elian Działek Agent i zaznacz cały tekst"
        )
        spoken = _synthesize_compound_reply(plan, [], rows)
        self.assertNotIn("Gotowe. Gotowe.", spoken)
        self.assertNotIn("Notatnik otwarte. Gotowe.", spoken)
        low = spoken.lower()
        self.assertIn("wpisan", low)
        self.assertIn("zaznaczon", low)
        self.assertTrue("notatnik" in low or "gotowe" in low)

    def test_three_success_semantic_all_steps(self):
        spoken = summarize_compound_steps(
            successful_steps=[
                {"tool": "open_app", "semantic_action": "open_service", "success": True, "data": {"app": "Notatnik"}, "message": "Notatnik otwarte."},
                {"tool": "type_text", "semantic_action": "type_text", "success": True, "message": "Gotowe."},
                {"tool": "select_all", "semantic_action": "select_all", "success": True, "message": "Gotowe."},
            ],
            failed_steps=[],
        )
        self.assertIn("wpisan", spoken.lower())
        self.assertIn("zaznaczon", spoken.lower())
        self.assertIn("Notatnik", spoken)

    def test_partial_compound_is_honest(self):
        spoken = compound_from_executed_rows(
            [
                _ok_row("open_service", "Notatnik otwarte.", app="Notatnik"),
                _ok_row("type_text", "Gotowe."),
                _fail_row("select_all", "Gotowe."),
            ]
        )
        low = spoken.lower()
        self.assertNotIn("gotowe.", low)
        self.assertFalse(low.startswith("gotowe"))
        self.assertIn("notatnik", low)
        self.assertTrue("wpis" in low)
        self.assertIn("zaznacz", low)
        self.assertIn("ale", low)

    def test_failed_compound_does_not_claim_success(self):
        spoken = compound_from_executed_rows(
            [
                _fail_row("open_service", "timeout", app="Notatnik"),
                _fail_row("type_text", "Narzędzie type_text status timeout"),
                _fail_row("select_all", "fail"),
            ]
        )
        low = spoken.lower()
        self.assertNotIn("gotowe", low)
        self.assertNotIn("wpisany", low)
        self.assertIn("nie udało", low)

    def test_tool_debug_does_not_leak(self):
        spoken = summarize_compound_steps(
            successful_steps=[],
            failed_steps=[
                {
                    "tool": "type_text",
                    "semantic_action": "type_text",
                    "success": False,
                    "message": "Narzędzie type_text status timeout UNKNOWN_SIDE_EFFECT",
                    "data": {"app": "Notatnik"},
                }
            ],
        )
        low = spoken.lower()
        self.assertNotIn("narzędzie", low)
        self.assertNotIn("timeout", low)
        self.assertNotIn("unknown_side_effect", low)
        self.assertIn("wpisać", low)

    def test_social_responses_unchanged(self):
        self.assertEqual(social_reply("co tam?"), "Spokojnie. Jestem, słucham.")
        self.assertNotIn("Notatnik", social_reply("co tam?"))
        self.assertNotIn("Spotify", social_reply("co tam?"))

    def test_single_step_success_remains_concise(self):
        text = execution_reply(
            ResponseContext(
                successful_steps=[
                    {
                        "tool": "open_app",
                        "success": True,
                        "message": "Notatnik otwarte.",
                        "data": {"app": "Notatnik"},
                    }
                ]
            )
        )
        self.assertLess(len(text), 80)
        self.assertNotIn("Gotowe. Gotowe.", text)
        self.assertIn("Notatnik", text)


if __name__ == "__main__":
    unittest.main()
