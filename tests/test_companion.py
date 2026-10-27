# ==========================
# ELIAN — Phone Companion MVP tests
# ==========================
from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import wave
from pathlib import Path

import numpy as np

from Config import get_config_value
from core.clock import FakeClock
from core.companion.protocol import STATUS_TIMEOUT, STATUS_UNAUTHORIZED, STATUS_UNAVAILABLE
from core.companion.registry import DeviceRegistry
from core.companion.server import CompanionServer
from core.companion.service import (
    CompanionService,
    CompanionSettings,
    redact_secrets,
    reset_companion_for_tests,
)
from core.companion.stt import decode_wav_pcm, transcribe_wav_bytes
from core.conversation_manager import ConversationManager, HandleResult
from core.event_bus import EventBus
from core.events import INCOMING_MESSAGE, Event, Priority
from core.model_client import FakeModelClient
from core.notification_inbox import InboxItem, note_inbox_item, reset_inbox_for_tests
from core.runtime import ElianRuntime
from core.tools import ToolRegistry, reset_registry_for_tests
from memory.manager import MemoryManager
from watchers.base import Watcher


def _wav_bytes(samples=None, rate=16000) -> bytes:
    audio = np.zeros(rate // 10, dtype=np.int16) if samples is None else np.asarray(samples, dtype=np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(audio.tobytes())
    return buf.getvalue()


def _settings(tmp: Path, **kwargs) -> CompanionSettings:
    cfg = CompanionSettings(
        enabled=True,
        bind="127.0.0.1",
        port=0,
        pairing_window_seconds=120,
        heartbeat_timeout_seconds=45,
        rate_limit_per_minute=60,
        allow_audio_upload=True,
        lan_only=True,
        command_timeout_seconds=5,
        max_command_chars=500,
        request_cache_ttl_seconds=300,
        registry_path=tmp / "companion_devices.json",
    )
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


def _cm(tmp: Path) -> ConversationManager:
    registry = ToolRegistry(confirm_medium=False)
    reset_registry_for_tests(registry)
    mem = MemoryManager(tmp / "mem.json")
    return ConversationManager(
        registry=registry,
        model=FakeModelClient([]),
        memory_manager=mem,
        memory=[],
    )


def _svc(tmp: Path, *, cm=None, clock=None, **kwargs) -> CompanionService:
    clock = clock or FakeClock(start=1_700_000_000.0)
    settings = _settings(tmp, **kwargs)
    registry = DeviceRegistry(
        settings.registry_path,
        clock=clock,
        pairing_window_seconds=settings.pairing_window_seconds,
        heartbeat_timeout_seconds=settings.heartbeat_timeout_seconds,
        rate_limit_per_minute=settings.rate_limit_per_minute,
    )
    return CompanionService(
        settings=settings,
        registry=registry,
        conversation_manager=cm,
        clock=clock,
    )


def _pair(svc: CompanionService, *, device_id="dev_test", name="Pixel") -> tuple[str, str]:
    start = svc.dispatch(
        method="POST",
        path="/v1/pair/start",
        headers={},
        body=b"{}",
        remote_ip="127.0.0.1",
    )
    assert start.ok, start.error
    body = json.dumps({
        "pairing_code": start.data["pairing_code"],
        "name": name,
        "device_id": device_id,
        "platform": "test",
    }).encode("utf-8")
    done = svc.dispatch(
        method="POST",
        path="/v1/pair",
        headers={},
        body=body,
        remote_ip="192.168.0.50",
    )
    assert done.ok, done.error
    return str(done.data["token"]), str(done.data["device_id"])


def _auth(token: str, device_id: str = "") -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    if device_id:
        headers["X-Elian-Device-Id"] = device_id
    return headers


class CompanionDisabledTests(unittest.TestCase):
    def test_default_config_is_off(self):
        self.assertFalse(bool(get_config_value("companion", "enabled", default=False)))

    def test_disabled_returns_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            svc = _svc(tmp)
            svc.settings.enabled = False
            env = svc.dispatch(
                method="POST",
                path="/v1/command",
                headers={},
                body=b'{"text":"Ok."}',
                remote_ip="127.0.0.1",
            )
            self.assertFalse(env.ok)
            self.assertEqual(env.status, STATUS_UNAVAILABLE)
            self.assertIn("wyłączony", env.error.lower())

    def test_runtime_does_not_start_companion_when_off(self):
        bus = EventBus()

        class FakeWatcher(Watcher):
            name = "fake"
            interval_seconds = 999

            def poll_once(self):
                pass

        runtime = ElianRuntime(
            bus=bus,
            watchers=[FakeWatcher(bus)],
            start_background=False,
        )
        runtime.start()
        try:
            self.assertIsNone(runtime._companion)
        finally:
            runtime.stop()


class CompanionPairingTests(unittest.TestCase):
    def tearDown(self):
        reset_companion_for_tests()
        reset_registry_for_tests(None)

    def test_pairing_issues_hashed_token(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            svc = _svc(tmp)
            token, device_id = _pair(svc)
            self.assertTrue(token)
            self.assertEqual(device_id, "dev_test")
            raw = (tmp / "companion_devices.json").read_text(encoding="utf-8")
            self.assertNotIn(token, raw)
            self.assertIn("token_hash", raw)
            device = svc.registry.authenticate(token)
            self.assertIsNotNone(device)
            self.assertEqual(device.device_id, device_id)

    def test_bad_pairing_code_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            svc = _svc(Path(td))
            svc.dispatch(method="POST", path="/v1/pair/start", headers={}, body=b"{}", remote_ip="127.0.0.1")
            env = svc.dispatch(
                method="POST",
                path="/v1/pair",
                headers={},
                body=b'{"pairing_code":"000000","name":"X"}',
                remote_ip="192.168.0.50",
            )
            self.assertFalse(env.ok)
            self.assertEqual(env.status, STATUS_UNAUTHORIZED)

    def test_expired_pairing_code_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            clock = FakeClock(start=1_700_000_000.0)
            svc = _svc(Path(td), clock=clock, pairing_window_seconds=10)
            start = svc.dispatch(
                method="POST", path="/v1/pair/start", headers={}, body=b"{}", remote_ip="127.0.0.1"
            )
            code = start.data["pairing_code"]
            clock.advance(11)
            env = svc.dispatch(
                method="POST",
                path="/v1/pair",
                headers={},
                body=json.dumps({"pairing_code": code, "name": "X"}).encode(),
                remote_ip="192.168.0.50",
            )
            self.assertFalse(env.ok)
            self.assertEqual(env.status, STATUS_UNAUTHORIZED)
            self.assertIn("wygas", env.error.lower())

    def test_pair_start_forbidden_off_loopback(self):
        with tempfile.TemporaryDirectory() as td:
            svc = _svc(Path(td))
            env = svc.dispatch(
                method="POST",
                path="/v1/pair/start",
                headers={},
                body=b"{}",
                remote_ip="192.168.0.50",
            )
            self.assertFalse(env.ok)
            self.assertEqual(env.status, "forbidden")


class CompanionAuthTests(unittest.TestCase):
    def tearDown(self):
        reset_companion_for_tests()
        reset_registry_for_tests(None)

    def test_unauthorized_command(self):
        with tempfile.TemporaryDirectory() as td:
            svc = _svc(Path(td), cm=_cm(Path(td)))
            env = svc.dispatch(
                method="POST",
                path="/v1/command",
                headers={},
                body=b'{"text":"Ok."}',
                remote_ip="192.168.0.50",
            )
            self.assertFalse(env.ok)
            self.assertEqual(env.status, STATUS_UNAUTHORIZED)
            self.assertEqual(svc._handle_calls, [])

    def test_invalid_token_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            svc = _svc(Path(td), cm=_cm(Path(td)))
            _pair(svc)
            env = svc.dispatch(
                method="POST",
                path="/v1/command",
                headers=_auth("not-a-real-token", "dev_test"),
                body=b'{"text":"Ok."}',
                remote_ip="192.168.0.50",
            )
            self.assertFalse(env.ok)
            self.assertEqual(env.status, STATUS_UNAUTHORIZED)

    def test_revoked_device_cannot_command(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            svc = _svc(tmp, cm=_cm(tmp))
            token, device_id = _pair(svc)
            revoked = svc.dispatch(
                method="POST",
                path="/v1/pair/revoke",
                headers={},
                body=json.dumps({"device_id": device_id}).encode(),
                remote_ip="127.0.0.1",
            )
            self.assertTrue(revoked.ok)
            env = svc.dispatch(
                method="POST",
                path="/v1/command",
                headers=_auth(token, device_id),
                body=b'{"text":"Ok."}',
                remote_ip="192.168.0.50",
            )
            self.assertFalse(env.ok)
            self.assertEqual(env.status, STATUS_UNAUTHORIZED)
            self.assertEqual(svc._handle_calls, [])

    def test_device_id_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            svc = _svc(Path(td), cm=_cm(Path(td)))
            token, device_id = _pair(svc)
            env = svc.dispatch(
                method="POST",
                path="/v1/command",
                headers=_auth(token, "other_device"),
                body=b'{"text":"Ok."}',
                remote_ip="192.168.0.50",
            )
            self.assertFalse(env.ok)
            self.assertEqual(env.status, STATUS_UNAUTHORIZED)
            self.assertEqual(svc._handle_calls, [])


class CompanionRateLimitTests(unittest.TestCase):
    def tearDown(self):
        reset_companion_for_tests()
        reset_registry_for_tests(None)

    def test_ip_rate_limit(self):
        with tempfile.TemporaryDirectory() as td:
            svc = _svc(Path(td), rate_limit_per_minute=2)
            token, device_id = _pair(svc)
            headers = _auth(token, device_id)
            env1 = svc.dispatch(
                method="GET", path="/v1/status", headers=headers, remote_ip="192.168.0.77"
            )
            env2 = svc.dispatch(
                method="GET", path="/v1/status", headers=headers, remote_ip="192.168.0.77"
            )
            env3 = svc.dispatch(
                method="GET", path="/v1/status", headers=headers, remote_ip="192.168.0.77"
            )
            self.assertTrue(env1.ok)
            self.assertTrue(env2.ok)
            self.assertFalse(env3.ok)
            self.assertEqual(env3.status, "rate_limited")


class CompanionCommandIngressTests(unittest.TestCase):
    def tearDown(self):
        reset_companion_for_tests()
        reset_registry_for_tests(None)

    def test_authorized_command_enters_conversation_manager(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cm = _cm(tmp)
            seen = []
            original = cm.handle

            def wrapped(text):
                seen.append(text)
                return original(text)

            cm.handle = wrapped
            svc = _svc(tmp, cm=cm)
            token, device_id = _pair(svc)
            env = svc.dispatch(
                method="POST",
                path="/v1/command",
                headers=_auth(token, device_id),
                body=b'{"text":"Ok.","request_id":"corr-1"}',
                remote_ip="192.168.0.50",
            )
            self.assertTrue(env.ok, env.error)
            self.assertEqual(seen, ["Ok."])
            self.assertEqual(svc._handle_calls[0]["text"], "Ok.")
            self.assertEqual(svc._handle_calls[0]["correlation_id"], "corr-1")
            self.assertEqual(env.data.get("correlation_id"), "corr-1")
            self.assertEqual(env.data.get("result", {}).get("channel"), "companion")
            self.assertEqual(env.data.get("result", {}).get("companion_device_id"), device_id)
            self.assertEqual(env.data.get("result", {}).get("correlation_id"), "corr-1")

    def test_empty_command_does_not_idle_timeout(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cm = _cm(tmp)
            svc = _svc(tmp, cm=cm)
            token, device_id = _pair(svc)
            env = svc.dispatch(
                method="POST",
                path="/v1/command",
                headers=_auth(token, device_id),
                body=b'{"text":"  "}',
                remote_ip="192.168.0.50",
            )
            self.assertFalse(env.ok)
            self.assertTrue(cm.active is False or env.status == "invalid")

    def test_command_length_limit(self):
        with tempfile.TemporaryDirectory() as td:
            svc = _svc(Path(td), cm=_cm(Path(td)), max_command_chars=20)
            token, device_id = _pair(svc)
            env = svc.dispatch(
                method="POST",
                path="/v1/command",
                headers=_auth(token, device_id),
                body=json.dumps({"text": "x" * 21}).encode(),
                remote_ip="192.168.0.50",
            )
            self.assertFalse(env.ok)
            self.assertEqual(env.status, "invalid")
            self.assertEqual(svc._handle_calls, [])

    def test_malformed_json_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            svc = _svc(Path(td), cm=_cm(Path(td)))
            token, device_id = _pair(svc)
            env = svc.dispatch(
                method="POST",
                path="/v1/command",
                headers=_auth(token, device_id),
                body=b"{not-json",
                remote_ip="192.168.0.50",
            )
            self.assertFalse(env.ok)
            self.assertEqual(env.status, "invalid")
            self.assertIn("json", env.error.lower())

    def test_duplicate_request_id_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cm = _cm(tmp)
            svc = _svc(tmp, cm=cm)
            token, device_id = _pair(svc)
            body = json.dumps({"text": "Ok.", "request_id": "dup-1"}).encode()
            first = svc.dispatch(
                method="POST",
                path="/v1/command",
                headers=_auth(token, device_id),
                body=body,
                remote_ip="192.168.0.50",
            )
            second = svc.dispatch(
                method="POST",
                path="/v1/command",
                headers=_auth(token, device_id),
                body=body,
                remote_ip="192.168.0.50",
            )
            self.assertTrue(first.ok)
            self.assertTrue(second.ok)
            self.assertTrue(second.data.get("duplicate"))
            self.assertEqual(len(svc._handle_calls), 1)
            self.assertEqual(first.data.get("reply"), second.data.get("reply"))

    def test_offline_elian_without_cm(self):
        with tempfile.TemporaryDirectory() as td:
            svc = _svc(Path(td), cm=None)
            token, device_id = _pair(svc)
            env = svc.dispatch(
                method="POST",
                path="/v1/command",
                headers=_auth(token, device_id),
                body=b'{"text":"Ok."}',
                remote_ip="192.168.0.50",
            )
            self.assertFalse(env.ok)
            self.assertEqual(env.status, STATUS_UNAVAILABLE)
            self.assertTrue(env.data.get("offline"))

    def test_conversation_manager_failure_safe_serialization(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)

            class BoomCM:
                active = False

                def handle_from_companion(self, text, *, device_id="", correlation_id=""):
                    raise RuntimeError("secret_token_abc /path/to/secret")

            svc = _svc(tmp, cm=BoomCM())
            token, device_id = _pair(svc)
            env = svc.dispatch(
                method="POST",
                path="/v1/command",
                headers=_auth(token, device_id),
                body=b'{"text":"Ok."}',
                remote_ip="192.168.0.50",
            )
            self.assertFalse(env.ok)
            self.assertEqual(env.status, STATUS_UNAVAILABLE)
            blob = json.dumps(env.to_dict())
            self.assertNotIn("secret_token_abc", blob)
            self.assertNotIn("/path/to/secret", blob)
            self.assertNotIn("RuntimeError", blob)

    def test_command_timeout_degraded(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)

            class SlowCM:
                active = False

                def handle_from_companion(self, text, *, device_id="", correlation_id=""):
                    time.sleep(1.5)
                    return HandleResult.speak("późno", source="slow", data={"correlation_id": correlation_id})

            svc = _svc(tmp, cm=SlowCM(), command_timeout_seconds=0.3)
            token, device_id = _pair(svc)
            env = svc.dispatch(
                method="POST",
                path="/v1/command",
                headers=_auth(token, device_id),
                body=b'{"text":"Ok.","request_id":"to-1"}',
                remote_ip="192.168.0.50",
            )
            self.assertFalse(env.ok)
            self.assertEqual(env.status, STATUS_TIMEOUT)
            self.assertTrue(env.data.get("timeout"))
            self.assertEqual(env.data.get("correlation_id"), "to-1")

    def test_concurrent_two_device_commands(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cm = _cm(tmp)
            svc = _svc(tmp, cm=cm, rate_limit_per_minute=200)
            t1, d1 = _pair(svc, device_id="dev_a", name="A")
            t2, d2 = _pair(svc, device_id="dev_b", name="B")
            results = {}

            def send(key, token, device_id, text):
                results[key] = svc.dispatch(
                    method="POST",
                    path="/v1/command",
                    headers=_auth(token, device_id),
                    body=json.dumps({"text": text, "request_id": f"c-{key}"}).encode(),
                    remote_ip="192.168.0.50",
                )

            threads = [
                threading.Thread(target=send, args=("a", t1, d1, "Ok.")),
                threading.Thread(target=send, args=("b", t2, d2, "Ok.")),
            ]
            for th in threads:
                th.start()
            for th in threads:
                th.join(timeout=10)
            self.assertTrue(results["a"].ok, results["a"].error)
            self.assertTrue(results["b"].ok, results["b"].error)
            self.assertEqual(len(svc._handle_calls), 2)
            device_ids = {row["device_id"] for row in svc._handle_calls}
            self.assertEqual(device_ids, {"dev_a", "dev_b"})


class CompanionReconnectTests(unittest.TestCase):
    def tearDown(self):
        reset_companion_for_tests()
        reset_registry_for_tests(None)

    def test_offline_then_reconnect(self):
        with tempfile.TemporaryDirectory() as td:
            clock = FakeClock(start=1_700_000_000.0)
            svc = _svc(Path(td), clock=clock)
            token, device_id = _pair(svc)
            self.assertTrue(svc.registry.is_online(device_id))
            clock.advance(50)
            self.assertFalse(svc.registry.is_online(device_id))
            env = svc.dispatch(
                method="POST",
                path="/v1/heartbeat",
                headers=_auth(token, device_id),
                remote_ip="192.168.0.50",
            )
            self.assertTrue(env.ok)
            self.assertTrue(svc.registry.is_online(device_id))
            self.assertEqual(env.data.get("connection"), "online")

    def test_server_restart_preserves_token(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            clock = FakeClock(start=1_700_000_000.0)
            svc1 = _svc(tmp, clock=clock)
            token, device_id = _pair(svc1)
            # Simulate restart: new service, same registry file.
            svc2 = _svc(tmp, cm=_cm(tmp), clock=clock)
            env = svc2.dispatch(
                method="POST",
                path="/v1/command",
                headers=_auth(token, device_id),
                body=b'{"text":"Ok."}',
                remote_ip="192.168.0.50",
            )
            self.assertTrue(env.ok, env.error)
            self.assertEqual(env.data.get("result", {}).get("companion_device_id"), device_id)


class CompanionNotificationTests(unittest.TestCase):
    def setUp(self):
        reset_inbox_for_tests()

    def tearDown(self):
        reset_inbox_for_tests()
        reset_companion_for_tests()
        reset_registry_for_tests(None)

    def test_notification_delivery_and_duplicate_event(self):
        with tempfile.TemporaryDirectory() as td:
            svc = _svc(Path(td))
            token, device_id = _pair(svc)
            item = InboxItem(
                id="n1",
                source="windows",
                app="Mail",
                title="Paczka",
                body="Kurier jest w drodze",
            )
            note_inbox_item(item)
            first = svc.fanout_inbox(item)
            second = svc.fanout_inbox(item)
            self.assertIsNotNone(first)
            self.assertIsNone(second)
            pending = svc.pending_events(device_id)
            self.assertEqual(len(pending), 1)
            env = svc.dispatch(
                method="GET",
                path="/v1/events",
                headers=_auth(token, device_id),
                remote_ip="192.168.0.50",
            )
            self.assertTrue(env.ok)
            self.assertEqual(len(env.data["events"]), 1)
            self.assertIn("Paczka", env.data["events"][0]["title"])

    def test_muted_device_skips_notifications(self):
        with tempfile.TemporaryDirectory() as td:
            svc = _svc(Path(td))
            token, device_id = _pair(svc)
            mute = svc.dispatch(
                method="POST",
                path="/v1/quick-action",
                headers=_auth(token, device_id),
                body=b'{"id":"mute"}',
                remote_ip="192.168.0.50",
            )
            self.assertTrue(mute.ok)
            svc.fanout_inbox(InboxItem(id="n2", source="windows", title="Cicho", body="x"))
            self.assertEqual(svc.pending_events(device_id), [])

    def test_bus_event_bridges_inbox_source(self):
        with tempfile.TemporaryDirectory() as td:
            svc = _svc(Path(td))
            _token, device_id = _pair(svc)
            svc.on_bus_event(
                Event(
                    type=INCOMING_MESSAGE,
                    source="os_notifications",
                    data={"title": "SMS", "body": "Hej", "item_id": "m1"},
                    priority=Priority.NORMAL,
                    id="evt_bus_1",
                )
            )
            pending = svc.pending_events(device_id)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].title, "SMS")

    def test_offline_device_still_queues_for_reconnect(self):
        with tempfile.TemporaryDirectory() as td:
            clock = FakeClock(start=1_700_000_000.0)
            svc = _svc(Path(td), clock=clock)
            token, device_id = _pair(svc)
            clock.advance(50)
            self.assertFalse(svc.registry.is_online(device_id))
            svc.enqueue_event(
                event_id="queued_offline",
                event_type="notification",
                title="Przypomnienie",
                body="Woda",
                source="reminders",
            )
            self.assertEqual(len(svc.pending_events(device_id)), 1)
            svc.dispatch(
                method="POST",
                path="/v1/heartbeat",
                headers=_auth(token, device_id),
                remote_ip="192.168.0.50",
            )
            env = svc.dispatch(
                method="GET",
                path="/v1/events",
                headers=_auth(token, device_id),
                remote_ip="192.168.0.50",
            )
            self.assertEqual(len(env.data["events"]), 1)


class CompanionDeferredAndAudioTests(unittest.TestCase):
    def tearDown(self):
        reset_companion_for_tests()
        reset_registry_for_tests(None)

    def test_camera_location_handoff_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            svc = _svc(Path(td))
            token, device_id = _pair(svc)
            for path in ("/v1/camera", "/v1/location", "/v1/handoff"):
                env = svc.dispatch(
                    method="POST",
                    path=path,
                    headers=_auth(token, device_id),
                    body=b"{}",
                    remote_ip="192.168.0.50",
                )
                self.assertEqual(env.status, STATUS_UNAVAILABLE)
                self.assertFalse(env.ok)

    def test_audio_upload_uses_stt_seam_then_handle(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cm = _cm(tmp)
            seen = []
            original = cm.handle

            def wrapped(text):
                seen.append(text)
                return original(text)

            cm.handle = wrapped
            svc = _svc(tmp, cm=cm)
            svc.transcribe_fn = lambda audio: "Ok."
            token, device_id = _pair(svc)
            env = svc.dispatch(
                method="POST",
                path="/v1/audio",
                headers=_auth(token, device_id),
                body=_wav_bytes(),
                remote_ip="192.168.0.50",
            )
            self.assertTrue(env.ok, env.error)
            self.assertEqual(env.data.get("transcript"), "Ok.")
            self.assertEqual(seen, ["Ok."])

    def test_audio_invalid_without_wav(self):
        with tempfile.TemporaryDirectory() as td:
            svc = _svc(Path(td), cm=_cm(Path(td)))
            token, device_id = _pair(svc)
            env = svc.dispatch(
                method="POST",
                path="/v1/audio",
                headers=_auth(token, device_id),
                body=b"not-a-wav",
                remote_ip="192.168.0.50",
            )
            self.assertFalse(env.ok)
            self.assertEqual(env.status, "invalid")

    def test_stt_decode_wav(self):
        pcm = decode_wav_pcm(_wav_bytes(rate=16000))
        self.assertGreater(pcm.size, 0)
        text, status = transcribe_wav_bytes(_wav_bytes(), transcribe_fn=lambda _a: "hej")
        self.assertEqual(status, "ok")
        self.assertEqual(text, "hej")


class CompanionHttpAndSecurityTests(unittest.TestCase):
    def tearDown(self):
        reset_companion_for_tests()
        reset_registry_for_tests(None)

    def test_http_health_and_unauthorized(self):
        with tempfile.TemporaryDirectory() as td:
            svc = _svc(Path(td), cm=_cm(Path(td)))
            server = CompanionServer(svc, bind="127.0.0.1", port=0)
            server.start()
            try:
                time.sleep(0.05)
                base = f"http://127.0.0.1:{server.actual_port}"
                with urllib.request.urlopen(base + "/v1/health", timeout=2) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(payload.get("ok"))
                req = urllib.request.Request(
                    base + "/v1/command",
                    data=b'{"text":"Ok."}',
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(req, timeout=2)
                self.assertEqual(caught.exception.code, 401)
            finally:
                server.stop()

    def test_client_html_has_no_secrets(self):
        html = Path(__file__).resolve().parents[1] / "core" / "companion" / "client.html"
        text = html.read_text(encoding="utf-8")
        lowered = text.lower()
        self.assertNotIn("token_urlsafe", lowered)
        self.assertNotIn("admin", lowered)
        self.assertNotIn("secret", lowered)
        self.assertNotIn("password", lowered)
        self.assertIn("localStorage", text)

    def test_redact_secrets_masks_bearer(self):
        masked = redact_secrets("Authorization: Bearer supersecrettokenvalue")
        self.assertNotIn("supersecrettokenvalue", masked)
        self.assertIn("***", masked)

    def test_public_lan_blocked_when_lan_only(self):
        with tempfile.TemporaryDirectory() as td:
            svc = _svc(Path(td), lan_only=True)
            env = svc.dispatch(
                method="GET",
                path="/v1/capabilities",
                headers={},
                remote_ip="8.8.8.8",
            )
            self.assertFalse(env.ok)
            self.assertEqual(env.status, "forbidden")


if __name__ == "__main__":
    unittest.main()
