# ==========================
# ELIAN — SituationEngine (correlate → one situation)
# ==========================
"""Raw events + WorldState → correlated Situation candidates.

Feeds AttentionManager → ProactivePolicy. Does NOT replace ProactivePolicy.
Never emits three separate messages for three related facts.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Set

from core.events import (
    BATTERY_CHARGING,
    BATTERY_CRITICAL,
    BATTERY_LOW,
    CALENDAR_UPCOMING,
    CPU_HIGH_SUSTAINED,
    DISK_LOW,
    HA_UNAVAILABLE,
    LEAVING_HOME,
    Priority,
    REMINDER_DUE,
    Event,
)
from core.interesting_event import expected_value_of_interruption
from core.situation_types import (
    ASK,
    NOTIFY,
    SUGGEST,
    Observation,
    Situation,
    SituationConsiderResult,
    SituationType,
)

_LOCK = threading.RLock()
_ENGINE: Optional["SituationEngine"] = None

# Observation kinds
OBS_LAPTOP_BATTERY_LOW = "laptop_battery_low"
OBS_AC_DISCONNECTED = "ac_disconnected"
OBS_AC_CONNECTED = "ac_connected"
OBS_CALENDAR_SOON = "calendar_soon"
OBS_ACTIVE_USE = "active_laptop_use"
OBS_PHONE_BATTERY_LOW = "phone_battery_low"
OBS_PHONE_UNAVAILABLE = "phone_unavailable"
OBS_LEAVING = "leaving"
OBS_ARRIVING = "arriving"
OBS_PRESENCE_UNAVAILABLE = "presence_unavailable"
OBS_REMINDER = "reminder_due"
OBS_LIGHTS_ON = "lights_on"
OBS_HA_DOWN = "ha_unavailable"
OBS_HA_TOPIC = "ha_topic_active"
OBS_DISK_LOW = "disk_low"
OBS_DOWNLOAD_DONE = "download_complete"
OBS_CALENDAR_CONFLICT = "calendar_conflict"
OBS_DEADLINE = "deadline_approaching"
OBS_CPU_SUSTAINED = "cpu_sustained"
OBS_FULLSCREEN_GAME = "fullscreen_game"

_CORRELATION_WINDOW = 900.0  # 15 min
_DEFAULT_EXPIRY = 1800.0


def get_situation_engine() -> "SituationEngine":
    global _ENGINE
    with _LOCK:
        if _ENGINE is None:
            _ENGINE = SituationEngine()
        return _ENGINE


def reset_situation_engine_for_tests() -> None:
    global _ENGINE
    with _LOCK:
        if _ENGINE is not None:
            _ENGINE.reset()
        _ENGINE = SituationEngine()


class SituationEngine:
    """Situation Aggregator + correlator over a short observation window."""

    def __init__(self) -> None:
        self._observations: List[Observation] = []
        self._active: Dict[str, Situation] = {}
        self._presented_keys: Dict[str, float] = {}
        self._resolved_keys: Set[str] = set()
        self._clock = time.time

    def reset(self) -> None:
        self._observations.clear()
        self._active.clear()
        self._presented_keys.clear()
        self._resolved_keys.clear()

    def set_clock(self, clock_fn) -> None:
        self._clock = clock_fn

    def active_situations(self) -> List[Situation]:
        now = float(self._clock())
        return [
            s
            for s in self._active.values()
            if not s.is_expired(now) and not s.already_presented and not s.resolved
        ]

    def mark_presented(self, situation: Situation, *, now: float | None = None) -> None:
        stamp = float(now if now is not None else self._clock())
        situation.already_presented = True
        key = situation.mute_key or situation.type
        self._presented_keys[key] = stamp
        self._active[situation.id] = situation

    def resolve(self, situation_id: str = "", *, mute_key: str = "") -> None:
        if situation_id and situation_id in self._active:
            self._active[situation_id].resolved = True
        if mute_key:
            self._resolved_keys.add(mute_key)
            for sit in self._active.values():
                if sit.mute_key == mute_key or sit.type == mute_key:
                    sit.resolved = True

    def note_observation(self, obs: Observation) -> None:
        if not obs.timestamp:
            obs.timestamp = float(self._clock())
        self._observations.append(obs)
        self._prune(float(obs.timestamp))

    def consider(self, event: Event, host: Any = None) -> SituationConsiderResult:
        """Ingest one event; maybe emit a correlated situation, else legacy/silent."""
        now = float(getattr(getattr(host, "clock", None), "time", self._clock)())
        world = _snapshot_world()
        ev = expected_value_of_interruption(event, world=world)
        if ev <= 0.0 and event.priority is not Priority.CRITICAL:
            # Still record if it carries state we need later? Skip noise.
            if str(event.type) in (
                "heartbeat",
                "world_state_refresh",
                "WORLD_STATE_REFRESH",
                "cpu_sample",
                "SCREEN_FRAME_UPDATED",
            ):
                return SituationConsiderResult(route="silent", reason="noise")
            if str(event.type) == "ACTIVE_WINDOW_CHANGED":
                # Keep ProactiveManager contract: silent window churn = state_only.
                return SituationConsiderResult(route="silent", reason="state_only")

        obs_list = self._event_to_observations(event, world, now)
        for obs in obs_list:
            self.note_observation(obs)

        # Honest UNAVAILABLE — never invent phone/presence facts
        for obs in obs_list:
            if obs.kind == OBS_PHONE_UNAVAILABLE:
                return SituationConsiderResult(
                    route="silent", reason="phone_unavailable"
                )
            if obs.kind == OBS_PRESENCE_UNAVAILABLE:
                return SituationConsiderResult(
                    route="silent", reason="presence_unavailable"
                )

        # Resolution signals
        if event.type == BATTERY_CHARGING:
            self.resolve(mute_key=SituationType.DEVICE_MAY_NOT_LAST_FOR_UPCOMING_EVENT.value)
            self.resolve(mute_key=SituationType.BATTERY_LOW.value)
            self.resolve(mute_key=SituationType.PHONE_CHARGE_BEFORE_DEPARTURE.value)
            drop = {
                OBS_LAPTOP_BATTERY_LOW,
                OBS_PHONE_BATTERY_LOW,
                OBS_AC_DISCONNECTED,
                OBS_AC_CONNECTED,
            }
            self._observations = [o for o in self._observations if o.kind not in drop]
            # Allow future battery situations after charge
            self._resolved_keys.discard(
                SituationType.DEVICE_MAY_NOT_LAST_FOR_UPCOMING_EVENT.value
            )
            self._resolved_keys.discard(SituationType.BATTERY_LOW.value)
            self._resolved_keys.discard(
                SituationType.PHONE_CHARGE_BEFORE_DEPARTURE.value
            )
            self._presented_keys.pop(
                SituationType.DEVICE_MAY_NOT_LAST_FOR_UPCOMING_EVENT.value, None
            )
            self._presented_keys.pop(
                SituationType.PHONE_CHARGE_BEFORE_DEPARTURE.value, None
            )
            return SituationConsiderResult(route="legacy", reason="charging_resolve")

        situation = self._correlate(now, world, trigger=event)
        if situation is None:
            return SituationConsiderResult(route="legacy", reason="no_correlation")

        if situation.resolved or situation.is_expired(now):
            return SituationConsiderResult(route="silent", reason="stale_or_resolved")

        mute = situation.mute_key or situation.type
        if mute in self._resolved_keys:
            return SituationConsiderResult(route="silent", reason="user_resolved")
        last = self._presented_keys.get(mute)
        if last is not None and situation.already_presented:
            return SituationConsiderResult(route="absorb", reason="already_presented")
        if last is not None and (now - last) < 600.0 and not situation.critical:
            return SituationConsiderResult(route="absorb", reason="situation_cooldown")

        # Replace prior open situation of same type
        for sid, existing in list(self._active.items()):
            if existing.type == situation.type and not existing.resolved:
                if existing.already_presented:
                    return SituationConsiderResult(
                        route="absorb",
                        situation=existing,
                        reason="duplicate_open",
                    )
                situation.id = existing.id
                situation.first_seen = existing.first_seen

        self._active[situation.id] = situation
        return SituationConsiderResult(
            route="situation",
            situation=situation,
            reason="correlated" if len(situation.observations) > 1 else "single",
        )

    # ----- observation mapping -----

    def _event_to_observations(
        self, event: Event, world: Dict[str, Any], now: float
    ) -> List[Observation]:
        data = dict(event.data or {})
        out: List[Observation] = []
        etype = event.type
        source = event.source or "system"

        if etype in (BATTERY_LOW, BATTERY_CRITICAL):
            device = str(data.get("device") or data.get("target") or "laptop").lower()
            plugged = data.get("plugged")
            if plugged is None:
                plugged = data.get("charging")
            pct = data.get("percent", data.get("battery_percent"))
            if device in ("phone", "companion", "mobile"):
                status = str(data.get("status") or "KNOWN")
                if status == "UNAVAILABLE" or data.get("unavailable"):
                    out.append(
                        Observation(
                            OBS_PHONE_UNAVAILABLE,
                            value=None,
                            source=source,
                            timestamp=now,
                            entity="phone",
                            status="UNAVAILABLE",
                        )
                    )
                else:
                    out.append(
                        Observation(
                            OBS_PHONE_BATTERY_LOW,
                            value=pct,
                            source=source,
                            timestamp=now,
                            entity="phone",
                            meta={"plugged": bool(plugged)} if plugged is not None else {},
                        )
                    )
                    if plugged is False:
                        out.append(
                            Observation(
                                OBS_AC_DISCONNECTED,
                                value=False,
                                source=source,
                                timestamp=now,
                                entity="phone",
                            )
                        )
            else:
                out.append(
                    Observation(
                        OBS_LAPTOP_BATTERY_LOW,
                        value=pct,
                        source=source,
                        timestamp=now,
                        entity="laptop",
                        meta={"critical": etype == BATTERY_CRITICAL},
                    )
                )
                if plugged is False or (
                    plugged is None and not data.get("charging")
                ):
                    # Default: battery_low implies on battery unless said otherwise
                    if plugged is False or data.get("on_battery") or plugged is None:
                        out.append(
                            Observation(
                                OBS_AC_DISCONNECTED,
                                value=False,
                                source=source,
                                timestamp=now,
                                entity="laptop",
                            )
                        )

        if etype == BATTERY_CHARGING:
            out.append(
                Observation(
                    OBS_AC_CONNECTED,
                    value=True,
                    source=source,
                    timestamp=now,
                    entity=str(data.get("device") or "laptop"),
                )
            )

        if etype == CALENDAR_UPCOMING:
            minutes = data.get("minutes")
            title = str(data.get("title") or data.get("summary") or "wydarzenie")
            out.append(
                Observation(
                    OBS_CALENDAR_SOON,
                    value={"title": title, "minutes": minutes},
                    source=source,
                    timestamp=now,
                    entity="calendar",
                    meta={
                        "tomorrow": bool(data.get("tomorrow")),
                        "conflict": bool(data.get("conflict")),
                        "start": data.get("start"),
                    },
                )
            )
            if data.get("conflict"):
                out.append(
                    Observation(
                        OBS_CALENDAR_CONFLICT,
                        value=data.get("conflict_with") or title,
                        source=source,
                        timestamp=now,
                        entity="calendar",
                    )
                )

        if etype == LEAVING_HOME or data.get("leaving"):
            presence_status = str(data.get("presence_status") or "KNOWN")
            if presence_status == "UNAVAILABLE" or data.get("presence_unavailable"):
                out.append(
                    Observation(
                        OBS_PRESENCE_UNAVAILABLE,
                        value=None,
                        source=source,
                        timestamp=now,
                        entity="presence",
                        status="UNAVAILABLE",
                    )
                )
            else:
                out.append(
                    Observation(
                        OBS_LEAVING,
                        value=True,
                        source=source,
                        timestamp=now,
                        entity="presence",
                    )
                )
                # Lights still on? from payload or world — never invent
                lights = data.get("lights_on")
                if lights:
                    label = "Lampa"
                    if isinstance(lights, list) and lights:
                        first = lights[0]
                        if isinstance(first, dict):
                            label = str(
                                first.get("name")
                                or first.get("friendly_name")
                                or "Lampa"
                            )
                        else:
                            label = str(first)
                    elif isinstance(lights, str):
                        label = lights
                    out.append(
                        Observation(
                            OBS_LIGHTS_ON,
                            value=label,
                            source=source,
                            timestamp=now,
                            entity="light",
                        )
                    )
                else:
                    self._maybe_note_lights(out, world, now)

        if etype == "home_arrival" or data.get("home_arrival"):
            out.append(
                Observation(
                    OBS_ARRIVING,
                    value=True,
                    source=source,
                    timestamp=now,
                    entity="presence",
                )
            )

        if etype == REMINDER_DUE:
            out.append(
                Observation(
                    OBS_REMINDER,
                    value=str(data.get("text") or data.get("title") or "przypomnienie"),
                    source=source,
                    timestamp=now,
                    entity="reminder",
                )
            )

        if etype == HA_UNAVAILABLE:
            out.append(
                Observation(
                    OBS_HA_DOWN,
                    value=True,
                    source=source,
                    timestamp=now,
                    entity="home_assistant",
                )
            )
            conv = world.get("conversation") or {}
            if isinstance(conv, dict) and (
                conv.get("ha_topic") or str(conv.get("last_intent") or "").startswith("turn_")
            ):
                out.append(
                    Observation(
                        OBS_HA_TOPIC,
                        value=conv.get("last_intent"),
                        source="world",
                        timestamp=now,
                        entity="session",
                    )
                )

        if etype == DISK_LOW:
            out.append(
                Observation(
                    OBS_DISK_LOW,
                    value=data.get("free_gb"),
                    source=source,
                    timestamp=now,
                    entity="disk",
                )
            )

        if etype == CPU_HIGH_SUSTAINED:
            sustained = float(data.get("sustained_seconds") or data.get("duration") or 120)
            out.append(
                Observation(
                    OBS_CPU_SUSTAINED,
                    value=sustained,
                    source=source,
                    timestamp=now,
                    entity="cpu",
                    meta={"percent": data.get("percent")},
                )
            )

        if etype in ("download_complete", "task_completed") and (
            data.get("download") or etype == "download_complete" or data.get("kind") == "download"
        ):
            out.append(
                Observation(
                    OBS_DOWNLOAD_DONE,
                    value=str(data.get("name") or data.get("message") or "pobieranie"),
                    source=source,
                    timestamp=now,
                    entity="download",
                )
            )

        if data.get("deadline") or etype == "deadline_approaching":
            out.append(
                Observation(
                    OBS_DEADLINE,
                    value=data.get("deadline") or data.get("goal") or data.get("text"),
                    source=source,
                    timestamp=now,
                    entity="goal",
                    meta={"minutes": data.get("minutes")},
                )
            )

        if data.get("active_use") or data.get("laptop_active"):
            out.append(
                Observation(
                    OBS_ACTIVE_USE,
                    value=True,
                    source=source,
                    timestamp=now,
                    entity="laptop",
                )
            )

        if data.get("fullscreen") or data.get("gaming"):
            out.append(
                Observation(
                    OBS_FULLSCREEN_GAME,
                    value=True,
                    source=source,
                    timestamp=now,
                    entity="session",
                    meta={"app": data.get("app") or data.get("process")},
                )
            )

        # Enrich from world when event is battery/calendar related
        if etype in (BATTERY_LOW, BATTERY_CRITICAL, CALENDAR_UPCOMING):
            self._enrich_from_world(out, world, now)

        return out

    def _enrich_from_world(
        self, out: List[Observation], world: Dict[str, Any], now: float
    ) -> None:
        kinds = {o.kind for o in out}
        cal = world.get("calendar") or {}
        if isinstance(cal, dict) and OBS_CALENDAR_SOON not in kinds:
            minutes = cal.get("minutes")
            title = cal.get("next") or cal.get("title") or cal.get("summary")
            if title and minutes is not None and float(minutes) <= 45:
                out.append(
                    Observation(
                        OBS_CALENDAR_SOON,
                        value={"title": title, "minutes": minutes},
                        source="world",
                        timestamp=now,
                        entity="calendar",
                    )
                )
        pc = world.get("pc") or world.get("computer") or {}
        if isinstance(pc, dict) and OBS_LAPTOP_BATTERY_LOW not in kinds:
            pct = pc.get("battery_percent")
            if pct is not None and float(pct) <= 20:
                out.append(
                    Observation(
                        OBS_LAPTOP_BATTERY_LOW,
                        value=pct,
                        source="world",
                        timestamp=now,
                        entity="laptop",
                    )
                )
                if pc.get("plugged") is False or pc.get("on_battery"):
                    out.append(
                        Observation(
                            OBS_AC_DISCONNECTED,
                            value=False,
                            source="world",
                            timestamp=now,
                            entity="laptop",
                        )
                    )
        session = world.get("session") or {}
        if isinstance(session, dict) and session.get("active"):
            if OBS_ACTIVE_USE not in kinds:
                out.append(
                    Observation(
                        OBS_ACTIVE_USE,
                        value=True,
                        source="world",
                        timestamp=now,
                        entity="laptop",
                    )
                )
        conv = world.get("conversation") or {}
        if isinstance(conv, dict):
            if conv.get("leaving") and OBS_LEAVING not in kinds:
                out.append(
                    Observation(
                        OBS_LEAVING,
                        value=True,
                        source="world",
                        timestamp=now,
                        entity="presence",
                    )
                )

    def _maybe_note_lights(
        self, out: List[Observation], world: Dict[str, Any], now: float
    ) -> None:
        freshness = str(world.get("_smart_home_freshness") or "CURRENT")
        if freshness in ("UNKNOWN", "UNAVAILABLE"):
            return
        smart = world.get("smart_home") or {}
        if not isinstance(smart, dict) or not smart:
            return
        for entity_id, device in smart.items():
            if not isinstance(device, dict):
                continue
            state = str(device.get("state") or device.get("status") or "").strip().lower()
            if state not in ("on", "true", "1"):
                continue
            domain = str(device.get("domain") or entity_id or "").lower()
            name = str(device.get("name") or device.get("friendly_name") or "").lower()
            if (
                "light" in domain
                or "lamp" in name
                or "lampa" in name
                or str(entity_id).startswith("light.")
                or device.get("is_light")
            ):
                label = str(
                    device.get("name") or device.get("friendly_name") or "Lampa"
                )
                out.append(
                    Observation(
                        OBS_LIGHTS_ON,
                        value=label,
                        source="world",
                        timestamp=now,
                        entity=str(entity_id),
                    )
                )
                return

    # ----- correlation -----

    def _recent(self, now: float) -> List[Observation]:
        self._prune(now)
        return list(self._observations)

    def _prune(self, now: float) -> None:
        cutoff = now - _CORRELATION_WINDOW
        self._observations = [o for o in self._observations if o.timestamp >= cutoff]
        expired = [
            sid
            for sid, s in self._active.items()
            if s.is_expired(now) or s.resolved
        ]
        for sid in expired:
            self._active.pop(sid, None)

    def _latest(self, kinds: Set[str], recent: List[Observation]) -> Dict[str, Observation]:
        found: Dict[str, Observation] = {}
        for obs in recent:
            if obs.kind in kinds:
                found[obs.kind] = obs  # last wins
        return found

    def _correlate(
        self, now: float, world: Dict[str, Any], trigger: Event
    ) -> Optional[Situation]:
        recent = self._recent(now)
        by = self._latest({o.kind for o in recent}, recent)

        # Honesty: unavailable phone / presence never pretend
        if OBS_PHONE_UNAVAILABLE in by and trigger.type in (BATTERY_LOW, BATTERY_CRITICAL):
            if str((trigger.data or {}).get("device") or "").lower() in (
                "phone",
                "companion",
                "mobile",
            ):
                return None  # legacy will also stay quiet if we silent — use silent situation skip
        if OBS_PRESENCE_UNAVAILABLE in by and trigger.type == LEAVING_HOME:
            return None

        # 1) Laptop battery + AC off + upcoming meeting (+ optional active use)
        if (
            OBS_LAPTOP_BATTERY_LOW in by
            and OBS_AC_DISCONNECTED in by
            and OBS_CALENDAR_SOON in by
        ):
            bat = by[OBS_LAPTOP_BATTERY_LOW]
            cal = by[OBS_CALENDAR_SOON]
            cal_val = cal.value if isinstance(cal.value, dict) else {"title": cal.value}
            title = str(cal_val.get("title") or "wydarzenie")
            minutes = cal_val.get("minutes")
            pct = bat.value
            when = f"za {int(minutes)} minut" if minutes is not None else "wkrótce"
            active = OBS_ACTIVE_USE in by
            msg = (
                f"Bateria laptopa ma {int(pct) if pct is not None else '?'}% bez ładowania, "
                f"a {when} masz {title}."
            )
            if active:
                msg += " Możesz nie dotrwać do końca."
            msg += " Podłączyć tryb oszczędzania?"
            return Situation(
                type=SituationType.DEVICE_MAY_NOT_LAST_FOR_UPCOMING_EVENT.value,
                observations=[bat, by[OBS_AC_DISCONNECTED], cal]
                + ([by[OBS_ACTIVE_USE]] if active else []),
                entities={
                    "battery_percent": pct,
                    "event_title": title,
                    "minutes": minutes,
                },
                first_seen=min(bat.timestamp, cal.timestamp),
                last_updated=now,
                importance=0.85,
                confidence=0.8 if active else 0.7,
                urgency=_urgency_from_minutes(minutes, pct),
                relevance=0.9,
                expiry=now + _DEFAULT_EXPIRY,
                related_event=title,
                suggested_action="enable_battery_saver",
                message=msg,
                offer_kind="battery_saver",
                offer_extra={"tool": "set_power_mode", "mode": "saver"},
                policy_hint=SUGGEST,
                deferrable=True,
                mute_key=SituationType.DEVICE_MAY_NOT_LAST_FOR_UPCOMING_EVENT.value,
            )

        # 2) Phone low + leaving + not charging
        if OBS_PHONE_BATTERY_LOW in by and OBS_LEAVING in by:
            phone = by[OBS_PHONE_BATTERY_LOW]
            if phone.status == "UNAVAILABLE":
                return None
            plugged = bool((phone.meta or {}).get("plugged"))
            if plugged:
                return None
            pct = phone.value
            msg = (
                f"Telefon ma {int(pct) if pct is not None else '?'}% baterii i nie ładuje się, "
                "a zaraz wychodzisz. Podłączyć na chwilę?"
            )
            return Situation(
                type=SituationType.PHONE_CHARGE_BEFORE_DEPARTURE.value,
                observations=[phone, by[OBS_LEAVING]]
                + ([by[OBS_AC_DISCONNECTED]] if OBS_AC_DISCONNECTED in by else []),
                entities={"battery_percent": pct, "device": "phone"},
                first_seen=min(phone.timestamp, by[OBS_LEAVING].timestamp),
                last_updated=now,
                importance=0.8,
                confidence=0.75,
                urgency=0.7,
                relevance=0.85,
                expiry=now + 1200.0,
                suggested_action="charge_phone",
                message=msg,
                policy_hint=SUGGEST,
                deferrable=True,
                mute_key=SituationType.PHONE_CHARGE_BEFORE_DEPARTURE.value,
            )

        # 3) Calendar conflict
        if OBS_CALENDAR_CONFLICT in by:
            conflict = by[OBS_CALENDAR_CONFLICT]
            return Situation(
                type=SituationType.CALENDAR_CONFLICT.value,
                observations=[conflict],
                entities={"conflict": conflict.value},
                first_seen=conflict.timestamp,
                last_updated=now,
                importance=0.8,
                confidence=0.7,
                urgency=0.65,
                relevance=0.85,
                expiry=now + 3600.0,
                message=f"Kolizja w kalendarzu: {conflict.value}.",
                policy_hint=NOTIFY,
                mute_key=SituationType.CALENDAR_CONFLICT.value,
            )

        # 4) Deadline approaching for a prior goal
        if OBS_DEADLINE in by:
            dl = by[OBS_DEADLINE]
            return Situation(
                type=SituationType.DEADLINE_APPROACHING.value,
                observations=[dl],
                entities={"goal": dl.value},
                first_seen=dl.timestamp,
                last_updated=now,
                importance=0.75,
                confidence=0.7,
                urgency=0.7,
                relevance=0.8,
                expiry=now + 1800.0,
                related_goal=str(dl.value or ""),
                message=f"Zbliża się termin: {dl.value}.",
                policy_hint=NOTIFY,
                mute_key=SituationType.DEADLINE_APPROACHING.value,
            )

        # 5) Evening + tomorrow event
        if OBS_CALENDAR_SOON in by:
            cal = by[OBS_CALENDAR_SOON]
            meta = cal.meta or {}
            cal_val = cal.value if isinstance(cal.value, dict) else {}
            if meta.get("tomorrow") or (isinstance(cal_val, dict) and cal_val.get("tomorrow")):
                local = _local_hour(host_clock=None, world=world)
                if local is not None and local >= 18:
                    title = str(
                        (cal_val.get("title") if isinstance(cal_val, dict) else cal.value)
                        or "wydarzenie"
                    )
                    return Situation(
                        type=SituationType.EVENING_TOMORROW_EVENT.value,
                        observations=[cal],
                        entities={"title": title},
                        first_seen=cal.timestamp,
                        last_updated=now,
                        importance=0.55,
                        confidence=0.65,
                        urgency=0.2,
                        relevance=0.6,
                        expiry=now + 7200.0,
                        related_event=title,
                        message=f"Na jutro masz {title}. Chcesz przygotować coś wieczorem?",
                        policy_hint=SUGGEST,
                        deferrable=True,
                        mute_key=SituationType.EVENING_TOMORROW_EVENT.value,
                    )

        # 6) Download done — high value interesting
        if OBS_DOWNLOAD_DONE in by and trigger.type in (
            "download_complete",
            "task_completed",
        ):
            dl = by[OBS_DOWNLOAD_DONE]
            return Situation(
                type=SituationType.DOWNLOAD_COMPLETE.value,
                observations=[dl],
                entities={"name": dl.value},
                first_seen=dl.timestamp,
                last_updated=now,
                importance=0.55,
                confidence=0.9,
                urgency=0.3,
                relevance=0.7,
                expiry=now + 600.0,
                message=f"Pobieranie zakończone: {dl.value}.",
                policy_hint=NOTIFY,
                mute_key=f"download:{dl.value}",
            )

        # 7) Sustained CPU only when long enough (interesting_event already gated)
        if OBS_CPU_SUSTAINED in by and trigger.type == CPU_HIGH_SUSTAINED:
            cpu = by[OBS_CPU_SUSTAINED]
            sustained = float(cpu.value or 0)
            if sustained < 600:
                return None  # short pressure → legacy may still notify; prefer silence via EV
            return Situation(
                type=SituationType.SUSTAINED_SYSTEM_PRESSURE.value,
                observations=[cpu],
                entities={"sustained_seconds": sustained},
                first_seen=cpu.timestamp,
                last_updated=now,
                importance=0.5,
                confidence=0.7,
                urgency=0.3,
                relevance=0.45,
                expiry=now + 1800.0,
                message="Procesor jest mocno obciążony od dłuższego czasu.",
                policy_hint=SUGGEST,
                suggested_action="review_cpu",
                mute_key=SituationType.SUSTAINED_SYSTEM_PRESSURE.value,
            )

        # Reminder while gaming → situation that Attention will DEFER
        if OBS_REMINDER in by and OBS_FULLSCREEN_GAME in by:
            rem = by[OBS_REMINDER]
            return Situation(
                type=SituationType.REMINDER_WHILE_BUSY.value,
                observations=[rem, by[OBS_FULLSCREEN_GAME]],
                entities={"text": rem.value},
                first_seen=rem.timestamp,
                last_updated=now,
                importance=0.7,
                confidence=0.8,
                urgency=0.5,
                relevance=0.75,
                expiry=now + 1800.0,
                message=f"Przypomnienie: {rem.value}.",
                policy_hint=NOTIFY,
                deferrable=True,
                mute_key=f"reminder:{rem.value}",
            )

        # HA mid-procedure / leaving+lights remain in ProactivePolicy (legacy multi-signal).
        # Disk-low alone stays legacy so existing category/cooldown contracts hold.
        return None


def _urgency_from_minutes(minutes: Any, pct: Any) -> float:
    try:
        m = float(minutes)
    except (TypeError, ValueError):
        m = 40.0
    try:
        p = float(pct)
    except (TypeError, ValueError):
        p = 20.0
    # Sooner meeting + lower battery → higher urgency
    time_u = max(0.0, min(1.0, 1.0 - (m / 60.0)))
    bat_u = max(0.0, min(1.0, 1.0 - (p / 30.0)))
    return round(0.5 * time_u + 0.5 * bat_u, 3)


def _local_hour(host_clock=None, world: Optional[Dict[str, Any]] = None) -> Optional[int]:
    try:
        if host_clock is not None and hasattr(host_clock, "now_local"):
            return int(host_clock.now_local().hour)
    except Exception:
        pass
    world = world or {}
    session = world.get("session") or {}
    if isinstance(session, dict) and session.get("hour") is not None:
        try:
            return int(session["hour"])
        except (TypeError, ValueError):
            pass
    try:
        from datetime import datetime

        return int(datetime.now().hour)
    except Exception:
        return None


def _snapshot_world() -> Dict[str, Any]:
    try:
        from core.world_state import get_world_state, take_snapshot

        world = dict(get_world_state() or {})
        try:
            snap = take_snapshot()
        except Exception:
            snap = None
        if snap is not None:
            for slot in (
                "session",
                "tts",
                "stt",
                "calendar",
                "reminders",
                "pc",
                "computer",
                "integrations",
                "network",
                "media",
                "conversation",
            ):
                fact = snap.fact(slot) if hasattr(snap, "fact") else None
                if fact is None:
                    continue
                val = getattr(fact, "value", None)
                if isinstance(val, dict):
                    world[slot] = dict(val)
                elif val is not None:
                    world[slot] = val
        return world
    except Exception:
        return {}
