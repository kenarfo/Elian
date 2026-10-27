# ==========================
# ELIAN — ActionHistory + semantic undo
# ==========================
"""Compatibility facade. Implementation lives in core.action_journal."""

from __future__ import annotations

from core.action_journal import (
    ActionJournal,
    EntryStatus,
    JournalEntry,
    UndoResult,
    get_journal,
    last_action,
    note_registry_result,
    record_action,
    reset_action_history,
    set_session_id,
    undo_last_action,
)

MAX_HISTORY = 32

__all__ = [
    "ActionJournal",
    "EntryStatus",
    "JournalEntry",
    "MAX_HISTORY",
    "UndoResult",
    "get_journal",
    "last_action",
    "note_registry_result",
    "record_action",
    "reset_action_history",
    "set_session_id",
    "undo_last_action",
]
