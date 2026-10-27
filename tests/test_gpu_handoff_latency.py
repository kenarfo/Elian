# ==========================
# ELIAN — Fish/Ollama exclusive GPU handoff (latency)
# ==========================
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from core.ollama_lifecycle import (
    ensure_gpu_for_ollama,
    fish_exclusive_tts_enabled,
    ollama_keep_alive,
)
from voice.fish_s2_backend import session_idle_s


class GpuHandoffLatencyTests(unittest.TestCase):
    def test_session_idle_default_is_conversation_scale(self):
        with patch("Config.get_config_value", side_effect=Exception("no cfg")):
            self.assertGreaterEqual(session_idle_s(), 30.0)

    def test_session_idle_reads_config(self):
        with patch(
            "Config.get_config_value",
            side_effect=lambda *a, **k: 45.0
            if a[:2] == ("voice", "fish_session_idle_s")
            else k.get("default"),
        ):
            self.assertEqual(session_idle_s(), 45.0)

    def test_ollama_keep_alive_zero_under_exclusive_fish(self):
        with patch(
            "core.ollama_lifecycle.fish_exclusive_tts_enabled", return_value=True
        ):
            with patch(
                "Config.get_config_value",
                side_effect=lambda *a, **k: None
                if a[:2] == ("ai", "ollama_keep_alive")
                else k.get("default"),
            ):
                self.assertEqual(ollama_keep_alive(), 0)

    def test_ollama_keep_alive_long_when_not_exclusive(self):
        with patch(
            "core.ollama_lifecycle.fish_exclusive_tts_enabled", return_value=False
        ):
            with patch(
                "Config.get_config_value",
                side_effect=lambda *a, **k: None
                if a[:2] == ("ai", "ollama_keep_alive")
                else k.get("default"),
            ):
                self.assertEqual(ollama_keep_alive(), "30m")

    def test_ensure_gpu_ends_active_fish_session(self):
        with patch(
            "core.ollama_lifecycle.fish_exclusive_tts_enabled", return_value=True
        ):
            with patch(
                "voice.fish_s2_backend.fish_session_snapshot",
                return_value={"active": True},
            ):
                with patch(
                    "voice.fish_s2_backend.end_fish_tts_session"
                ) as end:
                    info = ensure_gpu_for_ollama(reason="test")
        self.assertTrue(info["ended_fish"])
        end.assert_called_once_with(reason="test")

    def test_ollama_chat_requests_gpu_and_short_keep_alive(self):
        fake_ollama = MagicMock()
        fake_ollama.Client.return_value.chat.return_value = {
            "message": {"content": "ok"}
        }
        with patch.dict("sys.modules", {"ollama": fake_ollama}):
            with patch(
                "core.ollama_lifecycle.ensure_gpu_for_ollama"
            ) as ensure:
                with patch(
                    "core.ollama_lifecycle.ollama_keep_alive", return_value=0
                ):
                    from core.model_client import OllamaModelClient

                    client = OllamaModelClient(model="m", timeout=5)
                    out = client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(out, "ok")
        ensure.assert_called()
        kwargs = fake_ollama.Client.return_value.chat.call_args.kwargs
        self.assertEqual(kwargs.get("keep_alive"), 0)


if __name__ == "__main__":
    unittest.main()
