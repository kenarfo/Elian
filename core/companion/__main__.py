# ==========================
# ELIAN — companion CLI (pairing / status / revoke)
# ==========================
"""Talks to a running Elian companion on localhost. Does not start a second brain."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any, Dict

from Config import get_config_value


def _base() -> str:
    port = int(get_config_value("companion", "port", default=8742) or 8742)
    return f"http://127.0.0.1:{port}"


def _request(method: str, path: str, payload: Dict[str, Any] | None = None, token: str = "") -> Dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(_base() + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"ok": False, "status": "unavailable", "error": body or str(exc)}
    except OSError as exc:
        return {
            "ok": False,
            "status": "unavailable",
            "error": "Companion nie odpowiada. Włącz companion.enabled i uruchom Eliana.",
            "detail": str(exc),
        }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = (args[0] if args else "help").lower()
    if cmd in ("help", "-h", "--help"):
        print("python -m core.companion pair | status | revoke <device_id>")
        print("Parowanie startuje tylko z tego komputera (localhost).")
        return 0
    if cmd == "status":
        print(json.dumps(_request("GET", "/v1/health"), ensure_ascii=False, indent=2))
        return 0
    if cmd == "pair":
        result = _request("POST", "/v1/pair/start", {})
        data = result.get("data") or {}
        code = data.get("pairing_code")
        if code:
            print(f"Kod parowania: {code}")
            print("Wpisz go na telefonie (klient testowy: http://<PC>:port/).")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if cmd == "revoke":
        if len(args) < 2:
            print("Użycie: python -m core.companion revoke <device_id>")
            return 2
        result = _request("POST", "/v1/pair/revoke", {"device_id": args[1]})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    print("Nieznana komenda. python -m core.companion help")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
