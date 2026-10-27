# Shared OS-volume simulator for compound volume verify tests (P0 verify stays fail-closed).
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def simulated_system_volume(*, start: float = 50.0, step: float = 5.0):
    """Make Music.run change a fake master volume so plan_cycle verify sees a delta."""
    state = {"level": float(start)}

    def _read():
        level = float(state["level"])
        return {
            "level": level,
            "scalar": level / 100.0,
            "mute": False,
            "step": 10,
            "steps": 50,
        }

    def _music(cmd):
        low = str(cmd or "").lower()
        if "ciszej" in low:
            state["level"] = max(0.0, state["level"] - step)
        elif "głośniej" in low or "glosniej" in low:
            state["level"] = min(100.0, state["level"] + step)
        return "ok"

    with patch("core.builtin_tools._read_system_volume", side_effect=_read), patch(
        "core.builtin_tools.Music.run", side_effect=_music
    ):
        yield state
