# ==========================
# ELIAN — PersonalVocabulary (Phase 1 store)
# ==========================
"""Local aliases only. Never bypasses Policy. Not auto-learned from destructive acts."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from core.persistence import safe_read_json

_DEFAULT = {
    "people": [],
    "apps": [],
    "projects": [],
    "devices": [],
    "phrases": [],
}


def vocabulary_path():
    from pathlib import Path

    return Path(__file__).resolve().parents[1] / "Data" / "personal_vocabulary.json"


def load_vocabulary(path=None) -> Dict[str, Any]:
    data, _recovered = safe_read_json(path or vocabulary_path(), default=dict(_DEFAULT))
    raw = data if isinstance(data, dict) else dict(_DEFAULT)
    out: Dict[str, Any] = {}
    for key in _DEFAULT:
        items = raw.get(key) or []
        if key == "phrases":
            kept = []
            for item in items:
                if isinstance(item, dict):
                    src = str(item.get("from") or item.get("alias") or "").strip()
                    dest = str(item.get("to") or item.get("canonical") or "").strip()
                    if src and dest:
                        kept.append({"from": src, "to": dest})
                elif str(item).strip():
                    kept.append(str(item).strip())
            out[key] = kept
        else:
            out[key] = [str(item).strip() for item in items if str(item).strip()]
    return out


def lookup_alias(text: str, *, vocab: Dict[str, List[str]] | None = None) -> Dict[str, Any]:
    blob = str(text or "").lower()
    store = vocab if vocab is not None else load_vocabulary()
    hits = []
    for kind, names in store.items():
        for name in names:
            if isinstance(name, dict):
                label = str(name.get("from") or name.get("alias") or "").strip()
            else:
                label = str(name).strip()
            if label and label.lower() in blob:
                hits.append({"kind": kind, "name": label})
    return {"hits": hits}


def apply_aliases(text: str, *, vocab: Dict[str, List[str]] | None = None) -> str:
    """Replace known phrase aliases before semantics. Never invents tools."""
    raw = str(text or "")
    store = vocab if vocab is not None else load_vocabulary()
    for item in store.get("phrases") or []:
        if isinstance(item, dict):
            src = str(item.get("from") or item.get("alias") or "").strip()
            dest = str(item.get("to") or item.get("canonical") or "").strip()
            if src and dest and src.lower() in raw.lower():
                raw = re.sub(re.escape(src), dest, raw, count=1, flags=re.I)
    return raw


def remember_phrase(src: str, dest: str, *, path=None) -> bool:
    """Store a user-taught rewrite. Policy still applies after expansion."""
    alias = str(src or "").strip()
    canonical = str(dest or "").strip()
    if not alias or not canonical:
        return False
    try:
        from memory.sensitive import looks_sensitive

        if looks_sensitive(alias) or looks_sensitive(canonical):
            return False
    except Exception:
        pass
    target = path or vocabulary_path()
    store = load_vocabulary(target)
    phrases: List[Any] = list(store.get("phrases") or [])
    lowered = alias.lower()
    kept = []
    for item in phrases:
        if isinstance(item, dict):
            existing = str(item.get("from") or item.get("alias") or "").strip().lower()
            if existing == lowered:
                continue
        kept.append(item)
    kept.append({"from": alias, "to": canonical})
    payload = dict(store)
    payload["phrases"] = kept
    try:
        import json

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False
