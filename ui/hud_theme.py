# ==========================
# ELIAN — HUD theme / labels (Jarvis holographic)
# ==========================
"""Visual tokens for the Elian desktop GUI. No backend imports beyond hud_events."""

from __future__ import annotations

from core.hud_events import HudDisplayState

STATE_LABELS = {
    HudDisplayState.IDLE: "STANDBY",
    HudDisplayState.AWARE: "AWARE",
    HudDisplayState.LISTENING: "LISTENING",
    HudDisplayState.THINKING: "ANALYZING",
    HudDisplayState.ACTING: "EXECUTING",
    HudDisplayState.SPEAKING: "SPEAKING",
    HudDisplayState.INTERRUPTED: "INTERRUPTED",
    HudDisplayState.MUTED: "MUTED",
    HudDisplayState.ERROR: "FAULT",
    HudDisplayState.OFFLINE: "OFFLINE",
    HudDisplayState.DEGRADED: "DEGRADED",
}

# Holographic cyan / amber — Jarvis-like, not chat-app purple.
STATE_COLORS = {
    HudDisplayState.IDLE: "#1a6a7a",
    HudDisplayState.AWARE: "#2ee6ff",
    HudDisplayState.LISTENING: "#00d4ff",
    HudDisplayState.THINKING: "#ffc94a",
    HudDisplayState.ACTING: "#4aa8ff",
    HudDisplayState.SPEAKING: "#3dffc0",
    HudDisplayState.INTERRUPTED: "#ff8a4a",
    HudDisplayState.MUTED: "#5a6570",
    HudDisplayState.ERROR: "#ff3d5a",
    HudDisplayState.OFFLINE: "#c9a227",
    HudDisplayState.DEGRADED: "#e0a020",
}

PULSE_STATES = frozenset(
    {
        HudDisplayState.AWARE,
        HudDisplayState.LISTENING,
        HudDisplayState.THINKING,
        HudDisplayState.ACTING,
        HudDisplayState.SPEAKING,
    }
)

MEMORY_KIND_LABELS = {
    "session": "Sesja",
    "candidate": "Kandydat",
    "persistent": "Trwała",
}


def integration_glyph(status: str) -> str:
    s = (status or "unknown").lower()
    if s in ("online", "ready", "ok"):
        return "●"
    if s == "degraded":
        return "◐"
    if s in ("offline", "error", "unavailable"):
        return "○"
    if s in ("disabled", "not_configured", "off"):
        return "–"
    return "·"


def stylesheet(*, opacity: float = 0.92) -> str:
    alpha = max(0.5, min(1.0, float(opacity)))
    panel_a = int(230 * alpha)
    return f"""
QFrame#HudPanel {{
    background-color: rgba(2, 8, 18, {panel_a});
    border: 1px solid rgba(0, 220, 255, 90);
    border-radius: 4px;
}}
QFrame#HudCard {{
    background-color: rgba(4, 20, 36, 210);
    border: 1px solid rgba(0, 200, 255, 55);
    border-radius: 2px;
}}
QLabel {{
    color: #9ef0ff;
    font-size: 11px;
    font-family: "Segoe UI", "Consolas", monospace;
    background: transparent;
}}
QLabel#HudBrand {{
    color: #00e8ff;
    font-size: 13px;
    letter-spacing: 6px;
    font-weight: 700;
}}
QLabel#HudState {{
    color: #e8ffff;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 3px;
}}
QLabel#HudSection {{
    color: #3ad4ff;
    font-size: 10px;
    letter-spacing: 2px;
    font-weight: 600;
}}
QLabel#HudMuted {{
    color: #5a90a0;
    font-size: 10px;
}}
QLabel#HudTranscript {{
    color: #c8f8ff;
    font-size: 12px;
}}
QLabel#HudResponse {{
    color: #7affd4;
    font-size: 12px;
}}
QLabel#HudThought {{
    color: #6ad0e8;
    font-size: 10px;
    font-style: italic;
}}
QLabel#HudUser {{
    color: #c8f8ff;
    font-size: 12px;
}}
QLabel#HudDot {{
    color: #00e8ff;
    font-size: 14px;
}}
QProgressBar {{
    background: rgba(0, 40, 60, 160);
    border: 1px solid rgba(0, 180, 220, 80);
    border-radius: 1px;
    max-height: 6px;
}}
QProgressBar::chunk {{
    background-color: #00d4ff;
}}
"""
