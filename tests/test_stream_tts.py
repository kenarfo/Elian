# ==========================
# ELIAN — Phase 0–2 streaming Brain + chunked TTS (offline)
# ==========================

from __future__ import annotations

import os
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

import Brain
from Brain import begin_ai_stream, reset_conversation
from core.conversation_manager import ConversationManager, HandleResult
from core.model_client import (
    FakeModelClient,
    reset_model_client,
    set_model_client,
)
from core.tools import ToolRegistry
from voice.pipeline import VoicePipeline
from voice.settings import VoiceSettings
from voice.speech_chunker import iter_speech_chunks
from voice.state import SpeechState
from voice.wake import FakeWakeListener


def _silent_pcm(_text):
    return (np.zeros(800, dtype=np.float32), 22050)


def _play_ok(_audio, _rate):
    return True


class SpeechChunkerTests(unittest.TestCase):
    def test_sentence_boundaries_polish(self):
        tokens = ["Cześć. ", "Jak ", "się ", "masz? ", "Dobrze…"]
        chunks = list(iter_speech_chunks(tokens, clause_flush_chars=0))
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(chunks[0].rstrip().endswith("."))
        joined = " ".join(chunks)
        self.assertIn("Cześć", joined)
        self.assertIn("masz?", joined)

    def test_clause_flush_before_period(self):
        # Long clause with comma should flush before the model finishes.
        text = (
            "To jest dość długie wprowadzenie do tematu, "
            "które powinno ruszyć TTS wcześnie. Koniec."
        )
        chunks = list(iter_speech_chunks([text], clause_flush_chars=40))
        self.assertGreaterEqual(len(chunks), 2)
        self.assertIn(",", chunks[0])


class FakeModelStreamTests(unittest.TestCase):
    def tearDown(self):
        reset_model_client()

    def test_blocking_chat_still_works(self):
        client = FakeModelClient(responses=["Halo."])
        out = client.chat([{"role": "user", "content": "hej"}])
        self.assertEqual(out, "Halo.")
        self.assertEqual(len(client.calls), 1)

    def test_stream_yields_ordered_pieces(self):
        client = FakeModelClient(responses=["Ala ma kota."])
        parts = list(client.chat_stream([{"role": "user", "content": "x"}]))
        self.assertEqual("".join(parts).strip(), "Ala ma kota.")

    def test_cancel_stops_further_tokens(self):
        client = FakeModelClient(
            responses=["jeden dwa trzy cztery pięć sześć"],
            stream_delay_s=0.05,
        )
        got = []

        def run():
            for piece in client.chat_stream([{"role": "user", "content": "x"}]):
                got.append(piece)
                if len(got) >= 2:
                    client.cancel_active_stream()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout=3.0)
        self.assertTrue(t.is_alive() is False)
        self.assertGreaterEqual(client.cancelled_streams, 1)
        self.assertLess(len("".join(got).split()), 6)


class BrainStreamHistoryTests(unittest.TestCase):
    def setUp(self):
        reset_conversation()
        reset_model_client()

    def tearDown(self):
        reset_model_client()
        reset_conversation()

    def test_unspoken_not_in_history(self):
        set_model_client(
            FakeModelClient(responses=["Pierwsze. Drugie zdanie ukryte."])
        )
        session = begin_ai_stream("opowiedz", mode="chat")
        self.assertIsNotNone(session)
        # Consume tokens but commit nothing (interrupt before speech).
        list(session.iter_tokens())
        session.commit_spoken("")
        assistants = [
            m for m in Brain.conversation if m.get("role") == "assistant"
        ]
        self.assertEqual(assistants, [])

    def test_spoken_partial_committed(self):
        set_model_client(
            FakeModelClient(responses=["Alpha. Beta gamma. Delta."])
        )
        session = begin_ai_stream("kontynuuj", mode="chat")
        list(session.iter_tokens())
        session.commit_spoken("Alpha.")
        assistants = [
            m for m in Brain.conversation if m.get("role") == "assistant"
        ]
        self.assertEqual(len(assistants), 1)
        self.assertEqual(assistants[0]["content"], "Alpha.")
        self.assertNotIn("Delta", assistants[0]["content"])


