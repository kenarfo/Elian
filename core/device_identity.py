# ==========================
# ELIAN — device identity / spoken names (C.5.5)
# ==========================
"""Separate entity_id, friendly_name, and what Elian actually says."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional

from Config import get_config_value


_ENTITY_ID = re.compile(r"\b[a-z][a-z0-9_]+\.[a-z0-9_]+\b", re.I)


@dataclass
class DeviceIdentity:
    entity_id: str = ""
    canonical_name: str = ""
    friendly_name: str = ""
    spoken_name: str = ""
    aliases: List[str] = field(default_factory=list)
    domain: str = ""

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "canonical_name": self.canonical_name,
            "friendly_name": self.friendly_name,
            "spoken_name": self.spoken_name,
            "aliases": list(self.aliases),
            "domain": self.domain,
        }


def dedup_device_name(name: str) -> str:
    """Drop repeated tokens: 'Inspire Vizzini D56 Vizzini' → 'Inspire Vizzini D56'."""
    tokens = [t for t in str(name or "").split() if t]
    seen = set()
    out: List[str] = []
    for token in tokens:
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return " ".join(out).strip()


def strip_entity_ids(text: str) -> str:
    return _ENTITY_ID.sub("", str(text or "")).strip()


def has_duplicate_device_token(text: str) -> bool:
    tokens = [t.casefold() for t in str(text or "").split() if t]
    return len(tokens) != len(set(tokens))


def _configured_spoken(entity_id: str) -> str:
    mapping = get_config_value("smart_home", "spoken_names", default={}) or {}
    if isinstance(mapping, dict):
        return str(mapping.get(entity_id) or "").strip()
    return ""


def spoken_name_for(
    *,
    entity_id: str = "",
    friendly_name: str = "",
    domain: str = "",
    utterance: str = "",
    disambiguate: bool = False,
) -> str:
    """Ordinary speech uses a semantic name, not the product string."""
    configured = _configured_spoken(entity_id)
    raw = str(utterance or "").lower()
    if disambiguate:
        return dedup_device_name(friendly_name or configured or entity_id.split(".")[-1])
    if re.search(r"[sś]wiat[lł]", raw):
        return "światło"
    if re.search(r"\blamp", raw):
        return "lampa"
    if configured:
        return configured
    domain = (domain or (entity_id.split(".", 1)[0] if "." in entity_id else "")).lower()
    if domain == "light":
        return "światło"
    if domain == "climate":
        return "klimatyzacja"
    if domain == "switch":
        return "przełącznik"
    cleaned = dedup_device_name(friendly_name)
    if cleaned:
        return cleaned
    if "." in entity_id:
        return entity_id.split(".", 1)[1].replace("_", " ")
    return "urządzenie"


def identity_from_entity(entity: Any, *, utterance: str = "", disambiguate: bool = False) -> DeviceIdentity:
    entity_id = str(getattr(entity, "entity_id", "") or "")
    friendly = str(getattr(entity, "friendly_name", "") or "")
    domain = str(getattr(entity, "domain", "") or "")
    aliases = list(getattr(entity, "aliases", ()) or [])
    canonical = dedup_device_name(friendly) or entity_id
    spoken = spoken_name_for(
        entity_id=entity_id,
        friendly_name=friendly,
        domain=domain,
        utterance=utterance,
        disambiguate=disambiguate,
    )
    extras = ("światło", "lampa", "vizzini", "lampa vizzini")
    merged = []
    seen = set()
    for item in list(aliases) + [spoken, canonical] + list(extras):
        key = str(item or "").strip().casefold()
        if key and key not in seen:
            seen.add(key)
            merged.append(str(item).strip())
    return DeviceIdentity(
        entity_id=entity_id,
        canonical_name=canonical,
        friendly_name=friendly,
        spoken_name=spoken,
        aliases=merged,
        domain=domain,
    )
