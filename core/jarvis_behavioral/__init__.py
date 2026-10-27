# ==========================
# ELIAN — Jarvis Behavioral Gate (P2)
# ==========================
"""Full-pipeline behavioral gate — not unittest discovery.

Usage:
  python main.py --jarvis-behavioral
  python main.py --jarvis-behavioral --critical
  python main.py --jarvis-behavioral --multi-turn
  python main.py --jarvis-behavioral --poison
"""

from __future__ import annotations

from core.jarvis_behavioral.runner import run_jarvis_behavioral, run_jarvis_explore

__all__ = ["run_jarvis_behavioral", "run_jarvis_explore"]
