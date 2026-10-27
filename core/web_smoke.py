# ==========================
# ELIAN — web smoke (Phase 11)
# ==========================
"""Live provider smoke for structured web_search — no mic."""

from __future__ import annotations

import time

from core.web_search_provider import (
    configured_provider,
    print_web_trace,
    provider_available,
    search_structured,
)


def run_web_smoke(*, query: str = "OpenAI latest news") -> int:
    print("=== Elian web-smoke (Phase 11) ===")
    provider = configured_provider()
    available = provider_available(provider)
    print(f"provider={provider}")
    print(f"provider_available={available}")
    if not available:
        print("search_success=False")
        print("FAIL — provider not available (configure WEB_SEARCH_PROVIDER / API key)")
        return 1

    started = time.perf_counter()
    result = search_structured(query, max_results=5)
    latency = int((time.perf_counter() - started) * 1000)
    print_web_trace(result)

    hits = result.results or []
    title_ok = all(bool(hit.title) for hit in hits) if hits else False
    url_ok = all(bool(hit.url) for hit in hits) if hits else False
    snippet_ok = all(bool(hit.snippet) for hit in hits) if hits else False
    synthesis_success = bool(hits) and title_ok and url_ok and snippet_ok

    print(f"query={query!r}")
    print(f"search_success={result.success}")
    print(f"result_count={len(hits)}")
    print(f"title_present={title_ok}")
    print(f"url_present={url_ok}")
    print(f"snippet_present={snippet_ok}")
    print(f"synthesis_success={synthesis_success}")
    print(f"latency_ms={result.latency_ms or latency}")

    ok = (
        available
        and result.success
        and len(hits) >= 1
        and title_ok
        and url_ok
        and snippet_ok
        and synthesis_success
    )
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run_web_smoke())
