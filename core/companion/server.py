# ==========================
# ELIAN — companion HTTP sidecar (stdlib)
# ==========================
"""ThreadingHTTPServer for LAN phone companion. Not a FastAPI/React product."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import unquote

from core.companion.service import CompanionService, CompanionSettings, set_companion_service


class CompanionHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, *, service: CompanionService):
        super().__init__(addr, handler)
        self.companion_service = service


def _make_handler(service: CompanionService):
    class CompanionHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            # Path only — never dump Authorization / tokens.
            print(f"[COMPANION] {self.command} {self.path}")

        def _headers_map(self) -> dict:
            return {str(k): str(v) for k, v in self.headers.items()}

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return b""
            return self.rfile.read(min(length, 2 * 1024 * 1024 + 1))

        def _write(self, env) -> None:
            payload = env.body_bytes
            if payload is None:
                payload = json.dumps(env.to_dict(), ensure_ascii=False).encode("utf-8")
            self.send_response(int(env.http_status or 200))
            self.send_header("Content-Type", env.content_type or "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _dispatch(self) -> None:
            remote = str(self.client_address[0] if self.client_address else "127.0.0.1")
            try:
                env = service.dispatch(
                    method=self.command,
                    path=unquote(self.path or "/"),
                    headers=self._headers_map(),
                    body=self._read_body(),
                    remote_ip=remote,
                )
            except Exception:
                from core.companion.protocol import unavailable

                env = unavailable("Companion napotkał błąd wewnętrzny.")
            self._write(env)

        def do_GET(self) -> None:
            self._dispatch()

        def do_POST(self) -> None:
            self._dispatch()

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Allow", "GET, POST, OPTIONS")
            self.end_headers()

    return CompanionHandler


class CompanionServer:
    def __init__(self, service: CompanionService, *, bind: str = "", port: int = 0):
        self.service = service
        self.bind = bind or service.settings.bind
        self.port = int(port or service.settings.port)
        self._httpd: Optional[CompanionHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def actual_port(self) -> int:
        if self._httpd is not None:
            return int(self._httpd.server_address[1])
        return self.port

    def start(self) -> None:
        handler = _make_handler(self.service)
        self._httpd = CompanionHTTPServer(
            (self.bind, self.port),
            handler,
            service=self.service,
        )
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="elian-companion",
            daemon=True,
        )
        self._thread.start()
        set_companion_service(self.service)
        print(f"[COMPANION] nasłuch http://{self.bind}:{self.actual_port}")

    def stop(self) -> None:
        bus = getattr(self.service, "_bus", None)
        if bus is not None:
            try:
                bus.unsubscribe(self.service.on_bus_event)
            except Exception:
                pass
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            try:
                self._httpd.server_close()
            except Exception:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._httpd = None
        self._thread = None
        set_companion_service(None)

    def threads_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


def maybe_start_companion(
    *,
    conversation_manager=None,
    bus=None,
    clock=None,
    settings: Optional[CompanionSettings] = None,
) -> Optional[CompanionServer]:
    cfg = settings or CompanionSettings.from_config()
    if not cfg.enabled:
        return None
    service = CompanionService(
        settings=cfg,
        conversation_manager=conversation_manager,
        clock=clock,
    )
    if bus is not None:
        try:
            bus.subscribe(service.on_bus_event)
            service._bus = bus
        except Exception:
            pass
    server = CompanionServer(service)
    server.start()
    return server
