# ==========================
# ELIAN — vision tools for ToolRegistry (Phase 5)
# ==========================

from __future__ import annotations

from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from vision.actions import (
    click_coordinates,
    click_element,
    press_key,
    scroll_screen,
    type_into_element,
)
from vision.session import get_vision_session


def _session():
    return get_vision_session()


def _get_active_window(_arguments):
    title, process = _session().get_active_window()
    if not title and not process:
        return ToolResult.fail(
            "Nie udało się odczytać aktywnego okna.",
            tool_name="get_active_window",
        )
    msg = f"Aktywne okno: {title or '(brak tytułu)'}."
    if process:
        msg += f" Proces: {process}."
    return ToolResult.ok(
        msg,
        tool_name="get_active_window",
        data={"active_window": title, "active_process": process},
    )


def _describe_screen(arguments):
    question = str(arguments.get("question") or arguments.get("query") or "").strip()
    active_only = bool(arguments.get("active_window_only", False))
    session = _session()
    snap = session.refresh(
        force=True,
        question=question,
        active_window_only=active_only,
    )
    message = snap.summary or "Zrobiłem obserwację ekranu."

    if not snap.elements and not snap.visible_text and not snap.summary:
        return ToolResult.fail(
            "Nie potrafię teraz poprawnie odczytać tego ekranu.",
            tool_name="describe_screen",
            data=snap.to_result_data(),
            retryable=True,
        )

    observation = snap.observation_for_planner()
    return ToolResult.ok(
        message,
        tool_name="describe_screen",
        data={
            **snap.to_result_data(),
            "observation": observation,
        },
    )


def _get_screen(arguments):
    # Alias focused on structured observation for planner
    return _describe_screen(arguments)


def _ask_about_screen(arguments):
    question = str(
        arguments.get("question") or arguments.get("query") or ""
    ).strip()
    if not question:
        return ToolResult.fail(
            "Jakie pytanie o ekran mam zadać?",
            tool_name="ask_about_screen",
        )
    session = _session()
    answer = session.ask_about_screen(question)
    snap = session.current
    return ToolResult.ok(
        answer,
        tool_name="ask_about_screen",
        data={
            "answer": answer,
            "snapshot_id": snap.id if snap else "",
            "observation": snap.observation_for_planner() if snap else "",
        },
    )


def _find_ui_element(arguments):
    query = str(arguments.get("query") or arguments.get("text") or "").strip()
    if not query:
        return ToolResult.fail(
            "Czego mam szukać na ekranie?",
            tool_name="find_ui_element",
        )
    session = _session()
    session.require_current()
    matches = session.find_elements(query, limit=5)
    if not matches:
        return ToolResult.fail(
            f"Nie znalazłem elementu pasującego do: {query}.",
            tool_name="find_ui_element",
            data={"query": query, "candidates": []},
            retryable=True,
        )

    best_el, best_score = matches[0]
    candidates = [
        {
            "element_id": el.id,
            "full_id": f"{session.current.id}:{el.id}" if session.current else el.id,
            "text": "" if el.is_password else el.text,
            "role": el.role,
            "confidence": round(score, 3),
            "source": el.source,
        }
        for el, score in matches
    ]

    # Ambiguity: top two close scores
    if len(matches) >= 2 and (matches[0][1] - matches[1][1]) < 0.12 and matches[1][1] >= 0.5:
        return ToolResult.ok(
            "Znalazłem kilka podobnych elementów — potrzebuję doprecyzowania.",
            tool_name="find_ui_element",
            data={
                "ambiguous": True,
                "candidates": candidates,
                "query": query,
            },
        )

    if best_score < 0.55:
        return ToolResult.ok(
            "Nie jestem pewien, który element masz na myśli.",
            tool_name="find_ui_element",
            data={
                "ambiguous": True,
                "candidates": candidates,
                "query": query,
                "best_confidence": round(best_score, 3),
            },
        )

    return ToolResult.ok(
        f"Znalazłem {best_el.id}: {best_el.role} „{best_el.text}”.",
        tool_name="find_ui_element",
        data={
            "element_id": best_el.id,
            "full_id": f"{session.current.id}:{best_el.id}" if session.current else best_el.id,
            "confidence": round(best_score, 3),
            "candidates": candidates,
            "ambiguous": False,
        },
    )


def _click_ui_element(arguments):
    element_id = str(
        arguments.get("element_id") or arguments.get("id") or ""
    ).strip()
    if not element_id:
        return ToolResult.fail(
            "Podaj element_id z bieżącego snapshotu.",
            tool_name="click_ui_element",
        )
    confirmed = bool(arguments.get("confirmed", False))
    return click_element(
        _session(),
        element_id,
        confirmed=confirmed,
        reobserve=True,
    )


def _type_into_element(arguments):
    element_id = str(arguments.get("element_id") or arguments.get("id") or "").strip()
    text = str(arguments.get("text") or arguments.get("value") or "")
    if not element_id:
        return ToolResult.fail(
            "Podaj element_id pola tekstowego.",
            tool_name="type_into_element",
        )
    if text == "" and text is not None:
        # allow empty clear? require non-empty for now
        pass
    if not str(text):
        return ToolResult.fail(
            "Jaki tekst mam wpisać?",
            tool_name="type_into_element",
        )
    return type_into_element(
        _session(),
        element_id,
        text,
        confirmed=bool(arguments.get("confirmed", False)),
        reobserve=True,
    )


