# ==========================
# ELIAN — optional HUD smoke (Phase 13 / GUI 2.0)
# ==========================
"""Offscreen HUD smoke. Missing PySide6 / display → UNAVAILABLE, exit 0.

Simulates: IDLE → AWARE → LISTENING → THINKING → ACTING (3-step plan) →
proactive situation → memory update → SPEAKING → ERROR/DEGRADED → recovery →
clean shutdown.

Visual / live hardware HUD remains NIE TESTOWANE — this gate only proves
observer wiring, state projection, and clean close under QT_QPA_PLATFORM=offscreen.
"""

from __future__ import annotations

import os
import traceback

from core.hud_events import (
    HudDisplayState,
    HudSnapshot,
    apply_event,
    emit_error,
    emit_goal_started,
    emit_integration,
    emit_memory_candidate,
    emit_memory_saved,
    emit_plan_updated,
    emit_response,
    emit_situation,
    emit_state_changed,
    emit_tool_finished,
    emit_tool_started,
    emit_transcript,
    emit_world_state_updated,
    get_hud_queue,
    reset_hud_events_for_tests,
)


def _drain_apply(snap: HudSnapshot) -> HudSnapshot:
    for event in get_hud_queue().drain():
        snap = apply_event(snap, event)
    return snap


def _expect(snap: HudSnapshot, state: HudDisplayState, step: str) -> None:
    if snap.state is not state:
        raise AssertionError(f"{step}: expected {state.value}, got {snap.state.value}")


