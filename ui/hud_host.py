# ==========================
# ELIAN — HUD host / crash isolation (Phase 13 / GUI 2.0)
# ==========================
"""Runs the HUD on a daemon thread. GUI crashes must not kill voice.

Observer only: does not own ConversationManager, SpeechStateMachine, or ToolRegistry.
Stopping the HUD never stops the Elian runtime.
Closing the window hides to tray by default — does not shut down Elian.
"""

from __future__ import annotations

import os
import sys
import threading
import traceback
from typing import Any, Callable, Optional

from Config import DATA_DIR, get_config_value
from core.hud_events import (
    HudSnapshot,
    apply_event,
    emit_error,
    emit_integration,
    emit_state_changed,
    get_hud_queue,
)


def hud_enabled_from_cli_and_config() -> bool:
    if "--no-hud" in sys.argv:
        return False
    if "--hud" in sys.argv:
        return True
    return bool(get_config_value("hud", "enabled", default=False))


def _safe_ui(fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception as exc:
        print(f"[HUD] UI action failed (ignored): {exc}")


class HudHost:
    """Owns the Qt event loop on a side thread. Core stays Qt-free."""

    def __init__(
        self,
        *,
        pipeline: Any = None,
        runtime: Any = None,
        run_fn: Optional[Callable[[], None]] = None,
        always_on_top: Optional[bool] = None,
        compact: Optional[bool] = None,
        hide_to_tray: Optional[bool] = None,
        mini: Optional[bool] = None,
        opacity: Optional[float] = None,
        animations: Optional[bool] = None,
        dev_view: Optional[bool] = None,
        world_throttle_ms: Optional[int] = None,
        on_request_shutdown: Optional[Callable[[], None]] = None,
    ):
        self.pipeline = pipeline
        self.runtime = runtime
        self._run_fn = run_fn
        self._always_on_top = (
            bool(get_config_value("hud", "always_on_top", default=True))
            if always_on_top is None
            else bool(always_on_top)
        )
        self._compact = (
            bool(get_config_value("hud", "compact", default=False))
            if compact is None
            else bool(compact)
        )
        self._hide_to_tray = (
            bool(get_config_value("hud", "hide_to_tray", default=True))
            if hide_to_tray is None
            else bool(hide_to_tray)
        )
        self._mini = (
            bool(get_config_value("hud", "mini", default=False))
            if mini is None
            else bool(mini)
        )
        self._opacity = (
            float(get_config_value("hud", "opacity", default=0.94))
            if opacity is None
            else float(opacity)
        )
        self._animations = (
            bool(get_config_value("hud", "animations", default=True))
            if animations is None
            else bool(animations)
        )
        self._dev_view = (
            bool(get_config_value("hud", "dev_view", default=False))
            if dev_view is None
            else bool(dev_view)
        )
        self._world_throttle_s = (
            max(0.0, float(get_config_value("hud", "world_update_ms", default=500)) / 1000.0)
            if world_throttle_ms is None
            else max(0.0, float(world_throttle_ms) / 1000.0)
        )
        self._proactive_enabled = bool(
            get_config_value("hud", "proactive_enabled", default=True)
        )
        self._muted = False
        self._on_request_shutdown = on_request_shutdown
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.crashed = False
        self.crash_exc: Optional[BaseException] = None
        self.snapshot = HudSnapshot(proactive_enabled=self._proactive_enabled)
        self._window = None
        self._state_listener = None
        self._bus_handler = None
        self.render_errors = 0
        self._idle_skip = 0  # paint throttle when idle

    def start(self) -> None:
        """Idempotent. A crashed/stopped host may be started again."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.crashed = False
        self.crash_exc = None
        self.render_errors = 0
        self._thread = threading.Thread(
            target=self._run_isolated,
            name="elian-hud",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop HUD only. Never calls runtime.stop / pipeline.close."""
        self._stop.set()
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)
        self._detach_observers()
        self._window = None

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run_isolated(self) -> None:
        try:
            if self._run_fn is not None:
                self._run_fn()
            else:
                self._run_qt()
        except Exception as exc:
            self.crashed = True
            self.crash_exc = exc
            print(f"[HUD] crashed - voice continues: {exc}")
            if self._run_fn is None:
                traceback.print_exc()
                try:
                    from core.crash_log import write_runtime_crash

                    write_runtime_crash(exc, thread_name="elian-hud")
                except Exception:
                    pass
        finally:
            self._detach_observers()
            self._window = None

    def _run_qt(self) -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QAction, QIcon, QPixmap, QColor, QPainter
        from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

        from ui.hud_window import HudWindow

        app = QApplication.instance()
        if app is None:
            app = QApplication(["elian-hud"])
        app.setQuitOnLastWindowClosed(False)

        window = HudWindow(
            always_on_top=self._always_on_top,
            compact=self._compact,
            mini=self._mini,
            opacity=self._opacity,
            animations=self._animations,
            dev_view=self._dev_view,
        )
        self._window = window

        def _on_window_close() -> None:
            try:
                if self._hide_to_tray:
                    window.hide()
                else:
                    self._stop.set()
            except Exception:
                self._stop.set()

        window.set_close_handler(_on_window_close)
        window.show()

        self._attach_observers()
        self._seed_status()

        tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            pix = QPixmap(32, 32)
            pix.fill(QColor(0, 0, 0, 0))
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setBrush(QColor("#3ecfff"))
            painter.setPen(QColor("#0a1628"))
            painter.drawEllipse(4, 4, 24, 24)
            painter.end()
            tray = QSystemTrayIcon(QIcon(pix))
            menu = QMenu()

            show_act = QAction("Pokaż GUI")
            show_act.triggered.connect(
                lambda: _safe_ui(lambda: (window.set_mini(False), window.show()))
            )
            hide_act = QAction("Ukryj GUI")
            hide_act.triggered.connect(lambda: _safe_ui(window.hide))

            mini_act = QAction("Tryb mini (orb)")
            mini_act.setCheckable(True)
            mini_act.setChecked(self._mini)

            def _toggle_mini(checked: bool) -> None:
                self._mini = bool(checked)
                _safe_ui(lambda: window.set_mini(self._mini))

            mini_act.triggered.connect(_toggle_mini)

            top_act = QAction("Zawsze na wierzchu")
            top_act.setCheckable(True)
            top_act.setChecked(self._always_on_top)

            def _toggle_top(checked: bool) -> None:
                self._always_on_top = bool(checked)
                _safe_ui(lambda: window.set_always_on_top(self._always_on_top))

            top_act.triggered.connect(_toggle_top)

            compact_act = QAction("Tryb kompaktowy")
            compact_act.setCheckable(True)
            compact_act.setChecked(self._compact)

            def _toggle_compact(checked: bool) -> None:
                self._compact = bool(checked)
                _safe_ui(lambda: window.set_compact(self._compact))

            compact_act.triggered.connect(_toggle_compact)

            mute_act = QAction("Wycisz (HUD)")
            mute_act.setCheckable(True)
            mute_act.setChecked(self._muted)

            def _toggle_mute(checked: bool) -> None:
                self._muted = bool(checked)
                try:
                    emit_state_changed(
                        self.snapshot.speech_name or "IDLE",
                        muted=self._muted,
                    )
                except Exception:
                    pass

            mute_act.triggered.connect(_toggle_mute)

            proactive_act = QAction("Proaktywne karty")
            proactive_act.setCheckable(True)
            proactive_act.setChecked(self._proactive_enabled)

            def _toggle_proactive(checked: bool) -> None:
                self._proactive_enabled = bool(checked)
                self.snapshot.proactive_enabled = self._proactive_enabled
                try:
                    emit_state_changed(
                        self.snapshot.speech_name or "IDLE",
                        proactive_enabled=self._proactive_enabled,
                    )
                except Exception:
                    pass
                _safe_ui(lambda: window.render(self.snapshot))

            proactive_act.triggered.connect(_toggle_proactive)

            status_menu = QMenu("Status integracji")
            self._tray_status_actions = {}
            for key, title in (
                ("ollama", "Ollama"),
                ("whisper", "Whisper"),
                ("piper", "Piper"),
                ("ha", "HA"),
                ("browser", "Browser"),
                ("phone", "Phone"),
            ):
                act = QAction(f"{title}: —")
                act.setEnabled(False)
                status_menu.addAction(act)
                self._tray_status_actions[key] = act

            logs_act = QAction("Otwórz logi")
            logs_act.triggered.connect(lambda: _safe_ui(_open_logs))

            # Tray "Zamknij GUI" stops GUI only — runtime keeps running.
            close_act = QAction("Zamknij GUI")
            close_act.triggered.connect(lambda: self._stop.set())

            shutdown_act = QAction("Wyłącz Elian…")
            if self._on_request_shutdown is not None:

                def _shutdown() -> None:
                    try:
                        self._on_request_shutdown()
                    except Exception as exc:
                        print(f"[HUD] shutdown request failed: {exc}")

                shutdown_act.triggered.connect(_shutdown)
            else:
                shutdown_act.setEnabled(False)
                shutdown_act.setToolTip("Niedostępne w tej sesji")

            menu.addAction(show_act)
            menu.addAction(hide_act)
            menu.addAction(mini_act)
            menu.addSeparator()
            menu.addAction(top_act)
            menu.addAction(compact_act)
            menu.addAction(mute_act)
            menu.addAction(proactive_act)
            menu.addMenu(status_menu)
            menu.addSeparator()
            menu.addAction(logs_act)
            menu.addAction(close_act)
            menu.addSeparator()
            menu.addAction(shutdown_act)
            tray.setContextMenu(menu)
            tray.setToolTip("Elian")
            tray.activated.connect(
                lambda reason: _safe_ui(window.show)
                if reason == QSystemTrayIcon.ActivationReason.Trigger
                else None
            )
            tray.show()
            self._tray = tray
            self._tray_status_menu = status_menu
        else:
            self._tray = None
            self._tray_status_actions = {}

        # 50 ms tick with idle coalescing — avoid busy paint when nothing changes
        timer = QTimer()
        timer.setInterval(50)

        def _tick() -> None:
            if self._stop.is_set():
                try:
                    window.hide()
                except Exception:
                    pass
                app.quit()
                return
            self._drain_into_window()

        timer.timeout.connect(_tick)
        timer.start()
        try:
            app.exec()
        finally:
            timer.stop()
            if tray is not None:
                try:
                    tray.hide()
                except Exception:
                    pass

    def _refresh_tray_status(self) -> None:
        actions = getattr(self, "_tray_status_actions", None) or {}
        snap = self.snapshot
        titles = {
            "ollama": "Ollama",
            "whisper": "Whisper",
            "piper": "Piper",
            "ha": "HA",
            "browser": "Browser",
            "phone": "Phone",
        }
        for key, act in actions.items():
            status = str(getattr(snap, key, "unknown") or "unknown").upper()
            try:
                act.setText(f"{titles.get(key, key)}: {status}")
            except Exception:
                pass

    def _drain_into_window(self) -> None:
        from dataclasses import replace

        from core.hud_events import HudEvent, HudEventType

        try:
            events = get_hud_queue().drain()
        except Exception:
            return
        if not events:
            # Idle: skip most paint frames (no 60 FPS when quiet)
            self._idle_skip = (self._idle_skip + 1) % 8
            return
        snap = self.snapshot
        for event in events:
            try:
                snap = apply_event(
                    snap,
                    event,
                    world_throttle_s=self._world_throttle_s,
                )
            except Exception:
                continue
        snap = replace_proactive(snap, self._proactive_enabled)
        if self._muted:
            snap = apply_event(
                replace(snap, muted=True),
                HudEvent(
                    HudEventType.STATE_CHANGED,
                    {"speech": snap.speech_name, "muted": True},
                ),
            )
        self.snapshot = snap
        self._refresh_tray_status()
        window = self._window
        if window is None:
            return
        try:
            window.render(snap)
        except Exception:
            self.render_errors += 1

    def _attach_observers(self) -> None:
        pipeline = self.pipeline
        if pipeline is not None and getattr(pipeline, "state", None) is not None:
            def _on_state(old, new) -> None:
                try:
                    emit_state_changed(new)
                except Exception:
                    pass

            self._state_listener = _on_state
            try:
                pipeline.state.add_listener(_on_state)
                emit_state_changed(pipeline.state.state)
            except Exception:
                pass
            emit_integration("voice", "online")

        bus = getattr(self.runtime, "bus", None)
        if bus is not None:
            from core.events import (
                CRITICAL_TASK_FAILURE,
                HA_RESTORED,
                HA_UNAVAILABLE,
                NETWORK_LOST,
                NETWORK_RESTORED,
            )

            def _on_bus(event) -> None:
                try:
                    et = getattr(event, "type", "")
                    if et == HA_UNAVAILABLE:
                        emit_integration("ha", "offline")
                    elif et == HA_RESTORED:
                        emit_integration("ha", "online")
                    elif et == NETWORK_LOST:
                        emit_integration("voice", "degraded")
                    elif et == NETWORK_RESTORED:
                        emit_integration("voice", "online")
                    elif et == CRITICAL_TASK_FAILURE:
                        emit_error(str((event.data or {}).get("message") or "błąd"))
                except Exception:
                    pass

            self._bus_handler = _on_bus
            try:
                bus.subscribe(_on_bus)
            except Exception:
                pass

    def _detach_observers(self) -> None:
        pipeline = self.pipeline
        if pipeline is not None and self._state_listener is not None:
            try:
                pipeline.state.remove_listener(self._state_listener)
            except Exception:
                pass
        self._state_listener = None
        bus = getattr(self.runtime, "bus", None)
        if bus is not None and self._bus_handler is not None:
            try:
                bus.unsubscribe(self._bus_handler)
            except Exception:
                pass
        self._bus_handler = None

    def _seed_status(self) -> None:
        try:
            from core.health import HealthStatus, get_health_registry

            reg = get_health_registry()
            mapping = {
                "ha": "ha",
                "home_assistant": "ha",
                "ollama": "ollama",
                "voice": "voice",
                "whisper": "whisper",
                "piper": "piper",
                "browser": "browser",
                "phone": "phone",
                "companion": "phone",
            }
            for src, dest in mapping.items():
                item = reg.get(src)
                if item is None:
                    continue
                status = item.status
                if status is HealthStatus.READY:
                    label = "online"
                elif status is HealthStatus.DEGRADED:
                    label = "degraded"
                elif status in (
                    HealthStatus.UNAVAILABLE,
                    HealthStatus.ERROR,
                    HealthStatus.NOT_CONFIGURED,
                ):
                    label = (
                        "disabled"
                        if status is HealthStatus.NOT_CONFIGURED
                        else "offline"
                    )
                else:
                    label = "unknown"
                emit_integration(dest, label)
        except Exception:
            pass
        try:
            if not bool(get_config_value("voice", "tts_enabled", default=True)):
                self.snapshot.muted = True
                self._muted = True
        except Exception:
            pass


