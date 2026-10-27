# ==========================
# ELIAN — HA mutation verification (C.5.5)
# ==========================
"""COMMAND ACCEPTED is not SUCCESS. Read before → maybe skip → service → after."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from Diagnostics import log_turn
from core.device_identity import identity_from_entity
from core.ha_speech import ha_user_message


SUCCESS = "SUCCESS"
ALREADY_SATISFIED = "ALREADY_SATISFIED"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"
UNAVAILABLE = "UNAVAILABLE"

_ON_STATES = frozenset(
    {"on", "open", "playing", "cool", "heat", "auto", "heat_cool", "dry", "fan_only"}
)
_OFF_STATES = frozenset({"off", "closed", "idle", "paused"})

# entity_id+action → last unverified command (do not blind retry)
_pending_unverified: Dict[str, float] = {}
_LAST: Optional["HaVerifyResult"] = None


@dataclass
class HaVerifyResult:
    outcome: str
    entity_id: str = ""
    before: str = ""
    requested: str = ""
    service_status: str = ""
    after: str = ""
    verification: str = ""
    latency_ms: float = 0.0
    mutated: bool = False
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def log(self) -> None:
        print(
            f"[HA VERIFY] entity={self.entity_id} before={self.before} "
            f"requested={self.requested} service_status={self.service_status} "
            f"after={self.after} verification={self.verification} "
            f"latency_ms={self.latency_ms:.0f}"
        )
        log_turn(
            "ha_verify",
            entity=self.entity_id,
            before=self.before,
            requested=self.requested,
            service_status=self.service_status,
            after=self.after,
            verification=self.verification,
            latency_ms=round(self.latency_ms, 1),
        )


def last_verify_result() -> Optional[HaVerifyResult]:
    return _LAST


def take_last_verify() -> Optional[HaVerifyResult]:
    global _LAST
    result = _LAST
    _LAST = None
    return result


def remember_verify(result: Optional[HaVerifyResult]) -> Optional[HaVerifyResult]:
    global _LAST
    _LAST = result
    return result


def _timeout_s() -> float:
    try:
        from Config import get_config_value

        return float(get_config_value("smart_home", "verify_timeout_seconds", default=6.5) or 6.5)
    except Exception:
        return 6.5


def _poll_s() -> float:
    try:
        from Config import get_config_value

        return float(get_config_value("smart_home", "verify_poll_seconds", default=0.15) or 0.15)
    except Exception:
        return 0.15


def _fixture_mode() -> bool:
    try:
        from HomeAssistant import get_registry

        return bool(getattr(get_registry(), "_fixture_mode", False))
    except Exception:
        return False


def _headers_available() -> bool:
    try:
        from HomeAssistant import get_headers

        return get_headers() is not None
    except Exception:
        return False


def read_entity_state(entity_id: str):
    """Fresh state when live API allows; otherwise registry snapshot."""
    eid = str(entity_id or "").strip()
    if not eid:
        return None
    if not _fixture_mode() and _headers_available():
        try:
            from HomeAssistant import api_request, entity_from_state

            response = api_request("GET", f"/api/states/{eid}")
            if response is not None and 200 <= response.status_code < 300:
                payload = response.json()
                if isinstance(payload, dict):
                    entity = entity_from_state(payload)
                    if entity is not None:
                        return entity
        except Exception:
            pass
    try:
        from HomeAssistant import get_registry

        return get_registry().get(eid)
    except Exception:
        return None


def _norm_state(entity) -> str:
    if entity is None:
        return ""
    return str(getattr(entity, "state", "") or "").strip().lower()


def _desired_on(action: str) -> Optional[bool]:
    if action in ("device_on", "light_on", "turn_on"):
        return True
    if action in ("device_off", "light_off", "turn_off"):
        return False
    return None


def _matches_power(state: str, want_on: bool) -> bool:
    if want_on:
        return state in _ON_STATES
    return state in _OFF_STATES


def _brightness_pct(entity) -> Optional[int]:
    if entity is None:
        return None
    attrs = getattr(entity, "attributes", None) or {}
    raw = attrs.get("brightness")
    if raw is None:
        return None
    try:
        return int(round(int(raw) * 100 / 255))
    except Exception:
        return None


def _pending_key(entity_id: str, action: str, requested: str = "") -> str:
    base = f"{entity_id}|{action}"
    req = str(requested or "").strip()
    return f"{base}|{req}" if req else base


def clear_unverified(entity_id: str = "", action: str = "") -> None:
    if entity_id and action:
        prefix = f"{entity_id}|{action}"
        for key in list(_pending_unverified):
            if key == prefix or key.startswith(prefix + "|"):
                _pending_unverified.pop(key, None)
        return
    _pending_unverified.clear()


def _live_readback() -> bool:
    return (not _fixture_mode()) and _headers_available()


def _mutate_local(entity, action: str, value, extra: str = "") -> None:
    """Offline / fixture HA: apply the requested postcondition locally."""
    if entity is None:
        return
    want_on = _desired_on(action)
    attrs = getattr(entity, "attributes", None)
    if not isinstance(attrs, dict):
        attrs = {}
        try:
            entity.attributes = attrs
        except Exception:
            pass
    if want_on is True:
        entity.state = "on"
    elif want_on is False:
        entity.state = "off"
    if action == "light_brightness" and value is not None:
        pct = max(0, min(100, int(value)))
        entity.state = "off" if pct <= 0 else "on"
        if pct > 0:
            attrs["brightness"] = max(1, min(255, int(round(pct * 255 / 100))))
        else:
            attrs["brightness"] = 0
    if action == "adjust_brightness":
        current = _brightness_pct(entity)
        if current is None:
            current = 50 if _matches_power(_norm_state(entity), True) else 0
        nxt = max(0, min(100, int(current) + int(value or 0)))
        entity.state = "off" if nxt <= 0 else "on"
        if nxt > 0:
            attrs["brightness"] = max(1, min(255, int(round(nxt * 255 / 100))))
    if action == "light_color":
        entity.state = "on"
        try:
            from HaActions import COLORS

            rgb = COLORS.get(str(extra or value or "").strip().lower())
            if rgb:
                attrs["rgb_color"] = list(rgb)
        except Exception:
            pass


def execute_verified(
    action: str,
    entity,
    *,
    value=None,
    extra: str = "",
    utterance: str = "",
    call_service: Optional[Callable[..., bool]] = None,
    read_state: Optional[Callable[[str], Any]] = None,
    apply_service: Optional[Callable[..., Tuple[bool, str]]] = None,
    timeout_s: Optional[float] = None,
) -> HaVerifyResult:
    """Mutation with before/after proof. Does not invent success."""
    from HaActions import _call_service, _remember

    t0 = time.perf_counter()
    eid = str(getattr(entity, "entity_id", "") or "")
    ident = identity_from_entity(entity, utterance=utterance)
    reader = read_state or read_entity_state
    before_ent = reader(eid) or entity
    before = _norm_state(before_ent)

    def _finish(result: HaVerifyResult) -> HaVerifyResult:
        result.data = _bundle(ident, action, result, value, extra)
        if not result.message:
            result.message = ha_user_message(
                outcome=result.outcome,
                action=action,
                data=result.data,
                utterance=utterance,
            )
        result.log()
        remember_verify(result)
        return result

    if before_ent is None or before in ("unavailable", "unknown"):
        return _finish(
            HaVerifyResult(
                outcome=UNAVAILABLE,
                entity_id=eid,
                before=before or "unavailable",
                requested=action,
                service_status="skipped",
                after=before or "unavailable",
                verification=UNAVAILABLE,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                mutated=False,
                message=ha_user_message(
                    outcome=UNAVAILABLE,
                    action=action,
                    data={"spoken_name": ident.spoken_name},
                    utterance=utterance,
                ),
            )
        )

    want_on = _desired_on(action)
    requested_state = action
    if want_on is True:
        requested_state = "on"
        if _matches_power(before, True):
            return _finish(_already(ident, action, eid, before, t0, "on", utterance))
    elif want_on is False:
        requested_state = "off"
        if _matches_power(before, False):
            return _finish(_already(ident, action, eid, before, t0, "off", utterance))
    elif action == "light_brightness" and value is not None:
        requested_state = str(int(value))
        got = _brightness_pct(before_ent)
        if got is not None and abs(int(got) - int(value)) <= 8:
            if int(value) <= 0 and _matches_power(before, False):
                return _finish(_already(ident, action, eid, before, t0, requested_state, utterance))
            if int(value) > 0 and _matches_power(before, True):
                return _finish(_already(ident, action, eid, before, t0, requested_state, utterance))

    key = _pending_key(eid, action, requested_state)
    if key in _pending_unverified:
        after_ent = reader(eid) or before_ent
        after = _norm_state(after_ent)
        if want_on is not None and _matches_power(after, want_on):
            _pending_unverified.pop(key, None)
            return _finish(
                _success(
                    ident,
                    action,
                    eid,
                    before,
                    after,
                    t0,
                    mutated=False,
                    value=value,
                    extra=extra,
                    utterance=utterance,
                )
            )
        return _finish(
            HaVerifyResult(
                outcome=UNKNOWN,
                entity_id=eid,
                before=before,
                requested=requested_state,
                service_status="skipped_unverified_retry",
                after=after,
                verification=UNKNOWN,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                mutated=False,
                message=ha_user_message(
                    outcome=UNKNOWN,
                    action=action,
                    data={"spoken_name": ident.spoken_name},
                    utterance=utterance,
                ),
            )
        )

    service_ok = False
    if apply_service is not None:
        service_ok, _service_name = apply_service(action, entity, value, extra)
    else:
        service_ok, _service_name = _dispatch_service(
            action, entity, value, extra, call_service or _call_service
        )

    service_status = "accepted" if service_ok else "failed"
    if not service_ok:
        return _finish(
            HaVerifyResult(
                outcome=FAILED,
                entity_id=eid,
                before=before,
                requested=requested_state,
                service_status="failed",
                after=before,
                verification=FAILED,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                mutated=False,
                message=ha_user_message(
                    outcome=FAILED,
                    action=action,
                    data={"spoken_name": ident.spoken_name},
                    utterance=utterance,
                ),
            )
        )

    if not _live_readback() and read_state is None:
        _mutate_local(entity, action, value, extra)
        try:
            from HomeAssistant import get_registry

            live_ent = get_registry().get(eid)
            if live_ent is not None and live_ent is not entity:
                _mutate_local(live_ent, action, value, extra)
        except Exception:
            pass
        after_ent = reader(eid) or entity
        after = _norm_state(after_ent)
        latency = (time.perf_counter() - t0) * 1000.0
        if _postcondition_ok(action, want_on, after, after_ent, value):
            _pending_unverified.pop(key, None)
            try:
                _remember(after_ent or entity, action, value)
            except Exception:
                pass
            return _finish(
                _success(
                    ident,
                    action,
                    eid,
                    before,
                    after,
                    t0,
                    mutated=True,
                    value=value,
                    extra=extra,
                    latency=latency,
                    utterance=utterance,
                )
            )

    after_ent, after = _wait_after(
        reader,
        eid,
        entity if read_state is None else before_ent,
        action,
        want_on,
        value,
        timeout_s=timeout_s,
    )
    latency = (time.perf_counter() - t0) * 1000.0
    verified = _postcondition_ok(action, want_on, after, after_ent, value)

    if verified:
        _pending_unverified.pop(key, None)
        try:
            _remember(after_ent or entity, action, value)
        except Exception:
            pass
        return _finish(
            _success(
                ident,
                action,
                eid,
                before,
                after,
                t0,
                mutated=True,
                value=value,
                extra=extra,
                latency=latency,
                utterance=utterance,
            )
        )

    _pending_unverified[key] = time.time()
    return _finish(
        HaVerifyResult(
            outcome=UNKNOWN,
            entity_id=eid,
            before=before,
            requested=requested_state,
            service_status=service_status,
            after=after or "unread",
            verification=UNKNOWN,
            latency_ms=latency,
            mutated=True,
            message=ha_user_message(
                outcome=UNKNOWN,
                action=action,
                data={"spoken_name": ident.spoken_name},
                utterance=utterance,
            ),
        )
    )


def _postcondition_ok(action, want_on, after, after_ent, value) -> bool:
    if want_on is not None:
        return _matches_power(after, want_on)
    if action == "light_brightness" and value is not None:
        if int(value) <= 0:
            return _matches_power(after, False)
        got = _brightness_pct(after_ent)
        if got is None:
            return _matches_power(after, True)
        return abs(int(got) - int(value)) <= 8
    if action == "adjust_brightness":
        return after in _ON_STATES or _brightness_pct(after_ent) is not None
    if action in ("light_color", "light_temperature"):
        return _matches_power(after, True)
    return after != "" and after not in ("unavailable", "unknown")


def _already(ident, action, eid, before, t0, requested, utterance) -> HaVerifyResult:
    return HaVerifyResult(
        outcome=ALREADY_SATISFIED,
        entity_id=eid,
        before=before,
        requested=requested,
        service_status="skipped",
        after=before,
        verification=ALREADY_SATISFIED,
        latency_ms=(time.perf_counter() - t0) * 1000.0,
        mutated=False,
        message=ha_user_message(
            outcome=ALREADY_SATISFIED,
            action=action,
            data={"spoken_name": ident.spoken_name},
            utterance=utterance,
        ),
    )


def _success(
    ident,
    action,
    eid,
    before,
    after,
    t0,
    *,
    mutated,
    value=None,
    extra="",
    latency=None,
    utterance="",
) -> HaVerifyResult:
    payload = {"spoken_name": ident.spoken_name}
    if action in ("light_brightness", "adjust_brightness"):
        payload["requested_brightness"] = value
    if action == "light_color":
        payload["color_name"] = str(extra or value or "").strip()
    return HaVerifyResult(
        outcome=SUCCESS,
        entity_id=eid,
        before=before,
        requested=after,
        service_status="accepted" if mutated else "skipped",
        after=after,
        verification=SUCCESS,
        latency_ms=latency if latency is not None else (time.perf_counter() - t0) * 1000.0,
        mutated=mutated,
        message=ha_user_message(
            outcome=SUCCESS,
            action=action,
            data=payload,
            utterance=utterance,
        ),
    )


def _bundle(ident, action, result: HaVerifyResult, value, extra: str = "") -> Dict[str, Any]:
    data = ident.to_dict()
    data.update(
        {
            "entity_id": result.entity_id,
            "resolved_entity_id": result.entity_id,
            "ha_action": action,
            "ha_outcome": result.outcome,
            "verification": result.verification,
            "before": result.before,
            "after": result.after,
            "service_status": result.service_status,
            "mutated": result.mutated,
            "requested_brightness": value
            if action in ("light_brightness", "adjust_brightness")
            else None,
            "color_name": str(extra or "").strip() if action == "light_color" else "",
        }
    )
    return data


def _wait_after(reader, eid, before_ent, action, want_on, value, timeout_s=None):
    limit = float(timeout_s if timeout_s is not None else _timeout_s())
    deadline = time.perf_counter() + max(0.0, limit)
    last = before_ent
    last_state = _norm_state(before_ent)
    poll = max(0.02, _poll_s())
    first = True
    while first or time.perf_counter() < deadline:
        first = False
        ent = reader(eid)
        if ent is not None:
            last = ent
            last_state = _norm_state(ent)
            if _postcondition_ok(action, want_on, last_state, last, value):
                return last, last_state
        if time.perf_counter() >= deadline:
            break
        time.sleep(min(poll, max(0.0, deadline - time.perf_counter())))
    return last, last_state


def _dispatch_service(action, entity, value, extra, call_service) -> Tuple[bool, str]:
    domain = entity.domain
    eid = entity.entity_id
    if action in ("device_on", "light_on", "turn_on"):
        service = "turn_on"
        if domain == "cover":
            service = "open_cover"
        if domain == "media_player":
            service = "media_play"
        return bool(call_service(domain, service, eid)), service
    if action in ("device_off", "light_off", "turn_off"):
        service = "turn_off"
        if domain == "cover":
            service = "close_cover"
        if domain == "media_player":
            service = "media_stop"
        return bool(call_service(domain, service, eid)), service
    if action == "light_brightness":
        brightness = max(0, min(100, int(value or 0)))
        if brightness <= 0:
            return bool(call_service(domain, "turn_off", eid)), "turn_off"
        return bool(
            call_service(domain, "turn_on", eid, {"brightness_pct": brightness})
        ), "turn_on"
    if action == "adjust_brightness":
        from HaActions import _current_brightness

        current = _current_brightness(entity)
        nxt = max(0, min(100, int(current) + int(value or 0)))
        if nxt <= 0:
            return bool(call_service(domain, "turn_off", eid)), "turn_off"
        return bool(
            call_service(domain, "turn_on", eid, {"brightness_pct": nxt})
        ), "turn_on"
    if action == "light_color":
        from HaActions import COLORS

        rgb = COLORS.get(str(extra or value or "").strip().lower())
        if not rgb:
            return False, ""
        return bool(call_service("light", "turn_on", eid, {"rgb_color": rgb})), "turn_on"
    if action == "light_temperature":
        from HaActions import _clamp_kelvin, _color_temperature_map

        phrase = str(extra or "").strip().lower()
        mapping = _color_temperature_map()
        kelvin = mapping.get(phrase)
        if kelvin is None:
            try:
                kelvin = int(value or 0)
            except (TypeError, ValueError):
                kelvin = 0
        if not kelvin:
            return False, ""
        kelvin = _clamp_kelvin(entity, kelvin)
        return bool(
            call_service("light", "turn_on", eid, {"color_temp_kelvin": kelvin})
        ), "turn_on"
    return False, ""
