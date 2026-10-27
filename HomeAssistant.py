import os
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field

import requests

from Config import config, get_secret


SUPPORTED_DOMAINS = (
    "light",
    "climate",
    "switch",
    "fan",
    "media_player",
    "cover",
    "sensor",
    "binary_sensor",
    "scene",
    "select",
    "number",
    "button",
)

CONTROLLABLE_DOMAINS = (
    "light",
    "climate",
    "switch",
    "fan",
    "media_player",
    "cover",
)


SMART_HOME_CONFIG = config.get("smart_home", {})


def _ha_url():
    return str(
        os.getenv("ELIAN_HA_URL", "").strip()
        or get_secret("home_assistant_url")
        or SMART_HOME_CONFIG.get("url", "http://homeassistant.local:8123")
    ).rstrip("/")


def get_headers():
    token = get_secret(
        "home_assistant_token",
        env_var="ELIAN_HA_TOKEN",
    )
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def api_request(method, path, *, timeout=None, **kwargs):
    headers = get_headers()
    if headers is None:
        return None
    # Default 5s; registry / hot-path callers may pass a tighter budget.
    if timeout is None:
        try:
            timeout = float(SMART_HOME_CONFIG.get("request_timeout", 5) or 5)
        except Exception:
            timeout = 5.0
    try:
        return requests.request(
            method,
            f"{_ha_url()}{path}",
            headers=headers,
            timeout=float(timeout),
            **kwargs,
        )
    except requests.RequestException as error:
        print("Błąd połączenia z Home Assistant:", error)
        return None


def test_connection():
    response = api_request("GET", "/api/")
    return bool(response is not None and 200 <= response.status_code < 300)


@dataclass
class HaEntity:
    entity_id: str
    domain: str
    object_id: str
    friendly_name: str
    state: str = ""
    attributes: dict = field(default_factory=dict)
    aliases: tuple[str, ...] = ()
    supported_features: int = 0
    color_modes: tuple[str, ...] = ()
    hvac_modes: tuple[str, ...] = ()

    def display_name(self):
        return self.friendly_name or self.object_id.replace("_", " ")


def _aliases_from_state(state):
    attributes = state.get("attributes", {}) or {}
    values = []
    friendly = attributes.get("friendly_name")
    if isinstance(friendly, str) and friendly.strip():
        values.append(friendly)
    extra = attributes.get("aliases") or attributes.get("friendly_name_aliases")
    if isinstance(extra, str):
        values.append(extra)
    elif isinstance(extra, list):
        values.extend(str(item) for item in extra if item)
    entity_id = state.get("entity_id", "")
    if "." in entity_id:
        values.append(entity_id.split(".", 1)[1].replace("_", " "))
    unique = []
    seen = set()
    for value in values:
        text = str(value).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            unique.append(text)
    return tuple(unique)


def entity_from_state(state):
    entity_id = str(state.get("entity_id", "")).strip()
    if "." not in entity_id:
        return None
    domain, object_id = entity_id.split(".", 1)
    if domain not in SUPPORTED_DOMAINS:
        return None
    attributes = state.get("attributes", {}) or {}
    if not isinstance(attributes, dict):
        attributes = {}
    color_modes = attributes.get("supported_color_modes") or []
    hvac_modes = attributes.get("hvac_modes") or []
    return HaEntity(
        entity_id=entity_id,
        domain=domain,
        object_id=object_id,
        friendly_name=str(attributes.get("friendly_name") or object_id).strip(),
        state=str(state.get("state") or ""),
        attributes=deepcopy(attributes),
        aliases=_aliases_from_state(state),
        supported_features=int(attributes.get("supported_features") or 0),
        color_modes=tuple(str(item) for item in color_modes),
        hvac_modes=tuple(str(item) for item in hvac_modes),
    )


