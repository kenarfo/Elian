# ==========================
# ELIAN — Persistent Fish S2 worker / session tests (no GPU required)
# ==========================
from __future__ import annotations

import os
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from voice.fish_s2_backend import (
    begin_fish_tts_session,
    cancel_s2_synthesis,
    end_fish_tts_session,
    fish_session_snapshot,
    reset_fish_health_for_tests,
    synthesize_s2_wav,
)
from voice.fish_s2_worker import (
    FishS2ServerWorker,
    drop_worker_singleton,
    get_worker,
    reset_worker_for_tests,
)
from voice.tts_engine import RoutedTTS, reset_tts_engine_for_tests


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _FakeGenerateHandler(BaseHTTPRequestHandler):
    hits = 0

    def log_message(self, fmt, *args):  # noqa: A003
        return

    def do_POST(self):  # noqa: N802
        type(self).hits += 1
        length = int(self.headers.get("Content-Length") or 0)
        _ = self.rfile.read(length)
        body = b"RIFF" + b"\x00" * 2000
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _FakeProc:
    def __init__(self, pid: int = 4242):
        self.pid = pid
        self.stdout = iter(())
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self._alive = False

    def kill(self):
        self._alive = False

    def wait(self, timeout=None):
        self._alive = False
        return 0


class FishWorkerUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "0"
        os.environ["ELIAN_FISH_PERSISTENT"] = "1"
        reset_fish_health_for_tests()
        reset_worker_for_tests()
        reset_tts_engine_for_tests()
        _FakeGenerateHandler.hits = 0

    def tearDown(self) -> None:
        end_fish_tts_session(reason="teardown")
        reset_worker_for_tests()
        os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "1"

    def _cfg(self):
        from voice.fish_s2_backend import load_fish_s2_config

        return load_fish_s2_config()

    def _start_fake_http(self, port: int) -> HTTPServer:
        httpd = HTTPServer(("127.0.0.1", port), _FakeGenerateHandler)
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        return httpd

    def test_A_worker_start_and_B_readiness(self) -> None:
        port = _free_port()
        # Port free before spawn; become ready immediately after Popen.
        seq = {"n": 0}

        def _port_open(host, p):
            seq["n"] += 1
            # First check in start(): free after orphan sweep → False
            # Ready wait loop → True
            return seq["n"] > 1

        w = FishS2ServerWorker(cfg=self._cfg(), host="127.0.0.1", port=port)
        with patch("voice.fish_s2_backend._kill_s2_orphans", return_value=0) as kill:
            with patch("voice.fish_s2_worker._port_open", side_effect=_port_open):
                with patch("subprocess.Popen", return_value=_FakeProc(4242)):
                    ok, detail = w.start(timeout_s=5)
        self.assertTrue(ok, detail)
        self.assertTrue(w._ready)
        self.assertEqual(w.pid, 4242)
        kill.assert_called()
        w.stop(reason="test")

    def test_start_sweeps_orphans_instead_of_stacking_ports(self) -> None:
        """Busy :3037 must not spawn a second s2 on :3038 without orphan kill."""
        port = 3037
        seq = {"n": 0}

        def _port_open(host, p):
            seq["n"] += 1
            # After kill, port free on first probe; ready on later probes.
            return seq["n"] > 1

        w = FishS2ServerWorker(cfg=self._cfg(), host="127.0.0.1", port=port)
        with patch("voice.fish_s2_backend._kill_s2_orphans", return_value=3) as kill:
            with patch("voice.fish_s2_worker.find_free_port") as free:
                with patch("voice.fish_s2_worker._port_open", side_effect=_port_open):
                    with patch("subprocess.Popen", return_value=_FakeProc(99)):
                        ok, _ = w.start(timeout_s=5)
        self.assertTrue(ok)
        kill.assert_called()
        free.assert_not_called()
        self.assertEqual(w.port, 3037)
        w.stop(reason="test")

    def test_C_D_E_multi_generate_same_session_no_reinit(self) -> None:
        port = _free_port()
        httpd = self._start_fake_http(port)
        try:
            cfg = self._cfg()
            w = FishS2ServerWorker(cfg=cfg, host="127.0.0.1", port=port)
            seq = {"n": 0}

            def _port_open(host, p):
                seq["n"] += 1
                return seq["n"] > 1

            with patch("voice.fish_s2_worker._port_open", side_effect=_port_open):
                with patch("subprocess.Popen", return_value=_FakeProc(5252)):
                    self.assertTrue(w.start(timeout_s=5)[0])

            # After start, real readiness uses real port check against fake HTTP.
            w._ready = True
            starts_before = w.stats.starts
            paths = []
            for i in range(3):
                fd, path = tempfile.mkstemp(suffix=".wav")
                os.close(fd)
                paths.append(path)
                ok, lat, det = w.generate_to_file(f"line {i}", path, max_tokens=32)
                self.assertTrue(ok, det)
                self.assertGreaterEqual(lat, 0.0)
                self.assertGreater(Path(path).stat().st_size, 1000)
            self.assertEqual(w.stats.starts, starts_before)
            self.assertEqual(w.stats.generates, 3)
            self.assertEqual(_FakeGenerateHandler.hits, 3)
            for p in paths:
                try:
                    os.unlink(p)
                except OSError:
                    pass
            w.stop(reason="test")
            self.assertFalse(w.is_alive())
            self.assertIsNone(w.pid)
        finally:
            httpd.shutdown()

    def test_F_G_session_end_no_orphan_handle(self) -> None:
        with patch("voice.fish_s2_backend._acquire_exclusive", return_value={"lease": True}):
            with patch("voice.fish_s2_backend._release_exclusive") as rel:
                with patch(
                    "voice.fish_s2_worker.FishS2ServerWorker.start",
                    return_value=(True, "ready"),
                ):
                    with patch(
                        "voice.fish_s2_worker.FishS2ServerWorker.is_ready",
                        return_value=True,
                    ):
                        with patch(
                            "voice.fish_s2_worker.FishS2ServerWorker.is_alive",
                            return_value=True,
                        ):
                            ok, det = begin_fish_tts_session(cfg=self._cfg())
                            self.assertTrue(ok, det)
                            snap = fish_session_snapshot()
                            self.assertTrue(snap["active"])
                            info = end_fish_tts_session(reason="test_end")
                            self.assertTrue(info["was_active"])
                            rel.assert_called()
                            self.assertFalse(fish_session_snapshot()["active"])
                            self.assertIsNone(get_worker(create=False))

    def test_H_cancel_kills_session(self) -> None:
        w = get_worker(cfg=self._cfg(), create=True)
        w._ready = True
        w._proc = MagicMock()
        w._proc.poll.return_value = None
        w._proc.pid = 9991
        cancel_s2_synthesis()
        self.assertFalse(fish_session_snapshot()["active"])
        self.assertIsNone(get_worker(create=False))

    def test_I_crash_recovery_restarts_worker(self) -> None:
        cfg = self._cfg()
        calls = {"n": 0}

        def _start(self, timeout_s=120.0):
            calls["n"] += 1
            self._ready = True
            return True, "ready"

        with patch("voice.fish_s2_backend._acquire_exclusive", return_value={}):
            with patch("voice.fish_s2_backend._release_exclusive"):
                with patch.object(FishS2ServerWorker, "start", _start):
                    with patch.object(
                        FishS2ServerWorker, "is_ready", lambda self: self._ready
                    ):
                        with patch.object(
                            FishS2ServerWorker, "is_alive", lambda self: True
                        ):
                            ok1, _ = begin_fish_tts_session(cfg=cfg)
                            self.assertTrue(ok1)
                            w = get_worker(create=False)
                            assert w is not None
                            w._ready = False
                            ok2, det = begin_fish_tts_session(cfg=cfg)
                            self.assertTrue(ok2, det)
                            self.assertGreaterEqual(calls["n"], 2)

    def test_J_piper_fallback_when_fish_forced_off(self) -> None:
        os.environ["ELIAN_FISH_FORCE_UNAVAILABLE"] = "1"
        reset_tts_engine_for_tests()
        reset_fish_health_for_tests()
        routed = RoutedTTS(prefer_fish=True)
        st = routed.health_check()
        self.assertEqual(st.extras.get("fish_readiness"), "UNAVAILABLE")

    def test_K_L_handoff_uses_exclusive_acquire_release(self) -> None:
        port = _free_port()
        httpd = self._start_fake_http(port)
        try:
            cfg = self._cfg()
            drop_worker_singleton(stop=False)
            w = FishS2ServerWorker(cfg=cfg, host="127.0.0.1", port=port)
            import voice.fish_s2_worker as wm

            wm._WORKER = w
            seq = {"n": 0}

            def _port_open(host, p):
                seq["n"] += 1
                return seq["n"] > 1

            with patch("voice.fish_s2_backend._acquire_exclusive", return_value={"lease": True, "unload": {"ok": True}}) as acq:
                with patch("voice.fish_s2_backend._release_exclusive") as rel:
                    with patch("voice.fish_s2_worker._port_open", side_effect=_port_open):
                        with patch("subprocess.Popen", return_value=_FakeProc(777)):
                            ok_s, det = begin_fish_tts_session(cfg=cfg)
                            self.assertTrue(ok_s, det)
                            acq.assert_called()
                            w._ready = True
                            fd, path = tempfile.mkstemp(suffix=".wav")
                            os.close(fd)
                            try:
                                # Avoid idle timer ending session between requests.
                                with patch(
                                    "voice.fish_s2_backend.schedule_fish_session_end"
                                ):
                                    ok, _lat, detail = synthesize_s2_wav(
                                        "handoff one", path, cfg=cfg, max_tokens=32
                                    )
                                    self.assertTrue(ok, detail)
                                    ok2, _lat2, detail2 = synthesize_s2_wav(
                                        "handoff two", path, cfg=cfg, max_tokens=32
                                    )
                                    self.assertTrue(ok2, detail2)
                                rel.assert_not_called()
                                end_fish_tts_session(reason="handoff_done")
                                rel.assert_called()
                            finally:
                                try:
                                    os.unlink(path)
                                except OSError:
                                    pass
        finally:
            httpd.shutdown()


if __name__ == "__main__":
    unittest.main()
