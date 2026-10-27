import time

from Config import get_config_value


_state = {}
_expires_at = 0.0
_waiting_until = 0.0
_pending_goal = None


def _ttl():
    return float(
        get_config_value("settings", "context_ttl", default=180)
    )


def clear():
    global _state, _expires_at, _waiting_until, _pending_goal
    _state = {}
    _expires_at = 0.0
    _waiting_until = 0.0
    _pending_goal = None


def update(**fields):
    global _state, _expires_at
    cleaned = {
        key: value
        for key, value in fields.items()
        if value not in (None, "")
    }
    if not cleaned:
        return
    current = get()
    current.update(cleaned)

    domain = cleaned.get("last_domain") or current.get("last_domain")
    entity_id = cleaned.get("last_device") or current.get("last_device")
    name = cleaned.get("last_device_name") or current.get("last_device_name")
    if domain == "climate" and entity_id:
        current["last_climate"] = entity_id
        if name:
            current["last_climate_name"] = name
    if domain == "light" and entity_id:
        current["last_light"] = entity_id
        if name:
            current["last_light_name"] = name
    if cleaned.get("last_application"):
        current["last_app"] = cleaned["last_application"]

    _state = current
    _expires_at = time.monotonic() + _ttl()


def get():
    if not _state:
        return {}
    if time.monotonic() >= _expires_at:
        clear()
        return {}
    return dict(_state)


def last(key, default=None):
    return get().get(key, default)


def touch():
    """Refresh TTL without changing values."""
    global _expires_at
    if _state or _pending_goal:
        _expires_at = time.monotonic() + _ttl()


def extend_wait(seconds=None):
    """Keep conversation active while the user needs time to think."""
    global _waiting_until
    if seconds is None:
        seconds = float(
            get_config_value(
                "settings",
                "conversation_wait_extension",
                default=60,
            )
        )
    _waiting_until = time.monotonic() + max(5.0, float(seconds))
    touch()


def waiting_seconds_left():
    remaining = _waiting_until - time.monotonic()
    return max(0.0, remaining)


def clear_wait():
    global _waiting_until
    _waiting_until = 0.0


def set_pending_goal(goal):
    global _pending_goal
    _pending_goal = dict(goal) if goal else None
    touch()


def get_pending_goal():
    if _pending_goal is None:
        return None
    if not get() and not _pending_goal:
        return None
    return dict(_pending_goal)


def clear_pending_goal():
    global _pending_goal
    _pending_goal = None
