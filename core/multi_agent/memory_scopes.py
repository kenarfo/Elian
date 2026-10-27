# ==========================
# ELIAN — Scoped agent memory (not full dump into every context)
# ==========================
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .types import MemoryScope


class ScopedMemoryStore:
    """Compact key/value memory partitioned by scope."""

    def __init__(self, path: Optional[Path] = None) -> None:
        root = Path(__file__).resolve().parents[2]
        self.path = Path(path or (root / "Data" / "multi_agent" / "scoped_memory.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data: Dict[str, Dict[str, Any]] = {
            s.value: {} for s in MemoryScope
        }
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                with self._lock:
                    for scope, bucket in raw.items():
                        if isinstance(bucket, dict):
                            self._data[str(scope)] = dict(bucket)
        except Exception:
            pass

    def save(self) -> None:
        with self._lock:
            payload = json.dumps(self._data, ensure_ascii=False, indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(
            prefix=".scoped_mem_", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def put(self, scope: str, key: str, value: Any) -> None:
        scope_key = str(scope or MemoryScope.EPHEMERAL.value)
        with self._lock:
            bucket = self._data.setdefault(scope_key, {})
            bucket[str(key)] = {
                "value": value,
                "updated_at": time.time(),
            }
            if scope_key == MemoryScope.EPHEMERAL.value and len(bucket) > 200:
                # Drop oldest ephemeral keys.
                ordered = sorted(bucket.items(), key=lambda kv: kv[1].get("updated_at", 0))
                for drop_key, _ in ordered[: max(0, len(bucket) - 200)]:
                    bucket.pop(drop_key, None)
        if scope_key != MemoryScope.EPHEMERAL.value:
            self.save()

    def get(self, scope: str, key: str) -> Any:
        with self._lock:
            bucket = self._data.get(str(scope) or "", {})
            row = bucket.get(str(key))
            if isinstance(row, dict) and "value" in row:
                return row.get("value")
            return row

    def list_keys(self, scope: str) -> List[str]:
        with self._lock:
            return sorted(list((self._data.get(str(scope) or "") or {}).keys()))

    def publish_fact(self, key: str, value: Any, *, scope: str = "TASK") -> None:
        """Cross-agent durable fact — keep compact."""
        if isinstance(value, str) and len(value) > 500:
            value = value[:500]
        self.put(scope, key, value)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                scope: {"keys": sorted(list(bucket.keys())), "count": len(bucket)}
                for scope, bucket in self._data.items()
            }
