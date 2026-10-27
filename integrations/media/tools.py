# ==========================
# ELIAN — media tools (Phase 7)
# ==========================

from __future__ import annotations

from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from integrations.media.provider import (
    get_media_override,
    get_primary_media_provider,
    play_with_fallback,
    preferred_media_app,
)


def _play_media(arguments):
    query = str(arguments.get("query") or arguments.get("title") or "").strip()
    app = str(arguments.get("app") or arguments.get("provider") or "").strip()
    override = get_media_override()
    if override is not None:
        result = override.play(query, app=app or preferred_media_app())
    else:
        result = play_with_fallback(query, preferred_app=app or preferred_media_app())
    if not result.success:
        return ToolResult.fail(
            result.message,
            tool_name="play_media",
            data=result.to_tool_kwargs(),
            retryable=result.error_code == "UNAVAILABLE",
        )
    print(f"[INTEGRATION] media.play query={query!r} status=success")
    return ToolResult.ok(result.message, tool_name="play_media", data=result.to_tool_kwargs())


def _pause_media(_arguments):
    provider = get_primary_media_provider()
    result = provider.pause()
    return (
        ToolResult.ok(result.message, tool_name="pause_media", data=result.to_tool_kwargs())
        if result.success
        else ToolResult.fail(result.message, tool_name="pause_media", data=result.to_tool_kwargs())
    )


def _resume_media(_arguments):
    provider = get_primary_media_provider()
    result = provider.resume()
    return (
        ToolResult.ok(result.message, tool_name="resume_media", data=result.to_tool_kwargs())
        if result.success
        else ToolResult.fail(result.message, tool_name="resume_media", data=result.to_tool_kwargs())
    )


def _next_track(_arguments):
    provider = get_primary_media_provider()
    result = provider.next_track()
    return ToolResult.ok(result.message, tool_name="next_track", data=result.to_tool_kwargs())


def _previous_track(_arguments):
    provider = get_primary_media_provider()
    result = provider.previous_track()
    return ToolResult.ok(
        result.message, tool_name="previous_track", data=result.to_tool_kwargs()
    )


def _get_now_playing(_arguments):
    provider = get_primary_media_provider()
    result = provider.now_playing()
    return ToolResult.ok(
        result.message, tool_name="get_now_playing", data=result.to_tool_kwargs()
    )


def register_media_tools(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="play_media",
        description=(
            "Odtwarza lub wyszukuje muzykę/media (Spotify itd.). "
            "Używaj gdy użytkownik chce czegoś posłuchać — także parafrazy. "
            "Wymaga arguments.query (artysta/utwór). Opcjonalnie app."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "title": {"type": "string"},
                "app": {"type": "string"},
                "provider": {"type": "string"},
            },
        },
        risk_level=RiskLevel.SAFE,
        handler=_play_media,
        timeout=30,
    ))
    registry.register(Tool(
        name="pause_media",
        description="Pause current media playback.",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_pause_media,
    ))
    registry.register(Tool(
        name="resume_media",
        description="Resume media playback.",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_resume_media,
    ))
    registry.register(Tool(
        name="next_track",
        description="Skip to next track.",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_next_track,
    ))
    registry.register(Tool(
        name="previous_track",
        description="Go to previous track.",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_previous_track,
    ))
    registry.register(Tool(
        name="get_now_playing",
        description="What is currently playing (if known).",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_get_now_playing,
    ))
