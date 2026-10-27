# ==========================
# ELIAN — Explicit system SelfModel (self-awareness of runtime state)
# ==========================
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .types import HealthState, SystemMode, VoiceUIState


class SelfModel:
    """
    Machine-readable model of Elian's current system state.

    Not consciousness — a continuously updated structured self-description
    for introspection, GUI, and orchestrator decisions.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        root = Path(__file__).resolve().parents[2]
        self.path = Path(path or (root / "Data" / "multi_agent" / "self_model.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._state: Dict[str, Any] = self._default()
        self.load()

    def _default(self) -> Dict[str, Any]:
        return {
            "identity": "Elian",
            "system_name": "Elian Multi-Agent Runtime",
            "version": "2.0.0-ma",
            "language": "en-US",
            "mode": SystemMode.NORMAL.value,
            "health": HealthState.HEALTHY.value,
            "voice_state": VoiceUIState.IDLE.value,
            "current_focus": "",
            "active_agents": [],
            "available_capabilities": [],
            "active_tasks": [],
            "resource_state": {},
            "known_limitations": [],
            "recent_failures": [],
            "recent_successes": [],
            "development": {
                "active": False,
                "cycle": 0,
                "phase": "STOPPED",
                "candidate": "",
                "last_improvement": "",
                "last_rollback": "",
            },
            "installed_tools": [],
            "disabled_tools": [],
            "available_models": [],
            "voice_engine": "",
            "memory_state": {"scopes": ["GLOBAL", "USER", "SESSION", "TASK", "AGENT"]},
            "runtime_state": {},
            "updated_at": "",
        }

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return self.snapshot()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                with self._lock:
                    base = self._default()
                    base.update(raw)
                    self._state = base
        except Exception:
            pass
        return self.snapshot()

    def save(self) -> None:
        with self._lock:
            self._state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            payload = json.dumps(self._state, ensure_ascii=False, indent=2) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".self_model_", suffix=".tmp", dir=str(self.path.parent))
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

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._state))

    def update(self, **kwargs: Any) -> Dict[str, Any]:
        with self._lock:
            for key, value in kwargs.items():
                if key == "development" and isinstance(value, dict):
                    dev = dict(self._state.get("development") or {})
                    dev.update(value)
                    self._state["development"] = dev
                else:
                    self._state[key] = value
        self.save()
        return self.snapshot()

    def set_agents(self, agents: List[dict]) -> None:
        self.update(active_agents=list(agents))

    def set_voice_state(self, state: str) -> None:
        self.update(voice_state=str(state))

    def set_development(self, **kwargs: Any) -> None:
        self.update(development=kwargs)

    def note_success(self, summary: str) -> None:
        with self._lock:
            rows = list(self._state.get("recent_successes") or [])
            rows.append({"ts": time.time(), "summary": str(summary)[:200]})
            self._state["recent_successes"] = rows[-20:]
        self.save()

    def note_failure(self, summary: str) -> None:
        with self._lock:
            rows = list(self._state.get("recent_failures") or [])
            rows.append({"ts": time.time(), "summary": str(summary)[:200]})
            self._state["recent_failures"] = rows[-20:]
        self.save()

    def add_limitation(self, limitation: str) -> None:
        with self._lock:
            lims = list(self._state.get("known_limitations") or [])
            text = str(limitation or "").strip()
            if text and text not in lims:
                lims.append(text)
            self._state["known_limitations"] = lims[-40:]
        self.save()

    def describe(self) -> str:
        """English human-readable self-description from real state."""
        s = self.snapshot()
        agents = s.get("active_agents") or []
        working = [a for a in agents if str(a.get("status") or "") == "WORKING"]
        idle = [a for a in agents if str(a.get("status") or "") == "IDLE"]
        dev = s.get("development") or {}
        lines = [
            f"I am {s.get('identity') or 'Elian'}.",
            f"System health: {s.get('health')}. Mode: {s.get('mode')}. Language: {s.get('language')}.",
            f"Voice: {s.get('voice_state')} via {s.get('voice_engine') or 'fallback'}.",
            f"Focus: {s.get('current_focus') or 'idle'}.",
            f"Agents: {len(agents)} total — {len(working)} working, {len(idle)} idle.",
        ]
        if working:
            lines.append(
                "Working: "
                + "; ".join(
                    f"{a.get('agent_type')}#{a.get('agent_id','')[-4:]} ({a.get('task') or 'task'})"
                    for a in working[:6]
                )
            )
        if dev.get("active"):
            lines.append(
                f"Self-development: cycle {dev.get('cycle')} phase {dev.get('phase')} "
                f"candidate={dev.get('candidate') or '-'}."
            )
        elif dev.get("last_improvement"):
            lines.append(f"Last retained improvement: {dev.get('last_improvement')}.")
        lims = s.get("known_limitations") or []
        if lims:
            lines.append("Known limitations: " + "; ".join(lims[:5]) + ".")
        return " ".join(lines)