class HomeAssistantDeviceRegistry:
    def __init__(self, states_loader=None):
        self._states_loader = states_loader or self._load_states
        self._entities = []
        self._lock = threading.RLock()
        self._updated_at = 0.0
        self._fixture_mode = False
        self._bg_refresh_pending = False
        self._last_refresh_fail_at = 0.0
        self._refresh_fail_cooldown_s = 30.0

    def _load_states(self):
        """Return list of states, or None when the HA transport/API call failed.

        None must not wipe a previous registry (false-empty / timeout).
        An empty list means HA answered successfully with zero entities.
        """
        try:
            states_timeout = float(
                SMART_HOME_CONFIG.get("states_timeout", 2.5) or 2.5
            )
        except Exception:
            states_timeout = 2.5
        response = api_request("GET", "/api/states", timeout=states_timeout)
        if response is None or response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, list):
            return None
        return payload

    def refresh(self):
        raw = self._states_loader()
        # Transport / API failure: keep previous entities (incl. fixtures).
        if raw is None:
            with self._lock:
                n = len(self._entities)
                self._last_refresh_fail_at = time.monotonic()
            print(f"[elian] ha_registry: refresh_failed keep_previous entities={n}")
            return self.all()
        entities = []
        for state in raw:
            if not isinstance(state, dict):
                continue
            entity = entity_from_state(state)
            if entity is not None:
                entities.append(entity)
        with self._lock:
            self._entities = entities
            self._updated_at = time.monotonic()
            self._fixture_mode = False
            self._last_refresh_fail_at = 0.0
        print(f"[elian] ha_registry: entities={len(entities)}")
        return list(entities)

    def _schedule_background_refresh(self) -> None:
        """Kick one in-flight background refresh (voice path must not block)."""
        with self._lock:
            if getattr(self, "_bg_refresh_pending", False):
                return
            self._bg_refresh_pending = True

        def _worker() -> None:
            try:
                self.refresh()
            except Exception as error:
                print("Błąd tła rejestru Home Assistant:", error)
            finally:
                with self._lock:
                    self._bg_refresh_pending = False

        threading.Thread(
            target=_worker, name="HaRegistryHotRefresh", daemon=True
        ).start()

    def maybe_refresh(self, force=False):
        """Return entities; never stall the voice turn on a stale refresh.

        ``force=True`` (startup) still refreshes synchronously.
        Stale hot-path calls return cache immediately and refresh in background.
        Empty never-loaded registry does one short sync attempt so first resolve works.
        """
        interval = float(SMART_HOME_CONFIG.get("refresh_interval", 60))
        with self._lock:
            fixture = bool(self._fixture_mode)
            has_entities = bool(self._entities)
            never_loaded = self._updated_at <= 0.0 and not has_entities
            stale = (
                not has_entities
                or time.monotonic() - self._updated_at >= interval
            )
        # Fixture worlds (tests / text_inject Fake HA) never hit live HA unless
        # ELIAN_HA_LIVE_REFRESH=1 is set intentionally.
        if fixture and not force:
            return self.all()
        if fixture and force and not str(
            __import__("os").environ.get("ELIAN_HA_LIVE_REFRESH", "")
        ).strip().lower() in ("1", "true", "yes"):
            return self.all()
        if force:
            try:
                self.refresh()
            except Exception as error:
                print("Błąd odświeżania rejestru Home Assistant:", error)
            return self.all()
        if stale:
            with self._lock:
                fail_age = time.monotonic() - float(self._last_refresh_fail_at or 0.0)
                in_fail_cooldown = (
                    self._last_refresh_fail_at > 0.0
                    and fail_age < float(self._refresh_fail_cooldown_s)
                )
            if in_fail_cooldown:
                # HA just timed out — do not re-block or thrash every phrase.
                return self.all()
            elif never_loaded:
                # First resolve with empty cache — one short sync try, then cache.
                try:
                    self.refresh()
                except Exception as error:
                    print("Błąd odświeżania rejestru Home Assistant:", error)
            else:
                self._schedule_background_refresh()
        return self.all()

    def all(self, domains=None):
        with self._lock:
            entities = list(self._entities)
        if domains is None:
            return entities
        allowed = {domains} if isinstance(domains, str) else set(domains)
        return [entity for entity in entities if entity.domain in allowed]

    def get(self, entity_id):
        entity_id = str(entity_id or "").strip()
        for entity in self.all():
            if entity.entity_id == entity_id:
                return entity
        return None

    def load_states(self, states):
        entities = []
        for state in states:
            entity = entity_from_state(state)
            if entity is not None:
                entities.append(entity)
        with self._lock:
            self._entities = entities
            self._updated_at = time.monotonic()
            self._fixture_mode = True
        return list(entities)


_REGISTRY = HomeAssistantDeviceRegistry()
_REFRESH_THREAD = None
_REFRESH_LOCK = threading.Lock()


def get_registry():
    return _REGISTRY


def refresh_registry(force=False):
    return _REGISTRY.maybe_refresh(force=force)


def start_registry():
    global _REFRESH_THREAD
    if not SMART_HOME_CONFIG.get("enabled", False):
        return
    if SMART_HOME_CONFIG.get("system", "home_assistant") != "home_assistant":
        return
    if get_headers() is None:
        print("Rejestr Home Assistant: brak tokenu.")
        return
    refresh_registry(force=True)
    with _REFRESH_LOCK:
        if _REFRESH_THREAD is not None and _REFRESH_THREAD.is_alive():
            return
        _REFRESH_THREAD = threading.Thread(
            target=_refresh_loop,
            name="HaRegistry",
            daemon=True,
        )
        _REFRESH_THREAD.start()


def _refresh_loop():
    interval = max(15, float(SMART_HOME_CONFIG.get("refresh_interval", 60)))
    while True:
        time.sleep(interval)
        try:
            _REGISTRY.refresh()
        except Exception as error:
            print("Błąd tła rejestru Home Assistant:", error)