class SpeakStreamTests(unittest.TestCase):
    def test_first_chunk_before_tokens_finish(self):
        import Voice as voice_mod

        first_play = threading.Event()
        tokens_done = threading.Event()
        play_order = []

        def slow_tokens():
            yield "Pierwsze zdanie. "
            # Give player time to flush first sentence before more tokens.
            time.sleep(0.05)
            if not first_play.wait(timeout=1.0):
                self.fail("first chunk did not play before token stream finished")
            yield "Drugie zdanie końcowe."
            tokens_done.set()

        def play(audio, rate):
            play_order.append(time.monotonic())
            first_play.set()
            time.sleep(0.02)
            return True

        result = voice_mod.speak_stream(
            slow_tokens(),
            clause_flush_chars=0,
            cooldown=0,
            synthesize_fn=_silent_pcm,
            play_fn=play,
        )
        self.assertFalse(result.interrupted)
        self.assertGreaterEqual(result.chunks_spoken, 2)
        self.assertIn("Pierwsze", result.spoken_text)
        self.assertIn("Drugie", result.spoken_text)
        self.assertEqual(play_order, sorted(play_order))

    def test_interrupt_stops_later_chunks(self):
        import Voice as voice_mod

        played = []

        def tokens():
            yield "Jeden. "
            yield "Dwa. "
            yield "Trzy. "
            yield "Cztery."

        def play(audio, rate):
            played.append(True)
            if len(played) == 1:
                voice_mod.cancel_stream_tts()
            return len(played) == 1

        result = voice_mod.speak_stream(
            tokens(),
            clause_flush_chars=0,
            cooldown=0,
            synthesize_fn=_silent_pcm,
            play_fn=play,
        )
        self.assertTrue(result.interrupted)
        self.assertLessEqual(result.chunks_spoken, 2)
        self.assertNotIn("Cztery", result.spoken_text)


