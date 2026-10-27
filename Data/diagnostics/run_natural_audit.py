"""Natural-speech audit probe — python Data/diagnostics/run_natural_audit.py"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.conversation_manager import ConversationManager
from core.model_client import FakeModelClient
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from memory.manager import MemoryManager

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


def _registry(log):
    registry = ToolRegistry(confirm_medium=False)

    def _ok(name):
        def handler(args):
            log.append({"tool": name, "arguments": dict(args or {})})
            data = dict(args or {})
            if name in ("turn_on_device", "turn_off_device", "set_device_value"):
                data.setdefault("resolved_entity_id", "light.vizzini")
                data["ha_verify"] = {"verification": "SUCCESS"}
            if name == "web_search":
                data["results"] = [
                    {"title": "A", "url": "https://a.example"},
                    {"title": "B", "url": "https://b.example"},
                ]
            if name == "get_now_playing":
                data.update({"title": "Beat It", "artist": "Michael Jackson"})
            return ToolResult.ok("ok", tool_name=name, data=data)

        return handler

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
        registry.register(
            Tool(
                name=name,
                description=name,
                handler=_ok(name),
                risk_level=RiskLevel.SAFE,
                parameters={"type": "object", "properties": {}},
            )
        )
    return registry


def main() -> None:
    log = []
    reg = _registry(log)
    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")
        cm = ConversationManager(
            registry=reg,
            model=FakeModelClient(
                [{"type": "final_answer", "message": "MODEL"}] * 40
            ),
            memory_manager=mem,
        )
        cm.activate()
        cm.pending_executable_goal = {
            "goal": "wyszukaj X",
            "status": "incomplete",
            "failed_steps": [{"tool": "web_search", "error": "timeout"}],
            "last_error": "timeout",
        }
        rows = []
        for utt in UTTERANCES:
            before = len(log)
            result = cm.handle(utt)
            env = getattr(cm, "_decision_envelope", None)
            ug = getattr(cm, "_user_goal", None)
            goal = getattr(getattr(cm, "goals", None), "current", None)
            tools = [c["tool"] for c in log[before:]]
            row = {
                "input": utt,
                "source": result.source,
                "text": (result.text or "")[:120],
                "mode": getattr(env, "mode", None),
                "intent": getattr(env, "intent", None),
                "utt": getattr(env, "utterance_class", None),
                "soft": getattr(ug, "soft_kind", None) if ug else None,
                "active_goal": getattr(goal, "objective", None) if goal else None,
                "tools": tools,
            }
            rows.append(row)
            print(
                "%r | %s | %s/%s/%s | soft=%s | tools=%s | %r"
                % (
                    utt,
                    row["source"],
                    row["mode"],
                    row["intent"],
                    row["utt"],
                    row["soft"],
                    row["tools"],
                    row["text"],
                )
            )
        out = ROOT / "Data" / "diagnostics" / "natural_speech_audit.json"
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print("WROTE", out)


if __name__ == "__main__":
    main()
