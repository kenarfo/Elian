# ==========================
# ELIAN — desktop GUI window (GUI 2.0)
# ==========================
"""PySide6 view only. No ConversationManager, planner, or ToolRegistry.

Surfaces: orb, status, transcript, thought, goal/plan, world, proactive cards,
memory toast, notifications, integrations, mini↔full, optional DEV VIEW.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, List, Optional

from core.hud_events import (
    HudSnapshot,
    emit_dismiss_situation,
    status_label_pl,
)
from ui.hud_theme import (
    MEMORY_KIND_LABELS,
    STATE_LABELS,
    integration_glyph,
    stylesheet,
)
from ui.hud_widgets import HudOrb, build_situation_card


class HudWindow:
    """Full / mini / compact observer window. Imported only after PySide6 OK."""

    def __init__(
        self,
        *,
        always_on_top: bool = True,
        compact: bool = False,
        mini: bool = False,
        opacity: float = 0.94,
        animations: bool = True,
        dev_view: bool = False,
    ):
        from PySide6.QtCore import Qt, QPoint
        from PySide6.QtWidgets import (
            QFrame,
            QHBoxLayout,
            QLabel,
            QVBoxLayout,
            QWidget,
        )

        self._Qt = Qt
        self._QPoint = QPoint

        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint

        self.widget = QWidget()
        self.widget.setObjectName("HudRoot")
        self.widget.setWindowTitle("Elian")
        self.widget.setWindowFlags(flags)
        self.widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._always_on_top = always_on_top
        self._compact = bool(compact)
        self._mini = bool(mini)
        self._opacity = float(opacity)
        self._animations = bool(animations)
        self._dev_view = bool(dev_view)
        self._drag_pos: Optional[QPoint] = None
        self.snapshot = HudSnapshot()
        self._on_close = None
        self._on_expand: Optional[Callable[[], None]] = None
        self._on_situation_action: Optional[Callable[[str], None]] = None
        self._situation_widgets: List = []

        root = QVBoxLayout(self.widget)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(0)

        self._panel = QFrame(self.widget)
        self._panel.setObjectName("HudPanel")
        self._panel_layout = QVBoxLayout(self._panel)
        self._panel_layout.setContentsMargins(14, 12, 14, 12)
        self._panel_layout.setSpacing(6)

        # Brand + orb + state
        self._brand = QLabel("ELIAN  ·  CORE")
        self._brand.setObjectName("HudBrand")
        self._panel_layout.addWidget(self._brand)

        top = QHBoxLayout()
        self._top_layout = top
        self._orb = HudOrb(self._panel, size=72, animations=self._animations)
        self._orb.set_click_handler(self._on_orb_click)
        self._state = QLabel("STANDBY")
        self._state.setObjectName("HudState")
        self._conf = QLabel("")
        self._conf.setObjectName("HudDot")
        top.addWidget(self._orb.widget, 0)
        col = QVBoxLayout()
        self._state_col = col
        col.addWidget(self._state)
        self._thought = QLabel("")
        self._thought.setObjectName("HudThought")
        self._thought.setWordWrap(True)
        col.addWidget(self._thought)
        top.addLayout(col, 1)
        top.addWidget(self._conf, 0)
        self._panel_layout.addLayout(top)

        # Live transcript (last utterance only)
        self._sec_user = QLabel("UTTERANCE")
        self._sec_user.setObjectName("HudSection")
        self._user = QLabel("")
        self._user.setObjectName("HudUser")
        self._user.setWordWrap(True)
        self._panel_layout.addWidget(self._sec_user)
        self._panel_layout.addWidget(self._user)

        # Active goal / plan
        self._sec_goal = QLabel("MISSION")
        self._sec_goal.setObjectName("HudSection")
        self._goal = QLabel("")
        self._goal.setObjectName("HudGoal")
        self._goal.setWordWrap(True)
        self._plan_box = QVBoxLayout()
        self._plan_box.setSpacing(2)
        self._panel_layout.addWidget(self._sec_goal)
        self._panel_layout.addWidget(self._goal)
        self._panel_layout.addLayout(self._plan_box)
        self._plan_labels: List[QLabel] = []

        # World snapshot
        self._sec_world = QLabel("ŚWIAT")
        self._sec_world.setObjectName("HudSection")
        self._world = QLabel("")
        self._world.setObjectName("HudWorld")
        self._world.setWordWrap(True)
        self._panel_layout.addWidget(self._sec_world)
        self._panel_layout.addWidget(self._world)

        # Proactive cards
        self._sec_sit = QLabel("PROAKTYWNE")
        self._sec_sit.setObjectName("HudSection")
        self._sit_host = QVBoxLayout()
        self._sit_host.setSpacing(4)
        self._panel_layout.addWidget(self._sec_sit)
        self._panel_layout.addLayout(self._sit_host)

        # Memory toast
        self._memory = QLabel("")
        self._memory.setObjectName("HudMemory")
        self._memory.hide()
        self._panel_layout.addWidget(self._memory)

        # Notifications (compact history)
        self._sec_notes = QLabel("POWIADOMIENIA")
        self._sec_notes.setObjectName("HudSection")
        self._notes = QLabel("")
        self._notes.setObjectName("HudNote")
        self._notes.setWordWrap(True)
        self._panel_layout.addWidget(self._sec_notes)
        self._panel_layout.addWidget(self._notes)

        # Reply (spoken, not a chat thread)
        self._reply = QLabel("")
        self._reply.setObjectName("HudReply")
        self._reply.setWordWrap(True)
        self._panel_layout.addWidget(self._reply)

        # Integrations
        dots = QHBoxLayout()
        self._integ_labels = {}
        for key, caption in (
            ("ollama", "Ollama"),
            ("whisper", "Whisper"),
            ("piper", "Piper"),
            ("ha", "HA"),
            ("browser", "Browser"),
            ("phone", "Phone"),
            ("voice", "Voice"),
        ):
            lab = QLabel(f"· {caption}")
            lab.setObjectName("HudDot")
            self._integ_labels[key] = lab
            dots.addWidget(lab)
        dots.addStretch(1)
        self._panel_layout.addLayout(dots)

        # DEV VIEW
        self._dev = QLabel("")
        self._dev.setObjectName("HudDev")
        self._dev.setWordWrap(True)
        self._panel_layout.addWidget(self._dev)

        root.addWidget(self._panel)
        self.widget.setStyleSheet(stylesheet(opacity=self._opacity))
        self._apply_mode()
        self._install_drag()
        self.render(self.snapshot)

    # --- mode / chrome -------------------------------------------------

    def _on_orb_click(self) -> None:
        if self._mini:
            self.set_mini(False)
            if self._on_expand:
                try:
                    self._on_expand()
                except Exception:
                    pass

    def set_expand_handler(self, handler: Optional[Callable[[], None]]) -> None:
        self._on_expand = handler

    def set_situation_action_handler(
        self, handler: Optional[Callable[[str], None]]
    ) -> None:
        """UI-local only — must not invoke ToolRegistry."""
        self._on_situation_action = handler

    def _apply_mode(self) -> None:
        if self._mini:
            self.widget.setFixedSize(88, 88)
            self._brand.hide()
            for w in (
                self._state,
                self._thought,
                self._sec_user,
                self._user,
                self._sec_goal,
                self._goal,
                self._sec_world,
                self._world,
                self._sec_sit,
                self._memory,
                self._sec_notes,
                self._notes,
                self._reply,
                self._dev,
                self._conf,
            ):
                w.hide()
            for lab in self._integ_labels.values():
                lab.hide()
            self._clear_plan_labels()
            self._clear_situations()
            self._panel_layout.setContentsMargins(18, 18, 18, 18)
            self._top_layout.setStretch(0, 1)
            self._top_layout.setStretch(1, 0)
            self._top_layout.setStretch(2, 0)
            return

        self._brand.show()
        self._state.show()
        self._thought.show()
        self._conf.show()
        for lab in self._integ_labels.values():
            lab.show()
        self._panel_layout.setContentsMargins(14, 12, 14, 12)
        self._top_layout.setStretch(0, 0)
        self._top_layout.setStretch(1, 1)
        self._top_layout.setStretch(2, 0)

        if self._compact:
            self.widget.setFixedSize(280, 120)
            for w in (
                self._sec_user,
                self._user,
                self._sec_goal,
                self._goal,
                self._sec_world,
                self._world,
                self._sec_sit,
                self._memory,
                self._sec_notes,
                self._notes,
                self._reply,
                self._dev,
            ):
                w.hide()
            self._clear_plan_labels()
            self._clear_situations()
        else:
            self.widget.setFixedSize(360, 520)
            for w in (
                self._sec_user,
                self._user,
                self._sec_goal,
                self._goal,
                self._sec_world,
                self._world,
                self._sec_sit,
                self._sec_notes,
                self._notes,
                self._reply,
            ):
                w.show()
            if self._dev_view:
                self._dev.show()
            else:
                self._dev.hide()

    def set_compact(self, compact: bool) -> None:
        self._compact = bool(compact)
        if self._compact:
            self._mini = False
        self._apply_mode()
        self.render(self.snapshot)

    def set_mini(self, mini: bool) -> None:
        self._mini = bool(mini)
        if self._mini:
            self._compact = False
        self._apply_mode()
        self.render(self.snapshot)

    def set_always_on_top(self, enabled: bool) -> None:
        from PySide6.QtCore import Qt

        self._always_on_top = bool(enabled)
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self._always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.widget.setWindowFlags(flags)
        self.widget.show()

    def set_opacity(self, opacity: float) -> None:
        self._opacity = max(0.5, min(1.0, float(opacity)))
        self.widget.setStyleSheet(stylesheet(opacity=self._opacity))

    def set_animations(self, enabled: bool) -> None:
        self._animations = bool(enabled)
        self._orb.set_animations(self._animations)

    def set_dev_view(self, enabled: bool) -> None:
        self._dev_view = bool(enabled)
        self._apply_mode()
        self.render(self.snapshot)

    def set_close_handler(self, handler) -> None:
        self._on_close = handler

        def _close(event):
            if self._on_close:
                self._on_close()
                event.ignore()
            else:
                event.ignore()
                self.widget.hide()

        self.widget.closeEvent = _close  # type: ignore[method-assign]

    def _install_drag(self) -> None:
        Qt = self._Qt
        widget = self.widget

        def press(event):
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_pos = event.globalPosition().toPoint() - widget.frameGeometry().topLeft()
            event.accept()

        def move(event):
            if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
                widget.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

        def release(event):
            self._drag_pos = None
            event.accept()

        widget.mousePressEvent = press  # type: ignore[method-assign]
        widget.mouseMoveEvent = move  # type: ignore[method-assign]
        widget.mouseReleaseEvent = release  # type: ignore[method-assign]

    # --- plan / situations ---------------------------------------------

    def _clear_plan_labels(self) -> None:
        from PySide6.QtWidgets import QLabel

        while self._plan_labels:
            lab = self._plan_labels.pop()
            self._plan_box.removeWidget(lab)
            lab.deleteLater()

    def _clear_situations(self) -> None:
        while self._situation_widgets:
            w = self._situation_widgets.pop()
            self._sit_host.removeWidget(w)
            w.deleteLater()

    def _rebuild_plan(self, snap: HudSnapshot) -> None:
        self._clear_plan_labels()
        if self._mini or self._compact:
            return
        from PySide6.QtWidgets import QLabel

        for step in snap.plan_steps:
            mark = "✓" if step.done else "○"
            lab = QLabel(f"{mark}  {step.label}")
            lab.setObjectName("HudStepDone" if step.done else "HudStep")
            self._plan_box.addWidget(lab)
            self._plan_labels.append(lab)

    def _rebuild_situations(self, snap: HudSnapshot) -> None:
        self._clear_situations()
        if self._mini or self._compact or not snap.proactive_enabled:
            return

        def _local_dismiss(sid: str) -> None:
            emit_dismiss_situation(sid)
            try:
                self.snapshot = replace(
                    self.snapshot,
                    situations=tuple(
                        s for s in self.snapshot.situations if s.id != sid
                    ),
                )
                self._rebuild_situations(self.snapshot)
                if not self.snapshot.situations:
                    self._sec_sit.hide()
            except Exception:
                pass

        def _action(sid: str) -> None:
            if self._on_situation_action:
                try:
                    self._on_situation_action(sid)
                except Exception:
                    pass
            # Local dismiss after action ack (GUI does not execute tools)
            _local_dismiss(sid)

        def _dismiss(sid: str) -> None:
            _local_dismiss(sid)

        for sit in snap.situations:
            card = build_situation_card(
                self._panel,
                sit,
                on_action=_action,
                on_dismiss=_dismiss,
            )
            self._sit_host.addWidget(card)
            self._situation_widgets.append(card)

    # --- render --------------------------------------------------------

    def render(self, snap: HudSnapshot) -> None:
        self.snapshot = snap
        self._orb.set_from_state(snap.state)
        label = status_label_pl(snap.state, snap)
        if snap.state.value in STATE_LABELS and not snap.status_text:
            label = STATE_LABELS.get(snap.state, label)
        self._state.setText(label)

        if snap.confidence is not None:
            self._conf.setText(f"{snap.confidence:.2f}")
        else:
            self._conf.setText("")

        if self._mini:
            return

        self._thought.setText(snap.thought or "")
        self._user.setText(snap.transcript or "")
        self._reply.setText(snap.reply or "")

        if snap.goal_title:
            self._goal.setText(snap.goal_title)
            self._sec_goal.show()
            self._goal.show()
        else:
            self._goal.setText("")
            if not self._compact:
                self._sec_goal.hide()
                self._goal.hide()

        self._rebuild_plan(snap)

        if snap.world:
            parts = [f"{k}: {v}" for k, v in snap.world.items() if v]
            self._world.setText("  ·  ".join(parts))
            self._sec_world.show()
            self._world.show()
        else:
            self._world.setText("")
            if not self._compact:
                self._sec_world.hide()
                self._world.hide()

        self._rebuild_situations(snap)
        if snap.situations and snap.proactive_enabled and not self._compact:
            self._sec_sit.show()
        else:
            self._sec_sit.hide()

        if snap.memory_hint and snap.memory_kind:
            kind_pl = MEMORY_KIND_LABELS.get(snap.memory_kind, snap.memory_kind)
            self._memory.setText(f"{kind_pl}: {snap.memory_hint}")
            self._memory.show()
        else:
            self._memory.hide()

        if snap.notifications:
            lines = []
            for note in snap.notifications[:5]:
                lines.append(f"• {note.title}")
            self._notes.setText("\n".join(lines))
            self._sec_notes.show()
            self._notes.show()
        else:
            self._notes.setText("")
            self._sec_notes.hide()
            self._notes.hide()

        for key, lab in self._integ_labels.items():
            status = getattr(snap, key, "unknown")
            caption = lab.text().split(" ", 1)[-1] if " " in lab.text() else key
            # Stable captions
            captions = {
                "ollama": "Ollama",
                "whisper": "Whisper",
                "piper": "Piper",
                "ha": "HA",
                "browser": "Browser",
                "phone": "Phone",
                "voice": "Voice",
            }
            lab.setText(f"{integration_glyph(status)} {captions.get(key, caption)}")

        if self._dev_view and not self._compact:
            parts = [
                f"family={snap.intent_family or '—'}",
                f"tool={snap.selected_tool or snap.tool or '—'}",
                f"lat={snap.latency_ms if snap.latency_ms is not None else '—'}ms",
                f"world_age={snap.world_freshness_s if snap.world_freshness_s is not None else '—'}",
                f"session={snap.session_id or '—'}",
                f"phase={snap.planner_phase or '—'}",
                f"err={snap.last_error_dev or '—'}",
            ]
            self._dev.setText("DEV  " + " | ".join(parts))
            self._dev.show()
        else:
            self._dev.hide()

    def show(self) -> None:
        self.widget.show()

    def hide(self) -> None:
        self.widget.hide()

    def is_visible(self) -> bool:
        return bool(self.widget.isVisible())

    @property
    def is_mini(self) -> bool:
        return self._mini
