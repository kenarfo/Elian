# ==========================
# ELIAN — Safe checkpoints for autonomous development
# ==========================
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


# Paths never snapshotted / restored by autonomous engine
FORBIDDEN_PREFIXES = (
    "Data/config.json",
    "Data/credentials",
    ".env",
    "Secrets/",
    "secrets/",
)
FORBIDDEN_NAMES = {
    ".env",
    "credentials.json",
    "token.json",
    "secrets.json",
    "api_keys.json",
}
PROTECTED_TELEMETRY = {
    "Data/self_improve/experience_log.json",
}


@dataclass
class Checkpoint:
    checkpoint_id: str
    strategy: str  # file | git
    created_at: str
    files: Dict[str, str] = field(default_factory=dict)  # relpath -> snapshot abs path
    new_files: List[str] = field(default_factory=list)
    git_ref: str = ""
    baseline_dirty: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "strategy": self.strategy,
            "created_at": self.created_at,
            "files": dict(self.files),
            "new_files": list(self.new_files),
            "git_ref": self.git_ref,
            "baseline_dirty": list(self.baseline_dirty),
        }

    @classmethod
    def from_dict(cls, row: Any) -> Optional["Checkpoint"]:
        if not isinstance(row, dict) or not row.get("checkpoint_id"):
            return None
        return cls(
            checkpoint_id=str(row["checkpoint_id"]),
            strategy=str(row.get("strategy") or "file"),
            created_at=str(row.get("created_at") or ""),
            files={str(k): str(v) for k, v in (row.get("files") or {}).items()},
            new_files=[str(x) for x in row.get("new_files") or []],
            git_ref=str(row.get("git_ref") or ""),
            baseline_dirty=[str(x) for x in row.get("baseline_dirty") or []],
        )


