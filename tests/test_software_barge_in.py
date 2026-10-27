# ==========================
# ELIAN — software barge-in state machine (not acoustic HARDWARE)
# ==========================
"""SPEAKING → INTERRUPTED → ACTIVE_LISTENING without mic."""

from __future__ import annotations

import unittest

from voice.state import SpeechState, SpeechStateMachine


class SoftwareBargeInStateTests(unittest.TestCase):
    def test_speaking_to_interrupted_to_listening(self):
        sm = SpeechStateMachine(SpeechState.SPEAKING)
        ok = sm.transition(SpeechState.INTERRUPTED, reason="barge_in")
        self.assertTrue(ok)
        self.assertEqual(sm.state, SpeechState.INTERRUPTED)
        ok2 = sm.transition(SpeechState.ACTIVE_LISTENING, reason="after_barge")
        self.assertTrue(ok2)
        self.assertEqual(sm.state, SpeechState.ACTIVE_LISTENING)

    def test_acting_can_interrupt(self):
        sm = SpeechStateMachine(SpeechState.ACTING)
        self.assertTrue(sm.transition(SpeechState.INTERRUPTED, reason="barge_in"))
        self.assertEqual(sm.state, SpeechState.INTERRUPTED)

    def test_double_interrupt_stays_interrupted(self):
        sm = SpeechStateMachine(SpeechState.SPEAKING)
        self.assertTrue(sm.transition(SpeechState.INTERRUPTED, reason="barge_early"))
        # Second barge while already interrupted — allow stay or re-enter.
        sm.transition(SpeechState.INTERRUPTED, reason="barge_double")
        self.assertEqual(sm.state, SpeechState.INTERRUPTED)

    def test_text_inject_barge_flag_does_not_crash(self):
        from core.text_inject import run_inject_text

        code = run_inject_text("Co tam?", live_tools=False, debug=False, barge_in=True)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
