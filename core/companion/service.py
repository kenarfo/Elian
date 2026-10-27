# ==========================
# ELIAN — Phone Companion service (command ingress + notification bridge)
# ==========================
"""LAN companion brain-side: pairing, auth, events, ConversationManager ingress.

Commands enter ConversationManager.handle — never ToolRegistry directly.
Notifications reuse core.notification_inbox / EventBus. Not Twilio.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from Config import DATA_DIR, get_config_value
from core.clock import Clock, SystemClock
from core.companion.protocol import (
    DEFERRED_CAPABILITIES,
    QUICK_ACTIONS,
    CompanionEnvelope,
    capabilities,
    forbidden,
    invalid,
    ok,
    rate_limited,
    timed_out,
    unauthorized,
    unavailable,
    STATUS_UNAVAILABLE,
)
from core.companion.registry import CompanionDevice, DeviceRegistry
from core.events import (
    CALENDAR_UPCOMING,
    INCOMING_MESSAGE,
    OS_NOTIFICATION,
    REMINDER_DUE,
    Event,
)
from core.notification_inbox import InboxItem

_CLIENT_HTML = Path(__file__).with_name("client.html")
_MAX_BODY = 2 * 1024 * 1024
_DEFAULT_MAX_COMMAND = 500
_NOTIFY_TYPES = {INCOMING_MESSAGE, OS_NOTIFICATION, REMINDER_DUE, CALENDAR_UPCOMING}
_TOKEN_RE = re.compile(r"(Bearer\s+)([A-Za-z0-9_\-\.=]+)", re.IGNORECASE)

_SERVICE: Optional["CompanionService"] = None


@dataclass
class CompanionSettings:
    enabled: bool = False
    bind: str = "0.0.0.0"
    port: int = 8742
    pairing_window_seconds: float = 120.0
    heartbeat_timeout_seconds: float = 45.0
    rate_limit_per_minute: int = 60
    allow_audio_upload: bool = True
    lan_only: bool = True
    also_speak: bool = False
    max_queued_events: int = 50
    command_timeout_seconds: float = 25.0
    max_command_chars: int = 500
    request_cache_ttl_seconds: float = 300.0
    registry_path: Optional[Path] = None

    @classmethod
    def from_config(cls) -> "CompanionSettings":
        return cls(
            enabled=bool(get_config_value("companion", "enabled", default=False)),
            bind=str(get_config_value("companion", "bind", default="0.0.0.0") or "0.0.0.0"),
            port=int(get_config_value("companion", "port", default=8742) or 8742),
            pairing_window_seconds=float(
                get_config_value("companion", "pairing_window_seconds", default=120) or 120
            ),
            heartbeat_timeout_seconds=float(
                get_config_value("companion", "heartbeat_timeout_seconds", default=45) or 45
            ),
            rate_limit_per_minute=int(
                get_config_value("companion", "rate_limit_per_minute", default=60) or 60
            ),
            allow_audio_upload=bool(
                get_config_value("companion", "allow_audio_upload", default=True)
            ),
            lan_only=bool(get_config_value("companion", "lan_only", default=True)),
            also_speak=bool(get_config_value("companion", "also_speak", default=False)),
            max_queued_events=int(
                get_config_value("companion", "max_queued_events", default=50) or 50
            ),
            command_timeout_seconds=float(
                get_config_value("companion", "command_timeout_seconds", default=25) or 25
            ),
            max_command_chars=int(
                get_config_value("companion", "max_command_chars", default=_DEFAULT_MAX_COMMAND)
                or _DEFAULT_MAX_COMMAND
            ),
            request_cache_ttl_seconds=float(
                get_config_value("companion", "request_cache_ttl_seconds", default=300) or 300
            ),
        )


@dataclass
class QueuedEvent:
    id: str
    type: str
    title: str = ""
    body: str = ""
    source: str = ""
    created_at: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "body": self.body,
            "source": self.source,
            "created_at": self.created_at,
        }
        if self.extra:
            payload["extra"] = dict(self.extra)
        return payload


@dataclass
class _CachedCommand:
    correlation_id: str
    created_at: float
    envelope: CompanionEnvelope


def is_loopback(ip: str) -> bool:
    host = str(ip or "").split("%")[0].strip().lower()
    if host.startswith("::ffff:"):
        host = host.split(":")[-1]
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")


def is_private_ip(ip: str) -> bool:
    host = str(ip or "").split("%")[0].strip().lower()
    if host in ("127.0.0.1", "::1", "localhost", "0.0.0.0"):
        return True
    if host.startswith("::ffff:"):
        host = host.split(":")[-1]
    parts = host.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return a == 10 or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31)


def _bearer(headers: Dict[str, str]) -> str:
    raw = headers.get("authorization") or headers.get("Authorization") or ""
    if raw.lower().startswith("bearer "):
        return raw.split(" ", 1)[1].strip()
    return ""


def _parse_json_body(body: bytes) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Returns (payload, error). Empty body → empty dict; malformed → error."""
    if not body:
        return {}, None
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None, "Niepoprawne kodowanie UTF-8."
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, "Niepoprawny JSON."
    if not isinstance(parsed, dict):
        return None, "Oczekiwano obiektu JSON."
    return parsed, None


