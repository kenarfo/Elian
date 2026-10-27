# ==========================
# ELIAN 2.0 — single version source (Phase 9)
# ==========================

from __future__ import annotations

__version__ = "2.0.0-rc1"
VERSION = __version__


def version_banner() -> str:
    return f"Elian {VERSION}"
