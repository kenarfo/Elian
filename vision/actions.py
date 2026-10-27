# ==========================
# ELIAN — UI actions (Phase 5)
# ==========================

from __future__ import annotations

import time
from typing import Optional

from core.latency import current_tracker
from core.risk import RiskLevel
from core.tool_result import ToolResult
from vision.coordinates import validate_bounds_in_screen, validate_point_in_screen
from vision.models import Bounds, UIElement
from vision.risk_policy import (
    confirmation_prompt_for,
    escalate_element_risk,
    is_dangerous_label,
)
from vision.session import VisionSession


KEY_WHITELIST = {
    "enter",
    "escape",
    "esc",
    "tab",
    "space",
    "backspace",
    "delete",
    "up",
    "down",
    "left",
    "right",
    "home",
    "end",
    "pageup",
    "pagedown",
    "f5",
    "ctrl+c",
    "ctrl+v",
    "ctrl+x",
    "ctrl+a",
    "ctrl+s",
    "ctrl+z",
    "ctrl+l",
    "ctrl+t",
    "ctrl+w",
    "alt+f4",
    "alt+tab",
    "win+d",
}


def resolve_element(
    session: VisionSession,
    element_id: str,
    *,
    require_fresh: bool = True,
) -> tuple[Optional[UIElement], Optional[ToolResult]]:
    snap = session.current
    if snap is None or (require_fresh and not snap.valid):
        return None, ToolResult.fail(
            "Ekran się zmienił — potrzebuję świeżej obserwacji (describe_screen).",
            tool_name="click_ui_element",
            error="stale_snapshot",
            data={"needs_refresh": True},
            retryable=True,
        )
    # Snapshot id mismatch in composite id
    raw = str(element_id or "")
    if ":" in raw:
        snap_id, _ = raw.split(":", 1)
        if snap_id != snap.id:
            return None, ToolResult.fail(
                "Element pochodzi ze starego zrzutu ekranu. Odśwież obserwację.",
                tool_name="click_ui_element",
                error="stale_element",
                data={"needs_refresh": True, "snapshot_id": snap.id},
                retryable=True,
            )
    element = snap.get_element(element_id)
    if element is None:
        return None, ToolResult.fail(
            f"Nie znam elementu {element_id} na bieżącym ekranie.",
            tool_name="click_ui_element",
            error="unknown_element",
            retryable=True,
        )
    return element, None


def click_element(
    session: VisionSession,
    element_id: str,
    *,
    confirmed: bool = False,
    reobserve: bool = True,
) -> ToolResult:
    element, err = resolve_element(session, element_id)
    if err:
        return err

    assert element is not None
    snap = session.current
    assert snap is not None

    screen = snap.capture.screen_bounds
    bounds_err = validate_bounds_in_screen(element.bounds, screen)
    if bounds_err:
        return ToolResult.fail(
            "Współrzędne elementu są poza ekranem — nie klikam.",
            tool_name="click_ui_element",
            error="out_of_bounds",
            data={"detail": bounds_err},
        )

    risk = escalate_element_risk(element, base=RiskLevel.MEDIUM)
    if risk is RiskLevel.DANGEROUS and not confirmed:
        return ToolResult.needs_confirmation(
            confirmation_prompt_for(element),
            tool_name="click_ui_element",
            data={
                "element_id": element.id,
                "snapshot_id": snap.id,
                "risk_level": risk.value,
                "arguments": {"element_id": element_id, "confirmed": True},
            },
        )

    tracker = current_tracker()
    t0 = time.perf_counter()
    ok = session.ui.click(element)
    if tracker:
        tracker.record_ms("ui_action_ms", (time.perf_counter() - t0) * 1000)

    print(
        f"[UI] click element={element.id} text={element.label_for_log()} "
        f"risk={risk.value}"
    )

    session.invalidate()
    observation = None
    if reobserve:
        t1 = time.perf_counter()
        new_snap = session.refresh(force=True)
        if tracker:
            tracker.record_ms(
                "screen_verify_ms", (time.perf_counter() - t1) * 1000
            )
        observation = new_snap.observation_for_planner()

    if not ok:
        return ToolResult.fail(
            "Nie udało się wysłać kliknięcia.",
            tool_name="click_ui_element",
            retryable=True,
        )

    data = {
        "element_id": element.id,
        "snapshot_id": snap.id,
        "click_dispatched": True,
        "risk_level": risk.value,
    }
    if observation:
        data["new_observation"] = observation
        data["new_snapshot_id"] = session.current.id if session.current else ""

    return ToolResult.ok(
        "Kliknięcie wysłane.",
        tool_name="click_ui_element",
        data=data,
    )


