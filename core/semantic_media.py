# ==========================
# ELIAN — music semantic goals (Phase 13 Stage B)
# ==========================
"""Understand play goals. Execution uses existing media tools; BrowserAgent is Stage D."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict

_PLAY = re.compile(
    r"\b(?:pu[sś]ci[cć]|pu[sś][cć]|odtw[oó]rz|graj|zagraj)\b",
    re.I,
)
_SIMILAR = re.compile(r"\b(?:podobn\w*|w\s+tym\s+stylu|takie\s+co[sś])\b", re.I)
_MOOD = re.compile(
    r"\b(?:spokojn\w*|relaks\w*|chill|smutn\w*|weso[lł]\w*|energetyczn\w*|do\s+pracy|do\s+snu)\b",
    re.I,
)
_SOMETHING = re.compile(r"\bco[sś]\b", re.I)
_ARTIST_OF = re.compile(
    r"\b(?:michaela?\s+jacksona?|billie\s+eilish|the\s+beatles)\b",
    re.I,
)


@dataclass
class MediaGoal:
    semantic_action: str = "play_media"
    query: str = ""
    artist: str = ""
    semantic_query: str = ""
    referent: str = ""
    preferred_service: str = ""
    complete: bool = False

    def to_slots(self) -> Dict[str, Any]:
        return {
            "semantic_action": self.semantic_action,
            "media_query": self.query or self.artist or self.semantic_query,
            "query": self.query,
            "artist": self.artist,
            "semantic_query": self.semantic_query,
            "referent": self.referent,
            "preferred_service": self.preferred_service,
        }


def parse_media_goal(text: str, *, default_app: str = "") -> MediaGoal | None:
    raw = str(text or "").strip()
    if not raw or not _PLAY.search(raw):
        return None
    goal = MediaGoal(preferred_service=str(default_app or ""), complete=True)
    if _SIMILAR.search(raw):
        goal.semantic_action = "play_media"
        goal.referent = "current_media"
        goal.query = ""
        return goal
    if _MOOD.search(raw) and _SOMETHING.search(raw):
        goal.semantic_action = "play_media"
        mood = _MOOD.search(raw)
        goal.semantic_query = (mood.group(0) if mood else "spokojna") + " muzyka"
        goal.query = goal.semantic_query
        return goal
    stripped = re.sub(
        r"^(?:pu[sś]ci[cć]|pu[sś][cć]|odtw[oó]rz|graj|zagraj)\s+",
        "",
        raw,
        flags=re.I,
    ).strip(" .,!?")
    stripped = re.sub(r"^co[sś]\s+", "", stripped, flags=re.I).strip()
    if _SOMETHING.search(raw) or _ARTIST_OF.search(raw):
        goal.semantic_action = "play_media"
        titled = stripped
        try:
            from core.intent_family import _title_case_artist

            titled = _title_case_artist(stripped) or stripped
        except Exception:
            titled = stripped
        goal.artist = titled
        goal.query = titled
        return goal
    if stripped:
        goal.semantic_action = "play_track"
        goal.query = stripped.strip(" \"'")
        return goal
    goal.complete = False
    return goal
