import re
from html.parser import HTMLParser
from urllib.parse import quote_plus, urljoin, urlparse

import ollama
import requests

from Config import get_config_value
from Diagnostics import log_turn


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._chunks = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False
        if tag in ("p", "br", "li", "h1", "h2", "h3", "h4"):
            self._chunks.append("\n")

    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self._chunks.append(text + " ")

    def text(self):
        return re.sub(r"\s+", " ", "".join(self._chunks)).strip()


RESEARCH_PATTERNS = (
    re.compile(r"\b(?:jaka jest|jaka bedzie|jaka bedzie|jaka)\s+pogoda\b", re.I),
    re.compile(r"\bco jest (?:teraz )?na czasie\b", re.I),
    re.compile(r"\bco sie (?:ostatnio )?wydarzylo\b", re.I),
    re.compile(r"\b(?:znajdz|znajdz mi|podaj mi|daj mi)\s+przepis\b", re.I),
    re.compile(r"\bprzepis na\b", re.I),
    re.compile(r"\bsprawdz(?: mi)?\s+(?:kiedy|ile|cene|cene|kosztuje)\b", re.I),
    re.compile(r"\bkiedy wychodzi\b", re.I),
    re.compile(r"\bile kosztuje\b", re.I),
    re.compile(r"\b(?:aktualn|dzisiaj|dzis|teraz|najnowsz)\w*\b", re.I),
    re.compile(r"\bco jest popularne\b", re.I),
)

SKIP_DOMAINS = (
    "youtube.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "tiktok.com",
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 Elian/1.0"
)

_sources = []


def get_last_sources():
    return list(_sources)


def needs_web_research(text):
    if not bool(get_config_value("web_research", "enabled", default=True)):
        return False
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith(("otworz", "wyszukaj w google", "szukaj w google")):
        return False
    return any(pattern.search(normalized) for pattern in RESEARCH_PATTERNS)


def _request_timeout():
    return float(get_config_value("web_research", "timeout", default=12))


def _max_pages():
    return int(get_config_value("web_research", "max_pages", default=3))


def _max_chars():
    return int(get_config_value("web_research", "max_chars_per_page", default=8000))


def _fetch(url):
    try:
        response = requests.get(
            url,
            timeout=_request_timeout(),
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type and "text" not in content_type:
            return ""
        return response.text[: _max_chars()]
    except requests.RequestException as error:
        print("Błąd pobierania strony:", url, error)
        return ""


def _extract_text(html):
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        return ""
    return parser.text()


def _search_duckduckgo(query):
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query[:300])
    html = _fetch(url)
    if not html:
        return []

    results = []
    for match in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        html,
        re.I | re.S,
    ):
        href = match.group(1)
        title = re.sub(r"<[^>]+>", "", match.group(2))
        title = re.sub(r"\s+", " ", title).strip()
        if not href or not title:
            continue
        if href.startswith("//"):
            href = "https:" + href
        parsed = urlparse(href)
        if parsed.netloc.endswith("duckduckgo.com"):
            continue
        if any(domain in parsed.netloc for domain in SKIP_DOMAINS):
            continue
        results.append({"title": title, "url": href})
        if len(results) >= _max_pages():
            break
    return results


def _collect_snippets(query):
    global _sources
    _sources = []
    hits = _search_duckduckgo(query)
    snippets = []
    for hit in hits:
        page_html = _fetch(hit["url"])
        if not page_html:
            continue
        text = _extract_text(page_html)
        if len(text) < 120:
            continue
        snippet = text[:2500]
        snippets.append(
            {
                "title": hit["title"],
                "url": hit["url"],
                "text": snippet,
            }
        )
        _sources.append({"title": hit["title"], "url": hit["url"]})
        if len(snippets) >= _max_pages():
            break
    return snippets


def _synthesize(query, snippets):
    if not snippets:
        return (
            "Nie udało mi się pobrać aktualnych informacji z internetu. "
            "Spróbuj zadać pytanie inaczej."
        )

    context_blocks = []
    for index, snippet in enumerate(snippets, start=1):
        context_blocks.append(
            f"Źródło {index}: {snippet['title']} ({snippet['url']})\n"
            f"{snippet['text']}"
        )
    context = "\n\n".join(context_blocks)

    model = get_config_value(
        "ai",
        "ollama_model",
        default="llama3.1:8b",
        legacy_key="ollama_model",
    )
    prompt = (
        "Na podstawie poniższych fragmentów stron internetowych odpowiedz po polsku "
        "konkretnie na pytanie użytkownika. Używaj wyłącznie faktów obecnych w źródłach. "
        "Jeśli źródła są niewystarczające, powiedz to wprost. "
        "Dla przepisów podaj składniki i kroki. Odpowiedź ma być zwięzła, 2-6 zdań, "
        "nadaje się do odczytania na głos.\n\n"
        f"Pytanie: {query}\n\n{context}"
    )
    try:
        client = ollama.Client(timeout=float(get_config_value("ai", "request_timeout", default=30)))
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.2},
        )
        answer = response["message"]["content"].strip()
        if answer:
            return answer
    except Exception as error:
        print("Błąd syntezy web research:", error)

    fallback = snippets[0]["text"][:700].strip()
    if fallback:
        return fallback
    return "Znalazłem strony, ale nie mogę teraz streścić wyniku."


def research(query):
    query = str(query or "").strip().strip(".,!?;:")
    if not query:
        return "Co mam sprawdzić?"

    log_turn("web_research", query=query)
    snippets = _collect_snippets(query)
    answer = _synthesize(query, snippets)
    log_turn("web_research_result", query=query, sources=len(_sources))
    return answer