def run_hud_smoke() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    reset_hud_events_for_tests()
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
    except Exception as exc:
        print(f"HUD-SMOKE UNAVAILABLE: PySide6 missing or failed ({exc})")
        print("HUD visual/live RC: NIE TESTOWANE")
        return 0

    try:
        from ui.hud_host import HudHost
        from ui.hud_window import HudWindow
    except Exception as exc:
        print(f"HUD-SMOKE UNAVAILABLE: HUD import failed ({exc})")
        print("HUD visual/live RC: NIE TESTOWANE")
        return 0

    host = None
    try:
        app = QApplication.instance() or QApplication(["elian-hud-smoke"])
        app.setQuitOnLastWindowClosed(False)

        window = HudWindow(always_on_top=False, compact=False, mini=False)
        # Host without a second Qt thread — smoke owns the QApplication loop.
        host = HudHost(always_on_top=False, compact=False, run_fn=lambda: None)
        host._window = window
        host.snapshot = HudSnapshot()
        window.set_close_handler(window.hide)

        # Repeated show/hide + mini/full must not crash.
        window.show()
        window.hide()
        window.show()
        window.set_mini(True)
        if not window.is_mini:
            raise AssertionError("mini mode not set")
        window.set_mini(False)

        # 1. IDLE
        emit_state_changed("IDLE")
        host.snapshot = _drain_apply(host.snapshot)
        window.render(host.snapshot)
        _expect(host.snapshot, HudDisplayState.IDLE, "IDLE")

        # 2. AWARE (wake)
        emit_state_changed("WAKE_LISTENING")
        host.snapshot = _drain_apply(host.snapshot)
        window.render(host.snapshot)
        _expect(host.snapshot, HudDisplayState.AWARE, "AWARE")

        # 3. LISTENING + transcript
        emit_state_changed("ACTIVE_LISTENING")
        emit_transcript("hej elian, sprawdź kalendarz")
        host.snapshot = _drain_apply(host.snapshot)
        window.render(host.snapshot)
        _expect(host.snapshot, HudDisplayState.LISTENING, "LISTENING")
        if "kalendarz" not in host.snapshot.transcript:
            raise AssertionError("transcript missing")

        # 4. THINKING
        emit_state_changed("PROCESSING")
        host.snapshot = _drain_apply(host.snapshot)
        window.render(host.snapshot)
        _expect(host.snapshot, HudDisplayState.THINKING, "THINKING")

        # 5. ACTING with 3-step plan
        emit_goal_started(
            "Sprawdź kalendarz",
            steps=[
                {"id": "1", "label": "Połącz z kalendarzem", "done": False},
                {"id": "2", "label": "Pobierz wydarzenia", "done": False},
                {"id": "3", "label": "Podsumuj dzień", "done": False},
            ],
            thought="Planuję 3 kroki",
        )
        emit_tool_started("calendar_list", thought="Łączę informacje…", status_text="Sprawdzam kalendarz")
        emit_state_changed("ACTING", status_text="Sprawdzam kalendarz")
        emit_world_state_updated(
            {"DOM": "biuro", "PC": "PC-KEN", "MEDIA": "—", "NEXT": "spotkanie 18:00"},
            force=True,
        )
        host.snapshot = _drain_apply(host.snapshot)
        window.render(host.snapshot)
        _expect(host.snapshot, HudDisplayState.ACTING, "ACTING")
        if len(host.snapshot.plan_steps) != 3:
            raise AssertionError(f"expected 3 plan steps, got {len(host.snapshot.plan_steps)}")
        if host.snapshot.tool != "calendar_list":
            raise AssertionError(f"ACTING: expected tool calendar_list, got {host.snapshot.tool!r}")
        if "DOM" not in host.snapshot.world:
            raise AssertionError("world snapshot missing DOM")

        emit_tool_finished("calendar_list", success=True, step_id="1")
        emit_plan_updated(
            steps=[
                {"id": "1", "label": "Połącz z kalendarzem", "done": True},
                {"id": "2", "label": "Pobierz wydarzenia", "done": True},
                {"id": "3", "label": "Podsumuj dzień", "done": False},
            ],
            thought="Łączę informacje…",
        )
        host.snapshot = _drain_apply(host.snapshot)
        window.render(host.snapshot)
        done = sum(1 for s in host.snapshot.plan_steps if s.done)
        if done < 2:
            raise AssertionError(f"expected ≥2 done steps, got {done}")

        # 6. Proactive situation card
        emit_situation(
            "Spotkanie za 15 min",
            body="Kalendarz: status z zespołem",
            situation_id="smoke-sit-1",
            action_label="Przypomnij",
        )
        host.snapshot = _drain_apply(host.snapshot)
        window.render(host.snapshot)
        if not host.snapshot.situations:
            raise AssertionError("proactive situation missing")

        # 7. Memory update
        emit_memory_candidate("preferuje krótkie odpowiedzi")
        emit_memory_saved("preferuje krótkie odpowiedzi", kind="persistent")
        host.snapshot = _drain_apply(host.snapshot)
        window.render(host.snapshot)
        if host.snapshot.memory_kind != "persistent":
            raise AssertionError(f"memory kind={host.snapshot.memory_kind!r}")

        # 8. SPEAKING
        emit_tool_finished("calendar_list", success=True, step_id="3")
        emit_response("Masz spotkanie o 18:00.")
        emit_state_changed("SPEAKING")
        host.snapshot = _drain_apply(host.snapshot)
        window.render(host.snapshot)
        _expect(host.snapshot, HudDisplayState.SPEAKING, "SPEAKING")

        # 9. ERROR then recover to DEGRADED then recovery to IDLE online
        emit_state_changed("IDLE")
        emit_error("smoke error")
        host.snapshot = _drain_apply(host.snapshot)
        window.render(host.snapshot)
        _expect(host.snapshot, HudDisplayState.ERROR, "ERROR")

        emit_state_changed("PROCESSING")
        emit_state_changed("IDLE")
        emit_integration("ollama", "degraded")
        host.snapshot = _drain_apply(host.snapshot)
        window.render(host.snapshot)
        _expect(host.snapshot, HudDisplayState.DEGRADED, "DEGRADED")

        emit_integration("ollama", "online")
        emit_integration("ha", "online")
        emit_state_changed("IDLE")
        host.snapshot = _drain_apply(host.snapshot)
        window.render(host.snapshot)
        _expect(host.snapshot, HudDisplayState.IDLE, "recovery IDLE")

        # Integrations vocabulary
        emit_integration("whisper", "online")
        emit_integration("piper", "online")
        emit_integration("browser", "disabled")
        emit_integration("phone", "offline")
        host.snapshot = _drain_apply(host.snapshot)
        window.render(host.snapshot)
        if host.snapshot.whisper != "online" or host.snapshot.browser != "disabled":
            raise AssertionError("integration status mismatch")

        # Clean HUD close — must not raise; runtime is not owned here.
        window.hide()
        host.stop()
        QTimer.singleShot(40, app.quit)
        app.exec()

        print("HUD-SMOKE PASS (offscreen)")
        print("HUD visual/live RC: NIE TESTOWANE")
        return 0
    except AssertionError as exc:
        print(f"HUD-SMOKE FAIL: {exc}")
        traceback.print_exc()
        return 1
    except Exception as exc:
        print(f"HUD-SMOKE UNAVAILABLE: {exc}")
        traceback.print_exc()
        print("HUD visual/live RC: NIE TESTOWANE")
        return 0
    finally:
        if host is not None:
            try:
                host.stop()
            except Exception:
                pass
        reset_hud_events_for_tests()


if __name__ == "__main__":
    raise SystemExit(run_hud_smoke())
