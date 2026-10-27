# ==========================
# ELIAN — companion device registry (pairing / tokens / revoke)
# ==========================
"""Persists paired LAN devices. Tokens are stored hashed, never plaintext."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.clock import Clock, SystemClock
from core.companion.protocol import DEFAULT_PERMISSIONS
from core.persistence import safe_read_json
from memory.store import atomic_write_json


REGISTRY_VERSION = 1


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _new_salt() -> str:
    return secrets.token_hex(16)


def hash_token(token: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{token}".encode("utf-8")).hexdigest()


def _token_matches(token: str, salt: str, stored_hash: str) -> bool:
    digest = hash_token(token, salt)
    return hmac.compare_digest(digest, stored_hash)


@dataclass
class CompanionDevice:
    device_id: str
    name: str = ""
    token_hash: str = ""
    token_salt: str = ""
    permissions: Dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_PERMISSIONS))
    muted: bool = False
    revoked: bool = False
    platform: str = ""
    created_at: float = 0.0
    last_seen: float = 0.0
    delivered_ids: List[str] = field(default_factory=list)

    def to_public_dict(self, *, online: bool = False) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "platform": self.platform,
            "muted": self.muted,
            "revoked": self.revoked,
            "online": online and not self.revoked,
            "permissions": dict(self.permissions),
            "last_seen": self.last_seen,
        }

    def to_store_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "token_hash": self.token_hash,
            "token_salt": self.token_salt,
            "permissions": dict(self.permissions),
            "muted": self.muted,
            "revoked": self.revoked,
            "platform": self.platform,
            "created_at": self.created_at,
            "last_seen": self.last_seen,
            "delivered_ids": list(self.delivered_ids)[-200:],
        }

    @classmethod
    def from_store(cls, raw: Dict[str, Any]) -> "CompanionDevice":
        perms = dict(DEFAULT_PERMISSIONS)
        extra = raw.get("permissions") if isinstance(raw.get("permissions"), dict) else {}
        perms.update({k: bool(v) for k, v in extra.items()})
        return cls(
            device_id=str(raw.get("device_id") or ""),
            name=str(raw.get("name") or ""),
            token_hash=str(raw.get("token_hash") or ""),
            token_salt=str(raw.get("token_salt") or ""),
            permissions=perms,
            muted=bool(raw.get("muted")),
            revoked=bool(raw.get("revoked")),
            platform=str(raw.get("platform") or ""),
            created_at=float(raw.get("created_at") or 0.0),
            last_seen=float(raw.get("last_seen") or 0.0),
            delivered_ids=list(raw.get("delivered_ids") or [])[-200:],
        )


@dataclass
class PendingPairing:
    code: str
    expires_at: float


class RateLimiter:
    def __init__(self, clock: Clock, *, limit: int, window_s: float):
        self.clock = clock
        self.limit = max(1, int(limit))
        self.window_s = float(window_s)
        self._hits: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = float(self.clock.time())
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if (now - t) < self.window_s]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


class DeviceRegistry:
    """Paired-device store. Tokens never written in plaintext."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Optional[Clock] = None,
        pairing_window_seconds: float = 120.0,
        heartbeat_timeout_seconds: float = 45.0,
        rate_limit_per_minute: int = 60,
    ):
        self.path = Path(path)
        self.clock = clock or SystemClock()
        self.pairing_window_seconds = float(pairing_window_seconds)
        self.heartbeat_timeout_seconds = float(heartbeat_timeout_seconds)
        self._lock = threading.RLock()
        self.devices: Dict[str, CompanionDevice] = {}
        self._pending: Optional[PendingPairing] = None
        self.ip_limiter = RateLimiter(
            self.clock, limit=rate_limit_per_minute, window_s=60.0
        )
        self.pair_limiter = RateLimiter(self.clock, limit=8, window_s=300.0)
        self._load()

    def _load(self) -> None:
        data, _recovered = safe_read_json(self.path, default={"version": REGISTRY_VERSION, "devices": []})
        if not isinstance(data, dict):
            data = {"devices": []}
        rows = data.get("devices") if isinstance(data.get("devices"), list) else []
        with self._lock:
            self.devices = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                device = CompanionDevice.from_store(row)
                if device.device_id:
                    self.devices[device.device_id] = device

    def _save(self) -> None:
        payload = {
            "version": REGISTRY_VERSION,
            "devices": [d.to_store_dict() for d in self.devices.values()],
        }
        atomic_write_json(self.path, payload)

    def start_pairing(self) -> Tuple[str, float]:
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires = float(self.clock.time()) + self.pairing_window_seconds
        with self._lock:
            self._pending = PendingPairing(code=code, expires_at=expires)
        return code, expires

    def pairing_active(self) -> bool:
        with self._lock:
            return self._pending_ok_unlocked()

    def _pending_ok_unlocked(self) -> bool:
        pending = self._pending
        if pending is None:
            return False
        if float(self.clock.time()) > pending.expires_at:
            self._pending = None
            return False
        return True

    def complete_pairing(
        self,
        *,
        pairing_code: str,
        device_id: str = "",
        name: str = "",
        platform: str = "",
    ) -> Tuple[Optional[CompanionDevice], str, str]:
        """Returns (device, plaintext_token, error). Token is shown once."""
        code = str(pairing_code or "").strip()
        with self._lock:
            if not self._pending_ok_unlocked():
                return None, "", "Kod parowania wygasł."
            if not hmac.compare_digest(self._pending.code, code):
                return None, "", "Nieprawidłowy kod parowania."
            now = float(self.clock.time())
            did = str(device_id or "").strip() or f"dev_{uuid.uuid4().hex}"
            token = _new_token()
            salt = _new_salt()
            existing = self.devices.get(did)
            if existing is None:
                device = CompanionDevice(
                    device_id=did,
                    name=str(name or "Telefon").strip() or "Telefon",
                    token_hash=hash_token(token, salt),
                    token_salt=salt,
                    permissions=dict(DEFAULT_PERMISSIONS),
                    platform=str(platform or "").strip(),
                    created_at=now,
                    last_seen=now,
                )
            else:
                device = existing
                device.name = str(name or device.name or "Telefon").strip() or device.name
                if platform:
                    device.platform = str(platform).strip()
                device.token_hash = hash_token(token, salt)
                device.token_salt = salt
                device.revoked = False
                device.last_seen = now
            self.devices[did] = device
            self._pending = None
            self._save()
            return device, token, ""

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            device = self.devices.get(str(device_id or ""))
            if device is None:
                return False
            device.revoked = True
            device.token_hash = ""
            device.token_salt = ""
            self._save()
            return True

    def authenticate(self, token: str) -> Optional[CompanionDevice]:
        raw = str(token or "").strip()
        if not raw:
            return None
        with self._lock:
            for device in self.devices.values():
                if device.revoked or not device.token_hash or not device.token_salt:
                    continue
                if _token_matches(raw, device.token_salt, device.token_hash):
                    return device
        return None

    def get(self, device_id: str) -> Optional[CompanionDevice]:
        with self._lock:
            return self.devices.get(str(device_id or ""))

    def heartbeat(self, device_id: str, *, persist: bool = True) -> Optional[CompanionDevice]:
        with self._lock:
            device = self.devices.get(str(device_id or ""))
            if device is None or device.revoked:
                return None
            now = float(self.clock.time())
            prev = float(device.last_seen or 0.0)
            device.last_seen = now
            # Debounce disk writes under frequent auth/heartbeat.
            if persist or (now - prev) >= 5.0 or prev <= 0.0:
                self._save()
            return device

    def is_online(self, device_id: str) -> bool:
        with self._lock:
            device = self.devices.get(str(device_id or ""))
            if device is None or device.revoked:
                return False
            if not device.last_seen:
                return False
            age = float(self.clock.time()) - float(device.last_seen)
            return age <= self.heartbeat_timeout_seconds

    def set_muted(self, device_id: str, muted: bool) -> Optional[CompanionDevice]:
        with self._lock:
            device = self.devices.get(str(device_id or ""))
            if device is None or device.revoked:
                return None
            device.muted = bool(muted)
            self._save()
            return device

    def mark_delivered(self, device_id: str, event_id: str) -> bool:
        eid = str(event_id or "")
        if not eid:
            return False
        with self._lock:
            device = self.devices.get(str(device_id or ""))
            if device is None:
                return False
            if eid in device.delivered_ids:
                return False
            device.delivered_ids.append(eid)
            del device.delivered_ids[:-200]
            self._save()
            return True

    def already_delivered(self, device_id: str, event_id: str) -> bool:
        with self._lock:
            device = self.devices.get(str(device_id or ""))
            if device is None:
                return False
            return str(event_id or "") in device.delivered_ids

    def list_active(self) -> List[CompanionDevice]:
        with self._lock:
            return [d for d in self.devices.values() if not d.revoked]
