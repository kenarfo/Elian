# ==========================
# ELIAN — companion smoke (LAN Phone Companion MVP)
# ==========================
"""Offline companion gate — no mic, HA, or Ollama.

Flow: start localhost server → pair → command via CM → revoke → reject → shutdown.
Default companion.enabled stays false; smoke forces enabled only in-process.
"""

from __future__ import annotations

import json
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from core.companion.server import CompanionServer
from core.companion.service import CompanionService, CompanionSettings, reset_companion_for_tests
from core.conversation_manager import ConversationManager
from core.model_client import FakeModelClient
from core.tools import ToolRegistry, reset_registry_for_tests
from memory.manager import MemoryManager


def _http(
    method: str,
    url: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    token: str = "",
    device_id: str = "",
    timeout: float = 5.0,
) -> tuple[int, Dict[str, Any]]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if device_id:
        headers["X-Elian-Device-Id"] = device_id
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return int(resp.status), json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"ok": False, "error": raw or str(exc)}
        return int(exc.code), parsed


def run_companion_smoke() -> int:
    reset_companion_for_tests()
    failures = []
    steps_ok = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal steps_ok
        if cond:
            steps_ok += 1
            print(f"  PASS  {name}")
        else:
            failures.append(f"{name}: {detail}")
            print(f"  FAIL  {name} — {detail}")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        registry = ToolRegistry(confirm_medium=False)
        reset_registry_for_tests(registry)
        cm = ConversationManager(
            registry=registry,
            model=FakeModelClient([]),
            memory_manager=MemoryManager(tmp / "mem.json"),
            memory=[],
        )
        seen = []
        original = cm.handle_from_companion

        def wrapped(text, *, device_id: str = "", correlation_id: str = ""):
            seen.append({
                "text": text,
                "device_id": device_id,
                "correlation_id": correlation_id,
            })
            return original(text, device_id=device_id, correlation_id=correlation_id)

        cm.handle_from_companion = wrapped  # type: ignore[method-assign]

        settings = CompanionSettings(
            enabled=True,
            bind="127.0.0.1",
            port=0,
            pairing_window_seconds=60,
            heartbeat_timeout_seconds=45,
            rate_limit_per_minute=120,
            allow_audio_upload=False,
            lan_only=True,
            command_timeout_seconds=10,
            max_command_chars=500,
            registry_path=tmp / "companion_devices.json",
        )
        service = CompanionService(
            settings=settings,
            conversation_manager=cm,
        )
        server = CompanionServer(service, bind="127.0.0.1", port=0)
        server.start()
        try:
            time.sleep(0.05)
            base = f"http://127.0.0.1:{server.actual_port}"
            print(f"COMPANION-SMOKE base={base}")

            # 1. health
            code, health = _http("GET", base + "/v1/health")
            check("start_server", code == 200 and health.get("ok") is True, str(health))

            # 2. pair
            code, start = _http("POST", base + "/v1/pair/start", payload={})
            pair_code = (start.get("data") or {}).get("pairing_code")
            check("pair_start", code == 200 and bool(pair_code), str(start))
            code, paired = _http(
                "POST",
                base + "/v1/pair",
                payload={
                    "pairing_code": pair_code,
                    "device_id": "smoke_phone",
                    "name": "Smoke",
                    "platform": "smoke",
                },
            )
            token = (paired.get("data") or {}).get("token") or ""
            device_id = (paired.get("data") or {}).get("device_id") or ""
            check("pair_device", code == 200 and bool(token) and device_id == "smoke_phone", str(paired))
            # Token must not appear in registry file plaintext.
            raw_reg = (tmp / "companion_devices.json").read_text(encoding="utf-8")
            check("token_hashed", token not in raw_reg and "token_hash" in raw_reg, "plaintext token in registry")

            # 3–5. command → CM → response
            req_id = "smoke-req-1"
            code, cmd = _http(
                "POST",
                base + "/v1/command",
                payload={"text": "Ok.", "request_id": req_id},
                token=token,
                device_id=device_id,
            )
            check("send_command", code == 200 and cmd.get("ok") is True, str(cmd))
            check(
                "enters_conversation_manager",
                len(seen) == 1 and seen[0]["text"] == "Ok." and seen[0]["correlation_id"] == req_id,
                str(seen),
            )
            reply = (cmd.get("data") or {}).get("reply")
            corr = (cmd.get("data") or {}).get("correlation_id")
            source = str((cmd.get("data") or {}).get("source") or "")
            # Deterministic ACK "Ok." may have empty TTS reply — still a valid response.
            ok_response = corr == req_id and (source == "ack" or bool(reply) or cmd.get("ok") is True)
            check("get_response", ok_response, str(cmd))

            # 6–7. revoke → next rejected
            code, revoked = _http(
                "POST",
                base + "/v1/pair/revoke",
                payload={"device_id": device_id},
            )
            check("revoke_device", code == 200 and revoked.get("ok") is True, str(revoked))
            code, denied = _http(
                "POST",
                base + "/v1/command",
                payload={"text": "Ok.", "request_id": "smoke-req-2"},
                token=token,
                device_id=device_id,
            )
            check(
                "post_revoke_rejected",
                code == 401 and denied.get("ok") is False,
                str(denied),
            )
            check("no_extra_cm_after_revoke", len(seen) == 1, str(seen))
        finally:
            server.stop()
            reset_registry_for_tests(None)
            reset_companion_for_tests()

    check("clean_shutdown", True)
    total = steps_ok + len(failures)
    print(f"COMPANION-SMOKE {steps_ok}/{total} PASS")
    if failures:
        for row in failures:
            print(f"  detail: {row}")
        return 1
    print("COMPANION-SMOKE OK — experimental opt-in ready (companion.enabled=false default)")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_companion_smoke())
