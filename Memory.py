import json
import os
import re
import tempfile
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "Data")
MEMORY_FILE = os.path.join(DATA_DIR, "memory.json")

MEMORY_VERSION = 2
MAX_FACTS = 250
MAX_ACTIONS = 50
MAX_CONVERSATION_TURNS = 16
MAX_CONTEXT_ITEMS = 20

_LOCK = threading.RLock()

DEFAULT_STORE = {
    "version": MEMORY_VERSION,
    "facts": [],
    "recent_actions": [],
    "conversation": [],
    "context": {},
}

STOP_WORDS = {
    "a",
    "aby",
    "albo",
    "ale",
    "bo",
    "co",
    "czy",
    "do",
    "i",
    "jak",
    "jest",
    "mi",
    "na",
    "nie",
    "o",
    "od",
    "się",
    "sie",
    "to",
    "w",
    "z",
    "że",
    "ze",
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_text(text, limit=1000):
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _new_fact(text, category="fact"):
    timestamp = _now()
    return {
        "text": text,
        "category": category,
        "created_at": timestamp,
        "last_used_at": timestamp,
    }


def _normalize_store(raw):
    store = deepcopy(DEFAULT_STORE)

    if isinstance(raw, list):
        store["facts"] = [
            _new_fact(text)
            for item in raw
            if (text := _clean_text(item, 500))
        ][-MAX_FACTS:]
        return store

    if not isinstance(raw, dict):
        return store

    facts = []
    for item in raw.get("facts", []):
        if isinstance(item, str):
            text = _clean_text(item, 500)
            if text:
                facts.append(_new_fact(text))
            continue

        if not isinstance(item, dict):
            continue

        text = _clean_text(item.get("text", ""), 500)
        if not text:
            continue

        facts.append({
            "text": text,
            "category": _clean_text(
                item.get("category", "fact"),
                40,
            ) or "fact",
            "created_at": item.get("created_at") or _now(),
            "last_used_at": item.get("last_used_at") or _now(),
        })

    store["facts"] = facts[-MAX_FACTS:]

    for key, maximum in (
        ("recent_actions", MAX_ACTIONS),
        ("conversation", MAX_CONVERSATION_TURNS),
    ):
        values = raw.get(key, [])
        if isinstance(values, list):
            store[key] = [
                item
                for item in values
                if isinstance(item, dict)
            ][-maximum:]

    context = raw.get("context", {})
    if isinstance(context, dict):
        store["context"] = context

    return store


def _memory_path():
    return Path(MEMORY_FILE)


def create_memory_file():
    path = _memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _write_store(deepcopy(DEFAULT_STORE))


def _read_store():
    with _LOCK:
        create_memory_file()
        try:
            with _memory_path().open("r", encoding="utf-8") as file:
                return _normalize_store(json.load(file))
        except (OSError, json.JSONDecodeError):
            return deepcopy(DEFAULT_STORE)


def _write_store(store):
    with _LOCK:
        path = _memory_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized = _normalize_store(store)
        descriptor, temp_name = tempfile.mkstemp(
            prefix="elian-memory-",
            suffix=".json",
            dir=path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(
                    normalized,
                    file,
                    ensure_ascii=False,
                    indent=2,
                )
                file.flush()
                os.fsync(file.fileno())
            # Windows: os.replace can hit WinError 5 when another reader/writer
            # holds memory.json briefly (parallel smoke/explore/tests).
            last_err: Exception | None = None
            for attempt in range(8):
                try:
                    os.replace(temp_name, path)
                    last_err = None
                    break
                except PermissionError as err:
                    last_err = err
                    time.sleep(0.02 * (attempt + 1))
                except OSError as err:
                    # WinError 5 / sharing violation
                    if getattr(err, "winerror", None) not in (5, 32) and err.errno not in (
                        13,
                        11,
                    ):
                        raise
                    last_err = err
                    time.sleep(0.02 * (attempt + 1))
            if last_err is not None:
                raise last_err
        finally:
            if os.path.exists(temp_name):
                try:
                    os.remove(temp_name)
                except OSError:
                    pass


def load_memory():
    return [item["text"] for item in _read_store()["facts"]]


def save_memory(memory):
    store = _read_store()
    store["facts"] = [
        _new_fact(text)
        for item in memory
        if (text := _clean_text(item, 500))
    ][-MAX_FACTS:]
    _write_store(store)


def add_memory(text, category="fact"):
    text = _clean_text(text, 500)
    if not text:
        return False

    # Keep legacy store in sync and also write through MemoryManager when available.
    store = _read_store()
    normalized = text.casefold()
    for fact in store["facts"]:
        if fact["text"].casefold() == normalized:
            fact["last_used_at"] = _now()
            _write_store(store)
            return False

    store["facts"].append(_new_fact(text, category))
    store["facts"] = store["facts"][-MAX_FACTS:]
    _write_store(store)

    try:
        from memory.manager import get_memory_manager
        get_memory_manager().remember_fact(text, category=category, explicit=True)
    except Exception:
        pass
    return True


def _query_terms(text):
    return {
        token
        for token in re.findall(r"[a-ząćęłńóśźż0-9]+", text.lower())
        if len(token) > 1 and token not in STOP_WORDS
    }


def search_memory(query, limit=6):
    store = _read_store()
    terms = _query_terms(query)
    scored = []

    for index, fact in enumerate(store["facts"]):
        fact_terms = _query_terms(fact["text"])
        overlap = len(terms & fact_terms)
        if terms and overlap == 0:
            continue
        score = overlap * 10 + index / max(1, len(store["facts"]))
        scored.append((score, fact))

    if not terms:
        scored = [
            (index, fact)
            for index, fact in enumerate(store["facts"])
        ]

    selected = [
        fact
        for _score, fact in sorted(
            scored,
            key=lambda item: item[0],
            reverse=True,
        )[:max(1, limit)]
    ]

    if selected:
        selected_ids = {id(item) for item in selected}
        for fact in store["facts"]:
            if id(fact) in selected_ids:
                fact["last_used_at"] = _now()
        _write_store(store)

    return [fact["text"] for fact in selected]


def forget_memory(query):
    terms = _query_terms(query)
    if not terms:
        return 0

    store = _read_store()
    before = len(store["facts"])
    store["facts"] = [
        fact
        for fact in store["facts"]
        if not terms.issubset(_query_terms(fact["text"]))
    ]
    removed = before - len(store["facts"])
    if removed:
        _write_store(store)
    return removed


def clear_memory():
    store = _read_store()
    store["facts"] = []
    _write_store(store)


def clear_all_memory():
    _write_store(deepcopy(DEFAULT_STORE))


def record_action(
    action,
    argument="",
    result="",
    success=True,
    value=0,
):
    store = _read_store()
    store["recent_actions"].append({
        "action": _clean_text(action, 80),
        "argument": _clean_text(argument, 300),
        "result": _clean_text(result, 500),
        "success": bool(success),
        "value": int(value) if isinstance(value, (int, float)) else 0,
        "timestamp": _now(),
    })
    store["recent_actions"] = store["recent_actions"][-MAX_ACTIONS:]
    _write_store(store)


def get_last_action(allowed_actions=None):
    actions = reversed(_read_store()["recent_actions"])
    for action in actions:
        if allowed_actions is None or action.get("action") in allowed_actions:
            return deepcopy(action)
    return None


def record_conversation(role, content):
    if role not in ("user", "assistant"):
        return

    content = _clean_text(content, 1200)
    if not content:
        return

    store = _read_store()
    store["conversation"].append({
        "role": role,
        "content": content,
        "timestamp": _now(),
    })
    store["conversation"] = store["conversation"][
        -MAX_CONVERSATION_TURNS:
    ]
    _write_store(store)


def load_recent_conversation(limit=8):
    return [
        {
            "role": item.get("role"),
            "content": item.get("content", ""),
        }
        for item in _read_store()["conversation"][-max(0, limit):]
        if item.get("role") in ("user", "assistant")
    ]


def set_context(key, value):
    key = _clean_text(key, 80)
    if not key:
        return

    if isinstance(value, list):
        value = value[:MAX_CONTEXT_ITEMS]

    store = _read_store()
    store["context"][key] = {
        "value": value,
        "timestamp": _now(),
    }
    _write_store(store)


def get_context(key, default=None):
    value = _read_store()["context"].get(key)
    if not isinstance(value, dict):
        return default
    return deepcopy(value.get("value", default))


def clear_short_term_context():
    store = _read_store()
    store["context"] = {}
    _write_store(store)


def memory_stats():
    store = _read_store()
    return {
        "facts": len(store["facts"]),
        "actions": len(store["recent_actions"]),
        "conversation_turns": len(store["conversation"]),
    }
