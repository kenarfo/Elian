"""Shared model client — single entry for Ollama (and future local/cloud models)."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Iterator, Optional

from Config import get_config_value
from Diagnostics import log_turn


class ModelClient:
    """Thin wrapper around a chat model. Subclass or inject for tests."""

    def chat(
        self,
        messages,
        *,
        temperature=0.2,
        format=None,
        model=None,
    ) -> str:
        raise NotImplementedError

    def chat_stream(
        self,
        messages,
        *,
        temperature=0.2,
        format=None,
        model=None,
        options: Optional[dict] = None,
    ) -> Iterator[str]:
        """Yield content token deltas. Default: one-shot via chat()."""
        text = self.chat(
            messages, temperature=temperature, format=format, model=model
        )
        if text:
            yield text

    def cancel_active_stream(self) -> None:
        """Best-effort cancel of an in-flight chat_stream (no-op by default)."""
        return None


class OllamaModelClient(ModelClient):
    def __init__(self, model=None, timeout=None, host=None):
        import ollama

        self.model = model or get_config_value(
            "ai",
            "ollama_model",
            default="llama3.1:8b",
            legacy_key="ollama_model",
        )
        self._timeout = float(
            timeout
            if timeout is not None
            else get_config_value("ai", "request_timeout", default=30)
        )
        # Prefer 127.0.0.1 — Windows localhost can stall on IPv6.
        self.host = (
            host
            or get_config_value("ai", "ollama_host", default="http://127.0.0.1:11434")
            or "http://127.0.0.1:11434"
        ).rstrip("/")
        try:
            self._client = ollama.Client(host=self.host, timeout=self._timeout)
        except TypeError:
            self._client = ollama.Client(timeout=self._timeout)
        self._stream_lock = threading.Lock()
        self._active_resp = None

    def chat(self, messages, *, temperature=0.2, format=None, model=None) -> str:
        try:
            from core.ollama_lifecycle import ensure_gpu_for_ollama, ollama_keep_alive

            ensure_gpu_for_ollama(reason="ollama_chat")
            keep_alive = ollama_keep_alive()
        except Exception:
            keep_alive = "30m"
        kwargs = {
            "model": model or self.model,
            "messages": messages,
            "options": {"temperature": temperature},
            "keep_alive": keep_alive,
        }
        if format is not None:
            kwargs["format"] = format
        response = self._client.chat(**kwargs)
        return (response.get("message") or {}).get("content", "").strip()

    def cancel_active_stream(self) -> None:
        """Close the active HTTP response so Ollama stops generating."""
        with self._stream_lock:
            resp = self._active_resp
            self._active_resp = None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    def chat_stream(
        self,
        messages,
        *,
        temperature=0.2,
        format=None,
        model=None,
        options: Optional[dict] = None,
    ) -> Iterator[str]:
        """Stream token deltas from Ollama ``/api/chat``.

        Cancellation: call ``cancel_active_stream()`` from another thread —
        a stop flag alone is not enough because ``iter_lines`` blocks on read.
        """
        import requests

        try:
            from core.ollama_lifecycle import ensure_gpu_for_ollama, ollama_keep_alive

            ensure_gpu_for_ollama(reason="ollama_stream")
            keep_alive = ollama_keep_alive()
        except Exception:
            keep_alive = "30m"

        opts: dict[str, Any] = {"temperature": temperature}
        if options:
            opts.update(options)

        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
            "keep_alive": keep_alive,
            "options": opts,
        }
        if format is not None:
            payload["format"] = format

        url = f"{self.host}/api/chat"
        timeout = (min(10.0, self._timeout), max(self._timeout, 30.0))
        resp = None
        try:
            resp = requests.post(url, json=payload, stream=True, timeout=timeout)
            resp.raise_for_status()
            with self._stream_lock:
                self._active_resp = resp
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                msg = data.get("message") or {}
                piece = msg.get("content") or ""
                if piece:
                    yield piece
                if data.get("done"):
                    break
        except (requests.exceptions.RequestException, OSError, ValueError) as exc:
            # Mid-stream close from cancel_active_stream is expected.
            if resp is not None and getattr(resp, "closed", False):
                return
            err_name = type(exc).__name__
            if "Connection" in err_name or "Chunked" in err_name:
                return
            raise
        finally:
            with self._stream_lock:
                if self._active_resp is resp:
                    self._active_resp = None
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass


class FakeModelClient(ModelClient):
    """Deterministic offline client for planner/agent tests."""

    def __init__(self, responses=None, *, stream_delay_s: float = 0.0):
        self._responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []
        self.stream_delay_s = float(stream_delay_s or 0.0)
        self.cancelled_streams = 0
        self._cancel_stream = threading.Event()
        self._stream_active = False

    def chat(self, messages, *, temperature=0.2, format=None, model=None) -> str:
        self.calls.append({
            "messages": messages,
            "temperature": temperature,
            "format": format,
            "model": model,
        })
        return self._next_text()

    def chat_stream(
        self,
        messages,
        *,
        temperature=0.2,
        format=None,
        model=None,
        options: Optional[dict] = None,
    ) -> Iterator[str]:
        self.stream_calls.append({
            "messages": messages,
            "temperature": temperature,
            "format": format,
            "model": model,
            "options": options,
        })
        self.calls.append({
            "messages": messages,
            "temperature": temperature,
            "format": format,
            "model": model,
        })
        self._cancel_stream.clear()
        self._stream_active = True
        try:
            text = self._next_text()
            # Yield word-sized deltas so tests can observe mid-stream cancel.
            parts = text.split(" ")
            for i, word in enumerate(parts):
                if self._cancel_stream.is_set():
                    return
                piece = word if i == 0 else " " + word
                if self.stream_delay_s > 0:
                    time.sleep(self.stream_delay_s)
                if self._cancel_stream.is_set():
                    return
                yield piece
        finally:
            self._stream_active = False

    def cancel_active_stream(self) -> None:
        self._cancel_stream.set()
        self.cancelled_streams += 1

    def _next_text(self) -> str:
        if not self._responses:
            return json.dumps({
                "type": "final_answer",
                "message": "Nie wiem, co zrobić.",
            }, ensure_ascii=False)
        item = self._responses.pop(0)
        if isinstance(item, dict):
            return json.dumps(item, ensure_ascii=False)
        return str(item)

    def push(self, *responses):
        self._responses.extend(responses)


_default_client: ModelClient | None = None


def get_model_client() -> ModelClient:
    global _default_client
    if _default_client is None:
        _default_client = OllamaModelClient()
    return _default_client


def set_model_client(client: ModelClient | None):
    global _default_client
    _default_client = client
    if client is not None:
        log_turn("model_client", event="set", kind=type(client).__name__)


def reset_model_client():
    set_model_client(None)
