import json
import os
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from Config import DATA_DIR
from DeviceResolver import normalize_pl


CONTACTS_FILE = os.path.join(DATA_DIR, "contacts.json")


@dataclass
class Contact:
    name: str
    aliases: tuple[str, ...] = ()
    channels: dict = field(default_factory=dict)

    def names(self):
        values = [self.name, *self.aliases]
        return [normalize_pl(value) for value in values if value]


def _load_contacts():
    path = CONTACTS_FILE
    example = os.path.join(DATA_DIR, "contacts.example.json")
    for candidate in (path, example):
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payload = payload.get("contacts", [])
        if not isinstance(payload, list):
            continue
        contacts = []
        for item in payload:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            aliases = item.get("aliases") or []
            channels = item.get("channels") or {}
            contacts.append(Contact(
                name=str(item["name"]).strip(),
                aliases=tuple(str(alias) for alias in aliases if alias),
                channels=dict(channels) if isinstance(channels, dict) else {},
            ))
        return contacts
    return []


def resolve_contact(query):
    query_norm = normalize_pl(query)
    if not query_norm:
        return "none", [], "Do kogo mam to wysłać?"

    scored = []
    for contact in _load_contacts():
        names = contact.names()
        score = 0.0
        for name in names:
            if query_norm == name or query_norm in name or name in query_norm:
                score = max(score, 1.0)
            else:
                score = max(score, SequenceMatcher(None, query_norm, name).ratio())
        if score >= 0.78:
            scored.append((score, contact))

    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return "none", [], f"Nie mam kontaktu: {query}."

    top = scored[0][0]
    close = [contact for score, contact in scored if top - score <= 0.08]
    if len(close) > 1:
        names = " czy ".join(contact.name for contact in close[:3])
        return "ambiguous", close, f"Do kogo: {names}?"
    return "exact", [close[0]], ""
