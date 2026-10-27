# ==========================
# ELIAN — safe page fetch (Phase 11)
# ==========================
"""Read-only HTTP fetch with SSRF guards; plain text only."""

from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from typing import Tuple
from urllib.parse import urlparse

import requests


MAX_BYTES = 500_000
MAX_TEXT_CHARS = 12_000
FETCH_TIMEOUT = 12


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "header"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "header"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._skip:
            return
        text = str(data or "").strip()
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        joined = " ".join(self._chunks)
        return re.sub(r"\s+", " ", joined).strip()


def _is_private_host(host: str) -> bool:
    host = (host or "").strip().lower()
    if not host or host == "localhost" or host.endswith(".local"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True
    for info in infos:
        ip_text = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return True
    return False


def is_safe_public_http_url(url: str) -> Tuple[bool, str]:
    raw = str(url or "").strip()
    if not raw or len(raw) > 2000:
        return False, "Nieprawidłowy URL."
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, "Dozwolone są tylko http/https."
    host = parsed.hostname or ""
    if not host:
        return False, "Brak hosta."
    if _is_private_host(host):
        return False, "Adres sieci prywatnej jest zablokowany."
    return True, ""


def fetch_web_page_text(url: str) -> Tuple[bool, str, dict]:
    ok, reason = is_safe_public_http_url(url)
    if not ok:
        return False, reason, {"url": url, "blocked": True}
    try:
        response = requests.get(
            url,
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": "ElianAssistant/1.0"},
            stream=True,
            allow_redirects=True,
        )
        # Re-check final URL after redirects.
        final = response.url
        ok_final, reason_final = is_safe_public_http_url(final)
        if not ok_final:
            return False, reason_final, {"url": url, "final_url": final, "blocked": True}
        raw = response.content[:MAX_BYTES]
        response.raise_for_status()
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if "html" not in content_type and "text" not in content_type:
            return False, "Nieobsługiwany typ treści.", {"url": final, "content_type": content_type}
        html = raw.decode(response.encoding or "utf-8", errors="replace")
        parser = _TextExtractor()
        parser.feed(html)
        text = parser.text()[:MAX_TEXT_CHARS]
        if not text:
            return False, "Nie udało się wyciągnąć tekstu ze strony.", {"url": final}
        return True, text, {"url": final, "chars": len(text)}
    except Exception as error:
        return False, f"Timeout lub błąd pobierania: {error}"[:200], {"url": url}