def _safe_error_message(exc: BaseException) -> str:
    """Public error string — never leak paths, tokens, or exception types to phone."""
    return "Elian nie mógł obsłużyć komendy."


def redact_secrets(text: str) -> str:
    return _TOKEN_RE.sub(r"\1***", str(text or ""))


def _new_correlation_id() -> str:
    return f"cmp_{uuid.uuid4().hex}"


class CompanionService:
    """In-process companion protocol. HTTP is a thin adapter around dispatch()."""

    def __init__(
        self,
        *,
        settings: Optional[CompanionSettings] = None,
        registry: Optional[DeviceRegistry] = None,
        conversation_manager=None,
        clock: Optional[Clock] = None,
        transcribe_fn=None,
    ):
        self.settings = settings or CompanionSettings.from_config()
        self.clock = clock or SystemClock()
        path = self.settings.registry_path or (Path(DATA_DIR) / "companion_devices.json")
        self.registry = registry or DeviceRegistry(
            path,
            clock=self.clock,
            pairing_window_seconds=self.settings.pairing_window_seconds,
            heartbeat_timeout_seconds=self.settings.heartbeat_timeout_seconds,
            rate_limit_per_minute=self.settings.rate_limit_per_minute,
        )
        self.conversation_manager = conversation_manager
        self.transcribe_fn = transcribe_fn
        self._cm_lock = threading.Lock()
        self._queues: Dict[str, List[QueuedEvent]] = {}
        self._queue_lock = threading.Lock()
        self._seen_event_ids: set[str] = set()
        self._handle_calls: List[Dict[str, str]] = []
        self._request_cache: Dict[str, _CachedCommand] = {}
        self._cache_lock = threading.Lock()

    def attach_conversation_manager(self, manager) -> None:
        self.conversation_manager = manager

    def disabled_envelope(self) -> CompanionEnvelope:
        return unavailable("Companion jest wyłączony.")

    def dispatch(
        self,
        *,
        method: str,
        path: str,
        headers: Dict[str, str],
        body: bytes = b"",
        remote_ip: str = "127.0.0.1",
    ) -> CompanionEnvelope:
        try:
            return self._dispatch_inner(
                method=method,
                path=path,
                headers=headers,
                body=body,
                remote_ip=remote_ip,
            )
        except Exception:
            # Never serialize stack traces / secrets to the phone client.
            return unavailable("Companion napotkał błąd wewnętrzny.")

    def _dispatch_inner(
        self,
        *,
        method: str,
        path: str,
        headers: Dict[str, str],
        body: bytes,
        remote_ip: str,
    ) -> CompanionEnvelope:
        method = str(method or "GET").upper()
        parsed = urlparse(str(path or "/"))
        route = parsed.path.rstrip("/") or "/"
        if len(body or b"") > _MAX_BODY:
            return invalid("Zbyt duże żądanie.")

        if self.settings.lan_only and not is_private_ip(remote_ip):
            return forbidden("Companion działa tylko w sieci lokalnej.")

        if route in ("/health", "/v1/health"):
            return self._health()

        if not self.settings.enabled:
            return self.disabled_envelope()

        if not self.registry.ip_limiter.allow(f"ip:{remote_ip}"):
            return rate_limited()

        if method == "GET" and route == "/":
            return self._client_html()
        if method == "GET" and route == "/v1/capabilities":
            return ok({"capabilities": capabilities(
                audio=self.settings.allow_audio_upload,
                enabled=True,
            )})
        if method == "POST" and route == "/v1/pair/start":
            return self._pair_start(remote_ip)
        if method == "POST" and route in ("/v1/pair", "/v1/pair/complete"):
            return self._pair_complete(body, remote_ip)
        if method == "POST" and route == "/v1/pair/revoke":
            return self._pair_revoke(headers, body, remote_ip)
        if method == "POST" and route in ("/v1/camera", "/v1/photos"):
            return unavailable(DEFERRED_CAPABILITIES["camera"])
        if method == "POST" and route == "/v1/location":
            return unavailable(DEFERRED_CAPABILITIES["location"])
        if method == "POST" and route == "/v1/handoff":
            return unavailable(DEFERRED_CAPABILITIES["handoff"])
        if method == "POST" and route in ("/v1/in-transit", "/v1/in_transit"):
            return unavailable(DEFERRED_CAPABILITIES["in_transit"])

        device, err = self._auth_device(headers)
        if err is not None:
            return err

        if method == "GET" and route == "/v1/status":
            return self._status(device)
        if method == "POST" and route == "/v1/heartbeat":
            return self._heartbeat(device)
        if method == "POST" and route == "/v1/command":
            return self._command(device, body, headers)
        if method == "POST" and route == "/v1/audio":
            return self._audio(device, body, headers)
        if method == "GET" and route == "/v1/events":
            return self._events(device)
        if method == "POST" and route == "/v1/events/ack":
            return self._events_ack(device, body)
        if method == "POST" and route == "/v1/quick-action":
            return self._quick_action(device, body, headers)
        if method == "POST" and route == "/v1/settings":
            return self._settings(device, body)
        return invalid("Nieznany endpoint.")

    def _health(self) -> CompanionEnvelope:
        enabled = bool(self.settings.enabled)
        data = {
            "enabled": enabled,
            "status": "ok" if enabled else STATUS_UNAVAILABLE,
            "capabilities": capabilities(
                audio=self.settings.allow_audio_upload,
                enabled=enabled,
            ),
        }
        if not enabled:
            return unavailable("Companion jest wyłączony.", data=data)
        return ok(data)

    def _client_html(self) -> CompanionEnvelope:
        try:
            html = _CLIENT_HTML.read_text(encoding="utf-8")
        except OSError:
            html = "<!doctype html><meta charset=utf-8><p>Brak client.html</p>"
        env = ok()
        env.content_type = "text/html; charset=utf-8"
        env.body_bytes = html.encode("utf-8")
        return env

    def _pair_start(self, remote_ip: str) -> CompanionEnvelope:
        if not is_loopback(remote_ip):
            return forbidden("Parowanie rozpoczyna się na komputerze z Elianem.")
        code, expires = self.registry.start_pairing()
        print(
            f"[COMPANION] kod parowania: {code} "
            f"(ważny {int(self.settings.pairing_window_seconds)} s)"
        )
        return ok({
            "pairing_code": code,
            "expires_at": expires,
            "window_seconds": self.settings.pairing_window_seconds,
        })

    def _pair_complete(self, body: bytes, remote_ip: str) -> CompanionEnvelope:
        if not self.registry.pair_limiter.allow(f"pair:{remote_ip}"):
            return rate_limited()
        payload, err = _parse_json_body(body)
        if err:
            return invalid(err)
        assert payload is not None
        device, token, error = self.registry.complete_pairing(
            pairing_code=str(payload.get("pairing_code") or payload.get("code") or ""),
            device_id=str(payload.get("device_id") or ""),
            name=str(payload.get("name") or payload.get("device_name") or ""),
            platform=str(payload.get("platform") or ""),
        )
        if error or device is None:
            return unauthorized(error or "Nie udało się sparować.")
        self.registry.heartbeat(device.device_id, persist=True)
        # Token returned once over the wire — never log plaintext.
        print(f"[COMPANION] sparowano urządzenie {device.device_id}")
        return ok({
            "device_id": device.device_id,
            "token": token,
            "name": device.name,
            "permissions": dict(device.permissions),
        })

    def _pair_revoke(self, headers: Dict[str, str], body: bytes, remote_ip: str) -> CompanionEnvelope:
        payload, err = _parse_json_body(body)
        if err:
            return invalid(err)
        assert payload is not None
        device_id = str(payload.get("device_id") or "")
        loopback = is_loopback(remote_ip)
        if not loopback:
            device, auth_err = self._auth_device(headers)
            if auth_err is not None:
                return auth_err
            assert device is not None
            device_id = device_id or device.device_id
            if device.device_id != device_id:
                return forbidden("Możesz odłączyć tylko to urządzenie.")
        if not device_id:
            return invalid("Podaj device_id.")
        if not self.registry.revoke(device_id):
            return invalid("Nie znam tego urządzenia.")
        with self._queue_lock:
            self._queues.pop(device_id, None)
        with self._cache_lock:
            stale = [k for k in self._request_cache if k.startswith(f"{device_id}:")]
            for key in stale:
                self._request_cache.pop(key, None)
        return ok({"revoked": device_id})

    def _auth_device(self, headers: Dict[str, str]) -> Tuple[Optional[CompanionDevice], Optional[CompanionEnvelope]]:
        token = _bearer(headers)
        device = self.registry.authenticate(token)
        if device is None:
            return None, unauthorized()
        claimed = (
            headers.get("x-elian-device-id")
            or headers.get("X-Elian-Device-Id")
            or ""
        ).strip()
        if claimed and claimed != device.device_id:
            return None, unauthorized("To urządzenie nie pasuje do tokenu.")
        self.registry.heartbeat(device.device_id, persist=False)
        return device, None

    def _status(self, device: CompanionDevice) -> CompanionEnvelope:
        online = self.registry.is_online(device.device_id)
        return ok({
            "connection": "online" if online else "offline",
            "device": device.to_public_dict(online=online),
            "server_time": float(self.clock.time()),
            "capabilities": capabilities(audio=self.settings.allow_audio_upload),
        })

    def _heartbeat(self, device: CompanionDevice) -> CompanionEnvelope:
        self.registry.heartbeat(device.device_id, persist=True)
        return ok({
            "connection": "online",
            "device_id": device.device_id,
            "server_time": float(self.clock.time()),
        })

    def _resolve_correlation(
        self,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> str:
        headers = headers or {}
        rid = (
            str(payload.get("request_id") or payload.get("correlation_id") or "").strip()
            or str(
                headers.get("x-request-id")
                or headers.get("X-Request-Id")
                or headers.get("idempotency-key")
                or headers.get("Idempotency-Key")
                or ""
            ).strip()
        )
        if rid and len(rid) <= 128:
            return rid
        return _new_correlation_id()

    def _cache_key(self, device_id: str, correlation_id: str) -> str:
        return f"{device_id}:{correlation_id}"

    def _get_cached(self, device_id: str, correlation_id: str) -> Optional[CompanionEnvelope]:
        key = self._cache_key(device_id, correlation_id)
        ttl = float(self.settings.request_cache_ttl_seconds)
        now = float(self.clock.time())
        with self._cache_lock:
            row = self._request_cache.get(key)
            if row is None:
                return None
            if (now - row.created_at) > ttl:
                self._request_cache.pop(key, None)
                return None
            # Return a shallow copy so callers cannot mutate the cache.
            cached = row.envelope
            return CompanionEnvelope(
                ok=cached.ok,
                status=cached.status,
                error=cached.error,
                data=dict(cached.data),
                http_status=cached.http_status,
            )

    def _put_cached(self, device_id: str, correlation_id: str, env: CompanionEnvelope) -> None:
        key = self._cache_key(device_id, correlation_id)
        with self._cache_lock:
            self._request_cache[key] = _CachedCommand(
                correlation_id=correlation_id,
                created_at=float(self.clock.time()),
                envelope=CompanionEnvelope(
                    ok=env.ok,
                    status=env.status,
                    error=env.error,
                    data=dict(env.data),
                    http_status=env.http_status,
                ),
            )
            # Cap cache size (drop oldest by created_at).
            if len(self._request_cache) > 256:
                ordered = sorted(
                    self._request_cache.items(),
                    key=lambda item: item[1].created_at,
                )
                for stale_key, _ in ordered[: len(ordered) - 200]:
                    self._request_cache.pop(stale_key, None)

    def submit_command(
        self,
        text: str,
        *,
        device_id: str = "",
        correlation_id: str = "",
        use_cache: bool = True,
    ) -> CompanionEnvelope:
        raw = str(text or "").strip()
        cid = str(correlation_id or "").strip() or _new_correlation_id()
        max_chars = max(1, int(self.settings.max_command_chars or _DEFAULT_MAX_COMMAND))
        if not raw:
            return invalid("Pusta komenda.", data={"correlation_id": cid})
        if len(raw) > max_chars:
            return invalid("Komenda jest za długa.", data={"correlation_id": cid})

        if use_cache and device_id:
            cached = self._get_cached(device_id, cid)
            if cached is not None:
                cached.data["correlation_id"] = cid
                cached.data["duplicate"] = True
                return cached

        manager = self.conversation_manager
        if manager is None:
            return unavailable(
                "ConversationManager nie jest podłączony.",
                data={"correlation_id": cid, "offline": True},
            )

        self._handle_calls.append({"text": raw, "correlation_id": cid, "device_id": device_id})
        timeout_s = max(0.5, float(self.settings.command_timeout_seconds or 25.0))
        holder: Dict[str, Any] = {}
        done = threading.Event()

        def _run() -> None:
            try:
                with self._cm_lock:
                    holder["result"] = manager.handle_from_companion(
                        raw,
                        device_id=device_id,
                        correlation_id=cid,
                    )
            except Exception as exc:
                holder["error"] = exc
            finally:
                done.set()

        worker = threading.Thread(target=_run, name="elian-companion-cmd", daemon=True)
        worker.start()
        if not done.wait(timeout_s):
            env = timed_out(
                "Elian nie odpowiedział na czas.",
                data={
                    "correlation_id": cid,
                    "timeout": True,
                    "reply": "Elian nie odpowiedział na czas. Spróbuj ponownie.",
                },
            )
            if use_cache and device_id:
                self._put_cached(device_id, cid, env)
            return env

        if "error" in holder:
            env = unavailable(
                _safe_error_message(holder["error"]),
                data={"correlation_id": cid, "failed": True},
            )
            if use_cache and device_id:
                self._put_cached(device_id, cid, env)
            return env

        result = holder.get("result")
        reply = str(getattr(result, "text", "") or "")
        source = str(getattr(result, "source", "") or "")
        data = dict(getattr(result, "data", None) or {})
        data["channel"] = "companion"
        data["correlation_id"] = cid
        if device_id:
            data["companion_device_id"] = device_id
        env = ok({
            "reply": reply,
            "should_speak": bool(getattr(result, "should_speak", False)),
            "source": source,
            "active": bool(getattr(result, "active", False)),
            "correlation_id": cid,
            "result": data,
        })
        if use_cache and device_id:
            self._put_cached(device_id, cid, env)
        return env

    def _command(
        self,
        device: CompanionDevice,
        body: bytes,
        headers: Optional[Dict[str, str]] = None,
    ) -> CompanionEnvelope:
        if not device.permissions.get("commands", True):
            return forbidden("To urządzenie nie ma zgody na komendy.")
        payload, err = _parse_json_body(body)
        if err:
            return invalid(err)
        assert payload is not None
        text = str(payload.get("text") or payload.get("command") or "").strip()
        cid = self._resolve_correlation(payload, headers)
        return self.submit_command(text, device_id=device.device_id, correlation_id=cid)

    def _audio(
        self,
        device: CompanionDevice,
        body: bytes,
        headers: Optional[Dict[str, str]] = None,
    ) -> CompanionEnvelope:
        if not self.settings.allow_audio_upload or not device.permissions.get("audio", True):
            return unavailable("Przesyłanie audio jest wyłączone.")
        from core.companion.stt import transcribe_wav_bytes

        text, status = transcribe_wav_bytes(body, transcribe_fn=self.transcribe_fn)
        if status == "invalid":
            return invalid("Niepoprawne audio. Wyślij WAV (PCM 16-bit).")
        if status == "unavailable":
            return unavailable("STT jest teraz niedostępne.")
        if not text:
            return ok({"reply": "Nic nie rozpoznałem.", "transcript": ""})
        cid = self._resolve_correlation({}, headers)
        env = self.submit_command(text, device_id=device.device_id, correlation_id=cid)
        if env.ok:
            env.data["transcript"] = text
        return env

    def _events(self, device: CompanionDevice) -> CompanionEnvelope:
        with self._queue_lock:
            pending = list(self._queues.get(device.device_id, []))
        return ok({
            "events": [row.to_dict() for row in pending],
            "connection": "online" if self.registry.is_online(device.device_id) else "offline",
        })

    def _events_ack(self, device: CompanionDevice, body: bytes) -> CompanionEnvelope:
        payload, err = _parse_json_body(body)
        if err:
            return invalid(err)
        assert payload is not None
        ids = payload.get("ids") if isinstance(payload.get("ids"), list) else []
        wanted = {str(x) for x in ids if x}
        with self._queue_lock:
            q = self._queues.get(device.device_id, [])
            self._queues[device.device_id] = [row for row in q if row.id not in wanted]
        return ok({"acked": sorted(wanted)})

    def _quick_action(
        self,
        device: CompanionDevice,
        body: bytes,
        headers: Optional[Dict[str, str]] = None,
    ) -> CompanionEnvelope:
        if not device.permissions.get("quick_actions", True):
            return forbidden("Szybkie akcje są wyłączone na tym urządzeniu.")
        payload, err = _parse_json_body(body)
        if err:
            return invalid(err)
        assert payload is not None
        action_id = str(payload.get("id") or payload.get("action") or "").strip()
        catalog = {row["id"]: row for row in QUICK_ACTIONS}
        spec = catalog.get(action_id)
        if spec is None:
            return invalid("Nieznana szybka akcja.")
        if action_id == "mute":
            self.registry.set_muted(device.device_id, True)
            return ok({"muted": True, "reply": "Powiadomienia na telefon wyciszone."})
        if action_id == "unmute":
            self.registry.set_muted(device.device_id, False)
            return ok({"muted": False, "reply": "Powiadomienia na telefon włączone."})
        cid = self._resolve_correlation(payload, headers)
        return self.submit_command(
            str(spec.get("command") or ""),
            device_id=device.device_id,
            correlation_id=cid,
        )

    def _settings(self, device: CompanionDevice, body: bytes) -> CompanionEnvelope:
        payload, err = _parse_json_body(body)
        if err:
            return invalid(err)
        assert payload is not None
        if "mute" in payload or "muted" in payload:
            muted = bool(payload.get("mute", payload.get("muted")))
            updated = self.registry.set_muted(device.device_id, muted)
            if updated is None:
                return unauthorized()
            return ok({"muted": updated.muted})
        return invalid("Brak ustawienia.")

    def fanout_inbox(self, item: InboxItem) -> Optional[QueuedEvent]:
        return self.enqueue_event(
            event_id=f"inbox:{item.id}",
            event_type="notification",
            title=str(item.title or item.app or item.sender or "Powiadomienie"),
            body=item.preview() if hasattr(item, "preview") else str(item.body or ""),
            source=str(item.source or "inbox"),
            extra={"item_id": item.id, "app": item.app, "sender": item.sender},
        )

    def on_bus_event(self, event: Event) -> None:
        if event is None or str(getattr(event, "type", "")) not in _NOTIFY_TYPES:
            return
        data = dict(getattr(event, "data", None) or {})
        title = str(data.get("title") or data.get("app") or event.type)
        body = str(data.get("body") or data.get("message") or data.get("text") or "")
        self.enqueue_event(
            event_id=str(event.id or event.dedup_key or ""),
            event_type="notification",
            title=title,
            body=body,
            source=str(event.source or ""),
            extra={"event_type": event.type, **{k: data[k] for k in data if k != "body"}},
        )

    def enqueue_event(
        self,
        *,
        event_id: str,
        event_type: str,
        title: str = "",
        body: str = "",
        source: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Optional[QueuedEvent]:
        eid = str(event_id or "").strip()
        if not eid:
            return None
        if eid in self._seen_event_ids:
            return None
        self._seen_event_ids.add(eid)
        if len(self._seen_event_ids) > 2000:
            self._seen_event_ids = set(list(self._seen_event_ids)[-1000:])
        queued = QueuedEvent(
            id=eid,
            type=event_type,
            title=str(title or ""),
            body=str(body or ""),
            source=str(source or ""),
            created_at=float(self.clock.time()),
            extra=dict(extra or {}),
        )
        cap = max(1, int(self.settings.max_queued_events))
        with self._queue_lock:
            for device in self.registry.list_active():
                if not device.permissions.get("notifications", True):
                    continue
                if device.muted:
                    continue
                if self.registry.already_delivered(device.device_id, eid):
                    continue
                q = self._queues.setdefault(device.device_id, [])
                q.append(queued)
                del q[:-cap]
                self.registry.mark_delivered(device.device_id, eid)
        return queued

    def pending_events(self, device_id: str) -> List[QueuedEvent]:
        with self._queue_lock:
            return list(self._queues.get(device_id, []))


def get_companion_service() -> Optional[CompanionService]:
    return _SERVICE


def set_companion_service(service: Optional[CompanionService]) -> None:
    global _SERVICE
    _SERVICE = service


def reset_companion_for_tests() -> None:
    global _SERVICE
    _SERVICE = None