def type_into_element(
    session: VisionSession,
    element_id: str,
    text: str,
    *,
    confirmed: bool = False,
    reobserve: bool = True,
) -> ToolResult:
    element, err = resolve_element(session, element_id)
    if err:
        err.tool_name = "type_into_element"
        return err
    assert element is not None
    snap = session.current
    assert snap is not None

    if element.role not in ("textbox", "edit", "input", "combobox", "document"):
        # Allow if OCR/vision labeled loosely
        if element.role not in ("text",):
            return ToolResult.fail(
                f"Element {element.id} nie wygląda na pole tekstowe ({element.role}).",
                tool_name="type_into_element",
                error="not_an_input",
            )

    if element.is_password and not confirmed:
        return ToolResult.needs_confirmation(
            confirmation_prompt_for(element),
            tool_name="type_into_element",
            data={
                "element_id": element.id,
                "risk_level": RiskLevel.DANGEROUS.value,
                "arguments": {
                    "element_id": element_id,
                    "text": text,
                    "confirmed": True,
                },
            },
        )

    # Never log password contents
    log_text = "***" if element.is_password else text[:40]
    print(f"[UI] type element={element.id} text={log_text!r}")

    tracker = current_tracker()
    t0 = time.perf_counter()
    ok = session.ui.type_text(element, text)
    if tracker:
        tracker.record_ms("ui_action_ms", (time.perf_counter() - t0) * 1000)

    session.invalidate()
    data = {"element_id": element.id, "typed": True}
    if reobserve:
        new_snap = session.refresh(force=True)
        data["new_observation"] = new_snap.observation_for_planner()

    if not ok:
        return ToolResult.fail(
            "Nie udało się wpisać tekstu.",
            tool_name="type_into_element",
            retryable=True,
        )
    return ToolResult.ok("Wpisałem tekst.", tool_name="type_into_element", data=data)


def click_coordinates(
    session: VisionSession,
    x: int,
    y: int,
    *,
    confirmed: bool = False,
    reobserve: bool = True,
) -> ToolResult:
    if not confirmed:
        return ToolResult.needs_confirmation(
            f"Mam kliknąć surowe współrzędne ({x}, {y})? Preferuję element_id.",
            tool_name="click_coordinates",
            data={
                "risk_level": RiskLevel.MEDIUM.value,
                "arguments": {"x": x, "y": y, "confirmed": True},
            },
        )

    snap = session.require_current()
    err = validate_point_in_screen(int(x), int(y), snap.capture.screen_bounds)
    if err:
        return ToolResult.fail(
            "Współrzędne poza ekranem — nie klikam.",
            tool_name="click_coordinates",
            error="out_of_bounds",
            data={"detail": err},
        )

    fake = UIElement(
        id="coord",
        role="point",
        text="",
        bounds=Bounds(int(x), int(y), 1, 1),
        source="vision",
        snapshot_id=snap.id,
    )
    ok = session.ui.click(fake)
    session.invalidate()
    data = {"x": x, "y": y, "click_dispatched": True}
    if reobserve:
        new_snap = session.refresh(force=True)
        data["new_observation"] = new_snap.observation_for_planner()
    if not ok:
        return ToolResult.fail("Kliknięcie nie powiodło się.", tool_name="click_coordinates")
    return ToolResult.ok("Kliknięcie wysłane.", tool_name="click_coordinates", data=data)


def press_key(session: VisionSession, keys: str, *, reobserve: bool = False) -> ToolResult:
    normalized = str(keys or "").strip().lower().replace(" ", "")
    normalized = normalized.replace("esc", "escape") if normalized == "esc" else normalized
    if normalized not in KEY_WHITELIST:
        return ToolResult.fail(
            f"Klawisz „{keys}” nie jest na whiteliscie.",
            tool_name="press_key",
            error="key_not_allowed",
        )
    ok = session.ui.press_key(normalized)
    # Some keys change UI
    if normalized in ("alt+f4", "alt+tab", "win+d", "enter", "escape"):
        session.invalidate()
        if reobserve:
            session.refresh(force=True)
    if not ok:
        return ToolResult.fail("Nie udało się wysłać klawisza.", tool_name="press_key")
    return ToolResult.ok(f"Wysłano: {normalized}.", tool_name="press_key")


def scroll_screen(
    session: VisionSession,
    *,
    direction: str = "down",
    amount: int = 3,
) -> ToolResult:
    direction = str(direction or "down").lower()
    if direction not in ("up", "down", "góra", "gora", "dół", "dol"):
        return ToolResult.fail(
            "Kierunek scroll: up/down.",
            tool_name="scroll",
        )
    if direction in ("góra", "gora"):
        direction = "up"
    if direction in ("dół", "dol"):
        direction = "down"
    ok = session.ui.scroll(direction=direction, amount=int(amount))
    session.invalidate()
    new_snap = session.refresh(force=True)
    if not ok:
        return ToolResult.fail("Scroll nie powiódł się.", tool_name="scroll")
    return ToolResult.ok(
        f"Przewinęto ({direction}).",
        tool_name="scroll",
        data={"new_observation": new_snap.observation_for_planner()},
    )
