"""Central MemoryManager — short-term, long-term, preferences, context builder."""

from __future__ import annotations

import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from Diagnostics import log_turn

from memory.classify import classify_remember_text
from memory.long_term import LongTermMemory
from memory.preferences import PreferencesStore
from memory.retrieval import select_compact
from memory.sensitive import looks_sensitive, refuse_sensitive_message
from memory.short_term import ShortTermMemory
from memory.store import (
    SCHEMA_VERSION,
    atomic_write_json,
    clean_text,
    locked,
    now_iso,
    query_terms,
    read_json,
)
from memory.typed_store import TypedMemoryStore
from memory.types import (
    CONFIDENCE_EXPLICIT,
    MemoryType,
    WriteResult,
    WriteStatus,
    normalize_type,
)


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "Data"
DEFAULT_PATH = DATA_DIR / "elian_memory.json"
LEGACY_PATH = DATA_DIR / "memory.json"

MAX_CONTEXT_CHARS = 1800


class MemoryManager:
    def __init__(self, path=None, *, now_fn=None):
        self.path = Path(path) if path else DEFAULT_PATH
        self.now_fn = now_fn
        self.short = ShortTermMemory()
        self.long = LongTermMemory()
        self.preferences = PreferencesStore()
        self.typed = TypedMemoryStore(now_fn=now_fn)
        self._load()

    # ------------------------------------------------------------------ storage
    def _stamp(self):
        if self.now_fn:
            return str(self.now_fn())
        return now_iso()

    def _empty_store(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "facts": [],
            "preferences": [],
            "entries": [],
            "recent_actions": [],
            "conversation": [],
            "episodic_hooks": [],
        }

    @locked
    def _load(self):
        raw = read_json(self.path, default=None)
        if raw is None or raw == {}:
            # Migrate legacy only for the default production store — never into
            # explicit/temp paths (unittest / behavioral harness isolation).
            use_legacy = False
            try:
                use_legacy = self.path.resolve() == DEFAULT_PATH.resolve()
            except Exception:
                use_legacy = self.path == DEFAULT_PATH
            if use_legacy:
                legacy = read_json(LEGACY_PATH, default=None)
                if legacy:
                    raw = self._migrate_legacy(legacy)
                    self._write(raw)
                else:
                    raw = self._empty_store()
                    self._write(raw)
            else:
                raw = self._empty_store()
                self._write(raw)

        version = int(raw.get("schema_version") or raw.get("version") or 1)
        if version < 3:
            raw = self._migrate_legacy(raw)
            version = int(raw.get("schema_version") or 3)
        if version < SCHEMA_VERSION:
            raw = self._migrate_v4(raw)
            self._write(raw)

        facts = []
        for item in raw.get("facts") or []:
            if isinstance(item, str):
                facts.append({
                    "id": f"fact_migrated_{abs(hash(item)) % 10**10}",
                    "text": clean_text(item, 500),
                    "category": "fact",
                    "tags": [],
                    "explicit": True,
                    "source": "legacy",
                    "confidence": 1.0,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                    "last_used_at": now_iso(),
                })
            elif isinstance(item, dict) and item.get("text"):
                entry = dict(item)
                entry.setdefault("id", f"fact_migrated_{abs(hash(entry['text'])) % 10**10}")
                entry.setdefault("tags", [])
                entry.setdefault("explicit", True)
                entry.setdefault("source", "legacy")
                entry.setdefault("confidence", 1.0)
                facts.append(entry)

        self.long = LongTermMemory(facts)
        self.preferences = PreferencesStore(raw.get("preferences") or [])
        entries = list(raw.get("entries") or [])
        if not entries:
            entries = self._entries_from_legacy(facts, raw.get("preferences") or [])
        self.typed = TypedMemoryStore(entries, now_fn=self.now_fn)
        self.typed.purge_expired()

    def _migrate_legacy(self, legacy):
        store = self._empty_store()
        if isinstance(legacy, list):
            store["facts"] = [
                {
                    "id": f"fact_migrated_{index}",
                    "text": clean_text(item, 500),
                    "category": "fact",
                    "tags": [],
                    "explicit": True,
                    "source": "legacy",
                    "confidence": 1.0,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                    "last_used_at": now_iso(),
                }
                for index, item in enumerate(legacy)
                if clean_text(str(item), 500)
            ]
            return store

        if not isinstance(legacy, dict):
            return store

        facts = []
        for index, item in enumerate(legacy.get("facts") or []):
            if isinstance(item, str):
                text = clean_text(item, 500)
                if not text:
                    continue
                facts.append({
                    "id": f"fact_migrated_{index}",
                    "text": text,
                    "category": "fact",
                    "tags": [],
                    "explicit": True,
                    "source": "legacy",
                    "confidence": 1.0,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                    "last_used_at": now_iso(),
                })
            elif isinstance(item, dict) and item.get("text"):
                entry = dict(item)
                entry.setdefault("id", item.get("id") or f"fact_migrated_{index}")
                entry.setdefault("tags", [])
                entry.setdefault("explicit", True)
                entry.setdefault("source", "legacy")
                entry.setdefault("confidence", 1.0)
                facts.append(entry)
        store["facts"] = facts
        store["preferences"] = list(legacy.get("preferences") or [])
        store["recent_actions"] = list(legacy.get("recent_actions") or [])[-50:]
        store["conversation"] = list(legacy.get("conversation") or [])[-16:]
        store["entries"] = self._entries_from_legacy(facts, store["preferences"])
        return store

    def _entries_from_legacy(self, facts, preferences):
        entries = []
        stamp = self._stamp()
        for item in facts or []:
            if not isinstance(item, dict):
                continue
            text = clean_text(str(item.get("text") or ""), 500)
            if not text:
                continue
            entries.append({
                "id": item.get("id") or f"fact_migrated_{abs(hash(text)) % 10**10}",
                "type": MemoryType.FACT.value,
                "key": clean_text(str(item.get("key") or item.get("category") or "fact"), 80),
                "value": text,
                "text": text,
                "source": item.get("source") or "legacy",
                "confidence": float(item.get("confidence") or 1.0),
                "created_at": item.get("created_at") or stamp,
                "updated_at": item.get("updated_at") or stamp,
                "last_used_at": item.get("last_used_at") or stamp,
                "importance": 0.6 if item.get("explicit", True) else 0.4,
                "explicit": bool(item.get("explicit", True)),
                "tags": list(item.get("tags") or []),
                "provenance": {"migrated_from": "facts", "schema": 3},
                "persistent": True,
            })
        for item in preferences or []:
            if not isinstance(item, dict) or not item.get("key"):
                continue
            entries.append({
                "id": item.get("id") or f"preference_migrated_{abs(hash(item['key'])) % 10**10}",
                "type": MemoryType.PREFERENCE.value,
                "key": item.get("key"),
                "value": item.get("value") or "",
                "text": f"{item.get('key')}={item.get('value')}",
                "source": item.get("source") or "legacy",
                "confidence": float(item.get("confidence") or 1.0),
                "created_at": item.get("created_at") or stamp,
                "updated_at": item.get("updated_at") or stamp,
                "last_used_at": item.get("last_used_at") or item.get("updated_at") or stamp,
                "importance": 0.85,
                "explicit": bool(item.get("explicit", True)),
                "provenance": {"migrated_from": "preferences", "schema": 3},
                "persistent": True,
            })
        return entries

    def _migrate_v4(self, raw):
        store = dict(raw) if isinstance(raw, dict) else self._empty_store()
        store["schema_version"] = SCHEMA_VERSION
        store.setdefault("facts", [])
        store.setdefault("preferences", [])
        store.setdefault("recent_actions", [])
        store.setdefault("conversation", [])
        store.setdefault("episodic_hooks", [])
        entries = list(store.get("entries") or [])
        if not entries:
            entries = self._entries_from_legacy(store.get("facts") or [], store.get("preferences") or [])
        store["entries"] = entries
        return store

    def _serialize(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "facts": self.long.all(),
            "preferences": self.preferences.all(),
            "entries": self.typed.persistent_dump(),
            "recent_actions": getattr(self, "_actions", [])[-50:],
            "conversation": getattr(self, "_conversation", [])[-16:],
            "episodic_hooks": [],
        }

    @locked
    def _write(self, data=None):
        payload = data if data is not None else self._serialize()
        atomic_write_json(self.path, payload)

    def save(self):
        self._write()

    # ------------------------------------------------------------------ API
    def clear_session(self):
        self.short.clear()
        self.typed.drop_type(MemoryType.SESSION)

    def add_turn(self, role, content):
        self.short.add_turn(role, content)

    def remember(
        self,
        *,
        type,
        key,
        value,
        source="user",
        confidence=None,
        explicit=True,
        expires_at=None,
        importance=None,
        provenance=None,
        text="",
        tags=None,
        ask_on_conflict=True,
    ):
        blob = f"{key} {value} {text}"
        if looks_sensitive(blob):
            return WriteResult(
                WriteStatus.REJECTED_SECRET,
                message=refuse_sensitive_message(),
            )
        kind = normalize_type(type)
        result = self.typed.upsert(
            type=kind,
            key=key,
            value=value,
            source=source,
            confidence=confidence,
            explicit=explicit,
            expires_at=expires_at,
            importance=importance,
            provenance=provenance,
            text=text or str(value or ""),
            tags=tags,
            ask_on_conflict=False if kind == MemoryType.PREFERENCE and explicit else ask_on_conflict,
        )
        if not result.ok:
            return result
        if kind not in (MemoryType.TEMPORARY, MemoryType.SESSION):
            self._project_entry(result.entry or {})
        if kind != MemoryType.SESSION:
            self.save()
        return result

    def remember_fact(self, text, *, category="fact", tags=None, explicit=True, source="user"):
        if looks_sensitive(text):
            return False, refuse_sensitive_message()
        text = clean_text(text, 500)
        if not text:
            return False, "Nie mam czego zapamiętać."
        classified = classify_remember_text(text, now=self._stamp())
        kind = classified.get("type") or MemoryType.FACT
        if kind == MemoryType.PREFERENCE and classified.get("key"):
            return self.set_preference(
                classified["key"],
                classified.get("value") or "",
                source=source,
                explicit=explicit,
            )
        result = self.typed.upsert(
            type=kind,
            key=classified.get("key") or category,
            value=classified.get("value") or text,
            source=source,
            confidence=CONFIDENCE_EXPLICIT if explicit else 0.4,
            explicit=explicit,
            expires_at=classified.get("expires_at"),
            provenance=classified.get("provenance"),
            text=classified.get("text") or text,
            tags=tags,
            ask_on_conflict=True,
        )
        if result.status == WriteStatus.CONFLICT_ASK:
            return False, result.message
        if result.status == WriteStatus.REJECTED_WEAK:
            return False, result.message
        if result.status == WriteStatus.REJECTED_SECRET:
            return False, result.message
        entry = result.entry
        if kind != MemoryType.TEMPORARY:
            entry = self.long.remember(
                classified.get("text") or text,
                category=category,
                tags=tags,
                explicit=explicit,
                source=source,
            ) or entry
        if entry is None and not result.ok:
            return False, "Nie mam czego zapamiętać."
        self.save()
        log_turn("memory", event="remember_fact", id=(result.entry or entry or {}).get("id"), text=str(text)[:80])
        return True, "Zapamiętałem."

    def set_preference(self, key, value, *, source="user", explicit=True, confidence=None):
        if looks_sensitive(f"{key} {value}"):
            return False, refuse_sensitive_message()
        result = self.typed.upsert(
            type=MemoryType.PREFERENCE,
            key=key,
            value=value,
            source=source,
            confidence=confidence if confidence is not None else (CONFIDENCE_EXPLICIT if explicit else 0.4),
            explicit=explicit,
            importance=0.85 if explicit else 0.4,
            provenance={"api": "set_preference"},
            ask_on_conflict=False,
        )
        if result.status == WriteStatus.REJECTED_WEAK:
            log_turn("memory", event="set_preference_rejected_weak", key=str(key))
            return False, result.message or "Nie zmieniam — mam pewniejszą preferencję."
        entry = self.preferences.set(
            key,
            value,
            source=source,
            explicit=explicit,
            confidence=confidence,
        )
        if entry is None:
            return False, "Nie udało się zapisać preferencji."
        self.save()
        log_turn("memory", event="set_preference", key=entry["key"], value=str(entry["value"])[:80])
        print(f"[MEMORY] preference {entry['key']}={entry['value']}")
        return True, "Zapamiętam."

    def get_preference(self, key, default=None):
        value = self.preferences.get(key, default)
        if value is not None:
            found = self.typed.find_by_key(key, type=MemoryType.PREFERENCE)
            if found:
                self.typed.touch(found["id"])
        return value

    def behavior_preferences(self):
        """Small read API for quiet hours / morning brief — does not rewrite ProactivePolicy."""
        return {
            "quiet_hours_enabled": self.get_preference("proactive.quiet_hours.enabled"),
            "quiet_hours_start": self.get_preference("proactive.quiet_hours.start"),
            "quiet_hours_end": self.get_preference("proactive.quiet_hours.end"),
            "morning_brief": self.get_preference("voice.morning_brief"),
            "response_style": self.get_preference("voice.response_style"),
        }

    def search_facts(self, query, limit=6):
        hits = self.long.search(query, limit=limit)
        extra = self.typed.retrieve(
            query,
            types=(MemoryType.FACT, MemoryType.RELATION, MemoryType.TEMPORARY),
            limit=limit,
        )
        seen = {str(item.get("text") or "").casefold() for item in hits}
        for item in extra:
            text = str(item.get("text") or item.get("value") or "")
            if not text or text.casefold() in seen:
                continue
            seen.add(text.casefold())
            hits.append({**item, "text": text})
            if len(hits) >= limit:
                break
        return hits

    def retrieve(self, query="", *, types=None, key=None, limit=8, include_session=False):
        return self.typed.retrieve(
            query,
            types=types,
            key=key,
            limit=limit,
            include_session=include_session,
        )

    def select_compact_memory(self, goal, *, intent_family="", max_items=8, max_chars=900):
        prefs = self.preferences.as_dict()
        selected = select_compact(
            self.typed,
            goal,
            intent_family=intent_family,
            extra_preferences=prefs,
            max_items=max_items,
            max_chars=max_chars,
        )
        return selected

    def cleanup_candidates(self):
        return self.typed.cleanup_candidates()

    def purge_expired(self):
        removed = self.typed.purge_expired()
        if removed:
            self.save()
        return removed

    def remember_vocabulary(self, src, dest, *, source="user"):
        """Explicit alias. Writes personal_vocabulary.json AND a VOCABULARY entry."""
        if looks_sensitive(f"{src} {dest}"):
            return False, refuse_sensitive_message()
        ok = self._dual_write_vocabulary(src, dest)
        result = self.typed.upsert(
            type=MemoryType.VOCABULARY,
            key=src,
            value=dest,
            source=source,
            confidence=CONFIDENCE_EXPLICIT,
            explicit=True,
            provenance={"store": "personal_vocabulary.json"},
            ask_on_conflict=False,
        )
        if result.ok:
            self.save()
        return ok or result.ok, "Zapamiętałem." if (ok or result.ok) else "Nie zapisałem aliasu."

    def _dual_write_vocabulary(self, src, dest):
        try:
            from memory.vocabulary import remember_phrase

            return bool(remember_phrase(src, dest))
        except Exception:
            return False

    def _project_entry(self, entry):
        if not isinstance(entry, dict):
            return
        kind = normalize_type(entry.get("type"))
        if kind == MemoryType.PREFERENCE:
            self.preferences.set(
                entry.get("key"),
                entry.get("value"),
                source=entry.get("source") or "user",
                explicit=bool(entry.get("explicit", True)),
                confidence=entry.get("confidence"),
            )
        elif kind in (MemoryType.FACT, MemoryType.RELATION):
            text = entry.get("text") or entry.get("value")
            if text:
                self.long.remember(
                    text,
                    category=kind.value.lower(),
                    tags=entry.get("tags") or [],
                    explicit=bool(entry.get("explicit", True)),
                    source=entry.get("source") or "user",
                    confidence=float(entry.get("confidence") or 1.0),
                )

    def forget(self, query):
        if looks_sensitive(query):
            # Still allow forgetting sensitive-looking queries about stored facts.
            pass
        pref_removed = self.preferences.forget(query)
        fact_removed = self.long.forget(query)
        typed_removed = self.typed.forget(query)
        if pref_removed or fact_removed or typed_removed:
            self.save()
            log_turn(
                "memory",
                event="forget",
                preferences=len(pref_removed),
                facts=len(fact_removed),
                entries=len(typed_removed),
            )
            return {
                "preferences": pref_removed,
                "facts": fact_removed,
                "entries": typed_removed,
            }
        return {"preferences": [], "facts": [], "entries": []}

    def note_tool_success(self, tool_name, arguments=None, observation=None):
        """Update active entities after tool resolution — prefer canonical HA ids."""
        arguments = dict(arguments or {})
        observation = dict(observation or {})
        if observation:
            self.short.add_observation(observation)
        tool = str(tool_name or "")
        data = dict(observation.get("data") or {})
        status = str(observation.get("status") or "").lower()
        success = bool(observation.get("success", True))

        # Device tools: update from resolved_* even when action is unsupported
        # (target resolution itself succeeded). Never poison with raw aliases.
        if tool in ("turn_on_device", "turn_off_device", "set_device_value"):
            from core.device_context import extract_resolved_device, sync_active_device_to_conversation_context

            resolved = extract_resolved_device(observation, arguments)
            if resolved is None or not resolved.is_valid:
                # Failed before resolution — do not overwrite prior active_device.
                return
            if not success and status not in ("unsupported", "success", ""):
                # Soft: keep prior context on hard failures after resolve
                # but still refresh identity when we know the target.
                pass
            name = resolved.name or resolved.entity_id
            self.short.set_entity(
                "device",
                name,
                entity_id=resolved.entity_id,
                domain=resolved.domain,
                capabilities=resolved.capabilities,
                alias=resolved.alias,
                last_action=resolved.last_action or tool,
            )
            sync_active_device_to_conversation_context(resolved)
            if success:
                from core.device_context import build_last_device_operation

                op = build_last_device_operation(tool, arguments, observation)
                if op is not None:
                    self.short.last_device_operation = op.to_dict()
            print(
                f"[CONTEXT] active_app="
                f"{(self.short.get_entity('application') or type('E', (), {'name': None})()).name} "
                f"active_device={resolved.entity_id} "
                f"domain={resolved.domain}"
            )
            return

        if not success:
            return

        name = (
            arguments.get("name")
            or arguments.get("app")
            or (observation.get("data") or {}).get("app")
            or (observation.get("data") or {}).get("target")
            or ""
        )

        if tool in ("open_app", "close_app", "check_app_installed") and name:
            try:
                from core.active_service import note_service_from_observation

                note_service_from_observation(
                    tool_name=tool, arguments=arguments, observation=observation
                )
            except Exception:
                pass
            kind = str(data.get("resolved_kind") or "")
            if kind == "WEBSITE" or tool == "close_browser_tab":
                url = str(data.get("official_url") or data.get("target") or name)
                if url:
                    self.short.set_entity("website", url)
                print(
                    f"[CONTEXT] active_service={data.get('canonical_name') or name} "
                    f"resolved_kind={kind or 'WEBSITE'}"
                )
                return
            self.short.set_entity("application", name)
            try:
                from ConversationContext import update as update_ha_context
                update_ha_context(last_application=name, last_app=name)
            except Exception:
                pass
        elif tool == "close_browser_tab":
            try:
                from core.active_service import note_service_from_observation

                note_service_from_observation(
                    tool_name=tool, arguments=arguments, observation=observation
                )
            except Exception:
                pass
        elif tool in ("open_website", "web_search"):
            target = arguments.get("target") or arguments.get("url") or arguments.get("query") or ""
            if target:
                self.short.set_entity("website", target)

        print(
            f"[CONTEXT] active_app="
            f"{(self.short.get_entity('application') or type('E', (), {'name': None})()).name} "
            f"active_device="
            f"{(self.short.get_entity('device') or type('E', (), {'name': None})()).name}"
        )

    def expand_utterance(self, text):
        """Rewrite simple pronouns using active entities for parsers/tools.

        Device pronouns keep speech intact when a canonical entity_id exists —
        CommandParser / FastPath resolve via ConversationContext, not raw alias.
        """
        original = str(text or "")
        entity = self.short.resolve_pronoun(original)
        if entity is None:
            # Implicit device follow-up without pronoun ("Ustaw na 55%").
            from core.device_context import get_active_device, looks_like_percent_followup, sync_active_device_to_conversation_context

            device = get_active_device(self)
            if device and looks_like_percent_followup(original):
                sync_active_device_to_conversation_context(device)
            return original

        if entity.type == "device" and getattr(entity, "id", ""):
            from core.device_context import ActiveDevice, sync_active_device_to_conversation_context

            sync_active_device_to_conversation_context(
                ActiveDevice(
                    entity_id=entity.id,
                    domain=entity.domain or (entity.id.split(".", 1)[0] if "." in entity.id else ""),
                    name=entity.canonical_name or entity.name,
                    last_action=str((entity.meta or {}).get("last_action") or ""),
                    capabilities=list(entity.capabilities or []),
                )
            )
            # Do not stringify to raw alias ("lampe") — keeps entity_id identity.
            return original

        if entity.name.casefold() in original.casefold():
            return original
        rewritten = re.sub(
            r"\b(ją|ja|je|go|niego|nią|nia|tam)\b",
            entity.name,
            original,
            count=1,
            flags=re.I,
        )
        if rewritten != original:
            return rewritten
        return original + f" [kontekst: {entity.type}={entity.name}]"

    def resolve_references(self, text):
        entity = self.short.resolve_pronoun(text)
        return text, entity

    def build_context(
        self,
        user_message="",
        *,
        max_chars=MAX_CONTEXT_CHARS,
        include_preferences=True,
        knowledge_only=False,
    ):
        """Compact, non-secret context for Agent / Brain / Planner."""
        user_message = str(user_message or "")
        local_ha = False
        try:
            from core.local_device_status import should_exclude_website_context

            local_ha = should_exclude_website_context(user_message)
        except Exception:
            local_ha = False

        prefs = self.preferences.as_dict()
        relevant_prefs = {}
        if include_preferences and not knowledge_only and not local_ha:
            relevant_prefs = dict(list(prefs.items())[:12])
            # Always include music/browser defaults if present.
            for key in ("music.default_app", "browser.default", "game.launcher_preference"):
                if key in prefs:
                    relevant_prefs[key] = prefs[key]
            # Relevance filter: skip music prefs for pure knowledge questions.
            lowered = user_message.lower()
            knowledgeish = any(
                token in lowered
                for token in (
                    "co to jest",
                    "czym jest",
                    "co oznacza",
                    "wyjaśnij",
                    "wyjasnij",
                    "czarna dziura",
                    "prezydent",
                )
            )
            if knowledgeish:
                relevant_prefs = {
                    key: value
                    for key, value in relevant_prefs.items()
                    if not key.startswith("music.")
                }

        facts = []
        compact_payload = {"entries": [], "text": ""}
        if not knowledge_only and not local_ha:
            compact_payload = self.select_compact_memory(user_message, max_items=8, max_chars=900)
            facts = self.long.search(user_message, limit=5) if user_message else self.long.all()[-3:]
            # Prefer compact typed hits when present; keep long-term texts as fallback.
            compact_facts = [
                item
                for item in compact_payload.get("entries") or []
                if str(item.get("type") or "") != MemoryType.PREFERENCE.value
            ]
            if compact_facts:
                facts = [
                    {"text": item.get("text") or item.get("value") or ""}
                    for item in compact_facts
                    if item.get("text") or item.get("value")
                ]
        entities = {
            key: entity.to_dict()
            for key, entity in self.short.entities.items()
            if key in ("application", "app", "device", "website")
        }
        if knowledge_only:
            entities = {}
        if local_ha:
            entities = {
                key: value
                for key, value in entities.items()
                if key == "device"
            }
        turns = self.short.turns[-6:]
        focus = dict(getattr(self.short, "focus", {}) or {})
        if local_ha:
            focus = {}

        blocks = []
        if focus.get("current_topic") or focus.get("last_answer_summary"):
            lines = []
            if focus.get("current_topic"):
                lines.append(f"topic: {focus['current_topic']}")
            if focus.get("last_question_summary"):
                lines.append(f"last_question: {focus['last_question_summary']}")
            if focus.get("last_answer_summary"):
                lines.append(f"last_answer: {focus['last_answer_summary']}")
            blocks.append("CONVERSATION FOCUS:\n" + "\n".join(lines))
        if turns and not local_ha:
            lines = [f"{item['role']}: {item['content']}" for item in turns]
            blocks.append("CURRENT SESSION:\n" + "\n".join(lines))
        device_id = ""
        device_ent = entities.get("device") or {}
        if isinstance(device_ent, dict):
            device_id = str(device_ent.get("id") or device_ent.get("entity_id") or device_ent.get("name") or "")
        if local_ha:
            print(f"[CONTEXT]\ndevice={device_id or None}\nwebsite=None")
            if entities:
                lines = [f"{key}: {value.get('name')}" for key, value in entities.items()]
                blocks.append("ACTIVE ENTITIES:\n" + "\n".join(lines))
        elif entities:
            lines = [f"{key}: {value.get('name')}" for key, value in entities.items()]
            blocks.append("ACTIVE ENTITIES:\n" + "\n".join(lines))
            print("[CONTEXT]\n" + "\n".join(lines))
        if relevant_prefs:
            lines = [f"{key}: {value}" for key, value in relevant_prefs.items()]
            blocks.append(
                "PREFERENCES (read-only action defaults — do NOT call set_preference "
                "unless the user explicitly asked to remember/change a preference):\n"
                + "\n".join(lines)
                + "\n"
                "(music.default_app means: when playing music without an app, use that app.)"
            )
            print("[MEMORY]\nRelevant preferences (read-only):\n" + "\n".join(lines))
        compact_text = compact_payload.get("text") or ""
        if compact_text and compact_text not in "\n\n".join(blocks):
            # Compact typed slice only — never the whole memory file.
            blocks.append("MEMORY (compact, current goal):\n" + compact_text)
        if facts:
            lines = [fact.get("text", "") for fact in facts if fact.get("text")]
            # Avoid duplicating compact lines in RELEVANT FACTS.
            if not compact_text:
                blocks.append("RELEVANT FACTS:\n" + "\n".join(f"- {line}" for line in lines))

        text = "\n\n".join(blocks).strip()
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "…"

        return {
            "text": text,
            "preferences": relevant_prefs,
            "facts": [fact.get("text") for fact in facts],
            "entities": entities,
            "turns": turns,
            "observations": list(self.short.recent_observations),
            "focus": focus,
            "compact": compact_payload.get("entries") or [],
        }

    def parse_explicit_memory_command(self, text):
        """
        Fast recognition of obvious memory intents.
        Returns (kind, payload) or (None, None).
        kind: remember | prefer | forget | recall
        """
        normalized = clean_text(text, 500).lower()
        if not normalized:
            return None, None

        # Sensitive refuse early for remember/prefer.
        if looks_sensitive(normalized) and any(
            normalized.startswith(prefix)
            for prefix in ("zapamiętaj", "zapamietaj", "pamiętaj", "pamietaj", "od teraz", "zawsze")
        ):
            return "refuse_sensitive", {}

        patterns_prefer = (
            r"^zawsze\s+u[zż]ywaj\s+(.+?)\s+do\s+muzyki$",
            r"^od teraz\s+u[zż]ywaj\s+(.+?)(?:\s+do\s+muzyki)?$",
            r"^(?:zawsze|od teraz)\s+(?:u[zż]ywaj|otwieraj|uzywaj)\s+(?:mi\s+)?(.+)$",
            r"^wol[eę]\s+(.+)$",
            r"^(?:moja ulubiona aplikacja do muzyki to|ulubiona aplikacja do muzyki to)\s+(.+)$",
        )
        if re.search(
            r"wol[eę].{0,48}rano.{0,40}(?:kr[oó]tk|zwi[eę][zź]l|mniej gad)",
            normalized,
        ):
            return "prefer", {"key": "voice.morning_brief", "value": "short"}
        for pattern in patterns_prefer:
            match = re.search(pattern, normalized, re.I)
            if match:
                value = clean_text(match.group(1), 80)
                value = re.sub(r"^(do muzyki|muzyki)\s+", "", value, flags=re.I).strip()
                value = re.sub(r"\s+do muzyki$", "", value, flags=re.I).strip(" .,!")
                key = "music.default_app"
                lower_value = value.lower()
                if "youtube" in lower_value:
                    value = "YouTube Music"
                elif "spotify" in lower_value:
                    value = "Spotify"
                if "przeglądar" in normalized or "przegladar" in normalized or "chrome" in lower_value:
                    key = "browser.default"
                    if "chrome" in lower_value:
                        value = "Chrome"
                if value:
                    return "prefer", {"key": key, "value": value}

        if normalized.startswith(("zapamiętaj że ", "zapamietaj że ", "zapamiętaj ze ", "zapamietaj ze ",
                                  "pamiętaj że ", "pamietaj że ", "pamiętaj ze ", "pamietaj ze ",
                                  "zapamiętaj, że ", "zapamietaj, że ")):
            for prefix in (
                "zapamiętaj, że ", "zapamiętaj że ", "zapamietaj, że ", "zapamietaj że ",
                "zapamiętaj ze ", "zapamietaj ze ",
                "pamiętaj, że ", "pamiętaj że ", "pamietaj, że ", "pamietaj że ",
                "pamiętaj ze ", "pamietaj ze ",
            ):
                if normalized.startswith(prefix):
                    fact = clean_text(text[len(prefix):], 500)
                    return "remember", {"text": fact}

        if normalized.startswith("zapamiętaj ") or normalized.startswith("zapamietaj "):
            fact = clean_text(text.split(" ", 1)[1], 500)
            if fact.startswith(("że ", "ze ")):
                fact = fact[3:].strip()
            return "remember", {"text": fact}

        forget_prefixes = (
            "zapomnij że ", "zapomnij ze ", "zapomnij, że ",
            "zapomnij o ", "zapomnij ", "usuń informację ", "usun informacje ",
            "nie pamiętaj ", "nie pamietaj ",
        )
        for prefix in forget_prefixes:
            if normalized.startswith(prefix):
                return "forget", {"query": clean_text(text[len(prefix):], 200)}

        if normalized.startswith(("jaka jest moja ", "jaki jest mój ", "jaki jest moj ",
                                  "co pamiętasz", "co pamietasz", "co wiesz o ")):
            return "recall", {"query": clean_text(text, 200)}

        return None, None


_default_manager: MemoryManager | None = None


def get_memory_manager(path=None) -> MemoryManager:
    global _default_manager
    if path is not None:
        return MemoryManager(path=path)
    if _default_manager is None:
        _default_manager = MemoryManager()
    return _default_manager


def reset_memory_manager(path=None):
    global _default_manager
    if path is not None:
        _default_manager = MemoryManager(path=path)
    else:
        _default_manager = None
    return _default_manager
