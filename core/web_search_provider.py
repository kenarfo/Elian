# ==========================
# ELIAN — web search providers (Phase 11)
# ==========================
"""Structured internet search. Optional providers; Elian runs without a key."""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlparse

import requests

from Config import get_secret


@dataclass
class WebSearchHit:
    title: str = ""
    url: str = ""
    snippet: str = ""
    source: str = ""
    published_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value}


@dataclass
class WebSearchResult:
    success: bool
    query: str
    results: List[WebSearchHit] = field(default_factory=list)
    provider: str = ""
    error: str = ""
    latency_ms: int = 0
    available: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "query": self.query,
            "provider": self.provider,
            "results": [item.to_dict() for item in self.results],
            "error": self.error,
            "latency_ms": self.latency_ms,
            "available": self.available,
            "result_count": len(self.results),
        }


def _web_config() -> Dict[str, Any]:
    try:
        from Config import config as app_config

        cfg = app_config.get("web")
        if isinstance(cfg, dict):
            return cfg
    except Exception:
        pass
    return {
        "enabled": True,
        "search_provider": "duckduckgo",
        "api_key": "",
        "max_results": 5,
        "fetch_enabled": True,
    }


def configured_provider() -> str:
    env = (os.getenv("WEB_SEARCH_PROVIDER") or "").strip().lower()
    if env:
        return env
    return str(_web_config().get("search_provider") or "duckduckgo").strip().lower()


def api_key() -> str:
    return (
        get_secret("web_search_api_key", env_var="WEB_SEARCH_API_KEY")
        or str(_web_config().get("api_key") or "").strip()
    )


def provider_available(provider: str = "") -> bool:
    name = (provider or configured_provider()).strip().lower()
    if name in ("", "none", "off", "disabled"):
        return False
    if name in ("duckduckgo", "ddg"):
        return True
    if name in ("brave", "brave_search"):
        return bool(api_key())
    return False


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _search_duckduckgo(query: str, max_results: int) -> WebSearchResult:
    """Prefer ddgs / duckduckgo_search package; fallback Instant Answer JSON."""
    started = time.perf_counter()
    try:
        try:
            from ddgs import DDGS  # type: ignore
        except ImportError:
            from duckduckgo_search import DDGS  # type: ignore

        hits: List[WebSearchHit] = []
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=max_results):
                url = str(item.get("href") or item.get("link") or "").strip()
                hits.append(
                    WebSearchHit(
                        title=str(item.get("title") or "").strip(),
                        url=url,
                        snippet=str(item.get("body") or item.get("snippet") or "").strip(),
                        source=_domain(url),
                    )
                )
        latency = int((time.perf_counter() - started) * 1000)
        if hits:
            return WebSearchResult(
                success=True,
                query=query,
                results=hits,
                provider="duckduckgo",
                latency_ms=latency,
            )
    except Exception as error:
        # Fall through to Instant Answer.
        instant_error = str(error)[:160]
    else:
        instant_error = ""

    try:
        response = requests.get(
            "https://api.duckduckgo.com/",
            params={
                "q": query,
                "format": "json",
                "no_html": 1,
                "skip_disambig": 1,
            },
            timeout=12,
            headers={"User-Agent": "ElianAssistant/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
        hits = []
        abstract = str(payload.get("AbstractText") or "").strip()
        abs_url = str(payload.get("AbstractURL") or "").strip()
        heading = str(payload.get("Heading") or query).strip()
        if abstract and abs_url:
            hits.append(
                WebSearchHit(
                    title=heading,
                    url=abs_url,
                    snippet=abstract,
                    source=_domain(abs_url),
                )
            )
        for topic in payload.get("RelatedTopics") or []:
            if len(hits) >= max_results:
                break
            if not isinstance(topic, dict):
                continue
            if "Topics" in topic:
                continue
            text = str(topic.get("Text") or "").strip()
            url = str((topic.get("FirstURL") or "")).strip()
            if text and url:
                hits.append(
                    WebSearchHit(
                        title=text.split(" - ", 1)[0][:120],
                        url=url,
                        snippet=text,
                        source=_domain(url),
                    )
                )
        latency = int((time.perf_counter() - started) * 1000)
        if hits:
            return WebSearchResult(
                success=True,
                query=query,
                results=hits[:max_results],
                provider="duckduckgo_instant",
                latency_ms=latency,
            )
        return WebSearchResult(
            success=False,
            query=query,
            provider="duckduckgo",
            error=instant_error or "Brak wyników wyszukiwania.",
            latency_ms=latency,
        )
    except Exception as error:
        latency = int((time.perf_counter() - started) * 1000)
        return WebSearchResult(
            success=False,
            query=query,
            provider="duckduckgo",
            error=str(error)[:200],
            latency_ms=latency,
        )


def _search_brave(query: str, max_results: int) -> WebSearchResult:
    started = time.perf_counter()
    key = api_key()
    if not key:
        return WebSearchResult(
            success=False,
            query=query,
            provider="brave",
            error="Brak WEB_SEARCH_API_KEY dla Brave.",
            available=False,
            latency_ms=0,
        )
    try:
        response = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": key,
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        hits = []
        for item in (payload.get("web") or {}).get("results") or []:
            url = str(item.get("url") or "").strip()
            hits.append(
                WebSearchHit(
                    title=str(item.get("title") or "").strip(),
                    url=url,
                    snippet=str(item.get("description") or "").strip(),
                    source=_domain(url),
                    published_at=str(
                        (item.get("age") or item.get("page_age") or "")
                    ).strip(),
                )
            )
        latency = int((time.perf_counter() - started) * 1000)
        if not hits:
            return WebSearchResult(
                success=False,
                query=query,
                provider="brave",
                error="Brak wyników wyszukiwania.",
                latency_ms=latency,
            )
        return WebSearchResult(
            success=True,
            query=query,
            results=hits[:max_results],
            provider="brave",
            latency_ms=latency,
        )
    except Exception as error:
        latency = int((time.perf_counter() - started) * 1000)
        return WebSearchResult(
            success=False,
            query=query,
            provider="brave",
            error=str(error)[:200],
            latency_ms=latency,
        )


def search_structured(
    query: str,
    *,
    max_results: int = 5,
) -> WebSearchResult:
    q = str(query or "").strip()
    if not q:
        return WebSearchResult(
            success=False,
            query="",
            error="Puste zapytanie.",
            available=provider_available(),
        )
    limit = max(1, min(10, int(max_results or 5)))
    provider = configured_provider()
    if not provider_available(provider):
        return WebSearchResult(
            success=False,
            query=q,
            provider=provider or "none",
            error="Wyszukiwanie internetowe nie jest skonfigurowane.",
            available=False,
        )
    if provider in ("brave", "brave_search"):
        return _search_brave(q, limit)
    return _search_duckduckgo(q, limit)


def print_web_trace(result: WebSearchResult) -> None:
    print(
        f"[WEB] provider={result.provider} query={result.query!r} "
        f"results={len(result.results)} latency_ms={result.latency_ms} "
        f"success={result.success}"
    )
    if result.error:
        print(f"[WEB] error={result.error}")
    if result.results:
        print("[WEB SOURCES]")
        for index, hit in enumerate(result.results, start=1):
            print(f"{index}. title={hit.title}")
            print(f"   domain={hit.source or _domain(hit.url)}")
            print(f"   url={hit.url}")
