_SECRET_KEYS = (
    "token",
    "password",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "bearer",
)


def _is_secret_key(key):
    lowered = str(key).lower()
    return any(part in lowered for part in _SECRET_KEYS)


def _safe_value(value):
    text = str(value)
    if len(text) > 240:
        return text[:237] + "..."
    return text


def log_turn(stage, **fields):
    parts = []
    for key, value in fields.items():
        if value in (None, "", [], {}):
            continue
        if _is_secret_key(key):
            continue
        parts.append(f"{key}={_safe_value(value)}")

    suffix = ", ".join(parts)
    if suffix:
        print(f"[elian] {stage}: {suffix}")
    else:
        print(f"[elian] {stage}")


def summarize_intent(intent):
    if intent is None:
        return ""
    parts = [
        getattr(intent, "action", ""),
        getattr(intent, "target", "") or getattr(intent, "argument", ""),
        getattr(intent, "domain", ""),
    ]
    return " ".join(str(part) for part in parts if part)
