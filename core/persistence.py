# ==========================
# ELIAN — safe JSON persistence (Phase 9)
# ==========================

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Tuple


def _timestamp_suffix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup_corrupt_file(path: Path, *, reason: str = "corrupt") -> Optional[Path]:
    """Move unreadable JSON aside; return backup path or None."""
    path = Path(path)
    if not path.exists():
        return None
    backup = path.with_suffix(path.suffix + f".{reason}.{_timestamp_suffix()}.bak")
    try:
        shutil.copy2(path, backup)
        print(f"[PERSISTENCE] Backed up corrupt file: {backup.name} ({reason})")
        return backup
    except OSError as exc:
        print(f"[PERSISTENCE] Could not backup {path.name}: {exc}")
        return None


def safe_read_json(
    path: Path,
    default=None,
    *,
    on_corrupt: Optional[Callable[[Path, Exception], None]] = None,
) -> Tuple[Any, bool]:
    """
    Read JSON with corruption handling.
    Returns (data, recovered_from_corruption).
    """
    path = Path(path)
    if not path.exists():
        return deepcopy(default) if default is not None else {}, False

    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file), False
    except json.JSONDecodeError as exc:
        backup_corrupt_file(path, reason="corrupt")
        if on_corrupt:
            on_corrupt(path, exc)
        else:
            print(f"[PERSISTENCE] Invalid JSON in {path.name}: {exc}")
        return deepcopy(default) if default is not None else {}, True
    except OSError as exc:
        print(f"[PERSISTENCE] Read error {path.name}: {exc}")
        return deepcopy(default) if default is not None else {}, False