class PipelineStreamCancelTests(unittest.TestCase):
    def setUp(self):
        reset_conversation()
        reset_model_client()
        os.environ["ELIAN_STREAM_LLM"] = "1"

    def tearDown(self):
        os.environ["ELIAN_STREAM_LLM"] = "0"
        reset_model_client()
        reset_conversation()

    def test_interrupt_cancels_ollama_and_keeps_spoken_partial(self):
        import Voice as voice_mod

        client = FakeModelClient(
            responses=["Alpha. Beta. Gamma. Delta. Epsilon."],
            stream_delay_s=0.04,
        )
        set_model_client(client)

        settings = VoiceSettings(
            barge_in_enabled=False,
            stream_llm_responses=True,
            stream_tts=True,
            tts_clause_flush_chars=0,
            tts_enabled=True,
        )
        pipeline = VoicePipeline(
            settings=settings,
            wake_listener=FakeWakeListener(),
            listen_fn=lambda **kw: "",
            speak_fn=lambda *a, **k: None,
            stop_speak_fn=lambda **k: None,
            is_speaking_fn=lambda: False,
        )
        pipeline.start()

        session = begin_ai_stream("opowiedz historię", mode="chat")
        self.assertIsNotNone(session)

        play_count = {"n": 0}

        def play(audio, rate):
            play_count["n"] += 1
            if play_count["n"] == 1:
                # Interrupt after first spoken chunk.
                threading.Timer(0.01, pipeline.stop_speaking).start()
            time.sleep(0.03)
            return play_count["n"] == 1

        with patch.object(voice_mod, "speak_stream", wraps=voice_mod.speak_stream):
            # Inject synth/play via patching speak_stream call inside pipeline
            original = voice_mod.speak_stream

            def wrapped(token_source, **kwargs):
                kwargs.setdefault("synthesize_fn", _silent_pcm)
                kwargs.setdefault("play_fn", play)
                kwargs.setdefault("cooldown", 0)
                return original(token_source, **kwargs)

            with patch.object(voice_mod, "speak_stream", side_effect=wrapped):
                t = threading.Thread(
                    target=lambda: pipeline.speak_prose_stream(
                        session, allow_barge=False
                    ),
                    daemon=True,
                )
                t.start()
                t.join(timeout=5.0)
                self.assertFalse(t.is_alive())

        self.assertGreaterEqual(client.cancelled_streams, 1)
        self.assertTrue(pipeline.was_interrupted)
        spoken = session.spoken_text
        self.assertTrue(spoken)
        self.assertIn("Alpha", spoken)
        self.assertNotIn("Epsilon", spoken)
        assistants = [
            m for m in Brain.conversation if m.get("role") == "assistant"
        ]
        self.assertEqual(len(assistants), 1)
        self.assertEqual(assistants[0]["content"], spoken)
        pipeline.close()

    def test_planner_json_not_attached_as_prose_stream(self):
        """Agent/planner HandleResult must not set prose_stream."""
        from core.builtin_tools import build_default_registry

        model = FakeModelClient(
            responses=[
                {
                    "type": "final_answer",
                    "message": '{"type":"final_answer","message":"nope"}',
                }
            ]
        )
        manager = ConversationManager(
            registry=build_default_registry(),
            model=model,
        )
        manager.activate()
        # FastPath volume — no prose_stream
        with patch("core.builtin_tools.Music.run", return_value="Gotowe."):
            result = manager.handle("głośniej")
        self.assertIsNone(getattr(result, "prose_stream", None))
        self.assertNotIn(result.source, ("",))
        # Ensure we did not speak raw planner JSON via stream field
        self.assertFalse(str(result.text or "").lstrip().startswith('{"type"'))

    def test_fast_path_still_works_with_stream_env(self):
        from core.builtin_tools import build_default_registry

        model = FakeModelClient(responses=[])
        manager = ConversationManager(
            registry=build_default_registry(),
            model=model,
        )
        manager.activate()
        with patch("core.builtin_tools.Music.run", return_value="Gotowe."):
            result = manager.handle("głośniej")
        self.assertEqual(len(model.calls), 0)
        self.assertTrue(result.should_speak)
        self.assertIsNone(result.prose_stream)

    def test_cm_chat_returns_prose_stream_when_enabled(self):
        set_model_client(FakeModelClient(responses=["Cześć kolego."]))
        manager = ConversationManager(registry=ToolRegistry(), model=FakeModelClient([]))
        manager.activate()
        # Force stream path regardless of settings object on manager
        with patch.object(manager, "_brain_stream_enabled", return_value=True):
            result = manager._brain_reply("hej", mode="chat")
        self.assertIsInstance(result, HandleResult)
        self.assertIsNotNone(result.prose_stream)
        self.assertEqual(result.source, "chat")


class ExecuteTurnStreamWiringTests(unittest.TestCase):
    def tearDown(self):
        reset_model_client()
        reset_conversation()

    def test_execute_turn_uses_prose_stream_not_blocking_speak(self):
        import Voice as voice_mod

        set_model_client(FakeModelClient(responses=["Ser."]))
        spoken = []

        class FakeManager:
            active = True

            def handle(self, text):
                session = begin_ai_stream(text, mode="chat")
                return HandleResult(
                    should_speak=True,
                    active=True,
                    source="chat",
                    prose_stream=session,
                )

        pipeline = VoicePipeline(
            settings=VoiceSettings(
                barge_in_enabled=False,
                stream_tts=True,
                tts_enabled=True,
            ),
            wake_listener=FakeWakeListener(),
            listen_fn=lambda **kw: "",
            speak_fn=lambda t, **k: spoken.append(("block", t)),
            stop_speak_fn=lambda **k: None,
        )
        pipeline.start()

        original = voice_mod.speak_stream

        def wrapped(token_source, **kwargs):
            kwargs.setdefault("synthesize_fn", _silent_pcm)
            kwargs.setdefault("play_fn", _play_ok)
            kwargs.setdefault("cooldown", 0)
            result = original(token_source, **kwargs)
            spoken.append(("stream", result.spoken_text))
            return result

        with patch.object(voice_mod, "speak_stream", side_effect=wrapped):
            result = pipeline.execute_turn(FakeManager(), "powiedz ser")
        self.assertTrue(any(kind == "stream" for kind, _ in spoken))
        self.assertFalse(any(kind == "block" for kind, _ in spoken))
        self.assertIn("Ser", result.text)
        pipeline.close()


if __name__ == "__main__":
    unittest.main()
