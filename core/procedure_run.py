# ==========================
# ELIAN — ProcedureEngine (morning + sleep v1, 2.0 declarative overlay)
# ==========================
"""ToolRegistry only. Unknown IDs stay honest Stage E stubs unless user-saved."""

from __future__ import annotations

from core.procedure_contract import procedure_unavailable_message
from core.tool_result import ToolResult


def run_named_procedure(procedure_id: str, registry=None, **kwargs) -> ToolResult:
    name = str(procedure_id or "").strip().lower()
    confirmed = bool(kwargs.get("confirmed"))
    cancel_check = kwargs.get("cancel_check")
    try:
        from core.procedure_engine import get_procedure, find_by_trigger, execute_procedure

        proc = get_procedure(name) or find_by_trigger(name, enabled_only=True)
        if proc is not None and proc.steps:
            return execute_procedure(
                proc,
                registry=registry,
                confirmed=confirmed,
                cancel_check=cancel_check,
            )
    except Exception:
        proc = None
    if name == "morning":
        try:
            from core.tools import get_registry

            reg = registry or get_registry()
            result = reg.invoke("get_situation_briefing", {}, confirmed=True)
            _note_world_procedure("morning", "ran" if result.success else "failed")
            return result
        except Exception as exc:
            return ToolResult.fail(
                "Nie udało się złożyć porannego briefingu.",
                tool_name="procedure_run",
                error=str(exc)[:120],
            )
    if name == "sleep":
        spoken = []
        try:
            from core.tools import get_registry

            reg = registry or get_registry()
            paused = reg.invoke("pause_media", {}, confirmed=True)
            if getattr(paused, "success", False) and paused.message:
                spoken.append(str(paused.message).rstrip("."))
        except Exception:
            pass
        text = "Dobranoc."
        if spoken:
            text = "Dobranoc. " + ". ".join(spoken) + "."
        _note_world_procedure("sleep", "ran")
        return ToolResult.ok(text, tool_name="procedure_run", data={"procedure_id": "sleep"})
    _note_world_procedure(name or "unknown", "unavailable")
    return ToolResult.unavailable(
        procedure_unavailable_message(name or "tej procedury"),
        tool_name="procedure_run",
        data={"procedure_id": name, "deferred_stage": "E"},
    )


def _note_world_procedure(procedure_id: str, status: str) -> None:
    try:
        from core.world_state import note_procedure

        note_procedure({"id": procedure_id, "status": status})
    except Exception:
        pass