def replace_proactive(snap: HudSnapshot, enabled: bool) -> HudSnapshot:
    from dataclasses import replace

    if snap.proactive_enabled == enabled:
        return snap
    return replace(snap, proactive_enabled=enabled)


def _open_logs() -> None:
    path = os.path.join(DATA_DIR, "diagnostics")
    try:
        os.makedirs(path, exist_ok=True)
        os.startfile(path)  # Windows
    except Exception as exc:
        print(f"[HUD] nie otwarto logów: {exc}")


def try_start_hud(
    *,
    pipeline: Any = None,
    runtime: Any = None,
    enabled: Optional[bool] = None,
    run_fn: Optional[Callable[[], None]] = None,
    on_request_shutdown: Optional[Callable[[], None]] = None,
) -> Optional[HudHost]:
    """Start HUD if enabled. ImportError / crash → None, voice continues."""
    if enabled is None:
        enabled = hud_enabled_from_cli_and_config()
    if not enabled:
        return None
    if run_fn is None:
        try:
            import PySide6  # noqa: F401
        except ImportError:
            print("[HUD] PySide6 brak - głos działa dalej")
            return None
    host = HudHost(
        pipeline=pipeline,
        runtime=runtime,
        run_fn=run_fn,
        on_request_shutdown=on_request_shutdown,
    )
    try:
        host.start()
    except Exception as exc:
        print(f"[HUD] start failed — voice continues: {exc}")
        return None
    print("[HUD] observer started (voice loop stays on main thread)")
    return host
