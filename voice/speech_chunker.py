# ==========================
# ELIAN — stream speech chunker (Phase 0–2)
# Pure text helpers; no audio I/O.
# ==========================

from __future__ import annotations

import re
from typing import Iterable, Iterator, Optional

# Sentence end: . ! ? … (unicode ellipsis) and ASCII ...
_SENTENCE_END = re.compile(
    r"(.+?(?:[.!?]|\u2026|\.{3}))(\s+|$)",
    re.DOTALL,
)

# Clause flush: comma / semicolon / colon / em-dash after enough text
_CLAUSE_BREAK = re.compile(r"^(.{15,}?[,;:\-—–])\s+")


def iter_speech_chunks(
    tokens: Iterable[str],
    *,
    clause_flush_chars: int = 50,
    max_chunk_chars: int = 240,
) -> Iterator[str]:
    """Yield speakable Polish prose chunks from a token stream.

    Flushes on sentence boundaries (. ! ? …) as soon as they appear.
    Before the first sentence, optionally flushes a long clause at a comma
    once ``clause_flush_chars`` of buffered text is available (0 = disabled).
    """
    buf = ""
    spoke_first = False
    clause_limit = max(0, int(clause_flush_chars or 0))

    for token in tokens:
        piece = str(token or "")
        if not piece:
            continue
        buf += piece

        while True:
            m = _SENTENCE_END.search(buf)
            if not m:
                break
            sent = (m.group(1) or "").strip()
            buf = buf[m.end() :]
            if sent:
                for part in _wrap_long(sent, max_chunk_chars):
                    yield part
                spoke_first = True

        if not spoke_first and clause_limit and len(buf) >= clause_limit:
            flushed = _try_clause_flush(buf, clause_limit)
            if flushed is not None:
                chunk, buf = flushed
                if chunk:
                    for part in _wrap_long(chunk, max_chunk_chars):
                        yield part
                    spoke_first = True

        while len(buf) >= max_chunk_chars:
            # Hard wrap at last space so Piper never gets a huge blob.
            cut = buf.rfind(" ", 0, max_chunk_chars)
            if cut < 40:
                cut = max_chunk_chars
            chunk = buf[:cut].strip()
            buf = buf[cut:].lstrip()
            if chunk:
                yield chunk
                spoke_first = True

    tail = buf.strip()
    if tail:
        for part in _wrap_long(tail, max_chunk_chars):
            yield part


def _try_clause_flush(
    buf: str, clause_limit: int
) -> Optional[tuple[str, str]]:
    head = buf[: max(clause_limit + 40, 80)]
    m = _CLAUSE_BREAK.match(head)
    if m:
        cut = m.end()
        return buf[:cut].strip(), buf[cut:].lstrip()
    # Fall back: break at last space in the flush window.
    window = buf[:clause_limit]
    sp = window.rfind(" ")
    if sp >= 30:
        return buf[:sp].strip(), buf[sp:].lstrip()
    return None


def _wrap_long(text: str, max_chars: int) -> Iterator[str]:
    text = (text or "").strip()
    if not text:
        return
    if len(text) <= max_chars:
        yield text
        return
    while text:
        if len(text) <= max_chars:
            yield text
            return
        cut = text.rfind(" ", 0, max_chars)
        if cut < 40:
            cut = max_chars
        yield text[:cut].strip()
        text = text[cut:].lstrip()
