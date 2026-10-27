# ==========================
# ELIAN — Phase 13 Stage B jarvis-core smoke (dry tools)
# ==========================
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


SAMPLE_LIGHT = {
    "entity_id": "light.vizzini",
    "state": "on",
    "attributes": {
        "friendly_name": "Inspire Vizzini D56 Vizzini",
        "supported_color_modes": ["rgb", "color_temp", "brightness"],
        "brightness": 128,
    },
}
CLIMATE = {
    "entity_id": "climate.salon",
    "state": "heat",
    "attributes": {"friendly_name": "Klimatyzacja salon", "temperature": 22},
}


def run_jarvis_core_smoke() -> int:
    import HaActions
    import HomeAssistant
    from core.active_service import (
        KIND_APP,
        KIND_WEBSITE,
        ActiveServiceContext,
        get_active_service,
        reset_active_service,
        set_active_service,
    )
    from core.builtin_tools import build_default_registry
    from core.compound_plan import parse_compound_goal
    from core.context_selection import select_context
    from core.conversation_manager import ConversationManager
    from core.intent_family import classify_intent_family
    from core.model_client import FakeModelClient, reset_model_client
    from core.planner import Planner
    from core.tool_result import ToolStatus
    from core.tool_shortlist import shortlist_tools
    from core.tools import reset_registry_for_tests
    from memory.manager import MemoryManager

    reset_registry_for_tests()
    reset_model_client()
    reset_active_service()
    HomeAssistant.get_registry().load_states([SAMPLE_LIGHT, CLIMATE])

    failed = 0
    rows = []

    def _row(**kwargs):
        rows.append(kwargs)
        if not kwargs.get("ok"):
            failed_box[0] += 1

    failed_box = [0]

    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")

        def _planner(registry=None):
            return Planner(
                registry or build_default_registry(confirm_medium=False),
                model=FakeModelClient([]),
            )

        def _mgr(registry=None):
            mgr = ConversationManager(
                registry=registry or build_default_registry(confirm_medium=False),
                memory_manager=mem,
                memory=[],
                model=FakeModelClient([]),
            )
            mgr.activate()
            return mgr

        # 1. APP+SYSTEM
        text = "Otwórz Spotify i zwiększ głośność."
        plan = parse_compound_goal(text)
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                result = _planner().run(text)
        _row(
            ok=(
                plan.detected
                and [s.family for s in plan.steps] == ["APP", "SYSTEM"]
                and result.source == "compound"
                and len(result.steps) >= 2
                and result.steps[0]["tool"] == "open_app"
                and result.steps[1]["tool"] == "volume_up"
            ),
            case="1 APP+SYSTEM",
        )

        # 2. APP+SYSTEM+HA
        text = "Otwórz Spotify, zwiększ głośność i wyłącz lampę."
        plan = parse_compound_goal(text)
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                with patch.object(HaActions, "_call_service", return_value=True):
                    result = _planner().run(text)
        fams = [s.family for s in plan.steps]
        tools = [item.get("tool") for item in result.steps]
        _row(
            ok=(
                plan.detected
                and len(plan.steps) >= 3
                and fams[:3] == ["APP", "SYSTEM", "SMART_HOME"]
                and result.source == "compound"
                and "open_app" in tools
                and "volume_up" in tools
                and "turn_off_device" in tools
            ),
            case="2 APP+SYSTEM+HA",
        )

        # 3. HA+HA
        text = "Wyłącz lampę i ustaw klimę na 22 stopnie."
        plan = parse_compound_goal(text)
        with patch.object(HaActions, "_call_service", return_value=True):
            result = _planner().run(text)
        _row(
            ok=(
                [s.family for s in plan.steps] == ["SMART_HOME", "SMART_HOME"]
                and result.source == "compound"
                and result.steps[0]["tool"] == "turn_off_device"
                and result.steps[1]["tool"] == "set_device_value"
            ),
            case="3 HA+HA",
        )

        # 4. APP+HA
        text = "Zamknij Chrome i wyłącz lampę."
        plan = parse_compound_goal(text)
        with patch("ComputerControl.close_application", return_value="Zamykam aplikację Chrome."):
            with patch.object(HaActions, "_call_service", return_value=True):
                result = _planner().run(text)
        _row(
            ok=(
                [s.family for s in plan.steps] == ["APP", "SMART_HOME"]
                and result.source == "compound"
                and result.steps[0]["tool"] == "close_app"
                and result.steps[1]["tool"] == "turn_off_device"
            ),
            case="4 APP+HA",
        )

        # 5. WEB/BROWSER semantic plan
        text = "Otwórz YouTube i wyszukaj Minecraft."
        plan = parse_compound_goal(text)
        _row(
            ok=(
                plan.detected
                and len(plan.steps) == 2
                and plan.steps[0].semantic_action == "open_service"
                and plan.steps[1].semantic_action == "browser_search_service"
                and plan.steps[1].family == "WEB"
            ),
            case="5 WEB/BROWSER plan",
        )

        # 6. negative NP coordination
        _row(
            ok=(
                not parse_compound_goal("Znajdź Spider-Mana i Iron Mana").detected
                and not parse_compound_goal("Wyszukaj sklepy w Łodzi i okolicy.").detected
                and not parse_compound_goal("Powiedz coś o Japonii i Korei.").detected
            ),
            case="6 negative NP",
        )

        # 7. source per step
        text = "Co robi klima i otwórz Spotify."
        plan = parse_compound_goal(text)
        kinds = [str((s.slots or {}).get("source_kind") or "") for s in plan.steps]
        ctx_source = None
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            result = _planner().run(text)
            ctx_source = (result.steps[0].get("source_kind") if result.steps else "")
        _row(
            ok=(
                len(plan.steps) >= 2
                and kinds[0] == "LOCAL_HA"
                and kinds[1] == "LOCAL_COMPUTER"
                and ctx_source == "LOCAL_HA"
                and all(item.get("source_kind") != "WEB" for item in result.steps)
            ),
            case="7 source per step",
        )

        # 8. tool shortlist union
        info = classify_intent_family("Otwórz Spotify, zwiększ głośność i wyłącz lampę.")
        catalog = build_default_registry(confirm_medium=False).descriptions_for_planner()
        names = [
            item.get("name")
            for item in shortlist_tools(catalog, goal=info.raw_text, classification=info)
        ]
        _row(
            ok=(
                "open_app" in names
                and "volume_up" in names
                and "turn_off_device" in names
                and "volume_down" not in names
                and "web_search" not in names
                and "create_reminder" not in names
                and "describe_screen" not in names
            ),
            case="8 shortlist union",
        )

        # 9. active service APP
        reset_active_service()
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            result = build_default_registry(confirm_medium=False).invoke(
                "open_app", {"name": "Spotify"}
            )
        service = get_active_service()
        _row(
            ok=(
                result.data.get("resolved_kind") == KIND_APP
                and service is not None
                and service.resolved_kind == KIND_APP
                and service.service_name.lower() == "spotify"
                and "CLOSE" in (service.capabilities or [])
            ),
            case="9 active service APP",
        )

        # 10. active service WEBSITE fallback
        reset_active_service()
        mgr = _mgr()
        with patch("core.builtin_tools.launch_application", return_value=(False, "brak")):
            with patch("WebActions.open_website", return_value=(True, "ok")):
                mgr.handle("Otwórz Spotify.")
        service = get_active_service()
        app_ent = mgr.mem.short.get_entity("application")
        _row(
            ok=(
                service is not None
                and service.resolved_kind == KIND_WEBSITE
                and "open.spotify.com" in str(service.official_url or service.current_url)
                and (app_ent is None or str(getattr(app_ent, "name", "")).lower() != "spotify")
                and "CLOSE" not in (service.capabilities or [])
            ),
            case="10 active service WEBSITE",
        )

        # 11. close_service APP
        reset_active_service()
        set_active_service(
            ActiveServiceContext(
                service_name="Spotify",
                resolved_kind=KIND_APP,
                capabilities=["OPEN", "CLOSE"],
            )
        )
        mgr = _mgr()
        with patch("ComputerControl.close_application", return_value="Zamykam aplikację Spotify.") as close:
            result = mgr.handle("Wyłącz Spotify.")
        _row(
            ok=(
                close.called
                and result.source in ("fast_path", "agent", "tool")
                and "przeglądark" not in (result.text or "").lower()
            ),
            case="11 close_service APP",
        )

        # 12. close_service WEBSITE unavailable until D
        reset_active_service()
        set_active_service(
            ActiveServiceContext(
                service_name="Spotify",
                resolved_kind=KIND_WEBSITE,
                official_url="https://open.spotify.com",
                capabilities=["OPEN"],
            )
        )
        with patch("ComputerControl.close_application") as close:
            invoked = build_default_registry(confirm_medium=False).invoke(
                "close_app", {"name": "Spotify"}
            )
        _row(
            ok=(
                not close.called
                and invoked.success is False
                and invoked.status == ToolStatus.UNAVAILABLE
                and str(invoked.tool_name) == "close_browser_tab"
            ),
            case="12 close WEBSITE unavailable",
        )

        # 13. context negative
        selection = select_context("Jak się masz?")
        _row(
            ok=(
                "service" in selection.excluded
                and "device" in selection.excluded
                and "browser" in selection.excluded
                and "execution_plan" in selection.excluded
                and not selection.include_service
                and not selection.include_device
                and not selection.include_plan
            ),
            case="13 context negative",
        )

        # 14. referent ambiguity clarification
        selection = select_context(
            "Zamknij to.",
            close_candidates=[
                {"name": "Spotify", "confidence": 0.48},
                {"name": "Chrome", "confidence": 0.45},
            ],
        )
        clarify = _planner().run(
            "Zamknij to.",
            context={"force_clarification": selection.clarification},
        )
        _row(
            ok=(
                selection.needs_clarification
                and "Spotify" in (selection.clarification or "")
                and "Chrome" in (selection.clarification or "")
                and clarify.needs_clarification
            ),
            case="14 referent CLARIFY",
        )

        # 15. no fake BrowserAgent success
        reset_active_service()
        text = "Otwórz YouTube i wyszukaj Minecraft."
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            result = _planner().run(text)
        second = result.steps[1]["observation"] if len(result.steps) > 1 else {}
        lowered = (result.message or "").lower()
        _row(
            ok=(
                result.source == "compound"
                and len(result.steps) >= 2
                and second.get("success") is False
                and str(second.get("status") or "") == "unavailable"
                and str(result.steps[1].get("tool") or "") == "browser_search_service"
                and "już puszczam" not in lowered
                and "wyszukałem" not in lowered
                and "nie umiem" in lowered
            ),
            case="15 no fake BrowserAgent",
        )

        # 16. social isolation
        reset_active_service()
        set_active_service(
            ActiveServiceContext(
                service_name="Spotify",
                resolved_kind=KIND_APP,
                capabilities=["OPEN", "CLOSE"],
            )
        )
        social = _planner().run(
            "co tam?",
            context={"active_device": {"entity_id": "light.vizzini", "name": "lampa"}},
        )
        lowered = (social.message or "").lower()
        _row(
            ok=(
                social.source == "social"
                and not social.steps
                and "lampa" not in lowered
                and "spotify" not in lowered
            ),
            case="16 social co tam",
        )

        # 17. joke vs Spotify poison
        reset_active_service()
        set_active_service(
            ActiveServiceContext(
                service_name="Spotify",
                resolved_kind=KIND_APP,
                capabilities=["OPEN", "CLOSE"],
            )
        )
        joke_calls = []
        joke_reg = build_default_registry(confirm_medium=False)
        joke_orig = joke_reg.invoke

        def _joke_wrap(name, args=None, **kwargs):
            joke_calls.append(name)
            return joke_orig(name, args, **kwargs)

        joke_reg.invoke = _joke_wrap
        joke = Planner(joke_reg, model=FakeModelClient([])).run(
            "opowiedz żart",
            context={"active_service": {"service_name": "Spotify"}},
        )
        _row(
            ok=(
                joke.source in ("talk", "social")
                and not joke_calls
                and "spotify" not in (joke.message or "").lower()
                and "nie rozumiem" not in (joke.message or "").lower()
            ),
            case="17 joke vs Spotify",
        )

    failed = failed_box[0]
    passed = len(rows) - failed
    print(f"jarvis-core-smoke {passed}/{len(rows)}")
    for row in rows:
        mark = "PASS" if row.get("ok") else "FAIL"
        print(f"  {mark}  {row.get('case')}")
    return 0 if failed == 0 and len(rows) == 17 else 1
