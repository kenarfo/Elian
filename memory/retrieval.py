# ==========================
# ELIAN — Memory 2.0 compact retrieval
# ==========================
"""Select a small, goal-relevant slice. Never dump the whole memory file."""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from memory.store import fold_pl, query_terms
from memory.types import MemoryType, normalize_type

MAX_COMPACT_ITEMS = 8
MAX_COMPACT_CHARS = 900

_IRRELEVANT_STATUSES = frozenset(
    {"invalidated", "cancelled", "forgotten", "expired", "cleanup_candidate"}
)


def is_retrieval_relevant(entry: dict[str, Any] | None) -> bool:
    """Relevance gate: soft-cancelled / expired entries must not resurface."""
    if not isinstance(entry, dict):
        return False
    status = str(entry.get("status") or "active").lower()
    if status in _IRRELEVANT_STATUSES:
        return False
    if entry.get("persistent") is False and status in ("candidate", "session"):
        # Session-only scratch must not leak via compact retrieval.
        return False
    return True


_GOAL_TYPE_HINTS = (
    (
        MemoryType.PREFERENCE,
        ("wolę", "wole", "prefer", "muzyk", "spotify", "rano", "krótk", "krotk", "cich"),
    ),
    (
        MemoryType.RELATION,
        ("lampa", "lamp", "świat", "swiat", "entity", "alias", "vizzini", "light.", "switch."),
    ),
    (
        MemoryType.VOCABULARY,
        ("szot", "jak powiem", "alias", "znaczy"),
    ),
    (
        MemoryType.ROUTINE,
        ("jak powiem", "rutyn", "zawsze gdy"),
    ),
    (
        MemoryType.TEMPORARY,
        ("jutro", "dentyst", "termin", "pojutrze", "mam w"),
    ),
)


def types_for_goal(goal: str, intent_family: str = "") -> list[MemoryType]:
    text = fold_pl(goal)
    family = str(intent_family or "").casefold()
    picked: list[MemoryType] = []
    for kind, tokens in _GOAL_TYPE_HINTS:
        if any(fold_pl(token) in text for token in tokens):
            picked.append(kind)
    if family in ("preference", "media", "app"):
        if MemoryType.PREFERENCE not in picked:
            picked.append(MemoryType.PREFERENCE)
    if family in ("smart_home", "home_assistant", "device"):
        if MemoryType.RELATION not in picked:
            picked.append(MemoryType.RELATION)
    if family in ("calendar", "reminder"):
        if MemoryType.TEMPORARY not in picked:
            picked.append(MemoryType.TEMPORARY)
    if not picked:
        picked = [
            MemoryType.PREFERENCE,
            MemoryType.FACT,
            MemoryType.RELATION,
            MemoryType.TEMPORARY,
        ]
    if MemoryType.FACT not in picked:
        picked.append(MemoryType.FACT)
    return picked


def format_compact_line(entry: dict[str, Any]) -> str:
    kind = str(entry.get("type") or "FACT")
    key = str(entry.get("key") or "")
    value = str(entry.get("value") or entry.get("text") or "")
    if kind == MemoryType.PREFERENCE.value:
        return f"{key}={value}"
    if kind == MemoryType.RELATION.value:
        return f"{key} → {value}"
    if kind in (MemoryType.VOCABULARY.value, MemoryType.ROUTINE.value):
        return f"„{key}” → {value}"
    text = str(entry.get("text") or value)
    return text


def compact_entries(
    entries: Sequence[dict[str, Any]],
    *,
    max_items: int = MAX_COMPACT_ITEMS,
    max_chars: int = MAX_COMPACT_CHARS,
) -> list[dict[str, Any]]:
    out = []
    used = 0
    relevant = [item for item in entries if is_retrieval_relevant(item)]
    for item in relevant[: max(1, int(max_items))]:
        line = format_compact_line(item)
        if used + len(line) + 1 > max_chars and out:
            break
        slim = {
            "id": item.get("id"),
            "type": item.get("type"),
            "key": item.get("key"),
            "value": item.get("value"),
            "text": item.get("text") or item.get("value"),
            "confidence": item.get("confidence"),
        }
        out.append(slim)
        used += len(line) + 1
    return out


def render_compact(entries: Sequence[dict[str, Any]]) -> str:
    if not entries:
        return ""
    grouped: dict[str, list[str]] = {}
    for item in entries:
        kind = str(item.get("type") or "FACT")
        grouped.setdefault(kind, []).append(format_compact_line(item))
    blocks = []
    for kind, lines in grouped.items():
        blocks.append(f"{kind}:\n" + "\n".join(f"- {line}" for line in lines if line))
    return "\n".join(blocks)


def select_compact(
    store,
    goal: str,
    *,
    intent_family: str = "",
    extra_preferences: Optional[dict[str, Any]] = None,
    max_items: int = MAX_COMPACT_ITEMS,
    max_chars: int = MAX_COMPACT_CHARS,
    include_session: bool = False,
) -> dict[str, Any]:
    """Goal-scoped slice for Planner / Semantic Executive."""
    wanted = types_for_goal(goal, intent_family)
    hits: list[dict[str, Any]] = []
    if store is not None and hasattr(store, "retrieve"):
        hits = store.retrieve(
            goal,
            types=wanted,
            limit=max_items,
            include_session=include_session,
        )
    # Always allow a couple of high-signal prefs named in extra_preferences
    # when the goal looks related (caller already filtered music.* for knowledge).
    extras = []
    if extra_preferences:
        terms = query_terms(goal)
        for key, value in list(extra_preferences.items())[:8]:
            blob_terms = query_terms(f"{key} {value}")
            related = bool(terms & blob_terms)
            goal_l = str(goal or "").casefold()
            if key == "music.default_app" and any(
                token in goal_l for token in ("muzyk", "puść", "pusc", "spotify", "odtwórz", "odtworz")
            ):
                related = True
            if key == "browser.default" and any(
                token in goal_l for token in ("przeglad", "przegląd", "chrome", "stron")
            ):
                related = True
            if key in ("voice.morning_brief", "voice.response_style") and any(
                token in goal_l for token in ("rano", "krótk", "krotk", "styl")
            ):
                related = True
            if related:
                extras.append(
                    {
                        "id": f"pref:{key}",
                        "type": MemoryType.PREFERENCE.value,
                        "key": key,
                        "value": value,
                        "text": f"{key}={value}",
                        "confidence": 1.0,
                    }
                )
    merged = []
    seen = set()
    for item in extras + hits:
        if not is_retrieval_relevant(item):
            continue
        marker = (str(item.get("type")), str(item.get("key")).casefold(), str(item.get("value")).casefold())
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(item)
    compact = compact_entries(merged, max_items=max_items, max_chars=max_chars)
    return {
        "entries": compact,
        "text": render_compact(compact),
        "types": [kind.value if isinstance(kind, MemoryType) else str(kind) for kind in wanted],
    }
