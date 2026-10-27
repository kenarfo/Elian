# One-shot natural-speech audit probe (not a permanent test fixture dump).
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

from core.conversation_manager import ConversationManager
from core.memory_manager import MemoryManager
from core.tools import ToolRegistry, ToolSpec, ToolResult, ToolStatus


class TraceRegistry(ToolRegistry):
    def __init__(self):
        super().__init__()
        self.calls: List[Dict[str, Any]] = []

    def register(self, spec: ToolSpec) -> None:
        super().register(spec)

    def invoke(self, name: str, arguments=None, *, confirmed: bool = False):
        args = dict(arguments or {})
        self.calls.append({"tool": name, "args": args})
        if name in ("volume_down", "volume_up"):
            return ToolResult.ok(name, f"ok:{name}", data=args)
        if name in ("turn_on_device", "turn_off_device", "set_device_value"):
            return ToolResult.ok(
                name,
                f"ok:{name}",
                data={
                    **args,
                    "resolved_entity_id": args.get("entity_id") or "light.vizzini",
                    "ha_verify": {"verification": "SUCCESS", "after": "on"},
                },
            )
        if name == "play_media":
            return ToolResult.ok(name, "ok:play", data={"query": args.get("query")})
        if name == "get_now_playing":
            return ToolResult.ok(
                name, "ok:np", data={"title": "Beat It", "artist": "Michael Jackson"}
            )
        if name == "web_search":
            return ToolResult.ok(
                name,
                "ok:web",
                data={
                    "query": args.get("query"),
                    "results": [
                        {"title": "A", "url": "https://a.example"},
                        {"title": "B", "url": "https://b.example"},
                    ],
                },
            )
        if name == "open_website":
            return ToolResult.ok(name, "ok:open", data=args)
        if name == "open_app":
            return ToolResult.ok(name, "ok:app", data=args)
        return ToolResult.fail(name, f"unknown:{name}", error_code="TOOL_UNAVAILABLE")


UTTERANCES = [
    "ciemno tu",
    "weź coś z tym zrób",
    "jeszcze jaśniej",
    "nie aż tak",
    "zgaś ją",
    "odpal coś michaela",
    "trochę ciszej",
    "co teraz leci",
    "nie to, coś innego",
    "wyszukaj coś o tym",
    "otwórz pierwszy",
    "nie, drugi",
    "jeszcze tego nie zrobiłeś",
    "no to zrób",
    "czemu nie zrobiłeś?",
    "spróbuj jeszcze raz",
    "spróbuj inaczej",
    "dobra nieważne",
    "ej jednak zrób",
    "zrób jak wcześniej",
    "miałeś mi o czymś przypomnieć",
    "co ja miałem zrobić?",
    "zapamiętaj to",
    "nie zapisuj tego",
    "już nie musisz mi o tym przypominać",
]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")
        reg = TraceRegistry()
        # Minimal tools for soft/agent paths
        for name in (
            "volume_down",
            "volume_up",
            "turn_on_device",
            "turn_off_device",
            "set_device_value",
            "play_media",
            "get_now_playing",
            "web_search",
            "open_website",
            "open_app",
        ):
            reg.register(
                ToolSpec(
                    name=name,
                    description=name,
                    parameters={"type": "object", "properties": {}},
                    handler=lambda **kw: None,
                )
            )
        # Override invoke
        TraceRegistry.invoke = TraceRegistry.invoke  # keep

        model = MagicMock()
        model.chat = MagicMock(
            return_value=json.dumps(
                {"type": "final_answer", "message": "MODEL_FALLBACK"}
            )
        )
        cm = ConversationManager(registry=reg, model=model, memory_manager=mem)
        cm.activate()

        # Seed a failed pending goal for retry/why traces
        cm.pending_executable_goal = {
            "goal": "wyszukaj X",
            "status": "incomplete",
            "failed_steps": [{"tool": "web_search", "error": "timeout"}],
            "last_error": "timeout",
        }

        rows = []
        for utt in UTTERANCES:
            before = len(reg.calls)
            env = getattr(cm, "_decision_envelope", None)
            result = cm.handle(utt)
            after_calls = reg.calls[before:]
            env = getattr(cm, "_decision_envelope", None)
            ug = getattr(cm, "_user_goal", None)
            goal = getattr(cm.goals, "current", None) if getattr(cm, "goals", None) else None
            rows.append(
                {
                    "input": utt,
                    "source": getattr(result, "source", None),
                    "text": (getattr(result, "text", None) or "")[:120],
                    "mode": getattr(env, "mode", None),
                    "intent": getattr(env, "intent", None),
                    "utt_class": getattr(env, "utterance_class", None),
                    "soft": getattr(ug, "soft_kind", None) if ug else None,
                    "active_goal": getattr(goal, "objective", None) if goal else None,
                    "tools": [c["tool"] for c in after_calls],
                }
            )
            print(
                f"{utt!r} -> src={rows[-1]['source']} mode={rows[-1]['mode']} "
                f"intent={rows[-1]['intent']} utt={rows[-1]['utt_class']} "
                f"soft={rows[-1]['soft']} tools={rows[-1]['tools']} "
                f"text={rows[-1]['text']!r}"
            )

        out = Path("Data/diagnostics/natural_speech_audit.json")
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print("WROTE", out)


if __name__ == "__main__":
    main()
