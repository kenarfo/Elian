# ==========================
# ELIAN — named memory layers (Phase 1 facade)
# ==========================
"""Map existing MemoryManager stores onto named layers. Do not dump all layers into prompts."""

from __future__ import annotations

from typing import Any, Dict, Optional

WORKING = "working"
CONVERSATION = "conversation"
TASK = "task"
EPISODIC = "episodic"
SEMANTIC = "semantic"
PROJECT = "project"
PROCEDURAL = "procedural"
TEMPORARY = "temporary"

LAYER_NAMES = (
    WORKING,
    CONVERSATION,
    TASK,
    EPISODIC,
    SEMANTIC,
    PROJECT,
    PROCEDURAL,
    TEMPORARY,
)


def layers_from_manager(manager: Any, *, project: str = "") -> Dict[str, Any]:
    """Read-only view. Missing layers are empty — never invent facts."""
    short = getattr(manager, "short", None)
    long = getattr(manager, "long", None)
    working = list(getattr(short, "turns", None) or [])[-8:]
    conversation = {
        "topic": str(getattr(short, "topic", "") or ""),
        "focus": dict(getattr(short, "focus", None) or {}),
        "entities": [
            item.to_dict() if hasattr(item, "to_dict") else dict(item)
            for item in (getattr(short, "entities", None) or {}).values()
        ],
    }
    facts = list(getattr(long, "facts", None) or [])
    if not facts and hasattr(manager, "long") and hasattr(manager.long, "all"):
        try:
            facts = list(manager.long.all() or [])
        except Exception:
            facts = []
    episodic = []
    try:
        raw = getattr(manager, "_store", None) or {}
        if isinstance(raw, dict):
            episodic = list(raw.get("episodic_hooks") or [])
    except Exception:
        episodic = []
    extra = _extra_buckets(manager)
    wanted = str(project or "").strip().lower()
    projects = dict(extra.get("projects") or {})
    project_slice = projects.get(wanted, {}) if wanted else {}
    typed = []
    temporary_typed = []
    try:
        store = getattr(manager, "typed", None)
        if store is not None and hasattr(store, "all"):
            typed = list(store.all(include_session=False) or [])
            temporary_typed = [
                item for item in typed if str(item.get("type") or "") == "TEMPORARY"
            ]
    except Exception:
        typed = []
        temporary_typed = []
    semantic = facts
    if typed:
        semantic = facts + [
            item
            for item in typed
            if str(item.get("type") or "") in ("FACT", "RELATION", "PREFERENCE")
        ]
    return {
        WORKING: working,
        CONVERSATION: conversation,
        TASK: dict(extra.get("task") or {}),
        EPISODIC: episodic,
        SEMANTIC: semantic,
        PROJECT: project_slice,
        PROCEDURAL: list(extra.get("procedures") or []),
        TEMPORARY: list(getattr(short, "recent_observations", None) or []) + temporary_typed,
    }


def _extra_buckets(manager: Any) -> Dict[str, Any]:
    store = getattr(manager, "_extra_layers", None)
    if isinstance(store, dict):
        return store
    return {"projects": {}, "task": {}, "procedures": []}


def salient_layers(
    manager: Any,
    *,
    utterance: str = "",
    project: str = "",
    task_active: bool = False,
) -> Dict[str, Any]:
    """Only layers that may enter a prompt. Talk/jokes get conversation working memory only."""
    all_layers = layers_from_manager(manager, project=project)
    text = str(utterance or "").lower()
    selected = {
        WORKING: all_layers[WORKING][-4:],
        CONVERSATION: all_layers[CONVERSATION],
    }
    if task_active or "jak idzie" in text or "wróćmy" in text or "wrocmy" in text:
        selected[TASK] = all_layers[TASK]
        selected[PROJECT] = all_layers[PROJECT]
    if project:
        selected[PROJECT] = all_layers[PROJECT]
    return selected
