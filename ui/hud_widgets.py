# ==========================
# ELIAN — HUD orb + small widgets (GUI 2.0)
# ==========================
"""Pure view helpers. No ConversationManager / ToolRegistry / planner."""

from __future__ import annotations

from typing import Callable, Optional

from core.hud_events import HudSituation
from ui.hud_theme import PULSE_STATES, STATE_COLORS


class HudOrb:
    """Animated status orb. Idle uses a slow tick to avoid 60 FPS waste."""

    def __init__(self, parent, *, size: int = 72, animations: bool = True):
        from PySide6.QtCore import QTimer, Qt
        from PySide6.QtGui import QColor, QPainter, QBrush, QRadialGradient, QPen
        from PySide6.QtWidgets import QWidget

        self._QColor = QColor
        self._QPainter = QPainter
        self._QBrush = QBrush
        self._QRadialGradient = QRadialGradient
        self._QPen = QPen
        self._Qt = Qt
        self._color = QColor("#1a6a7a")
        self._pulse = False
        self._phase = 0.0
        self._animations = bool(animations)
        self._size = int(size)
        self.widget = QWidget(parent)
        self.widget.setFixedSize(self._size, self._size)
        self.widget.setObjectName("HudOrb")

        def paint(_event):
            painter = QPainter(self.widget)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            s = self._size
            cx = cy = s / 2
            radius = s * 0.34
            alpha = 220
            glow = 0.4
            if self._pulse and self._animations:
                import math

                wave = 0.5 + 0.5 * math.sin(self._phase)
                alpha = int(160 + 80 * wave)
                glow = 0.3 + 0.35 * wave
                radius = s * (0.32 + 0.05 * wave)
            color = QColor(self._color)
            # Outer holographic ring
            ring = QColor(color)
            ring.setAlpha(int(120 + 80 * glow))
            painter.setPen(QPen(ring, max(1.5, s * 0.03)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(int(s * 0.08), int(s * 0.08), int(s * 0.84), int(s * 0.84))
            # Soft outer glow
            grad = QRadialGradient(cx, cy, s * 0.5)
            glow_c = QColor(color)
            glow_c.setAlpha(int(100 * glow))
            core = QColor(color)
            core.setAlpha(alpha)
            white = QColor(200, 255, 255, int(40 + 40 * glow))
            grad.setColorAt(0.0, white)
            grad.setColorAt(0.35, core)
            grad.setColorAt(0.7, glow_c)
            grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(grad))
            painter.drawEllipse(0, 0, s, s)
            # Inner core
            painter.setBrush(QBrush(core))
            painter.drawEllipse(
                int(cx - radius),
                int(cy - radius),
                int(radius * 2),
                int(radius * 2),
            )
            painter.end()

        self.widget.paintEvent = paint  # type: ignore[method-assign]
        self._timer = QTimer(self.widget)
        self._timer.setInterval(50 if self._animations else 200)
        self._timer.timeout.connect(self._tick)
        self._click_handler: Optional[Callable[[], None]] = None

        def mouse_press(event):
            if event.button() == Qt.MouseButton.LeftButton and self._click_handler:
                self._click_handler()
            event.accept()

        self.widget.mousePressEvent = mouse_press  # type: ignore[method-assign]

    def set_click_handler(self, handler: Optional[Callable[[], None]]) -> None:
        self._click_handler = handler

    def set_animations(self, enabled: bool) -> None:
        self._animations = bool(enabled)
        if not self._animations:
            self._timer.stop()
            self._phase = 0.0
            self.widget.update()
        elif self._pulse:
            self._timer.start()

    def _tick(self) -> None:
        self._phase += 0.28
        self.widget.update()

    def set_from_state(self, state) -> None:
        color = STATE_COLORS.get(state, "#3ecfff")
        pulse = state in PULSE_STATES
        self.set_color(color, pulse=pulse)
        # Slow pulse when idle-aware; stop timer fully when idle/muted static
        if pulse and self._animations:
            interval = 200 if state.value in ("AWARE",) else 110
            self._timer.setInterval(interval)
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._phase = 0.0
            self.widget.update()

    def set_color(self, hex_color: str, *, pulse: bool) -> None:
        self._color = self._QColor(hex_color)
        self._pulse = pulse
        if pulse and self._animations:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._phase = 0.0
        self.widget.update()


def build_situation_card(
    parent,
    situation: HudSituation,
    *,
    on_action: Optional[Callable[[str], None]] = None,
    on_dismiss: Optional[Callable[[str], None]] = None,
):
    """Build a proactive situation card. Callbacks are UI-local only."""
    from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

    card = QFrame(parent)
    card.setObjectName("HudCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(8, 6, 8, 6)
    layout.setSpacing(4)
    title = QLabel(situation.title)
    title.setObjectName("HudGoal")
    title.setWordWrap(True)
    layout.addWidget(title)
    if situation.body:
        body = QLabel(situation.body)
        body.setObjectName("HudNote")
        body.setWordWrap(True)
        layout.addWidget(body)
    row = QHBoxLayout()
    row.addStretch(1)
    act = QPushButton(situation.action_label)
    act.setObjectName("HudAction")
    act.clicked.connect(
        lambda _=False, sid=situation.id: on_action and on_action(sid)
    )
    row.addWidget(act)
    if situation.dismissible:
        dismiss = QPushButton("Odrzuć")
        dismiss.setObjectName("HudDismiss")
        dismiss.clicked.connect(
            lambda _=False, sid=situation.id: on_dismiss and on_dismiss(sid)
        )
        row.addWidget(dismiss)
    layout.addLayout(row)
    return card