def _click_coordinates(arguments):
    try:
        x = int(arguments.get("x"))
        y = int(arguments.get("y"))
    except (TypeError, ValueError):
        return ToolResult.fail(
            "Podaj całkowite x oraz y.",
            tool_name="click_coordinates",
        )
    return click_coordinates(
        _session(),
        x,
        y,
        confirmed=bool(arguments.get("confirmed", False)),
        reobserve=True,
    )


def _press_key(arguments):
    keys = str(arguments.get("keys") or arguments.get("key") or "").strip()
    if not keys:
        return ToolResult.fail("Jaki klawisz mam wcisnąć?", tool_name="press_key")
    return press_key(_session(), keys, reobserve=False)


def _scroll(arguments):
    direction = str(arguments.get("direction") or "down")
    amount = arguments.get("amount", 3)
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        amount = 3
    return scroll_screen(_session(), direction=direction, amount=amount)


def register_vision_tools(registry: ToolRegistry) -> ToolRegistry:
    registry.register(Tool(
        name="get_active_window",
        description=(
            "Returns the foreground window title and process name. "
            "Use before screen UI tasks to know the active app."
        ),
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_get_active_window,
        timeout=10,
    ))
    registry.register(Tool(
        name="describe_screen",
        description=(
            "Capture and understand the current screen (UIA + OCR + local vision). "
            "Returns a structured observation with snapshot_id and element ids "
            "(button_1, input_2, ...). Prefer this before click_ui_element. "
            "Optional question focuses the description. Does NOT click."
        ),
        parameters={
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "query": {"type": "string"},
                "active_window_only": {"type": "boolean"},
            },
        },
        risk_level=RiskLevel.SAFE,
        handler=_describe_screen,
        timeout=90,
    ))
    registry.register(Tool(
        name="get_screen",
        description=(
            "Same as describe_screen — get current screen observation for planning."
        ),
        parameters={
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "active_window_only": {"type": "boolean"},
            },
        },
        risk_level=RiskLevel.SAFE,
        handler=_get_screen,
        timeout=90,
    ))
    registry.register(Tool(
        name="ask_about_screen",
        description=(
            "Answer a specific question about what is visible on screen "
            "(e.g. download percent, error text). Does not perform UI actions."
        ),
        parameters={
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["question"],
        },
        risk_level=RiskLevel.SAFE,
        handler=_ask_about_screen,
        timeout=90,
    ))
    registry.register(Tool(
        name="find_ui_element",
        description=(
            "Find a visible UI element by query (text/role/hints) on the current "
            "snapshot. Returns element_id and confidence. If ambiguous, returns "
            "multiple candidates — ask the user, do NOT click randomly."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["query"],
        },
        risk_level=RiskLevel.SAFE,
        handler=_find_ui_element,
        timeout=30,
    ))
    registry.register(Tool(
        name="click_ui_element",
        description=(
            "Click a visible UI element previously identified on the current screen "
            "snapshot. Use element_id whenever possible. After click, returns a new "
            "screen observation. Dangerous labels (Delete, Usuń konto, Pay, ...) "
            "require user confirmation. Do not use for passwords."
        ),
        parameters={
            "type": "object",
            "properties": {
                "element_id": {"type": "string"},
                "id": {"type": "string"},
                "confirmed": {"type": "boolean"},
            },
            "required": ["element_id"],
        },
        risk_level=RiskLevel.SAFE,  # semantic escalation inside handler
        handler=_click_ui_element,
        timeout=30,
    ))
    registry.register(Tool(
        name="type_into_element",
        description=(
            "Focus a text input element by element_id and type text. "
            "Validate the target is an input. Password fields require confirmation "
            "and are never logged or stored in memory."
        ),
        parameters={
            "type": "object",
            "properties": {
                "element_id": {"type": "string"},
                "id": {"type": "string"},
                "text": {"type": "string"},
                "value": {"type": "string"},
                "confirmed": {"type": "boolean"},
            },
            "required": ["element_id", "text"],
        },
        risk_level=RiskLevel.SAFE,
        handler=_type_into_element,
        timeout=30,
    ))
    registry.register(Tool(
        name="click_coordinates",
        description=(
            "Fallback: click raw screen coordinates. Prefer click_ui_element. "
            "Always requires confirmation. Rejects out-of-bounds points."
        ),
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "confirmed": {"type": "boolean"},
            },
            "required": ["x", "y"],
        },
        risk_level=RiskLevel.MEDIUM,
        handler=_click_coordinates,
        timeout=20,
        confirmation_prompt="Mam kliknąć surowe współrzędne na ekranie?",
    ))
    registry.register(Tool(
        name="press_key",
        description=(
            "Press a whitelisted key or chord (enter, escape, tab, ctrl+s, alt+tab, ...). "
            "Arbitrary key scripting is not allowed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "keys": {"type": "string"},
                "key": {"type": "string"},
            },
            "required": ["keys"],
        },
        risk_level=RiskLevel.SAFE,
        handler=_press_key,
        timeout=10,
    ))
    registry.register(Tool(
        name="scroll",
        description=(
            "Scroll the active view (direction=up/down, amount). "
            "Invalidates the current screen snapshot and re-observes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "direction": {"type": "string"},
                "amount": {"type": "integer"},
            },
        },
        risk_level=RiskLevel.SAFE,
        handler=_scroll,
        timeout=15,
    ))
    return registry
