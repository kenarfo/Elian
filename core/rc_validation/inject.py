# ==========================
# ELIAN — RC text inject (debug / hardware only)
# ==========================
"""Bypass mic/VAD/STT/wake extraction. Canonical entry remains ConversationManager.handle.

Not a user intent. Not a second planner/tool path.
"""

from __future__ import annotations

from typing import Tuple

from Diagnostics import log_turn


NOTEPAD_STAGE_C_PHRASE = (
    "Otwórz Notatnik, wpisz Elian działa jak agent i zaznacz cały tekst."
)


def parse_rc_inject_line(line: str) -> Tuple[bool, str]:
    """Parse stdin `rc inject <text>`.

    Returns (is_inject_command, payload).
    Payload is empty when the command is `rc inject` without text.
    """
    raw = str(line or "").strip()
    if not raw:
        return False, ""
    parts = raw.split(maxsplit=2)
    if len(parts) < 2:
        return False, ""
    if parts[0].lower() != "rc" or parts[1].lower() != "inject":
        return False, ""
    payload = parts[2] if len(parts) >= 3 else ""
    payload = str(payload).strip()
    if len(payload) >= 2 and payload[0] == payload[-1] and payload[0] in ("'", '"'):
        payload = payload[1:-1].strip()
    return True, payload


def log_rc_inject(text: str) -> None:
    print("[RC INJECT]")
    print(f"text={text}")
    print("bypass=VAD,STT,WAKE")
    print("entry=ConversationManager.handle")
    log_turn(
        "rc_inject",
        text=str(text or "")[:200],
        bypass="VAD,STT,WAKE",
        entry="ConversationManager.handle",
    )


def prepare_rc_inject_session(manager) -> None:
    """Simulate post-wake session without wake extraction / STT."""
    if manager is not None and not getattr(manager, "active", False):
        manager.activate()


def dispatch_rc_inject(manager, text: str):
    """RC debug entry: activate if idle, then canonical ConversationManager.handle."""
    payload = str(text or "").strip()
    log_rc_inject(payload)
    prepare_rc_inject_session(manager)
    return manager.handle(payload)
