"""In-session short-term memory (turns + active entities)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from memory.store import clean_text, now_iso


MAX_TURNS = 12
MAX_OBSERVATIONS = 8


@dataclass
class ContextEntity:
    type: str
    name: str
    id: str = ""
    domain: str = ""
    canonical_name: str = ""
    capabilities: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    resolved_at: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        data = {
            "type": self.type,
            "name": self.name,
            "entity_id": self.id or self.meta.get("entity_id", ""),
            "domain": self.domain or self.meta.get("domain", ""),
            "canonical_name": self.canonical_name or self.name,
        }
        if self.id:
            data["id"] = self.id
        if self.capabilities:
            data["capabilities"] = list(self.capabilities)
        if self.aliases:
            data["aliases"] = list(self.aliases)
        if self.resolved_at:
            data["resolved_at"] = self.resolved_at
        if self.meta:
            data["meta"] = dict(self.meta)
        return data


class ShortTermMemory:
    def __init__(self, max_turns=MAX_TURNS):
        self.max_turns = max(2, int(max_turns))
        self.turns: list[dict[str, Any]] = []
        self.entities: dict[str, ContextEntity] = {}
        self.topic: str = ""
        self.pending_clarification: dict[str, Any] | None = None
        self.pending_confirmation: dict[str, Any] | None = None
        self.recent_observations: list[dict[str, Any]] = []
        self.focus: dict[str, Any] = {
            "current_topic": "",
            "referenced_people": [],
            "referenced_apps": [],
            "referenced_devices": [],
            "referenced_web_entities": [],
            "last_question_summary": "",
            "last_answer_summary": "",
            "last_web_consensus": "",
            "last_web_query": "",
        }
        self.last_device_operation: dict[str, Any] = {}

    def clear(self):
        self.turns.clear()
        self.entities.clear()
        self.topic = ""
        self.pending_clarification = None
        self.pending_confirmation = None
        self.recent_observations.clear()
        self.focus = {
            "current_topic": "",
            "referenced_people": [],
            "referenced_apps": [],
            "referenced_devices": [],
            "referenced_web_entities": [],
            "last_question_summary": "",
            "last_answer_summary": "",
            "last_web_consensus": "",
            "last_web_query": "",
        }
        self.last_device_operation = {}

    def add_turn(self, role, content):
        text = clean_text(content, 800)
        if role not in ("user", "assistant") or not text:
            return
        self.turns.append({
            "role": role,
            "content": text,
            "timestamp": now_iso(),
        })
        self.turns = self.turns[-self.max_turns:]

    def set_entity(self, entity_type, name, *, entity_id="", domain="", capabilities=None, **meta):
        name = clean_text(name, 120)
        entity_id = clean_text(entity_id, 120)
        # Prefer entity_id as identity; name may be display / fallback.
        if not name and not entity_id:
            return
        if not name:
            name = entity_id
        caps = list(capabilities or [])
        if domain == "light" and "brightness" not in caps:
            caps.append("brightness")
        if domain == "climate" and "temperature" not in caps:
            caps.append("temperature")
        alias = clean_text(str(meta.pop("alias", "") or ""), 120)
        aliases = []
        if alias:
            aliases.append(alias.lower())
        if name and name.lower() not in aliases:
            aliases.append(name.lower())
        entity = ContextEntity(
            type=str(entity_type or "entity"),
            name=name,
            id=entity_id,
            domain=clean_text(domain, 40),
            canonical_name=name,
            capabilities=caps,
            aliases=aliases,
            resolved_at=now_iso(),
            meta={key: value for key, value in meta.items() if value not in (None, "")},
        )
        if alias:
            entity.meta["alias"] = alias
        if entity.id:
            entity.meta.setdefault("entity_id", entity.id)
        if entity.domain:
            entity.meta.setdefault("domain", entity.domain)
        self.entities[entity.type] = entity
        # Convenience aliases
        if entity.type == "application":
            self.entities["app"] = entity
        if entity.type == "device":
            self.entities["device"] = entity

    def get_entity(self, entity_type, default=None):
        entity = self.entities.get(entity_type)
        return entity if entity else default

    def resolve_pronoun(self, text):
        """Best-effort: map ją/je/go/tam to last relevant entity."""
        lowered = str(text or "").lower()
        app = self.entities.get("application") or self.entities.get("app")
        device = self.entities.get("device")

        if any(token in lowered.split() for token in ("ją", "ja", "nią", "nia", "tę", "te", "tą", "ta")):
            if device:
                return device
        if any(token in lowered.split() for token in ("je", "go", "niego", "to", "ten")):
            if device and any(
                token in lowered.split()
                for token in ("je", "go", "niego", "to", "ten")
            ):
                # Device anaphora first for HA follow-ups; app only if no device.
                if device and any(
                    w in lowered
                    for w in ("włącz", "wlacz", "wyłącz", "wylacz", "ustaw", "przyciemn", "rozjaśn", "rozjasn")
                ):
                    return device
            if app:
                return app
            if device:
                return device
        if "tam" in lowered.split() or "tam." in lowered:
            if app:
                return app
        return None

    def add_observation(self, observation):
        if not isinstance(observation, dict):
            return
        self.recent_observations.append(dict(observation))
        self.recent_observations = self.recent_observations[-MAX_OBSERVATIONS:]

    def update_focus(
        self,
        *,
        user_text="",
        assistant_text="",
        entities=None,
        web_consensus="",
        web_query="",
    ):
        from core.conversation_focus import update_focus

        self.focus = update_focus(
            self.focus,
            user_text=user_text,
            assistant_text=assistant_text,
            entities=entities,
            web_consensus=web_consensus,
            web_query=web_query,
        )
        if self.focus.get("current_topic"):
            self.topic = str(self.focus["current_topic"])

    def snapshot(self):
        return {
            "turns": list(self.turns),
            "entities": {
                key: entity.to_dict()
                for key, entity in self.entities.items()
            },
            "topic": self.topic,
            "focus": dict(self.focus),
            "pending_clarification": dict(self.pending_clarification or {}) or None,
            "pending_confirmation": dict(self.pending_confirmation or {}) or None,
            "observations": list(self.recent_observations),
            "last_device_operation": dict(self.last_device_operation or {}),
        }
