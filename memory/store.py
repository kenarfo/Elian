"""Shared helpers for memory storage and text cleaning."""

from __future__ import annotations

import re
import secrets
import tempfile
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


_LOCK = threading.RLock()

SCHEMA_VERSION = 4
LEGACY_SCHEMA_VERSION = 3

STOP_WORDS = {
    "a", "aby", "albo", "ale", "bo", "co", "czy", "do", "i", "jak", "jest",
    "mi", "na", "nie", "o", "od", "się", "sie", "to", "w", "z", "że", "ze",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_ts(value):
    """Parse ISO timestamp to aware UTC datetime, or None."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_expired(expires_at, *, now=None):
    expiry = parse_ts(expires_at)
    if expiry is None:
        return False
    current = parse_ts(now) if now else datetime.now(timezone.utc)
    if current is None:
        current = datetime.now(timezone.utc)
    return expiry <= current


def clean_text(text, limit=1000):
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text).strip()[:limit]


def fold_pl(text):
    text = str(text or "").casefold()
    replacements = str.maketrans({
        "ą": "a",
        "ć": "c",
        "ę": "e",
        "ł": "l",
        "ń": "n",
        "ó": "o",
        "ś": "s",
        "ź": "z",
        "ż": "z",
    })
    return text.translate(replacements)


def query_terms(text):
    text = fold_pl(text)
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text)
        if len(token) > 1 and token not in STOP_WORDS
    }


def new_id(prefix):
    return f"{prefix}_{secrets.token_hex(6)}"


def atomic_write_json(path: Path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix="elian-mem-",
        suffix=".json",
        dir=path.parent,
    )
    import json
    import os

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


def read_json(path: Path, default=None):
    from core.persistence import safe_read_json

    data, _recovered = safe_read_json(path, default=default)
    return data


def locked(fn):
    def wrapper(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return wrapper
