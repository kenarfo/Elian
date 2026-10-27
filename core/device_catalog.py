# ==========================
# ELIAN — universal device catalog (any brand via HA)
# ==========================
"""Map user names → HA entity_ids without brand-specific voice routers.

Vizzini / Gree / etc. appear only as Home Assistant entities (or optional
aliases in config). Voice always resolves through DeviceResolver + registry.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from Config import get_config_value


def device_aliases() -> Dict[str, str]:
    """Normalize alias → entity_id from smart_home.aliases."""
    raw = get_config_value("smart_home", "aliases", default=None) or {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for key, value in raw.items():
        k = " ".join(str(key or "").strip().casefold().split())
        v = str(value or "").strip()
        if k and v and "." in v:
            out[k] = v
    return out


def resolve_alias(query: str) -> Optional[str]:
    """Exact / contained alias hit → entity_id."""
    q = " ".join(str(query or "").strip().casefold().split())
    if not q:
        return None
    aliases = device_aliases()
    if q in aliases:
        return aliases[q]
    for name, entity_id in aliases.items():
        if name in q or q in name:
            return entity_id
    return None


def default_fallback_entity() -> str:
    """Optional singleton fallback (legacy). Prefer registry resolve first."""
    return str(get_config_value("smart_home", "entity_id", default="") or "").strip()


def catalog_summary() -> Dict[str, Any]:
    return {
        "aliases": device_aliases(),
        "fallback_entity": default_fallback_entity(),
        "match_cutoff": get_config_value("smart_home", "match_cutoff", default=0.78),
    }


def _light_entities_from_registry() -> List[Any]:
    try:
        from HomeAssistant import get_registry

        pool = list(get_registry().maybe_refresh() or [])
        return [e for e in pool if str(getattr(e, "domain", "") or "") == "light"]
    except Exception:
        return []


def resolve_light_target(
    query: str = "",
    *,
    memory: Any = None,
    prefer_active: bool = True,
) -> Tuple[Optional[str], str]:
    """Resolve a light entity without brand hardcoding.

    Returns (entity_id | None, reason):
      active | alias | unique_registry | default_config | ambiguous | none
    """
    q = str(query or "").strip()

    if prefer_active and memory is not None:
        try:
            from core.device_context import get_active_device

            active = get_active_device(memory)
            if active is not None and str(getattr(active, "domain", "") or "") == "light":
                eid = str(getattr(active, "entity_id", "") or "").strip()
                if eid:
                    return eid, "active"
        except Exception:
            pass

    alias_id = resolve_alias(q) if q else None
    if not alias_id and q:
        # Only apply generic light-word aliases when the utterance actually
        # mentions a lamp/light — not for ambient "ciemno tu" alone.
        q_fold = q.casefold()
        light_mentioned = any(
            token in q_fold
            for token in (
                "lamp",
                "świat",
                "swiat",
                "oświetl",
                "oswietl",
                "vizzini",
            )
        )
        if light_mentioned:
            for key in ("lampa", "światło", "swiatlo", "oswietlenie"):
                alias_id = resolve_alias(key)
                if alias_id:
                    break
    if alias_id:
        return alias_id, "alias"

    lights = _light_entities_from_registry()

    if q:
        try:
            from DeviceResolver import resolve_device

            match = resolve_device(q, domain="light", entities=lights or None)
            status = str(getattr(match, "status", "") or "")
            entity = getattr(match, "entity", None)
            if status in ("exact", "unique") and entity is not None:
                eid = str(getattr(entity, "entity_id", "") or "").strip()
                if eid:
                    return eid, "resolved"
            if status == "ambiguous":
                return None, "ambiguous"
        except Exception:
            pass

    if len(lights) == 1:
        eid = str(getattr(lights[0], "entity_id", "") or "").strip()
        if eid:
            return eid, "unique_registry"

    if len(lights) > 1:
        return None, "ambiguous"

    # No registry lights — optional singleton from config.
    fallback = default_fallback_entity()
    if fallback.startswith("light."):
        return fallback, "default_config"

    return None, "none"
