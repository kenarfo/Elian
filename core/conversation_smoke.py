# ==========================
# ELIAN — conversation smoke (Phase 11)
# ==========================
"""Offline conversation / knowledge / web-decision smoke — no mic, dry tools."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List

from core.agent import run_agent
from core.conversation_focus import enrich_goal_with_focus, update_focus
from core.conversation_manager import ConversationManager
from core.intent_family import classify_intent_family
from core.model_client import FakeModelClient
from core.risk import RiskLevel
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry
from memory.manager import MemoryManager


CASES = (
    ("Fajny masz głos.", "TALK", ()),
    ("Nie wiem co robić.", "TALK", ()),
    ("Co to jest czarna dziura?", "KNOWLEDGE", ()),
    ("Kto jest teraz prezydentem USA?", "WEB", ("web_search",)),
    ("Spotify jest fajne.", "TALK", ()),
    ("Otwórz Discord.", "TOOL", ("open_app",)),
    ("Co by było gdybym wyłączył komputer?", "ANSWER", ()),
    ("Znajdź mi informacje o RTX 5070.", "WEB", ("web_search",)),
)


def _dry_registry() -> ToolRegistry:
    registry = ToolRegistry(confirm_medium=False)
    invoked: List[Dict[str, Any]] = []

    def _ok(name: str, message: str, data_builder=None) -> Callable:
        def handler(args):
            payload = dict(args or {})
            invoked.append({"tool": name, "arguments": payload})
            data = data_builder(payload) if data_builder else payload
            return ToolResult.ok(message, tool_name=name, data=data)

        return handler

    def web_data(args):
        return {
            "query": args.get("query"),
            "results": [
                {
                    "title": "Example",
                    "url": "https://example.com",
                    "snippet": "Example snippet",
                    "source": "example.com",
                }
            ],
            "result_count": 1,
            "provider": "dry",
            "success": True,
        }

    for spec in (
        ("open_app", "Uruchomiłem.", {"name": {"type": "string"}}, ["name"], None),
        ("play_media", "Odtwarzam.", {"query": {"type": "string"}}, ["query"], None),
        (
            "set_preference",
            "Zapisano.",
            {"key": {"type": "string"}, "value": {"type": "string"}},
            ["key", "value"],
            None,
        ),
        (
            "web_search",
            "Znalazłem wyniki.",
            {"query": {"type": "string"}},
            ["query"],
            web_data,
        ),
        (
            "fetch_web_page",
            "Pobrałem.",
            {"url": {"type": "string"}},
            ["url"],
            None,
        ),
        (
            "open_website",
            "Otwieram.",
            {"url": {"type": "string"}, "target": {"type": "string"}},
            [],
            None,
        ),
        ("shutdown_pc", "Wyłączam.", {}, [], None),
    ):
        name, msg, props, required, builder = spec
        registry.register(
            Tool(
                name=name,
                description=name,
                handler=_ok(name, msg, builder),
                risk_level=RiskLevel.SAFE,
                parameters={
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            )
        )
    registry._dry_invoked = invoked  # type: ignore[attr-defined]
    return registry


def _scripted_model(text: str) -> FakeModelClient:
    lowered = text.lower()
    # WEB family often resolves via deterministic web_search; model only synthesizes.
    if "prezydent" in lowered or "rtx 5070" in lowered or "informacje o" in lowered:
        return FakeModelClient(
            [
                {
                    "type": "final_answer",
                    "message": "Według kilku aktualnych źródeł to aktualny stan.",
                },
            ]
        )
    if "discord" in lowered and ("otwórz" in lowered or "otworz" in lowered):
        return FakeModelClient(
            [
                {
                    "type": "tool_call",
                    "tool": "open_app",
                    "arguments": {"name": "Discord"},
                }
            ]
        )
    if "czarna dziura" in lowered:
        return FakeModelClient(
            [
                {
                    "type": "final_answer",
                    "message": "Czarna dziura to obszar o tak silnej grawitacji, że nic nie ucieka.",
                }
            ]
        )
    if "wyłączył komputer" in lowered or "wylaczyl komputer" in lowered:
        return FakeModelClient(
            [
                {
                    "type": "final_answer",
                    "message": "Wtedy sesja by się zakończyła — na szczęście to tylko hipotetycznie.",
                }
            ]
        )
    if "głos" in lowered or "glos" in lowered:
        return FakeModelClient(
            [{"type": "final_answer", "message": "Dzięki, miło słyszeć."}]
        )
    if "nie wiem co robić" in lowered:
        return FakeModelClient(
            [
                {
                    "type": "final_answer",
                    "message": "Możemy pogadać albo coś odpalić — co wolisz?",
                }
            ]
        )
    if "spotify jest fajne" in lowered:
        return FakeModelClient(
            [{"type": "final_answer", "message": "Też lubię Spotify do muzyki."}]
        )
    return FakeModelClient(
        [{"type": "final_answer", "message": "Okej, rozumiem."}]
    )


def _evaluate(text: str, expected_kind: str, expected_tools: tuple) -> Dict[str, Any]:
    registry = _dry_registry()
    model = _scripted_model(text)
    classification = classify_intent_family(text)

    # Deterministic WEB / APP / KNOWLEDGE paths exercise real planner wiring.
    result = run_agent(
        text,
        registry=registry,
        model=model,
        context={
            "raw_text": text,
            "intent_classification": classification,
            "preferences": {},
        },
    )
    invoked = list(getattr(registry, "_dry_invoked", []) or [])
    tools = [item.get("tool") for item in invoked]
    spoken = (result.message if result else "") or ""
    open_app_calls = tools.count("open_app")
    set_pref_calls = tools.count("set_preference")
    web_calls = tools.count("web_search")
    shutdown_calls = sum(1 for t in tools if "shutdown" in str(t))

    ok = "nie zrozumiałem" not in spoken.lower()
    if expected_kind in ("TALK", "ANSWER", "KNOWLEDGE"):
        ok = ok and len(invoked) == 0 and web_calls == 0
        if expected_kind == "KNOWLEDGE":
            ok = ok and (
                (result and result.needs_knowledge)
                or (result and not result.steps)
            )
    elif expected_kind == "WEB":
        ok = ok and web_calls >= 1 and "web_search" in expected_tools
    elif expected_kind == "TOOL":
        ok = ok and any(t in tools for t in expected_tools)

    if "spotify jest fajne" in text.lower():
        ok = ok and open_app_calls == 0 and set_pref_calls == 0
    if "wyłączył" in text.lower() or "wylaczyl" in text.lower():
        ok = ok and shutdown_calls == 0

    return {
        "input": text,
        "expected": expected_kind,
        "family": classification.family.value,
        "tools": tools,
        "web_calls": web_calls,
        "open_app_calls": open_app_calls,
        "set_preference_calls": set_pref_calls,
        "shutdown_calls": shutdown_calls,
        "message": spoken,
        "pass": bool(ok),
    }


def _followup_smoke() -> Dict[str, Any]:
    focus = update_focus(
        None,
        user_text="Kto stworzył Minecrafta?",
        assistant_text="Markus Persson, znany jako Notch, stworzył Minecrafta.",
    )
    second = enrich_goal_with_focus("A kiedy?", focus)
    focus = update_focus(
        focus,
        user_text="A kiedy?",
        assistant_text="Gra została po raz pierwszy wydana w 2009 roku.",
    )
    third = enrich_goal_with_focus("A ile miał wtedy lat?", focus)
    ok = (
        "Minecraft" in second
        and "Markus" in second
        and "Minecraft" in third
        and "Markus" in third
    )
    # Also ensure CM keeps focus across turns with FakeModel.
    registry = _dry_registry()
    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "mem.json")
        model = FakeModelClient(
            [
                {
                    "type": "final_answer",
                    "message": "Markus Persson stworzył Minecrafta.",
                },
                {
                    "type": "final_answer",
                    "message": "Pierwsza publiczna wersja była w 2009.",
                },
                {
                    "type": "final_answer",
                    "message": "Miał wtedy około 30 lat.",
                },
            ]
        )
        manager = ConversationManager(
            registry=registry,
            model=model,
            memory_manager=mem,
        )
        manager.activate()
        r1 = manager.handle("Kto stworzył Minecrafta?")
        r2 = manager.handle("A kiedy?")
        r3 = manager.handle("A ile miał wtedy lat?")
        focus_now = dict(getattr(mem.short, "focus", {}) or {})
        cm_ok = (
            "nie zrozumiałem" not in (r1.text or "").lower()
            and "nie zrozumiałem" not in (r2.text or "").lower()
            and "nie zrozumiałem" not in (r3.text or "").lower()
            and bool(focus_now.get("current_topic"))
            and len(getattr(registry, "_dry_invoked", []) or []) == 0
        )
    return {
        "name": "follow-up context",
        "enriched_second": second[:120],
        "enriched_third": third[:120],
        "focus_topic": focus_now.get("current_topic", ""),
        "pass": bool(ok and cm_ok),
    }


def run_conversation_smoke() -> int:
    print("=== Elian conversation-smoke (Phase 11) ===")
    results = [_evaluate(text, kind, tools) for text, kind, tools in CASES]
    follow = _followup_smoke()
    passed = sum(1 for item in results if item["pass"])
    total = len(results)
    for item in results:
        mark = "PASS" if item["pass"] else "FAIL"
        print(
            f"[{mark}] {item['input']!r} expected={item['expected']} "
            f"family={item['family']} tools={item['tools']} "
            f"msg={(item['message'] or '')[:60]!r}"
        )
    fmark = "PASS" if follow["pass"] else "FAIL"
    print(
        f"[{fmark}] follow-up topic={follow.get('focus_topic')!r} "
        f"second={follow.get('enriched_second')!r}"
    )
    ok = passed == total and follow["pass"]
    print(f"Result: {passed}/{total} cases + follow-up={'PASS' if follow['pass'] else 'FAIL'}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run_conversation_smoke())
