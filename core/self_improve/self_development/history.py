# ==========================
# ELIAN — Autonomous development history
# ==========================
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .types import CycleRecord


class DevelopmentHistoryStore:
    """Compact cycle history (no utterances, no secrets)."""

    def __init__(self, path: Path, *, max_entries: int = 500) -> None:
        self.path = Path(path)
        self.max_entries = max(50, int(max_entries))
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if isinstance(raw, dict):
            rows = raw.get("cycles") or []
        elif isinstance(raw, list):
            rows = raw
        else:
            rows = []
        return [r for r in rows if isinstance(r, dict)]

    def append(self, record: CycleRecord) -> None:
        rows = self.load()
        rows.append(record.to_dict())
        if len(rows) > self.max_entries:
            rows = rows[-self.max_entries :]
        self._write({"cycles": rows, "count": len(rows)})

    def fingerprint_failures(self, fingerprint: str) -> int:
        fp = str(fingerprint or "")
        n = 0
        for row in self.load():
            if str(row.get("result") or "") != "ROLLED_BACK":
                continue
            # candidate field stores title; metadata may embed fingerprint in reason
            if fp and fp in str(row.get("failure_reason") or ""):
                n += 1
            elif fp and fp in str(row.get("candidate") or ""):
                n += 1
        return n

    def recent_fingerprints(self, limit: int = 20) -> List[str]:
        out: List[str] = []
        for row in reversed(self.load()):
            fr = str(row.get("failure_reason") or "")
            if "fingerprint=" in fr:
                part = fr.split("fingerprint=", 1)[1].split(";", 1)[0].strip()
                if part:
                    out.append(part)
            if len(out) >= limit:
                break
        return out

    def _write(self, payload: Dict[str, Any]) -> None:
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            prefix=".dev_history_",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


def default_history_path(project_root: Optional[Path] = None) -> Path:
    root = Path(project_root) if project_root else Path(__file__).resolve().parents[3]
    return root / "Data" / "self_improve" / "development_history.json"