class CheckpointManager:
    """
    File-snapshot checkpoints (always).

    Never runs destructive git reset --hard against unrelated user work.
    Optional git commit on KEEP when git identity + repo are available.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        checkpoint_root: Optional[Path] = None,
    ) -> None:
        self.root = Path(project_root).resolve()
        self.checkpoint_root = Path(
            checkpoint_root
            or (self.root / "Data" / "self_improve" / "checkpoints")
        )
        self.checkpoint_root.mkdir(parents=True, exist_ok=True)

    def is_forbidden(self, relpath: str) -> bool:
        rp = relpath.replace("\\", "/")
        while rp.startswith("./"):
            rp = rp[2:]
        rp = rp.lstrip("/")
        name = Path(rp).name.lower()
        if name in FORBIDDEN_NAMES:
            return True
        if rp == ".env" or rp.startswith(".env."):
            return True
        for pref in FORBIDDEN_PREFIXES:
            if rp == pref or rp.startswith(pref.rstrip("/") + "/"):
                return True
        if rp in PROTECTED_TELEMETRY:
            return True
        return False

    def list_dirty_paths(self) -> List[str]:
        """Best-effort dirty listing (git if available, else empty)."""
        if not (self.root / ".git").exists():
            return []
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception:
            return []
        if proc.returncode != 0:
            return []
        out: List[str] = []
        for line in (proc.stdout or "").splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip().replace("\\", "/")
            if " -> " in path:
                path = path.split(" -> ", 1)[1].strip()
            if path:
                out.append(path)
        return out

    def create(
        self,
        cycle_id: str,
        *,
        tracked_files: Optional[List[str]] = None,
    ) -> Checkpoint:
        cid = f"cp_{cycle_id}_{int(time.time())}"
        dest = self.checkpoint_root / cid
        dest.mkdir(parents=True, exist_ok=True)
        baseline_dirty = self.list_dirty_paths()
        files: Dict[str, str] = {}
        for rel in tracked_files or []:
            rel_n = rel.replace("\\", "/")
            while rel_n.startswith("./"):
                rel_n = rel_n[2:]
            rel_n = rel_n.lstrip("/")
            if self.is_forbidden(rel_n):
                continue
            src = self.root / rel_n
            if not src.is_file():
                continue
            snap = dest / rel_n
            snap.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, snap)
            files[rel_n] = str(snap)
        meta = Checkpoint(
            checkpoint_id=cid,
            strategy="file",
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            files=files,
            new_files=[],
            baseline_dirty=baseline_dirty,
        )
        (dest / "checkpoint.json").write_text(
            json.dumps(meta.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return meta

    def _norm(self, relpath: str) -> str:
        rel_n = relpath.replace("\\", "/")
        while rel_n.startswith("./"):
            rel_n = rel_n[2:]
        return rel_n.lstrip("/")

    def note_new_file(self, checkpoint: Checkpoint, relpath: str) -> None:
        rel_n = self._norm(relpath)
        if self.is_forbidden(rel_n):
            return
        if rel_n not in checkpoint.new_files:
            checkpoint.new_files.append(rel_n)
        self._persist(checkpoint)

    def snapshot_before_write(self, checkpoint: Checkpoint, relpath: str) -> None:
        """Capture original content the first time a file is about to be overwritten."""
        rel_n = self._norm(relpath)
        if self.is_forbidden(rel_n) or rel_n in checkpoint.files:
            return
        src = self.root / rel_n
        if not src.is_file():
            if rel_n not in checkpoint.new_files:
                checkpoint.new_files.append(rel_n)
            self._persist(checkpoint)
            return
        dest = self.checkpoint_root / checkpoint.checkpoint_id / rel_n
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        checkpoint.files[rel_n] = str(dest)
        self._persist(checkpoint)

    def rollback(self, checkpoint: Checkpoint) -> List[str]:
        """
        Restore only files owned by this autonomous cycle.
        Never deletes unrelated user dirty files.
        """
        restored: List[str] = []
        for rel, snap_path in list(checkpoint.files.items()):
            if self.is_forbidden(rel):
                continue
            target = self.root / rel
            snap = Path(snap_path)
            if snap.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(snap, target)
                restored.append(rel)
        for rel in list(checkpoint.new_files):
            if self.is_forbidden(rel):
                continue
            # Only remove if it was created by this cycle (was new)
            if rel in checkpoint.files:
                continue
            target = self.root / rel
            if target.is_file():
                try:
                    target.unlink()
                    restored.append(rel + " (removed)")
                except OSError:
                    pass
        return restored

    def keep(self, checkpoint: Checkpoint, *, message: str = "") -> str:
        """Mark keep; optionally create a git commit if safe."""
        commit_ref = ""
        if (self.root / ".git").exists():
            commit_ref = self._try_git_commit(checkpoint, message=message)
        # Retain snapshot metadata for history; do not delete (audit)
        meta_path = self.checkpoint_root / checkpoint.checkpoint_id / "checkpoint.json"
        data = checkpoint.to_dict()
        data["kept"] = True
        data["commit_ref"] = commit_ref
        try:
            meta_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        return commit_ref

    def _persist(self, checkpoint: Checkpoint) -> None:
        meta_path = self.checkpoint_root / checkpoint.checkpoint_id / "checkpoint.json"
        try:
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(
                json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def _try_git_commit(self, checkpoint: Checkpoint, *, message: str) -> str:
        """Best-effort commit of cycle files only. Never reset --hard."""
        paths: Set[str] = set(checkpoint.files) | set(checkpoint.new_files)
        safe = [p for p in paths if not self.is_forbidden(p)]
        if not safe:
            return ""
        try:
            # Refuse if dirty paths outside our set appear after baseline
            dirty = set(self.list_dirty_paths())
            unrelated = dirty - set(safe) - set(checkpoint.baseline_dirty)
            # Allow dirty that were already dirty at baseline
            for base in checkpoint.baseline_dirty:
                unrelated.discard(base)
            # Only add our files
            for p in safe:
                target = self.root / p
                if target.exists():
                    subprocess.run(
                        ["git", "add", "--", p],
                        cwd=str(self.root),
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
            msg = message or f"self-dev checkpoint {checkpoint.checkpoint_id}"
            proc = subprocess.run(
                ["git", "commit", "-m", msg, "--", *safe],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if proc.returncode != 0:
                return ""
            sha = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            return (sha.stdout or "").strip()
        except Exception:
            return ""
